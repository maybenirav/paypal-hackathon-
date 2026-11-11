"""Import 12 months from PayPal: retries, partial imports and plain-English errors."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import backfill
from app.main import app
from app.paypal_client import PayPalError

client = TestClient(app)
TX = {"transaction_id": "T1", "date": "2026-11-02T10:00:00+0000", "event_code": "T0002", "status": "S",
      "amount": 15.0, "currency": "USD", "reference_id": "I-ABC", "reference_type": "SUB", "payer_email": "b@x.com"}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(backfill, "_sleep", lambda s: None)          # no real waiting in tests
    monkeypatch.setattr(backfill, "fetch_plans", lambda: [])


def flaky(fail_times: int, err: Exception):
    """fetch_window that fails `fail_times` times in total, then succeeds."""
    state = {"left": fail_times}

    def fetch(start, end):
        if state["left"] > 0:
            state["left"] -= 1
            raise err
        return [TX]
    return fetch


def test_one_hiccup_is_retried_and_import_is_complete(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_window", flaky(1, PayPalError(503, "")))
    r = client.post("/connect/backfill?months=12")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["partial"] is False and d["periods_failed"] == 0 and d["periods"] == 12
    assert "warning" not in d


def test_a_window_that_keeps_failing_is_skipped_not_fatal(monkeypatch):
    calls = {"n": 0}

    def fetch(start, end):
        calls["n"] += 1
        if calls["n"] <= backfill.RETRIES:          # every attempt of the first window fails
            raise PayPalError(503, "")
        return [TX]
    monkeypatch.setattr(backfill, "fetch_window", fetch)
    r = client.post("/connect/backfill?months=12")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["partial"] is True and d["periods_failed"] == 1
    assert "Imported 11 of 12 periods" in d["warning"]


def test_paypal_down_gives_a_plain_english_error(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_window", flaky(10_000, PayPalError(503, "")))
    r = client.post("/connect/backfill?months=2")
    assert r.status_code == 503
    assert "PayPal's sandbox is busy right now" in r.json()["detail"]


def test_missing_keys_is_reported_immediately_without_retries(monkeypatch):
    calls = {"n": 0}

    def fetch(start, end):
        calls["n"] += 1
        raise PayPalError(0, "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET missing in .env")
    monkeypatch.setattr(backfill, "fetch_window", fetch)
    r = client.post("/connect/backfill?months=12")
    assert r.status_code == 400 and "missing in .env" in r.json()["detail"]
    assert calls["n"] == 1


def test_network_error_is_retried(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_window", flaky(2, httpx.ConnectError("boom")))
    r = client.post("/connect/backfill?months=12")
    assert r.status_code == 200 and r.json()["partial"] is False


def test_network_down_everywhere_gives_a_plain_english_error(monkeypatch):
    monkeypatch.setattr(backfill, "fetch_window", flaky(10_000, httpx.ConnectError("boom")))
    r = client.post("/connect/backfill?months=1")
    assert r.status_code == 503 and "Couldn't reach PayPal" in r.json()["detail"]
