"""Local SQLite persistence: a JSON document store plus an append-only event table.

Every in-memory object (experiments, Save Agent log, memory, PayPal subscription mapping) is
written through here and loaded back at startup, so a restart never wipes the workspace, with
no database server needed. Path comes from PLANLAB_DB (":memory:" in tests).

When Supabase is configured (DATABASE_URL, see `app.db`), Supabase is the source of truth for
experiments, arms, events and PayPal subscriptions; SQLite then keeps only what Supabase has
no tables for (Save Agent log and rules, experiment memory).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parent.parent / "planlab.db"


def supabase_enabled() -> bool:
    """Same rule as app.db.enabled(), without importing psycopg or the store."""
    import sys
    if "pytest" in sys.modules:
        return False
    if os.getenv("PLANLAB_DB_DISABLED", "").lower() in ("1", "true", "yes"):
        return False
    return bool(os.getenv("DATABASE_URL"))


class DB:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.getenv("PLANLAB_DB") or str(_DEFAULT)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL") if self.path != ":memory:" else None
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS docs (kind TEXT, id TEXT, body TEXT, PRIMARY KEY (kind, id));
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                                               exp_id TEXT, body TEXT);
            CREATE INDEX IF NOT EXISTS events_exp ON events (exp_id);
        """)
        self._conn.commit()

    # ---- documents ----
    def put(self, kind: str, id: str, body: dict) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO docs VALUES (?, ?, ?)",
                               (kind, id, json.dumps(body, default=str)))
            self._conn.commit()

    def get(self, kind: str, id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT body FROM docs WHERE kind=? AND id=?",
                                     (kind, id)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT body FROM docs WHERE kind=? ORDER BY rowid",
                                      (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def delete(self, kind: str, id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM docs WHERE kind=? AND id=?", (kind, id))
            self._conn.commit()

    # ---- events ----
    def add_events(self, exp_id: str, bodies: list[str]) -> None:
        with self._lock:
            self._conn.executemany("INSERT INTO events (exp_id, body) VALUES (?, ?)",
                                   [(exp_id, b) for b in bodies])
            self._conn.commit()

    def clear_events(self, exp_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE exp_id=?", (exp_id,))
            self._conn.commit()

    def events(self, exp_id: str) -> list[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute(
                "SELECT body FROM events WHERE exp_id=? ORDER BY seq", (exp_id,))]

    def reset(self) -> None:
        with self._lock:
            self._conn.executescript("DELETE FROM docs; DELETE FROM events;")
            self._conn.commit()


db = DB()
