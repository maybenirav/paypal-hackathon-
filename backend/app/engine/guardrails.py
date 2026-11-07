"""All guardrails in one place.

1. Relative churn guardrail: stop a challenger when C_arm / C_base > gamma AND the one-sided
   p-value that its churn is higher is < 0.05 (so noise on small samples never stops an arm).
2. Absolute guardrails the agent cannot cross: retention floor, refund and dispute ceilings.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_GAMMA = 1.30
P_CHURN_THRESHOLD = 0.05


def normal_sf(z: float) -> float:
    """P(Z > z) for the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2))


# ---- relative churn ---------------------------------------------------------------

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
    return normal_sf((c_arm / n_arm - c_base / n_base) / se)


def check_guardrail(c_base: int, n_base: int, c_arm: int, n_arm: int,
                    gamma: float = DEFAULT_GAMMA) -> GuardrailResult:
    """c_* = churned subscribers by time t, n_* = subscribers at start."""
    if n_base <= 0 or n_arm <= 0:
        raise ValueError("sample sizes must be positive")
    churn_base, churn_arm = c_base / n_base, c_arm / n_arm
    if churn_base == 0:
        ratio = math.inf if churn_arm > 0 else 1.0
    else:
        ratio = churn_arm / churn_base
    p = churn_p_value(c_base, n_base, c_arm, n_arm)
    return GuardrailResult(churn_base, churn_arm, ratio, p,
                           ratio > gamma and p < P_CHURN_THRESHOLD)


# ---- absolute guardrails ------------------------------------------------------------

@dataclass
class GuardrailConfig:
    retention_floor: float = 0.80    # min share of sign-ups still subscribed (60-day horizon)
    refund_ceiling: float = 0.08     # max refunds / payments
    dispute_ceiling: float = 0.02    # max disputes / sign-ups
    min_signups: int = 30            # don't judge an arm on tiny samples


@dataclass(frozen=True)
class Breach:
    arm_id: str
    guardrail: str
    value: float
    limit: float
    message: str


def check_arm(row: dict, cfg: GuardrailConfig) -> list[Breach]:
    """`row` is a metrics row: needs arm_id, signups, churn, refund_rate, dispute_rate."""
    if row["signups"] < cfg.min_signups:
        return []
    out: list[Breach] = []
    retention = 1 - row["churn"]
    if retention < cfg.retention_floor:
        out.append(Breach(row["arm_id"], "retention_floor", retention, cfg.retention_floor,
                          f"Retention {retention:.1%} is below the {cfg.retention_floor:.0%} floor."))
    if row["refund_rate"] > cfg.refund_ceiling:
        out.append(Breach(row["arm_id"], "refund_ceiling", row["refund_rate"], cfg.refund_ceiling,
                          f"Refund rate {row['refund_rate']:.1%} is above the {cfg.refund_ceiling:.0%} ceiling."))
    if row["dispute_rate"] > cfg.dispute_ceiling:
        out.append(Breach(row["arm_id"], "dispute_ceiling", row["dispute_rate"], cfg.dispute_ceiling,
                          f"Dispute rate {row['dispute_rate']:.1%} is above the {cfg.dispute_ceiling:.0%} ceiling."))
    return out


def status(rows: list[dict], cfg: GuardrailConfig) -> list[dict]:
    """Per-arm guardrail status for the dashboard widget (baseline is reported but never paused)."""
    res = []
    for r in rows:
        breaches = check_arm(r, cfg)
        res.append({"arm_id": r["arm_id"], "ok": not breaches,
                    "retention": 1 - r["churn"], "refund_rate": r["refund_rate"],
                    "dispute_rate": r["dispute_rate"],
                    "breaches": [b.message for b in breaches]})
    return res
