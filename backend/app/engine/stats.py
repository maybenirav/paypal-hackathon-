"""Conversion significance: two-proportion Z-test with pooled rate."""
from __future__ import annotations

import math
from dataclasses import dataclass

Z_CRITICAL = 1.96


@dataclass(frozen=True)
class ZTestResult:
    p_base: float
    p_arm: float
    pooled: float
    z: float
    significant: bool


def two_proportion_z(x_base: int, n_base: int, x_arm: int, n_arm: int,
                     z_critical: float = Z_CRITICAL) -> ZTestResult:
    """Z = (p_arm - p_base) / sqrt(p(1-p)(1/n_base + 1/n_arm)), p pooled.

    Significant when |Z| >= z_critical (1.96 ~ 95% two-sided).
    """
    if n_base <= 0 or n_arm <= 0:
        raise ValueError("sample sizes must be positive")
    if not (0 <= x_base <= n_base and 0 <= x_arm <= n_arm):
        raise ValueError("conversions must be between 0 and sample size")
    p_base = x_base / n_base
    p_arm = x_arm / n_arm
    pooled = (x_base + x_arm) / (n_base + n_arm)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_base + 1 / n_arm))
    z = 0.0 if se == 0 else (p_arm - p_base) / se
    return ZTestResult(p_base, p_arm, pooled, z, abs(z) >= z_critical)


def normal_sf(z: float) -> float:
    """P(Z > z) for the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2))
