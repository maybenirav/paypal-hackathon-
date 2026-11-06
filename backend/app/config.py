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
