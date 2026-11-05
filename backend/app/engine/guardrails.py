"""Absolute guardrails the agent cannot cross: retention floor, refund and dispute ceilings."""
from __future__ import annotations

from dataclasses import dataclass


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
