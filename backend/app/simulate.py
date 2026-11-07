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
WINNING_RATES = WINNER["annual_discount"]
DEFAULT_RATES = (0.10, 0.10)
GUARDRAIL_RISKY = (0.14, 0.28)   # attractive sign-up rate, but churn spikes
BASE_REFUND, RISKY_REFUND = 0.02, 0.15
FAILED_PAYMENT = 0.03             # share of renewal attempts that fail (feeds the Save Agent)
CANCEL_INTENT = 0.04              # share of staying subscribers who start to cancel, then don't


def _challengers(arms: list[Arm]) -> list[Arm]:
    return [a for a in arms if not a.is_baseline]


def scenario_rates(arms: list[Arm], scenario: str) -> dict[str, tuple[float, float]]:
    if scenario == "winner":
        rates = {a.arm_id: WINNER.get(a.arm_id, DEFAULT_RATES) for a in arms}
        if WINNING_RATES not in rates.values() and _challengers(arms):
            # The AI names its own options, so choose the winner by role: an annual plan if there is one
            annual = [a for a in _challengers(arms) if a.interval == "YEAR"]
            rates[(annual or _challengers(arms))[0].arm_id] = WINNING_RATES
        return rates
    if scenario == "guardrail":
        rates = {a.arm_id: DEFAULT_RATES for a in arms}
        if _challengers(arms):
            rates[_challengers(arms)[-1].arm_id] = GUARDRAIL_RISKY
        return rates
    raise ValueError(f"unknown scenario: {scenario}")


def rates_risky(arms: list[Arm], scenario: str) -> str | None:
    """The arm whose behaviour is made bad in the guardrail scenario."""
    if scenario != "guardrail" or not _challengers(arms):
        return None
    return _challengers(arms)[-1].arm_id


def generate(experiment_id: str, arms: list[Arm], scenario: str, seed: int = 7) -> list[Event]:
    rng = random.Random(seed)
    side = random.Random(seed + 1)   # separate stream: extra event types never change sign-up/churn draws
    rates = scenario_rates(arms, scenario)
    events: list[Event] = []
    risky_id = rates_risky(arms, scenario)

    def emit(arm: Arm, sid: str, kind: str, day: int, amount: float = 0.0) -> None:
        events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id, subscriber_id=sid, type=kind,
                            ts=START + timedelta(days=day), amount=amount, source="simulated"))

    for arm in arms:
        conv, churn = rates[arm.arm_id]
        refund_p = RISKY_REFUND if arm.arm_id == risky_id else BASE_REFUND
        for i in range(VISITORS):
            sid = f"{arm.arm_id}-{i}"
            visit_day = rng.randint(0, 14)
            emit(arm, sid, "VISIT", visit_day)
            if rng.random() >= conv:
                continue
            emit(arm, sid, "SIGNUP", visit_day)
            cancel_day = visit_day + rng.randint(5, DAYS - visit_day - 1) if rng.random() < churn else None
            step = 365 if arm.interval == "YEAR" else 30
            first_payment = visit_day + arm.trial_days   # a free trial isn't charged until it ends
            for d in range(first_payment, DAYS + 1, step):
                if cancel_day is not None and d >= cancel_day:
                    break
                if d > first_payment and side.random() < FAILED_PAYMENT:
                    emit(arm, sid, "PAYMENT_FAILED", d)
                    continue
                emit(arm, sid, "PAYMENT", d, arm.price + (arm.setup_fee if d == first_payment else 0.0))
                if side.random() < refund_p:
                    emit(arm, sid, "REFUND", d + 1, arm.price)
            if cancel_day is not None:
                emit(arm, sid, "CANCELLED", cancel_day)
            elif side.random() < CANCEL_INTENT:
                emit(arm, sid, "CANCEL_INTENT", side.randint(visit_day + 3, DAYS))
    events.sort(key=lambda e: e.ts)
    return events
