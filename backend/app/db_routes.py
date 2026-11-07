"""Database-backed endpoints for the Bryntum Gantt (Person E) and AG Grid / AG Studio (Person C).

Bryntum Gantt
  GET    /experiments/{id}/gantt             timeline in Bryntum's load format
  POST   /experiments/{id}/gantt/generate    (re)build the timeline from the experiment
  PATCH  /gantt/tasks/{task_id}              edit one task (dragged dates, status, note)

AG Grid / AG Studio
  GET/POST        /grid-views                 saved column / filter / sort setups
  PUT/DELETE      /grid-views/{id}
  GET/POST        /dashboards                 AG Studio dashboard layouts
  PUT/DELETE      /dashboards/{id}
  GET             /experiments/{id}/snapshots          metric history for charts
  POST            /experiments/{id}/snapshots?day=N    capture one day
  POST            /experiments/{id}/snapshots/backfill capture every N days up to the end

  GET             /db/health
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app.store import Experiment, store

router = APIRouter(tags=["database"])


# ---------- helpers ----------

def _require_db() -> None:
    if not db.enabled():
        raise HTTPException(503, "Database not configured (DATABASE_URL missing in .env)")


def _exp(exp_id: str) -> Experiment:
    _require_db()
    exp = store.get(exp_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    db.save_now(exp)  # rows below reference it, so it must exist in the database
    return exp


def _clean(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _row(r: dict) -> dict:
    return {k: _clean(v) for k, v in r.items()}


def _fetch(sql: str, params=()) -> list[dict]:
    from psycopg.rows import dict_row

    def work(c):
        with c.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []
    return [_row(r) for r in db.run(work)]


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


@router.get("/db/health")
def db_health():
    if not db.enabled():
        return {"database": "disabled"}
    try:
        n = _fetch("select count(*) as n from experiments")[0]["n"]
        return {"database": "connected", "experiments": n}
    except Exception as e:  # noqa: BLE001
        return {"database": "error", "detail": str(e)[:300]}


# ---------- Bryntum Gantt ----------

def _gantt_payload(exp_id: str) -> dict:
    tasks = _fetch("select * from gantt_tasks where experiment_id = %s "
                   "order by parent_id nulls first, ordinal, id", (exp_id,))
    deps = _fetch("select id, from_task, to_task, type from gantt_dependencies "
                  "where experiment_id = %s order by id", (exp_id,))

    nodes = {}
    for t in tasks:
        nodes[t["id"]] = {
            "id": t["id"], "name": t["name"], "startDate": t["start_date"],
            "endDate": t["end_date"], "duration": t["duration"], "durationUnit": "day",
            "percentDone": t["percent_done"], "manuallyScheduled": True,
            "kind": t["kind"], "status": t["status"], "note": t["note"],
        }
    roots = []
    for t in tasks:
        node = nodes[t["id"]]
        parent = nodes.get(t["parent_id"])
        if parent is None:
            roots.append(node)
        else:
            parent.setdefault("children", []).append(node)
            parent["expanded"] = True

    return {
        "success": True,
        "tasks": {"rows": roots},
        "dependencies": {"rows": [{"id": d["id"], "fromTask": d["from_task"],
                                    "toTask": d["to_task"], "type": d["type"]} for d in deps]},
    }


@router.get("/experiments/{exp_id}/gantt")
def gantt_get(exp_id: str):
    _exp(exp_id)
    return _gantt_payload(exp_id)


@router.post("/experiments/{exp_id}/gantt/generate")
def gantt_generate(exp_id: str, start: datetime | None = None):
    """Build the timeline: launch -> guardrail checkpoints -> decision. Replaces any old one."""
    exp = _exp(exp_id)
    total = int(os.getenv("SIM_TOTAL_DAYS") or 60)
    step = int(os.getenv("GUARDRAIL_CHECKPOINT_DAYS") or 14)
    start = (start or datetime.now(timezone.utc))
    start = (start if start.tzinfo else start.replace(tzinfo=timezone.utc)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    decided = exp.status == "winner"

    steps = [("Launch arms as PayPal plans", "launch", 0, "done")]
    steps += [(f"Guardrail checkpoint (day {d})", "checkpoint", d, "pending")
              for d in range(step, total, step)]
    steps += [("Decision: pick the winning price", "decision", total,
               "done" if decided else "pending")]
    if exp.stopped_arms:
        steps[1] = (steps[1][0], steps[1][1], steps[1][2], "breached")

    def work(c):
        with c.transaction():
            c.execute("delete from gantt_tasks where experiment_id = %s", (exp.id,))
            parent_id = c.execute(
                """insert into gantt_tasks (experiment_id, name, kind, start_date, end_date,
                                            duration, percent_done, status, ordinal)
                   values (%s, %s, 'experiment', %s, %s, %s, %s, %s, 0) returning id""",
                (exp.id, f"Experiment: {exp.goal}"[:200], start, start + timedelta(days=total),
                 total, 100 if decided else 0, "done" if decided else "running")).fetchone()[0]
            ids = []
            for i, (name, kind, day, status) in enumerate(steps):
                when = start + timedelta(days=day)
                ids.append(c.execute(
                    """insert into gantt_tasks (experiment_id, parent_id, name, kind, start_date,
                                                end_date, duration, percent_done, status, ordinal)
                       values (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s) returning id""",
                    (exp.id, parent_id, name, kind, when, when,
                     100 if status == "done" else 0, status, i)).fetchone()[0])
            with c.cursor() as cur:
                cur.executemany(
                    "insert into gantt_dependencies (experiment_id, from_task, to_task, type) "
                    "values (%s, %s, %s, 2)",
                    [(exp.id, a, b) for a, b in zip(ids, ids[1:])])

    db.run(work)
    return _gantt_payload(exp.id)


class GanttTaskUpdate(BaseModel):
    name: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    duration: float | None = None
    percent_done: float | None = None
    status: str | None = None
    note: str | None = None


@router.patch("/gantt/tasks/{task_id}")
def gantt_update_task(task_id: int, req: GanttTaskUpdate):
    _require_db()
    changes = req.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "nothing to update")
    cols = ", ".join(f"{k} = %s" for k in changes)  # keys come from the model, not the user
    rows = _fetch(f"update gantt_tasks set {cols}, updated_at = now() where id = %s returning *",
                  (*changes.values(), task_id))
    if not rows:
        raise HTTPException(404, "task not found")
    return rows[0]


# ---------- AG Grid: saved views ----------

class GridView(BaseModel):
    grid_id: str
    name: str
    experiment_id: str | None = None
    column_state: list = []
    filter_model: dict = {}
    sort_model: list = []
    is_default: bool = False


def _check_exp_ref(exp_id: str | None) -> None:
    if exp_id is not None:
        _exp(exp_id)


@router.get("/grid-views")
def grid_views_list(grid_id: str | None = None, experiment_id: str | None = None):
    _require_db()
    return _fetch(
        """select * from grid_views
           where (%(g)s::text is null or grid_id = %(g)s)
             and (%(e)s::text is null or experiment_id = %(e)s or experiment_id is null)
           order by is_default desc, updated_at desc""",
        {"g": grid_id, "e": experiment_id})


@router.post("/grid-views")
def grid_views_create(view: GridView):
    _require_db()
    _check_exp_ref(view.experiment_id)
    from psycopg.types.json import Jsonb
    return _fetch(
        """insert into grid_views (grid_id, name, experiment_id, column_state, filter_model,
                                   sort_model, is_default)
           values (%s, %s, %s, %s, %s, %s, %s) returning *""",
        (view.grid_id, view.name, view.experiment_id, Jsonb(view.column_state),
         Jsonb(view.filter_model), Jsonb(view.sort_model), view.is_default))[0]


@router.put("/grid-views/{view_id}")
def grid_views_update(view_id: int, view: GridView):
    _require_db()
    _check_exp_ref(view.experiment_id)
    from psycopg.types.json import Jsonb
    rows = _fetch(
        """update grid_views set grid_id = %s, name = %s, experiment_id = %s, column_state = %s,
                                 filter_model = %s, sort_model = %s, is_default = %s,
                                 updated_at = now()
           where id = %s returning *""",
        (view.grid_id, view.name, view.experiment_id, Jsonb(view.column_state),
         Jsonb(view.filter_model), Jsonb(view.sort_model), view.is_default, view_id))
    if not rows:
        raise HTTPException(404, "grid view not found")
    return rows[0]


@router.delete("/grid-views/{view_id}")
def grid_views_delete(view_id: int):
    _require_db()
    if not _fetch("delete from grid_views where id = %s returning id", (view_id,)):
        raise HTTPException(404, "grid view not found")
    return {"deleted": view_id}


# ---------- AG Studio: dashboards ----------

class Dashboard(BaseModel):
    name: str
    experiment_id: str | None = None
    layout: dict = {}
    created_by: str = "user"   # user | ai
    prompt: str | None = None


@router.get("/dashboards")
def dashboards_list(experiment_id: str | None = None):
    _require_db()
    return _fetch(
        """select * from dashboards
           where (%(e)s::text is null or experiment_id = %(e)s or experiment_id is null)
           order by updated_at desc""", {"e": experiment_id})


@router.post("/dashboards")
def dashboards_create(d: Dashboard):
    _require_db()
    _check_exp_ref(d.experiment_id)
    from psycopg.types.json import Jsonb
    return _fetch(
        """insert into dashboards (name, experiment_id, layout, created_by, prompt)
           values (%s, %s, %s, %s, %s) returning *""",
        (d.name, d.experiment_id, Jsonb(d.layout), d.created_by, d.prompt))[0]


@router.put("/dashboards/{dashboard_id}")
def dashboards_update(dashboard_id: int, d: Dashboard):
    _require_db()
    _check_exp_ref(d.experiment_id)
    from psycopg.types.json import Jsonb
    rows = _fetch(
        """update dashboards set name = %s, experiment_id = %s, layout = %s, created_by = %s,
                                 prompt = %s, updated_at = now()
           where id = %s returning *""",
        (d.name, d.experiment_id, Jsonb(d.layout), d.created_by, d.prompt, dashboard_id))
    if not rows:
        raise HTTPException(404, "dashboard not found")
    return rows[0]


@router.delete("/dashboards/{dashboard_id}")
def dashboards_delete(dashboard_id: int):
    _require_db()
    if not _fetch("delete from dashboards where id = %s returning id", (dashboard_id,)):
        raise HTTPException(404, "dashboard not found")
    return {"deleted": dashboard_id}


# ---------- metric snapshots (chart history) ----------

def _capture(exp: Experiment, day: int) -> int:
    from app.main import _rows  # imported here to avoid a circular import
    _, rows, _ = _rows(exp, day)
    values = [(exp.id, r["arm_id"], day, int(r.get("visitors") or 0), int(r.get("signups") or 0),
               int(r.get("churned") or 0), _num(r.get("mrr")) or 0, _num(r.get("conversion")),
               _num(r.get("churn")), _num(r.get("projected_ltv")), _num(r.get("prob_best")))
              for r in rows]

    def work(c):
        with c.cursor() as cur:
            cur.executemany(
                """insert into metric_snapshots (experiment_id, arm_id, day, visitors, signups,
                                                 churned, mrr, conversion, churn, ltv, prob_best)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (experiment_id, arm_id, day) do update set
                     visitors = excluded.visitors, signups = excluded.signups,
                     churned = excluded.churned, mrr = excluded.mrr,
                     conversion = excluded.conversion, churn = excluded.churn,
                     ltv = excluded.ltv, prob_best = excluded.prob_best, captured_at = now()""",
                values)
    db.run(work)
    return len(values)


@router.get("/experiments/{exp_id}/snapshots")
def snapshots_list(exp_id: str):
    _exp(exp_id)
    return _fetch("select * from metric_snapshots where experiment_id = %s "
                  "order by day, arm_id", (exp_id,))


@router.post("/experiments/{exp_id}/snapshots")
def snapshots_capture(exp_id: str, day: int):
    exp = _exp(exp_id)
    return {"experiment_id": exp_id, "day": day, "rows": _capture(exp, day)}


@router.post("/experiments/{exp_id}/snapshots/backfill")
def snapshots_backfill(exp_id: str, every: int = 7):
    """Capture day 0, every N days, and the final day - fills the history charts in one go."""
    exp = _exp(exp_id)
    total = int(os.getenv("SIM_TOTAL_DAYS") or 60)
    days = sorted(set(range(0, total + 1, max(1, every))) | {total})
    count = sum(_capture(exp, d) for d in days)
    return {"experiment_id": exp_id, "days": days, "rows": count}
