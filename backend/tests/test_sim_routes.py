"""Person D: tests for the simulated clock endpoints."""
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import simulate
from app.main import app
from app.store import store

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)   # use the fallback arms
    monkeypatch.setattr(simulate, "START", simulate.START)
    monkeypatch.setattr(simulate, "DAYS", simulate.DAYS)


def make():
    r = client.post("/experiments", json={"goal": "raise annual uptake", "num_arms": 3})
    return r.json()["id"]


def manual_start(eid, scenario="guardrail"):
    r = client.post(f"/experiments/{eid}/sim/start",
                    json={"scenario": scenario, "seed": 42, "seconds_per_day": 0})
    assert r.status_code == 200
    return r.json()


def test_start_releases_only_day_zero():
    eid = make()
    s = manual_start(eid)
    assert s["day"] == 0 and s["status"] == "running"
    assert 0 < s["released_events"] < s["total_events"]


def test_step_moves_forward_and_finishes():
    eid = make()
    manual_start(eid)
    s = client.post(f"/experiments/{eid}/sim/step?days=5").json()
    assert s["day"] == 5
    s = client.post(f"/experiments/{eid}/sim/step?days=100").json()
    assert s["status"] == "done" and s["released_events"] == s["total_events"]


def test_guardrail_after_full_run():
    eid = make()
    manual_start(eid)
    client.post(f"/experiments/{eid}/sim/step?days=100")
    d = client.post(f"/experiments/{eid}/decide").json()
    assert d["action"] == "stop_arm" and d["arm_id"] == "monthly_trial"


def test_real_events_survive_restart_and_sit_inside_window():
    eid = make()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    real = [{"experiment_id": eid, "arm_id": "baseline", "subscriber_id": "I-REAL1",
             "type": "SIGNUP", "ts": now.isoformat(), "source": "real"}]
    assert client.post(f"/experiments/{eid}/events", json=real).status_code == 200
    manual_start(eid)
    s = manual_start(eid, "winner")          # restart: must not delete the real sign-up
    assert s["real_signups"] == 1
    exp = store.get(eid)
    reals = [e for e in exp.events if e.source == "real"]
    assert len(reals) == 1
    assert simulate.START <= reals[0].ts <= simulate.START + timedelta(days=simulate.DAYS)


def test_background_clock_ticks_and_pauses():
    eid = make()
    client.post(f"/experiments/{eid}/sim/start",
                json={"scenario": "guardrail", "seconds_per_day": 0.01})
    time.sleep(0.3)
    s = client.post(f"/experiments/{eid}/sim/pause").json()
    assert s["day"] >= 3 and s["status"] in ("paused", "done")


def test_errors():
    assert client.get("/experiments/zzz/sim/status").status_code == 404
    eid = make()
    assert client.get(f"/experiments/{eid}/sim/status").status_code == 409
    r = client.post(f"/experiments/{eid}/sim/start", json={"scenario": "nope"})
    assert r.status_code == 422
