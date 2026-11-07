"""Automatic guardrail checks: stop a risky price without waiting for someone to run the analysis.

Always-on checks look at the data far more often than a person does, and every extra look is another
chance to be fooled by noise. In simulations with daily checks, the thresholds used for a manual
analysis wrongly stopped a healthy price in 24% of tests, so these are stricter (about 1%, and risky
prices were still all caught). Stops only change who new customers are offered, and can be undone.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Callable

from . import alerts, simulate
from .engine.analysis import monthly_churn
from .engine.decision import MIN_EXPOSURE_MONTHS
from .engine.guardrail import check_churn_rate
from .engine.guardrails import check_arm, projected_retention
from .events import utcnow
from .experiments import baseline_of, record_memory, record_stop, rolled_out_winner, snapshot, timeline_day, window
from .metrics import aggregate, to_arm_data
from .store import Experiment, store

log = logging.getLogger("planlab.watcher")

AUTO_CONFIDENCE = 0.99
AUTO_CHURN_ALPHA = 0.002
CHECK_SECONDS = int(os.getenv("GUARDRAIL_CHECK_SECONDS") or 900)


def find_breaches(exp: Experiment, as_of: datetime | None, until: datetime | None) -> dict[str, list[str]]:
    """Live challengers past a limit at this moment, with the reasons. The baseline is never paused."""
    cfg = exp.guardrails
    agg = aggregate(exp.arms, exp.events, as_of, until, with_retention=False)
    data = {d.arm_id: d for d in to_arm_data(exp.arms, agg)}
    base = data[baseline_of(exp.arms).arm_id]
    enough = lambda a: a.signups >= cfg.min_signups and a.exposure_months >= MIN_EXPOSURE_MONTHS   # noqa: E731
    found: dict[str, list[str]] = {}
    for arm_id, arm in data.items():
        if arm_id == base.arm_id or arm_id in exp.stopped_arms or arm_id in exp.auto_exempt:
            continue
        row = {**agg[arm_id], "retention_60": round(projected_retention(monthly_churn(arm)), 4)}
        reasons = [b.message for b in check_arm(row, cfg, AUTO_CONFIDENCE)]
        if enough(base) and enough(arm):
            g = check_churn_rate(base.churned, base.exposure_months, arm.churned, arm.exposure_months,
                                 cfg.churn_ratio, AUTO_CHURN_ALPHA)
            if g.triggered:
                reasons.insert(0, f"Monthly churn {g.churn_arm:.1%} vs baseline {g.churn_base:.1%} "
                                  f"({g.ratio:.2f}x > {cfg.churn_ratio:.2f}x, p={g.p_churn:.3f}).")
        if reasons:
            found[arm_id] = reasons
    return found


def replay_checks(exp: Experiment) -> None:
    """Run the daily checks across a simulated timeline, as they would have happened live.

    A stopped option gets no new customers, so anyone who would have arrived later is removed.
    People who had already signed up stay, and their cancellations and payments still count.
    """
    if not exp.guardrails.auto_stop:
        return
    for day in range(1, simulate.DAYS + 1):
        at = simulate.START + timedelta(days=day)
        for arm_id, reasons in find_breaches(exp, at, at).items():
            record_stop(exp, arm_id, " ".join(reasons), day, automatic=True)
            late = {e.subscriber_id for e in exp.events
                    if e.arm_id == arm_id and e.type == "VISIT" and e.ts > at}
            if late:
                store.replace_events(exp, [e for e in exp.events
                                           if e.arm_id != arm_id or e.subscriber_id not in late])
    if exp.stops:
        record_memory(exp, snapshot(exp).rows)


def check_experiment(exp: Experiment) -> list[str]:
    """Check an experiment as it stands now. Returns the options it stopped."""
    if not exp.guardrails.auto_stop or not exp.events or rolled_out_winner(exp):
        return []
    found = find_breaches(exp, None, window(exp, None)[1])
    day = timeline_day(exp)
    for arm_id, reasons in found.items():
        record_stop(exp, arm_id, " ".join(reasons), day, automatic=True)
        if day is None:     # real data. Simulated runs are demos, so they don't alert anyone
            alerts.queue(exp, arm_id)
        log.warning("Stopped %s in experiment %s: %s", arm_id, exp.id, " ".join(reasons))
    exp.last_auto_check = utcnow()
    if found:
        record_memory(exp, snapshot(exp).rows)
    return list(found)


def sweep() -> dict[str, list[str]]:
    """Check every experiment receiving real data. Simulated ones were checked when they were run."""
    stopped = {}
    for exp in store.list():
        if not any(e.source == "real" for e in exp.events):
            continue
        try:
            result = check_experiment(exp)
        except Exception:   # one bad experiment must not stop the checks on the others
            log.exception("Guardrail check failed for experiment %s", exp.id)
            continue
        if result:
            stopped[exp.id] = result
    return stopped


def start(on_change: Callable[[], None], seconds: int = CHECK_SECONDS) -> threading.Event | None:
    """Run the sweep in the background. Set the returned event to stop it. 0 seconds turns it off."""
    if seconds <= 0:
        return None
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(seconds):
            try:
                sweep()
                on_change()
            except Exception:
                log.exception("Guardrail sweep failed")

    threading.Thread(target=loop, name="guardrail-watcher", daemon=True).start()
    return stop
