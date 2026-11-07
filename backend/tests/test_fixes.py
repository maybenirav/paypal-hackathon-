"""Tests for the council fixes: persistence, PayPal constraints, fair rollout, readiness."""
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import Arm
from app.store import Store, store
from app.validation import validate_arms

client = TestClient(app)


def make(n=3, **kw):
    r = client.post("/experiments", json={"goal": "raise annual uptake", "num_arms": n, **kw})
    assert r.status_code == 200
    return r.json()


def test_experiments_survive_a_restart():
    eid = make()["id"]
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    reloaded = Store()               # what a fresh process does at startup
    exp = reloaded.get(eid)
    assert exp is not None and exp.status == "winner" and exp.winner == "annual_discount"
    assert len(exp.events) == len(store.get(eid).events)


def test_validation_rejects_out_of_bounds_and_copycat_arms():
    base = Arm(arm_id="base", name="b", price=10, is_baseline=True)
    arms = [base,
            Arm(arm_id="huge", name="h", price=45),             # 4.5x the baseline
            Arm(arm_id="copy", name="c", price=10),             # identical to baseline
            Arm(arm_id="annual", name="a", price=100, interval="YEAR"),
            Arm(arm_id="cheaper", name="p", price=8)]
    v = {r.arm_id: r for r in validate_arms(arms)}
    assert not v["huge"].valid and "allowed range" in v["huge"].errors[0]
    assert not v["copy"].valid
    assert v["annual"].valid and v["annual"].rollout == "new_signups_only" and v["annual"].warnings
    assert v["cheaper"].valid and v["cheaper"].rollout == "in_place"


def test_readiness_reported_on_create_and_metrics():
    small = make(monthly_visitors=300)
    assert small["readiness"]["feasible"] is False
    m = client.get(f"/experiments/{small['id']}/metrics").json()
    assert m["readiness"]["needed_per_arm"] > 0 and m["events"] == {"real": 0, "simulated": 0}


def test_cheaper_in_place_winner_lowers_price_for_everyone():
    eid = make()["id"]
    exp = store.get(eid)
    cheaper = exp.arms[2].model_copy(update={"interval": "MONTH", "trial_days": 0, "price": 9})
    exp.arms[2] = cheaper
    exp.winner, exp.status = cheaper.arm_id, "winner"
    store.save(exp)
    p = client.post(f"/experiments/{eid}/rollout/propose").json()
    assert p["kind"] == "update_pricing_scheme" and not p["grandfathered"]
    assert p["requests"][0]["path"].endswith("/update-pricing-schemes")


def test_simulated_save_actions_never_reach_paypal():
    from app import paypal_sdk
    req = {"method": "POST", "path": "/v1/billing/subscriptions/SIM-x/suspend",
           "body": {"reason": "x"}, "simulated": True}
    assert paypal_sdk.execute(req)["status"] == "dry_run"
    assert paypal_sdk.execute({"method": "POST", "path": "/v1/billing/plans/PLAN_ID_a/deactivate",
                               "body": {}})["status"] == "dry_run"


def test_timeline_has_checkpoints_and_decision():
    eid = make()["id"]
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    t = client.get(f"/experiments/{eid}/timeline").json()
    names = [r["name"] for r in t["tasks"]["rows"]]
    assert sum("checkpoint" in n for n in names) == 4 and names[-1].startswith("Decision")
    assert len(t["dependencies"]["rows"]) == len(t["tasks"]["rows"]) - 1


def test_demo_replay_keeps_real_events():
    eid = make()["id"]
    real = [{"experiment_id": eid, "arm_id": "baseline", "subscriber_id": "I-REAL", "type": "SIGNUP",
             "ts": "2026-11-07T10:00:00", "source": "real"}]
    client.post(f"/experiments/{eid}/events", json=real)
    r = client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"}).json()
    assert r["real_events_kept"] == 1
    assert client.get(f"/experiments/{eid}/metrics").json()["events"]["real"] == 1


def test_sdk_plan_model_keeps_infinite_billing():
    # Regression: the SDK parser drops total_cycles=0, which PayPal turns into "1 cycle".
    from app.paypal_plans import build_plan_payload
    from app.paypal_sdk import plan_model
    payload = build_plan_payload(Arm(arm_id="a", name="a", price=10, trial_days=7), "PROD", "x")
    cycles = plan_model(payload).billing_cycles
    assert [c.total_cycles for c in cycles] == [1, 0]
