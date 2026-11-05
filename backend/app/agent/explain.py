"""Plain-English explanation of a Decision (Gemini, with template fallback)."""
from __future__ import annotations

import os

import httpx

from ..engine.decision import Action, Decision
from .pricing_agent import GEMINI_URL


def template_explanation(d: Decision) -> str:
    base = d.arms[0]
    if d.action == Action.STOP_ARM:
        return (f"Stopped arm '{d.arm_id}'. {d.reasons[d.arm_id]} "
                f"Continuing would risk losing subscribers faster than the baseline "
                f"({base.arm_id}) for no proven gain.")
    if d.action == Action.WINNER:
        return (f"Arm '{d.arm_id}' is the winner. {d.reasons[d.arm_id]} "
                f"It beats the baseline on both sign-ups and long-run revenue.")
    return ("No arm has shown a statistically significant improvement in conversion with "
            "higher 12-month LTV yet. Keep the experiment running to collect more data.")


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
              f"Reasons: {d.reasons}\nArms: {summary}")
    try:
        r = httpx.post(
            GEMINI_URL.format(model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash")),
            params={"key": key}, timeout=30,
            json={"contents": [{"parts": [{"text": prompt}]}]})
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return fallback
