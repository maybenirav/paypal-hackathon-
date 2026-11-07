"""Request checks that apply to every route.

Browsers send some cross-site POSTs without asking first, and CORS only stops the attacker reading the
answer. So state-changing requests from another website's page are refused here, before they run.
"""
from __future__ import annotations

import os
import re
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from . import config

LOCAL_ORIGIN = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
_local = re.compile(LOCAL_ORIGIN)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Health checks and PayPal's signed webhooks can't send our token. The docs reveal only the API's shape.
OPEN_PATHS = {"/health", "/webhooks/paypal", "/docs", "/openapi.json", "/redoc"}

API_TOKEN = os.getenv("PLANLAB_API_TOKEN", "")


def allowed_origins() -> list[str]:
    extra = [o.strip().rstrip("/") for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    return [config.FRONTEND_URL.rstrip("/"), *extra]


def origin_allowed(origin: str) -> bool:
    return bool(_local.match(origin)) or origin.rstrip("/") in allowed_origins()


async def guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and request.method not in SAFE_METHODS and not origin_allowed(origin):
        return JSONResponse({"detail": "Requests from this website aren't allowed."}, status_code=403)
    if API_TOKEN and request.method != "OPTIONS" and request.url.path not in OPEN_PATHS:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(supplied, API_TOKEN):
            return JSONResponse({"detail": "Missing or wrong API token."}, status_code=401)
    return await call_next(request)
