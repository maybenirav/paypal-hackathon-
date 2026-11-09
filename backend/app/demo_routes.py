"""Person D: the one-call "Run demo".

  POST /demo/run        create experiment -> publish real PayPal plans -> start the simulated
                        clock -> real KERNEL sign-ups (in parallel) -> AI decides every 7 sim days
                        Returns the experiment id straight away; the story runs in the background.
  GET  /demo/{exp_id}   progress log, AI decisions, real sign-ups, clock status
                        (the frontend polls this + /experiments/{id}/metrics every second)

Story told: "real PayPal sign-ups, simulated time".
Every step is fault tolerant: PayPal/network hiccups are retried, a failed real sign-up or a
failed AI decision is logged and the demo carries on, and only a hard failure (e.g. PayPal plans
cannot be published at all) marks the run as failed - with the reason in `error`.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app import kernel_signups, sim_clock
from app.store import store

log = logging.getLogger("planlab.demo")
router = APIRouter(prefix="/demo", tags=["demo"])

DEFAULT_GOAL = "Raise annual-plan uptake without hurting 60-day retention"
DECIDE_FROM_DAY = 14      # give the arms two weeks of data before the AI may act
DECIDE_EVERY = 7          # then let the AI decide once per simulated week
SIGNUP_TIMEOUT = 420      # seconds to wait for real sign-ups before finishing anyway
PUBLISH_TRIES = 6


class DemoRunRequest(BaseModel):
    goal: str = Field(default=DEFAULT_GOAL, min_length=3)
    current_price: float = Field(default=10.0, gt=0)
    scenario: Literal["winner", "guardrail"] | None = None   # default: SIM_SCENARIO
    seed: int | None = None                                   # default: SIM_RANDOM_SEED
    real_signups: int = Field(default=2, ge=0, le=5)          # 0 = skip KERNEL
    seconds_per_day: float | None = Field(default=None, ge=0, le=5)  # 0 = as fast as possible
    publish: bool = True                                      # create real PayPal plans


RUN_EXAMPLES = {
    "defaults": {"summary": "Full demo with the .env settings (recommended)", "value": {}},
    "guardrail": {"summary": "Guardrail story: the AI stops a risky arm",
                  "value": {"scenario": "guardrail", "real_signups": 2}},
    "winner": {"summary": "Winner story: the annual plan wins",
               "value": {"scenario": "winner", "real_signups": 2}},
    "offline": {"summary": "No PayPal / KERNEL (quick rehearsal)",
                "value": {"real_signups": 0, "publish": False}},
}


@dataclass
class DemoRun:
    experiment_id: str
    scenario: str
    status: str = "starting"   # starting | running | done | failed
    log: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    real_signups: list[dict] = field(default_factory=list)
    error: str | None = None

    def say(self, msg: str) -> None:
        log.info("demo %s: %s", self.experiment_id, msg)
        self.log.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "msg": msg})


_demos: dict[str, DemoRun] = {}


def _short(e: BaseException) -> str:
    detail = getattr(e, "detail", None)
    text = str(detail if detail is not None else e).strip() or type(e).__name__
    return text.splitlines()[0][:300]


# ---------------------------------------------------------------- steps

def _publish(run: DemoRun) -> None:
    """Create the real PayPal plans. Retries PayPal outages AND network timeouts."""
    from app.paypal_experiments import publish_plans
    for i in range(1, PUBLISH_TRIES + 1):
        try:
            res = publish_plans(run.experiment_id)
            run.say(f"Published {len(res['plans'])} real PayPal billing plans")
            return
        except HTTPException as e:
            if e.status_code < 500:          # e.g. PAYPAL_PRODUCT_ID missing: retrying won't help
                raise RuntimeError(f"publish failed: {_short(e)}") from e
            err = e
        except Exception as e:               # timeouts, connection resets, DNS hiccups ...
            err = e
        if i == PUBLISH_TRIES:
            raise RuntimeError(f"publish failed after {PUBLISH_TRIES} tries: {_short(err)}")
        run.say(f"PayPal not reachable ({_short(err)[:80]}), retrying publish {i}/{PUBLISH_TRIES - 1} ...")
        time.sleep(min(5 * i, 20))           # safe: publish skips arms that already have a plan


def _signups(run: DemoRun, n: int) -> None:
    try:
        res = kernel_signups.run_signups(run.experiment_id, n)
        run.real_signups = [r or {"ok": False, "error": "no result"} for r in res]
        ok = sum(bool(r.get("ok")) for r in run.real_signups)
        run.say(f"Real PayPal sign-ups ACTIVE: {ok}/{n}")
    except Exception as e:
        run.say(f"Real sign-ups failed: {_short(e)}")


def _decide(run: DemoRun, day: int, final: bool = False) -> None:
    """Ask the AI engine for a decision. A failure here is logged, never fatal."""
    from app.main import experiment_decide
    exp = store.get(run.experiment_id)
    if exp is None or exp.winner:
        return
    try:
        d = experiment_decide(run.experiment_id, gamma=1.30, days=None)
    except Exception as e:
        run.say(f"Day {day}: AI decision skipped ({_short(e)})")
        return
    if d["action"] == "keep_testing" and not final:
        return
    run.decisions.append({"day": day, "action": d["action"], "arm_id": d["arm_id"],
                          "explanation": d["explanation"]})
    arm = f" ({d['arm_id']})" if d["arm_id"] else ""
    run.say(f"Day {day}: AI decision = {d['action']}{arm}")


def _persist() -> None:
    """Save to the database (if DATABASE_URL is set); background work isn't saved otherwise."""
    try:
        from app import db
        db.save_dirty()
    except Exception:
        log.exception("saving the demo to the database failed")


def _orchestrate(run: DemoRun, req: DemoRunRequest) -> None:
    try:
        exp = store.get(run.experiment_id)
        if exp is None:
            raise RuntimeError("experiment disappeared (server restarted?)")
        if req.publish:
            _publish(run)

        sim = sim_clock.start(exp, run.scenario, req.seed, req.seconds_per_day)
        run.status = "running"
        run.say(f"Simulated clock started: {sim.days} days at {sim.seconds_per_day:g} s/day, "
                f"scenario '{run.scenario}'")

        signups = None
        if req.real_signups:
            signups = threading.Thread(target=_signups, args=(run, req.real_signups), daemon=True)
            signups.start()
            run.say(f"Started {req.real_signups} real PayPal sign-ups in KERNEL cloud browsers")

        manual = sim.seconds_per_day <= 0
        next_day = DECIDE_FROM_DAY
        t0 = time.time()
        while True:
            if manual and sim.status not in ("done", "stopped"):
                sim_clock.step(exp.id)
            if sim.status == "stopped":
                raise RuntimeError("the simulation was restarted by another request")
            if sim.status != "done" and sim.day >= next_day:
                _decide(run, sim.day)
                next_day = max(next_day + DECIDE_EVERY, sim.day + 1)
            sim_done = sim.status == "done"
            waited = time.time() - t0
            signups_done = signups is None or not signups.is_alive() or waited > SIGNUP_TIMEOUT
            if sim_done and signups_done:
                break
            if not manual or sim_done:
                time.sleep(0.2)

        if signups is not None and signups.is_alive():
            run.say("Real sign-ups still running; deciding with the data so far")
        _decide(run, sim.day, final=True)
        run.status = "done"
        run.say("Demo finished")
    except Exception as e:
        log.exception("demo %s failed", run.experiment_id)
        run.status, run.error = "failed", _short(e)
        run.say(f"Demo failed: {run.error}")
    finally:
        _persist()


# ---------------------------------------------------------------- endpoints

@router.post("/run")
def demo_run(req: Annotated[DemoRunRequest | None, Body(openapi_examples=RUN_EXAMPLES)] = None):
    req = req or DemoRunRequest()
    if req.real_signups:
        if not req.publish:
            raise HTTPException(422, "real_signups need publish=true (PayPal plans must exist)")
        import os
        if not os.getenv("KERNEL_API_KEY"):
            raise HTTPException(422, "KERNEL_API_KEY missing - add it to .env or send real_signups=0")
        if kernel_signups.missing_packages():
            raise HTTPException(422, f"missing packages {kernel_signups.missing_packages()} - "
                                     "run: pip install -r requirements.txt")
        if not kernel_signups.load_buyers():
            raise HTTPException(422, "No PAYPAL_SANDBOX_BUYER_1_EMAIL / _PASSWORD in .env - "
                                     "add US sandbox buyers or send real_signups=0")
    scenario = req.scenario or sim_clock.SIM_SCENARIO

    from app.main import create_experiment
    from app.schemas import GoalRequest
    created = create_experiment(GoalRequest(goal=req.goal, current_price=req.current_price))

    run = DemoRun(created["id"], scenario)
    agent = {"gemini": "Gemini", "groq": "Groq", "ollama": "Ollama"}.get(
        created.get("source"), "the fallback agent")
    run.say(f"Experiment created: {len(created['arms'])} arms proposed by {agent}")
    _demos[run.experiment_id] = run
    threading.Thread(target=_orchestrate, args=(run, req), daemon=True,
                     name=f"demo-{run.experiment_id}").start()
    return {"experiment_id": run.experiment_id, "scenario": scenario,
            "source": created.get("source"), "arms": created["arms"],
            "status_url": f"/demo/{run.experiment_id}",
            "metrics_url": f"/experiments/{run.experiment_id}/metrics"}


@router.get("/{exp_id}")
def demo_status(exp_id: str):
    run = _demos.get(exp_id)
    if run is None:
        raise HTTPException(404, "no demo run for this experiment (server restarted?)")
    sim = sim_clock.get_run(exp_id)
    exp = store.get(exp_id)
    return {
        "experiment_id": exp_id,
        "status": run.status,
        "scenario": run.scenario,
        "error": run.error,
        "log": list(run.log),
        "decisions": list(run.decisions),
        "real_signups": list(run.real_signups),
        "sim": sim_clock.status(sim) if sim else None,
        "experiment": ({"status": exp.status, "winner": exp.winner,
                        "stopped_arms": list(exp.stopped_arms)} if exp else None),
    }
