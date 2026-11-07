"""Saves the experiment store to Supabase (Postgres) and reloads it on startup.

The rest of the app keeps using `store` exactly as before. After every request that
can change data (anything except GET), changed experiments are written to the database.
If DATABASE_URL is not set, or tests are running, everything simply stays in memory.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import asdict, fields
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from app import config  # noqa: F401  (loads .env)
from app.engine.guardrails import GuardrailConfig
from app.events import Event
from app.schemas import Arm
from app.store import Experiment, store

log = logging.getLogger("planlab.db")

_lock = threading.RLock()
_conn = None

# What we last wrote per experiment, so we only save what changed
_saved: dict[str, dict] = {}
_saved_subs: dict[str, tuple] = {}


def database_url() -> str:
    return os.getenv("DATABASE_URL", "")


def enabled() -> bool:
    if "pytest" in sys.modules:  # tests never touch the real database
        return False
    if os.getenv("PLANLAB_DB_DISABLED", "").lower() in ("1", "true", "yes"):
        return False
    return bool(database_url())


# ---------- connection ----------

def _connect():
    import psycopg
    return psycopg.connect(database_url(), autocommit=True, prepare_threshold=None,
                           connect_timeout=10)


def run(fn):
    """Run fn(connection) under a lock, reconnecting once if the connection dropped."""
    import psycopg
    global _conn
    with _lock:
        if _conn is None or _conn.closed or _conn.broken:
            _conn = _connect()
        try:
            return fn(_conn)
        except psycopg.OperationalError:
            _conn = _connect()
            return fn(_conn)


# ---------- helpers ----------

def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _first_marker(exp: Experiment):
    return id(exp.events[0]) if exp.events else None


def _fingerprint(exp: Experiment) -> tuple:
    return (exp.goal, exp.source, exp.status, exp.winner, tuple(exp.stopped_arms),
            len(exp.events), _first_marker(exp),
            tuple((a.arm_id, a.plan_id) for a in exp.arms),
            json.dumps(asdict(exp.guardrails), sort_keys=True, default=str),
            json.dumps(exp.rollout, sort_keys=True, default=str))


# ---------- save ----------

def _save_experiment(c, exp: Experiment) -> None:
    from psycopg.types.json import Jsonb

    prev = _saved.get(exp.id)
    first = _first_marker(exp)
    # Events were cleared/regenerated (e.g. /demo) -> rewrite them all
    rewrite = prev is None or prev["first"] != first or prev["n"] > len(exp.events)

    with c.transaction():
        c.execute(
            """insert into experiments (id, goal, source, status, winner, stopped_arms,
                                        guardrails, rollout, updated_at)
               values (%s, %s, %s, %s, %s, %s, %s, %s, now())
               on conflict (id) do update set
                 goal = excluded.goal, source = excluded.source, status = excluded.status,
                 winner = excluded.winner, stopped_arms = excluded.stopped_arms,
                 guardrails = excluded.guardrails, rollout = excluded.rollout,
                 updated_at = now()""",
            (exp.id, exp.goal, exp.source, exp.status, exp.winner,
             Jsonb(list(exp.stopped_arms)), Jsonb(_jsonable(asdict(exp.guardrails))),
             Jsonb(_jsonable(exp.rollout)) if exp.rollout is not None else None))

        with c.cursor() as cur:
            cur.executemany(
                """insert into arms (experiment_id, arm_id, name, price, currency, interval,
                                     trial_days, setup_fee, is_baseline, reason, plan_id, position)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (experiment_id, arm_id) do update set
                     name = excluded.name, price = excluded.price, currency = excluded.currency,
                     interval = excluded.interval, trial_days = excluded.trial_days,
                     setup_fee = excluded.setup_fee, is_baseline = excluded.is_baseline,
                     reason = excluded.reason, plan_id = excluded.plan_id,
                     position = excluded.position""",
                [(exp.id, a.arm_id, a.name, a.price, a.currency, a.interval, a.trial_days,
                  a.setup_fee, a.is_baseline, a.reason, a.plan_id, i)
                 for i, a in enumerate(exp.arms)])

            if rewrite:
                cur.execute("delete from events where experiment_id = %s", (exp.id,))
                new_events = exp.events
            else:
                new_events = exp.events[prev["n"]:]
            if new_events:
                with cur.copy("copy events (experiment_id, arm_id, subscriber_id, type, ts, "
                              "amount, source) from stdin") as cp:
                    for e in new_events:
                        cp.write_row((exp.id, e.arm_id, e.subscriber_id, e.type, _aware(e.ts),
                                      e.amount, e.source))

    _saved[exp.id] = {"fp": _fingerprint(exp), "n": len(exp.events), "first": first}


def _save_paypal(c) -> None:
    import app.paypal_experiments as pe

    rows = []
    for sub_id, (exp_id, arm_id) in list(pe._subs.items()):
        if exp_id not in _saved:  # experiment must exist in the database first
            continue
        meta = pe._sub_meta.get(sub_id, {})
        key = (exp_id, arm_id, meta.get("status"), meta.get("approve_url"))
        if _saved_subs.get(sub_id) != key:
            rows.append((sub_id, exp_id, arm_id, meta.get("status"), meta.get("approve_url"), key))
    if rows:
        with c.cursor() as cur:
            cur.executemany(
                """insert into paypal_subscriptions (subscription_id, experiment_id, arm_id,
                                                     status, approve_url, updated_at)
                   values (%s, %s, %s, %s, %s, now())
                   on conflict (subscription_id) do update set
                     status = coalesce(excluded.status, paypal_subscriptions.status),
                     approve_url = coalesce(excluded.approve_url, paypal_subscriptions.approve_url),
                     updated_at = now()""",
                [r[:5] for r in rows])
        for r in rows:
            _saved_subs[r[0]] = r[5]

    if pe._webhook_log:
        from psycopg.types.json import Jsonb
        batch = []
        while pe._webhook_log:
            batch.append(pe._webhook_log.pop(0))
        with c.cursor() as cur:
            cur.executemany(
                """insert into paypal_webhook_log (webhook_id, event_type, result)
                   values (%s, %s, %s) on conflict (webhook_id) do nothing""",
                [(wid, etype or "", Jsonb(_jsonable(result))) for wid, etype, result in batch])


def save_dirty() -> None:
    """Write every experiment that changed since it was last saved."""
    if not enabled():
        return

    def work(c):
        for exp in store.list():
            prev = _saved.get(exp.id)
            if prev is None or prev["fp"] != _fingerprint(exp):
                _save_experiment(c, exp)
        _save_paypal(c)

    try:
        run(work)
    except Exception:
        log.exception("Saving to the database failed")


def save_now(exp: Experiment) -> None:
    """Make sure one experiment is in the database (needed before linking rows to it)."""
    if enabled() and (exp.id not in _saved or _saved[exp.id]["fp"] != _fingerprint(exp)):
        run(lambda c: _save_experiment(c, exp))


# ---------- load ----------

def load_all() -> int:
    """Load every experiment (with arms and events) from the database into the store."""
    from psycopg.rows import dict_row
    import app.paypal_experiments as pe

    gr_fields = {f.name for f in fields(GuardrailConfig)}

    def work(c):
        with c.cursor(row_factory=dict_row) as cur:
            exps = cur.execute("select * from experiments order by created_at").fetchall()
            arms = cur.execute("select * from arms order by experiment_id, position, arm_id").fetchall()
            evs = cur.execute("select experiment_id, arm_id, subscriber_id, type, ts, amount, source "
                              "from events order by experiment_id, id").fetchall()
            subs = cur.execute("select * from paypal_subscriptions").fetchall()
            hooks = cur.execute("select webhook_id from paypal_webhook_log").fetchall()
        return exps, arms, evs, subs, hooks

    exps, arms, evs, subs, hooks = run(work)

    arms_by: dict[str, list[Arm]] = {}
    for a in arms:
        arms_by.setdefault(a["experiment_id"], []).append(Arm(
            arm_id=a["arm_id"], name=a["name"], price=float(a["price"]), currency=a["currency"],
            interval=a["interval"], trial_days=a["trial_days"], setup_fee=float(a["setup_fee"]),
            is_baseline=a["is_baseline"], reason=a["reason"], plan_id=a["plan_id"]))

    events_by: dict[str, list[Event]] = {}
    for e in evs:
        events_by.setdefault(e["experiment_id"], []).append(Event(
            experiment_id=e["experiment_id"], arm_id=e["arm_id"], subscriber_id=e["subscriber_id"],
            type=e["type"], ts=pe._to_store_tz(e["ts"]), amount=float(e["amount"]),
            source=e["source"]))

    loaded = 0
    for row in exps:
        guardrails = GuardrailConfig(**{k: v for k, v in (row["guardrails"] or {}).items()
                                        if k in gr_fields})
        exp = Experiment(id=row["id"], goal=row["goal"], arms=arms_by.get(row["id"], []),
                         source=row["source"], status=row["status"],
                         stopped_arms=list(row["stopped_arms"] or []), winner=row["winner"],
                         events=events_by.get(row["id"], []), guardrails=guardrails,
                         rollout=row["rollout"])
        with store._lock:
            store._exps[exp.id] = exp
        _saved[exp.id] = {"fp": _fingerprint(exp), "n": len(exp.events),
                          "first": _first_marker(exp)}
        loaded += 1

    for s in subs:
        pe._subs[s["subscription_id"]] = (s["experiment_id"], s["arm_id"])
        pe._sub_meta[s["subscription_id"]] = {"status": s["status"], "approve_url": s["approve_url"]}
        _saved_subs[s["subscription_id"]] = (s["experiment_id"], s["arm_id"], s["status"],
                                             s["approve_url"])
    pe._seen_webhooks.update(h["webhook_id"] for h in hooks)
    return loaded


# ---------- wiring ----------

_loaded = False


def ensure_loaded() -> None:
    """Load the database into memory once (on the first request after a start/restart)."""
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        _loaded = True
        if not enabled():
            log.warning("DATABASE_URL not set - experiments are kept in memory only")
            return
        try:
            n = load_all()
            log.warning("Loaded %d experiment(s) from the database", n)
        except Exception:
            log.exception("Loading from the database failed - starting with an empty store")


def init_app(app) -> None:
    """Call once from main.py: loads data on the first request and saves after every change."""

    @app.middleware("http")
    async def _persist_changes(request, call_next):
        if not _loaded:
            await run_in_threadpool(ensure_loaded)
        response = await call_next(request)
        if request.method != "GET" and enabled():
            await run_in_threadpool(save_dirty)
        return response
