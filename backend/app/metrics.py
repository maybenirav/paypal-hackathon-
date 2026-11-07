"""Aggregate raw events into per-arm metrics and ArmStats for the decision engine."""
from __future__ import annotations

from datetime import datetime, timedelta

from .engine.decision import ArmStats
from .events import Event
from .schemas import Arm


def _interval_days(arm: Arm) -> int:
    return 365 if arm.interval == "YEAR" else 30


def aggregate(arms: list[Arm], events: list[Event], as_of: datetime | None = None) -> dict:
    by_arm: dict[str, dict] = {a.arm_id: {
        "visitors": 0, "signups": 0, "churned": 0, "revenue": 0.0, "denied": 0,
        "payments": 0, "refunds": 0, "disputes": 0, "failed": 0, "real": 0,
        "signup_ts": {}, "cancel_ts": {}, "pay_count": {}, "first_pay": {}} for a in arms}
    latest: datetime | None = None
    for e in events:
        if e.arm_id not in by_arm or (as_of and e.ts > as_of):
            continue
        latest = e.ts if latest is None or e.ts > latest else latest
        m = by_arm[e.arm_id]
        m["real"] += e.source == "real"
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
            m["pay_count"][e.subscriber_id] = m["pay_count"].get(e.subscriber_id, 0) + 1
            m["first_pay"].setdefault(e.subscriber_id, e.ts)
        elif e.type == "REFUND":
            m["refunds"] += 1
            m["revenue"] -= e.amount
        elif e.type == "DISPUTE":
            m["disputes"] += 1
        elif e.type == "PAYMENT_FAILED":
            m["failed"] += 1
        elif e.type == "DENIED":
            m["denied"] += 1

    now = as_of or latest
    out = {}
    for a in arms:
        m = by_arm[a.arm_id]
        # Churn is measured on paying subscribers. A trial that ends unpaid is a lost
        # conversion (it already lowers paid_conversion), not a churned customer.
        paid_cancels = {s: t for s, t in m["cancel_ts"].items() if s in m["pay_count"]}
        trial_cancels = m["churned"] - len(paid_cancels)
        m["churned"], m["cancel_ts"] = len(paid_cancels), paid_cancels
        paid = len(m["pay_count"])
        active = paid - m["churned"]
        conv_paid = paid / m["visitors"] if m["visitors"] else 0.0
        out[a.arm_id] = {
            "arm_id": a.arm_id, "name": a.name, "price": a.price, "currency": a.currency,
            "interval": a.interval, "trial_days": a.trial_days, "is_baseline": a.is_baseline,
            "visitors": m["visitors"], "signups": m["signups"], "paid": paid,
            "churned": m["churned"], "trial_cancels": trial_cancels, "active": active,
            "denied": m["denied"],
            "conversion": m["signups"] / m["visitors"] if m["visitors"] else 0.0,
            "paid_conversion": conv_paid,
            "mrr_per_visitor": round(conv_paid * a.monthly_equivalent, 4),
            "trial_to_paid": _trial_to_paid(a, m, now),
            "first_renewal": _first_renewal(a, m, now),
            "churn": m["churned"] / paid if paid else 0.0,
            "revenue": round(m["revenue"], 2),
            "payments": m["payments"], "refunds": m["refunds"], "disputes": m["disputes"],
            "failed_payments": m["failed"],
            "refund_rate": m["refunds"] / m["payments"] if m["payments"] else 0.0,
            "dispute_rate": m["disputes"] / paid if paid else 0.0,
            "mrr": round(active * a.monthly_equivalent, 2),
            "retention": _retention({s: t for s, t in m["signup_ts"].items()
                                     if s in m["pay_count"]}, m["cancel_ts"]),
            "real_events": m["real"],
        }
    return out


def _trial_to_paid(arm: Arm, m: dict, now: datetime | None) -> float | None:
    """Share of sign-ups whose trial has ended that went on to pay. None without a trial."""
    if arm.trial_days == 0 or now is None:
        return None
    due = [s for s, ts in m["signup_ts"].items() if ts + timedelta(days=arm.trial_days) <= now]
    return round(sum(s in m["pay_count"] for s in due) / len(due), 4) if due else None


def _first_renewal(arm: Arm, m: dict, now: datetime | None) -> float | None:
    """Share of paying subscribers whose first renewal is due that renewed (paid twice)."""
    if now is None:
        return None
    step = timedelta(days=_interval_days(arm))
    due = [s for s, ts in m["first_pay"].items() if ts + step <= now]
    return round(sum(m["pay_count"][s] >= 2 for s in due) / len(due), 4) if due else None


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
    pts = []
    for d in range(0, days + 1, step):
        agg = aggregate(arms, events, as_of=start + timedelta(days=d))
        pts.append({"day": d, **{k: v["mrr"] for k, v in agg.items()}})
    return pts


def to_arm_stats(arms: list[Arm], agg: dict) -> tuple[ArmStats, list[ArmStats]]:
    stats = {a.arm_id: ArmStats(a.arm_id, round(a.monthly_equivalent, 4),
                                agg[a.arm_id]["visitors"], agg[a.arm_id]["signups"],
                                agg[a.arm_id]["churned"], agg[a.arm_id]["paid"]) for a in arms}
    base = next((a for a in arms if a.is_baseline), arms[0])
    return stats[base.arm_id], [s for k, s in stats.items() if k != base.arm_id]
