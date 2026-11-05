"""Shared event format: one shape for real PayPal webhooks and simulated events."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal["VISIT", "SIGNUP", "PAYMENT", "CANCELLED", "DENIED",
                    "PAYMENT_FAILED", "REFUND", "DISPUTE", "CANCEL_INTENT"]

# How real PayPal webhook event_types map onto our shared types.
PAYPAL_EVENT_MAP: dict[str, EventType] = {
    "BILLING.SUBSCRIPTION.ACTIVATED": "SIGNUP",
    "PAYMENT.SALE.COMPLETED": "PAYMENT",
    "BILLING.SUBSCRIPTION.CANCELLED": "CANCELLED",
    "PAYMENT.SALE.DENIED": "DENIED",
    "BILLING.SUBSCRIPTION.PAYMENT.FAILED": "PAYMENT_FAILED",
    "PAYMENT.SALE.REFUNDED": "REFUND",
    "CUSTOMER.DISPUTE.CREATED": "DISPUTE",
}


class Event(BaseModel):
    experiment_id: str
    arm_id: str
    subscriber_id: str
    type: EventType
    ts: datetime
    amount: float = Field(default=0, ge=0)
    source: Literal["real", "simulated"] = "real"
