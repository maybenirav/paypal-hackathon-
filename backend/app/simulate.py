"""Seeded, repeatable demo scenarios that emit events in the shared format."""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from .events import Event
from .schemas import Arm

START = datetime(2026, 1, 1)
DAYS = 60
VISITORS = 1000

# (conversion, churn over the window) per arm_id
WINNER = {"baseline": (0.10, 0.10), "annual_discount": (0.14, 0.02),
          "monthly_trial": (0.11, 0.10), "premium_monthly": (0.09, 0.11)}
GUARDRAIL_RISKY = (0.14, 0.28)   # attractive sign-up rate, but churn spikes


def scenario_rates(arms: list[Arm], scenario: str) -> dict[str, tuple[float, float]]:
    if scenario == "winner":
        return {a.arm_id: WINNER.get(a.arm_id, (0.10, 0.10)) for a in arms}
    if scenario == "guardrail":
        rates = {a.arm_id: (0.10, 0.10) for a in arms}
        risky = [a for a in arms if not a.is_baseline][-1]
        rates[risky.arm_id] = GUARDRAIL_RISKY
        return rates
    raise ValueError(f"unknown scenario: {scenario}")


def generate(experiment_id: str, arms: list[Arm], scenario: str, seed: int = 7) -> list[Event]:
    rng = random.Random(seed)
    rates = scenario_rates(arms, scenario)
    events: list[Event] = []
    for arm in arms:
        conv, churn = rates[arm.arm_id]
        for i in range(VISITORS):
            sid = f"{arm.arm_id}-{i}"
            visit_day = rng.randint(0, 14)
            events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id, subscriber_id=sid,
                                type="VISIT", ts=START + timedelta(days=visit_day), source="simulated"))
            if rng.random() >= conv:
                continue
            events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id, subscriber_id=sid,
                                type="SIGNUP", ts=START + timedelta(days=visit_day),
                                source="simulated"))
            cancel_day = visit_day + rng.randint(5, DAYS - visit_day - 1) if rng.random() < churn else None
            step = 365 if arm.interval == "YEAR" else 30
            for d in range(visit_day, DAYS + 1, step):
                if cancel_day is not None and d >= cancel_day:
                    break
                events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id,
                                    subscriber_id=sid, type="PAYMENT", amount=arm.price,
                                    ts=START + timedelta(days=d), source="simulated"))
            if cancel_day is not None:
                events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id,
                                    subscriber_id=sid, type="CANCELLED",
                                    ts=START + timedelta(days=cancel_day), source="simulated"))
    events.sort(key=lambda e: e.ts)
    return events
