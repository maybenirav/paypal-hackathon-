"""The value model and decision rules, with hand-calculated expectations where possible."""
from datetime import datetime, timedelta

import numpy as np
import pytest

from app.engine.analysis import ArmData, analyze, monthly_churn
from app.engine.bayes import shares_with_floor
from app.engine.decision import Action, decide_by_value
from app.engine.guardrail import check_churn_rate
from app.engine.guardrails import GuardrailConfig, check_arm, projected_retention
from app.engine.ltv import plan_value
from app.metrics import retention_curve


# ---- 12-month value ------------------------------------------------------------------------------

def test_monthly_plan_no_churn_is_twelve_payments():
    assert plan_value(10, "MONTH", 0.0, discount=0.0) == pytest.approx(120)


def test_monthly_plan_with_churn_hand_calc():
    # 10 * (1 - 0.9^12) / (1 - 0.9) = 10 * 7.1757 = 71.757
    assert plan_value(10, "MONTH", 0.1, discount=0.0) == pytest.approx(71.757, abs=1e-3)


def test_discount_hand_calc():
    # Two payments at t=0 and t=1 with 10% discount and no churn: 10 + 10/1.1 = 19.0909
    assert plan_value(10, "MONTH", 0.0, discount=0.1, horizon=2) == pytest.approx(19.0909, abs=1e-3)


def test_annual_plan_is_paid_up_front_whatever_the_churn():
    assert plan_value(100, "YEAR", 0.0, discount=0.0) == pytest.approx(100)
    assert plan_value(100, "YEAR", 0.5, discount=0.0) == pytest.approx(100)


def test_trial_delays_and_can_lose_the_first_payment():
    # A 7-day trial still leaves 12 payments in the year, each one due a little later
    assert plan_value(10, "MONTH", 0.0, discount=0.0, trial_days=7) == pytest.approx(120)
    assert plan_value(100, "YEAR", 0.1, discount=0.0, trial_days=7) == pytest.approx(100 * 0.9 ** (7 / 30.44))
    # A 31-day trial pushes the 12th payment past the horizon
    assert plan_value(10, "MONTH", 0.0, discount=0.0, trial_days=31) == pytest.approx(110)


def test_setup_fee_and_refunds():
    assert plan_value(10, "MONTH", 0.0, discount=0.0, setup_fee=5, refund_rate=0.1) == pytest.approx(5 + 108)


def test_plan_value_works_on_arrays_of_draws():
    values = plan_value(10, "MONTH", np.array([0.0, 0.1]), discount=0.0)
    assert values == pytest.approx([120, 71.757], abs=1e-3)


# ---- churn per month of exposure -----------------------------------------------------------------

def test_churn_accounts_for_exposure():
    young = ArmData("y", 10, signups=100, churned=5, exposure_months=25)
    old = ArmData("o", 10, signups=100, churned=5, exposure_months=250)
    assert monthly_churn(young) > 4 * monthly_churn(old)


def test_churn_estimate_with_no_data_uses_the_prior():
    assert monthly_churn(ArmData("n", 10)) == pytest.approx(0.1)


def test_retention_projection_hand_calc():
    # 10% a month for 60 days: 0.9^(60/30.44) = 0.8125
    assert projected_retention(0.1) == pytest.approx(0.8125, abs=1e-3)


# ---- retention curve -----------------------------------------------------------------------------

START = datetime(2026, 1, 1)


def test_recent_signups_are_not_counted_as_retained():
    # 4 people: one followed for 3 weeks and left in week 2; three followed for 3 weeks and stayed
    signups = {k: START for k in "abcd"}
    cancels = {"a": START + timedelta(days=10)}
    curve = retention_curve(signups, cancels, START + timedelta(days=21), weeks=9)
    assert curve[:4] == [1.0, 1.0, 0.75, 0.75]
    assert curve[4:] == [None] * 5


def test_retention_handles_people_who_joined_later():
    # Everyone stays, but half joined only a week before the end: still full retention, not less
    signups = {"a": START, "b": START, "c": START + timedelta(days=14), "d": START + timedelta(days=14)}
    curve = retention_curve(signups, {}, START + timedelta(days=21), weeks=4)
    assert curve == [1.0, 1.0, 1.0, 1.0]


def test_retention_empty_cohort():
    assert retention_curve({}, {}, START) == []


# ---- traffic split -------------------------------------------------------------------------------

def test_floor_keeps_every_option_alive():
    shares = shares_with_floor({"a": 1.0, "b": 0.0})
    assert shares["b"] > 0.04 and sum(shares.values()) == pytest.approx(1)


def test_baseline_keeps_its_protected_share():
    shares = shares_with_floor({"base": 0.0, "a": 1.0, "b": 0.0}, protected="base", protected_share=0.3)
    assert shares["base"] == pytest.approx(0.3)
    assert sum(shares.values()) == pytest.approx(1)
    assert shares["a"] > shares["b"] > 0


def test_protection_never_takes_away_from_a_leading_baseline():
    shares = shares_with_floor({"base": 0.9, "a": 0.1}, protected="base", protected_share=0.3)
    assert shares["base"] == pytest.approx(0.9)


def test_stopped_options_get_no_traffic():
    arms = [ArmData("base", 10, visitors=100, signups=10), ArmData("x", 10, visitors=100, signups=10)]
    results = analyze(arms, "base", live={"base"})
    assert results["x"].allocation == 0 and results["base"].allocation == 1


def test_analysis_repeats_exactly():
    arms = [ArmData("base", 10, visitors=500, signups=50, churned=5, exposure_months=70),
            ArmData("x", 13, visitors=500, signups=45, churned=4, exposure_months=60)]
    assert analyze(arms, "base") == analyze(arms, "base")


# ---- decisions -----------------------------------------------------------------------------------

def arm(arm_id, price, visitors, signups, interval="MONTH", churned=None, exposure=None, **kw):
    churned = round(signups * 0.08) if churned is None else churned
    return ArmData(arm_id, price, interval, visitors=visitors, signups=signups, churned=churned,
                   exposure_months=signups * 1.0 if exposure is None else exposure, payments=signups, **kw)


def run(base, *others, **kw):
    return decide_by_value(base, list(others), analyze([base, *others], base.arm_id), **kw)


def test_a_higher_price_can_win_on_slightly_fewer_signups():
    # 9.5% at $13 earns more than 10% at $10, which a sign-ups-first rule could never pick
    d = run(arm("base", 10, 8000, 800), arm("premium", 13, 8000, 760))
    assert d.action == Action.WINNER and d.arm_id == "premium"
    assert d.arms[1].conversion < d.arms[0].conversion
    assert "per visitor" in d.reasons["premium"]


def test_more_signups_at_a_lower_value_does_not_win():
    d = run(arm("base", 10, 8000, 800), arm("cheap", 5, 8000, 1000))
    assert not (d.action == Action.WINNER and d.arm_id == "cheap")


def test_the_baseline_wins_when_it_is_clearly_best():
    d = run(arm("base", 10, 8000, 800), arm("worse", 7, 8000, 700))
    assert d.action == Action.WINNER and d.arm_id == "base"
    assert "Worth" in d.reasons["base"]


def test_too_few_signups_means_no_decision():
    d = run(arm("base", 10, 100, 5), arm("x", 100, 100, 29))
    assert d.action == Action.KEEP_TESTING and "sign-ups" in d.note


def test_too_little_time_means_no_decision():
    # Plenty of sign-ups, but they all joined moments ago, so nobody has had time to cancel
    d = run(arm("base", 10, 8000, 800, exposure=2), arm("x", 30, 8000, 800, exposure=2))
    assert d.action == Action.KEEP_TESTING and "long enough" in d.note


def test_close_results_keep_testing():
    d = run(arm("base", 10, 3000, 300), arm("x", 10, 3000, 305))
    assert d.action == Action.KEEP_TESTING and "A win needs" in d.note


def test_more_challengers_need_stronger_evidence():
    base = arm("base", 10, 2000, 200)
    strong = arm("x", 10, 2000, 244)
    results = analyze([base, strong], "base")
    p = results["x"].prob_beats_baseline
    assert 0.6 < p < 0.99
    # Set the bar just under this option's chance: it clears it alone, but not among 4 challengers
    one = decide_by_value(base, [strong], results, win_prob=p - 0.001)
    many = decide_by_value(base, [strong, *[arm(f"y{i}", 10, 2000, 200) for i in range(3)]],
                           analyze([base, strong, *[arm(f"y{i}", 10, 2000, 200) for i in range(3)]], "base"),
                           win_prob=p - 0.001)
    assert one.action == Action.WINNER
    assert many.action == Action.KEEP_TESTING


def test_significantly_worse_churn_stops_an_option():
    d = run(arm("base", 10, 4000, 400, churned=30, exposure=500),
            arm("risky", 10, 4000, 400, churned=90, exposure=500))
    assert d.action == Action.STOP_ARM and d.arm_id == "risky"
    assert "Monthly churn" in d.reasons["risky"]


def test_churn_is_compared_per_month_of_exposure():
    # The same 30 cancellations, but from a group watched for half as long, is twice the churn rate
    g = check_churn_rate(30, 500, 30, 250)
    assert g.ratio == pytest.approx(2.0)
    same = check_churn_rate(30, 500, 30, 500)
    assert same.ratio == pytest.approx(1.0) and not same.triggered


def test_churn_rate_p_value_hand_calc():
    # log((20.5/100)/(10.5/100)) = 0.6690, se = sqrt(1/20.5 + 1/10.5) = 0.3795, z = 1.763, p = 0.039
    g = check_churn_rate(10, 100, 20, 100)
    assert g.p_churn == pytest.approx(0.039, abs=1e-3) and g.triggered


# ---- guardrail confidence ------------------------------------------------------------------------

def guard_row(**kw):
    base = {"arm_id": "x", "signups": 100, "churn": 0.1, "churned": 10, "exposure_months": 130,
            "retention_60": 0.85, "refund_rate": 0.01, "refunds": 1, "payments": 100,
            "dispute_rate": 0.0, "disputes": 0}
    return {**base, **kw}


def test_a_wobble_just_over_a_limit_is_not_a_breach():
    # 9 refunds in 100 payments is over the 8% ceiling, but well within what chance could produce
    assert check_arm(guard_row(refund_rate=0.09, refunds=9), GuardrailConfig()) == []


def test_a_clear_breach_is_caught():
    breaches = check_arm(guard_row(refund_rate=0.2, refunds=20), GuardrailConfig())
    assert [b.guardrail for b in breaches] == ["refund_ceiling"]


def test_retention_breach_needs_confidence():
    churned, months = 40, 150     # about 27% a month
    breaches = check_arm(guard_row(churned=churned, exposure_months=months, retention_60=0.54), GuardrailConfig())
    assert [b.guardrail for b in breaches] == ["retention_floor"]
    # About 11% a month would be under the floor at face value, but on 3 cancellations it could be luck
    assert check_arm(guard_row(churned=3, exposure_months=27, retention_60=0.79, signups=30), GuardrailConfig()) == []


# ---- calibration: does it hold up when checked every week? ---------------------------------------

WEEK_MONTHS = 7 / 30.44


def simulate_tests(setup, weekly_visitors, sims, seed, weeks=8, samples=1000):
    """Weekly checks with the real engine and adaptive traffic. Returns outcome shares."""
    rng = np.random.default_rng(seed)
    n = len(setup)
    ids = [f"a{i}" for i in range(n)]
    tally = {"challenger": 0, "baseline": 0, "stop": 0, "none": 0}
    for _ in range(sims):
        visitors, signups, churned = np.zeros(n, int), np.zeros(n, int), np.zeros(n, int)
        active, exposure, shares = np.zeros(n, int), np.zeros(n), np.full(n, 1 / n)
        outcome = "none"
        for _ in range(weeks):
            new_visitors = rng.multinomial(weekly_visitors * n, shares)
            joined = rng.binomial(new_visitors, [s[1] for s in setup])
            visitors += new_visitors
            signups += joined
            exposure += active * WEEK_MONTHS + joined * WEEK_MONTHS / 2
            active += joined
            left = rng.binomial(active, [1 - (1 - s[2]) ** WEEK_MONTHS for s in setup])
            churned += left
            active -= left
            arms = [ArmData(ids[i], setup[i][0], visitors=int(visitors[i]), signups=int(signups[i]),
                            churned=int(churned[i]), exposure_months=float(exposure[i]),
                            payments=int(signups[i])) for i in range(n)]
            results = analyze(arms, ids[0], samples=samples, seed=int(rng.integers(1 << 30)))
            shares = np.array([results[i].allocation for i in ids])
            d = decide_by_value(arms[0], arms[1:], results)
            if d.action == Action.STOP_ARM:
                outcome = "stop"
            elif d.action == Action.WINNER:
                outcome = "baseline" if d.arm_id == ids[0] else "challenger"
            if outcome != "none":
                break
        tally[outcome] += 1
    return {k: v / sims for k, v in tally.items()}


def test_false_winners_stay_rare_despite_repeated_checks():
    # Three identical prices, checked weekly for 8 weeks: any challenger "winning" is a false alarm.
    # The old rule (95% on conversion, no minimum sample) gave about 15% here.
    outcome = simulate_tests([(10, 0.10, 0.08)] * 3, weekly_visitors=400, sims=150, seed=1)
    assert outcome["challenger"] <= 0.08
    assert outcome["stop"] <= 0.12


def test_real_improvements_are_still_found():
    outcome = simulate_tests([(10, 0.10, 0.08), (10, 0.13, 0.08), (10, 0.10, 0.08)],
                             weekly_visitors=1000, sims=60, seed=2)
    assert outcome["challenger"] >= 0.6
