"""Rule-based workspace assistant: natural-language question -> grid filter + chart hint.

Deterministic so it works offline; the response shape (`filter`, `sort`, `chart`) is what an
AG Grid assistant can apply directly to the arms grid.
"""
from __future__ import annotations

import re

METRICS = {
    "churn": "churn", "conversion": "conversion", "mrr": "mrr", "revenue": "revenue",
    "signups": "signups", "sign-ups": "signups", "visitors": "visitors",
    "ltv": "ltv", "retention": "retention",
}


def ask(question: str, rows: list[dict]) -> dict:
    q = question.lower()
    metric = next((v for k, v in METRICS.items() if k in q), None)
    op = None
    if re.search(r"\b(above|over|higher|greater|more than|exceed)", q):
        op = ">"
    elif re.search(r"\b(below|under|lower|less than)", q):
        op = "<"
    out = {"question": question, "filter": None, "sort": None, "chart": None, "rows": rows,
           "answer": "I can filter arms by a metric (e.g. 'arms with churn above baseline') "
                     "or rank them (e.g. 'best conversion')."}
    if metric is None:
        return out

    base = next((r for r in rows if r.get("is_baseline")), None)
    num = re.search(r"(\d+(?:\.\d+)?)\s*%?", q)
    if op:
        if "baseline" in q and base is not None:
            threshold = base[metric]
        elif num:
            threshold = float(num.group(1))
            if metric in ("churn", "conversion") and "%" in q:
                threshold /= 100
        else:
            threshold = None
        if threshold is not None:
            keep = [r for r in rows if (r[metric] > threshold if op == ">" else r[metric] < threshold)
                    and not r.get("is_baseline")]
            out.update(filter={"metric": metric, "op": op, "value": threshold},
                       rows=keep, chart={"type": "bar", "metric": metric},
                       answer=f"{len(keep)} arm(s) with {metric} {op} {threshold:.4g}: "
                              + (", ".join(r["arm_id"] for r in keep) or "none") + ".")
            return out
    if re.search(r"\b(best|top|highest|most)", q) or re.search(r"\b(worst|lowest|least)", q):
        desc = not re.search(r"\b(worst|lowest|least)", q)
        ranked = sorted(rows, key=lambda r: r[metric], reverse=desc)
        out.update(sort={"metric": metric, "desc": desc}, rows=ranked,
                   chart={"type": "bar", "metric": metric},
                   answer=f"{ranked[0]['arm_id']} has the {'highest' if desc else 'lowest'} {metric}.")
        return out
    out.update(chart={"type": "bar", "metric": metric}, answer=f"Showing {metric} by arm.")
    return out
