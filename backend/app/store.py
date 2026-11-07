"""Experiment store. Lives in memory while running; persistence.py saves it to disk."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .engine.guardrails import GuardrailConfig
from .events import Event, utcnow
from .schemas import Arm

MAX_SEEN_WEBHOOKS = 10_000


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
    last_decision: dict | None = None
    scenario: str | None = None        # set while the data comes from a seeded demo scenario
    validation: list[dict] = field(default_factory=list)
    assignments: dict[str, str] = field(default_factory=dict)      # visitor id -> arm id
    subscriptions: dict[str, dict] = field(default_factory=dict)   # PayPal subscription id -> details
    sales: dict[str, str] = field(default_factory=dict)            # PayPal sale id -> subscription id
    created_at: datetime = field(default_factory=utcnow)
    events_version: int = 0            # goes up when events are replaced rather than added to
    stops: dict[str, dict] = field(default_factory=dict)      # arm id -> day, at, reasons, automatic
    auto_exempt: list[str] = field(default_factory=list)      # resumed by the merchant, so left alone
    last_auto_check: datetime | None = None


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._exps: dict[str, Experiment] = {}
        self.seen_webhooks: dict[str, None] = {}

    def create(self, goal: str, arms: list[Arm], source: str,
               validation: list[dict] | None = None) -> Experiment:
        exp = Experiment(uuid.uuid4().hex[:8], goal, arms, source, validation=validation or [])
        with self._lock:
            self._exps[exp.id] = exp
        return exp

    def get(self, exp_id: str) -> Experiment | None:
        return self._exps.get(exp_id)

    def list(self) -> list[Experiment]:
        return list(self._exps.values())

    def delete(self, exp_id: str) -> bool:
        with self._lock:
            return self._exps.pop(exp_id, None) is not None

    def add_events(self, exp: Experiment, events: list[Event]) -> None:
        with self._lock:
            exp.events.extend(events)

    def replace_events(self, exp: Experiment, events: list[Event]) -> None:
        with self._lock:
            exp.events = list(events)
            exp.events_version += 1

    def find_subscription(self, subscription_id: str) -> tuple[Experiment, dict] | None:
        for exp in self.list():
            record = exp.subscriptions.get(subscription_id)
            if record:
                return exp, record
        return None

    def find_sale(self, sale_id: str) -> tuple[Experiment, str] | None:
        for exp in self.list():
            subscription_id = exp.sales.get(sale_id)
            if subscription_id:
                return exp, subscription_id
        return None

    def mark_webhook(self, event_id: str) -> None:
        with self._lock:
            self.seen_webhooks[event_id] = None
            while len(self.seen_webhooks) > MAX_SEEN_WEBHOOKS:
                self.seen_webhooks.pop(next(iter(self.seen_webhooks)))


store = Store()
