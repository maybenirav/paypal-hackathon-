"""Person D: tests for the simulated clock timeline."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import sim_clock, simulate
from app.main import app
from app.schemas import Arm
from app.store import store

client = TestClient(app)


@pytest.fixture(autouse=True)
def restore_start(monkeypatch):
    """sim_clock moves simulate.START to today; put it back after each test."""
    monkeypatch.setattr(simulate, "START", simulate.START)
    monkeypatch.setattr(simulate, "DAYS", simulate.DAYS)


def gemini_style_arms():
    """Arm names that are NOT the fallback names - like Gemini might invent."""
    return [Arm(arm_id="control", name="Control", price=10, is_baseline=True),
            Arm(arm_id="yearly_saver", name="Yearly saver", price=100, interval="YEAR"),
            Arm(arm_id="trial_week", name="Week trial", price=10, trial_days=7)]


def run(scenario, seed=42):
    start = sim_clock.anchor_to_today()
    exp = store.create("test goal", gemini_style_arms(), "test")
    store.add_events(exp, sim_clock.build_timeline(exp.id, exp.arms, scenario, seed, start))
    return exp


def test_timeline_is_repeatable():
    start = sim_clock.anchor_to_today()
    arms = gemini_style_arms()
    a = sim_clock.build_timeline("x", arms, "guardrail", 42, start)
    b = sim_clock.build_timeline("x", arms, "guardrail", 42, start)
    assert a == b


def test_timeline_starts_today():
    start = sim_clock.anchor_to_today()
    events = sim_clock.build_timeline("x", gemini_style_arms(), "winner", 42, start)
    assert start.date() == datetime.now(timezone.utc).date()
    end = start + timedelta(days=sim_clock.SIM_TOTAL_DAYS + 1)
    assert all(start <= e.ts <= end for e in events)
    assert all(e.source == "simulated" for e in events)


def test_winner_works_with_any_arm_names():
    exp = run("winner")
    d = client.post(f"/experiments/{exp.id}/decide").json()
    assert d["action"] == "winner" and d["arm_id"] == "yearly_saver"


def test_guardrail_works_with_any_arm_names():
    exp = run("guardrail")
    d = client.post(f"/experiments/{exp.id}/decide").json()
    assert d["action"] == "stop_arm" and d["arm_id"] == "trial_week"
