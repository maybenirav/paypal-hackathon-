"""Automatic guardrail checks: they stop risky prices alone, rarely stop healthy ones, and can be undone."""
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import simulate, watcher
from app.engine.analysis import ArmData, monthly_churn
from app.engine.guardrail import check_churn_rate
from app.engine.guardrails import GuardrailConfig, check_arm, projected_retention
from app.events import utcnow
from app.main import app
from app.memory import memory
from app.persistence import Persistence
from app.store import store

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def make(n=3):
    return client.post("/experiments", json={"goal": "raise annual uptake", "num_arms": n}).json()["id"]


def demo(eid, scenario="guardrail"):
    assert client.post(f"/experiments/{eid}/demo", json={"scenario": scenario}).status_code == 200
    return client.get(f"/experiments/{eid}").json()


def rows_at(eid, days=None):
    query = "" if days is None else f"?days={days}"
    m = client.get(f"/experiments/{eid}/metrics{query}").json()
    return {r["arm_id"]: r for r in m["rows"]}, m


def real_events(eid, arm, count, start="2026-10-01T10:00:00", refunds=0):
    events = []
    for i in range(count):
        sid = f"{arm}-{i}"
        events += [{"experiment_id": eid, "arm_id": arm, "subscriber_id": sid, "type": kind, "ts": start,
                    "source": "real", "amount": amount}
                   for kind, amount in (("VISIT", 0), ("SIGNUP", 0), ("PAYMENT", 10))]
        if i < refunds:
            events.append({"experiment_id": eid, "arm_id": arm, "subscriber_id": sid, "type": "REFUND",
                           "ts": start, "source": "real", "amount": 10})
    return events


def test_risky_price_is_stopped_without_anyone_running_the_analysis():
    d = demo(make())
    assert d["stopped_arms"] == ["monthly_trial"] and d["status"] == "stopped_arm"
    stop = d["stops"][0]
    assert stop["automatic"] and 1 <= stop["day"] < 60 and "Refund rate" in stop["reasons"]


def test_healthy_prices_are_left_alone():
    d = demo(make(), "winner")
    assert d["stopped_arms"] == [] and d["stops"] == [] and d["status"] == "running"


def test_the_timeline_shows_the_stop_only_from_the_day_it_happened():
    eid = make()
    day = demo(eid)["stops"][0]["day"]
    before, m_before = rows_at(eid, day - 1)
    after, m_after = rows_at(eid, day)
    assert before["monthly_trial"]["status"] == "running" and before["monthly_trial"]["allocation"] > 0
    assert m_before["stopped_arms"] == []
    assert after["monthly_trial"]["status"] == "stopped" and after["monthly_trial"]["allocation"] == 0
    assert m_after["stopped_arms"] == ["monthly_trial"]


def test_a_stopped_option_gets_no_new_customers_but_keeps_existing_ones():
    # Simulated customers arrive in the first 14 days, so only a stop before then can turn anyone away
    eid = make()
    client.put(f"/experiments/{eid}/guardrails", json={"refund_ceiling": 0.05})
    d = demo(eid)
    day = d["stops"][0]["day"]
    assert d["stopped_arms"] == ["monthly_trial"] and day < 14
    cutoff = simulate.START + timedelta(days=day)
    events = [e for e in store.get(eid).events if e.arm_id == "monthly_trial"]
    assert not [e for e in events if e.type in ("VISIT", "SIGNUP") and e.ts > cutoff]
    assert [e for e in events if e.type == "CANCELLED" and e.ts > cutoff]   # earlier sign-ups can still leave
    rows, _ = rows_at(eid)
    assert rows["monthly_trial"]["visitors"] < 1000 and rows["baseline"]["visitors"] == 1000


def test_stopped_customers_are_moved_on_and_memory_is_updated():
    eid = make()
    demo(eid)
    arms = {client.post(f"/experiments/{eid}/assign", json={"visitor_id": f"v{i}"}).json()["arm_id"] for i in range(25)}
    assert "monthly_trial" not in arms
    assert memory.records[0].stopped == ["monthly_trial"] and memory.records[0].simulated


def test_resume_undoes_a_stop_and_automatic_checks_leave_it_alone():
    eid = make()
    demo(eid)
    assert client.post(f"/experiments/{eid}/arms/monthly_trial/resume").json()["status"] == "running"
    rows, m = rows_at(eid)
    assert rows["monthly_trial"]["status"] == "running" and rows["monthly_trial"]["allocation"] > 0
    assert m["stopped_arms"] == [] and m["stops"] == []
    assert memory.records == []
    assert watcher.check_experiment(store.get(eid)) == []
    assert client.post(f"/experiments/{eid}/arms/monthly_trial/resume").status_code == 409


def test_a_manual_analysis_can_still_stop_a_resumed_option():
    eid = make()
    demo(eid)
    client.post(f"/experiments/{eid}/arms/monthly_trial/resume")
    d = client.post(f"/experiments/{eid}/decide").json()
    assert d["action"] == "stop_arm" and d["arm_id"] == "monthly_trial"
    assert store.get(eid).stops["monthly_trial"]["automatic"] is False


def test_an_analysis_stop_stays_at_its_place_on_the_timeline():
    eid = make()
    client.put(f"/experiments/{eid}/guardrails", json={"auto_stop": False})
    demo(eid)
    client.post(f"/experiments/{eid}/decide")
    assert client.get(f"/experiments/{eid}").json()["stops"][0]["day"] == 60
    before, m = rows_at(eid, 10)
    assert before["monthly_trial"]["status"] == "running" and m["stopped_arms"] == []


def test_automatic_stops_can_be_turned_off():
    eid = make()
    client.put(f"/experiments/{eid}/guardrails", json={"auto_stop": False})
    d = demo(eid)
    assert d["stops"] == [] and d["status"] == "running"
    assert client.get(f"/experiments/{eid}/metrics").json()["auto_checks"]["enabled"] is False
    assert client.post(f"/experiments/{eid}/decide").json()["action"] == "stop_arm"


def test_tighter_limits_apply_straight_away_and_never_to_the_baseline():
    eid = make()
    demo(eid, "winner")
    client.put(f"/experiments/{eid}/guardrails", json={"retention_floor": 0.9999})
    assert set(client.get(f"/experiments/{eid}").json()["stopped_arms"]) == {"annual_discount", "monthly_trial"}


def test_background_sweep_stops_a_risky_real_option_and_records_the_check():
    eid = make()
    client.post(f"/experiments/{eid}/events", json=real_events(eid, "baseline", 60)
                + real_events(eid, "annual_discount", 60, refunds=30))
    assert watcher.sweep() == {eid: ["annual_discount"]}
    d = client.get(f"/experiments/{eid}").json()
    assert d["stops"][0]["automatic"] and d["stops"][0]["day"] is None
    assert client.get(f"/experiments/{eid}/metrics").json()["auto_checks"]["last_checked"]
    assert watcher.sweep() == {}    # already stopped, so nothing new


def test_sweep_ignores_simulated_data_and_rolled_out_experiments():
    sim = make()
    demo(sim, "winner")
    assert watcher.sweep() == {}
    exp = store.get(sim)
    exp.winner, exp.rollout = "annual_discount", {"status": "approved"}
    exp.guardrails.retention_floor = 0.9999
    assert watcher.check_experiment(exp) == []


def test_one_bad_experiment_does_not_stop_the_others(monkeypatch):
    good, bad = make(), make()
    for eid in (good, bad):
        client.post(f"/experiments/{eid}/events", json=real_events(eid, "baseline", 40)
                    + real_events(eid, "annual_discount", 40, refunds=25))
    real_check = watcher.check_experiment

    def flaky(exp):
        if exp.id == bad:
            raise RuntimeError("boom")
        return real_check(exp)
    monkeypatch.setattr(watcher, "check_experiment", flaky)
    assert list(watcher.sweep()) == [good]


def test_the_background_loop_runs_and_can_be_stopped():
    ran = []
    stop = watcher.start(lambda: ran.append(utcnow()), seconds=0.01)
    assert stop is not None
    deadline = utcnow() + timedelta(seconds=3)
    while not ran and utcnow() < deadline:
        pass
    stop.set()
    assert ran
    assert watcher.start(lambda: None, seconds=0) is None


def test_stops_survive_a_restart(tmp_path):
    saved = Persistence(str(tmp_path / "test.db"))
    eid = make()
    demo(eid)
    before = client.get(f"/experiments/{eid}").json()
    saved.save()
    saved.close()
    store._exps.clear()
    reopened = Persistence(str(tmp_path / "test.db"))
    reopened.load()
    assert client.get(f"/experiments/{eid}").json() == before
    reopened.close()


# ---- are the always-on thresholds safe? -----------------------------------------------------------

def simulate_daily_checks(setup, signups_per_day, sims, seed, days=60):
    """Daily checks at the automatic thresholds. Returns (share with any stop, median stop day, share caught)."""
    rng = np.random.default_rng(seed)
    cfg = GuardrailConfig()
    n = len(setup)
    any_stop, stop_days = 0, []
    for _ in range(sims):
        signups, active, cancels = np.zeros(n, int), np.zeros(n, int), np.zeros(n, int)
        exposure, payments, refunds = np.zeros(n), np.zeros(n, int), np.zeros(n, int)
        fired: list = [None] * n
        for day in range(1, days + 1):
            joined = rng.poisson(signups_per_day, n)
            exposure += (active + joined / 2) / 30.44
            signups += joined
            active += joined
            left = rng.binomial(active, [1 - (1 - s[0]) ** (1 / 30.44) for s in setup])
            cancels += left
            active -= left
            paid = joined + rng.binomial(active, 1 / 30)
            payments += paid
            refunds += rng.binomial(paid, [s[1] for s in setup])
            for i in range(1, n):
                if fired[i] is not None or signups[i] < cfg.min_signups:
                    continue
                arm = ArmData("x", 10, signups=int(signups[i]), churned=int(cancels[i]), exposure_months=float(exposure[i]))
                row = {"arm_id": "x", "signups": int(signups[i]), "churn": cancels[i] / signups[i],
                       "churned": int(cancels[i]), "exposure_months": float(exposure[i]),
                       "retention_60": projected_retention(monthly_churn(arm)),
                       "refund_rate": refunds[i] / max(payments[i], 1), "refunds": int(refunds[i]),
                       "payments": int(payments[i]), "dispute_rate": 0.0, "disputes": 0}
                hit = bool(check_arm(row, cfg, watcher.AUTO_CONFIDENCE))
                if not hit and signups[0] >= 30 and min(exposure[0], exposure[i]) >= 15:
                    hit = check_churn_rate(int(cancels[0]), float(exposure[0]), int(cancels[i]), float(exposure[i]),
                                           cfg.churn_ratio, watcher.AUTO_CHURN_ALPHA).triggered
                if hit:
                    fired[i] = day
        any_stop += any(f is not None for f in fired[1:])
        if fired[-1] is not None:
            stop_days.append(fired[-1])
    return any_stop / sims, (float(np.median(stop_days)) if stop_days else None), len(stop_days) / sims


def test_healthy_prices_are_rarely_stopped_by_constant_checking():
    # At the manual analysis thresholds, this same simulation wrongly stopped about 24%
    share, _, _ = simulate_daily_checks([(0.07, 0.02)] * 3, signups_per_day=10, sims=80, seed=1)
    assert share <= 0.06


def test_risky_prices_are_caught_quickly():
    _, median_day, caught = simulate_daily_checks([(0.07, 0.02), (0.07, 0.02), (0.20, 0.15)],
                                                  signups_per_day=10, sims=40, seed=2)
    assert caught >= 0.95 and median_day <= 14
