"""Guardrail: stop an arm when C_arm(t)/C_base(t) > gamma AND p_churn < 0.05."""
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
