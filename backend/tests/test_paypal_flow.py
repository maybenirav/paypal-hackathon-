"""The PayPal routes against a fake PayPal, plus saving to disk."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, paypal_client, paypal_experiments
from app.main import app
from app.memory import memory
from app.paypal_client import PayPalError, get_access_token, paypal_request
from app.persistence import Persistence
from app.save_agent import save_log
from app.store import store

client = TestClient(app)


class FakePayPal:
    def __init__(self):
        self.calls = []
        self.status = "ACTIVE"

    def __call__(self, method, path, json=None, params=None):
        self.calls.append((method, path, json))
        if path == "/v1/billing/plans":
            return {"id": f"P-{len(self.calls)}", "status": "ACTIVE"}
        if path == "/v1/billing/subscriptions" and method == "POST":
            sub_id = f"I-{len(self.calls)}"
            self.custom = json["custom_id"]
            return {"id": sub_id, "status": "APPROVAL_PENDING",
                    "links": [{"rel": "approve", "href": f"https://paypal.test/{sub_id}"}]}
        if path.startswith("/v1/billing/subscriptions/"):
            return {"id": path.rsplit("/", 1)[1], "status": self.status, "custom_id": self.custom,
                    "start_time": "2026-11-05T10:00:00Z", "status_update_time": "2026-11-06T10:00:00Z"}
        raise PayPalError(404, "not mocked")


@pytest.fixture
def paypal(monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(paypal_experiments, "paypal_request", fake)
    monkeypatch.setattr(config, "PAYPAL_PRODUCT_ID", "PROD-1")
    monkeypatch.setattr(config, "PAYPAL_WEBHOOK_VERIFY", False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return fake


def published(n=3):
    eid = client.post("/experiments", json={"goal": "raise annual uptake", "num_arms": n}).json()["id"]
    assert client.post(f"/experiments/{eid}/paypal/publish").status_code == 200
    return eid


def subscribe(eid, **body):
    return client.post(f"/experiments/{eid}/paypal/subscribe", json=body).json()


def hook(event_id, event_type, resource):
    return client.post("/webhooks/paypal", json={"id": event_id, "event_type": event_type, "resource": resource,
                                                 "create_time": "2026-11-05T12:00:00Z"})


def row(eid, arm_id):
    rows = client.get(f"/experiments/{eid}/metrics").json()["rows"]
    return next(r for r in rows if r["arm_id"] == arm_id)


def test_publish_creates_one_plan_per_option_and_is_safe_to_repeat(paypal):
    eid = published()
    assert sum(c[1] == "/v1/billing/plans" for c in paypal.calls) == 3
    again = client.post(f"/experiments/{eid}/paypal/publish").json()
    assert all(not p["created"] for p in again["plans"])
    assert sum(c[1] == "/v1/billing/plans" for c in paypal.calls) == 3


def test_invalid_plans_are_not_published(paypal):
    eid = client.post("/experiments", json={"goal": "raise annual uptake"}).json()["id"]
    store.get(eid).validation[0]["valid"] = False
    r = client.post(f"/experiments/{eid}/paypal/publish")
    assert r.status_code == 422 and not paypal.calls


def test_returning_customer_gets_the_same_plan_and_counts_as_one_visit(paypal):
    eid = published()
    first = subscribe(eid, visitor_id="alice")
    second = subscribe(eid, visitor_id="alice")
    assert first["arm_id"] == second["arm_id"] and first["plan_id"] == second["plan_id"]
    assert row(eid, first["arm_id"])["visitors"] == 1


def test_subscriptions_are_listed_from_the_server(paypal):
    eid = published()
    created = subscribe(eid, arm_id="baseline")
    listed = client.get(f"/experiments/{eid}/paypal/subscriptions").json()["subscriptions"]
    assert listed[0]["subscription_id"] == created["subscription_id"]
    assert listed[0]["approve_url"].startswith("https://paypal.test/")


def test_sync_records_the_signup_then_the_cancellation(paypal):
    eid = published()
    sub = subscribe(eid, arm_id="baseline")
    url = f"/experiments/{eid}/paypal/sync/{sub['subscription_id']}"
    assert client.post(url).json()["added"] == ["SIGNUP"]
    assert client.post(url).json()["added"] == []
    assert row(eid, "baseline")["signups"] == 1
    paypal.status = "EXPIRED"
    assert client.post(url).json()["added"] == ["CANCELLED"]
    listed = client.get(f"/experiments/{eid}/paypal/subscriptions").json()["subscriptions"]
    assert listed[0]["status"] == "EXPIRED"


def test_payment_arriving_before_activation_still_counts_the_signup(paypal):
    eid = published()
    sub = subscribe(eid, arm_id="baseline")["subscription_id"]
    r = hook("WH-1", "PAYMENT.SALE.COMPLETED", {"id": "SALE-1", "billing_agreement_id": sub, "amount": {"total": "10.00"}})
    assert r.json()["status"] == "stored"
    b = row(eid, "baseline")
    assert b["signups"] == 1 and b["revenue"] == 10 and b["mrr"] == 10


def test_refund_is_linked_through_its_sale(paypal):
    eid = published()
    sub = subscribe(eid, arm_id="baseline")["subscription_id"]
    hook("WH-1", "PAYMENT.SALE.COMPLETED", {"id": "SALE-1", "billing_agreement_id": sub, "amount": {"total": "10.00"}})
    r = hook("WH-2", "PAYMENT.SALE.REFUNDED", {"id": "REF-1", "sale_id": "SALE-1", "amount": {"total": "10.00"}})
    assert r.json()["type"] == "REFUND"
    b = row(eid, "baseline")
    assert b["refunds"] == 1 and b["revenue"] == 0


def test_dispute_is_linked_through_its_sale(paypal):
    eid = published()
    sub = subscribe(eid, arm_id="baseline")["subscription_id"]
    hook("WH-1", "PAYMENT.SALE.COMPLETED", {"id": "SALE-1", "billing_agreement_id": sub, "amount": {"total": "10.00"}})
    r = hook("WH-2", "CUSTOMER.DISPUTE.CREATED", {"disputed_transactions": [{"seller_transaction_id": "SALE-1"}]})
    assert r.json()["type"] == "DISPUTE"
    assert row(eid, "baseline")["disputes"] == 1


def test_expired_subscription_counts_as_churn(paypal):
    eid = published()
    sub = subscribe(eid, arm_id="baseline")["subscription_id"]
    hook("WH-1", "BILLING.SUBSCRIPTION.ACTIVATED", {"id": sub, "custom_id": f"{eid}:baseline"})
    hook("WH-2", "BILLING.SUBSCRIPTION.EXPIRED", {"id": sub, "custom_id": f"{eid}:baseline"})
    assert row(eid, "baseline")["churned"] == 1


def test_webhook_retries_and_junk(paypal):
    eid = published()
    sub = subscribe(eid, arm_id="baseline")["subscription_id"]
    body = {"id": sub, "custom_id": f"{eid}:baseline"}
    assert hook("WH-1", "BILLING.SUBSCRIPTION.ACTIVATED", body).json()["status"] == "stored"
    assert hook("WH-1", "BILLING.SUBSCRIPTION.ACTIVATED", body).json()["status"] == "duplicate"
    assert hook("WH-3", "SOMETHING.ELSE", {}).json()["status"] == "ignored"
    assert hook("WH-4", "PAYMENT.SALE.COMPLETED", {"id": "X"}).json()["status"] == "ignored"
    assert client.post("/webhooks/paypal", content="not json").status_code == 400
    assert client.post("/webhooks/paypal", json=[1, 2]).status_code == 400


def test_webhooks_are_refused_when_they_cannot_be_verified(paypal, monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_WEBHOOK_VERIFY", True)
    monkeypatch.setattr(config, "PAYPAL_WEBHOOK_ID", "")
    assert hook("WH-9", "BILLING.SUBSCRIPTION.ACTIVATED", {}).status_code == 400


# ---- the PayPal client itself --------------------------------------------------------------------

class Response:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = str(body)
        self.content = b"x" if body else b""

    def json(self):
        return self._body


@pytest.fixture
def client_config(monkeypatch):
    monkeypatch.setattr(config, "PAYPAL_CLIENT_ID", "id")
    monkeypatch.setattr(config, "PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setitem(paypal_client._token, "value", None)
    monkeypatch.setitem(paypal_client._token, "expires_at", 0.0)


def test_network_failure_is_a_clear_error_not_a_crash(client_config, monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(paypal_client.httpx, "post", down)
    with pytest.raises(PayPalError, match="Can't reach PayPal"):
        get_access_token()
    r = client.get("/paypal/health")
    assert r.status_code == 200 and r.json()["paypal"] == "error"


def test_expired_token_is_refreshed_and_the_request_retried_once(client_config, monkeypatch):
    tokens = iter(["old", "new"])
    monkeypatch.setattr(paypal_client.httpx, "post",
                        lambda *a, **k: Response(200, {"access_token": next(tokens), "expires_in": 3000}))
    seen = []

    def request(method, url, headers, **kw):
        seen.append((headers["Authorization"], headers.get("PayPal-Request-Id")))
        return Response(401, {"error": "expired"}) if len(seen) == 1 else Response(200, {"ok": True})
    monkeypatch.setattr(paypal_client.httpx, "request", request)
    assert paypal_request("POST", "/v1/x", json={}) == {"ok": True}
    assert [s[0] for s in seen] == ["Bearer old", "Bearer new"]
    assert seen[0][1] == seen[1][1]     # same id, so PayPal can't create a duplicate


def test_transactions_leave_out_buyer_details():
    from app.paypal_transactions import _simplify
    item = {"transaction_info": {"transaction_id": "T1"}, "payer_info": {"email_address": "a@example.com"}}
    assert "a@example.com" not in str(_simplify(item))


# ---- saving to disk ------------------------------------------------------------------------------

def test_everything_survives_a_restart(tmp_path, paypal):
    saved = Persistence(str(tmp_path / "test.db"))
    eid = published()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    client.post(f"/experiments/{eid}/decide")
    client.post(f"/experiments/{eid}/rollout/propose")
    client.post(f"/experiments/{eid}/assign", json={"visitor_id": "alice"})
    client.put(f"/experiments/{eid}/guardrails", json={"retention_floor": 0.7})
    client.post("/save/sweep?limit=3")
    store.mark_webhook("WH-1")
    before = client.get(f"/experiments/{eid}").json()
    metrics_before = client.get(f"/experiments/{eid}/metrics").json()
    saved.save()
    saved.close()

    # A restart: everything held in memory is gone
    store._exps.clear()
    store.seen_webhooks.clear()
    memory.records.clear()
    save_log.actions.clear()

    reopened = Persistence(str(tmp_path / "test.db"))
    reopened.load()
    assert client.get(f"/experiments/{eid}").json() == before
    assert client.get(f"/experiments/{eid}/metrics").json() == metrics_before
    assert store.get(eid).assignments.keys() == {"alice"}
    assert store.get(eid).guardrails.retention_floor == 0.7
    assert store.get(eid).arms[0].plan_id
    assert "WH-1" in store.seen_webhooks
    assert len(memory.records) == 1 and memory.records[0].winner == "annual_discount"
    assert len(save_log.actions) == 3
    reopened.close()


def test_saving_only_appends_new_events_and_handles_replacements(tmp_path, paypal):
    saved = Persistence(str(tmp_path / "test.db"))
    eid = published()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    saved.save()
    count = len(store.get(eid).events)
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    replaced = len(store.get(eid).events)
    assert replaced != count
    saved.save()
    rows = saved._conn.execute("SELECT COUNT(*) FROM events WHERE experiment_id = ?", (eid,)).fetchone()[0]
    assert rows == replaced
    client.delete(f"/experiments/{eid}")
    saved.save()
    assert saved._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert saved._conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 0
    saved.close()


def test_unwritable_database_does_not_stop_the_app(tmp_path):
    broken = Persistence(str(tmp_path / "missing_folder" / "test.db"))
    assert not broken.enabled
    broken.save()
    broken.load()
