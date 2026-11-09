"""LLM provider chain: Gemini -> Groq -> Ollama failover, cooldowns, JSON cleanup."""
import json

import httpx
import pytest

from app.agent import llm, pricing_agent
from app.schemas import GoalRequest


@pytest.fixture(autouse=True)
def providers(monkeypatch):
    llm._cooldown.clear()
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GEMINI_MODEL", "gem-a")
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gem-b")
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.setenv("GROQ_MODEL", "groq-m")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2")
    monkeypatch.delenv("LLM_PROVIDERS", raising=False)
    yield
    llm._cooldown.clear()


def fake_post(monkeypatch, replies):
    """replies: url-substring -> text to answer, or an int HTTP status to fail with."""
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        req = httpx.Request("POST", url)
        for frag, reply in replies.items():
            if frag in url or frag in str((json or {}).get("model")):
                if isinstance(reply, int):
                    return httpx.Response(reply, request=req)
                if "googleapis" in url:
                    return httpx.Response(200, request=req, json={
                        "candidates": [{"content": {"parts": [{"text": reply}]}}]})
                if "/api/chat" in url:   # Ollama's native API
                    return httpx.Response(200, request=req, json={"message": {"content": reply}})
                return httpx.Response(200, request=req,
                                      json={"choices": [{"message": {"content": reply}}]})
        raise httpx.ConnectError("refused", request=req)

    monkeypatch.setattr(llm.httpx, "post", post)
    return calls


def test_chain_lists_only_configured_providers_in_order(monkeypatch):
    assert [t.key for t in llm.chain()] == ["gemini/gem-a", "gemini/gem-b", "groq/groq-m",
                                             "ollama/llama3.2"]
    monkeypatch.setenv("LLM_PROVIDERS", "groq,gemini")
    monkeypatch.delenv("GEMINI_API_KEY")
    assert [t.key for t in llm.chain()] == ["groq/groq-m"]
    monkeypatch.delenv("GROQ_API_KEY")
    assert not llm.enabled()


def test_gemini_overloaded_fails_over_to_groq_then_skips_gemini(monkeypatch):
    calls = fake_post(monkeypatch, {"gem-a": 503, "gem-b": 503, "groq-m": "price"})
    assert llm.generate("why cancel?") == "price"
    assert llm.provider_used() == "groq"
    assert len(calls) == 3
    # Gemini is cooling down, so the next call goes straight to Groq.
    calls.clear()
    assert llm.generate("again") == "price"
    assert len(calls) == 1 and "groq" in calls[0]
    assert {s["model"]: s["cooldown_s"] > 0 for s in llm.status()} == {
        "gem-a": True, "gem-b": True, "groq-m": False, "llama3.2": False}


def test_ollama_answer_is_cleaned_for_json(monkeypatch):
    fake_post(monkeypatch, {"gem": 500, "groq-m": 429,
                            "llama3.2": '<think>hmm</think>\n```json\n{"ok": true}\n```'})
    assert json.loads(llm.generate("x", json_mode=True)) == {"ok": True}
    assert llm.provider_used() == "ollama"


def test_all_down_fails_fast_then_recovers_after_cooldown(monkeypatch):
    fake_post(monkeypatch, {"gem": 503, "groq-m": 503})   # ollama: connection refused
    with pytest.raises(RuntimeError, match="ollama/llama3.2: ConnectError"):
        llm.generate("x")
    # Everything is cooling down: fail at once, without another round of network calls.
    calls = fake_post(monkeypatch, {"gem-a": "back"})
    with pytest.raises(RuntimeError, match="cooling down"):
        llm.generate("x")
    assert calls == []
    # Once the cooldown passes, the provider is tried again.
    llm._cooldown["gemini/gem-a"] = 0
    assert llm.generate("x") == "back"


def test_pricing_agent_unwraps_json_object_mode(monkeypatch):
    arms = [{"arm_id": "baseline", "name": "Base", "price": 10, "is_baseline": True, "reason": "c"},
            {"arm_id": "high", "name": "High", "price": 13, "is_baseline": False, "reason": "p"}]
    fake_post(monkeypatch, {"gem": 503, "groq-m": json.dumps({"items": arms})})
    out, source = pricing_agent.generate_arms(
        GoalRequest(goal="raise MRR", current_price=10, num_arms=2), mem=pricing_agent.Memory(persist=False))
    assert source == "groq" and [a.arm_id for a in out] == ["baseline", "high"]
