"""Decision logic: pick a winner, keep testing, or stop an arm."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .guardrail import DEFAULT_GAMMA, GuardrailResult, check_guardrail
from .ltv import ltv_12m, survival_from_churn
from .stats import ZTestResult, two_proportion_z


class Action(str, Enum):
    STOP_ARM = "stop_arm"
    WINNER = "winner"
    KEEP_TESTING = "keep_testing"


@dataclass
class ArmStats:
    arm_id: str
    price: float            # monthly-equivalent revenue per active subscriber (M_arm)
    visitors: int
    signups: int
    churned: int            # churned among signups so far
    survival: list[float] | None = None  # optional observed S(1..12)


@dataclass
class ArmAnalysis:
    arm_id: str
    conversion: float
    churn: float
    ltv: float
    ztest: ZTestResult | None
    guardrail: GuardrailResult | None


@dataclass
class Decision:
    action: Action
    arm_id: str | None
    arms: list[ArmAnalysis] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)


def _ltv(arm: ArmStats, discount: float) -> tuple[float, float]:
    churn = arm.churned / arm.signups if arm.signups else 0.0
    surv = arm.survival or survival_from_churn(churn)
    return ltv_12m(arm.price, surv, discount), churn


def decide(baseline: ArmStats, arms: list[ArmStats], gamma: float = DEFAULT_GAMMA,
           discount: float = 0.01) -> Decision:
    """Evaluate every challenger against the baseline.

    1. Any arm tripping the guardrail is stopped (first one reported).
    2. Otherwise the challenger with significantly better conversion AND
       higher 12-month LTV than baseline wins (highest LTV if several).
    3. Otherwise keep testing.
    """
    base_ltv, base_churn = _ltv(baseline, discount)
    analyses: list[ArmAnalysis] = [ArmAnalysis(
        baseline.arm_id,
        baseline.signups / baseline.visitors if baseline.visitors else 0.0,
        base_churn, base_ltv, None, None)]
    stopped: list[ArmAnalysis] = []
    candidates: list[ArmAnalysis] = []

    for arm in arms:
        ltv, churn = _ltv(arm, discount)
        z = (two_proportion_z(baseline.signups, baseline.visitors,
                              arm.signups, arm.visitors)
             if baseline.visitors and arm.visitors else None)
        g = (check_guardrail(baseline.churned, baseline.signups,
                             arm.churned, arm.signups, gamma)
             if baseline.signups and arm.signups else None)
        a = ArmAnalysis(arm.arm_id, arm.signups / arm.visitors if arm.visitors else 0.0,
                        churn, ltv, z, g)
        analyses.append(a)
        if g and g.triggered:
            stopped.append(a)
        elif z and z.significant and z.z > 0 and ltv > base_ltv:
            candidates.append(a)

    reasons: dict[str, str] = {}
    for a in stopped:
        g = a.guardrail
        reasons[a.arm_id] = (
            f"Churn {g.churn_arm:.1%} vs baseline {g.churn_base:.1%} "
            f"({g.ratio:.2f}x > {gamma:.2f}x, p={g.p_churn:.3f}).")
    if stopped:
        return Decision(Action.STOP_ARM, stopped[0].arm_id, analyses, reasons)
    if candidates:
        best = max(candidates, key=lambda a: a.ltv)
        reasons[best.arm_id] = (
            f"Conversion {best.conversion:.1%} vs {analyses[0].conversion:.1%} "
            f"(Z={best.ztest.z:.2f}), 12-month LTV {best.ltv:.2f} vs {base_ltv:.2f}.")
        return Decision(Action.WINNER, best.arm_id, analyses, reasons)
    return Decision(Action.KEEP_TESTING, None, analyses, reasons)
