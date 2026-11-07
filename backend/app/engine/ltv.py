"""12-month value of a subscriber.

`ltv_12m` is the simple formula from the team plan: V = sum_{t=1..12} M * S(t) / (1 + d)^t.

`plan_value` is what the experiment engine uses. It follows the plan's real payment schedule, so it
treats a $100 annual payment up front differently from $10 a month, and works on whole arrays of
posterior draws at once.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

DAYS_PER_MONTH = 30.44


def survival_from_churn(monthly_churn: float, months: int = 12) -> list[float]:
    """S(t) = (1 - c)^t for t = 1..months."""
    if not 0 <= monthly_churn <= 1:
        raise ValueError("monthly_churn must be in [0, 1]")
    return [(1 - monthly_churn) ** t for t in range(1, months + 1)]


def ltv_12m(monthly_revenue: float, survival: Sequence[float],
            discount_rate: float = 0.01) -> float:
    """Discounted expected revenue per subscriber over up to 12 months.

    `survival[t-1]` is S(t), the fraction still subscribed at month t.
    `discount_rate` is the monthly discount d.
    """
    if monthly_revenue < 0:
        raise ValueError("monthly_revenue must be >= 0")
    if len(survival) > 12:
        raise ValueError("at most 12 months of survival")
    return sum(monthly_revenue * s / (1 + discount_rate) ** t
               for t, s in enumerate(survival, start=1))


def plan_value(price: float, interval: str, monthly_churn, discount: float = 0.01,
               trial_days: int = 0, setup_fee: float = 0.0, refund_rate=0.0, horizon: int = 12):
    """Expected discounted revenue per subscriber in their first `horizon` months.

    Each payment falls due at time t (in months). It counts if the subscriber is still there,
    which happens with probability (1 - churn)^t, net of refunds and discounted by (1 + d)^t.
    The setup fee is charged at sign-up. `monthly_churn` and `refund_rate` may be arrays.
    """
    churn = np.asarray(monthly_churn, dtype=float)
    keep = (1 - churn) / (1 + discount)
    step = 12 if interval == "YEAR" else 1
    due = np.arange(trial_days / DAYS_PER_MONTH, horizon, step)
    paid = (keep[..., np.newaxis] ** due).sum(axis=-1)
    value = setup_fee + price * (1 - np.asarray(refund_rate, dtype=float)) * paid
    return float(value) if np.ndim(value) == 0 else value
