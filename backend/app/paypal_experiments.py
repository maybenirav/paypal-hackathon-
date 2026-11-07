"""Person A: connects experiments to real PayPal plans, subscriptions and webhooks.

Endpoints
  POST /experiments/{id}/paypal/publish                 create a PayPal plan per arm
  GET  /experiments/{id}/paypal/plans                   arm -> plan_id mapping
  POST /experiments/{id}/paypal/subscribe               pick an arm, create a subscription
  GET  /experiments/{id}/paypal/subscriptions           subscriptions created for this experiment
  POST /experiments/{id}/paypal/sync/{subscription_id}  pull status from PayPal (webhook backup)
  POST /webhooks/paypal                                 receive PayPal webhooks
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import config
from app.events import PAYPAL_EVENT_MAP, Event, utcnow
from app.experiments import pick_arm
from app.paypal_client import PayPalError, paypal_request
from app.paypal_plans import build_plan_payload
from app.store import Experiment, store

log = logging.getLogger("planlab.paypal")

router = APIRouter(prefix="/experiments", tags=["paypal"])
webhook_router = APIRouter(tags=["paypal-webhooks"])

# Event types a subscriber can only have once (PAYMENT can repeat every cycle)
ONE_TIME_TYPES = {"VISIT", "SIGNUP", "CANCELLED"}
ENDED_STATUSES = {"CANCELLED", "EXPIRED"}


# ---------- helpers ----------

def _exp(exp_id: str) -> Experiment:
    exp = store.get(exp_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    return exp


def _bad_gateway(e: PayPalError) -> HTTPException:
    return HTTPException(502, f"PayPal error {e.status}: {e.body[:500]}")


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return utcnow()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return utcnow()


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


def _set_status(exp: Experiment, subscription_id: str, status: str | None) -> None:
    record = exp.subscriptions.get(subscription_id)
    if record and status:
        record["status"] = status


def _resolve(sub_id: str | None, custom: str | None) -> tuple[str, str] | None:
    """Find which experiment and arm a subscription belongs to."""
    found = _parse_custom(custom)
    if found or not sub_id:
        return found
    known = store.find_subscription(sub_id)
    if known:
        return known[0].id, known[1]["arm_id"]
    try:
        sub = paypal_request("GET", f"/v1/billing/subscriptions/{sub_id}")
    except PayPalError:
        return None
    return _parse_custom(sub.get("custom_id"))


# ---------- Stage 2: plans ----------

@router.post("/{exp_id}/paypal/publish")
def publish_plans(exp_id: str):
    """Create one real PayPal billing plan per arm and save its plan_id on the arm."""
    exp = _exp(exp_id)
    if not config.PAYPAL_PRODUCT_ID:
        raise HTTPException(409, "PAYPAL_PRODUCT_ID missing in .env - run scripts/create_product.py first")
    invalid = [v["arm_id"] for v in exp.validation if not v["valid"]]
    if invalid:
        raise HTTPException(422, f"fix these plans before publishing: {', '.join(invalid)}")

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
    arm_id: str | None = None  # force an arm for testing; normally chosen by the traffic split
    visitor_id: str | None = Field(None, max_length=128)  # the same visitor keeps the same offer


@router.post("/{exp_id}/paypal/subscribe")
def subscribe(exp_id: str, req: SubscribeRequest | None = None):
    """Assign a new sign-up to an arm and create a PayPal subscription on that arm's plan."""
    exp = _exp(exp_id)
    visitor_id = req.visitor_id if req else None
    returning = bool(visitor_id and visitor_id in exp.assignments)

    if req and req.arm_id:
        arm_id = req.arm_id
    else:
        arm_id = pick_arm(exp, visitor_id)[0]

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
    approve_url = next((link["href"] for link in sub.get("links", [])
                        if link.get("rel") == "approve"), None)
    record = {"subscription_id": sub_id, "arm_id": arm.arm_id, "plan_id": arm.plan_id,
              "approve_url": approve_url, "status": sub.get("status"), "visitor_id": visitor_id,
              "created_at": utcnow().isoformat()}
    exp.subscriptions[sub_id] = record
    if visitor_id:
        exp.assignments.setdefault(visitor_id, arm.arm_id)

    # Reaching checkout counts as a visit for this arm's conversion rate, once per person
    if not returning:
        _add_once(exp, Event(experiment_id=exp.id, arm_id=arm.arm_id, subscriber_id=sub_id,
                             type="VISIT", ts=utcnow(), source="real"))
    return record


@router.get("/{exp_id}/paypal/subscriptions")
def list_subscriptions(exp_id: str):
    exp = _exp(exp_id)
    return {"subscriptions": list(reversed(list(exp.subscriptions.values())))}


@router.post("/{exp_id}/paypal/sync/{subscription_id}")
def sync_subscription(exp_id: str, subscription_id: str):
    """Backup for webhooks: read the subscription from PayPal and record what changed."""
    exp = _exp(exp_id)
    try:
        sub = paypal_request("GET", f"/v1/billing/subscriptions/{subscription_id}")
    except PayPalError as e:
        raise _bad_gateway(e) from e

    found = _parse_custom(sub.get("custom_id"))
    if not found:
        record = exp.subscriptions.get(subscription_id)
        found = (exp.id, record["arm_id"]) if record else None
    if not found or found[0] != exp.id:
        raise HTTPException(422, "subscription does not belong to this experiment")
    arm_id = found[1]
    status = sub.get("status")
    _set_status(exp, subscription_id, status)

    added = []
    if status in ("ACTIVE", "SUSPENDED", *ENDED_STATUSES):
        ts = _parse_ts(sub.get("start_time") or sub.get("create_time"))
        if _add_once(exp, Event(experiment_id=exp.id, arm_id=arm_id, subscriber_id=subscription_id,
                                type="SIGNUP", ts=ts, source="real")):
            added.append("SIGNUP")
    if status in ENDED_STATUSES:
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


def _subscription_of(event_type: str, res: dict) -> tuple[str | None, str | None]:
    """The subscription an event is about. Refunds and disputes only name a sale, so look that up."""
    if event_type.startswith("BILLING.SUBSCRIPTION."):
        return res.get("id"), res.get("custom_id")
    sale_ids: list[str | None] = []
    if event_type.startswith("PAYMENT.SALE."):
        if res.get("billing_agreement_id"):
            return res["billing_agreement_id"], res.get("custom")
        sale_ids = [res.get("sale_id"), res.get("id")]
        custom = res.get("custom")
    else:
        sale_ids = [t.get("seller_transaction_id") for t in res.get("disputed_transactions") or []]
        custom = None
    for sale_id in sale_ids:
        found = store.find_sale(sale_id) if sale_id else None
        if found:
            return found[1], custom
    return None, custom


def _handle(event: dict) -> dict:
    etype = event.get("event_type", "")
    our_type = PAYPAL_EVENT_MAP.get(etype)
    if our_type is None:
        return {"status": "ignored", "reason": f"unmapped event type {etype}"}

    res = event.get("resource") or {}
    sub_id, custom = _subscription_of(etype, res)
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

    ts = _parse_ts(event.get("create_time"))
    if our_type == "PAYMENT":
        if res.get("id"):
            exp.sales[res["id"]] = sub_id
        # A payment can arrive before the activation event, and it proves the sign-up happened
        _add_once(exp, Event(experiment_id=exp_id, arm_id=arm_id, subscriber_id=sub_id, type="SIGNUP",
                             ts=ts, source="real"))
    if our_type == "SIGNUP":
        _set_status(exp, sub_id, "ACTIVE")
    elif our_type == "CANCELLED":
        _set_status(exp, sub_id, "EXPIRED" if etype.endswith("EXPIRED") else "CANCELLED")
    ev = Event(experiment_id=exp_id, arm_id=arm_id, subscriber_id=sub_id, type=our_type,
               ts=ts, amount=amount, source="real")
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
    if not isinstance(event, dict):
        raise HTTPException(400, "expected a JSON object")

    event_id = event.get("id")
    if event_id and event_id in store.seen_webhooks:  # PayPal retries; ignore repeats
        return {"status": "duplicate"}

    if not await run_in_threadpool(_verify, dict(request.headers), event):
        raise HTTPException(400, "webhook signature verification failed")

    result = await run_in_threadpool(_handle, event)
    if event_id:
        store.mark_webhook(event_id)
    log.info("PayPal webhook %s -> %s", event.get("event_type"), result)
    return result
