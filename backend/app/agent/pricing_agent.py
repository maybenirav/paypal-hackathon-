"""Pricing agent: merchant goal -> 2-4 experiment arms (Gemini, with offline fallback)."""
from __future__ import annotations

import json

from ..memory import Memory, adapt_fallback, memory as default_memory
from ..schemas import Arm, GoalRequest
from . import llm

SYSTEM_PROMPT = """You design pricing experiments for a PayPal-billed subscription business.
Return ONLY a JSON array of {n} arms. The first arm MUST be the baseline at the current price
(is_baseline=true). Each arm: {{"arm_id": "short_snake_id", "name": str, "price": number,
"interval": "MONTH"|"YEAR", "trial_days": int 0-90, "setup_fee": number, "is_baseline": bool,
"reason": one sentence on why this arm tests the merchant's goal}}.
Prices are per interval, in {currency}. Keep prices realistic (within 0.5x-3x of the current price
per month-equivalent). Vary one or two levers per arm so results are interpretable.
PayPal can only change the price of an existing plan in place when the interval, trial and setup fee
stay the same as the baseline; any other arm can only be rolled out to new sign-ups. Prefer arms that
keep those levers when the goal is about price, and say in "reason" when an arm needs a new plan."""


def fallback_arms(req: GoalRequest) -> list[Arm]:
    """Deterministic arms used when no Gemini key is configured or the call fails."""
    p, c = req.current_price, req.currency
    arms = [
        Arm(arm_id="baseline", name="Baseline monthly", price=p, currency=c, is_baseline=True,
            reason="Control: current price, used as the comparison point."),
        Arm(arm_id="annual_discount", name="Annual, 2 months free", price=round(p * 10, 2),
            currency=c, interval="YEAR",
            reason="Tests whether a ~17% annual discount lifts annual-plan uptake."),
        Arm(arm_id="monthly_trial", name="Monthly with 7-day trial", price=p, currency=c,
            trial_days=7, reason="Tests whether a short trial raises conversion at the same price."),
        Arm(arm_id="premium_monthly", name="Premium monthly", price=round(p * 1.3, 2), currency=c,
            reason="Tests price sensitivity with a 30% higher monthly price."),
    ]
    return arms[:req.num_arms]


def _gemini(req: GoalRequest, mem: Memory) -> list[Arm]:
    prompt = (SYSTEM_PROMPT.format(n=req.num_arms, currency=req.currency)
              + f"\n\nCurrent monthly price: {req.current_price} {req.currency}."
              + f"\nMerchant goal: {req.goal}")
    past = mem.summary()
    if past:
        prompt += ("\n\nResults of this merchant's past experiments (avoid re-testing arms that "
                   "were stopped; build on winners):\n" + past)
    text = llm.generate(prompt, json_mode=True, temperature=0.4)
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("arms", [])
    arms = [Arm(currency=req.currency, **{k: v for k, v in a.items() if k != "currency"})
            for a in data]
    if not 2 <= len(arms) <= 4:
        raise ValueError(f"expected 2-4 arms, got {len(arms)}")
    if not any(a.is_baseline for a in arms):
        arms[0].is_baseline = True
    return arms


def generate_arms(req: GoalRequest, mem: Memory | None = None) -> tuple[list[Arm], str]:
    """Returns (arms, source) where source is 'gemini' or 'fallback'."""
    mem = mem if mem is not None else default_memory
    if llm.enabled():
        try:
            return _gemini(req, mem), "gemini"
        except Exception:  # network, overload, bad JSON, bad schema -> degrade gracefully
            pass
    return adapt_fallback(fallback_arms(req.model_copy(update={"num_arms": 4})), mem)[:req.num_arms], "fallback"
