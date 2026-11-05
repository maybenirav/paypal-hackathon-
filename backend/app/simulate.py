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
BASE_REFUND, RISKY_REFUND = 0.02, 0.15
FAILED_PAYMENT = 0.03             # share of renewal attempts that fail (feeds the Save Agent)


def scenario_rates(arms: list[Arm], scenario: str) -> dict[str, tuple[float, float]]:
    if scenario == "winner":
        return {a.arm_id: WINNER.get(a.arm_id, (0.10, 0.10)) for a in arms}
    if scenario == "guardrail":
        rates = {a.arm_id: (0.10, 0.10) for a in arms}
        risky = [a for a in arms if not a.is_baseline][-1]
        rates[risky.arm_id] = GUARDRAIL_RISKY
        return rates
    raise ValueError(f"unknown scenario: {scenario}")


def rates_risky(arms: list[Arm], scenario: str) -> str | None:
    """The arm whose behaviour is made bad in the guardrail scenario."""
    if scenario != "guardrail":
        return None
    return [a for a in arms if not a.is_baseline][-1].arm_id


def generate(experiment_id: str, arms: list[Arm], scenario: str, seed: int = 7) -> list[Event]:
    rng = random.Random(seed)
    side = random.Random(seed + 1)   # separate stream: extra event types never change sign-up/churn draws
    rates = scenario_rates(arms, scenario)
    events: list[Event] = []
    risky_id = rates_risky(arms, scenario)
    for arm in arms:
        conv, churn = rates[arm.arm_id]
        refund_p = RISKY_REFUND if arm.arm_id == risky_id else BASE_REFUND
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
                ts = START + timedelta(days=d)
                if d > visit_day and side.random() < FAILED_PAYMENT:
                    events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id,
                                        subscriber_id=sid, type="PAYMENT_FAILED", ts=ts,
                                        source="simulated"))
                    continue
                events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id,
                                    subscriber_id=sid, type="PAYMENT", amount=arm.price,
                                    ts=ts, source="simulated"))
                if side.random() < refund_p:
                    events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id,
                                        subscriber_id=sid, type="REFUND", amount=arm.price,
                                        ts=ts + timedelta(days=1), source="simulated"))
            if cancel_day is not None:
                events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id,
                                    subscriber_id=sid, type="CANCELLED",
                                    ts=START + timedelta(days=cancel_day), source="simulated"))
    events.sort(key=lambda e: e.ts)
    return events
