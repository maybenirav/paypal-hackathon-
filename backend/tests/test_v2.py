import random

import pytest
from fastapi.testclient import TestClient

from app.engine import bayes
from app.engine.guardrails import GuardrailConfig, check_arm
from app.main import app
from app.save_agent import SaveRules, SubscriberContext, choose_offer

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def make(n=3, goal="raise annual uptake"):
    return client.post("/experiments", json={"goal": goal, "num_arms": n}).json()["id"]


# ---- Bayesian ------------------------------------------------------------

def test_posterior_mean_hand_calc():
    # Beta(1+30, 1+70) -> mean 31/102
    assert bayes.posterior(30, 100).mean == pytest.approx(31 / 102)


def test_credible_interval_contains_mean_and_narrows():
    small = bayes.credible_interval(bayes.posterior(10, 100))
    big = bayes.credible_interval(bayes.posterior(100, 1000))
    assert small[0] < 11 / 102 < small[1]
    assert (big[1] - big[0]) < (small[1] - small[0])


def test_prob_best_clear_winner_and_symmetry():
    pb = bayes.prob_best({"a": bayes.posterior(100, 1000), "b": bayes.posterior(150, 1000)})
    assert pb["b"] > 0.99
    tie = bayes.prob_best({"a": bayes.posterior(50, 500), "b": bayes.posterior(50, 500)})
    assert tie["a"] == pytest.approx(0.5, abs=0.05)


def test_thompson_shifts_traffic_but_keeps_exploration():
    w = bayes.thompson_allocation({"a": bayes.posterior(100, 1000), "b": bayes.posterior(200, 1000)})
    assert w["b"] > 0.9 and w["a"] >= 0.04
    assert sum(w.values()) == pytest.approx(1.0)


def test_assign_arm_follows_weights():
    rng = random.Random(1)
    picks = [bayes.assign_arm({"a": 0.8, "b": 0.2}, rng) for _ in range(2000)]
    assert 0.76 < picks.count("a") / 2000 < 0.84


def test_posterior_validation():
    with pytest.raises(ValueError):
        bayes.posterior(5, 3)


# ---- Guardrails ----------------------------------------------------------

def row(**kw):
    base = {"arm_id": "x", "signups": 100, "churn": 0.1, "refund_rate": 0.01, "dispute_rate": 0.0}
    return {**base, **kw}


def test_guardrails_each_trip_independently():
    cfg = GuardrailConfig()
    assert check_arm(row(), cfg) == []
    assert [b.guardrail for b in check_arm(row(churn=0.3), cfg)] == ["retention_floor"]
    assert [b.guardrail for b in check_arm(row(refund_rate=0.2), cfg)] == ["refund_ceiling"]
    assert [b.guardrail for b in check_arm(row(dispute_rate=0.05), cfg)] == ["dispute_ceiling"]


def test_guardrails_ignore_tiny_samples_and_respect_config():
    assert check_arm(row(signups=5, churn=0.9), GuardrailConfig()) == []
    assert check_arm(row(churn=0.3), GuardrailConfig(retention_floor=0.5)) == []


# ---- Save agent ----------------------------------------------------------

def ctx(**kw):
    base = dict(subscriber_id="s1", subscription_id="I-1", plan_id="P-1", trigger="PAYMENT_FAILED",
                tenure_days=90, payments_made=3, price=10.0)
    return SubscriberContext(**{**base, **kw})


def test_policy_by_reason():
    r = SaveRules(downgrade_plan_id="P-CHEAP")
    assert choose_offer(ctx(), r).kind == "pause"
    assert choose_offer(ctx(trigger="CANCEL_INTENT", reason_hint="price"), r).kind == "discount"
    assert choose_offer(ctx(trigger="CANCEL_INTENT"), r).kind == "downgrade"


def test_policy_respects_rules_and_limits():
    assert choose_offer(ctx(), SaveRules(allow_pause=False, downgrade_plan_id=None)).kind == "discount"
    assert choose_offer(ctx(), SaveRules(allow_pause=False, allow_discount=False)).kind == "let_go"
    assert choose_offer(ctx(previous_saves=1), SaveRules()).kind == "let_go"
    assert choose_offer(ctx(payments_made=0), SaveRules()).kind == "let_go"


def test_discount_request_math():
    o = choose_offer(ctx(trigger="CANCEL_INTENT", reason_hint="price"), SaveRules(max_discount_pct=20))
    cycle = o.paypal_request["body"]["plan"]["billing_cycles"][0]
    assert cycle["pricing_scheme"]["fixed_price"]["value"] == "8.00" and cycle["total_cycles"] == 3
    assert o.paypal_request["path"].endswith("/I-1/revise")


def test_save_api_log_and_override():
    a = client.post("/save/handle", json=ctx().model_dump()).json()
    assert a["kind"] == "pause" and a["status"] == "dry_run"
    # second failure for the same subscriber: save limit reached -> let go
    b = client.post("/save/handle", json=ctx().model_dump()).json()
    assert b["kind"] == "let_go"
    o = client.post(f"/save/log/{a['id']}/override", json={"kind": "discount", "note": "VIP"})
    assert o.status_code == 200 and o.json()["overridden_by"] == a["id"]
    log = client.get("/save/log").json()
    assert any(x["status"] == "overridden" for x in log["actions"])
    assert client.post(f"/save/log/{a['id']}/override", json={"kind": "pause"}).status_code == 422
    assert client.post("/save/log/nope/override", json={"kind": "pause"}).status_code == 404
    assert client.post(f"/save/log/{b['id']}/override", json={"kind": "bogus"}).status_code == 422


def test_failed_executor_still_logs(monkeypatch):
    from app import save_agent

    def boom(_):
        raise RuntimeError("paypal down")
    monkeypatch.setattr(save_agent.save_log, "executor", boom)
    a = client.post("/save/handle", json=ctx().model_dump()).json()
    assert a["status"] == "failed"


def test_sweep_handles_failed_payments_once():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    first = client.post("/save/sweep?limit=1000").json()
    assert first["handled"] > 0
    assert client.post("/save/sweep?limit=1000").json()["handled"] == 0


# ---- Experiment flow -----------------------------------------------------

def test_metrics_include_bayes_and_allocation_shifts_to_winner():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    rows = {r["arm_id"]: r for r in client.get(f"/experiments/{eid}/metrics").json()["rows"]}
    assert rows["annual_discount"]["allocation"] > 0.8
    lo, hi = rows["annual_discount"]["conv_ci"]
    assert lo < rows["annual_discount"]["conversion"] < hi
    w = client.get(f"/experiments/{eid}/allocation").json()["weights"]
    assert sum(w.values()) == pytest.approx(1.0, abs=0.01)
    assert client.post(f"/experiments/{eid}/assign").json()["arm_id"] in w


def test_guardrail_scenario_trips_absolute_guardrail_and_zeroes_allocation():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    g = client.get(f"/experiments/{eid}/guardrails").json()["arms"]
    assert [a["arm_id"] for a in g if not a["ok"]] == ["monthly_trial"]
    d = client.post(f"/experiments/{eid}/decide").json()
    assert d["action"] == "stop_arm" and "floor" in d["reasons"]["monthly_trial"]
    rows = {r["arm_id"]: r for r in client.get(f"/experiments/{eid}/metrics").json()["rows"]}
    assert rows["monthly_trial"]["allocation"] == 0 and rows["monthly_trial"]["status"] == "stopped"


def test_guardrail_config_is_adjustable():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    client.put(f"/experiments/{eid}/guardrails", json={"retention_floor": 0.5, "refund_ceiling": 0.5})
    g = client.get(f"/experiments/{eid}/guardrails").json()["arms"]
    assert all(a["ok"] for a in g)
    assert client.put(f"/experiments/{eid}/guardrails", json={"bogus": 1}).status_code == 422


def test_rollout_requires_winner_then_proposes_and_approves():
    eid = make()
    assert client.post(f"/experiments/{eid}/rollout/propose").status_code == 409
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    assert client.post(f"/experiments/{eid}/decide").json()["action"] == "winner"
    p = client.post(f"/experiments/{eid}/rollout/propose").json()
    assert p["status"] == "proposed" and p["winner"] == "annual_discount"
    assert p["kind"] == "switch_default_plan"          # annual vs monthly baseline
    assert sum("/deactivate" in r["path"] for r in p["requests"]) == 2
    a = client.post(f"/experiments/{eid}/rollout/approve").json()
    assert a["status"] == "approved" and len(a["results"]) == len(a["requests"])
    assert client.post(f"/experiments/{eid}/rollout/approve").status_code == 409


# ---- Memory --------------------------------------------------------------

def test_memory_recorded_and_changes_next_proposal():
    eid = make(4)
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    client.post(f"/experiments/{eid}/decide")
    mem = client.get("/memory").json()
    assert len(mem["records"]) == 1 and mem["records"][0]["stopped"] == ["premium_monthly"]

    nxt = client.post("/experiments", json={"goal": "again", "num_arms": 4}).json()
    ids = [a["arm_id"] for a in nxt["arms"]]
    assert nxt["memory_used"] == 1
    assert "premium_monthly" not in ids and ids[0] == "baseline"


def test_memory_reprioritises_past_winner():
    eid = make(4)
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    nxt = client.post("/experiments", json={"goal": "again", "num_arms": 2}).json()
    assert [a["arm_id"] for a in nxt["arms"]] == ["baseline", "annual_discount"]
    assert "previous experiment" in nxt["arms"][1]["reason"]
