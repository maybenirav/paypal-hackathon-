from __future__ import annotations

import math
from dataclasses import asdict
from datetime import timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import simulate
from .agent.explain import explain
from .agent.pricing_agent import generate_arms
from .assistant import ask
from .engine.decision import Action, ArmStats, Decision, decide
from .events import Event
from .metrics import aggregate, mrr_series, to_arm_stats
from .schemas import ArmsResponse, DecideRequest, GoalRequest
from .store import Experiment, store
from .validation import validate_arms

app = FastAPI(title="PlanLab Agent & Math Engine")
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
    return {"id": exp.id, "goal": exp.goal, "source": source,
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
    rows = [{**r, "ltv": round(ltv[k], 2), "z": None if sig[k] is None else round(sig[k], 2),
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
    agg, _ = _rows(exp, days)
    active = [a for a in exp.arms if a.arm_id not in exp.stopped_arms]
    base, others = to_arm_stats(active, {k: agg[k] for k in (a.arm_id for a in active)})
    d = decide(base, others, gamma=gamma)
    if d.action == Action.STOP_ARM and d.arm_id:
        exp.stopped_arms.append(d.arm_id)
        exp.status = "stopped_arm"
    elif d.action == Action.WINNER:
        exp.winner, exp.status = d.arm_id, "winner"
    return _decision_payload(d, exp.goal)


class AskRequest(BaseModel):
    question: str


@app.post("/experiments/{exp_id}/ask")
def experiment_ask(exp_id: str, req: AskRequest):
    exp = _exp(exp_id)
    _, rows = _rows(exp)
    return ask(req.question, rows)
