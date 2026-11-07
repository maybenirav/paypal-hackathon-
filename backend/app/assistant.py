"""Rule-based workspace assistant: natural-language question -> grid filter + chart hint.

Deterministic so it works offline; the response shape (`filter`, `sort`, `chart`) is what an
AG Grid assistant can apply directly to the arms grid.
"""
from __future__ import annotations

import re

METRICS = {
    "churn": "churn", "conversion": "conversion", "mrr": "mrr", "revenue": "revenue",
    "signups": "signups", "sign-ups": "signups", "visitors": "visitors",
    "ltv": "ltv", "per visitor": "value_per_visitor", "retention": "retention",
    "refund": "refund_rate", "dispute": "dispute_rate",
}
RATES = {"churn", "conversion", "retention", "refund_rate", "dispute_rate"}
LABELS = {"mrr": "MRR", "ltv": "12-month value", "value_per_visitor": "value per visitor", "signups": "sign-ups",
          "refund_rate": "refund rate", "dispute_rate": "dispute rate"}


def _value(row: dict, metric: str) -> float | None:
    """Scalar for filtering and ranking. Retention is a weekly curve, so use its latest point."""
    v = row[metric]
    if isinstance(v, list):
        known = [x for x in v if x is not None]
        return known[-1] if known else None
    return v


def _fmt(metric: str, value: float) -> str:
    if metric in RATES:
        return f"{value:.1%}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _name(row: dict) -> str:
    return row.get("name") or row["arm_id"]


def _threshold(after_op: str, metric: str) -> float | None:
    num = re.search(r"(\d+(?:\.\d+)?)\s*(%?)", after_op)
    if not num:
        return None
    value = float(num.group(1))
    # Rates are stored as fractions, so "20%" and a bare "20" both mean 0.20
    return value / 100 if metric in RATES and (num.group(2) or value > 1) else value


def ask(question: str, rows: list[dict]) -> dict:
    q = question.lower()
    metric = next((v for k, v in METRICS.items() if k in q), None)
    above = re.search(r"\b(above|over|higher|greater|more than|exceed)", q)
    below = re.search(r"\b(below|under|lower|less than)", q)
    op_match = above or below
    op = ">" if above else "<" if below else None
    out = {"question": question, "filter": None, "sort": None, "chart": None, "rows": rows,
           "answer": "I can filter price options by a metric (e.g. 'churn above baseline') "
                     "or rank them (e.g. 'best conversion')."}
    if metric is None:
        return out

    label = LABELS.get(metric, metric)
    base = next((r for r in rows if r.get("is_baseline")), None)
    if op_match:
        threshold = (_value(base, metric) if "baseline" in q and base is not None
                     else _threshold(q[op_match.end():], metric))
        if threshold is not None:
            def matches(r: dict) -> bool:
                v = _value(r, metric)
                return v is not None and (v > threshold if op == ">" else v < threshold)

            keep = [r for r in rows if matches(r) and not r.get("is_baseline")]
            count = f"{len(keep)} price option{'' if len(keep) == 1 else 's'}"
            where = "above" if op == ">" else "below"
            out.update(filter={"metric": metric, "op": op, "value": threshold},
                       rows=keep, chart={"type": "bar", "metric": metric},
                       answer=f"{count} with {label} {where} {_fmt(metric, threshold)}"
                              + (": " + ", ".join(_name(r) for r in keep) + "." if keep else "."))
            return out
    if re.search(r"\b(best|top|highest|most|worst|lowest|least)", q):
        desc = not re.search(r"\b(worst|lowest|least)", q)
        if metric == "churn" and re.search(r"\b(best|worst)", q):
            desc = not desc   # the best churn is the lowest one
        ranked = sorted(rows, key=lambda r: (v if (v := _value(r, metric)) is not None else -1.0),
                        reverse=desc)
        out.update(sort={"metric": metric, "desc": desc}, rows=ranked,
                   chart={"type": "bar", "metric": metric},
                   answer=f"{_name(ranked[0])} has the {'highest' if desc else 'lowest'} {label}.")
        return out
    out.update(chart={"type": "bar", "metric": metric}, answer=f"Showing {label} by price option.")
    return out
