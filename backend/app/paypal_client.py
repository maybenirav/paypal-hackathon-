"""Small PayPal REST client: gets/caches an access token and sends requests."""
import time
import uuid
import httpx
from app import config


class PayPalError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"PayPal API error {status}: {body}")
        self.status = status
        self.body = body


_token = {"value": None, "expires_at": 0.0}


def get_access_token() -> str:
    # Reuse the token until 60 seconds before it expires
    if _token["value"] and time.time() < _token["expires_at"] - 60:
        return _token["value"]

    if not config.PAYPAL_CLIENT_ID or not config.PAYPAL_CLIENT_SECRET:
        raise PayPalError(0, "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET missing in .env")

    try:
        r = httpx.post(
            f"{config.PAYPAL_API_BASE}/v1/oauth2/token",
            auth=(config.PAYPAL_CLIENT_ID, config.PAYPAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise PayPalError(0, f"Can't reach PayPal: {type(e).__name__}") from e
    if r.status_code != 200:
        raise PayPalError(r.status_code, r.text)

    data = r.json()
    _token["value"] = data["access_token"]
    _token["expires_at"] = time.time() + data.get("expires_in", 3000)
    return _token["value"]


def paypal_request(method: str, path: str, json=None, params=None) -> dict:
    headers = {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    # The same request id on a retry tells PayPal it's a repeat, so it can't create a duplicate
    if method.upper() == "POST":
        headers["PayPal-Request-Id"] = str(uuid.uuid4())

    for attempt in range(2):
        try:
            r = httpx.request(
                method,
                f"{config.PAYPAL_API_BASE}{path}",
                headers=headers,
                json=json,
                params=params,
                timeout=30,
            )
        except httpx.HTTPError as e:
            raise PayPalError(0, f"Can't reach PayPal: {type(e).__name__}") from e
        if r.status_code != 401 or attempt == 1:
            break
        # The cached token was revoked or expired early: get a fresh one and try once more
        _token["value"] = None
        headers["Authorization"] = f"Bearer {get_access_token()}"
    if r.status_code >= 400:
        raise PayPalError(r.status_code, r.text)
    return r.json() if r.content else {}
