import math

import pytest

from app.engine.decision import Action, ArmStats, decide
from app.engine.guardrail import check_guardrail
from app.engine.ltv import ltv_12m, survival_from_churn
from app.engine.stats import two_proportion_z


def test_z_significant():
    # pooled=0.115, se=sqrt(0.115*0.885*0.002)=0.014267, z=0.03/se=2.1028
    r = two_proportion_z(100, 1000, 130, 1000)
    assert r.pooled == pytest.approx(0.115)
    assert r.z == pytest.approx(2.1028, abs=1e-3)
    assert r.significant


def test_z_not_significant():
    # pooled=0.105, se=0.013710, z=0.01/se=0.7294
    r = two_proportion_z(100, 1000, 110, 1000)
    assert r.z == pytest.approx(0.7294, abs=1e-3)
    assert not r.significant


def test_z_negative_direction():
    assert two_proportion_z(130, 1000, 100, 1000).z == pytest.approx(-2.1028, abs=1e-3)


def test_z_zero_variance():
    assert two_proportion_z(0, 50, 0, 50).z == 0


def test_z_invalid():
    with pytest.raises(ValueError):
        two_proportion_z(5, 0, 1, 10)
    with pytest.raises(ValueError):
        two_proportion_z(11, 10, 1, 10)


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
