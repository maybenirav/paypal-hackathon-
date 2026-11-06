"""Loads settings from .env (backend/.env first, then the repo root .env)."""
import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

for env_path in (BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env"):
    if env_path.exists():
        load_dotenv(env_path)
        break

PAYPAL_ENV = os.getenv("PAYPAL_ENV") or "sandbox"
PAYPAL_API_BASE = os.getenv("PAYPAL_API_BASE") or "https://api-m.sandbox.paypal.com"
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_PRODUCT_ID = os.getenv("PAYPAL_PRODUCT_ID", "")

FRONTEND_URL = os.getenv("FRONTEND_URL") or "http://localhost:5173"

# Where PayPal sends the buyer after approving / cancelling a subscription
PAYPAL_RETURN_URL = os.getenv("PAYPAL_RETURN_URL") or f"{FRONTEND_URL}/subscribe/success"
PAYPAL_CANCEL_URL = os.getenv("PAYPAL_CANCEL_URL") or f"{FRONTEND_URL}/subscribe/cancel"
PAYPAL_BRAND_NAME = os.getenv("PAYPAL_BRAND_NAME") or "PlanLab Demo"

# Webhooks: the ID shown in the PayPal dashboard after adding the webhook URL.
# Set PAYPAL_WEBHOOK_VERIFY=false ONLY for local testing with hand-made events.
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "")
PAYPAL_WEBHOOK_VERIFY = (os.getenv("PAYPAL_WEBHOOK_VERIFY") or "true").lower() != "false"
