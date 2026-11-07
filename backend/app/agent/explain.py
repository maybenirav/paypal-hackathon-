"""Plain-English explanation of a Decision (Gemini, with template fallback)."""
from __future__ import annotations

import logging
import os

from ..engine.decision import Action, Decision
from .pricing_agent import gemini_generate

log = logging.getLogger("planlab.agent")


def template_explanation(d: Decision) -> str:
    if d.action == Action.STOP_ARM:
        return (f"Stopped arm '{d.arm_id}'. {d.reasons[d.arm_id]} "
                f"Keeping it live would put subscribers at risk, whatever it does for sign-ups.")
    if d.action == Action.WINNER:
        if d.arms and d.arm_id == d.arms[0].arm_id:
            return f"Arm '{d.arm_id}' stays. {d.reasons[d.arm_id]} Keep your current price."
        return f"Arm '{d.arm_id}' is the winner. {d.reasons[d.arm_id]}"
    return (d.note or "No price option has clearly earned more than the baseline yet.") + (
        " Keep the experiment running to collect more data.")


def explain(d: Decision, goal: str | None = None) -> str:
    fallback = template_explanation(d)
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return fallback
    summary = "; ".join(
        f"{a.arm_id}: conv {a.conversion:.1%}, churn {a.churn:.1%}, LTV {a.ltv:.2f}"
        for a in d.arms)
    prompt = ("Explain this pricing-experiment decision to a non-technical merchant in 2-3 "
              "sentences. Use only the facts given; do not invent numbers.\n"
              f"Goal: {goal or 'not stated'}\nDecision: {d.action.value} {d.arm_id or ''}\n"
              f"Reasons: {d.reasons}\nNote: {d.note}\nArms: {summary}")
    try:
        return gemini_generate(prompt, key).strip()
    except Exception as exc:
        log.warning("Gemini explanation unavailable, using template: %s: %s", type(exc).__name__, exc)
        return fallback
