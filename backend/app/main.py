from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import alerts, security, simulate, watcher
from .agent.explain import explain
from .agent.pricing_agent import generate_arms
from .assistant import ask
from .engine.decision import Action, ArmStats, Decision, decide, decide_by_value
from .engine.guardrails import GuardrailConfig, check_arm
from .events import Event
from .experiments import (active_stops, baseline_of, guardrails_view, pick_arm, record_memory, record_stop,
                          rolled_out_winner, snapshot, timeline_day)
from .memory import memory
from .metrics import mrr_series
from .paypal_experiments import router as paypal_exp_router
from .paypal_experiments import webhook_router
from .paypal_routes import router as paypal_router
from .paypal_transactions import router as paypal_tx_router
from .persistence import persistence
from .save_agent import SaveRules, SubscriberContext, save_log
from .schemas import ArmsResponse, DecideRequest, GoalRequest
from .store import Experiment, store
from .validation import validate_arms

MAX_EVENT_BATCH = 10_000


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop_checks = watcher.start(persistence.save)   # automatic guardrail checks
    stop_alerts = alerts.start(persistence.save)    # tells people what those checks stopped
    yield
    stop_alerts.set()
    if stop_checks:
        stop_checks.set()


app = FastAPI(title="PlanLab API", lifespan=lifespan)
app.include_router(paypal_router)
app.include_router(paypal_exp_router)
app.include_router(webhook_router)
app.include_router(paypal_tx_router)


@app.middleware("http")
async def save_changes(request: Request, call_next):
    response = await call_next(request)
    if persistence.enabled and request.method not in security.SAFE_METHODS and response.status_code < 500:
        await run_in_threadpool(persistence.save)
    return response


app.middleware("http")(security.guard)
app.add_middleware(CORSMiddleware, allow_origins=security.allowed_origins(),
                   allow_origin_regex=security.LOCAL_ORIGIN, allow_methods=["*"], allow_headers=["*"])
persistence.load()


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
        "note": d.note,
        "explanation": explain(d, goal),
        "arms": _json_safe([asdict(a) for a in d.arms]),
    }


def _exp(exp_id: str) -> Experiment:
    exp = store.get(exp_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    return exp


@app.get("/health")
def health():
    return {"ok": True, "gemini": bool(os.getenv("GEMINI_API_KEY")), "saved_to_disk": persistence.enabled}


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
    validation = [v.model_dump() for v in validate_arms(arms)]
    exp = store.create(req.goal, arms, source, validation)
    return {"id": exp.id, "goal": exp.goal, "source": source, "memory_used": len(memory.records),
            "arms": [a.model_dump() for a in arms], "validation": validation}


@app.get("/experiments")
def list_experiments():
    return [{"id": e.id, "goal": e.goal, "status": e.status, "events": len(e.events),
             "created_at": e.created_at.isoformat()} for e in store.list()]


@app.get("/experiments/{exp_id}")
def get_experiment(exp_id: str):
    """Everything the workspace needs to restore an experiment after a page reload."""
    exp = _exp(exp_id)
    real = sum(e.source == "real" for e in exp.events)
    return {"id": exp.id, "goal": exp.goal, "source": exp.source, "status": exp.status,
            "winner": exp.winner, "stopped_arms": exp.stopped_arms, "scenario": exp.scenario,
            "arms": [a.model_dump() for a in exp.arms], "validation": exp.validation,
            "events": {"real": real, "simulated": len(exp.events) - real},
            "rollout": exp.rollout, "last_decision": exp.last_decision,
            "stops": list(exp.stops.values()), "created_at": exp.created_at.isoformat()}


@app.delete("/experiments/{exp_id}")
def delete_experiment(exp_id: str):
    if not store.delete(exp_id):
        raise HTTPException(404, "experiment not found")
    memory.remove(exp_id)
    save_log.forget(exp_id)
    return {"deleted": exp_id}


@app.post("/experiments/{exp_id}/events")
def add_events(exp_id: str, events: list[Event]):
    """Ingest events (real webhooks mapped by Person A, or simulated by Person D)."""
    exp = _exp(exp_id)
    if len(events) > MAX_EVENT_BATCH:
        raise HTTPException(413, f"send at most {MAX_EVENT_BATCH} events at a time")
    arm_ids = {a.arm_id for a in exp.arms}
    bad = [e.arm_id for e in events if e.arm_id not in arm_ids]
    if bad:
        raise HTTPException(422, f"unknown arm_id(s): {sorted(set(bad))}")
    store.add_events(exp, [e.model_copy(update={"experiment_id": exp_id}) for e in events])
    return {"accepted": len(events), "total": len(exp.events)}


class DemoRequest(BaseModel):
    scenario: str = "winner"
    seed: int = 7
    force: bool = False    # confirms that real PayPal events may be replaced


@app.post("/experiments/{exp_id}/demo")
def run_demo(exp_id: str, req: DemoRequest):
    exp = _exp(exp_id)
    real = sum(e.source == "real" for e in exp.events)
    if real and not req.force:
        raise HTTPException(409, f"this experiment has {real} real events, which a simulation would replace")
    try:
        events = simulate.generate(exp_id, exp.arms, req.scenario, req.seed)
    except ValueError as e:
        raise HTTPException(422, str(e))
    store.replace_events(exp, events)
    exp.status, exp.winner, exp.stopped_arms = "running", None, []
    exp.rollout, exp.last_decision, exp.scenario = None, None, req.scenario
    exp.assignments.clear()
    exp.stops, exp.auto_exempt = {}, []
    # Anything learned from the data just replaced no longer holds
    memory.remove(exp_id)
    save_log.forget(exp_id)
    watcher.replay_checks(exp)
    return {"scenario": req.scenario, "events": len(exp.events)}


def _series(exp: Experiment) -> tuple[list[dict], int]:
    """MRR points and the days they span: the demo window for simulated runs, real dates otherwise."""
    real = [e.ts for e in exp.events if e.source == "real"]
    if not real:
        return mrr_series(exp.arms, exp.events, simulate.START, simulate.DAYS), simulate.DAYS
    start = min(real).replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (max(real) - start).days + 1
    step = 1 if elapsed <= 14 else 7
    days = math.ceil(elapsed / step) * step
    return mrr_series(exp.arms, exp.events, start, days, step), days


@app.get("/experiments/{exp_id}/metrics")
def metrics(exp_id: str, days: int | None = Query(None, ge=0)):
    exp = _exp(exp_id)
    rows = snapshot(exp, days).rows
    series, span = _series(exp)
    return {"id": exp.id, "status": exp.status, "winner": exp.winner,
            "stopped_arms": sorted(active_stops(exp, days)), "stops": list(exp.stops.values()), "rows": rows,
            "mrr_series": series, "window_days": span,
            "guardrails": {"config": asdict(exp.guardrails), "arms": guardrails_view(exp, rows)},
            "auto_checks": {"enabled": exp.guardrails.auto_stop, "every_minutes": watcher.CHECK_SECONDS / 60,
                            "last_checked": exp.last_auto_check.isoformat() if exp.last_auto_check else None,
                            "alerts": alerts.channels()}}


@app.post("/experiments/{exp_id}/decide")
def experiment_decide(exp_id: str, gamma: float | None = Query(None, ge=1),
                      days: int | None = Query(None, ge=0)):
    exp = _exp(exp_id)
    if rolled_out_winner(exp):
        raise HTTPException(409, "this experiment has been rolled out, so its decision is final")
    snap = snapshot(exp, days)
    base = baseline_of(exp.arms)
    stopped = active_stops(exp, days)
    others = [snap.data[a.arm_id] for a in exp.arms if a.arm_id != base.arm_id and a.arm_id not in stopped]
    cfg = exp.guardrails
    d = decide_by_value(snap.data[base.arm_id], others, snap.results,
                        gamma=gamma if gamma is not None else cfg.churn_ratio, min_signups=cfg.min_signups)

    # Absolute guardrails (retention floor, refund/dispute ceilings) override everything.
    challengers = {a.arm_id for a in others}
    breaches = [b for r in snap.rows if r["arm_id"] in challengers for b in check_arm(r, cfg)]
    if breaches:
        if d.action == Action.WINNER:
            d.reasons.pop(d.arm_id, None)   # its winning evidence isn't why something got stopped
        d.action, d.arm_id = Action.STOP_ARM, breaches[0].arm_id
        for b in breaches:
            d.reasons[b.arm_id] = (d.reasons.get(b.arm_id, "") + " " + b.message).strip()

    if d.action == Action.STOP_ARM:
        record_stop(exp, d.arm_id, d.reasons[d.arm_id], timeline_day(exp, days), automatic=False)
    elif d.action == Action.WINNER:
        if exp.winner != d.arm_id:
            exp.rollout = None
        exp.winner, exp.status = d.arm_id, "winner"
    else:
        # The latest analysis is what counts, including when it no longer supports an earlier win
        exp.winner, exp.rollout = None, None
        exp.status = "stopped_arm" if exp.stopped_arms else "running"

    if exp.winner or exp.stopped_arms:
        record_memory(exp, snapshot(exp, days).rows)
    else:
        memory.remove(exp.id)
    exp.last_decision = {**_decision_payload(d, exp.goal), "as_of_day": days}
    return exp.last_decision


class AskRequest(BaseModel):
    question: str = Field(max_length=500)


@app.post("/experiments/{exp_id}/ask")
def experiment_ask(exp_id: str, req: AskRequest, days: int | None = Query(None, ge=0)):
    return ask(req.question, snapshot(_exp(exp_id), days).rows)


# ---- memory, guardrails, allocation (v2) ---------------------------------

@app.get("/memory")
def get_memory():
    return {"records": _json_safe([asdict(r) for r in memory.records]), "summary": memory.summary()}


@app.get("/experiments/{exp_id}/guardrails")
def get_guardrails(exp_id: str, days: int | None = Query(None, ge=0)):
    exp = _exp(exp_id)
    return {"config": asdict(exp.guardrails), "arms": guardrails_view(exp, snapshot(exp, days).rows)}


class GuardrailUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retention_floor: float | None = Field(None, ge=0, le=1)
    refund_ceiling: float | None = Field(None, ge=0, le=1)
    dispute_ceiling: float | None = Field(None, ge=0, le=1)
    min_signups: int | None = Field(None, ge=0, le=1_000_000)
    churn_ratio: float | None = Field(None, ge=1, le=100)
    auto_stop: bool | None = None


@app.put("/experiments/{exp_id}/guardrails")
def set_guardrails(exp_id: str, update: GuardrailUpdate):
    exp = _exp(exp_id)
    exp.guardrails = GuardrailConfig(**{**asdict(exp.guardrails), **update.model_dump(exclude_none=True)})
    watcher.check_experiment(exp)    # new limits apply straight away
    return asdict(exp.guardrails)


@app.post("/experiments/{exp_id}/arms/{arm_id}/resume")
def resume_arm(exp_id: str, arm_id: str):
    """Undo a stop. Automatic checks then leave this option alone, though an analysis can still stop it."""
    exp = _exp(exp_id)
    if arm_id not in exp.stopped_arms:
        raise HTTPException(409, "this option isn't stopped")
    if rolled_out_winner(exp):
        raise HTTPException(409, "this experiment has been rolled out, so its decision is final")
    exp.stopped_arms.remove(arm_id)
    exp.stops.pop(arm_id, None)
    if arm_id not in exp.auto_exempt:
        exp.auto_exempt.append(arm_id)
    if exp.last_decision and exp.last_decision.get("arm_id") == arm_id:
        exp.last_decision = None
    exp.status = "winner" if exp.winner else ("stopped_arm" if exp.stopped_arms else "running")
    if exp.winner or exp.stopped_arms:
        record_memory(exp, snapshot(exp).rows)
    else:
        memory.remove(exp.id)
    return {"resumed": arm_id, "status": exp.status}


@app.post("/alerts/test")
def test_alert():
    """Send a test message to every alert channel, to check the settings before they matter."""
    if not alerts.channels():
        raise HTTPException(409, "no alert channels are set up")
    if not alerts.may_test():
        raise HTTPException(429, "Wait a few seconds before sending another test alert.")
    return alerts.send_test()


@app.get("/experiments/{exp_id}/allocation")
def allocation(exp_id: str):
    """Traffic split across live arms, based on each arm's chance of being worth the most per visitor."""
    rows = snapshot(_exp(exp_id)).rows
    return {"weights": {r["arm_id"]: r["allocation"] for r in rows},
            "prob_best": {r["arm_id"]: r["prob_best"] for r in rows}}


class AssignRequest(BaseModel):
    visitor_id: str | None = Field(None, max_length=128)


@app.post("/experiments/{exp_id}/assign")
def assign(exp_id: str, req: AssignRequest | None = None):
    """Pick the arm for a customer. Pass a visitor_id so they see the same offer when they come back."""
    exp = _exp(exp_id)
    arm_id, weights, returning = pick_arm(exp, req.visitor_id if req else None)
    arm = next(a for a in exp.arms if a.arm_id == arm_id)
    return {"arm_id": arm_id, "plan_id": arm.plan_id, "weights": weights, "returning": returning}


# ---- rollout -------------------------------------------------------------

def _plan(a) -> str:
    return a.plan_id or f"PLAN_ID_{a.arm_id}"


def _deactivate(arm) -> dict:
    return {"method": "POST", "path": f"/v1/billing/plans/{_plan(arm)}/deactivate", "body": {}}


@app.post("/experiments/{exp_id}/rollout/propose")
def rollout_propose(exp_id: str):
    exp = _exp(exp_id)
    if not exp.winner:
        raise HTTPException(409, "no winner declared yet; run /decide first")
    if rolled_out_winner(exp):
        raise HTTPException(409, "this experiment has already been rolled out")
    win = next(a for a in exp.arms if a.arm_id == exp.winner)
    base = baseline_of(exp.arms)
    others = [a for a in exp.arms if a.arm_id != win.arm_id]
    if win.arm_id == base.arm_id:
        kind = "keep_baseline"
        requests = [_deactivate(a) for a in others]
    elif win.interval == base.interval and win.trial_days == base.trial_days:
        # The baseline plan takes on the winning price, so it has to stay active.
        kind = "update_pricing_scheme"
        requests = [{"method": "POST", "path": f"/v1/billing/plans/{_plan(base)}/update-pricing-schemes",
                     "body": {"pricing_schemes": [{
                         "billing_cycle_sequence": 2 if base.trial_days else 1,   # a trial takes cycle 1
                         "pricing_scheme": {"fixed_price": {"value": f"{win.price:.2f}",
                                                            "currency_code": win.currency}}}]}}]
        requests += [_deactivate(a) for a in others if a.arm_id != base.arm_id]
    else:
        kind = "switch_default_plan"
        requests = [{"method": "INTERNAL", "path": "set_default_plan", "body": {"plan_id": _plan(win)},
                     "note": "Winner differs in interval/trial, so new sign-ups are pointed at its plan."}]
        requests += [_deactivate(a) for a in others]
    rows = snapshot(exp).rows
    exp.rollout = {"kind": kind, "status": "proposed", "winner": win.arm_id, "requests": requests,
                   "results": [],
                   # Existing subscribers stay on the price they signed up for
                   "existing_subscribers": {r["arm_id"]: r["active"] for r in rows if r["arm_id"] != win.arm_id}}
    return exp.rollout


@app.post("/experiments/{exp_id}/rollout/approve")
def rollout_approve(exp_id: str):
    exp = _exp(exp_id)
    if not exp.rollout or exp.rollout["status"] not in ("proposed", "failed"):
        raise HTTPException(409, "no proposed rollout to approve")
    results = []
    for request in exp.rollout["requests"]:
        if request["method"] == "INTERNAL":
            results.append({"status": "done"})    # PlanLab's own routing, applied by rolled_out_winner
            continue
        try:
            results.append(save_log.executor(request))
        except Exception as exc:
            results.append({"status": "failed", "error": str(exc)})
    exp.rollout["results"] = results
    exp.rollout["status"] = "failed" if any(r.get("status") == "failed" for r in results) else "approved"
    return exp.rollout


# ---- save agent ----------------------------------------------------------

def _action_json(a) -> dict:
    return {"id": a.id, "ts": a.ts.isoformat(), "status": a.status, "kind": a.offer.kind,
            "rationale": a.offer.rationale, "paypal_request": a.offer.paypal_request,
            "subscriber_id": a.context.subscriber_id, "trigger": a.context.trigger,
            "experiment_id": a.context.experiment_id,
            "arm_id": a.context.arm_id, "overridden_by": a.overridden_by,
            "override_note": a.override_note}


@app.get("/save/rules")
def get_rules():
    return save_log.rules


@app.put("/save/rules")
def put_rules(rules: SaveRules):
    save_log.rules = rules
    return rules


@app.post("/save/handle")
def save_handle(ctx: SubscriberContext):
    return _action_json(save_log.handle(ctx))


@app.get("/save/log")
def get_save_log(experiment_id: str | None = None):
    actions = [a for a in save_log.actions if experiment_id is None or a.context.experiment_id == experiment_id]
    return {"summary": save_log.summary(actions), "actions": [_action_json(a) for a in reversed(actions)]}


class OverrideRequest(BaseModel):
    kind: str
    note: str = Field("", max_length=500)


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
    return SubscriberContext(
        experiment_id=exp.id,
        subscriber_id=ev.subscriber_id, subscription_id=f"I-{ev.subscriber_id}",
        plan_id=_plan(arm), arm_id=arm.arm_id, trigger=ev.type,  # type: ignore[arg-type]
        tenure_days=max(0, (ev.ts - signup).days),
        payments_made=sum(e.type == "PAYMENT" for e in mine),
        failed_payments=sum(e.type == "PAYMENT_FAILED" for e in mine),
        price=arm.price, currency=arm.currency,
        reason_hint="card" if ev.type == "PAYMENT_FAILED" else "unknown")


@app.post("/save/sweep")
def save_sweep(limit: int = Query(50, ge=1, le=1000), experiment_id: str | None = None):
    """Nightly sweep: handle PAYMENT_FAILED / CANCEL_INTENT events not yet handled."""
    out = []
    experiments = [e for e in store.list() if experiment_id is None or e.id == experiment_id]
    for exp in experiments:
        arms = {a.arm_id: a for a in exp.arms}
        for ev in _pending_triggers(exp):
            if len(out) >= limit:
                break
            if save_log.handled(exp.id, ev.subscriber_id, ev.type):
                continue
            out.append(_action_json(save_log.handle(_context(exp, arms[ev.arm_id], ev), now=ev.ts)))
    return {"handled": len(out), "actions": out}
