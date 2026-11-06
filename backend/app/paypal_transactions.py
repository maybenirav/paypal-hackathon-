"""Person A: merchant transaction history from PayPal's Transaction Search API.

  GET /paypal/transactions?days=30                      all recent transactions
  GET /experiments/{id}/paypal/transactions?days=30     only this experiment's subscribers

Note: PayPal can take up to ~3 hours to show new transactions in this API,
and the app needs "Transaction search" enabled in the developer dashboard.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.paypal_client import PayPalError, paypal_request
from app.store import store

router = APIRouter(tags=["paypal"])

MAX_PAGES = 10


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def _simplify(item: dict) -> dict:
    info = item.get("transaction_info") or {}
    payer = item.get("payer_info") or {}
    amount = info.get("transaction_amount") or {}
    fee = info.get("fee_amount") or {}
    return {
        "transaction_id": info.get("transaction_id"),
        "date": info.get("transaction_initiation_date"),
        "event_code": info.get("transaction_event_code"),
        "status": info.get("transaction_status"),
        "amount": float(amount.get("value") or 0),
        "currency": amount.get("currency_code"),
        "fee": float(fee.get("value") or 0),
        "subscription_id": info.get("paypal_reference_id"),
        "custom": info.get("custom_field"),
        "payer_email": payer.get("email_address"),
    }


def fetch_transactions(days: int) -> list[dict]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    out: list[dict] = []
    page, total_pages = 1, 1
    while page <= total_pages and page <= MAX_PAGES:
        try:
            data = paypal_request("GET", "/v1/reporting/transactions", params={
                "start_date": _fmt(start), "end_date": _fmt(end),
                "fields": "all", "page_size": 500, "page": page,
            })
        except PayPalError as e:
            if e.status == 403:
                raise HTTPException(403, "Transaction search is not enabled for this PayPal app "
                                         "(developer dashboard > your app > Features).") from e
            raise HTTPException(502, f"PayPal error {e.status}: {e.body[:500]}") from e
        out.extend(_simplify(t) for t in data.get("transaction_details", []))
        total_pages = data.get("total_pages") or 1
        page += 1
    out.sort(key=lambda t: t["date"] or "", reverse=True)
    return out


@router.get("/paypal/transactions")
def all_transactions(days: int = Query(30, ge=1, le=31)):
    txs = fetch_transactions(days)
    return {"days": days, "count": len(txs), "transactions": txs}


@router.get("/experiments/{exp_id}/paypal/transactions")
def experiment_transactions(exp_id: str, days: int = Query(30, ge=1, le=31)):
    exp = store.get(exp_id)
    if exp is None:
        raise HTTPException(404, "experiment not found")
    real_subs = {e.subscriber_id: e.arm_id for e in exp.events if e.source == "real"}
    txs = []
    for t in fetch_transactions(days):
        custom = t["custom"] or ""
        arm_id = real_subs.get(t["subscription_id"])
        if arm_id is None and custom.startswith(f"{exp_id}:"):
            arm_id = custom.split(":", 1)[1]
        if arm_id is not None:
            txs.append({**t, "arm_id": arm_id})
    return {"experiment_id": exp_id, "days": days, "count": len(txs),
            "total_amount": round(sum(t["amount"] for t in txs), 2), "transactions": txs}
