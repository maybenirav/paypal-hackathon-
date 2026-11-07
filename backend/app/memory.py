"""Experiment Memory: every settled test, so the Pricing Agent can learn from past results."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .events import utcnow
from .schemas import Arm


@dataclass
class MemoryRecord:
    experiment_id: str
    goal: str
    winner: str | None
    stopped: list[str]
    arms: list[dict]                 # arm_id, price, interval, trial_days, conversion, churn, ltv
    ts: datetime = field(default_factory=utcnow)
    simulated: bool = False
    winner_name: str | None = None
    stopped_names: list[str] = field(default_factory=list)


class Memory:
    def __init__(self) -> None:
        self.records: list[MemoryRecord] = []

    def upsert(self, rec: MemoryRecord) -> None:
        self.records = [r for r in self.records if r.experiment_id != rec.experiment_id] + [rec]

    def remove(self, experiment_id: str) -> None:
        self.records = [r for r in self.records if r.experiment_id != experiment_id]

    def _verdicts(self) -> dict[str, bool]:
        """Each arm's most recent result, oldest first so later tests overrule earlier ones."""
        verdicts: dict[str, bool] = {}
        for r in self.records:
            verdicts.update({arm: False for arm in r.stopped})
            if r.winner:
                verdicts[r.winner] = True
        return verdicts

    def winners(self) -> set[str]:
        return {arm for arm, won in self._verdicts().items() if won}

    def losers(self) -> set[str]:
        return {arm for arm, won in self._verdicts().items() if not won}

    def summary(self, limit: int = 5) -> str:
        if not self.records:
            return ""
        lines = []
        for r in self.records[-limit:]:
            kind = " (simulated data)" if r.simulated else ""
            lines.append(f"- Goal '{r.goal}'{kind}: winner={r.winner or 'none'}, stopped={r.stopped or 'none'}")
            for a in r.arms:
                lines.append(f"    {a['arm_id']}: {a['price']}/{a['interval']}, trial {a['trial_days']}d, "
                             f"conv {a['conversion']:.1%}, churn {a['churn']:.1%}, LTV {a['ltv']:.2f}")
        return "\n".join(lines)


def adapt_fallback(arms: list[Arm], mem: Memory) -> list[Arm]:
    """Reorder/replace fallback arms using past results: keep past winners, drop past losers."""
    losers, winners = mem.losers(), mem.winners()
    keep = [a for a in arms if a.is_baseline or a.arm_id not in losers]
    for a in keep:
        if a.arm_id in winners and not a.is_baseline:
            a.reason += " Won a previous experiment, so it is re-tested first."
    keep.sort(key=lambda a: (not a.is_baseline, a.arm_id not in winners))
    # if everything but the baseline was a past loser, fall back to the original set
    return keep if len(keep) >= 2 else arms


memory = Memory()
