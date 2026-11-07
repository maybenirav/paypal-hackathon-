import math

import pytest

from app.engine.decision import Action, ArmStats, decide
from app.engine.guardrails import check_guardrail
from app.engine.ltv import ltv_12m, survival_from_churn
from app.engine.readiness import readiness, visitors_per_arm


def test_sample_size_hand_calc():
    # p0=.10, p1=.125: 2.8016^2 * (.09 + .109375) / .025^2 = 2503.8 -> 2504
    assert visitors_per_arm(0.10, 0.25) == 2504


def test_readiness_flags_small_merchant():
    r = readiness(3, base_rate=0.10, monthly_visitors=300)
    assert r.feasible is False and r.days_needed > 60
    assert "Save Agent" in r.message


def test_readiness_progress():
    r = readiness(2, base_rate=0.10, have_per_arm=2503)
    assert r.progress == 1 and r.feasible is None


def test_ltv_hand_calc():
    # 10/1.1 + 10/1.21 = 9.0909 + 8.2645
    assert ltv_12m(10, [1, 1], 0.1) == pytest.approx(17.3554, abs=1e-3)


def test_ltv_with_churn():
    # S=[0.9, 0.81], d=0: 10*0.9 + 10*0.81 = 17.1
    assert ltv_12m(10, survival_from_churn(0.1, 2), 0.0) == pytest.approx(17.1)


def test_ltv_full_year_no_churn_no_discount():
    assert ltv_12m(5, [1] * 12, 0.0) == pytest.approx(60)


def test_survival_bounds():
    with pytest.raises(ValueError):
        survival_from_churn(1.5)


def test_guardrail_triggers():
    # churn 0.10 vs 0.20: ratio 2.0; pooled .15, se=.050498, z=1.980, p~0.0238
    g = check_guardrail(10, 100, 20, 100)
    assert g.ratio == pytest.approx(2.0)
    assert g.p_churn == pytest.approx(0.0238, abs=1e-3)
    assert g.triggered


def test_guardrail_ratio_high_but_not_significant():
    # 1/100 vs 2/100: ratio 2.0 but p ~ 0.28
    g = check_guardrail(1, 100, 2, 100)
    assert g.ratio == pytest.approx(2.0)
    assert g.p_churn > 0.05
    assert not g.triggered


def test_guardrail_custom_gamma():
    assert not check_guardrail(10, 100, 20, 100, gamma=2.5).triggered


def test_guardrail_zero_baseline_churn():
    g = check_guardrail(0, 200, 30, 200)
    assert math.isinf(g.ratio) and g.triggered


def test_decide_stops_risky_arm():
    base = ArmStats("base", 10, 1000, 100, 10)
    risky = ArmStats("risky", 8, 1000, 140, 40)
    d = decide(base, [risky])
    assert d.action == Action.STOP_ARM and d.arm_id == "risky"
    assert "Churn" in d.reasons["risky"]


def test_decide_picks_winner_by_ltv():
    base = ArmStats("base", 10, 1000, 100, 10)
    annual = ArmStats("annual", 12, 1000, 135, 10)
    d = decide(base, [annual])
    assert d.action == Action.WINNER and d.arm_id == "annual"


def test_decide_keeps_testing():
    base = ArmStats("base", 10, 1000, 100, 10)
    close = ArmStats("close", 11, 1000, 105, 11)
    assert decide(base, [close]).action == Action.KEEP_TESTING


def test_cheap_arm_does_not_win_on_conversion_alone():
    # Half price converts 30% better, but earns less per visitor: 0.13 * 5 < 0.10 * 10.
    base = ArmStats("base", 10, 2000, 200, 10)
    cheap = ArmStats("cheap", 5, 2000, 260, 13)
    d = decide(base, [cheap])
    assert d.action == Action.KEEP_TESTING
    assert d.arms[1].prob_beats_baseline < 0.05


def test_unpaid_trials_do_not_count():
    # Same sign-ups as a winning arm, but most trials never paid.
    base = ArmStats("base", 10, 1000, 100, 10)
    trial = ArmStats("trial", 12, 1000, 135, 10, paid=60)
    assert decide(base, [trial]).action == Action.KEEP_TESTING


def test_winner_needs_minimum_paying_subscribers():
    base = ArmStats("base", 10, 100, 5, 0)
    arm = ArmStats("arm", 10, 100, 20, 0)
    assert decide(base, [arm]).action == Action.KEEP_TESTING      # 20 paid < 30
    assert decide(base, [arm], min_paid=10).action == Action.WINNER
