"""Experiment store: in-memory cache, written through to SQLite (`app.local_db`).

With Supabase configured, `app.db` loads and saves experiments instead and SQLite is skipped.

Code that changes an experiment in place (status, stopped arms, plan ids, rollout, guardrails)
must call `store.save(exp)` afterwards. Events are appended with `add_events` and replaced
with `replace_events`.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .local_db import db, supabase_enabled
from .engine.guardrails import GuardrailConfig
from .events import Event
from .schemas import Arm


@dataclass
class Experiment:
    id: str
    goal: str
    arms: list[Arm]
    source: str
    status: str = "running"            # running | stopped_arm | winner
    stopped_arms: list[str] = field(default_factory=list)
    winner: str | None = None
    events: list[Event] = field(default_factory=list)
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)
    rollout: dict | None = None
    monthly_visitors: int | None = None
    rejected_arms: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_doc(self) -> dict:
        return {"id": self.id, "goal": self.goal, "source": self.source, "status": self.status,
                "arms": [a.model_dump() for a in self.arms], "stopped_arms": self.stopped_arms,
                "winner": self.winner, "guardrails": asdict(self.guardrails),
                "rollout": self.rollout, "monthly_visitors": self.monthly_visitors,
                "rejected_arms": self.rejected_arms, "created_at": self.created_at}

    @classmethod
    def from_doc(cls, d: dict, events: list[Event]) -> "Experiment":
        return cls(d["id"], d["goal"], [Arm(**a) for a in d["arms"]], d["source"], d["status"],
                   d["stopped_arms"], d["winner"], events, GuardrailConfig(**d["guardrails"]),
                   d.get("rollout"), d.get("monthly_visitors"), d.get("rejected_arms", []),
                   d.get("created_at") or datetime.utcnow().isoformat())


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._exps: dict[str, Experiment] = {}
        self.load()

    def load(self) -> None:
        self._exps = {}
        if supabase_enabled():
            return  # app.db loads experiments from Supabase on the first request
        for d in db.all("experiment"):
            events = [Event.model_validate_json(b) for b in db.events(d["id"])]
            self._exps[d["id"]] = Experiment.from_doc(d, events)

    def create(self, goal: str, arms: list[Arm], source: str, **extra) -> Experiment:
        exp = Experiment(uuid.uuid4().hex[:8], goal, arms, source, **extra)
        with self._lock:
            self._exps[exp.id] = exp
        self.save(exp)
        return exp

    def save(self, exp: Experiment) -> None:
        if not supabase_enabled():  # app.db's middleware saves changed experiments
            db.put("experiment", exp.id, exp.to_doc())

    def get(self, exp_id: str) -> Experiment | None:
        return self._exps.get(exp_id)

    def list(self) -> list[Experiment]:
        return list(self._exps.values())

    def add_events(self, exp: Experiment, events: list[Event]) -> None:
        with self._lock:
            exp.events.extend(events)
        if not supabase_enabled():
            db.add_events(exp.id, [e.model_dump_json() for e in events])

    def replace_events(self, exp: Experiment, events: list[Event]) -> None:
        with self._lock:
            exp.events[:] = list(events)
        if not supabase_enabled():
            db.clear_events(exp.id)
            db.add_events(exp.id, [e.model_dump_json() for e in events])

    def reset(self) -> None:
        """Drop everything (tests and the demo reset button)."""
        db.reset()
        with self._lock:
            self._exps.clear()


store = Store()
