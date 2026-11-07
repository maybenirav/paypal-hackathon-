"""Save Agent: intervene on failed payments / cancel intent within merchant-set rules.

Offers: pause, downgrade, discount, or let go. Every decision is logged as an auditable
action the merchant can override. PayPal calls go through an injectable executor; the
default is a dry run, so nothing moves money until Person A plugs in the real client.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Literal

from pydantic import BaseModel, Field

from .events import utcnow

OfferKind = Literal["pause", "downgrade", "discount", "let_go"]
Trigger = Literal["PAYMENT_FAILED", "CANCEL_INTENT"]


class SaveRules(BaseModel):
    allow_pause: bool = True
    allow_downgrade: bool = True
    allow_discount: bool = True
    max_discount_pct: int = Field(default=20, ge=1, le=50)
    discount_cycles: int = Field(default=3, ge=1, le=12)
    pause_days: int = Field(default=30, ge=1, le=90)
    max_saves_per_subscriber: int = Field(default=1, ge=0, le=5)
    downgrade_plan_id: str | None = None   # cheaper plan to move subscribers to


class SubscriberContext(BaseModel):
    experiment_id: str | None = None   # subscriber ids only need to be unique within an experiment
    subscriber_id: str
    subscription_id: str
    plan_id: str = "PLAN_ID"
    arm_id: str | None = None
    trigger: Trigger
    tenure_days: int = 0
    payments_made: int = 0
    failed_payments: int = 0
    previous_saves: int = 0
    price: float = 0
    currency: str = "USD"
    reason_hint: Literal["price", "not_using", "card", "unknown"] = "unknown"


class Offer(BaseModel):
    kind: OfferKind
    rationale: str
    paypal_request: dict   # {method, path, body, note?}


def _discount_request(ctx: SubscriberContext, rules: SaveRules) -> dict:
    new_price = round(ctx.price * (1 - rules.max_discount_pct / 100), 2)
    return {
        "method": "POST", "path": f"/v1/billing/subscriptions/{ctx.subscription_id}/revise",
        "body": {"plan_id": ctx.plan_id, "plan": {"billing_cycles": [{
            "sequence": 1, "total_cycles": rules.discount_cycles,
            "pricing_scheme": {"fixed_price": {"value": f"{new_price:.2f}",
                                               "currency_code": ctx.currency}}}]}},
        "note": "Plan override for the first N cycles; confirm the plan allows overrides in sandbox."}


def _build(kind: OfferKind, ctx: SubscriberContext, rules: SaveRules) -> Offer:
    if kind == "pause":
        payments = f"{ctx.payments_made} successful payment{'' if ctx.payments_made == 1 else 's'}"
        return Offer(kind=kind, rationale=(
            f"Pause for {rules.pause_days} days: {ctx.tenure_days}-day customer with "
            f"{payments} is worth keeping through a temporary gap."),
            paypal_request={"method": "POST",
                            "path": f"/v1/billing/subscriptions/{ctx.subscription_id}/suspend",
                            "body": {"reason": "Merchant-offered pause"},
                            "note": f"Worker reactivates after {rules.pause_days} days "
                                    f"(POST .../activate); PayPal has no timed pause."})
    if kind == "downgrade":
        return Offer(kind=kind, rationale="Move to the cheaper plan instead of losing the subscriber.",
                     paypal_request={"method": "POST",
                                     "path": f"/v1/billing/subscriptions/{ctx.subscription_id}/revise",
                                     "body": {"plan_id": rules.downgrade_plan_id}})
    if kind == "discount":
        why = ("the subscriber signalled price sensitivity" if ctx.reason_hint == "price"
               else "a smaller bill may keep a subscriber who is about to leave")
        return Offer(kind=kind, rationale=f"{rules.max_discount_pct}% off the next {rules.discount_cycles} "
                                          f"cycles: {why}.", paypal_request=_discount_request(ctx, rules))
    if ctx.trigger == "PAYMENT_FAILED":
        # Giving up on an offer must never cancel someone: PayPal keeps retrying the payment by itself
        request = {"method": "NONE", "path": "", "note": "No PayPal call. PayPal keeps retrying the payment."}
    else:
        request = {"method": "POST", "path": f"/v1/billing/subscriptions/{ctx.subscription_id}/cancel",
                   "body": {"reason": "Subscriber chose to leave"}}
    return Offer(kind="let_go", rationale="No offer within the merchant's rules is worth making.",
                 paypal_request=request)


def choose_offer(ctx: SubscriberContext, rules: SaveRules) -> Offer:
    """Deterministic policy. Preference order depends on why the subscriber is leaving."""
    if ctx.previous_saves >= rules.max_saves_per_subscriber or ctx.payments_made == 0:
        reason = ("Save limit reached" if ctx.previous_saves >= rules.max_saves_per_subscriber
                  else "Never paid successfully")
        o = _build("let_go", ctx, rules)
        o.rationale = f"{reason}; no further offer."
        return o
    if ctx.reason_hint == "price":
        order = ["discount", "downgrade", "pause"]
    elif ctx.reason_hint in ("not_using", "card") or ctx.trigger == "PAYMENT_FAILED":
        order = ["pause", "downgrade", "discount"]
    else:
        order = ["downgrade", "discount", "pause"]
    allowed = {
        "pause": rules.allow_pause,
        "downgrade": rules.allow_downgrade and bool(rules.downgrade_plan_id),
        "discount": rules.allow_discount and ctx.price > 0,
    }
    for kind in order:
        if allowed[kind]:
            return _build(kind, ctx, rules)  # type: ignore[arg-type]
    return _build("let_go", ctx, rules)


Executor = Callable[[dict], dict]


def dry_run_executor(request: dict) -> dict:
    return {"status": "dry_run", "request": request}


@dataclass
class SaveAction:
    id: str
    ts: datetime
    context: SubscriberContext
    offer: Offer
    status: str = "dry_run"            # executed | dry_run | overridden | failed
    result: dict = field(default_factory=dict)
    overridden_by: str | None = None
    override_note: str = ""


class SaveLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rules = SaveRules()
        self.actions: list[SaveAction] = []
        self.executor: Executor = dry_run_executor

    def _execute(self, request: dict) -> tuple[dict, str]:
        if request.get("method") == "NONE":
            return {"status": "no_call"}, "no_call"
        try:
            result = self.executor(request)
            return result, result.get("status", "executed")
        except Exception as exc:  # never lose the audit row
            return {"error": str(exc)}, "failed"

    def handle(self, ctx: SubscriberContext, now: datetime | None = None) -> SaveAction:
        previous = self.saves_for(ctx.experiment_id, ctx.subscriber_id)
        ctx = ctx.model_copy(update={"previous_saves": max(ctx.previous_saves, previous)})
        offer = choose_offer(ctx, self.rules)
        result, status = self._execute(offer.paypal_request)
        action = SaveAction(uuid.uuid4().hex[:8], now or utcnow(), ctx, offer, status, result)
        with self._lock:
            self.actions.append(action)
        return action

    def saves_for(self, experiment_id: str | None, subscriber_id: str) -> int:
        return sum(1 for a in self.actions if a.context.experiment_id == experiment_id
                   and a.context.subscriber_id == subscriber_id
                   and a.offer.kind != "let_go" and a.status != "overridden")

    def handled(self, experiment_id: str | None, subscriber_id: str, trigger: str) -> bool:
        return any(a.context.experiment_id == experiment_id and a.context.subscriber_id == subscriber_id
                   and a.context.trigger == trigger for a in self.actions)

    def forget(self, experiment_id: str) -> None:
        with self._lock:
            self.actions = [a for a in self.actions if a.context.experiment_id != experiment_id]

    def override(self, action_id: str, kind: OfferKind, note: str = "") -> SaveAction:
        orig = next((a for a in self.actions if a.id == action_id), None)
        if orig is None:
            raise KeyError(action_id)
        if orig.status == "overridden":
            raise ValueError("action already overridden")
        offer = _build(kind, orig.context, self.rules)
        if kind == "downgrade" and not self.rules.downgrade_plan_id:
            raise ValueError("no downgrade_plan_id configured in rules")
        orig.status, orig.override_note = "overridden", note
        result, status = self._execute(offer.paypal_request)
        new = SaveAction(uuid.uuid4().hex[:8], utcnow(), orig.context, offer, status, result,
                         overridden_by=action_id, override_note=note)
        with self._lock:
            self.actions.append(new)
        return new

    def summary(self, actions: list[SaveAction] | None = None) -> dict:
        actions = self.actions if actions is None else actions
        saved = [a for a in actions if a.offer.kind != "let_go" and a.status != "overridden"]
        return {"total": len(actions), "saves": len(saved),
                "let_go": sum(a.offer.kind == "let_go" for a in actions),
                "overridden": sum(a.status == "overridden" for a in actions)}


save_log = SaveLog()
