"""Guardrail: stop an arm when its churn is gamma times the baseline's AND that difference is significant."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .stats import normal_sf

DEFAULT_GAMMA = 1.30
P_CHURN_THRESHOLD = 0.05


@dataclass(frozen=True)
class GuardrailResult:
    churn_base: float
    churn_arm: float
    ratio: float
    p_churn: float
    triggered: bool


def churn_p_value(c_base: int, n_base: int, c_arm: int, n_arm: int) -> float:
    """One-sided p-value that the arm's churn rate exceeds the baseline's."""
    pooled = (c_base + c_arm) / (n_base + n_arm)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_base + 1 / n_arm))
    if se == 0:
        return 1.0
    z = (c_arm / n_arm - c_base / n_base) / se
    return normal_sf(z)


def check_guardrail(c_base: int, n_base: int, c_arm: int, n_arm: int,
                    gamma: float = DEFAULT_GAMMA) -> GuardrailResult:
    """c_* = churned subscribers by time t, n_* = subscribers at start."""
    if n_base <= 0 or n_arm <= 0:
        raise ValueError("sample sizes must be positive")
    churn_base = c_base / n_base
    churn_arm = c_arm / n_arm
    if churn_base == 0:
        ratio = math.inf if churn_arm > 0 else 1.0
    else:
        ratio = churn_arm / churn_base
    p = churn_p_value(c_base, n_base, c_arm, n_arm)
    return GuardrailResult(churn_base, churn_arm, ratio, p,
                           ratio > gamma and p < P_CHURN_THRESHOLD)


def check_churn_rate(c_base: int, months_base: float, c_arm: int, months_arm: float,
                     gamma: float = DEFAULT_GAMMA, alpha: float = P_CHURN_THRESHOLD) -> GuardrailResult:
    """The same check on cancellations per subscriber-month.

    Traffic shifts during a test, so options end up with subscribers of different ages. Comparing
    rates per month of exposure keeps a younger group from looking safer than it is. The p-value
    is a one-sided Wald test on the log rate ratio, with 0.5 added to keep zero counts finite.
    """
    if months_base <= 0 or months_arm <= 0:
        raise ValueError("exposure must be positive")
    rate_base = c_base / months_base
    rate_arm = c_arm / months_arm
    if rate_base == 0:
        ratio = math.inf if rate_arm > 0 else 1.0
    else:
        ratio = rate_arm / rate_base
    log_ratio = math.log(((c_arm + 0.5) / months_arm) / ((c_base + 0.5) / months_base))
    p = normal_sf(log_ratio / math.sqrt(1 / (c_arm + 0.5) + 1 / (c_base + 0.5)))
    return GuardrailResult(rate_base, rate_arm, ratio, p, ratio > gamma and p < alpha)
