"""Check AI-generated arms before anything reaches PayPal.

Three layers, all visible to the merchant:
1. Shape: each arm is turned into the exact POST /v1/billing/plans body that will be sent
   (`paypal_plans.build_plan_payload`, the same builder publishing uses) and checked against a
   JSON Schema taken from PayPal's Subscriptions OpenAPI spec.
2. Merchant bounds: price within 0.5x-3x of the baseline per month, and no arm that merely
   copies the baseline. The agent is told these limits; this is where they are enforced.
3. PayPal rollout constraint: a winning price can only be applied to an existing plan (and so
   to existing subscribers) when interval, trial and setup fee match the baseline. Other arms
   are allowed but labelled "new sign-ups only", so nobody is surprised at rollout time.
"""
from __future__ import annotations

from jsonschema import Draft7Validator

from .paypal_plans import build_plan_payload
from .schemas import Arm, ArmValidation

_MONEY = {
    "type": "object",
    "required": ["value", "currency_code"],
    "properties": {
        "value": {"type": "string", "pattern": r"^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$"},
        "currency_code": {"type": "string", "minLength": 3, "maxLength": 3},
    },
}

PLAN_SCHEMA = {
    "type": "object",
    "required": ["product_id", "name", "billing_cycles", "payment_preferences"],
    "properties": {
        "product_id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1, "maxLength": 127},
        "status": {"enum": ["CREATED", "INACTIVE", "ACTIVE"]},
        "billing_cycles": {
            "type": "array", "minItems": 1, "maxItems": 12,
            "items": {
                "type": "object",
                "required": ["frequency", "tenure_type", "sequence", "total_cycles"],
                "properties": {
                    "frequency": {
                        "type": "object",
                        "required": ["interval_unit"],
                        "properties": {
                            "interval_unit": {"enum": ["DAY", "WEEK", "MONTH", "YEAR"]},
                            "interval_count": {"type": "integer", "minimum": 1, "maximum": 365},
                        },
                    },
                    "tenure_type": {"enum": ["REGULAR", "TRIAL"]},
                    "sequence": {"type": "integer", "minimum": 1, "maximum": 99},
                    "total_cycles": {"type": "integer", "minimum": 0, "maximum": 999},
                    "pricing_scheme": {
                        "type": "object",
                        "required": ["fixed_price"],
                        "properties": {"fixed_price": _MONEY},
                    },
                },
            },
        },
        "payment_preferences": {
            "type": "object",
            "properties": {
                "auto_bill_outstanding": {"type": "boolean"},
                "setup_fee": _MONEY,
                "setup_fee_failure_action": {"enum": ["CONTINUE", "CANCEL"]},
                "payment_failure_threshold": {"type": "integer", "minimum": 0, "maximum": 999},
            },
        },
    },
}

_validator = Draft7Validator(PLAN_SCHEMA)


PRICE_BOUNDS = (0.5, 3.0)


def validate_arm(arm: Arm, product_id: str = "PRODUCT_ID_PLACEHOLDER") -> ArmValidation:
    payload = build_plan_payload(arm, product_id, "preview")
    errors = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
              for e in _validator.iter_errors(payload)]
    return ArmValidation(arm_id=arm.arm_id, valid=not errors, errors=errors,
                         paypal_payload=payload)


def rollout_mode(arm: Arm, base: Arm) -> str:
    same = (arm.interval == base.interval and arm.trial_days == base.trial_days
            and arm.setup_fee == base.setup_fee and arm.currency == base.currency)
    return "in_place" if same else "new_signups_only"


def validate_arms(arms: list[Arm]) -> list[ArmValidation]:
    results = [validate_arm(a) for a in arms]
    ids = [a.arm_id for a in arms]
    base = next((a for a in arms if a.is_baseline), arms[0] if arms else None)
    for arm, r in zip(arms, results):
        if ids.count(arm.arm_id) > 1:
            r.errors.append("duplicate arm_id")
        if base is None or arm is base:
            r.rollout = "baseline"
            continue
        ratio = arm.monthly_equivalent / base.monthly_equivalent
        if not PRICE_BOUNDS[0] <= ratio <= PRICE_BOUNDS[1]:
            r.errors.append(f"price is {ratio:.2f}x the baseline per month; allowed range is "
                            f"{PRICE_BOUNDS[0]}x to {PRICE_BOUNDS[1]}x")
        if (arm.price, arm.interval, arm.trial_days, arm.setup_fee) == (
                base.price, base.interval, base.trial_days, base.setup_fee):
            r.errors.append("identical to the baseline, so it tests nothing")
        r.rollout = rollout_mode(arm, base)
        if r.rollout == "new_signups_only":
            r.warnings.append("Changes interval, trial or setup fee: if it wins, PayPal can only "
                              "offer it to new sign-ups; existing subscribers keep their plan.")
        r.valid = not r.errors
    return results
