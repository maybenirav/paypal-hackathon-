"""The value model: what each price option is worth per visitor, and how sure we are.

Value per visitor = conversion x 12-month value per subscriber. Both parts are uncertain, so we draw
from their posteriors together and read everything else (ranges, chance of being best, chance of
beating the baseline, traffic share) off the same draws.

- Conversion:  Beta(1 + sign-ups, 1 + non-converting visitors)
- Churn:       Gamma on cancellations per subscriber-month of exposure, so subscribers who joined
               recently count for less than those we have watched for months
- Refund rate: Beta on refunds per payment
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bayes import SAMPLES, best_shares, interval, shares_with_floor
from .ltv import plan_value

# Weak priors: about 10% monthly churn and 5% refunds, worth a few subscribers of evidence. They keep
# early estimates sensible, so a new option isn't valued as if nobody will ever cancel.
CHURN_PRIOR_EVENTS, CHURN_PRIOR_MONTHS = 0.5, 5.0
REFUND_PRIOR_A, REFUND_PRIOR_B = 0.5, 9.5
DISCOUNT = 0.01
BASELINE_SHARE = 0.30   # the current price keeps at least this share of traffic during a test


@dataclass(frozen=True)
class ArmData:
    arm_id: str
    price: float
    interval: str = "MONTH"
    trial_days: int = 0
    setup_fee: float = 0.0
    visitors: int = 0
    signups: int = 0
    churned: int = 0
    exposure_months: float = 0.0
    payments: int = 0
    refunds: int = 0


@dataclass(frozen=True)
class ArmResult:
    arm_id: str
    conv_ci: tuple[float, float]
    monthly_churn: float
    ltv: float
    value_per_visitor: float
    value_ci: tuple[float, float]
    prob_best: float
    allocation: float
    prob_beats_baseline: float | None
    lift: float | None


def monthly_churn(arm: ArmData) -> float:
    return (arm.churned + CHURN_PRIOR_EVENTS) / (arm.exposure_months + CHURN_PRIOR_MONTHS)


def churn_draws(arm: ArmData, rng: np.random.Generator, n: int = SAMPLES) -> np.ndarray:
    scale = 1 / (arm.exposure_months + CHURN_PRIOR_MONTHS)
    return np.minimum(rng.gamma(arm.churned + CHURN_PRIOR_EVENTS, scale, n), 1.0)


def refund_rate(arm: ArmData) -> float:
    return (arm.refunds + REFUND_PRIOR_A) / (arm.payments + REFUND_PRIOR_A + REFUND_PRIOR_B)


def expected_ltv(arm: ArmData, discount: float = DISCOUNT) -> float:
    return plan_value(arm.price, arm.interval, monthly_churn(arm), discount, arm.trial_days,
                      arm.setup_fee, refund_rate(arm))


def analyze(arms: list[ArmData], baseline_id: str, live: set[str] | None = None, seed: int = 0,
            samples: int = SAMPLES, discount: float = DISCOUNT,
            baseline_share: float = BASELINE_SHARE) -> dict[str, ArmResult]:
    """Results per arm. Only `live` arms compete for traffic; stopped ones get none."""
    rng = np.random.default_rng(seed)
    live = {a.arm_id for a in arms} if live is None else live
    conversions: dict[str, np.ndarray] = {}
    values: dict[str, np.ndarray] = {}
    for arm in arms:
        converted = min(arm.signups, arm.visitors)
        conv = rng.beta(1 + converted, 1 + arm.visitors - converted, samples)
        refunds = rng.beta(REFUND_PRIOR_A + arm.refunds,
                           REFUND_PRIOR_B + max(arm.payments - arm.refunds, 0), samples)
        per_subscriber = plan_value(arm.price, arm.interval, churn_draws(arm, rng, samples), discount,
                                    arm.trial_days, arm.setup_fee, refunds)
        conversions[arm.arm_id] = conv
        values[arm.arm_id] = conv * per_subscriber

    best = best_shares({k: v for k, v in values.items() if k in live})
    shares = shares_with_floor(best, protected=baseline_id, protected_share=baseline_share)
    base = values[baseline_id]
    results: dict[str, ArmResult] = {}
    for arm in arms:
        k = arm.arm_id
        is_base = k == baseline_id
        results[k] = ArmResult(
            arm_id=k,
            conv_ci=interval(conversions[k]),
            monthly_churn=monthly_churn(arm),
            ltv=expected_ltv(arm, discount),
            value_per_visitor=float(values[k].mean()),
            value_ci=interval(values[k]),
            prob_best=best.get(k, 0.0),
            allocation=shares.get(k, 0.0),
            prob_beats_baseline=None if is_base else float((values[k] > base).mean()),
            lift=None if is_base else float(values[k].mean() / base.mean() - 1),
        )
    return results
