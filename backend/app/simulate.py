"""Seeded, repeatable demo scenarios that emit events in the shared format.

This is a labelled synthetic cohort (`source="simulated"` on every event). It replays 60 days of
traffic in a second so the dashboard, guardrails and Save Agent have something to act on; the
real PayPal sandbox flow (plans, checkout, webhooks) runs alongside it and is marked `real`.

Modelled on purpose so the decision metric is honest:
  - trial arms take their first payment only after the trial, and some trials end unpaid;
  - annual arms pay once a year, so their monthly-equivalent price is price / 12;
  - a few active subscribers raise a cancel intent with a free-text reason (feeds the Save Agent).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from .events import Event
from .schemas import Arm

START = datetime(2026, 1, 1)
DAYS = 60
VISITORS = 2500

# (sign-up conversion, churn over the window) per arm_id
WINNER = {"baseline": (0.10, 0.10), "annual_discount": (0.15, 0.03),
          "monthly_trial": (0.13, 0.10), "premium_monthly": (0.08, 0.11)}
GUARDRAIL_RISKY = (0.14, 0.28)   # attractive sign-up rate, but churn spikes
BASE_REFUND, RISKY_REFUND = 0.02, 0.15
FAILED_PAYMENT = 0.03             # share of renewal attempts that fail (feeds the Save Agent)
TRIAL_DROP = 0.30                 # share of trial sign-ups that cancel before the first payment
CANCEL_INTENT = 0.03              # share of paying, non-churning subscribers who try to cancel
CANCEL_REASONS = ["Too expensive for me right now", "I'm not using it much lately",
                  "Travelling for a few weeks", "Found a cheaper option", "Just need a break"]


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

    def ev(arm: Arm, sid: str, kind: str, day: float, **kw) -> None:
        events.append(Event(experiment_id=experiment_id, arm_id=arm.arm_id, subscriber_id=sid,
                            type=kind, ts=START + timedelta(days=day), source="simulated", **kw))

    for arm in arms:
        conv, churn = rates[arm.arm_id]
        refund_p = RISKY_REFUND if arm.arm_id == risky_id else BASE_REFUND
        step = 365 if arm.interval == "YEAR" else 30
        for i in range(VISITORS):
            sid = f"{arm.arm_id}-{i}"
            visit_day = rng.randint(0, 14)
            ev(arm, sid, "VISIT", visit_day)
            if rng.random() >= conv:
                continue
            ev(arm, sid, "SIGNUP", visit_day)
            first_pay = visit_day + arm.trial_days
            if arm.trial_days and side.random() < TRIAL_DROP:
                ev(arm, sid, "CANCELLED", visit_day + side.randint(1, arm.trial_days))
                continue
            cancel_day = (first_pay + rng.randint(5, max(6, DAYS - first_pay - 1))
                          if rng.random() < churn else None)
            for d in range(first_pay, DAYS + 1, step):
                if cancel_day is not None and d >= cancel_day:
                    break
                if d > first_pay and side.random() < FAILED_PAYMENT:
                    ev(arm, sid, "PAYMENT_FAILED", d)
                    continue
                ev(arm, sid, "PAYMENT", d, amount=arm.price)
                if side.random() < refund_p:
                    ev(arm, sid, "REFUND", d + 1, amount=arm.price)
            if cancel_day is not None and cancel_day <= DAYS:
                ev(arm, sid, "CANCELLED", cancel_day)
            elif first_pay < DAYS - 3 and side.random() < CANCEL_INTENT:
                ev(arm, sid, "CANCEL_INTENT", side.randint(first_pay + 1, DAYS - 1),
                   reason=side.choice(CANCEL_REASONS))
    events.sort(key=lambda e: e.ts)
    return events
