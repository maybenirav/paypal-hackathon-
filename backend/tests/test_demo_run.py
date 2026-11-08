"""Person D: tests for the one-call Run demo (offline: no PayPal, no KERNEL)."""
import time

import pytest
from fastapi.testclient import TestClient

from app import simulate
from app.main import app
from app.memory import memory

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)            # fallback arms
    monkeypatch.setattr(simulate, "START", simulate.START)
    monkeypatch.setattr(simulate, "DAYS", simulate.DAYS)
    monkeypatch.setattr(memory, "records", list(memory.records))   # don't leak into other tests


def run_offline(scenario):
    r = client.post("/demo/run", json={"scenario": scenario, "seed": 42, "real_signups": 0,
                                       "publish": False, "seconds_per_day": 0})
    assert r.status_code == 200, r.text
    eid = r.json()["experiment_id"]
    end = time.time() + 30
    while time.time() < end:
        s = client.get(f"/demo/{eid}").json()
        if s["status"] in ("done", "failed"):
            return s
        time.sleep(0.1)
    raise AssertionError(f"demo did not finish: {s}")


def test_guardrail_demo_stops_risky_arm():
    s = run_offline("guardrail")
    assert s["status"] == "done", s["error"]
    assert "monthly_trial" in s["experiment"]["stopped_arms"]
    assert any(d["action"] == "stop_arm" for d in s["decisions"])
    assert s["sim"]["status"] == "done"


def test_winner_demo_picks_annual():
    s = run_offline("winner")
    assert s["status"] == "done", s["error"]
    assert s["experiment"]["winner"] == "annual_discount"


def test_demo_validation():
    assert client.get("/demo/zzz").status_code == 404
    r = client.post("/demo/run", json={"real_signups": 1, "publish": False})
    assert r.status_code == 422
