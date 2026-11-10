"""One LLM call path for every agent: a chain of providers with fast failover.

The chain (default order gemini -> groq -> ollama, override with LLM_PROVIDERS) holds every
provider that is configured:

  gemini  GEMINI_API_KEY; GEMINI_MODEL, then GEMINI_FALLBACK_MODEL
  groq    GROQ_API_KEY (free key at console.groq.com); GROQ_MODEL
  ollama  OLLAMA_MODEL (a model pulled locally, e.g. `ollama pull llama3.2`); OLLAMA_BASE_URL;
          warmed up at startup and kept loaded

Each call walks the chain until one answers. A provider that is overloaded (429/5xx), slow or
unreachable is put on a short cooldown (LLM_COOLDOWN seconds, or the server's Retry-After) and
skipped by later calls, so one bad model costs one timeout, not one per request. When every
provider is cooling down the call fails at once. Callers keep
their own deterministic fallback for when the whole chain fails, so a demo never breaks.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass

import httpx

from .. import config  # noqa: F401  (loads .env before keys are read)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1"
DEFAULTS = {"gemini": "gemini-flash-latest", "gemini_fallback": "gemini-flash-lite-latest",
            "groq": "openai/gpt-oss-120b", "ollama_url": "http://localhost:11434"}
FAILOVER_TIMEOUT = 6.0  # cap per attempt when another provider is still left to try

log = logging.getLogger("planlab.llm")
last_model: str | None = None      # e.g. "groq/openai/gpt-oss-120b", for logs
_local = threading.local()          # which provider answered this thread's last call
_cooldown: dict[str, float] = {}   # "provider/model" -> monotonic time it may be retried


@dataclass(frozen=True)
class Target:
    provider: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"


def models() -> list[str]:
    """The Gemini models tried, in order (kept for callers and tests)."""
    primary = os.getenv("GEMINI_MODEL") or DEFAULTS["gemini"]
    fallback = os.getenv("GEMINI_FALLBACK_MODEL") or DEFAULTS["gemini_fallback"]
    return [primary] if primary == fallback else [primary, fallback]


def chain() -> list[Target]:
    """Every configured provider/model, in the order they are tried."""
    order = [p.strip().lower() for p in
             (os.getenv("LLM_PROVIDERS") or "gemini,groq,ollama").split(",") if p.strip()]
    out: list[Target] = []
    for p in order:
        if p == "gemini" and os.getenv("GEMINI_API_KEY"):
            out += [Target("gemini", m) for m in models()]
        elif p == "groq" and os.getenv("GROQ_API_KEY"):
            out.append(Target("groq", os.getenv("GROQ_MODEL") or DEFAULTS["groq"]))
        elif p == "ollama" and os.getenv("OLLAMA_MODEL"):
            out.append(Target("ollama", os.environ["OLLAMA_MODEL"]))
    return out


def provider_used() -> str:
    """The provider ("gemini", "groq" or "ollama") that answered this thread's last call."""
    return getattr(_local, "provider", None) or "gemini"


def enabled() -> bool:
    return bool(chain())


def status() -> list[dict]:
    """The chain and which entries are cooling down (for /health)."""
    now = time.monotonic()
    return [{"provider": t.provider, "model": t.model,
             "cooldown_s": max(0, round(_cooldown.get(t.key, 0) - now))} for t in chain()]


# ---------- provider calls ----------

def _gemini(t: Target, prompt: str, json_mode: bool, temperature: float, timeout: float) -> str:
    cfg: dict = {"temperature": temperature}
    if json_mode:
        cfg["responseMimeType"] = "application/json"
    # Key goes in a header, never the URL, so it can't leak into error messages or logs.
    r = httpx.post(GEMINI_URL.format(model=t.model), timeout=timeout,
                   headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                   json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": cfg})
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _groq(t: Target, prompt: str, json_mode: bool, temperature: float, timeout: float) -> str:
    """Groq speaks the OpenAI chat-completions API."""
    messages = [{"role": "user", "content": prompt}]
    body: dict = {"model": t.model, "messages": messages, "temperature": temperature}
    if json_mode:
        # JSON mode returns an object, so ask for arrays to be wrapped; callers unwrap them.
        messages.insert(0, {"role": "system", "content":
                            "Reply with one valid JSON object and nothing else. If you are asked "
                            'for a JSON array, return {"items": [...]}.'})
        body["response_format"] = {"type": "json_object"}
    r = httpx.post(f"{GROQ_URL}/chat/completions", json=body, timeout=timeout,
                   headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"].get("content") or ""


def _ollama_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or DEFAULTS["ollama_url"]).rstrip("/")


def _ollama(t: Target, prompt: str, json_mode: bool, temperature: float, timeout: float) -> str:
    """Local Ollama via its native API, which keeps the model loaded between calls."""
    body: dict = {"model": t.model, "stream": False, "keep_alive": "30m",
                  "messages": [{"role": "user", "content": prompt}],
                  "options": {"temperature": temperature}}
    if json_mode:
        body["format"] = "json"
    r = httpx.post(f"{_ollama_url()}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"].get("content") or ""


def warm_up() -> None:
    """Load the local Ollama model in the background so the first real call isn't a cold start."""
    model = os.getenv("OLLAMA_MODEL")
    if not model:
        return

    def run():
        try:
            httpx.post(f"{_ollama_url()}/api/generate",
                       json={"model": model, "keep_alive": "30m"}, timeout=120)
        except Exception as exc:
            log.warning("Ollama warm-up failed (%s): is `ollama serve` running?", type(exc).__name__)

    threading.Thread(target=run, daemon=True, name="ollama-warm-up").start()


CALLS = {"gemini": _gemini, "groq": _groq, "ollama": _ollama}


def _clean(text: str, json_mode: bool) -> str:
    """Drop reasoning blocks some local models emit, and code fences around JSON."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if json_mode:
        m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.S)
        text = m.group(1) if m else text
    return text


def _cooldown_for(exc: Exception) -> float:
    base = float(os.getenv("LLM_COOLDOWN") or 60)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            try:
                return min(300.0, max(base, float(exc.response.headers.get("retry-after", 0))))
            except ValueError:
                return base
        if code < 500:   # bad request / auth: retrying soon won't help, but don't lock out forever
            return base * 5
    return base          # 5xx, timeout, connection refused (e.g. Ollama not running)


def generate(prompt: str, *, json_mode: bool = False, temperature: float = 0.4,
             timeout: float = 20.0) -> str:
    """Return the first answer from the provider chain. Raises RuntimeError if none answered."""
    global last_model
    targets = chain()
    if not targets:
        raise RuntimeError("no LLM configured (set GEMINI_API_KEY, GROQ_API_KEY or OLLAMA_MODEL)")
    now = time.monotonic()
    todo = [t for t in targets if _cooldown.get(t.key, 0) <= now]
    if not todo:
        # Every provider failed within the last minute: let the caller's deterministic fallback
        # answer at once instead of paying another round of timeouts.
        raise RuntimeError("all LLM providers are cooling down after recent failures")
    errors = []
    for i, t in enumerate(todo):
        last = i == len(todo) - 1
        try:
            text = _clean(CALLS[t.provider](t, prompt, json_mode, temperature,
                                            timeout if last else min(timeout, FAILOVER_TIMEOUT)),
                          json_mode)
            if not text:
                raise ValueError("empty reply")
            _cooldown.pop(t.key, None)
            last_model, _local.provider = t.key, t.provider
            return text
        except Exception as exc:  # try the next provider
            detail = (f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
                      else type(exc).__name__)
            _cooldown[t.key] = time.monotonic() + _cooldown_for(exc)
            errors.append(f"{t.key}: {detail}")
            log.warning("LLM call failed on %s: %s", t.key, detail)
    raise RuntimeError("; ".join(errors))
