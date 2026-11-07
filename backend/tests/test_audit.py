"""Regression tests for the flaws found in the full audit. Each one failed before its fix."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import simulate
from app.agent import pricing_agent
from app.main import app
from app.paypal_plans import build_plan_payload
from app.save_agent import SaveRules, SubscriberContext, choose_offer
from app.schemas import Arm
from app.store import store
from app.validation import validate_arm

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("APIMATIC_API_KEY", raising=False)


def make(n=3):
    return client.post("/experiments", json={"goal": "raise annual uptake", "num_arms": n}).json()["id"]


def event(eid, arm, sid, kind, ts="2026-11-05T10:00:00", **kw):
    return {"experiment_id": eid, "arm_id": arm, "subscriber_id": sid, "type": kind, "ts": ts,
            "source": "real", **kw}


def rows_of(eid, query=""):
    return {r["arm_id"]: r for r in client.get(f"/experiments/{eid}/metrics{query}").json()["rows"]}


# ---- inputs that used to crash the API and break the experiment for good ------------------------

def test_signup_without_a_recorded_visit_does_not_crash_metrics():
    eid = make()
    client.post(f"/experiments/{eid}/events", json=[event(eid, "baseline", "s1", "SIGNUP")])
    assert client.get(f"/experiments/{eid}/metrics").status_code == 200
    assert rows_of(eid)["baseline"]["visitors"] == 1


def test_timezone_aware_timestamps_do_not_crash_metrics():
    eid = make()
    client.post(f"/experiments/{eid}/events", json=[
        event(eid, "baseline", "s1", "VISIT", ts="2026-11-05T10:00:00"),
        event(eid, "baseline", "s1", "SIGNUP", ts="2026-11-05T10:00:00+05:30"),
        event(eid, "baseline", "s1", "PAYMENT", ts="2026-11-05T12:00:00Z", amount=10)])
    assert client.get(f"/experiments/{eid}/metrics").status_code == 200


def test_repeated_events_are_counted_once():
    eid = make()
    client.post(f"/experiments/{eid}/events", json=[
        event(eid, "baseline", "s1", "VISIT"), event(eid, "baseline", "s1", "VISIT"),
        event(eid, "baseline", "s1", "SIGNUP"), event(eid, "baseline", "s1", "SIGNUP"),
        event(eid, "baseline", "s1", "CANCELLED", ts="2026-11-06T10:00:00"),
        event(eid, "baseline", "s1", "CANCELLED", ts="2026-11-07T10:00:00")])
    row = rows_of(eid)["baseline"]
    assert (row["visitors"], row["signups"], row["churned"]) == (1, 1, 1)
    assert row["churn"] == 1.0


@pytest.mark.parametrize("bad", [{"retention_floor": "abc"}, {"retention_floor": 2},
                                 {"refund_ceiling": -1}, {"min_signups": -5}, {"churn_ratio": 0.5}])
def test_bad_guardrail_values_are_refused(bad):
    eid = make()
    assert client.put(f"/experiments/{eid}/guardrails", json=bad).status_code == 422
    assert client.get(f"/experiments/{eid}/metrics").status_code == 200


def test_invalid_query_values_are_refused():
    eid = make()
    assert client.post(f"/experiments/{eid}/decide?gamma=0").status_code == 422
    assert client.get(f"/experiments/{eid}/metrics?days=-5").status_code == 422
    assert client.post("/save/sweep?limit=-1").status_code == 422


def test_goal_and_price_limits():
    assert client.post("/experiments", json={"goal": "x" * 2000}).status_code == 422
    assert client.post("/experiments", json={"goal": "fine", "currency": "dollars"}).status_code == 422


# ---- decisions -----------------------------------------------------------------------------------

def test_no_winner_on_a_tiny_sample():
    eid = make(2)
    events = []
    for i in range(5):
        events += [event(eid, "baseline", f"b{i}", "VISIT"), event(eid, "annual_discount", f"a{i}", "VISIT")]
    events += [event(eid, "annual_discount", f"a{i}", "SIGNUP") for i in range(4)]
    client.post(f"/experiments/{eid}/events", json=events)
    d = client.post(f"/experiments/{eid}/decide").json()
    assert d["action"] == "keep_testing"
    assert "sign-ups" in d["explanation"]


def test_stopping_the_winner_clears_the_win():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    assert client.post(f"/experiments/{eid}/decide").json()["action"] == "winner"
    client.post(f"/experiments/{eid}/rollout/propose")
    client.put(f"/experiments/{eid}/guardrails", json={"retention_floor": 0.9999})   # stops it automatically
    state = client.get(f"/experiments/{eid}").json()
    assert "annual_discount" in state["stopped_arms"]
    assert state["winner"] is None and state["rollout"] is None
    assert state["last_decision"] is None     # the page mustn't keep saying this option won


def test_analysis_is_refused_after_a_rollout():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    client.post(f"/experiments/{eid}/rollout/propose")
    client.post(f"/experiments/{eid}/rollout/approve")
    assert client.post(f"/experiments/{eid}/decide").status_code == 409


def test_annual_value_counts_the_full_upfront_payment():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    assert rows_of(eid)["annual_discount"]["ltv"] > 90   # $100 up front, minus a small refund rate


def test_churn_limits_are_read_as_stored_settings():
    eid = make()
    client.put(f"/experiments/{eid}/guardrails", json={"churn_ratio": 1.5})
    assert client.get(f"/experiments/{eid}/guardrails").json()["config"]["churn_ratio"] == 1.5


# ---- traffic split -------------------------------------------------------------------------------

def assign(eid, visitor):
    return client.post(f"/experiments/{eid}/assign", json={"visitor_id": visitor}).json()["arm_id"]


def test_returning_customer_keeps_the_same_offer():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    for i in range(25):
        first = assign(eid, f"v{i}")
        assert all(assign(eid, f"v{i}") == first for _ in range(3))


def test_returning_customer_moves_on_if_their_offer_is_stopped():
    eid = make()
    client.put(f"/experiments/{eid}/guardrails", json={"auto_stop": False})   # so a customer can land on it first
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    visitors = [f"v{i}" for i in range(200)]    # a stopped-to-be option can hold as little as 5% of traffic
    on_trial = [v for v in visitors if assign(eid, v) == "monthly_trial"]
    assert on_trial
    client.post(f"/experiments/{eid}/decide")
    assert all(assign(eid, v) != "monthly_trial" for v in on_trial)


def test_split_follows_value_not_just_signups():
    # The trial has the most sign-ups but is worth the least, so it must not get the most traffic
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    rows = rows_of(eid)
    assert rows["monthly_trial"]["conversion"] > rows["annual_discount"]["conversion"]
    assert rows["annual_discount"]["allocation"] > rows["monthly_trial"]["allocation"]


def test_everyone_gets_the_winner_after_rollout():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    client.post(f"/experiments/{eid}/rollout/propose")
    client.post(f"/experiments/{eid}/rollout/approve")
    assert {assign(eid, f"v{i}") for i in range(15)} == {"annual_discount"}
    assert rows_of(eid)["annual_discount"]["allocation"] == 1


# ---- simulation ----------------------------------------------------------------------------------

def test_trials_are_not_charged_on_signup():
    arm = Arm(arm_id="t", name="Trial", price=10, trial_days=7, is_baseline=False)
    base = Arm(arm_id="b", name="Base", price=10, is_baseline=True)
    events = simulate.generate("e", [base, arm], "winner", 7)
    signups = {e.subscriber_id: e.ts for e in events if e.type == "SIGNUP" and e.arm_id == "t"}
    payments = [e for e in events if e.type == "PAYMENT" and e.arm_id == "t"]
    assert payments and all(e.ts >= signups[e.subscriber_id] + timedelta(days=7) for e in payments)


def test_setup_fee_is_collected_in_simulation():
    arm = Arm(arm_id="f", name="Fee", price=10, setup_fee=5, is_baseline=True)
    events = simulate.generate("e", [arm], "winner", 7)
    assert max(e.amount for e in events if e.type == "PAYMENT") == 15


def test_winner_demo_works_with_ai_named_options():
    # Gemini picks its own arm ids, which the demo's lookup table never matched
    arms = [Arm(arm_id="control", name="Control", price=10, is_baseline=True),
            Arm(arm_id="yearly_saver", name="Yearly", price=100, interval="YEAR"),
            Arm(arm_id="low_intro", name="Intro", price=8)]
    exp = store.create("goal", arms, "gemini", [])
    client.post(f"/experiments/{exp.id}/demo", json={"scenario": "winner"})
    d = client.post(f"/experiments/{exp.id}/decide").json()
    assert d["action"] == "winner" and d["arm_id"] == "yearly_saver"


def test_demo_includes_cancel_intent_for_the_save_agent():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    triggers = {a["trigger"] for a in client.post("/save/sweep?limit=1000").json()["actions"]}
    assert triggers == {"PAYMENT_FAILED", "CANCEL_INTENT"}


# ---- metrics -------------------------------------------------------------------------------------

def test_retention_is_blank_for_weeks_nobody_has_reached():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    curve = rows_of(eid, "?days=14")["baseline"]["retention"]
    assert curve[0] == 1 and curve[8] is None


def test_mrr_excludes_people_who_have_not_paid_yet():
    eid = make()
    client.post(f"/experiments/{eid}/events", json=[
        event(eid, "baseline", "paid", "SIGNUP"), event(eid, "baseline", "paid", "PAYMENT", amount=10),
        event(eid, "baseline", "trial", "SIGNUP")])
    assert rows_of(eid)["baseline"]["mrr"] == 10


# ---- rollout -------------------------------------------------------------------------------------

def rollout_for(arms, winner):
    exp = store.create("goal", arms, "fallback", [])
    exp.winner, exp.status = winner, "winner"
    return client.post(f"/experiments/{exp.id}/rollout/propose").json()


def test_rollout_does_not_deactivate_the_plan_it_just_repriced():
    r = rollout_for([Arm(arm_id="base", name="B", price=10, is_baseline=True, plan_id="P-B"),
                     Arm(arm_id="high", name="H", price=13, plan_id="P-H"),
                     Arm(arm_id="low", name="L", price=8, plan_id="P-L")], "high")
    assert r["kind"] == "update_pricing_scheme"
    paths = [q["path"] for q in r["requests"]]
    assert "/v1/billing/plans/P-B/deactivate" not in paths
    assert "/v1/billing/plans/P-L/deactivate" in paths


def test_rollout_reprices_the_regular_cycle_of_a_trial_plan():
    r = rollout_for([Arm(arm_id="base", name="B", price=10, trial_days=7, is_baseline=True, plan_id="P-B"),
                     Arm(arm_id="high", name="H", price=13, trial_days=7, plan_id="P-H")], "high")
    scheme = r["requests"][0]["body"]["pricing_schemes"][0]
    assert scheme["billing_cycle_sequence"] == 2


def test_rollout_says_who_keeps_their_current_price():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    r = client.post(f"/experiments/{eid}/rollout/propose").json()
    assert r["existing_subscribers"]["baseline"] == rows_of(eid)["baseline"]["active"]


# ---- save agent ----------------------------------------------------------------------------------

def test_same_subscriber_id_in_two_experiments_is_not_mixed_up():
    a, b = make(), make()
    for e in (a, b):
        client.post(f"/experiments/{e}/demo", json={"scenario": "winner"})
    first = client.post(f"/save/sweep?limit=1000&experiment_id={a}").json()["handled"]
    second = client.post(f"/save/sweep?limit=1000&experiment_id={b}").json()["handled"]
    assert first > 0 and first == second
    assert all(x["experiment_id"] == b for x in client.get(f"/save/log?experiment_id={b}").json()["actions"])


def save_ctx(**kw):
    base = dict(subscriber_id="s1", subscription_id="I-1", plan_id="P-1", trigger="PAYMENT_FAILED",
                tenure_days=90, payments_made=3, price=10.0)
    return SubscriberContext(**{**base, **kw})


def test_giving_up_on_a_failed_payment_never_cancels_the_customer():
    offer = choose_offer(save_ctx(payments_made=0), SaveRules())
    assert offer.kind == "let_go" and "cancel" not in offer.paypal_request["path"]
    assert offer.paypal_request["method"] == "NONE"


def test_discount_only_claims_price_sensitivity_when_known():
    unknown = choose_offer(save_ctx(trigger="CANCEL_INTENT"), SaveRules())
    assert unknown.kind == "discount" and "price sensitivity" not in unknown.rationale


def test_rerunning_a_demo_clears_what_it_replaced():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    assert client.post("/save/sweep?limit=1000").json()["handled"] > 0
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    records = client.get("/memory").json()["records"]
    assert len(records) == 1 and records[0]["winner"] is None    # only what the new run found
    assert client.get(f"/save/log?experiment_id={eid}").json()["actions"] == []


def test_override_reports_a_failed_paypal_call(monkeypatch):
    from app import save_agent
    a = client.post("/save/handle", json=save_ctx().model_dump()).json()

    def boom(_):
        raise RuntimeError("paypal down")
    monkeypatch.setattr(save_agent.save_log, "executor", boom)
    o = client.post(f"/save/log/{a['id']}/override", json={"kind": "discount"})
    assert o.status_code == 200 and o.json()["status"] == "failed"


# ---- protecting data -----------------------------------------------------------------------------

def test_demo_will_not_replace_real_events_without_confirmation():
    eid = make()
    client.post(f"/experiments/{eid}/events", json=[event(eid, "baseline", "s1", "VISIT")])
    assert client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"}).status_code == 409
    assert client.get(f"/experiments/{eid}").json()["events"]["real"] == 1
    assert client.post(f"/experiments/{eid}/demo", json={"scenario": "winner", "force": True}).status_code == 200


def test_delete_experiment_removes_everything_about_it():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    client.post("/save/sweep?limit=5")
    assert client.delete(f"/experiments/{eid}").status_code == 200
    assert client.get(f"/experiments/{eid}").status_code == 404
    assert client.get("/memory").json()["records"] == []
    assert client.get("/save/log").json()["actions"] == []
    assert client.delete(f"/experiments/{eid}").status_code == 404


def test_memory_marks_simulated_results():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    record = client.get("/memory").json()["records"][0]
    assert record["simulated"] is True and record["winner_name"] == "Annual, 2 months free"


def test_a_later_win_overrides_an_earlier_stop_in_memory():
    from app.memory import Memory, MemoryRecord
    mem = Memory()
    mem.upsert(MemoryRecord("a", "g", None, ["x"], []))
    mem.upsert(MemoryRecord("b", "g", "x", [], []))
    assert "x" in mem.winners() and "x" not in mem.losers()


# ---- security ------------------------------------------------------------------------------------

def test_other_websites_cannot_trigger_actions():
    eid = make()
    evil = client.post(f"/experiments/{eid}/decide", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    ok = client.post(f"/experiments/{eid}/decide", headers={"Origin": "http://localhost:3000"})
    assert ok.status_code == 200


def test_optional_api_token(monkeypatch):
    from app import security
    monkeypatch.setattr(security, "API_TOKEN", "s3cret")
    assert client.get("/experiments").status_code == 401
    assert client.get("/experiments", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/experiments", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200   # stays open for uptime checks


def test_event_batch_size_is_limited():
    eid = make()
    batch = [event(eid, "baseline", f"s{i}", "VISIT") for i in range(10_001)]
    assert client.post(f"/experiments/{eid}/events", json=batch).status_code == 413


# ---- PayPal plans --------------------------------------------------------------------------------

def test_the_plan_we_validate_is_the_plan_we_send():
    arm = Arm(arm_id="t", name="Trial", price=9.99, trial_days=7, setup_fee=2, reason="why")
    assert validate_arm(arm, "PROD").paypal_payload == build_plan_payload(arm, "PROD", "EXPERIMENT")


def test_apimatic_check_runs_when_configured(monkeypatch):
    calls = []
    monkeypatch.setenv("APIMATIC_API_KEY", "k")
    monkeypatch.setenv("APIMATIC_VALIDATE_URL", "https://example.test/validate")

    class Response:
        status_code = 400
        text = "bad plan"

    monkeypatch.setattr("app.validation.httpx.post", lambda *a, **k: calls.append(a) or Response())
    from app.validation import validate_arms
    result = validate_arms([Arm(arm_id="a", name="A", price=5, is_baseline=True)])
    assert calls and not result[0].valid and "APIMatic" in result[0].errors[0]


# ---- Gemini --------------------------------------------------------------------------------------

class FakeGemini:
    def __init__(self, arms):
        import json
        self.body = {"candidates": [{"content": {"parts": [{"text": json.dumps(arms)}]}}]}

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


GOOD_ARMS = [{"arm_id": "control", "name": "Control", "price": 10, "interval": "MONTH", "is_baseline": True,
              "reason": "Baseline."},
             {"arm_id": "yearly", "name": "Yearly", "price": 100, "interval": "YEAR", "reason": "Annual."}]


def test_default_gemini_model_is_not_the_retired_one():
    import inspect
    assert "gemini-2.0-flash" not in inspect.getsource(pricing_agent)
    assert pricing_agent.DEFAULT_MODEL != "gemini-2.0-flash"


def test_gemini_key_stays_out_of_the_url(monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen.update(url=url, **kw)
        return FakeGemini(GOOD_ARMS)
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr("app.agent.pricing_agent.httpx.post", fake_post)
    r = client.post("/agent/arms", json={"goal": "raise uptake", "current_price": 10, "num_arms": 2}).json()
    assert r["source"] == "gemini"
    assert "secret-key" not in seen["url"] and "params" not in seen
    assert seen["headers"]["x-goog-api-key"] == "secret-key"


def test_gemini_prompt_uses_experiment_memory(monkeypatch):
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    seen = {}

    def fake_post(url, **kw):
        seen.update(kw)
        return FakeGemini(GOOD_ARMS)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr("app.agent.pricing_agent.httpx.post", fake_post)
    client.post("/agent/arms", json={"goal": "raise uptake", "current_price": 10, "num_arms": 2})
    assert "annual_discount" in seen["json"]["contents"][0]["parts"][0]["text"]


@pytest.mark.parametrize("bad", [
    [GOOD_ARMS[0], {**GOOD_ARMS[1], "arm_id": "control"}],                  # duplicate ids
    [GOOD_ARMS[0], {**GOOD_ARMS[1], "price": 5000}],                        # absurd price
    [{**GOOD_ARMS[0], "price": 40}, GOOD_ARMS[1]],                          # baseline isn't the current price
    [{**GOOD_ARMS[0], "is_baseline": True}, {**GOOD_ARMS[1], "is_baseline": True}],   # two baselines
])
def test_bad_gemini_output_falls_back_to_templates(monkeypatch, bad):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr("app.agent.pricing_agent.httpx.post", lambda *a, **k: FakeGemini(bad))
    r = client.post("/agent/arms", json={"goal": "raise uptake", "current_price": 10, "num_arms": 2}).json()
    assert r["source"] == "fallback"
