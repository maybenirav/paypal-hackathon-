"""In-memory experiment store (Person A owns the real DB; same shapes apply)."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

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


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._exps: dict[str, Experiment] = {}

    def create(self, goal: str, arms: list[Arm], source: str) -> Experiment:
        exp = Experiment(uuid.uuid4().hex[:8], goal, arms, source)
        with self._lock:
            self._exps[exp.id] = exp
        return exp

    def get(self, exp_id: str) -> Experiment | None:
        return self._exps.get(exp_id)

    def list(self) -> list[Experiment]:
        return list(self._exps.values())

    def add_events(self, exp: Experiment, events: list[Event]) -> None:
        with self._lock:
            exp.events.extend(events)


store = Store()
