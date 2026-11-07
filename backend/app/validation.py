"""Validate generated arms against PayPal's Create Plan request shape.

Arms are converted to the exact body for POST /v1/billing/plans and checked
against a JSON Schema derived from PayPal's OpenAPI spec (the fields we use).
If APIMATIC_API_KEY is set, the payload is additionally sent to the APIMatic
validation endpoint (see `validate_with_apimatic`).
"""
from __future__ import annotations

import os

import httpx
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
        "description": {"type": "string", "minLength": 1, "maxLength": 127},
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


def validate_arm(arm: Arm, product_id: str = "PRODUCT_ID_PLACEHOLDER") -> ArmValidation:
    # The same builder that publishes real plans, so what passes here is what PayPal receives
    payload = build_plan_payload(arm, product_id, "EXPERIMENT")
    errors = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
              for e in _validator.iter_errors(payload)]
    return ArmValidation(arm_id=arm.arm_id, valid=not errors, errors=errors,
                         paypal_payload=payload)


def validate_arms(arms: list[Arm]) -> list[ArmValidation]:
    results = [validate_arm(a) for a in arms]
    ids = [a.arm_id for a in arms]
    if len(set(ids)) != len(ids):
        for r in results:
            r.valid = False
            r.errors.append("duplicate arm_id")
    for r in results:
        if r.valid:
            remote = validate_with_apimatic(r.paypal_payload)
            if remote:
                r.valid = False
                r.errors.extend(remote)
    return results


def validate_with_apimatic(payload: dict, timeout: float = 15.0) -> list[str]:
    """Optional remote check. Returns error strings; empty list means OK/skipped.

    Endpoint and auth are configured via env vars so the team can point this at
    whichever APIMatic validation endpoint the hackathon account provides.
    """
    key = os.getenv("APIMATIC_API_KEY")
    url = os.getenv("APIMATIC_VALIDATE_URL")
    if not key or not url:
        return []
    try:
        r = httpx.post(url, json=payload, headers={"Authorization": f"X-Auth-Key {key}"},
                       timeout=timeout)
        if r.status_code >= 400:
            return [f"APIMatic: HTTP {r.status_code} {r.text[:200]}"]
    except httpx.HTTPError as exc:
        return [f"APIMatic unreachable: {exc}"]
    return []
