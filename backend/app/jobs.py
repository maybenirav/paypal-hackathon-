"""Scheduled jobs, called by the Render cron services (see render.yaml and scripts/run_job.py).

  POST /jobs/save-sweep        Save Agent: resume ended pauses, handle new failed payments / cancel intents
  POST /jobs/checkpoint        Guardrail checkpoint: run the decision engine on every live experiment
  POST /jobs/history-refresh   Re-import PayPal history, only if the merchant connected PayPal
  GET  /jobs                   what each job does and when it last ran

When JOBS_TOKEN is set, every job needs the header `X-Jobs-Token: <JOBS_TOKEN>`, so the public
web app cannot trigger them. Leave it unset for local development.
"""
from __future__ import annotations

import hmac
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException

from .local_db import db

router = APIRouter(prefix="/jobs", tags=["jobs"])

JOBS = {
    "save-sweep": "Save Agent: resume ended pauses, handle failed payments and cancel intents",
    "checkpoint": "Guardrail checkpoint: run the decision engine on every live experiment",
    "history-refresh": "Re-import 12 months of PayPal history (only if PayPal was connected)",
}


def _check(token: str | None) -> None:
    expected = os.getenv("JOBS_TOKEN")
    if expected and not (token and hmac.compare_digest(token, expected)):
        raise HTTPException(401, "missing or wrong X-Jobs-Token")


def _record(name: str, started: float, result: dict) -> dict:
    run = {"job": name, "finished_at": datetime.now(timezone.utc).isoformat(),
           "seconds": round(time.time() - started, 2), "result": result}
    db.put("job_run", name, run)
    return run


@router.get("")
def list_jobs():
    return {"jobs": [{"name": n, "description": d, "last_run": db.get("job_run", n)} for n, d in JOBS.items()],
            "protected": bool(os.getenv("JOBS_TOKEN"))}


@router.post("/save-sweep")
def job_save_sweep(limit: int = 200, x_jobs_token: str | None = Header(default=None)):
    _check(x_jobs_token)
    from .main import save_sweep
    t = time.time()
    out = save_sweep(limit=limit)
    return _record("save-sweep", t, {k: v for k, v in out.items() if k != "actions"})


@router.post("/checkpoint")
def job_checkpoint(x_jobs_token: str | None = Header(default=None)):
    """Experiments that already have a winner are left alone; the rollout still needs the merchant."""
    _check(x_jobs_token)
    from .main import experiment_decide
    from .store import store
    t = time.time()
    results = []
    for exp in store.list():
        if exp.status == "winner" or not exp.events:
            continue
        try:
            d = experiment_decide(exp.id)
            results.append({"experiment_id": exp.id, "action": d["action"], "arm_id": d["arm_id"]})
        except HTTPException as e:
            results.append({"experiment_id": exp.id, "error": e.detail})
    return _record("checkpoint", t, {"checked": len(results), "decisions": results})


@router.post("/history-refresh")
def job_history_refresh(months: int = 12, x_jobs_token: str | None = Header(default=None)):
    _check(x_jobs_token)
    from .backfill import backfill
    t = time.time()
    latest = db.get("backfill", "latest")
    if not latest or latest.get("source") != "paypal":
        return _record("history-refresh", t, {"skipped": "PayPal history was never imported"})
    out = backfill(months=months)
    return _record("history-refresh", t, {"customers": out["customers"], "transactions": out["transactions"]})
