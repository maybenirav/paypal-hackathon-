"""Workspace assistant: a merchant's question -> an AG Grid filter model + sort.

Gemini translates the question into structured filters over the arms grid's columns; the
frontend applies them as the grid's own column filters, so the merchant sees (and can edit)
exactly what the AI did. A deterministic rule parser is the fallback when Gemini is off or
returns something we can't validate. Matching rows and the answer text are always computed
here from the real numbers, never taken from the model.
"""
from __future__ import annotations

import json
import logging
import math
import re

from .agent import llm

log = logging.getLogger("planlab.assistant")

# Every metric the grid has a column for: key -> (label, unit). Unit "%" values are fractions.
COLUMNS: dict[str, tuple[str, str]] = {
    "price": ("price per billing interval", "$"),
    "visitors": ("checkout visitors", "count"),
    "signups": ("sign-ups", "count"),
    "paid": ("paying subscribers", "count"),
    "conversion": ("sign-up conversion", "%"),
    "paid_conversion": ("paid conversion (visitor -> paying)", "%"),
    "mrr_per_visitor": ("new monthly revenue per visitor (the decision metric)", "$"),
    "trial_to_paid": ("trial -> paid rate", "%"),
    "first_renewal": ("first renewal rate", "%"),
    "churn": ("churn among paying subscribers", "%"),
    "mrr": ("monthly recurring revenue", "$"),
    "revenue": ("revenue collected", "$"),
    "projected_ltv": ("projected 12-month LTV", "$"),
    "prob_beats_baseline": ("probability the arm earns more than the baseline", "%"),
    "allocation": ("share of traffic", "%"),
    "refund_rate": ("refund rate", "%"),
    "dispute_rate": ("dispute rate", "%"),
}
PERCENT = {k for k, (_, unit) in COLUMNS.items() if unit == "%"}

# Rule parser vocabulary. Longer phrases first so "paid conversion" wins over "conversion".
METRICS = {
    "mrr per visitor": "mrr_per_visitor", "per visitor": "mrr_per_visitor",
    "paid conversion": "paid_conversion", "trial": "trial_to_paid", "renewal": "first_renewal",
    "churn": "churn", "conversion": "conversion", "mrr": "mrr", "revenue": "revenue",
    "signups": "signups", "sign-ups": "signups", "visitors": "visitors",
    "ltv": "projected_ltv", "lifetime": "projected_ltv", "probability": "prob_beats_baseline",
    "likely": "prob_beats_baseline", "refund": "refund_rate", "dispute": "dispute_rate",
    "traffic": "allocation", "price": "price",
}


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _passes(row: dict, f: dict) -> bool:
    v = _num(row.get(f["metric"]))
    return v is not None and (v > f["value"] if f["op"] == ">" else v < f["value"])


def _fmt(metric: str, value: float) -> str:
    unit = COLUMNS.get(metric, ("", ""))[1]
    if unit == "%":
        return f"{value * 100:.3g}%"
    return f"${value:,.2f}" if unit == "$" else f"{value:,.4g}"


def _result(question: str, rows: list[dict], filters: list[dict], sort: dict | None,
            source: str, lead: str = "") -> dict:
    """Apply filters/sort to the real rows and phrase the answer from what actually matched."""
    # Filters compare challengers against a threshold, so the baseline is never "matched";
    # a plain ranking still includes it.
    keep = ([r for r in rows if not r.get("is_baseline") and all(_passes(r, f) for f in filters)]
            if filters else list(rows))
    if sort:
        keep = sorted((r for r in keep if _num(r.get(sort["metric"])) is not None),
                      key=lambda r: r[sort["metric"]], reverse=sort["desc"])
    parts = [lead.strip()] if lead.strip() else []
    if filters:
        cond = " and ".join(f"{f['metric']} {f['op']} {_fmt(f['metric'], f['value'])}" for f in filters)
        parts.append(f"{len(keep)} arm(s) with {cond}: " + (", ".join(r["arm_id"] for r in keep) or "none") + ".")
    if sort:
        if keep:
            parts.append(f"{keep[0]['arm_id']} has the {'highest' if sort['desc'] else 'lowest'} {sort['metric']}.")
        else:
            parts.append(f"No arm has {sort['metric']} yet.")
    focus = (filters[0]["metric"] if filters else sort["metric"] if sort else None)
    return {"question": question, "filters": filters, "filter": filters[0] if filters else None,
            "sort": sort, "chart": {"type": "bar", "metric": focus} if focus else None,
            "rows": keep if (filters or sort) else rows, "answer": " ".join(parts), "source": source}


# ---------- deterministic fallback ----------

def _metric(text: str) -> str | None:
    return next((v for k, v in METRICS.items() if k in text), None)


def _ask_rules(question: str, rows: list[dict]) -> dict:
    q = question.lower()
    # "... without more churn" adds a guard: that metric must not be worse than the baseline.
    main, _, guard = q.partition(" without ")
    metric = _metric(main) or _metric(q)
    if metric is None:
        out = _result(question, rows, [], None, "rules")
        out["answer"] = ("I can filter arms by a metric (e.g. 'arms with churn above baseline') "
                         "or rank them (e.g. 'best conversion').")
        return out
    base = next((r for r in rows if r.get("is_baseline")), None)
    op = (">" if re.search(r"\b(above|over|higher|greater|exceed|better than|more\b.*\bthan)", main)
          else "<" if re.search(r"\b(below|under|lower|less\b.*\bthan|fewer\b.*\bthan)", main) else None)
    if op:
        num = re.search(r"(\d+(?:\.\d+)?)\s*%?", main)
        threshold = None
        if "baseline" in q and base is not None:
            threshold = _num(base.get(metric))
        elif num:
            threshold = float(num.group(1))
            if metric in PERCENT and "%" in main:
                threshold /= 100
        if threshold is not None:
            filters = [{"metric": metric, "op": op, "value": threshold}]
            g_metric = _metric(guard) if guard else None
            g_value = _num(base.get(g_metric)) if (g_metric and base) else None
            if g_metric and g_metric != metric and g_value is not None:
                g_op = ">" if re.search(r"\b(less|lower|fewer|losing|drop)", guard) else "<"
                filters.append({"metric": g_metric, "op": g_op, "value": g_value})
            return _result(question, rows, filters, None, "rules")
    if re.search(r"\b(best|top|highest|most|worst|lowest|least)", q):
        desc = not re.search(r"\b(worst|lowest|least)", q)
        return _result(question, rows, [], {"metric": metric, "desc": desc}, "rules")
    out = _result(question, rows, [], None, "rules")
    out.update(chart={"type": "bar", "metric": metric}, answer=f"Showing {metric} by arm.")
    return out


# ---------- Gemini ----------

PROMPT = """You turn a subscription merchant's question about a pricing experiment into filters
and a sort for a data grid. Each row is one price arm.

Columns you may use (key: meaning [unit]; % columns hold fractions, so 3% is 0.03):
{columns}

Current rows:
{rows}

Question: {question}

Reply with JSON only:
{{"filters": [{{"metric": "<column key>", "op": ">" or "<", "value": <number>}}],
  "sort": {{"metric": "<column key>", "desc": true or false}} or null,
  "interpretation": "<one short sentence saying what you are showing>"}}

Rules: use only the column keys above. "Above/below baseline" means compare with the baseline row's
value for that column. "Best/top/highest" is a descending sort, "worst/lowest" ascending. Use an
empty filters list when the question doesn't ask to narrow the rows. Never invent numbers that are
not in the question or the rows."""


def _validate(raw: dict) -> tuple[list[dict], dict | None, str] | None:
    filters = []
    for f in raw.get("filters") or []:
        if not isinstance(f, dict):
            return None
        metric, op, value = f.get("metric"), f.get("op"), _num(f.get("value"))
        if metric not in COLUMNS or op not in (">", "<") or value is None:
            return None
        filters.append({"metric": metric, "op": op, "value": value})
    sort = raw.get("sort")
    if sort is not None:
        if not isinstance(sort, dict) or sort.get("metric") not in COLUMNS:
            return None
        sort = {"metric": sort["metric"], "desc": bool(sort.get("desc", True))}
    if not filters and sort is None:
        return None
    return filters, sort, str(raw.get("interpretation") or "")[:200]


def _ask_llm(question: str, rows: list[dict]) -> dict | None:
    table = [{"arm_id": r.get("arm_id"), "baseline": bool(r.get("is_baseline")),
              **{k: (round(v, 5) if (v := _num(r.get(k))) is not None else None) for k in COLUMNS}}
             for r in rows]
    prompt = PROMPT.format(
        columns="\n".join(f"- {k}: {label} [{unit}]" for k, (label, unit) in COLUMNS.items()),
        rows=json.dumps(table), question=question)
    text = llm.generate(prompt, json_mode=True, temperature=0.0, timeout=10)
    parsed = _validate(json.loads(text))
    if parsed is None:
        return None
    filters, sort, lead = parsed
    return _result(question, rows, filters, sort, llm.provider_used(), lead)


def ask(question: str, rows: list[dict], use_llm: bool = True) -> dict:
    if use_llm and llm.enabled():
        try:
            out = _ask_llm(question, rows)
            if out is not None:
                return out
        except Exception as exc:  # fall back to rules on any model/parse failure
            log.warning("LLM grid query failed, using rules: %s", exc)
    return _ask_rules(question, rows)
