"""Experiment Memory: every settled test, so the Pricing Agent can learn from past results."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from .local_db import db
from .schemas import Arm


@dataclass
class MemoryRecord:
    experiment_id: str
    goal: str
    winner: str | None
    stopped: list[str]
    arms: list[dict]                 # arm_id, price, interval, trial_days, conversion, churn, projected_ltv
    ts: datetime = field(default_factory=datetime.utcnow)


class Memory:
    def __init__(self, persist: bool = True) -> None:
        self.persist = persist
        self.records: list[MemoryRecord] = []
        if persist:
            for d in db.all("memory"):
                d["ts"] = datetime.fromisoformat(d["ts"])
                self.records.append(MemoryRecord(**d))

    def upsert(self, rec: MemoryRecord) -> None:
        self.records = [r for r in self.records if r.experiment_id != rec.experiment_id] + [rec]
        if self.persist:
            db.put("memory", rec.experiment_id, asdict(rec))

    def winners(self) -> set[str]:
        return {r.winner for r in self.records if r.winner}

    def losers(self) -> set[str]:
        return {a for r in self.records for a in r.stopped}

    def summary(self, limit: int = 5) -> str:
        if not self.records:
            return ""
        lines = []
        for r in self.records[-limit:]:
            lines.append(f"- Goal '{r.goal}': winner={r.winner or 'none'}, stopped={r.stopped or 'none'}")
            for a in r.arms:
                lines.append(f"    {a['arm_id']}: {a['price']}/{a['interval']}, trial {a['trial_days']}d, "
                             f"paid conv {a['conversion']:.1%}, churn {a['churn']:.1%}, "
                             f"MRR/visitor {a.get('mrr_per_visitor', 0):.2f}")
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
