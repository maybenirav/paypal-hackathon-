"""Aggregate raw events into per-arm metrics and ArmStats for the decision engine."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .engine.decision import ArmStats
from .events import Event
from .schemas import Arm


def aggregate(arms: list[Arm], events: list[Event], as_of: datetime | None = None) -> dict:
    by_arm: dict[str, dict] = {a.arm_id: {
        "visitors": 0, "signups": 0, "churned": 0, "revenue": 0.0, "denied": 0,
        "payments": 0, "refunds": 0, "disputes": 0, "failed": 0,
        "signup_ts": {}, "cancel_ts": {}} for a in arms}
    for e in events:
        if e.arm_id not in by_arm or (as_of and e.ts > as_of):
            continue
        m = by_arm[e.arm_id]
        if e.type == "VISIT":
            m["visitors"] += 1
        elif e.type == "SIGNUP":
            m["signups"] += 1
            m["signup_ts"][e.subscriber_id] = e.ts
        elif e.type == "CANCELLED":
            m["churned"] += 1
            m["cancel_ts"][e.subscriber_id] = e.ts
        elif e.type == "PAYMENT":
            m["revenue"] += e.amount
            m["payments"] += 1
        elif e.type == "REFUND":
            m["refunds"] += 1
            m["revenue"] -= e.amount
        elif e.type == "DISPUTE":
            m["disputes"] += 1
        elif e.type == "PAYMENT_FAILED":
            m["failed"] += 1
        elif e.type == "DENIED":
            m["denied"] += 1

    out = {}
    for a in arms:
        m = by_arm[a.arm_id]
        active = m["signups"] - m["churned"]
        out[a.arm_id] = {
            "arm_id": a.arm_id, "name": a.name, "price": a.price,
            "interval": a.interval, "is_baseline": a.is_baseline,
            "visitors": m["visitors"], "signups": m["signups"], "churned": m["churned"],
            "active": active, "denied": m["denied"],
            "conversion": m["signups"] / m["visitors"] if m["visitors"] else 0.0,
            "churn": m["churned"] / m["signups"] if m["signups"] else 0.0,
            "revenue": round(m["revenue"], 2),
            "payments": m["payments"], "refunds": m["refunds"], "disputes": m["disputes"],
            "failed_payments": m["failed"],
            "refund_rate": m["refunds"] / m["payments"] if m["payments"] else 0.0,
            "dispute_rate": m["disputes"] / m["signups"] if m["signups"] else 0.0,
            "mrr": round(active * a.monthly_equivalent, 2),
            "retention": _retention(m["signup_ts"], m["cancel_ts"]),
        }
    return out


def _retention(signups: dict, cancels: dict, weeks: int = 9) -> list[float]:
    """Share of subscribers still active w weeks after their own signup (w = 0..weeks-1)."""
    if not signups:
        return []
    if not cancels:
        return [1.0] * weeks
    out = []
    for w in range(weeks):
        alive = 0
        for sid, st in signups.items():
            ct = cancels.get(sid)
            if ct is None or (ct - st).days >= 7 * w:
                alive += 1
        out.append(round(alive / len(signups), 4))
    return out


def mrr_series(arms: list[Arm], events: list[Event], start: datetime, days: int,
               step: int = 7) -> list[dict]:
    """MRR per arm at weekly points; `start` is day 0."""
    from datetime import timedelta
    pts = []
    for d in range(0, days + 1, step):
        agg = aggregate(arms, events, as_of=start + timedelta(days=d))
        pts.append({"day": d, **{k: v["mrr"] for k, v in agg.items()}})
    return pts


def to_arm_stats(arms: list[Arm], agg: dict) -> tuple[ArmStats, list[ArmStats]]:
    stats = {a.arm_id: ArmStats(a.arm_id, round(a.monthly_equivalent, 4),
                                agg[a.arm_id]["visitors"], agg[a.arm_id]["signups"],
                                agg[a.arm_id]["churned"]) for a in arms}
    base = next((a for a in arms if a.is_baseline), arms[0])
    return stats[base.arm_id], [s for k, s in stats.items() if k != base.arm_id]
