"""One Gemini call path for every agent, with a fallback model.

The configured model (GEMINI_MODEL) is tried first; if it is overloaded (503), rate-limited
or slow, GEMINI_FALLBACK_MODEL is tried. Callers still keep their own deterministic fallback
when both fail, so a demo never breaks on an LLM outage.
"""
from __future__ import annotations

import logging
import os

import httpx

from .. import config  # noqa: F401  (loads .env before keys are read)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
log = logging.getLogger("planlab.llm")
last_model: str | None = None


def models() -> list[str]:
    primary = os.getenv("GEMINI_MODEL") or "gemini-flash-latest"
    fallback = os.getenv("GEMINI_FALLBACK_MODEL") or "gemini-flash-lite-latest"
    return [primary] if primary == fallback else [primary, fallback]


def enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def generate(prompt: str, *, json_mode: bool = False, temperature: float = 0.4,
             timeout: float = 20.0) -> str:
    """Return the model's text. Raises RuntimeError if no model answered."""
    global last_model
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    cfg: dict = {"temperature": temperature}
    if json_mode:
        cfg["responseMimeType"] = "application/json"
    errors = []
    chain = models()
    for i, model in enumerate(chain):
        # Fail over fast when the primary model is overloaded; give the last one full time.
        t = min(timeout, 8.0) if i < len(chain) - 1 else timeout
        try:
            # Key goes in a header, never the URL, so it can't leak into error messages or logs.
            r = httpx.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": key},
                           timeout=t,
                           json={"contents": [{"parts": [{"text": prompt}]}],
                                 "generationConfig": cfg})
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            last_model = model
            return text.strip()
        except Exception as exc:  # try the next model
            detail = (f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
                      else type(exc).__name__)
            errors.append(f"{model}: {detail}")
            log.warning("Gemini call failed on %s: %s", model, detail)
    raise RuntimeError("; ".join(errors))
