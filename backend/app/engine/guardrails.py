"""Absolute guardrails the agent cannot cross: retention floor, refund and dispute ceilings.

A limit is breached only when the data makes it at least CONFIDENCE likely, not whenever the latest
number happens to wobble over it. Retention is projected to the horizon from monthly churn, so a
bad price shows up before its subscribers are 60 days old.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .analysis import CHURN_PRIOR_EVENTS, CHURN_PRIOR_MONTHS
from .ltv import DAYS_PER_MONTH

RETENTION_DAYS = 60
CONFIDENCE = 0.90
_SAMPLES = 4000


@dataclass
class GuardrailConfig:
    retention_floor: float = 0.80    # min share of subscribers projected to remain after 60 days
    refund_ceiling: float = 0.08     # max refunds / payments
    dispute_ceiling: float = 0.02    # max disputes / sign-ups
    min_signups: int = 30            # don't judge an arm on tiny samples
    churn_ratio: float = 1.30        # max monthly churn relative to the baseline
    auto_stop: bool = True           # stop an option automatically when it crosses a limit


@dataclass(frozen=True)
class Breach:
    arm_id: str
    guardrail: str
    value: float
    limit: float
    message: str


def projected_retention(monthly_churn: float, days: int = RETENTION_DAYS) -> float:
    return (1 - min(monthly_churn, 1.0)) ** (days / DAYS_PER_MONTH)


def _likely_above(hits: int, total: int, limit: float, confidence: float) -> bool:
    rng = np.random.default_rng(0)
    return bool((rng.beta(hits + 1, total - hits + 1, _SAMPLES) > limit).mean() >= confidence)


def _retention_breached(row: dict, floor: float, confidence: float) -> bool:
    if "exposure_months" not in row:
        return row.get("retention_60", 1 - row["churn"]) < floor
    if floor >= 1:
        return True
    # retention < floor is the same as monthly churn > this limit
    churn_limit = 1 - floor ** (DAYS_PER_MONTH / RETENTION_DAYS)
    rng = np.random.default_rng(0)
    draws = rng.gamma(row["churned"] + CHURN_PRIOR_EVENTS,
                      1 / (row["exposure_months"] + CHURN_PRIOR_MONTHS), _SAMPLES)
    return bool((draws > churn_limit).mean() >= confidence)


def _refund_breached(row: dict, ceiling: float, confidence: float) -> bool:
    if "payments" not in row:
        return row["refund_rate"] > ceiling
    return row["refund_rate"] > ceiling and _likely_above(row["refunds"], row["payments"], ceiling, confidence)


def _dispute_breached(row: dict, ceiling: float, confidence: float) -> bool:
    if "disputes" not in row:
        return row["dispute_rate"] > ceiling
    return row["dispute_rate"] > ceiling and _likely_above(row["disputes"], row["signups"], ceiling, confidence)


def check_arm(row: dict, cfg: GuardrailConfig, confidence: float = CONFIDENCE) -> list[Breach]:
    """`row` is a metrics row: needs arm_id, signups, churn, refund_rate, dispute_rate."""
    if row["signups"] < cfg.min_signups:
        return []
    out: list[Breach] = []
    if _retention_breached(row, cfg.retention_floor, confidence):
        retention = row.get("retention_60", 1 - row["churn"])
        out.append(Breach(row["arm_id"], "retention_floor", retention, cfg.retention_floor,
                          f"Projected {RETENTION_DAYS}-day retention {retention:.1%} is below "
                          f"the {cfg.retention_floor:.0%} floor."))
    if _refund_breached(row, cfg.refund_ceiling, confidence):
        out.append(Breach(row["arm_id"], "refund_ceiling", row["refund_rate"], cfg.refund_ceiling,
                          f"Refund rate {row['refund_rate']:.1%} is above the {cfg.refund_ceiling:.0%} ceiling."))
    if _dispute_breached(row, cfg.dispute_ceiling, confidence):
        out.append(Breach(row["arm_id"], "dispute_ceiling", row["dispute_rate"], cfg.dispute_ceiling,
                          f"Dispute rate {row['dispute_rate']:.1%} is above the {cfg.dispute_ceiling:.0%} ceiling."))
    return out


def status(rows: list[dict], cfg: GuardrailConfig) -> list[dict]:
    """Per-arm guardrail status for the dashboard widget (baseline is reported but never paused)."""
    res = []
    for r in rows:
        breaches = check_arm(r, cfg)
        res.append({"arm_id": r["arm_id"], "ok": not breaches,
                    "retention": r.get("retention_60", 1 - r["churn"]),
                    "retention_so_far": 1 - r["churn"], "refund_rate": r["refund_rate"],
                    "dispute_rate": r["dispute_rate"],
                    "breaches": [b.message for b in breaches]})
    return res
