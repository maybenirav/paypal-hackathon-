import math

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import Arm
from app.validation import validate_arm

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("APIMATIC_API_KEY", raising=False)


def test_arms_fallback_valid():
    r = client.post("/agent/arms", json={"goal": "raise annual uptake", "num_arms": 4})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fallback" and len(body["arms"]) == 4
    assert all(v["valid"] for v in body["validation"])
    annual = next(v for v in body["validation"] if v["arm_id"] == "annual_discount")
    assert annual["paypal_payload"]["billing_cycles"][0]["frequency"]["interval_unit"] == "YEAR"


def test_trial_creates_trial_cycle():
    p = validate_arm(Arm(arm_id="t", name="t", price=9.99, trial_days=7)).paypal_payload
    assert [c["tenure_type"] for c in p["billing_cycles"]] == ["TRIAL", "REGULAR"]


def test_validation_catches_bad_currency():
    v = validate_arm(Arm(arm_id="x", name="x", price=5, currency="US"))
    assert not v.valid and v.errors


def test_decide_endpoint_guardrail():
    body = {"baseline": {"arm_id": "base", "price": 10, "visitors": 1000, "signups": 100, "churned": 10},
            "arms": [{"arm_id": "risky", "price": 8, "visitors": 1000, "signups": 140, "churned": 40}]}
    r = client.post("/engine/decide", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["action"] == "stop_arm" and j["arm_id"] == "risky"
    assert "risky" in j["explanation"]


def test_decide_endpoint_serializes_infinity_free():
    # baseline churn 0 -> ratio inf must not break JSON
    body = {"baseline": {"arm_id": "b", "price": 10, "visitors": 1000, "signups": 100, "churned": 0},
            "arms": [{"arm_id": "a", "price": 10, "visitors": 1000, "signups": 100, "churned": 30}]}
    r = client.post("/engine/decide", json=body)
    assert r.status_code == 200
    assert not math.isinf(r.json()["arms"][1]["guardrail"]["p_churn"])
