"""Decision logic: pick a winner, keep testing, or stop an arm."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .analysis import ArmData, ArmResult
from .guardrail import DEFAULT_GAMMA, GuardrailResult, check_churn_rate, check_guardrail
from .ltv import ltv_12m, survival_from_churn
from .stats import ZTestResult, two_proportion_z

# Thresholds for decide_by_value, chosen by simulating tests of identical prices with weekly checks.
# See tests/test_math.py::test_false_winners_stay_rare_despite_repeated_checks.
WIN_PROB = 0.99
MIN_SIGNUPS = 30
MIN_EXPOSURE_MONTHS = 15.0
CHURN_ALPHA = 0.01


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
    prob_beats_baseline: float | None = None
    value_per_visitor: float | None = None
    lift: float | None = None


@dataclass
class Decision:
    action: Action
    arm_id: str | None
    arms: list[ArmAnalysis] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    note: str = ""


def _ltv(arm: ArmStats, discount: float) -> tuple[float, float]:
    churn = arm.churned / arm.signups if arm.signups else 0.0
    surv = arm.survival or survival_from_churn(churn)
    return ltv_12m(arm.price, surv, discount), churn


def decide(baseline: ArmStats, arms: list[ArmStats], gamma: float = DEFAULT_GAMMA,
           discount: float = 0.01) -> Decision:
    """Stateless check from counts alone, using the Z-test (POST /engine/decide).

    1. Any arm tripping the guardrail is stopped (first one reported).
    2. Otherwise the challenger with significantly better conversion AND
       higher 12-month LTV than baseline wins (highest LTV if several).
    3. Otherwise keep testing.

    Running experiments use decide_by_value, which also handles uncertainty in value.
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


def _share(arm: ArmData) -> float:
    return arm.signups / arm.visitors if arm.visitors else 0.0


def decide_by_value(baseline: ArmData, arms: list[ArmData], results: dict[str, ArmResult],
                    gamma: float = DEFAULT_GAMMA, win_prob: float = WIN_PROB,
                    min_signups: int = MIN_SIGNUPS, min_exposure: float = MIN_EXPOSURE_MONTHS,
                    churn_alpha: float = CHURN_ALPHA) -> Decision:
    """Judge each challenger on expected 12-month revenue per visitor.

    1. Stop any arm whose churn rate is significantly worse than the baseline's.
    2. Wait until every live arm has enough sign-ups and enough time for churn to show.
    3. A challenger wins with P(worth more per visitor than baseline) >= win_prob, corrected for how
       many challengers are competing. If several qualify, the most valuable wins.
    4. If every challenger is that likely to be worse, the baseline wins: stop testing, keep the price.
    5. Otherwise keep testing.
    """
    base_result = results[baseline.arm_id]
    analyses = [ArmAnalysis(baseline.arm_id, _share(baseline),
                            baseline.churned / baseline.signups if baseline.signups else 0.0,
                            base_result.ltv, None, None,
                            value_per_visitor=base_result.value_per_visitor)]
    stopped: list[ArmAnalysis] = []
    enough = lambda a: a.signups >= min_signups and a.exposure_months >= min_exposure   # noqa: E731
    for arm in arms:
        r = results[arm.arm_id]
        z = (two_proportion_z(baseline.signups, baseline.visitors, arm.signups, arm.visitors)
             if baseline.visitors and arm.visitors else None)
        g = (check_churn_rate(baseline.churned, baseline.exposure_months, arm.churned,
                              arm.exposure_months, gamma, churn_alpha)
             if enough(baseline) and enough(arm) else None)
        a = ArmAnalysis(arm.arm_id, _share(arm), arm.churned / arm.signups if arm.signups else 0.0,
                        r.ltv, z, g, r.prob_beats_baseline, r.value_per_visitor, r.lift)
        analyses.append(a)
        if g and g.triggered:
            stopped.append(a)

    reasons: dict[str, str] = {}
    if stopped:
        for a in stopped:
            g = a.guardrail
            reasons[a.arm_id] = (f"Monthly churn {g.churn_arm:.1%} vs baseline {g.churn_base:.1%} "
                                 f"({g.ratio:.2f}x > {gamma:.2f}x, p={g.p_churn:.3f}).")
        return Decision(Action.STOP_ARM, stopped[0].arm_id, analyses, reasons)

    short = [a.arm_id for a in [baseline, *arms] if a.signups < min_signups]
    young = [a.arm_id for a in [baseline, *arms] if a.exposure_months < min_exposure]
    if short:
        return Decision(Action.KEEP_TESTING, None, analyses, reasons,
                        f"Every option needs at least {min_signups} sign-ups before it can be judged. "
                        f"Still short: {', '.join(short)}.")
    if young:
        return Decision(Action.KEEP_TESTING, None, analyses, reasons,
                        "Subscribers haven't been around long enough to see who cancels. "
                        f"Still too new: {', '.join(young)}.")

    needed = 1 - (1 - win_prob) / max(len(arms), 1)
    challengers = analyses[1:]
    winners = [a for a in challengers if a.prob_beats_baseline >= needed]
    if winners:
        best = max(winners, key=lambda a: a.value_per_visitor)
        reasons[best.arm_id] = (
            f"Worth ${best.value_per_visitor:.2f} per visitor vs ${base_result.value_per_visitor:.2f} "
            f"({best.lift:+.0%}), with a {best.prob_beats_baseline:.1%} chance it beats the baseline. "
            f"Conversion {best.conversion:.1%} vs {analyses[0].conversion:.1%}, "
            f"12-month value ${best.ltv:.2f} vs ${base_result.ltv:.2f}.")
        return Decision(Action.WINNER, best.arm_id, analyses, reasons)
    if challengers and all(a.prob_beats_baseline <= 1 - needed for a in challengers):
        reasons[baseline.arm_id] = (
            f"Worth ${base_result.value_per_visitor:.2f} per visitor, and every other option is at "
            f"least {needed:.1%} likely to be worth less.")
        return Decision(Action.WINNER, baseline.arm_id, analyses, reasons)
    closest = max(challengers, key=lambda a: a.prob_beats_baseline, default=None)
    note = (f"The closest option, {closest.arm_id}, has a {closest.prob_beats_baseline:.1%} chance of "
            f"beating the baseline. A win needs {needed:.1%}." if closest else "")
    return Decision(Action.KEEP_TESTING, None, analyses, reasons, note)
