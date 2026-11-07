"""Plain-English explanation of a Decision (Gemini, with template fallback)."""
from __future__ import annotations

from ..engine.decision import Action, Decision
from . import llm


def template_explanation(d: Decision) -> str:
    base = d.arms[0]
    if d.action == Action.STOP_ARM:
        return (f"Stopped arm '{d.arm_id}'. {d.reasons[d.arm_id]} "
                f"Continuing would risk losing subscribers faster than the baseline "
                f"({base.arm_id}) for no proven gain.")
    if d.action == Action.WINNER:
        return (f"Arm '{d.arm_id}' is the winner. {d.reasons[d.arm_id]} "
                f"Each visitor shown this price is worth more new monthly revenue than the "
                f"baseline, and no churn, refund or dispute guardrail fired.")
    return ("No arm is yet 95% likely to earn more per visitor than the baseline. "
            "Keep the experiment running to collect more data.")


def explain(d: Decision, goal: str | None = None) -> str:
    fallback = template_explanation(d)
    if not llm.enabled():
        return fallback
    summary = "; ".join(
        f"{a.arm_id}: paid conversion {a.paid_conversion:.1%}, new MRR per visitor "
        f"{a.mrr_per_visitor:.2f}, churn {a.churn:.1%}, projected 12-month LTV {a.projected_ltv:.2f}"
        for a in d.arms)
    prompt = ("Explain this pricing-experiment decision to a non-technical merchant in 2-3 "
              "sentences. Use only the facts given; do not invent numbers. Avoid statistics "
              "jargon: say '96% likely to earn more', not 'posterior'. LTV is only a projection.\n"
              f"Goal: {goal or 'not stated'}\nDecision: {d.action.value} {d.arm_id or ''}\n"
              f"Reasons: {d.reasons}\nArms: {summary}")
    try:
        return llm.generate(prompt, temperature=0.3) or fallback
    except Exception:
        return fallback
