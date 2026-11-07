"""Pricing agent: merchant goal -> 2-4 experiment arms (Gemini, with offline fallback)."""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

from ..memory import Memory, adapt_fallback, memory as default_memory
from ..schemas import Arm, GoalRequest

log = logging.getLogger("planlab.agent")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.8-flash"
MIN_PRICE_RATIO, MAX_PRICE_RATIO = 0.5, 3.0
ARM_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,40}$")

SYSTEM_PROMPT = """You design pricing experiments for a PayPal-billed subscription business.
Return ONLY a JSON array of {n} arms. The first arm MUST be the baseline at the current monthly price
(is_baseline=true, interval MONTH). Each arm: {{"arm_id": "short_snake_id", "name": str, "price": number,
"interval": "MONTH"|"YEAR", "trial_days": int 0-90, "setup_fee": number, "is_baseline": bool,
"reason": one sentence on why this arm tests the merchant's goal}}.
Prices are per interval, in {currency}. Keep prices realistic (within 0.5x-3x of the current price
per month-equivalent). Vary one or two levers per arm so results are interpretable."""


def gemini_generate(prompt: str, key: str, as_json: bool = False) -> str:
    config = {"responseMimeType": "application/json", "temperature": 0.4} if as_json else {}
    # The key goes in a header because URLs end up in logs and error messages
    r = httpx.post(GEMINI_URL.format(model=os.getenv("GEMINI_MODEL") or DEFAULT_MODEL),
                   headers={"x-goog-api-key": key}, timeout=30,
                   json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config})
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


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


def check_arms(arms: list[Arm], req: GoalRequest) -> list[Arm]:
    """AI output has to be usable as real PayPal plans, so anything off is rejected, not guessed at."""
    if not 2 <= len(arms) <= 4:
        raise ValueError(f"expected 2-4 arms, got {len(arms)}")
    ids = [a.arm_id for a in arms]
    if len(set(ids)) != len(ids) or not all(ARM_ID_PATTERN.match(i) for i in ids):
        raise ValueError("arm ids must be unique snake_case")
    baselines = [a for a in arms if a.is_baseline]
    if len(baselines) > 1:
        raise ValueError("more than one baseline")
    if not baselines:
        arms[0].is_baseline = True
        baselines = [arms[0]]
    if baselines[0].interval != "MONTH" or abs(baselines[0].price - req.current_price) > 0.005:
        raise ValueError("baseline must be the current monthly price")
    for arm in arms:
        ratio = arm.monthly_equivalent / req.current_price
        if not MIN_PRICE_RATIO <= ratio <= MAX_PRICE_RATIO:
            raise ValueError(f"{arm.arm_id} is priced {ratio:.1f}x the current price")
    return arms


def _gemini(req: GoalRequest, key: str, mem: Memory) -> list[Arm]:
    prompt = (SYSTEM_PROMPT.format(n=req.num_arms, currency=req.currency)
              + f"\n\nCurrent monthly price: {req.current_price} {req.currency}."
              + f"\nMerchant goal: {req.goal}")
    past = mem.summary()
    if past:
        prompt += ("\n\nResults of this merchant's past experiments. Re-test what won and avoid what "
                   f"was stopped:\n{past}")
    data = json.loads(gemini_generate(prompt, key, as_json=True))
    if isinstance(data, dict):
        data = data.get("arms", [])
    arms = [Arm(currency=req.currency, **{k: v for k, v in a.items() if k != "currency"}) for a in data]
    return check_arms(arms, req)


def generate_arms(req: GoalRequest, mem: Memory | None = None) -> tuple[list[Arm], str]:
    """Returns (arms, source) where source is 'gemini' or 'fallback'."""
    mem = mem if mem is not None else default_memory
    key = os.getenv("GEMINI_API_KEY")
    if key:
        try:
            return _gemini(req, key, mem), "gemini"
        except Exception as exc:  # network, bad JSON, bad schema -> degrade gracefully, but say why
            log.warning("Gemini arms unavailable, using templates: %s: %s", type(exc).__name__, exc)
    return adapt_fallback(fallback_arms(req.model_copy(update={"num_arms": 4})), mem)[:req.num_arms], "fallback"
