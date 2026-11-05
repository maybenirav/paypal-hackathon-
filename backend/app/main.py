from __future__ import annotations

import math
from dataclasses import asdict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent.explain import explain
from .agent.pricing_agent import generate_arms
from .engine.decision import ArmStats, decide
from .schemas import ArmsResponse, DecideRequest, GoalRequest
from .validation import validate_arms

app = FastAPI(title="PlanLab Agent & Math Engine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/agent/arms", response_model=ArmsResponse)
def agent_arms(req: GoalRequest):
    arms, source = generate_arms(req)
    return ArmsResponse(arms=arms, source=source, validation=validate_arms(arms))


@app.post("/engine/decide")
def engine_decide(req: DecideRequest):
    d = decide(ArmStats(**req.baseline.model_dump()),
               [ArmStats(**a.model_dump()) for a in req.arms],
               gamma=req.gamma, discount=req.discount)
    return {
        "action": d.action.value,
        "arm_id": d.arm_id,
        "reasons": d.reasons,
        "explanation": explain(d, req.goal),
        "arms": _json_safe([asdict(a) for a in d.arms]),
    }


def _json_safe(o):
    """Replace inf/nan (e.g. churn ratio vs zero-churn baseline) with None."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(v) for v in o]
    return o
