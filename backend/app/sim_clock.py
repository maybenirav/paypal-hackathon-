"""Person D: Simulated Clock.

Part 1: build a seeded, repeatable timeline of simulated events whose day 0 is TODAY,
        so real PayPal sign-ups land inside the same window.
Part 2: a clock that releases that timeline into the store one simulated day at a time,
        so the dashboard fills up live during the demo.

Rules this file follows:
  * the winning / risky arm is chosen by its properties, not its arm_id
    (works with any arm names Gemini invents)
  * only events with source="simulated" are ever removed - real sign-ups are kept
  * the clock runs as a background thread INSIDE the web server, because the
    store is in memory (a separate worker process could not see it)
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import config  # noqa: F401  (importing config loads backend/.env)
from . import simulate
from .events import Event
from .schemas import Arm
from .store import Experiment, store

log = logging.getLogger("planlab.sim")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


# ---- settings (from .env, with safe defaults) ----
MIN_DAYS = 20   # visitors arrive on days 0-14 and need room to churn later
SIM_TOTAL_DAYS = max(MIN_DAYS, _env_int("SIM_TOTAL_DAYS", 60))
SIM_SECONDS_PER_DAY = max(0.0, _env_float("SIM_SECONDS_PER_DAY", 0.5))
SIM_RANDOM_SEED = _env_int("SIM_RANDOM_SEED", 42)
SIM_SCENARIO = (os.getenv("SIM_SCENARIO") or "guardrail").strip().lower()
if SIM_SCENARIO not in ("winner", "guardrail"):
    SIM_SCENARIO = "guardrail"

# ---- rates: (conversion, churn over the window) ----
# Chosen so the story is unambiguous under the decision engine: only the story arm can win
# or be stopped, whatever the seed. Ordinary challengers convert a little worse than the
# baseline (more so when pricier - normal price sensitivity) and churn a little less, so
# random noise can't make them look like winners or trip a guardrail.
BASELINE_RATES = (0.10, 0.12)
ORDINARY_RATES = (0.085, 0.07)
WINNER_RATES = (0.17, 0.01)
VISITORS = 2500   # per arm; enough sign-ups that luck can't flip the story
RISKY_RATES = simulate.GUARDRAIL_RISKY              # (0.14, 0.28) - teammate's numbers
MIN_WINNER_PRICE_RATIO = 0.75   # an annual plan cheaper than this x baseline can't win on revenue


# =====================================================================
# Part 1: the timeline
# =====================================================================

def today_start() -> datetime:
    """Midnight today in UTC, without timezone info (same style as simulate.START)."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def anchor_to_today(days: int = SIM_TOTAL_DAYS) -> datetime:
    """Make simulated day 0 = today, so the metrics and MRR chart window covers real sign-ups.

    main.py reads simulate.START / simulate.DAYS every time it builds metrics,
    so changing them here is enough - no teammate files need editing.
    """
    start = today_start()
    simulate.START = start
    simulate.DAYS = days
    return start


def _challengers(arms: list[Arm]) -> list[Arm]:
    out = [a for a in arms if not a.is_baseline]
    if not out:
        raise ValueError("the experiment needs at least one non-baseline arm")
    return out


def _baseline(arms: list[Arm]) -> Arm:
    return next((a for a in arms if a.is_baseline), arms[0])


def pick_winner(arms: list[Arm]) -> Arm:
    """Story: the pricier annual plan wins. If there is no annual arm (or it is too cheap to
    beat the baseline on revenue), the priciest challenger wins instead."""
    challengers = _challengers(arms)
    floor = _baseline(arms).monthly_equivalent * MIN_WINNER_PRICE_RATIO
    annual = [a for a in challengers if a.interval == "YEAR" and a.monthly_equivalent >= floor]
    pool = annual or challengers
    return max(pool, key=lambda a: a.monthly_equivalent)


def pick_risky(arms: list[Arm]) -> Arm:
    """Same rule as simulate.py: the last non-baseline arm misbehaves."""
    return _challengers(arms)[-1]


def scenario_rates(arms: list[Arm], scenario: str) -> dict[str, tuple[float, float]]:
    if scenario not in ("winner", "guardrail"):
        raise ValueError(f"unknown scenario: {scenario}")
    base = _baseline(arms)
    rates: dict[str, tuple[float, float]] = {}
    for a in arms:
        if a.arm_id == base.arm_id:
            rates[a.arm_id] = BASELINE_RATES
        else:
            price_factor = min(1.0, base.monthly_equivalent / a.monthly_equivalent)
            rates[a.arm_id] = (round(ORDINARY_RATES[0] * price_factor, 4), ORDINARY_RATES[1])
    if scenario == "winner":
        rates[pick_winner(arms).arm_id] = WINNER_RATES
    else:
        rates[pick_risky(arms).arm_id] = RISKY_RATES
    return rates


def build_timeline(experiment_id: str, arms: list[Arm], scenario: str, seed: int,
                   start: datetime, days: int = SIM_TOTAL_DAYS) -> list[Event]:
    """All simulated events for the whole run, sorted by time. Same seed -> same events."""
    if days < MIN_DAYS:
        raise ValueError(f"days must be at least {MIN_DAYS}")
    rng = random.Random(seed)
    side = random.Random(seed + 1)   # separate stream for failed payments / refunds
    rates = scenario_rates(arms, scenario)
    risky_id = pick_risky(arms).arm_id if scenario == "guardrail" else None
    events: list[Event] = []

    def ev(arm: Arm, sid: str, etype: str, day: float, amount: float = 0) -> Event:
        return Event(experiment_id=experiment_id, arm_id=arm.arm_id, subscriber_id=sid,
                     type=etype, ts=start + timedelta(days=day), amount=amount,
                     source="simulated")

    for arm in arms:
        conv, churn = rates[arm.arm_id]
        refund_p = simulate.RISKY_REFUND if arm.arm_id == risky_id else simulate.BASE_REFUND
        step = 365 if arm.interval == "YEAR" else 30
        for i in range(VISITORS):
            sid = f"sim-{arm.arm_id}-{i}"
            visit_day = rng.randint(0, 14)
            events.append(ev(arm, sid, "VISIT", visit_day))
            if rng.random() >= conv:
                continue
            events.append(ev(arm, sid, "SIGNUP", visit_day))
            cancel_day = (visit_day + rng.randint(5, days - visit_day - 1)
                          if rng.random() < churn else None)
            for d in range(visit_day, days + 1, step):
                if cancel_day is not None and d >= cancel_day:
                    break
                if d > visit_day and side.random() < simulate.FAILED_PAYMENT:
                    events.append(ev(arm, sid, "PAYMENT_FAILED", d))
                    continue
                events.append(ev(arm, sid, "PAYMENT", d, arm.price))
                if side.random() < refund_p:
                    events.append(ev(arm, sid, "REFUND", d + 1, arm.price))
            if cancel_day is not None:
                events.append(ev(arm, sid, "CANCELLED", cancel_day))

    events.sort(key=lambda e: e.ts)
    return events


# =====================================================================
# Part 2: the clock
# =====================================================================

@dataclass
class ClockRun:
    experiment_id: str
    scenario: str
    seed: int
    start: datetime
    days: int
    seconds_per_day: float
    timeline: list[Event]
    day: int = -1              # last simulated day released (-1 = nothing yet)
    released: int = 0          # how many timeline events are already in the store
    status: str = "running"    # running | paused | done | stopped
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_runs: dict[str, ClockRun] = {}


def get_run(exp_id: str) -> ClockRun | None:
    return _runs.get(exp_id)


def _release_until(run: ClockRun, exp: Experiment, cutoff: datetime | None) -> None:
    """Move timeline events with ts < cutoff (or all, if cutoff is None) into the store."""
    i = run.released
    while i < len(run.timeline) and (cutoff is None or run.timeline[i].ts < cutoff):
        i += 1
    batch = run.timeline[run.released:i]
    if batch:
        store.add_events(exp, batch)
    run.released = i


def _step_run(run: ClockRun, n: int = 1) -> ClockRun:
    """Advance one specific run by n simulated days."""
    exp = store.get(run.experiment_id)
    with run.lock:
        if exp is None:
            run.status = "stopped"
            return run
        for _ in range(max(0, n)):
            if run.status in ("done", "stopped"):
                break
            run.day += 1
            if run.day >= run.days:
                run.day = run.days
                _release_until(run, exp, None)     # flush the tail (e.g. last-day refunds)
                run.status = "done"
            else:
                _release_until(run, exp, run.start + timedelta(days=run.day + 1))
    return run


def step(exp_id: str, n: int = 1) -> ClockRun:
    """Advance the clock by n simulated days. Safe to call by hand (tests, video) or from the thread."""
    return _step_run(_runs[exp_id], n)


def _loop(run: ClockRun) -> None:
    """Background thread: one simulated day every `seconds_per_day` seconds."""
    while True:
        time.sleep(run.seconds_per_day)
        if _runs.get(run.experiment_id) is not run or run.status in ("done", "stopped"):
            return
        if run.status == "running":
            try:
                _step_run(run)
            except Exception:   # never let the ticking thread die silently mid-demo
                log.exception("simulated clock tick failed")


def start(exp: Experiment, scenario: str | None = None, seed: int | None = None,
          seconds_per_day: float | None = None, days: int | None = None) -> ClockRun:
    """(Re)start the simulation for an experiment.

    Removes old SIMULATED events only, keeps real ones, resets the decision state,
    releases day 0 straight away, then (if seconds_per_day > 0) ticks in the background.
    seconds_per_day = 0 means manual mode: call step() yourself.
    """
    scenario = scenario or SIM_SCENARIO
    seed = SIM_RANDOM_SEED if seed is None else seed
    seconds_per_day = SIM_SECONDS_PER_DAY if seconds_per_day is None else seconds_per_day
    days = days or SIM_TOTAL_DAYS

    t0 = anchor_to_today(days)
    timeline = build_timeline(exp.id, exp.arms, scenario, seed, t0, days)  # may raise ValueError

    old = _runs.get(exp.id)
    if old:
        with old.lock:          # wait for an in-flight tick, then stop it
            old.status = "stopped"

    # Keep real events, drop old simulated ones. Copies (new objects) make db.py notice the
    # list was rebuilt and rewrite this experiment's events instead of appending to stale rows.
    with store._lock:
        exp.events = [e.model_copy() for e in exp.events if e.source != "simulated"]
    exp.status, exp.winner, exp.stopped_arms = "running", None, []

    run = ClockRun(exp.id, scenario, seed, t0, days, seconds_per_day, timeline)
    _runs[exp.id] = run
    _step_run(run)   # day 0 appears immediately
    if seconds_per_day > 0:
        threading.Thread(target=_loop, args=(run,), daemon=True).start()
    return run


def pause(exp_id: str) -> ClockRun:
    run = _runs[exp_id]
    if run.status == "running":
        run.status = "paused"
    return run


def resume(exp_id: str) -> ClockRun:
    run = _runs[exp_id]
    if run.status == "paused":
        run.status = "running"
    return run


def status(run: ClockRun) -> dict:
    exp = store.get(run.experiment_id)
    real = [e for e in exp.events if e.source == "real"] if exp else []
    return {
        "experiment_id": run.experiment_id,
        "status": run.status,
        "scenario": run.scenario,
        "seed": run.seed,
        "day": max(run.day, 0),
        "total_days": run.days,
        "seconds_per_day": run.seconds_per_day,
        "start": run.start.isoformat(),
        "released_events": run.released,
        "total_events": len(run.timeline),
        "real_events": len(real),
        "real_signups": sum(e.type == "SIGNUP" for e in real),
    }
