"""Save Agent: intervene on failed payments and cancel intent, within merchant-set rules.

How it decides (AI understands, rules decide):
1. Understand: the customer's free-text cancel reason ("too pricey", "travelling for a month")
   is classified into price / not_using / card / unknown by Gemini, with a keyword fallback.
2. Decide: a deterministic policy picks pause, downgrade, discount or let go, only among the
   offers the merchant allowed, so every decision is explainable, testable and overridable.
3. Act: each offer is a real PayPal Subscriptions call, built only from what PayPal supports:
     pause     -> suspend now; the nightly sweep calls activate after `pause_days`
                  (PayPal has no timed pause, so we schedule the reactivation ourselves)
     downgrade -> revise to the merchant's cheaper plan
     discount  -> revise to a discount plan (N discounted cycles, then the normal price)
     let go    -> cancel
   Plan switches return a PayPal approval link: the customer confirms the change themselves.
4. Log: every action lands in an audit log the merchant can override.

Calls go through an injectable executor. Default is a dry run; `SAVE_AGENT_LIVE=true` sends
real requests through PayPal's APIMatic-generated SDK (`app.paypal_sdk.execute`). Simulated
subscribers are never sent to PayPal.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Literal

from pydantic import BaseModel, Field

from .agent import llm
from .local_db import db

OfferKind = Literal["pause", "downgrade", "discount", "let_go"]
Trigger = Literal["PAYMENT_FAILED", "CANCEL_INTENT"]
ReasonHint = Literal["price", "not_using", "card", "unknown"]


class SaveRules(BaseModel):
    allow_pause: bool = True
    allow_downgrade: bool = True
    allow_discount: bool = True
    max_discount_pct: int = Field(default=20, ge=1, le=50)
    discount_cycles: int = Field(default=3, ge=1, le=12)
    pause_days: int = Field(default=30, ge=1, le=90)
    max_saves_per_subscriber: int = Field(default=1, ge=0, le=5)
    downgrade_price_pct: int = Field(default=60, ge=10, le=95)  # Lite plan price vs current
    downgrade_plan_id: str | None = None   # cheaper plan to move subscribers to
    discount_plan_id: str | None = None    # N discounted cycles, then full price


class SubscriberContext(BaseModel):
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
    monthly_value: float | None = None   # price per month (annual plans: price / 12)
    currency: str = "USD"
    reason_hint: ReasonHint = "unknown"
    reason_text: str | None = None
    simulated: bool = False


class Offer(BaseModel):
    kind: OfferKind
    rationale: str
    paypal_request: dict   # {method, path, body, note?, simulated?}
    message: str = ""      # what the customer sees


# ---- 1. understand ---------------------------------------------------------------------

_KEYWORDS: list[tuple[ReasonHint, str]] = [
    ("price", r"expensive|pric|cost|afford|cheaper|money|budget|discount"),
    ("card", r"card|payment (failed|declined)|bank|expired"),
    ("not_using", r"not using|don'?t use|no time|busy|travel|break|pause|holiday|vacation|later"),
]


def classify_reason(text: str | None, use_llm: bool = True) -> tuple[ReasonHint, str]:
    """Free-text cancel reason -> (hint, source). Gemini when configured, keywords otherwise."""
    if not text or not text.strip():
        return "unknown", "none"
    if use_llm and llm.enabled():
        try:
            word = llm.generate(
                "Classify why a subscriber wants to cancel. Answer with exactly one word: "
                "price, not_using, card or unknown.\nReason: " + text[:500],
                temperature=0, timeout=12).lower()
            word = re.sub(r"[^a-z_]", "", word)
            if word in ("price", "not_using", "card", "unknown"):
                return word, "gemini"  # type: ignore[return-value]
        except Exception:
            pass
    low = text.lower()
    for hint, pattern in _KEYWORDS:
        if re.search(pattern, low):
            return hint, "keywords"
    return "unknown", "keywords"


# ---- 2. decide -----------------------------------------------------------------------------

def _req(ctx: SubscriberContext, action: str, body: dict, note: str = "") -> dict:
    out = {"method": "POST", "path": f"/v1/billing/subscriptions/{ctx.subscription_id}/{action}",
           "body": body}
    if note:
        out["note"] = note
    if ctx.simulated:
        out["simulated"] = True
    return out


def _build(kind: OfferKind, ctx: SubscriberContext, rules: SaveRules) -> Offer:
    money = lambda v: f"{v:.2f} {ctx.currency}"  # noqa: E731
    if kind == "pause":
        return Offer(kind=kind, rationale=(
            f"Pause for {rules.pause_days} days: {ctx.tenure_days}-day customer with "
            f"{ctx.payments_made} successful payments is worth keeping through a temporary gap."),
            message=(f"No problem. We've paused your subscription for {rules.pause_days} days; "
                     f"you won't be charged until it resumes."),
            paypal_request=_req(ctx, "suspend", {"reason": "Customer-requested pause"},
                                f"PayPal has no timed pause: the sweep calls activate after "
                                f"{rules.pause_days} days."))
    if kind == "downgrade":
        lite = ctx.price * rules.downgrade_price_pct / 100
        return Offer(kind=kind, rationale="Move to the cheaper plan instead of losing the subscriber.",
                     message=(f"Would the Lite plan at {money(lite)} work better? "
                              f"Confirm the switch on PayPal and keep your account."),
                     paypal_request=_req(ctx, "revise", {
                         "plan_id": rules.downgrade_plan_id or "PLAN_ID_downgrade"},
                         "Plan switch: PayPal returns an approval link the customer confirms."))
    if kind == "discount":
        new = ctx.price * (1 - rules.max_discount_pct / 100)
        return Offer(kind=kind, rationale=(
            f"{rules.max_discount_pct}% off the next {rules.discount_cycles} cycles: the subscriber "
            f"signalled price sensitivity."),
            message=(f"Stay for {money(new)} for the next {rules.discount_cycles} months, then "
                     f"{money(ctx.price)}. Confirm on PayPal to keep your account."),
            paypal_request=_req(ctx, "revise", {
                "plan_id": rules.discount_plan_id or "PLAN_ID_discount"},
                "Switches to the discount plan (discounted cycles, then the normal price); "
                "PayPal returns an approval link the customer confirms."))
    return Offer(kind="let_go", rationale="No offer within the merchant's rules is worth making.",
                 message="Your subscription has been cancelled. Thanks for being with us.",
                 paypal_request=_req(ctx, "cancel", {"reason": "Subscriber chose to leave"}))


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
    # Real subscribers can only be moved to plans that exist in PayPal.
    has = lambda pid: bool(pid) or ctx.simulated  # noqa: E731
    allowed = {
        "pause": rules.allow_pause,
        "downgrade": rules.allow_downgrade and has(rules.downgrade_plan_id) and ctx.price > 0,
        "discount": rules.allow_discount and has(rules.discount_plan_id) and ctx.price > 0,
    }
    for kind in order:
        if allowed[kind]:
            return _build(kind, ctx, rules)  # type: ignore[arg-type]
    return _build("let_go", ctx, rules)


# ---- 3. act ------------------------------------------------------------------------------

Executor = Callable[[dict], dict]


def dry_run_executor(request: dict) -> dict:
    return {"status": "dry_run", "request": request}


def default_executor() -> Executor:
    if (os.getenv("SAVE_AGENT_LIVE") or "").lower() == "true":
        from .paypal_sdk import execute
        return execute
    return dry_run_executor


# ---- 4. log -------------------------------------------------------------------------------

@dataclass
class SaveAction:
    id: str
    ts: datetime
    context: SubscriberContext
    offer: Offer
    status: str = "dry_run"   # executed | dry_run | awaiting_buyer_approval | overridden | failed
    result: dict = field(default_factory=dict)
    overridden_by: str | None = None
    override_note: str = ""
    reason_source: str = "none"
    resume_at: datetime | None = None
    resumed: bool = False

    def to_doc(self) -> dict:
        return {"id": self.id, "ts": self.ts.isoformat(), "context": self.context.model_dump(),
                "offer": self.offer.model_dump(), "status": self.status,
                "result": json.loads(json.dumps(self.result, default=str)),
                "overridden_by": self.overridden_by, "override_note": self.override_note,
                "reason_source": self.reason_source,
                "resume_at": self.resume_at.isoformat() if self.resume_at else None,
                "resumed": self.resumed}

    @classmethod
    def from_doc(cls, d: dict) -> "SaveAction":
        return cls(d["id"], datetime.fromisoformat(d["ts"]), SubscriberContext(**d["context"]),
                   Offer(**d["offer"]), d["status"], d["result"], d["overridden_by"],
                   d["override_note"], d.get("reason_source", "none"),
                   datetime.fromisoformat(d["resume_at"]) if d.get("resume_at") else None,
                   d.get("resumed", False))


class SaveLog:
    def __init__(self, persist: bool = True) -> None:
        self._lock = threading.Lock()
        self.persist = persist
        self.rules = SaveRules()
        self.actions: list[SaveAction] = []
        self.executor: Executor = default_executor()
        if persist:
            saved = db.get("save_rules", "rules")
            if saved:
                self.rules = SaveRules(**saved)
            self.actions = [SaveAction.from_doc(d) for d in db.all("save_action")]

    def set_rules(self, rules: SaveRules) -> SaveRules:
        self.rules = rules
        if self.persist:
            db.put("save_rules", "rules", rules.model_dump())
        return rules

    def _save(self, a: SaveAction) -> None:
        if self.persist:
            db.put("save_action", a.id, a.to_doc())

    def _run(self, offer: Offer) -> tuple[dict, str]:
        try:
            result = self.executor(offer.paypal_request)
            return result, result.get("status", "executed")
        except Exception as exc:  # never lose the audit row
            return {"error": str(exc)}, "failed"

    def handle(self, ctx: SubscriberContext, now: datetime | None = None) -> SaveAction:
        source = "given"
        if ctx.reason_text and ctx.reason_hint == "unknown":
            # Simulated cohorts use the keyword classifier: no LLM call per synthetic row.
            hint, source = classify_reason(ctx.reason_text, use_llm=not ctx.simulated)
            ctx = ctx.model_copy(update={"reason_hint": hint})
        ctx = ctx.model_copy(update={"previous_saves": max(ctx.previous_saves,
                                                           self.saves_for(ctx.subscriber_id))})
        offer = choose_offer(ctx, self.rules)
        result, status = self._run(offer)
        ts = now or datetime.utcnow()
        resume = ts + timedelta(days=self.rules.pause_days) if offer.kind == "pause" else None
        action = SaveAction(uuid.uuid4().hex[:8], ts, ctx, offer, status, result,
                            reason_source=source, resume_at=resume)
        with self._lock:
            self.actions.append(action)
        self._save(action)
        return action

    def resume_due(self, now: datetime) -> list[SaveAction]:
        """Reactivate paused subscriptions whose pause has ended (PayPal has no timed pause)."""
        done = []
        for a in self.actions:
            if (a.offer.kind == "pause" and not a.resumed and a.resume_at and a.resume_at <= now
                    and a.status in ("executed", "dry_run")):
                req = {**a.offer.paypal_request,
                       "path": a.offer.paypal_request["path"].replace("/suspend", "/activate"),
                       "body": {"reason": "Pause ended"}}
                try:
                    a.result = {**a.result, "resume": self.executor(req)}
                    a.resumed = True
                except Exception as exc:
                    a.result = {**a.result, "resume_error": str(exc)}
                self._save(a)
                done.append(a)
        return done

    def saves_for(self, subscriber_id: str) -> int:
        return sum(1 for a in self.actions if a.context.subscriber_id == subscriber_id
                   and a.offer.kind != "let_go" and a.status != "overridden")

    def handled(self, subscriber_id: str, trigger: str) -> bool:
        return any(a.context.subscriber_id == subscriber_id and a.context.trigger == trigger
                   for a in self.actions)

    def override(self, action_id: str, kind: OfferKind, note: str = "") -> SaveAction:
        orig = next((a for a in self.actions if a.id == action_id), None)
        if orig is None:
            raise KeyError(action_id)
        if orig.status == "overridden":
            raise ValueError("action already overridden")
        if kind == "downgrade" and not (self.rules.downgrade_plan_id or orig.context.simulated):
            raise ValueError("no downgrade plan set up; call POST /save/plans/setup first")
        if kind == "discount" and not (self.rules.discount_plan_id or orig.context.simulated):
            raise ValueError("no discount plan set up; call POST /save/plans/setup first")
        offer = _build(kind, orig.context, self.rules)
        orig.status, orig.override_note = "overridden", note
        result, status = self._run(offer)
        new = SaveAction(uuid.uuid4().hex[:8], datetime.utcnow(), orig.context, offer,
                         status, result, overridden_by=action_id, override_note=note,
                         reason_source=orig.reason_source)
        with self._lock:
            self.actions.append(new)
        self._save(orig)
        self._save(new)
        return new

    def summary(self) -> dict:
        saved = [a for a in self.actions if a.offer.kind != "let_go" and a.status != "overridden"]
        mrr = sum(a.context.monthly_value if a.context.monthly_value is not None
                  else a.context.price for a in saved)
        return {"total": len(self.actions), "saves": len(saved),
                "let_go": sum(a.offer.kind == "let_go" for a in self.actions),
                "overridden": sum(a.status == "overridden" for a in self.actions),
                "mrr_protected": round(mrr, 2)}

    def reset(self) -> None:
        with self._lock:
            self.actions.clear()
        self.rules = SaveRules()


save_log = SaveLog()
