"""Person D: Simulated Clock.

Part 1 (this file for now): build a seeded, repeatable timeline of simulated events
whose day 0 is TODAY, so real PayPal sign-ups land inside the same window.

Differences from simulate.generate():
  * day 0 = today (UTC) instead of 1 Jan 2026
  * the winning / risky arm is chosen by its properties, not by its arm_id,
    so it works with any arm names Gemini invents
  * nothing here ever deletes events (real sign-ups are kept)
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone

from . import config  # noqa: F401  (importing config loads backend/.env)
from . import simulate
from .events import Event
from .schemas import Arm

# ---- settings (from .env, with safe defaults) ----
SIM_TOTAL_DAYS = int(os.getenv("SIM_TOTAL_DAYS", "60"))
SIM_SECONDS_PER_DAY = float(os.getenv("SIM_SECONDS_PER_DAY", "0.5"))
SIM_RANDOM_SEED = int(os.getenv("SIM_RANDOM_SEED", "42"))
SIM_SCENARIO = os.getenv("SIM_SCENARIO", "guardrail")

# ---- rates: (conversion, churn over the window) ----
NORMAL = (0.10, 0.10)
WINNER_RATES = simulate.WINNER["annual_discount"]   # (0.14, 0.02) - reuse teammate's numbers
RISKY_RATES = simulate.GUARDRAIL_RISKY              # (0.14, 0.28)


def today_start() -> datetime:
    """Midnight today in UTC, without timezone info (same style as simulate.START)."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def anchor_to_today() -> datetime:
    """Make simulated day 0 = today, so the metrics and MRR chart window covers real sign-ups.

    main.py reads simulate.START / simulate.DAYS every time it builds metrics,
    so changing them here is enough - no teammate files need editing.
    """
    start = today_start()
    simulate.START = start
    simulate.DAYS = SIM_TOTAL_DAYS
    return start


def pick_winner(arms: list[Arm]) -> Arm:
    """Story: the pricier annual plan wins. If there is no annual arm, the priciest challenger."""
    challengers = [a for a in arms if not a.is_baseline]
    annual = [a for a in challengers if a.interval == "YEAR"]
    pool = annual or challengers
    return max(pool, key=lambda a: a.monthly_equivalent)


def pick_risky(arms: list[Arm]) -> Arm:
    """Same rule as simulate.py: the last non-baseline arm misbehaves."""
    return [a for a in arms if not a.is_baseline][-1]


def scenario_rates(arms: list[Arm], scenario: str) -> dict[str, tuple[float, float]]:
    rates = {a.arm_id: NORMAL for a in arms}
    if scenario == "winner":
        rates[pick_winner(arms).arm_id] = WINNER_RATES
    elif scenario == "guardrail":
        rates[pick_risky(arms).arm_id] = RISKY_RATES
    else:
        raise ValueError(f"unknown scenario: {scenario}")
    return rates


def build_timeline(experiment_id: str, arms: list[Arm], scenario: str, seed: int,
                   start: datetime, days: int = SIM_TOTAL_DAYS) -> list[Event]:
    """All simulated events for the whole run, sorted by time. Same seed -> same events."""
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
        for i in range(simulate.VISITORS):
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
