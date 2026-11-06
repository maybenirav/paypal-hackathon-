"""PayPal-related API routes (Person A). Included from main.py."""
from fastapi import APIRouter

from app import config
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
        }
    except PayPalError as e:
        return {"paypal": "error", "detail": str(e)}
