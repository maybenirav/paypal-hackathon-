"""12-month LTV: V = sum_{t=1..12} M * S(t) / (1 + d)^t."""
from __future__ import annotations

from typing import Sequence


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
