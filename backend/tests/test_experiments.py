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


def _gemini(monkeypatch, reply):
    from app import assistant
    monkeypatch.setattr(assistant.llm, "enabled", lambda: True)

    def fake(prompt, **kw):
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(assistant.llm, "generate", fake)


ROWS = [{"arm_id": "base", "is_baseline": True, "churn": 0.10, "paid_conversion": 0.05, "mrr_per_visitor": 0.75},
        {"arm_id": "x", "is_baseline": False, "churn": 0.30, "paid_conversion": 0.07, "mrr_per_visitor": 0.84},
        {"arm_id": "y", "is_baseline": False, "churn": 0.05, "paid_conversion": 0.06, "mrr_per_visitor": 0.60}]


def test_assistant_gemini_multi_filter_counts_from_real_rows(monkeypatch):
    # Gemini picks the filters; which arms match is computed from the data, not trusted.
    _gemini(monkeypatch, '{"filters": [{"metric": "churn", "op": "<", "value": 0.2},'
                         ' {"metric": "paid_conversion", "op": ">", "value": 0.05}],'
                         ' "sort": {"metric": "mrr_per_visitor", "desc": true},'
                         ' "interpretation": "Arms that convert better without extra churn."}')
    r = ask("which arms convert better than baseline without churning more?", ROWS)
    assert r["source"] == "gemini"
    assert [x["arm_id"] for x in r["rows"]] == ["y"]
    assert len(r["filters"]) == 2 and r["sort"] == {"metric": "mrr_per_visitor", "desc": True}
    assert "1 arm(s)" in r["answer"]


def test_assistant_rejects_hallucinated_column_and_falls_back(monkeypatch):
    _gemini(monkeypatch, '{"filters": [{"metric": "happiness", "op": ">", "value": 1}], "sort": null}')
    r = ask("show arms with churn above baseline", ROWS)
    assert r["source"] == "rules" and [x["arm_id"] for x in r["rows"]] == ["x"]


def test_assistant_falls_back_when_gemini_down(monkeypatch):
    _gemini(monkeypatch, RuntimeError("503"))
    r = ask("best mrr per visitor", ROWS)
    assert r["source"] == "rules" and r["rows"][0]["arm_id"] == "x"


def test_assistant_rules_handle_guard_clause():
    # Works without Gemini: the main comparison plus a "without more churn" guard.
    r = ask("which arms earn more per visitor than baseline without more churn?", ROWS)
    assert r["source"] == "rules"
    assert r["filters"] == [{"metric": "mrr_per_visitor", "op": ">", "value": 0.75},
                            {"metric": "churn", "op": "<", "value": 0.10}]
    assert r["rows"] == []  # x earns more but churns more; y churns less but earns less
