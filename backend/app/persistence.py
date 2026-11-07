"""Saves experiments to a local SQLite file, so a restart no longer loses them.

Without this, a restart would also orphan real PayPal plans and make webhooks unmatchable. The app
still works from memory; after each change the new state is written here, appending only new events.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime

from . import config
from .engine.guardrails import GuardrailConfig
from .events import Event
from .memory import MemoryRecord, memory
from .save_agent import Offer, SaveAction, SaveRules, SubscriberContext, save_log
from .schemas import Arm
from .store import Experiment, store

log = logging.getLogger("planlab.persistence")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (experiment_id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,
                                   PRIMARY KEY (experiment_id, seq));
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _encode(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"can't save {type(value).__name__}")


def _dumps(value) -> str:
    return json.dumps(value, default=_encode)


def _experiment_to_dict(exp: Experiment) -> dict:
    return {"id": exp.id, "goal": exp.goal, "arms": [a.model_dump() for a in exp.arms], "source": exp.source,
            "status": exp.status, "stopped_arms": exp.stopped_arms, "winner": exp.winner,
            "guardrails": asdict(exp.guardrails), "rollout": exp.rollout, "last_decision": exp.last_decision,
            "scenario": exp.scenario, "validation": exp.validation, "assignments": exp.assignments,
            "subscriptions": exp.subscriptions, "sales": exp.sales, "created_at": exp.created_at,
            "events_version": exp.events_version, "stops": exp.stops, "auto_exempt": exp.auto_exempt,
            "last_auto_check": exp.last_auto_check}


def _experiment_from_dict(d: dict, events: list[Event]) -> Experiment:
    return Experiment(
        id=d["id"], goal=d["goal"], arms=[Arm(**a) for a in d["arms"]], source=d["source"], status=d["status"],
        stopped_arms=d["stopped_arms"], winner=d["winner"], events=events,
        guardrails=GuardrailConfig(**d["guardrails"]), rollout=d["rollout"], last_decision=d["last_decision"],
        scenario=d["scenario"], validation=d["validation"], assignments=d["assignments"],
        subscriptions=d["subscriptions"], sales=d["sales"], created_at=datetime.fromisoformat(d["created_at"]),
        events_version=d["events_version"], stops=d.get("stops", {}), auto_exempt=d.get("auto_exempt", []),
        last_auto_check=datetime.fromisoformat(d["last_auto_check"]) if d.get("last_auto_check") else None)


def _action_to_dict(a: SaveAction) -> dict:
    return {"id": a.id, "ts": a.ts, "context": a.context.model_dump(), "offer": a.offer.model_dump(),
            "status": a.status, "result": a.result, "overridden_by": a.overridden_by,
            "override_note": a.override_note}


def _action_from_dict(d: dict) -> SaveAction:
    return SaveAction(d["id"], datetime.fromisoformat(d["ts"]), SubscriberContext(**d["context"]),
                      Offer(**d["offer"]), d["status"], d["result"], d["overridden_by"], d["override_note"])


class Persistence:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._saved: dict[str, tuple[int, int]] = {}    # experiment id -> (events version, events saved)
        if not path:
            return
        try:
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            log.warning("Can't open %s, so data won't survive a restart: %s", path, exc)
            self._conn = None

    @property
    def enabled(self) -> bool:
        return self._conn is not None

    def load(self) -> None:
        if not self._conn:
            return
        with self._lock:
            for exp_id, data in self._conn.execute("SELECT id, data FROM experiments").fetchall():
                rows = self._conn.execute("SELECT data FROM events WHERE experiment_id = ? ORDER BY seq",
                                          (exp_id,)).fetchall()
                exp = _experiment_from_dict(json.loads(data), [Event.model_validate_json(r[0]) for r in rows])
                store._exps[exp.id] = exp
                self._saved[exp.id] = (exp.events_version, len(rows))
            state = dict(self._conn.execute("SELECT key, value FROM state").fetchall())
        if "memory" in state:
            memory.records = [MemoryRecord(**{**r, "ts": datetime.fromisoformat(r["ts"])})
                              for r in json.loads(state["memory"])]
        if "save_rules" in state:
            save_log.rules = SaveRules(**json.loads(state["save_rules"]))
        if "save_actions" in state:
            save_log.actions = [_action_from_dict(a) for a in json.loads(state["save_actions"])]
        if "seen_webhooks" in state:
            store.seen_webhooks = dict.fromkeys(json.loads(state["seen_webhooks"]))

    def save(self) -> None:
        if not self._conn:
            return
        with self._lock:
            try:
                with self._conn:
                    self._save_experiments()
                    self._conn.executemany("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", [
                        ("memory", _dumps([asdict(r) for r in memory.records])),
                        ("save_rules", _dumps(save_log.rules.model_dump())),
                        ("save_actions", _dumps([_action_to_dict(a) for a in save_log.actions])),
                        ("seen_webhooks", _dumps(list(store.seen_webhooks))),
                    ])
            except sqlite3.Error as exc:
                log.warning("Couldn't save to %s: %s", self.path, exc)

    def _save_experiments(self) -> None:
        current = store.list()
        for exp in current:
            self._conn.execute("INSERT OR REPLACE INTO experiments (id, data) VALUES (?, ?)",
                               (exp.id, _dumps(_experiment_to_dict(exp))))
            version, count = self._saved.get(exp.id, (None, 0))
            if version != exp.events_version:
                self._conn.execute("DELETE FROM events WHERE experiment_id = ?", (exp.id,))
                count = 0
            total = len(exp.events)
            if total > count:
                self._conn.executemany(
                    "INSERT INTO events (experiment_id, seq, data) VALUES (?, ?, ?)",
                    [(exp.id, i, exp.events[i].model_dump_json()) for i in range(count, total)])
            self._saved[exp.id] = (exp.events_version, total)
        for gone in set(self._saved) - {e.id for e in current}:
            self._conn.execute("DELETE FROM experiments WHERE id = ?", (gone,))
            self._conn.execute("DELETE FROM events WHERE experiment_id = ?", (gone,))
            del self._saved[gone]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


persistence = Persistence(config.PLANLAB_DB)
