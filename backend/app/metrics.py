"""Turn raw events into per-arm metrics.

Each person counts once however many times an event repeats, and a sign-up implies a visit. Retention
and churn account for how long each subscriber has actually been observed, so a group that joined
last week doesn't look more loyal than one that joined two months ago.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .engine.analysis import ArmData
from .engine.ltv import DAYS_PER_MONTH
from .events import Event
from .schemas import Arm

SECONDS_PER_DAY = 86_400
RETENTION_WEEKS = 9
MIN_REACHED_SHARE = 0.10    # a week needs at least this share of the cohort to have got that far


@dataclass
class _Tally:
    visitors: set[str] = field(default_factory=set)
    signup_ts: dict[str, datetime] = field(default_factory=dict)
    cancel_ts: dict[str, datetime] = field(default_factory=dict)
    first_paid: dict[str, datetime] = field(default_factory=dict)
    disputes: set[str] = field(default_factory=set)
    revenue: float = 0.0
    payments: int = 0
    refunds: int = 0
    failed: int = 0
    denied: int = 0


def _earliest(seen: dict[str, datetime], key: str, ts: datetime) -> None:
    if key not in seen or ts < seen[key]:
        seen[key] = ts


def _collect(arms: list[Arm], events: list[Event], as_of: datetime | None) -> dict[str, _Tally]:
    tallies = {a.arm_id: _Tally() for a in arms}
    for e in events:
        tally = tallies.get(e.arm_id)
        if tally is None or (as_of is not None and e.ts > as_of):
            continue
        if e.type == "VISIT":
            tally.visitors.add(e.subscriber_id)
        elif e.type == "SIGNUP":
            tally.visitors.add(e.subscriber_id)
            _earliest(tally.signup_ts, e.subscriber_id, e.ts)
        elif e.type == "CANCELLED":
            _earliest(tally.cancel_ts, e.subscriber_id, e.ts)
        elif e.type == "PAYMENT":
            tally.revenue += e.amount
            tally.payments += 1
            _earliest(tally.first_paid, e.subscriber_id, e.ts)
        elif e.type == "REFUND":
            tally.refunds += 1
            tally.revenue -= e.amount
        elif e.type == "DISPUTE":
            tally.disputes.add(e.subscriber_id)
        elif e.type == "PAYMENT_FAILED":
            tally.failed += 1
        elif e.type == "DENIED":
            tally.denied += 1
    for tally in tallies.values():
        tally.cancel_ts = {k: v for k, v in tally.cancel_ts.items() if k in tally.signup_ts}
    return tallies


def _days(start: datetime, stop: datetime) -> float:
    return max((stop - start).total_seconds(), 0.0) / SECONDS_PER_DAY


def _paying(tally: _Tally, at: datetime) -> int:
    """Subscribers at `at` who have actually paid, so unpaid free trials aren't counted as revenue."""
    count = 0
    for sid, paid in tally.first_paid.items():
        cancelled = tally.cancel_ts.get(sid)
        if sid in tally.signup_ts and paid <= at and (cancelled is None or cancelled > at):
            count += 1
    return count


def retention_curve(signups: dict[str, datetime], cancels: dict[str, datetime], end: datetime,
                    weeks: int = RETENTION_WEEKS) -> list[float | None]:
    """Life-table estimate of the share still subscribed w weeks after signing up.

    People who joined recently leave the at-risk group when their observation runs out, instead of
    being counted as retained. Weeks too few people have reached come back as None.
    """
    if not signups:
        return []
    followed = {k: _days(ts, end) for k, ts in signups.items()}
    lifetime = {k: _days(signups[k], ts) for k, ts in cancels.items()}
    needed = max(1, MIN_REACHED_SHARE * len(signups))
    curve: list[float | None] = [1.0]
    alive = 1.0
    for week in range(1, weeks):
        start, stop = 7 * (week - 1), 7 * week
        if sum(days >= stop for days in followed.values()) < needed:
            curve.append(None)
            continue
        at_risk = censored = left = 0
        for sid, days in followed.items():
            ended = lifetime.get(sid)
            if days < start or (ended is not None and ended < start):
                continue
            at_risk += 1
            if ended is not None and ended < stop:
                left += 1
            elif ended is None and days < stop:
                censored += 1
        exposed = at_risk - censored / 2
        if exposed > 0:
            alive *= 1 - left / exposed
        curve.append(round(alive, 4))
    return curve


def aggregate(arms: list[Arm], events: list[Event], as_of: datetime | None = None,
              until: datetime | None = None, with_retention: bool = True) -> dict:
    """Per-arm metrics. `as_of` replays history up to that moment; `until` is when observation ends."""
    tallies = _collect(arms, events, as_of)
    latest = max((e.ts for e in events), default=None)
    end = as_of or (max(until, latest) if until and latest else until or latest)

    out = {}
    for a in arms:
        t = tallies[a.arm_id]
        signups = len(t.signup_ts)
        churned = len(t.cancel_ts)
        visitors = len(t.visitors)
        exposure_days = 0.0
        if end is not None:
            exposure_days = sum(_days(ts, t.cancel_ts.get(sid, end)) for sid, ts in t.signup_ts.items())
        paying = _paying(t, end) if end is not None else 0
        out[a.arm_id] = {
            "arm_id": a.arm_id, "name": a.name, "price": a.price,
            "interval": a.interval, "is_baseline": a.is_baseline,
            "visitors": visitors, "signups": signups, "churned": churned,
            "active": signups - churned, "paying": paying, "denied": t.denied,
            "conversion": signups / visitors if visitors else 0.0,
            "churn": churned / signups if signups else 0.0,
            "exposure_months": round(exposure_days / DAYS_PER_MONTH, 3),
            "revenue": round(t.revenue, 2),
            "payments": t.payments, "refunds": t.refunds, "disputes": len(t.disputes),
            "failed_payments": t.failed,
            "refund_rate": min(t.refunds / t.payments, 1.0) if t.payments else 0.0,
            "dispute_rate": len(t.disputes) / signups if signups else 0.0,
            "mrr": round(paying * a.monthly_equivalent, 2),
            "retention": retention_curve(t.signup_ts, t.cancel_ts, end) if with_retention and end is not None else [],
        }
    return out


def mrr_series(arms: list[Arm], events: list[Event], start: datetime, days: int,
               step: int = 7) -> list[dict]:
    """MRR per arm at regular points; `start` is day 0."""
    tallies = _collect(arms, events, None)
    points = []
    for d in range(0, days + 1, step):
        at = start + timedelta(days=d)
        points.append({"day": d, **{a.arm_id: round(_paying(tallies[a.arm_id], at) * a.monthly_equivalent, 2)
                                    for a in arms}})
    return points


def to_arm_data(arms: list[Arm], agg: dict) -> list[ArmData]:
    return [ArmData(a.arm_id, a.price, a.interval, a.trial_days, a.setup_fee,
                    agg[a.arm_id]["visitors"], agg[a.arm_id]["signups"], agg[a.arm_id]["churned"],
                    agg[a.arm_id]["exposure_months"], agg[a.arm_id]["payments"], agg[a.arm_id]["refunds"])
            for a in arms]
