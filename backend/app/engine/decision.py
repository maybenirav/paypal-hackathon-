"""Decision logic: pick a winner, keep testing, or stop an arm.

One method, Bayesian, on a metric you can observe now:
  new MRR per visitor = paid conversion x monthly-equivalent price.
A challenger wins when P(its MRR per visitor beats the baseline's) >= win_prob, it has enough
paying subscribers, and no guardrail fires. 12-month LTV is reported as a projection only;
it never decides anything, because it takes a year to observe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .bayes import credible_interval, posterior, prob_beats
from .guardrails import DEFAULT_GAMMA, GuardrailResult, check_guardrail
from .ltv import ltv_12m, survival_from_churn

WIN_PROB = 0.95
MIN_PAID = 30


class Action(str, Enum):
    STOP_ARM = "stop_arm"
    WINNER = "winner"
    KEEP_TESTING = "keep_testing"


@dataclass
class ArmStats:
    arm_id: str
    price: float            # monthly-equivalent revenue per active subscriber
    visitors: int
    signups: int
    churned: int            # paying subscribers who cancelled so far
    paid: int | None = None  # signups that made at least one payment (None = same as signups)
    survival: list[float] | None = None  # optional observed S(1..12) for the LTV projection

    @property
    def paid_n(self) -> int:
        return self.signups if self.paid is None else self.paid


@dataclass
class ArmAnalysis:
    arm_id: str
    conversion: float               # sign-ups / visitors
    paid_conversion: float          # paying subscribers / visitors
    mrr_per_visitor: float          # paid_conversion x monthly price (the decision metric)
    churn: float
    projected_ltv: float            # display only
    guardrail: GuardrailResult | None
    prob_beats_baseline: float | None = None
    conv_ci: tuple[float, float] | None = None


@dataclass
class Decision:
    action: Action
    arm_id: str | None
    arms: list[ArmAnalysis] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)


def _analyse(a: ArmStats, discount: float) -> ArmAnalysis:
    churn = a.churned / a.paid_n if a.paid_n else 0.0
    surv = a.survival or survival_from_churn(churn)
    paid_conv = a.paid_n / a.visitors if a.visitors else 0.0
    ci = credible_interval(posterior(a.paid_n, a.visitors)) if a.visitors else None
    return ArmAnalysis(a.arm_id, a.signups / a.visitors if a.visitors else 0.0, paid_conv,
                       paid_conv * a.price, churn, ltv_12m(a.price, surv, discount), None, None, ci)


def decide(baseline: ArmStats, arms: list[ArmStats], gamma: float = DEFAULT_GAMMA,
           discount: float = 0.01, win_prob: float = WIN_PROB,
           min_paid: int = MIN_PAID) -> Decision:
    """1. Any challenger tripping the churn guardrail is stopped (first one reported).
    2. Otherwise the challenger most likely to earn more per visitor wins, if that probability
       is at least `win_prob` and it has `min_paid` paying subscribers.
    3. Otherwise keep testing.
    """
    base = _analyse(baseline, discount)
    analyses = [base]
    stopped: list[ArmAnalysis] = []
    candidates: list[ArmAnalysis] = []
    base_post = posterior(baseline.paid_n, baseline.visitors) if baseline.visitors else None

    for arm in arms:
        a = _analyse(arm, discount)
        if baseline.paid_n and arm.paid_n:
            a.guardrail = check_guardrail(baseline.churned, baseline.paid_n,
                                          arm.churned, arm.paid_n, gamma)
        if base_post and arm.visitors:
            a.prob_beats_baseline = prob_beats(posterior(arm.paid_n, arm.visitors), base_post,
                                               arm_scale=arm.price, base_scale=baseline.price)
        analyses.append(a)
        if a.guardrail and a.guardrail.triggered:
            stopped.append(a)
        elif (a.prob_beats_baseline is not None and a.prob_beats_baseline >= win_prob
              and arm.paid_n >= min_paid):
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
        best = max(candidates, key=lambda a: a.mrr_per_visitor)
        reasons[best.arm_id] = (
            f"{best.prob_beats_baseline:.0%} likely to earn more per visitor than the baseline: "
            f"{best.mrr_per_visitor:.2f} vs {base.mrr_per_visitor:.2f} new MRR per visitor "
            f"(paid conversion {best.paid_conversion:.1%}, 95% interval "
            f"{best.conv_ci[0]:.1%} to {best.conv_ci[1]:.1%}).")
        return Decision(Action.WINNER, best.arm_id, analyses, reasons)
    for a in analyses[1:]:
        if a.prob_beats_baseline is not None:
            reasons[a.arm_id] = (f"{a.prob_beats_baseline:.0%} likely to beat the baseline; "
                                 f"needs {win_prob:.0%}.")
    return Decision(Action.KEEP_TESTING, None, analyses, reasons)
