"""Day-zero import: backfill up to 12 months of a merchant's PayPal history into cohorts.

  POST /connect/backfill?months=12   live: Transaction Search + list plans (needs PayPal creds and
                                     "Transaction search" enabled on the app)
  POST /connect/backfill/sample      labelled synthetic history, for the demo and for tests
  GET  /connect/cohorts              the last import

PayPal's Transaction Search accepts at most 31 days per request, so the window is walked in
31-day slices. Each customer is keyed by subscription id (`paypal_reference_id`), else by payer
email. A customer's cohort is the month of their first completed payment; they count as retained
in month m if they paid in that month.
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from .local_db import db
from .paypal_client import PayPalError, paypal_request

router = APIRouter(prefix="/connect", tags=["connect"])

WINDOW_DAYS = 31
MAX_PAGES = 10
REFUND_CODES = ("T1107", "T1106", "T1105")       # payment refunds / reversals
DISPUTE_CODES = ("T1201", "T1202", "T1203", "T1204", "T1205", "T1206", "T1207")  # chargebacks


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def _simplify(item: dict) -> dict:
    info = item.get("transaction_info") or {}
    payer = item.get("payer_info") or {}
    amount = info.get("transaction_amount") or {}
    return {
        "transaction_id": info.get("transaction_id"),
        "date": info.get("transaction_initiation_date"),
        "event_code": info.get("transaction_event_code") or "",
        "status": info.get("transaction_status"),
        "amount": float(amount.get("value") or 0),
        "currency": amount.get("currency_code"),
        "reference_id": info.get("paypal_reference_id"),
        "reference_type": info.get("paypal_reference_id_type"),
        "payer_email": payer.get("email_address"),
    }


def windows(end: datetime, months: int) -> list[tuple[datetime, datetime]]:
    """[start, end) slices of at most 31 days covering `months` x 30 days back from `end`."""
    start = end - timedelta(days=30 * months)
    out, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=WINDOW_DAYS), end)
        out.append((cur, nxt))
        cur = nxt
    return out


def fetch_window(start: datetime, end: datetime) -> list[dict]:
    out: list[dict] = []
    page, total = 1, 1
    while page <= total and page <= MAX_PAGES:
        data = paypal_request("GET", "/v1/reporting/transactions", params={
            "start_date": _fmt(start), "end_date": _fmt(end),
            "fields": "transaction_info,payer_info", "page_size": 500, "page": page})
        out.extend(_simplify(t) for t in data.get("transaction_details", []))
        total = data.get("total_pages") or 1
        page += 1
    return out


def fetch_plans() -> list[dict]:
    plans, page = [], 1
    while page <= MAX_PAGES:
        data = paypal_request("GET", "/v1/billing/plans", params={
            "page_size": 20, "page": page, "total_required": "true"})
        plans.extend({"id": p.get("id"), "name": p.get("name"), "status": p.get("status"),
                      "product_id": p.get("product_id")} for p in data.get("plans", []))
        if page >= (data.get("total_pages") or 1):
            break
        page += 1
    return plans


# ---- cohort maths (pure, unit-tested) -----------------------------------------------------

def _month(date: str) -> str:
    return date[:7]  # "2026-03-14T..." -> "2026-03"


def _month_index(m: str) -> int:
    y, mo = m.split("-")
    return int(y) * 12 + int(mo) - 1


def _month_name(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def build_cohorts(txs: list[dict]) -> dict:
    payments: dict[str, set[int]] = defaultdict(set)
    revenue: dict[int, float] = defaultdict(float)
    refunds = disputes = n_payments = 0
    refund_amount = 0.0
    for t in txs:
        if not t.get("date"):
            continue
        code = t.get("event_code") or ""
        m = _month_index(_month(t["date"]))
        if code.startswith(DISPUTE_CODES):
            disputes += 1
            continue
        if code.startswith(REFUND_CODES) or t["amount"] < 0:
            refunds += 1
            refund_amount += abs(t["amount"])
            revenue[m] -= abs(t["amount"])
            continue
        if t.get("status") not in (None, "S") or t["amount"] <= 0:
            continue
        key = t.get("reference_id") or t.get("payer_email") or t.get("transaction_id")
        payments[key].add(m)
        revenue[m] += t["amount"]
        n_payments += 1

    if not payments:
        return {"customers": 0, "months": [], "cohorts": [], "revenue": [], "summary": {
            "payments": 0, "refunds": refunds, "disputes": disputes}}

    first = min(min(v) for v in payments.values())
    last = max(max(v) for v in payments.values())
    by_cohort: dict[int, list[set[int]]] = defaultdict(list)
    for months in payments.values():
        by_cohort[min(months)].append(months)

    cohorts = []
    for c in sorted(by_cohort):
        members = by_cohort[c]
        horizon = last - c
        retention = [round(sum(1 for ms in members if c + k in ms) / len(members), 4)
                     for k in range(horizon + 1)]
        cohorts.append({"cohort": _month_name(c), "size": len(members), "retention": retention})

    # Monthly churn: of customers who paid in month m, the share who did not pay in m+1.
    # The last month is excluded because its next month has not happened yet.
    churn_rates = []
    for m in range(first, last):
        active = [ms for ms in payments.values() if m in ms]
        if active:
            churn_rates.append(sum(1 for ms in active if m + 1 not in ms) / len(active))
    active_last = sum(1 for ms in payments.values() if last in ms)
    return {
        "customers": len(payments),
        "months": [_month_name(i) for i in range(first, last + 1)],
        "cohorts": cohorts,
        "revenue": [{"month": _month_name(i), "revenue": round(revenue.get(i, 0.0), 2)}
                    for i in range(first, last + 1)],
        "summary": {
            "payments": n_payments, "refunds": refunds, "disputes": disputes,
            "refund_rate": round(refunds / n_payments, 4) if n_payments else 0.0,
            "avg_monthly_churn": round(sum(churn_rates) / len(churn_rates), 4) if churn_rates else None,
            "active_last_month": active_last,
            "arpu_last_month": round(revenue.get(last, 0.0) / active_last, 2) if active_last else None,
        },
    }


def sample_transactions(months: int = 12, seed: int = 11, price: float = 15.0,
                        now: datetime | None = None) -> list[dict]:
    """Synthetic merchant history in Transaction Search shape (labelled `simulated` upstream)."""
    rng = random.Random(seed)
    now = now or datetime(2026, 11, 1, tzinfo=timezone.utc)
    start = now - timedelta(days=30 * months)
    txs, n = [], 0
    for month in range(months):
        for _ in range(rng.randint(35, 55)):            # new subscribers that month
            n += 1
            sub = f"I-SAMPLE{n:05d}"
            day = start + timedelta(days=30 * month + rng.randint(0, 27))
            churn = rng.choice((0.06, 0.08, 0.12))
            t = day
            while t < now:
                txs.append({"transaction_id": f"TX{len(txs):06d}", "date": t.isoformat(),
                            "event_code": "T0002", "status": "S", "amount": price,
                            "currency": "USD", "reference_id": sub, "reference_type": "SUB",
                            "payer_email": f"buyer{n}@example.com"})
                if rng.random() < 0.02:
                    txs.append({"transaction_id": f"TX{len(txs):06d}",
                                "date": (t + timedelta(days=2)).isoformat(), "event_code": "T1107",
                                "status": "S", "amount": -price, "currency": "USD",
                                "reference_id": sub, "reference_type": "SUB",
                                "payer_email": f"buyer{n}@example.com"})
                if rng.random() < churn:
                    break
                t += timedelta(days=30)
    return txs


# ---- routes ------------------------------------------------------------------------------

def _store(result: dict) -> dict:
    db.put("backfill", "latest", result)
    return result


@router.post("/backfill")
def backfill(months: int = Query(12, ge=1, le=36)):
    end = datetime.now(timezone.utc)
    txs: list[dict] = []
    try:
        for s, e in windows(end, months):
            txs.extend(fetch_window(s, e))
        plans = fetch_plans()
    except PayPalError as e:
        if e.status == 0:
            raise HTTPException(400, str(e)) from e
        if e.status == 403:
            raise HTTPException(403, "Transaction search is not enabled for this PayPal app "
                                     "(developer dashboard > your app > Features).") from e
        raise HTTPException(502, f"PayPal error {e.status}: {e.body[:300]}") from e
    return _store({"source": "paypal", "months": months, "imported_at": end.isoformat(),
                   "transactions": len(txs), "plans": plans, **build_cohorts(txs)})


@router.post("/backfill/sample")
def backfill_sample(months: int = Query(12, ge=1, le=36), seed: int = 11):
    txs = sample_transactions(months, seed)
    return _store({"source": "simulated", "months": months,
                   "imported_at": datetime.now(timezone.utc).isoformat(),
                   "transactions": len(txs), "plans": [], **build_cohorts(txs)})


@router.get("/cohorts")
def cohorts():
    data = db.get("backfill", "latest")
    if data is None:
        raise HTTPException(404, "nothing imported yet: POST /connect/backfill or /connect/backfill/sample")
    return data
