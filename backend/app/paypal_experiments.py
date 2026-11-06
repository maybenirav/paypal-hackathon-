"""Person A: connects experiments to real PayPal plans, subscriptions and webhooks.

Endpoints
  POST /experiments/{id}/paypal/publish                 create a PayPal plan per arm
  GET  /experiments/{id}/paypal/plans                   arm -> plan_id mapping
  POST /experiments/{id}/paypal/subscribe               pick an arm, create a subscription
  POST /experiments/{id}/paypal/sync/{subscription_id}  pull status from PayPal (webhook backup)
  POST /webhooks/paypal                                 receive PayPal webhooks
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app import config, simulate
from app.events import PAYPAL_EVENT_MAP, Event
from app.paypal_client import PayPalError, paypal_request
from app.paypal_plans import build_plan_payload
from app.store import Experiment, store

log = logging.getLogger("planlab.paypal")

router = APIRouter(prefix="/experiments", tags=["paypal"])
webhook_router = APIRouter(tags=["paypal-webhooks"])

# subscription id -> (experiment id, arm id). In memory, like the experiment store.
_subs: dict[str, tuple[str, str]] = {}
_seen_webhooks: set[str] = set()

# Event types a subscriber can only have once (PAYMENT can repeat every cycle)
ONE_TIME_TYPES = {"VISIT", "SIGNUP", "CANCELLED"}


# ---------- helpers ----------

def _exp(exp_id: str) -> Experiment:
    exp = store.get(exp_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    return exp


def _bad_gateway(e: PayPalError) -> HTTPException:
    return HTTPException(502, f"PayPal error {e.status}: {e.body[:500]}")


def _to_store_tz(dt: datetime) -> datetime:
    """Match the simulator's timezone style so real and simulated events compare safely."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if getattr(simulate.START, "tzinfo", None) is None:
        return dt.replace(tzinfo=None)
    return dt


def _now() -> datetime:
    return _to_store_tz(datetime.now(timezone.utc))


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return _now()
    try:
        return _to_store_tz(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return _now()


def _add_once(exp: Experiment, ev: Event) -> bool:
    """Store the event unless it's a one-time type this subscriber already has."""
    if ev.type in ONE_TIME_TYPES and any(
        e.subscriber_id == ev.subscriber_id and e.type == ev.type for e in exp.events
    ):
        return False
    store.add_events(exp, [ev])
    return True


def _parse_custom(custom: str | None) -> tuple[str, str] | None:
    if custom and ":" in custom:
        exp_id, arm_id = custom.split(":", 1)
        return exp_id, arm_id
    return None


def _resolve(sub_id: str | None, custom: str | None) -> tuple[str, str] | None:
    """Find which experiment and arm a subscription belongs to."""
    found = _parse_custom(custom) or (_subs.get(sub_id) if sub_id else None)
    if found or not sub_id:
        return found
    try:
        sub = paypal_request("GET", f"/v1/billing/subscriptions/{sub_id}")
    except PayPalError:
        return None
    found = _parse_custom(sub.get("custom_id"))
    if found:
        _subs[sub_id] = found
    return found


# ---------- Stage 2: plans ----------

@router.post("/{exp_id}/paypal/publish")
def publish_plans(exp_id: str):
    """Create one real PayPal billing plan per arm and save its plan_id on the arm."""
    exp = _exp(exp_id)
    if not config.PAYPAL_PRODUCT_ID:
        raise HTTPException(409, "PAYPAL_PRODUCT_ID missing in .env - run scripts/create_product.py first")

    results = []
    for arm in exp.arms:
        if arm.plan_id:  # already published, safe to re-run
            results.append({"arm_id": arm.arm_id, "plan_id": arm.plan_id, "created": False})
            continue
        payload = build_plan_payload(arm, config.PAYPAL_PRODUCT_ID, exp.id)
        try:
            plan = paypal_request("POST", "/v1/billing/plans", json=payload)
        except PayPalError as e:
            raise _bad_gateway(e) from e
        arm.plan_id = plan["id"]
        results.append({"arm_id": arm.arm_id, "plan_id": arm.plan_id,
                        "created": True, "status": plan.get("status")})
    return {"experiment_id": exp.id, "plans": results}


@router.get("/{exp_id}/paypal/plans")
def list_plans(exp_id: str):
    exp = _exp(exp_id)
    return {"experiment_id": exp.id, "plans": [
        {"arm_id": a.arm_id, "name": a.name, "price": a.price, "currency": a.currency,
         "interval": a.interval, "trial_days": a.trial_days, "setup_fee": a.setup_fee,
         "is_baseline": a.is_baseline, "plan_id": a.plan_id}
        for a in exp.arms]}


# ---------- Stage 3: subscriptions ----------

class SubscribeRequest(BaseModel):
    arm_id: str | None = None  # force an arm for testing; normally chosen by Thompson allocation


@router.post("/{exp_id}/paypal/subscribe")
def subscribe(exp_id: str, req: SubscribeRequest | None = None):
    """Assign a new sign-up to an arm and create a PayPal subscription on that arm's plan."""
    exp = _exp(exp_id)

    if req and req.arm_id:
        arm_id = req.arm_id
    else:
        from app.main import assign  # imported here to avoid a circular import
        arm_id = assign(exp_id)["arm_id"]

    arm = next((a for a in exp.arms if a.arm_id == arm_id), None)
    if arm is None:
        raise HTTPException(404, f"unknown arm_id {arm_id}")
    if arm.arm_id in exp.stopped_arms:
        raise HTTPException(409, f"arm {arm.arm_id} has been stopped")
    if not arm.plan_id:
        raise HTTPException(409, "arm has no PayPal plan yet - call /paypal/publish first")

    body = {
        "plan_id": arm.plan_id,
        "custom_id": f"{exp.id}:{arm.arm_id}",  # lets webhooks find the experiment + arm
        "application_context": {
            "brand_name": config.PAYPAL_BRAND_NAME,
            "user_action": "SUBSCRIBE_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": config.PAYPAL_RETURN_URL,
            "cancel_url": config.PAYPAL_CANCEL_URL,
        },
    }
    try:
        sub = paypal_request("POST", "/v1/billing/subscriptions", json=body)
    except PayPalError as e:
        raise _bad_gateway(e) from e

    sub_id = sub["id"]
    _subs[sub_id] = (exp.id, arm.arm_id)
    approve_url = next((link["href"] for link in sub.get("links", [])
                        if link.get("rel") == "approve"), None)

    # Reaching checkout counts as a visit for this arm's conversion rate
    _add_once(exp, Event(experiment_id=exp.id, arm_id=arm.arm_id, subscriber_id=sub_id,
                         type="VISIT", ts=_now(), source="real"))

    return {"subscription_id": sub_id, "arm_id": arm.arm_id, "plan_id": arm.plan_id,
            "approve_url": approve_url, "status": sub.get("status")}


@router.post("/{exp_id}/paypal/sync/{subscription_id}")
def sync_subscription(exp_id: str, subscription_id: str):
    """Backup for webhooks: read the subscription from PayPal and record what changed."""
    exp = _exp(exp_id)
    try:
        sub = paypal_request("GET", f"/v1/billing/subscriptions/{subscription_id}")
    except PayPalError as e:
        raise _bad_gateway(e) from e

    found = _parse_custom(sub.get("custom_id")) or _subs.get(subscription_id)
    if not found or found[0] != exp.id:
        raise HTTPException(422, "subscription does not belong to this experiment")
    arm_id = found[1]
    status = sub.get("status")

    added = []
    if status in ("ACTIVE", "SUSPENDED", "CANCELLED", "EXPIRED"):
        ts = _parse_ts(sub.get("start_time") or sub.get("create_time"))
        if _add_once(exp, Event(experiment_id=exp.id, arm_id=arm_id, subscriber_id=subscription_id,
                                type="SIGNUP", ts=ts, source="real")):
            added.append("SIGNUP")
    if status == "CANCELLED":
        ts = _parse_ts(sub.get("status_update_time"))
        if _add_once(exp, Event(experiment_id=exp.id, arm_id=arm_id, subscriber_id=subscription_id,
                                type="CANCELLED", ts=ts, source="real")):
            added.append("CANCELLED")
    return {"subscription_id": subscription_id, "arm_id": arm_id, "status": status, "added": added}


# ---------- Stage 4: webhooks ----------

def _verify(headers: dict, event: dict) -> bool:
    if not config.PAYPAL_WEBHOOK_VERIFY:
        return True
    if not config.PAYPAL_WEBHOOK_ID:
        log.warning("PAYPAL_WEBHOOK_ID not set - rejecting webhook")
        return False
    body = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
        "webhook_id": config.PAYPAL_WEBHOOK_ID,
        "webhook_event": event,
    }
    try:
        result = paypal_request("POST", "/v1/notifications/verify-webhook-signature", json=body)
    except PayPalError as e:
        log.warning("Webhook verification call failed: %s", e)
        return False
    return result.get("verification_status") == "SUCCESS"


def _handle(event: dict) -> dict:
    etype = event.get("event_type", "")
    our_type = PAYPAL_EVENT_MAP.get(etype)
    if our_type is None:
        return {"status": "ignored", "reason": f"unmapped event type {etype}"}

    res = event.get("resource") or {}
    if etype.startswith("BILLING.SUBSCRIPTION."):
        sub_id, custom = res.get("id"), res.get("custom_id")
    elif etype.startswith("PAYMENT.SALE."):
        sub_id, custom = res.get("billing_agreement_id"), res.get("custom")
    else:  # e.g. disputes: the payload has no direct subscription link
        sub_id, custom = None, None

    found = _resolve(sub_id, custom)
    if not found or not sub_id:
        return {"status": "ignored", "reason": "could not link event to an experiment"}
    exp_id, arm_id = found
    exp = store.get(exp_id)
    if exp is None or arm_id not in {a.arm_id for a in exp.arms}:
        return {"status": "ignored", "reason": "experiment or arm no longer exists"}

    amount = 0.0
    if our_type in ("PAYMENT", "REFUND"):
        amt = res.get("amount") or {}
        try:
            amount = abs(float(amt.get("total") or amt.get("value") or 0))
        except (TypeError, ValueError):
            amount = 0.0

    ev = Event(experiment_id=exp_id, arm_id=arm_id, subscriber_id=sub_id, type=our_type,
               ts=_parse_ts(event.get("create_time")), amount=amount, source="real")
    stored = _add_once(exp, ev)
    return {"status": "stored" if stored else "duplicate",
            "experiment_id": exp_id, "arm_id": arm_id, "type": our_type}


@webhook_router.post("/webhooks/paypal")
async def paypal_webhook(request: Request):
    raw = await request.body()
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "invalid JSON")

    event_id = event.get("id")
    if event_id and event_id in _seen_webhooks:  # PayPal retries; ignore repeats
        return {"status": "duplicate"}

    if not await run_in_threadpool(_verify, dict(request.headers), event):
        raise HTTPException(400, "webhook signature verification failed")

    result = await run_in_threadpool(_handle, event)
    if event_id:
        _seen_webhooks.add(event_id)
    log.info("PayPal webhook %s -> %s", event.get("event_type"), result)
    return result
