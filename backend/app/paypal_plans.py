"""Turns an experiment arm (schemas.Arm) into a PayPal billing-plan request body."""
from __future__ import annotations

from app.schemas import Arm


def _money(value: float, currency: str) -> dict:
    return {"value": f"{value:.2f}", "currency_code": currency}


def build_plan_payload(arm: Arm, product_id: str, exp_id: str) -> dict:
    cycles = []
    sequence = 1

    # Free trial: a TRIAL cycle with no pricing_scheme means it costs nothing
    if arm.trial_days > 0:
        cycles.append({
            "frequency": {"interval_unit": "DAY", "interval_count": arm.trial_days},
            "tenure_type": "TRIAL",
            "sequence": sequence,
            "total_cycles": 1,
        })
        sequence += 1

    # Regular billing, repeating forever (total_cycles = 0)
    cycles.append({
        "frequency": {"interval_unit": arm.interval, "interval_count": 1},
        "tenure_type": "REGULAR",
        "sequence": sequence,
        "total_cycles": 0,
        "pricing_scheme": {"fixed_price": _money(arm.price, arm.currency)},
    })

    prefs: dict = {"auto_bill_outstanding": True, "payment_failure_threshold": 3}
    if arm.setup_fee > 0:
        prefs["setup_fee"] = _money(arm.setup_fee, arm.currency)
        prefs["setup_fee_failure_action"] = "CONTINUE"

    return {
        "product_id": product_id,
        "name": f"PlanLab {exp_id} - {arm.name}"[:127],
        "description": (arm.reason or f"Experiment arm {arm.arm_id}")[:127],
        "status": "ACTIVE",
        "billing_cycles": cycles,
        "payment_preferences": prefs,
    }


def build_discount_plan_payload(base: Arm, pct: int, cycles: int, product_id: str) -> dict:
    """Save Agent discount plan: `cycles` cycles at (100 - pct)% of the price, then full price.

    PayPal allows only one REGULAR cycle per plan, so the discounted months are a paid TRIAL
    cycle (PayPal's "discounted trial"), followed by the normal REGULAR cycle.
    """
    discounted = round(base.price * (1 - pct / 100), 2)
    payload = build_plan_payload(base.model_copy(update={
        "name": f"{base.name} - save offer {pct}% off x{cycles}", "trial_days": 0,
        "setup_fee": 0, "reason": f"Save Agent: {pct}% off for {cycles} cycles"}),
        product_id, "save")
    payload["billing_cycles"] = [
        {"frequency": {"interval_unit": base.interval, "interval_count": 1},
         "tenure_type": "TRIAL", "sequence": 1, "total_cycles": cycles,
         "pricing_scheme": {"fixed_price": _money(discounted, base.currency)}},
        {**payload["billing_cycles"][-1], "sequence": 2},
    ]
    return payload
