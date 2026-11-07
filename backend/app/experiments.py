"""One consistent view of an experiment: its metrics, value model, traffic split and arm assignment."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import simulate
from .engine import bayes
from .engine.analysis import ArmData, ArmResult, analyze
from .engine.guardrails import projected_retention
from .engine.guardrails import status as guardrail_status
from .engine.stats import two_proportion_z
from .events import utcnow
from .memory import MemoryRecord, memory
from .metrics import aggregate, to_arm_data
from .schemas import Arm
from .store import Experiment


@dataclass
class Snapshot:
    data: dict[str, ArmData]
    results: dict[str, ArmResult]
    rows: list[dict]


def baseline_of(arms: list[Arm]) -> Arm:
    return next((a for a in arms if a.is_baseline), arms[0])


def rolled_out_winner(exp: Experiment) -> str | None:
    """Once a rollout is approved, every new customer gets the winning option."""
    return exp.winner if exp.rollout and exp.rollout.get("status") == "approved" else None


def active_stops(exp: Experiment, days: int | None = None) -> set[str]:
    """Options stopped by this point in the timeline. `days=None` means now."""
    return {a for a in exp.stopped_arms
            if days is None or exp.stops.get(a, {}).get("day") is None or exp.stops[a]["day"] <= days}


def timeline_day(exp: Experiment, days: int | None = None) -> int | None:
    """The simulated day a stop made now belongs to. Real data has no timeline, so it has no day."""
    if any(e.source == "real" for e in exp.events):
        return None
    return simulate.DAYS if days is None else days


def record_stop(exp: Experiment, arm_id: str, reasons: str, day: int | None, automatic: bool) -> None:
    if arm_id not in exp.stopped_arms:
        exp.stopped_arms.append(arm_id)
    exp.stops[arm_id] = {"arm_id": arm_id, "day": day, "at": utcnow().isoformat(), "reasons": reasons,
                         "automatic": automatic, "alert": None}
    if exp.winner == arm_id:
        exp.winner, exp.rollout = None, None
    decision = exp.last_decision or {}
    if decision.get("action") == "winner" and decision.get("arm_id") == arm_id:
        exp.last_decision = None    # an option that's been stopped can't still be shown as the winner
    if not exp.winner:
        exp.status = "stopped_arm"


def record_memory(exp: Experiment, rows: list[dict]) -> None:
    arms = {a.arm_id: a for a in exp.arms}
    memory.upsert(MemoryRecord(
        exp.id, exp.goal, exp.winner, list(exp.stopped_arms),
        [{"arm_id": r["arm_id"], "name": r["name"], "price": r["price"], "interval": r["interval"],
          "trial_days": arms[r["arm_id"]].trial_days, "conversion": r["conversion"],
          "churn": r["churn"], "ltv": r["ltv"]} for r in rows],
        simulated=not any(e.source == "real" for e in exp.events),
        winner_name=arms[exp.winner].name if exp.winner else None,
        stopped_names=[arms[a].name for a in exp.stopped_arms]))


def window(exp: Experiment, days: int | None) -> tuple[datetime | None, datetime]:
    """The moment to replay up to, and when observation ends: the demo's last day, or now for real data."""
    real = [e.ts for e in exp.events if e.source == "real"]
    if real:
        start = min(real).replace(hour=0, minute=0, second=0, microsecond=0)
        until = utcnow()
    else:
        start = simulate.START
        until = start + timedelta(days=simulate.DAYS)
    return (start + timedelta(days=days) if days is not None else None), until


def snapshot(exp: Experiment, days: int | None = None) -> Snapshot:
    as_of, until = window(exp, days)
    agg = aggregate(exp.arms, exp.events, as_of, until)
    data = to_arm_data(exp.arms, agg)
    base = baseline_of(exp.arms)
    stopped = active_stops(exp, days)
    live = {a.arm_id for a in exp.arms if a.arm_id not in stopped}
    results = analyze(data, base.arm_id, live)
    rolled = rolled_out_winner(exp)
    base_row = agg[base.arm_id]

    rows = []
    for arm in exp.arms:
        k = arm.arm_id
        row, r = agg[k], results[k]
        z = None
        if k != base.arm_id and row["visitors"] and base_row["visitors"]:
            z = round(two_proportion_z(base_row["signups"], base_row["visitors"],
                                       row["signups"], row["visitors"]).z, 2)
        share = r.allocation if rolled is None else float(k == rolled)
        rows.append({
            **row,
            "ltv": round(r.ltv, 2),
            "value_per_visitor": round(r.value_per_visitor, 2),
            "value_ci": [round(x, 2) for x in r.value_ci],
            "conv_ci": [round(x, 4) for x in r.conv_ci],
            "monthly_churn": round(r.monthly_churn, 4),
            "retention_60": round(projected_retention(r.monthly_churn), 4),
            "prob_best": round(r.prob_best, 3),
            "prob_beats_baseline": None if r.prob_beats_baseline is None else round(r.prob_beats_baseline, 3),
            "lift": None if r.lift is None else round(r.lift, 3),
            "allocation": round(share, 3),
            "z": z,
            "status": "stopped" if k in stopped else ("winner" if k == exp.winner else "running"),
        })
    return Snapshot({a.arm_id: a for a in data}, results, rows)


def guardrails_view(exp: Experiment, rows: list[dict]) -> list[dict]:
    return guardrail_status(rows, exp.guardrails)


def pick_arm(exp: Experiment, visitor_id: str | None = None) -> tuple[str, dict[str, float], bool]:
    """Choose the option for a customer: (arm id, current weights, whether they've been here before).

    A returning customer keeps the offer they first saw, unless that option has since been stopped
    or the test has been rolled out. Anyone without a visitor id is treated as new each time.
    """
    weights = {r["arm_id"]: r["allocation"] for r in snapshot(exp).rows if r["allocation"] > 0}
    known = exp.assignments.get(visitor_id) if visitor_id else None
    if known in weights:
        return known, weights, True
    arm_id = bayes.assign_arm(weights, random.Random())
    if visitor_id:
        exp.assignments[visitor_id] = arm_id
    return arm_id, weights, known is not None
