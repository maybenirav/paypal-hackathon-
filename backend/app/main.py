from __future__ import annotations

import logging
import math
import os
import random
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import config, simulate
from .agent.explain import explain
from .agent.pricing_agent import fallback_arms, generate_arms
from .assistant import ask
from .engine import bayes
from .engine.decision import Action, ArmStats, Decision, decide
from .engine.guardrails import GuardrailConfig, check_arm
from .engine.guardrails import status as guardrail_status
from .engine.readiness import readiness
from .events import Event
from .memory import MemoryRecord, memory
from .metrics import aggregate, mrr_series, to_arm_stats
from .paypal_client import PayPalError
from .save_agent import SaveRules, SubscriberContext, save_log
from .schemas import ArmsResponse, DecideRequest, GoalRequest
from .store import Experiment, store
from .validation import rollout_mode, validate_arms

log = logging.getLogger("planlab")

DEMO_GOAL = "Raise annual-plan uptake without hurting 60-day retention."


def seed_demo() -> Experiment:
    """A labelled synthetic merchant so the hosted app is never empty after a restart."""
    req = GoalRequest(goal=DEMO_GOAL, current_price=15, num_arms=3, monthly_visitors=12000)
    arms = fallback_arms(req.model_copy(update={"num_arms": 4}))[:3]
    exp = store.create(DEMO_GOAL, arms, "fallback", monthly_visitors=req.monthly_visitors)
    store.replace_events(exp, simulate.generate(exp.id, exp.arms, "winner"))
    return exp


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_supabase()   # with DATABASE_URL set, saved experiments come from Supabase first
    if not store.list() and (os.getenv("SEED_DEMO") or "true").lower() != "false":
        seed_demo()
        save_sweep(limit=40)   # simulated cancel attempts -> Save Agent decisions (dry run)
    yield


app = FastAPI(title="PlanLab: Pricing Agent, Save Agent & Math Engine", lifespan=lifespan)
from app.paypal_routes import router as paypal_router  # noqa: E402
app.include_router(paypal_router)
from app.paypal_experiments import lookup_sub, router as paypal_exp_router, webhook_router  # noqa: E402
app.include_router(paypal_exp_router)
app.include_router(webhook_router)
from app.paypal_transactions import router as paypal_tx_router  # noqa: E402
app.include_router(paypal_tx_router)
from app.db import ensure_loaded as load_supabase, init_app as init_db  # noqa: E402
from app.db_routes import router as db_router  # noqa: E402
app.include_router(db_router)
from app.sim_routes import router as sim_router  # noqa: E402  Person D: simulated clock
app.include_router(sim_router)
from app.demo_routes import router as demo_router  # noqa: E402  Person D: one-call Run demo
app.include_router(demo_router)
init_db(app)
from fastapi.responses import RedirectResponse  # noqa: E402


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")



app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _json_safe(o):
    """Replace inf/nan (e.g. churn ratio vs zero-churn baseline) with None."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def _decision_payload(d: Decision, goal: str | None) -> dict:
    return {
        "action": d.action.value,
        "arm_id": d.arm_id,
        "reasons": d.reasons,
        "explanation": explain(d, goal),
        "arms": _json_safe([asdict(a) for a in d.arms]),
    }


def _exp(exp_id: str) -> Experiment:
    exp = store.get(exp_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    return exp


def _start(exp: Experiment) -> datetime:
    """Day 0 of the experiment: its first event (simulated or real)."""
    return min((e.ts for e in exp.events), default=simulate.START)


@app.get("/health")
def health():
    return {"ok": True, "experiments": len(store.list()),
            "save_agent_live": save_log.executor.__name__ != "dry_run_executor"}


# ---- stateless endpoints -------------------------------------------------

@app.post("/agent/arms", response_model=ArmsResponse)
def agent_arms(req: GoalRequest):
    arms, source = generate_arms(req)
    return ArmsResponse(arms=arms, source=source, validation=validate_arms(arms))


@app.post("/engine/decide")
def engine_decide(req: DecideRequest):
    d = decide(ArmStats(**req.baseline.model_dump()),
               [ArmStats(**a.model_dump()) for a in req.arms],
               gamma=req.gamma, discount=req.discount)
    return _decision_payload(d, req.goal)


# ---- experiments ---------------------------------------------------------

@app.post("/experiments")
def create_experiment(req: GoalRequest):
    arms, source = generate_arms(req)
    validation = validate_arms(arms)
    ok = {v.arm_id for v in validation if v.valid}
    kept = [a for a in arms if a.arm_id in ok]
    rejected = [{"arm": a.model_dump(), "errors": v.errors}
                for a, v in zip(arms, validation) if not v.valid]
    if len(kept) < 2 or not any(a.is_baseline for a in kept):
        raise HTTPException(422, {"message": "the agent did not produce two valid arms",
                                  "rejected": rejected})
    exp = store.create(req.goal, kept, source, monthly_visitors=req.monthly_visitors,
                       rejected_arms=rejected)
    ready = readiness(len(kept), monthly_visitors=req.monthly_visitors)
    return {"id": exp.id, "goal": exp.goal, "source": source, "memory_used": len(memory.records),
            "arms": [a.model_dump() for a in kept], "rejected": rejected,
            "validation": [v.model_dump() for v in validation],
            "readiness": ready.to_dict()}


@app.get("/experiments")
def list_experiments():
    return [{"id": e.id, "goal": e.goal, "status": e.status, "events": len(e.events),
             "created_at": e.created_at} for e in store.list()]


@app.post("/experiments/{exp_id}/events")
def add_events(exp_id: str, events: list[Event]):
    """Ingest events (real webhooks mapped by Person A, or simulated by Person D)."""
    exp = _exp(exp_id)
    arm_ids = {a.arm_id for a in exp.arms}
    bad = [e.arm_id for e in events if e.arm_id not in arm_ids]
    if bad:
        raise HTTPException(422, f"unknown arm_id(s): {sorted(set(bad))}")
    store.add_events(exp, [e.model_copy(update={"experiment_id": exp_id}) for e in events])
    return {"accepted": len(events), "total": len(exp.events)}


class DemoRequest(BaseModel):
    scenario: str = "winner"
    seed: int = 7


@app.post("/experiments/{exp_id}/demo")
def run_demo(exp_id: str, req: DemoRequest):
    """Replay 60 simulated days. Real PayPal events already recorded are kept."""
    exp = _exp(exp_id)
    try:
        events = simulate.generate(exp_id, exp.arms, req.scenario, req.seed)
    except ValueError as e:
        raise HTTPException(422, str(e))
    real = [e for e in exp.events if e.source == "real"]
    exp.status, exp.winner, exp.stopped_arms, exp.rollout = "running", None, [], None
    store.replace_events(exp, real + events)
    store.save(exp)
    return {"scenario": req.scenario, "events": len(events), "real_events_kept": len(real)}


def _rows(exp: Experiment, days: int | None = None) -> tuple[dict, list[dict], Decision]:
    as_of = _start(exp) + timedelta(days=days) if days is not None else None
    agg = aggregate(exp.arms, exp.events, as_of)
    base, others = to_arm_stats(exp.arms, agg)
    d = decide(base, others)
    by_arm = {a.arm_id: a for a in d.arms}
    price = {a.arm_id: a.monthly_equivalent for a in exp.arms}
    live = [a for a in exp.arms if a.arm_id not in exp.stopped_arms]
    posts = {a.arm_id: bayes.posterior(agg[a.arm_id]["paid"], agg[a.arm_id]["visitors"])
             for a in exp.arms}
    live_posts = {a.arm_id: posts[a.arm_id] for a in live}
    pbest = bayes.prob_best(live_posts, scale=price) if live else {}
    alloc = bayes.thompson_allocation(live_posts, scale=price) if live else {}
    base_arm = next((a for a in exp.arms if a.is_baseline), exp.arms[0])
    rows = []
    for a in exp.arms:
        k, r, an = a.arm_id, agg[a.arm_id], by_arm[a.arm_id]
        rows.append({**r,
                     "projected_ltv": round(an.projected_ltv, 2),
                     "conv_ci": [round(x, 4) for x in bayes.credible_interval(posts[k])],
                     "prob_best": round(pbest.get(k, 0.0), 3),
                     "prob_beats_baseline": (None if an.prob_beats_baseline is None
                                             else round(an.prob_beats_baseline, 3)),
                     "allocation": round(alloc.get(k, 0.0), 3),
                     "rollout": "baseline" if a is base_arm else rollout_mode(a, base_arm),
                     "plan_id": a.plan_id,
                     "status": ("stopped" if k in exp.stopped_arms
                                else "winner" if k == exp.winner else "running")})
    return agg, rows, d


def _readiness(exp: Experiment, rows: list[dict]) -> dict:
    base = next((r for r in rows if r["is_baseline"]), rows[0])
    have = min((r["visitors"] for r in rows), default=0)
    return readiness(len(rows), base["paid_conversion"] or None, have,
                     exp.monthly_visitors).to_dict()


@app.get("/experiments/{exp_id}/metrics")
def metrics(exp_id: str, days: int | None = None):
    exp = _exp(exp_id)
    _, rows, _ = _rows(exp, days)
    real = sum(e.source == "real" for e in exp.events)
    return _json_safe({
        "id": exp.id, "goal": exp.goal, "status": exp.status, "winner": exp.winner,
        "stopped_arms": exp.stopped_arms, "rows": rows, "readiness": _readiness(exp, rows),
        "rejected_arms": exp.rejected_arms,
        "events": {"real": real, "simulated": len(exp.events) - real},
        "mrr_series": mrr_series(exp.arms, exp.events, _start(exp), simulate.DAYS)})


@app.post("/experiments/{exp_id}/decide")
def experiment_decide(exp_id: str, gamma: float = 1.30, days: int | None = None):
    exp = _exp(exp_id)
    agg, rows, _ = _rows(exp, days)
    active = [a for a in exp.arms if a.arm_id not in exp.stopped_arms]
    base, others = to_arm_stats(active, {a.arm_id: agg[a.arm_id] for a in active})
    d = decide(base, others, gamma=gamma)

    # Absolute guardrails (retention floor, refund/dispute ceilings) override everything.
    challengers = {s.arm_id for s in others}
    breaches = [b for r in rows if r["arm_id"] in challengers
                for b in check_arm(r, exp.guardrails)]
    if breaches:
        d.action, d.arm_id = Action.STOP_ARM, breaches[0].arm_id
        for b in breaches:
            d.reasons[b.arm_id] = (d.reasons.get(b.arm_id, "") + " " + b.message).strip()
    if d.action == Action.STOP_ARM and d.arm_id:
        exp.stopped_arms.append(d.arm_id)
        exp.status = "stopped_arm"
    elif d.action == Action.WINNER:
        exp.winner, exp.status = d.arm_id, "winner"
    if d.action != Action.KEEP_TESTING:
        store.save(exp)
        _, rows, _ = _rows(exp, days)
        _record_memory(exp, rows)
    return _decision_payload(d, exp.goal)


class AskRequest(BaseModel):
    question: str


@app.post("/experiments/{exp_id}/ask")
def experiment_ask(exp_id: str, req: AskRequest):
    exp = _exp(exp_id)
    _, rows, _ = _rows(exp)
    return _json_safe(ask(req.question, rows))


@app.get("/experiments/{exp_id}/timeline")
def timeline(exp_id: str, checkpoint_days: int = 14):
    """Experiment plan as tasks + dependencies (Bryntum Gantt's project format)."""
    exp = _exp(exp_id)
    start = _start(exp)
    end = start + timedelta(days=simulate.DAYS)
    fmt = lambda d: d.strftime("%Y-%m-%d")  # noqa: E731
    # Every task is pinned to its own date: without manuallyScheduled the Gantt engine would
    # pull dependent milestones forward to "as soon as possible".
    tasks = [{"id": "design", "name": "Agent designs and validates arms", "startDate": fmt(start),
              "duration": 1, "percentDone": 100},
             {"id": "launch", "name": "Plans published to PayPal", "startDate": fmt(start),
              "duration": 0, "percentDone": 100 if any(a.plan_id for a in exp.arms) else 0}]
    deps = [{"id": "d0", "fromTask": "design", "toTask": "launch"}]
    prev = "launch"
    for i, day in enumerate(range(checkpoint_days, simulate.DAYS + 1, checkpoint_days), 1):
        tid = f"cp{i}"
        tasks.append({"id": tid, "name": f"Guardrail checkpoint, day {day}",
                      "startDate": fmt(start + timedelta(days=day)), "duration": 0})
        deps.append({"id": f"d{i}", "fromTask": prev, "toTask": tid})
        prev = tid
    tasks.append({"id": "decision", "name": f"Decision: {exp.winner or 'pending'}",
                  "startDate": fmt(end), "duration": 0,
                  "percentDone": 100 if exp.status == "winner" else 0})
    deps.append({"id": "dz", "fromTask": prev, "toTask": "decision"})
    if exp.rollout:
        tasks.append({"id": "rollout", "name": f"Rollout ({exp.rollout['status']})",
                      "startDate": fmt(end), "duration": 1,
                      "percentDone": 100 if exp.rollout["status"] == "approved" else 0})
        deps.append({"id": "dr", "fromTask": "decision", "toTask": "rollout"})
    for a in exp.arms:
        if a.arm_id in exp.stopped_arms:
            tasks.append({"id": f"stop-{a.arm_id}", "name": f"Arm stopped: {a.name}",
                          "startDate": fmt(start), "duration": 0, "cls": "stopped"})
    for t in tasks:
        t["manuallyScheduled"] = True
    return {"project": {"startDate": fmt(start)}, "tasks": {"rows": tasks},
            "dependencies": {"rows": deps}}


# ---- memory, guardrails, allocation (v2) ---------------------------------

def _record_memory(exp: Experiment, rows: list[dict]) -> None:
    memory.upsert(MemoryRecord(
        exp.id, exp.goal, exp.winner, list(exp.stopped_arms),
        [{"arm_id": r["arm_id"], "price": r["price"], "interval": r["interval"],
          "trial_days": r["trial_days"], "conversion": r["paid_conversion"],
          "mrr_per_visitor": r["mrr_per_visitor"], "churn": r["churn"],
          "projected_ltv": r["projected_ltv"]} for r in rows]))


@app.get("/memory")
def get_memory():
    return {"records": _json_safe([asdict(r) for r in memory.records]), "summary": memory.summary()}


@app.get("/experiments/{exp_id}/guardrails")
def get_guardrails(exp_id: str):
    exp = _exp(exp_id)
    _, rows, _ = _rows(exp)
    return {"config": asdict(exp.guardrails), "arms": guardrail_status(rows, exp.guardrails)}


@app.put("/experiments/{exp_id}/guardrails")
def set_guardrails(exp_id: str, cfg: dict):
    exp = _exp(exp_id)
    try:
        exp.guardrails = GuardrailConfig(**{**asdict(exp.guardrails), **cfg})
    except TypeError as e:
        raise HTTPException(422, str(e))
    store.save(exp)
    return asdict(exp.guardrails)


@app.get("/experiments/{exp_id}/allocation")
def allocation(exp_id: str):
    """Thompson-sampling traffic split across arms that are still live."""
    exp = _exp(exp_id)
    _, rows, _ = _rows(exp)
    return {"weights": {r["arm_id"]: r["allocation"] for r in rows},
            "prob_best": {r["arm_id"]: r["prob_best"] for r in rows}}


@app.post("/experiments/{exp_id}/assign")
def assign(exp_id: str):
    """Pick the arm for a new sign-up (Person A calls this before creating the subscription)."""
    exp = _exp(exp_id)
    _, rows, _ = _rows(exp)
    weights = {r["arm_id"]: r["allocation"] for r in rows if r["allocation"] > 0}
    arm_id = bayes.assign_arm(weights, random.Random())
    arm = next(a for a in exp.arms if a.arm_id == arm_id)
    return {"arm_id": arm_id, "plan_id": arm.plan_id, "weights": weights}


# ---- rollout -------------------------------------------------------------

def _plan(a) -> str:
    return a.plan_id or f"PLAN_ID_{a.arm_id}"


@app.post("/experiments/{exp_id}/rollout/propose")
def rollout_propose(exp_id: str):
    """Fair rollout: nobody already subscribed is moved to a higher price without consent.

    - Winner is cheaper and PayPal can change the plan in place: lower the baseline plan's price,
      so existing subscribers get the better price too.
    - Otherwise: new sign-ups go to the winner's plan, existing subscribers stay on their current
      plan and price (grandfathered), and losing plans stop taking new sign-ups.
    """
    exp = _exp(exp_id)
    if not exp.winner:
        raise HTTPException(409, "no winner declared yet; run /decide first")
    win = next(a for a in exp.arms if a.arm_id == exp.winner)
    base = next(a for a in exp.arms if a.is_baseline)
    requests = []
    if rollout_mode(win, base) == "in_place" and win.price <= base.price:
        kind = "update_pricing_scheme"
        requests.append({"method": "POST",
                         "path": f"/v1/billing/plans/{_plan(base)}/update-pricing-schemes",
                         "body": {"pricing_schemes": [{"billing_cycle_sequence": 1, "pricing_scheme": {
                             "fixed_price": {"value": f"{win.price:.2f}",
                                             "currency_code": win.currency}}}]},
                         "note": "Price decrease applied to the existing plan: current "
                                 "subscribers also pay less."})
        keep = {base.arm_id}
    else:
        kind = "new_signups_only"
        requests.append({"method": "INTERNAL", "path": "set_default_plan",
                         "body": {"plan_id": _plan(win)},
                         "note": "New sign-ups get the winning plan. Existing subscribers keep "
                                 "their current plan and price (grandfathered); moving them would "
                                 "need each buyer's approval in PayPal."})
        keep = {win.arm_id}
    for a in exp.arms:
        if a.arm_id not in keep:
            requests.append({"method": "POST", "path": f"/v1/billing/plans/{_plan(a)}/deactivate",
                             "body": {}, "note": "Stops new sign-ups on this plan; existing "
                                                 "subscriptions on it keep billing."})
    exp.rollout = {"kind": kind, "status": "proposed", "winner": win.arm_id,
                   "grandfathered": kind == "new_signups_only",
                   "requests": requests, "results": []}
    store.save(exp)
    return exp.rollout


@app.post("/experiments/{exp_id}/rollout/approve")
def rollout_approve(exp_id: str):
    exp = _exp(exp_id)
    if not exp.rollout or exp.rollout["status"] != "proposed":
        raise HTTPException(409, "no proposed rollout to approve")
    results = []
    for r in exp.rollout["requests"]:
        try:
            results.append(save_log.executor(r))
        except PayPalError as e:
            results.append({"status": "failed", "error": str(e)})
    exp.rollout["results"] = results
    exp.rollout["status"] = "approved"
    store.save(exp)
    return exp.rollout


# ---- save agent ----------------------------------------------------------

def _action_json(a) -> dict:
    return {"id": a.id, "ts": a.ts.isoformat(), "status": a.status, "kind": a.offer.kind,
            "rationale": a.offer.rationale, "message": a.offer.message,
            "paypal_request": a.offer.paypal_request,
            "approve_url": (a.result or {}).get("approve_url"),
            "subscriber_id": a.context.subscriber_id, "trigger": a.context.trigger,
            "reason_text": a.context.reason_text, "reason_hint": a.context.reason_hint,
            "reason_source": a.reason_source, "simulated": a.context.simulated,
            "price": a.context.price, "arm_id": a.context.arm_id,
            "resume_at": a.resume_at.isoformat() if a.resume_at else None, "resumed": a.resumed,
            "overridden_by": a.overridden_by, "override_note": a.override_note}


@app.get("/save/rules")
def get_rules():
    return save_log.rules


@app.put("/save/rules")
def put_rules(rules: SaveRules):
    return save_log.set_rules(rules)


class PlanSetupRequest(BaseModel):
    experiment_id: str


@app.post("/save/plans/setup")
def setup_save_plans(req: PlanSetupRequest):
    """Create the real PayPal plans the Save Agent moves people to: Lite and the discount plan."""
    from . import paypal_sdk
    from .paypal_plans import build_discount_plan_payload, build_plan_payload
    if not config.PAYPAL_PRODUCT_ID:
        raise HTTPException(409, "PAYPAL_PRODUCT_ID missing in .env")
    exp = _exp(req.experiment_id)
    base = next(a for a in exp.arms if a.is_baseline)
    rules = save_log.rules
    lite = base.model_copy(update={"arm_id": "save_lite", "name": f"{base.name} - Lite",
                                   "price": round(base.price * rules.downgrade_price_pct / 100, 2),
                                   "trial_days": 0, "setup_fee": 0,
                                   "reason": "Save Agent downgrade plan"})
    try:
        lite_plan = paypal_sdk.create_plan(build_plan_payload(lite, config.PAYPAL_PRODUCT_ID, "save"))
        disc_plan = paypal_sdk.create_plan(build_discount_plan_payload(
            base, rules.max_discount_pct, rules.discount_cycles, config.PAYPAL_PRODUCT_ID))
    except PayPalError as e:
        raise HTTPException(502, f"PayPal error {e.status}: {e.body[:500]}") from e
    save_log.set_rules(rules.model_copy(update={"downgrade_plan_id": lite_plan["id"],
                                                "discount_plan_id": disc_plan["id"]}))
    return {"downgrade_plan_id": lite_plan["id"], "discount_plan_id": disc_plan["id"],
            "lite_price": lite.price, "rules": save_log.rules}


@app.post("/save/handle")
def save_handle(ctx: SubscriberContext):
    return _action_json(save_log.handle(ctx))


class CancelIntent(BaseModel):
    subscription_id: str
    reason_text: str = Field(default="", max_length=500)


@app.post("/save/cancel-intent")
def cancel_intent(req: CancelIntent):
    """A real subscriber clicked cancel: record it, let the Save Agent respond for real."""
    from . import paypal_sdk
    found = lookup_sub(req.subscription_id)
    if not found:
        raise HTTPException(404, "subscription is not part of any PlanLab experiment")
    exp = _exp(found[0])
    arm = next(a for a in exp.arms if a.arm_id == found[1])
    try:
        sub = paypal_sdk.get_subscription(req.subscription_id)
    except PayPalError as e:
        raise HTTPException(502, f"PayPal error {e.status}: {e.body[:500]}") from e
    if sub.get("status") != "ACTIVE":
        raise HTTPException(409, f"subscription is {sub.get('status')}, not ACTIVE")
    started = sub.get("start_time") or sub.get("create_time")
    tenure = 0
    if started:
        tenure = max(0, (datetime.utcnow() - datetime.fromisoformat(
            str(started).replace("Z", "+00:00")).replace(tzinfo=None)).days)
    cycles = (sub.get("billing_info") or {}).get("cycle_executions") or []
    payments = sum(c.get("cycles_completed", 0) for c in cycles)
    mine = [e for e in exp.events if e.subscriber_id == req.subscription_id]
    payments = max(payments, sum(e.type == "PAYMENT" for e in mine))
    ev = Event(experiment_id=exp.id, arm_id=arm.arm_id, subscriber_id=req.subscription_id,
               type="CANCEL_INTENT", ts=datetime.utcnow(), source="real",
               reason=req.reason_text or None)
    store.add_events(exp, [ev])
    ctx = SubscriberContext(
        subscriber_id=req.subscription_id, subscription_id=req.subscription_id,
        plan_id=arm.plan_id or "", arm_id=arm.arm_id, trigger="CANCEL_INTENT",
        tenure_days=tenure, payments_made=payments, price=arm.price,
        monthly_value=arm.monthly_equivalent, currency=arm.currency,
        reason_text=req.reason_text or None)
    return _action_json(save_log.handle(ctx))


@app.get("/save/log")
def get_save_log():
    return {"summary": save_log.summary(),
            "actions": [_action_json(a) for a in reversed(save_log.actions)]}


class OverrideRequest(BaseModel):
    kind: str
    note: str = ""


@app.post("/save/log/{action_id}/override")
def override(action_id: str, req: OverrideRequest):
    if req.kind not in ("pause", "downgrade", "discount", "let_go"):
        raise HTTPException(422, "kind must be pause, downgrade, discount or let_go")
    try:
        return _action_json(save_log.override(action_id, req.kind, req.note))  # type: ignore[arg-type]
    except KeyError:
        raise HTTPException(404, "action not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


def _pending_triggers(exp: Experiment):
    seen = set()
    cancelled = {e.subscriber_id for e in exp.events if e.type == "CANCELLED"}
    for e in exp.events:
        if e.type in ("PAYMENT_FAILED", "CANCEL_INTENT") and e.subscriber_id not in cancelled:
            key = (e.subscriber_id, e.type)
            if key not in seen:
                seen.add(key)
                yield e


def _context(exp: Experiment, arm, ev: Event) -> SubscriberContext:
    mine = [e for e in exp.events if e.subscriber_id == ev.subscriber_id]
    signup = next((e.ts for e in mine if e.type == "SIGNUP"), ev.ts)
    sim = ev.source == "simulated"
    return SubscriberContext(
        subscriber_id=ev.subscriber_id,
        subscription_id=f"SIM-{ev.subscriber_id}" if sim else ev.subscriber_id,
        plan_id=_plan(arm), arm_id=arm.arm_id, trigger=ev.type,  # type: ignore[arg-type]
        tenure_days=max(0, (ev.ts - signup).days),
        payments_made=sum(e.type == "PAYMENT" for e in mine),
        failed_payments=sum(e.type == "PAYMENT_FAILED" for e in mine),
        price=arm.price, monthly_value=arm.monthly_equivalent, currency=arm.currency,
        reason_hint="card" if ev.type == "PAYMENT_FAILED" else "unknown",
        reason_text=ev.reason, simulated=sim)


@app.post("/save/sweep")
def save_sweep(limit: int = 50):
    """Nightly sweep (Render cron): reactivate ended pauses, then handle new triggers."""
    resumed = save_log.resume_due(datetime.utcnow())
    out = []
    for exp in store.list():
        arms = {a.arm_id: a for a in exp.arms}
        for ev in _pending_triggers(exp):
            if len(out) >= limit:
                break
            if save_log.handled(ev.subscriber_id, ev.type):
                continue
            out.append(_action_json(save_log.handle(_context(exp, arms[ev.arm_id], ev), now=ev.ts)))
    return {"handled": len(out), "resumed": len(resumed), "actions": out}


# ---- demo control ----------------------------------------------------------

@app.post("/admin/reset-demo")
def reset_demo():
    """Wipe everything and re-seed the labelled demo merchant (for rehearsals)."""
    store.reset()
    save_log.reset()
    memory.records.clear()
    exp = seed_demo()
    save_sweep(limit=40)
    return {"id": exp.id}
