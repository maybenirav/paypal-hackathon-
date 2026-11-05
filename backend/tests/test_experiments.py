import pytest
from fastapi.testclient import TestClient

from app.assistant import ask
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def make(n=3):
    r = client.post("/experiments", json={"goal": "raise annual uptake", "num_arms": n})
    assert r.status_code == 200
    return r.json()["id"]


def test_winner_scenario_end_to_end():
    eid = make()
    assert client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"}).status_code == 200
    d = client.post(f"/experiments/{eid}/decide").json()
    assert d["action"] == "winner" and d["arm_id"] == "annual_discount"
    m = client.get(f"/experiments/{eid}/metrics").json()
    assert m["status"] == "winner" and m["winner"] == "annual_discount"
    assert len(m["mrr_series"]) > 5


def test_guardrail_scenario_stops_risky_arm():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    d = client.post(f"/experiments/{eid}/decide").json()
    assert d["action"] == "stop_arm" and d["arm_id"] == "monthly_trial"
    assert client.get(f"/experiments/{eid}/metrics").json()["stopped_arms"] == ["monthly_trial"]


def test_demo_is_repeatable():
    a, b = make(), make()
    for e in (a, b):
        client.post(f"/experiments/{e}/demo", json={"scenario": "winner", "seed": 3})
    ra = client.get(f"/experiments/{a}/metrics").json()["rows"]
    rb = client.get(f"/experiments/{b}/metrics").json()["rows"]
    assert ra == rb


def test_event_ingest_validates_arm():
    eid = make()
    ev = [{"experiment_id": eid, "arm_id": "nope", "subscriber_id": "s", "type": "SIGNUP",
           "ts": "2026-01-02T00:00:00"}]
    assert client.post(f"/experiments/{eid}/events", json=ev).status_code == 422


def test_unknown_experiment_and_scenario():
    assert client.get("/experiments/zzz/metrics").status_code == 404
    eid = make()
    assert client.post(f"/experiments/{eid}/demo", json={"scenario": "x"}).status_code == 422


def test_assistant_filters_churn_above_baseline():
    rows = [{"arm_id": "b", "is_baseline": True, "churn": 0.1},
            {"arm_id": "x", "is_baseline": False, "churn": 0.3},
            {"arm_id": "y", "is_baseline": False, "churn": 0.05}]
    r = ask("show arms with churn above baseline", rows)
    assert [x["arm_id"] for x in r["rows"]] == ["x"]
    assert r["filter"] == {"metric": "churn", "op": ">", "value": 0.1}


def test_assistant_ranking_and_percent():
    rows = [{"arm_id": "a", "is_baseline": True, "conversion": 0.1},
            {"arm_id": "b", "is_baseline": False, "conversion": 0.2}]
    assert ask("best conversion", rows)["rows"][0]["arm_id"] == "b"
    assert [r["arm_id"] for r in ask("conversion over 15%", rows)["rows"]] == ["b"]
