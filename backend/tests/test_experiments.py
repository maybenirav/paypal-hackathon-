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
    client.put(f"/experiments/{eid}/guardrails", json={"auto_stop": False})
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


def test_assistant_handles_retention_curve():
    rows = [{"arm_id": "a", "is_baseline": True, "retention": [1.0, 0.9]},
            {"arm_id": "b", "is_baseline": False, "retention": [1.0, 0.7]},
            {"arm_id": "c", "is_baseline": False, "retention": []}]
    below = ask("show 60-day retention below 80%", rows)
    assert below["filter"] == {"metric": "retention", "op": "<", "value": 0.8}
    assert [r["arm_id"] for r in below["rows"]] == ["b"]   # no sign-ups means no data, not "low"
    assert [r["arm_id"] for r in ask("retention below baseline", rows)["rows"]] == ["b"]
    assert ask("highest retention", rows)["rows"][0]["arm_id"] == "a"


def test_assistant_best_churn_means_lowest():
    rows = [{"arm_id": "a", "is_baseline": True, "churn": 0.1},
            {"arm_id": "b", "is_baseline": False, "churn": 0.3}]
    assert ask("best churn", rows)["rows"][0]["arm_id"] == "a"
    assert ask("highest churn", rows)["rows"][0]["arm_id"] == "b"
    assert [r["arm_id"] for r in ask("churn above 20", rows)["rows"]] == ["b"]


def test_assistant_on_live_metrics_never_errors():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    for q in ("retention below 80%", "refund above 5%", "best ltv", "hello there"):
        assert client.post(f"/experiments/{eid}/ask", json={"question": q}).status_code == 200


def test_assistant_answers_for_the_requested_day():
    eid = make(4)
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    q = {"question": "highest visitors"}
    early = client.post(f"/experiments/{eid}/ask?days=7", json=q).json()["rows"][0]
    shown = {r["arm_id"]: r for r in client.get(f"/experiments/{eid}/metrics?days=7").json()["rows"]}
    late = client.post(f"/experiments/{eid}/ask", json=q).json()["rows"][0]
    assert early["visitors"] == shown[early["arm_id"]]["visitors"]
    assert early["visitors"] < late["visitors"]


def test_guardrail_stop_explains_the_breach_not_the_winning_stats():
    # On day 14 the risky arm is winning on conversion but already over the refund ceiling
    eid = make(4)
    client.put(f"/experiments/{eid}/guardrails", json={"auto_stop": False})
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    d = client.post(f"/experiments/{eid}/decide?days=14").json()
    assert d["action"] == "stop_arm" and d["arm_id"] == "premium_monthly"
    assert "Refund rate" in d["explanation"] and "credible interval" not in d["explanation"]


def test_experiment_detail_restores_state_after_reload():
    eid = make()
    d = client.get(f"/experiments/{eid}").json()
    assert d["goal"] == "raise annual uptake" and len(d["arms"]) == 3 and len(d["validation"]) == 3
    assert d["events"] == {"real": 0, "simulated": 0}
    assert d["scenario"] is None and d["last_decision"] is None and d["rollout"] is None

    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide?days=60")
    client.post(f"/experiments/{eid}/rollout/propose")
    d = client.get(f"/experiments/{eid}").json()
    assert d["scenario"] == "winner" and d["events"]["real"] == 0 and d["events"]["simulated"] > 0
    assert d["last_decision"]["action"] == "winner" and d["last_decision"]["as_of_day"] == 60
    assert d["rollout"]["status"] == "proposed" and d["winner"] == "annual_discount"
    assert client.get("/experiments/zzz").status_code == 404


def test_rerunning_a_demo_clears_decision_and_rollout():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    client.post(f"/experiments/{eid}/rollout/propose")
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    d = client.get(f"/experiments/{eid}").json()
    assert d["scenario"] == "guardrail" and d["status"] == "stopped_arm" and d["winner"] is None
    assert d["last_decision"] is None and d["rollout"] is None


def test_metrics_carry_guardrails_for_the_requested_day():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    early = client.get(f"/experiments/{eid}/metrics?days=1").json()
    late = client.get(f"/experiments/{eid}/metrics").json()
    assert late["window_days"] == 60
    breached = {a["arm_id"] for a in late["guardrails"]["arms"] if not a["ok"]}
    assert "monthly_trial" in breached
    assert not [a for a in early["guardrails"]["arms"] if not a["ok"]]   # below min_signups on day 1
    assert late["guardrails"]["config"]["retention_floor"] == 0.80


def test_mrr_series_follows_real_event_dates():
    eid = make()
    ev = [{"experiment_id": eid, "arm_id": "baseline", "subscriber_id": "s1", "type": t,
           "ts": ts, "source": "real", "amount": a}
          for t, ts, a in (("VISIT", "2026-11-05T10:00:00", 0), ("SIGNUP", "2026-11-05T10:01:00", 0),
                           ("PAYMENT", "2026-11-07T09:00:00", 10))]
    assert client.post(f"/experiments/{eid}/events", json=ev).status_code == 200
    m = client.get(f"/experiments/{eid}/metrics").json()
    assert m["window_days"] == 3
    assert [p["day"] for p in m["mrr_series"]] == [0, 1, 2, 3]
    assert m["mrr_series"][-1]["baseline"] == 10
    assert client.get(f"/experiments/{eid}").json()["events"] == {"real": 3, "simulated": 0}
