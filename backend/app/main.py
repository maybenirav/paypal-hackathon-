from __future__ import annotations

import math
import random
from dataclasses import asdict
from datetime import timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import simulate
from .agent.explain import explain
from .agent.pricing_agent import generate_arms
from .assistant import ask
from .engine import bayes
from .engine.decision import Action, ArmStats, Decision, decide
from .engine.guardrails import GuardrailConfig, check_arm
from .engine.guardrails import status as guardrail_status
from .events import Event
from .memory import MemoryRecord, memory
from .metrics import aggregate, mrr_series, to_arm_stats
from .save_agent import SaveRules, SubscriberContext, save_log
from .schemas import ArmsResponse, DecideRequest, GoalRequest
from .store import Experiment, store
from .validation import validate_arms

app = FastAPI(title="PlanLab Agent & Math Engine")
from app.paypal_routes import router as paypal_router
app.include_router(paypal_router)
from app.paypal_experiments import router as paypal_exp_router, webhook_router
app.include_router(paypal_exp_router)
app.include_router(webhook_router)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _json_safe(o):
    """Replace inf/nan (e.g. churn ratio vs zero-churn baseline) with None."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
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


@app.get("/health")
def health():
    return {"ok": True}


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
    exp = store.create(req.goal, arms, source)
    return {"id": exp.id, "goal": exp.goal, "source": source, "memory_used": len(memory.records),
            "arms": [a.model_dump() for a in arms],
            "validation": [v.model_dump() for v in validation]}


@app.get("/experiments")
def list_experiments():
    return [{"id": e.id, "goal": e.goal, "status": e.status, "events": len(e.events)}
            for e in store.list()]


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
    exp = _exp(exp_id)
    try:
        events = simulate.generate(exp_id, exp.arms, req.scenario, req.seed)
    except ValueError as e:
        raise HTTPException(422, str(e))
    exp.events.clear()
    exp.status, exp.winner, exp.stopped_arms = "running", None, []
    store.add_events(exp, events)
    return {"scenario": req.scenario, "events": len(events)}


def _rows(exp: Experiment, days: int | None = None) -> tuple[dict, list[dict]]:
    as_of = simulate.START + timedelta(days=days) if days is not None else None
    agg = aggregate(exp.arms, exp.events, as_of)
    base, others = to_arm_stats(exp.arms, agg)
    d = decide(base, others)
    ltv = {a.arm_id: a.ltv for a in d.arms}
    sig = {a.arm_id: (a.ztest.z if a.ztest else None) for a in d.arms}
    live = [a for a in exp.arms if a.arm_id not in exp.stopped_arms]
    posts = {a.arm_id: bayes.posterior(agg[a.arm_id]["signups"], agg[a.arm_id]["visitors"])
             for a in exp.arms}
    live_posts = {a.arm_id: posts[a.arm_id] for a in live}
    pbest = bayes.prob_best(live_posts) if live else {}
    alloc = bayes.thompson_allocation(live_posts) if live else {}
    rows = [{**r, "ltv": round(ltv[k], 2),
             "conv_ci": [round(x, 4) for x in bayes.credible_interval(posts[k])],
             "prob_best": round(pbest.get(k, 0.0), 3), "allocation": round(alloc.get(k, 0.0), 3), "z": None if sig[k] is None else round(sig[k], 2),
             "status": "stopped" if k in exp.stopped_arms else ("winner" if k == exp.winner else "running")}
            for k, r in agg.items()]
    return agg, rows


@app.get("/experiments/{exp_id}/metrics")
def metrics(exp_id: str, days: int | None = None):
    exp = _exp(exp_id)
    _, rows = _rows(exp, days)
    return {"id": exp.id, "status": exp.status, "winner": exp.winner,
            "stopped_arms": exp.stopped_arms, "rows": rows,
            "mrr_series": mrr_series(exp.arms, exp.events, simulate.START, simulate.DAYS)}


@app.post("/experiments/{exp_id}/decide")
def experiment_decide(exp_id: str, gamma: float = 1.30, days: int | None = None):
    exp = _exp(exp_id)
    agg, rows = _rows(exp, days)
    active = [a for a in exp.arms if a.arm_id not in exp.stopped_arms]
    base, others = to_arm_stats(active, {k: agg[k] for k in (a.arm_id for a in active)})
    d = decide(base, others, gamma=gamma, method="bayes")

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
        _, rows = _rows(exp, days)
        _record_memory(exp, rows)
    return _decision_payload(d, exp.goal)


class AskRequest(BaseModel):
    question: str


@app.post("/experiments/{exp_id}/ask")
def experiment_ask(exp_id: str, req: AskRequest):
    exp = _exp(exp_id)
    _, rows = _rows(exp)
    return ask(req.question, rows)


# ---- memory, guardrails, allocation (v2) ---------------------------------

def _record_memory(exp: Experiment, rows: list[dict]) -> None:
    trial = {a.arm_id: a.trial_days for a in exp.arms}
    memory.upsert(MemoryRecord(
        exp.id, exp.goal, exp.winner, list(exp.stopped_arms),
        [{"arm_id": r["arm_id"], "price": r["price"], "interval": r["interval"],
          "trial_days": trial[r["arm_id"]], "conversion": r["conversion"],
          "churn": r["churn"], "ltv": r["ltv"]} for r in rows]))


@app.get("/memory")
def get_memory():
    return {"records": _json_safe([asdict(r) for r in memory.records]), "summary": memory.summary()}


@app.get("/experiments/{exp_id}/guardrails")
def get_guardrails(exp_id: str):
    exp = _exp(exp_id)
    _, rows = _rows(exp)
    return {"config": asdict(exp.guardrails), "arms": guardrail_status(rows, exp.guardrails)}


@app.put("/experiments/{exp_id}/guardrails")
def set_guardrails(exp_id: str, cfg: dict):
    exp = _exp(exp_id)
    try:
        exp.guardrails = GuardrailConfig(**{**asdict(exp.guardrails), **cfg})
    except TypeError as e:
        raise HTTPException(422, str(e))
    return asdict(exp.guardrails)


@app.get("/experiments/{exp_id}/allocation")
def allocation(exp_id: str):
    """Thompson-sampling traffic split across arms that are still live."""
    exp = _exp(exp_id)
    _, rows = _rows(exp)
    return {"weights": {r["arm_id"]: r["allocation"] for r in rows},
            "prob_best": {r["arm_id"]: r["prob_best"] for r in rows}}


@app.post("/experiments/{exp_id}/assign")
def assign(exp_id: str):
    """Pick the arm for a new sign-up (Person A calls this before creating the subscription)."""
    exp = _exp(exp_id)
    _, rows = _rows(exp)
    weights = {r["arm_id"]: r["allocation"] for r in rows if r["allocation"] > 0}
    arm_id = bayes.assign_arm(weights, random.Random())
    arm = next(a for a in exp.arms if a.arm_id == arm_id)
    return {"arm_id": arm_id, "plan_id": arm.plan_id, "weights": weights}


# ---- rollout -------------------------------------------------------------

def _plan(a) -> str:
    return a.plan_id or f"PLAN_ID_{a.arm_id}"


@app.post("/experiments/{exp_id}/rollout/propose")
def rollout_propose(exp_id: str):
    exp = _exp(exp_id)
    if not exp.winner:
        raise HTTPException(409, "no winner declared yet; run /decide first")
    win = next(a for a in exp.arms if a.arm_id == exp.winner)
    base = next(a for a in exp.arms if a.is_baseline)
    requests = []
    if win.interval == base.interval and win.trial_days == base.trial_days:
        kind = "update_pricing_scheme"
        requests.append({"method": "POST",
                         "path": f"/v1/billing/plans/{_plan(base)}/update-pricing-schemes",
                         "body": {"pricing_schemes": [{"billing_cycle_sequence": 1, "pricing_scheme": {
                             "fixed_price": {"value": f"{win.price:.2f}",
                                             "currency_code": win.currency}}}]}})
    else:
        kind = "switch_default_plan"
        requests.append({"method": "INTERNAL", "path": "set_default_plan",
                         "body": {"plan_id": _plan(win)},
                         "note": "Winner differs in interval/trial, so new sign-ups are pointed at its plan."})
    for a in exp.arms:
        if a.arm_id != win.arm_id:
            requests.append({"method": "POST", "path": f"/v1/billing/plans/{_plan(a)}/deactivate",
                             "body": {}})
    exp.rollout = {"kind": kind, "status": "proposed", "winner": win.arm_id,
                   "requests": requests, "results": []}
    return exp.rollout


@app.post("/experiments/{exp_id}/rollout/approve")
def rollout_approve(exp_id: str):
    exp = _exp(exp_id)
    if not exp.rollout or exp.rollout["status"] != "proposed":
        raise HTTPException(409, "no proposed rollout to approve")
    exp.rollout["results"] = [save_log.executor(r) for r in exp.rollout["requests"]]
    exp.rollout["status"] = "approved"
    return exp.rollout


# ---- save agent ----------------------------------------------------------

def _action_json(a) -> dict:
    return {"id": a.id, "ts": a.ts.isoformat(), "status": a.status, "kind": a.offer.kind,
            "rationale": a.offer.rationale, "paypal_request": a.offer.paypal_request,
            "subscriber_id": a.context.subscriber_id, "trigger": a.context.trigger,
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
    return SubscriberContext(
        subscriber_id=ev.subscriber_id, subscription_id=f"I-{ev.subscriber_id}",
        plan_id=_plan(arm), arm_id=arm.arm_id, trigger=ev.type,  # type: ignore[arg-type]
        tenure_days=max(0, (ev.ts - signup).days),
        payments_made=sum(e.type == "PAYMENT" for e in mine),
        failed_payments=sum(e.type == "PAYMENT_FAILED" for e in mine),
        price=arm.price, currency=arm.currency,
        reason_hint="card" if ev.type == "PAYMENT_FAILED" else "unknown")


@app.post("/save/sweep")
def save_sweep(limit: int = 50):
    """Nightly sweep: handle PAYMENT_FAILED / CANCEL_INTENT events not yet handled."""
    out = []
    for exp in store.list():
        arms = {a.arm_id: a for a in exp.arms}
        for ev in _pending_triggers(exp):
            if len(out) >= limit:
                break
            if save_log.handled(ev.subscriber_id, ev.type):
                continue
            out.append(_action_json(save_log.handle(_context(exp, arms[ev.arm_id], ev), now=ev.ts)))
    return {"handled": len(out), "actions": out}
