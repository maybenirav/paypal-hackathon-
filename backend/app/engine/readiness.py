"""Is there enough traffic to run a pricing test at all?

Small merchants usually don't have it. Instead of pretending, PlanLab computes the sample a
test needs (two-proportion power calculation, 95% confidence, 80% power) and how long the
merchant's own traffic takes to reach it. If that is longer than `max_days`, the experiment is
reported as not feasible and the merchant is pointed at the Save Agent (which works per
customer and needs no sample) or at testing a bigger change with fewer arms.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

Z_ALPHA = 1.959964   # two-sided 95%
Z_BETA = 0.841621    # 80% power
DEFAULT_BASE_RATE = 0.05
DEFAULT_MDE = 0.25   # smallest relative lift worth detecting
MAX_DAYS = 60


def visitors_per_arm(base_rate: float, mde: float = DEFAULT_MDE) -> int:
    """Visitors each arm needs to detect a relative lift `mde` over `base_rate`."""
    if not 0 < base_rate < 1:
        raise ValueError("base_rate must be in (0, 1)")
    p1 = min(base_rate * (1 + mde), 0.999)
    var = base_rate * (1 - base_rate) + p1 * (1 - p1)
    return math.ceil((Z_ALPHA + Z_BETA) ** 2 * var / (p1 - base_rate) ** 2)


@dataclass
class Readiness:
    needed_per_arm: int
    have_per_arm: int
    progress: float               # min(have / needed, 1)
    arms: int
    base_rate: float
    mde: float
    monthly_visitors: int | None
    days_needed: int | None       # total days at the merchant's traffic, None if unknown
    feasible: bool | None         # None when traffic is unknown
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def readiness(arms: int, base_rate: float | None = None, have_per_arm: int = 0,
              monthly_visitors: int | None = None, mde: float = DEFAULT_MDE,
              max_days: int = MAX_DAYS) -> Readiness:
    rate = base_rate if base_rate and 0 < base_rate < 1 else DEFAULT_BASE_RATE
    need = visitors_per_arm(rate, mde)
    progress = min(have_per_arm / need, 1.0) if need else 1.0
    days = feasible = None
    if monthly_visitors:
        days = math.ceil(need * arms / (monthly_visitors / 30))
        feasible = days <= max_days
    if progress >= 1:
        msg = f"Enough data: {have_per_arm:,} visitors per arm (needs {need:,})."
    elif feasible is False:
        msg = (f"Not enough traffic for a {arms}-arm test: each arm needs about {need:,} visitors "
               f"to detect a {mde:.0%} lift, which takes ~{days} days at {monthly_visitors:,} "
               f"visitors/month. Run the Save Agent now, or test one bolder change.")
    elif days is not None:
        msg = f"Each arm needs about {need:,} visitors; your traffic gets there in ~{days} days."
    else:
        msg = (f"Each arm needs about {need:,} visitors to detect a {mde:.0%} lift "
               f"({have_per_arm:,} so far).")
    return Readiness(need, have_per_arm, round(progress, 3), arms, round(rate, 4), mde,
                     monthly_visitors, days, feasible, msg)
