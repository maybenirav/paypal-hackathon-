"""PayPal-related API routes (Person A). Included from main.py."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import config
from app import paypal_sdk
from app.paypal_client import get_access_token, PayPalError

router = APIRouter(prefix="/paypal", tags=["paypal"])


@router.get("/health")
def paypal_health():
    try:
        get_access_token()
        return {
            "paypal": "connected",
            "env": config.PAYPAL_ENV,
            "product_id_set": bool(config.PAYPAL_PRODUCT_ID),
            "sdk": paypal_sdk.SDK_NAME,
        }
    except PayPalError as e:
        return {"paypal": "error", "detail": str(e)}


class CancelRequest(BaseModel):
    reason: str = "Cancelled from PlanLab"


@router.post("/subscriptions/{subscription_id}/cancel")
def cancel_subscription(subscription_id: str, req: CancelRequest | None = None):
    """Cancel a PayPal subscription. PayPal then sends BILLING.SUBSCRIPTION.CANCELLED to our webhook."""
    reason = (req.reason if req else "Cancelled from PlanLab")[:128]
    try:
        paypal_sdk.cancel(subscription_id, reason)
    except PayPalError as e:
        raise HTTPException(502, f"PayPal error {e.status}: {e.body[:500]}") from e
    return {"subscription_id": subscription_id, "status": "cancel requested"}
