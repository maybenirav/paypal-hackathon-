"""Person D: endpoints for the simulated clock.

  POST /experiments/{id}/sim/start    start (or restart) the clock - keeps real events
  POST /experiments/{id}/sim/pause
  POST /experiments/{id}/sim/resume
  POST /experiments/{id}/sim/step?days=N   jump N simulated days by hand
  GET  /experiments/{id}/sim/status   current day, events released, real sign-ups

The frontend polls /sim/status and /metrics (e.g. every second) while status == "running".
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import sim_clock
from app.store import store

router = APIRouter(prefix="/experiments", tags=["simulation"])


class SimStartRequest(BaseModel):
    scenario: Literal["winner", "guardrail"] | None = None   # default: SIM_SCENARIO
    seed: int | None = None                                   # default: SIM_RANDOM_SEED
    seconds_per_day: float | None = Field(default=None, ge=0, le=10)  # 0 = manual stepping
    days: int | None = Field(default=None, ge=sim_clock.MIN_DAYS, le=365)


def _exp(exp_id: str):
    exp = store.get(exp_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    return exp


def _run(exp_id: str):
    _exp(exp_id)
    run = sim_clock.get_run(exp_id)
    if run is None:
        raise HTTPException(409, "simulation not started - call /sim/start first")
    return run


@router.post("/{exp_id}/sim/start")
def sim_start(exp_id: str, req: SimStartRequest | None = None):
    exp = _exp(exp_id)
    req = req or SimStartRequest()
    try:
        run = sim_clock.start(exp, req.scenario, req.seed, req.seconds_per_day, req.days)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return sim_clock.status(run)


@router.post("/{exp_id}/sim/pause")
def sim_pause(exp_id: str):
    _run(exp_id)
    return sim_clock.status(sim_clock.pause(exp_id))


@router.post("/{exp_id}/sim/resume")
def sim_resume(exp_id: str):
    _run(exp_id)
    return sim_clock.status(sim_clock.resume(exp_id))


@router.post("/{exp_id}/sim/step")
def sim_step(exp_id: str, days: int = 1):
    _run(exp_id)
    if days < 1:
        raise HTTPException(422, "days must be at least 1")
    return sim_clock.status(sim_clock.step(exp_id, days))


@router.get("/{exp_id}/sim/status")
def sim_status(exp_id: str):
    return sim_clock.status(_run(exp_id))
