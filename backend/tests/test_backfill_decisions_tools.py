from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import backfill, decision_log
from app.main import app
from app.paypal_client import PayPalError
from app.save_agent import SubscriberContext

client = TestClient(app)


def make(n=3):
    return client.post("/experiments", json={"goal": "raise annual uptake", "num_arms": n}).json()["id"]


# ---- backfill: cohort maths ----------------------------------------------------------------

def tx(sub, date, amount=10.0, code="T0002", status="S"):
    return {"transaction_id": f"{sub}-{date}", "date": f"{date}-05T10:00:00+0000",
            "event_code": code, "status": status, "amount": amount, "currency": "USD",
            "reference_id": sub, "payer_email": None}


def test_cohorts_hand_calc():
    # A: Jan, Feb, Mar.  B: Jan only.  C: Feb, Mar.
    txs = [tx("A", "2026-01"), tx("A", "2026-02"), tx("A", "2026-03"),
           tx("B", "2026-01"), tx("C", "2026-02"), tx("C", "2026-03"),
           tx("A", "2026-03", -10.0, "T1107"),       # one refund
           tx("B", "2026-01", 99.0, status="P")]     # pending: ignored
    r = backfill.build_cohorts(txs)
    assert r["customers"] == 3 and r["months"] == ["2026-01", "2026-02", "2026-03"]
    jan, feb = r["cohorts"]
    assert jan == {"cohort": "2026-01", "size": 2, "retention": [1.0, 0.5, 0.5]}
    assert feb == {"cohort": "2026-02", "size": 1, "retention": [1.0, 1.0]}
    # revenue: Jan 20, Feb 20, Mar 20 - 10 refund
    assert [m["revenue"] for m in r["revenue"]] == [20.0, 20.0, 10.0]
    s = r["summary"]
    assert s["payments"] == 6 and s["refunds"] == 1 and s["refund_rate"] == pytest.approx(1 / 6, abs=1e-4)
    # churn Jan->Feb: B left of {A,B} = 0.5; Feb->Mar: none of {A,C} = 0 -> mean 0.25
    assert s["avg_monthly_churn"] == 0.25
    assert s["active_last_month"] == 2 and s["arpu_last_month"] == 5.0


def test_cohorts_empty_and_disputes():
    r = backfill.build_cohorts([tx("A", "2026-01", 5.0, "T1201")])
    assert r["customers"] == 0 and r["summary"]["disputes"] == 1


def test_windows_cover_range_in_31_day_slices():
    end = datetime(2026, 11, 1, tzinfo=timezone.utc)
    w = backfill.windows(end, 12)
    assert w[0][0] == end - timedelta(days=360) and w[-1][1] == end
    assert all((b - a).days <= 31 for a, b in w)
    assert all(w[i][1] == w[i + 1][0] for i in range(len(w) - 1))


def test_sample_backfill_route_and_cohorts():
    assert client.get("/connect/cohorts").status_code == 404
    r = client.post("/connect/backfill/sample?months=12").json()
    assert r["source"] == "simulated" and r["customers"] > 300
    assert len(r["cohorts"]) == 12 and all(c["retention"][0] == 1.0 for c in r["cohorts"])
    assert 0.03 < r["summary"]["avg_monthly_churn"] < 0.15
    assert client.get("/connect/cohorts").json()["customers"] == r["customers"]


def test_live_backfill_walks_windows_and_pages(monkeypatch):
    calls = []

    def fake(method, path, json=None, params=None):
        calls.append((path, params))
        if path == "/v1/billing/plans":
            return {"plans": [{"id": "P-1", "name": "Pro", "status": "ACTIVE", "product_id": "PROD"}],
                    "total_pages": 1}
        page = params["page"]
        day = params["start_date"][:7]
        return {"total_pages": 2, "transaction_details": [{
            "transaction_info": {"transaction_id": f"{day}-{page}", "transaction_initiation_date":
                                 params["start_date"], "transaction_event_code": "T0002",
                                 "transaction_status": "S", "paypal_reference_id": f"I-{page}",
                                 "transaction_amount": {"value": "15.00", "currency_code": "USD"}}}]}
    monkeypatch.setattr(backfill, "paypal_request", fake)
    r = client.post("/connect/backfill?months=2").json()
    tx_calls = [c for c in calls if c[0] == "/v1/reporting/transactions"]
    assert len(tx_calls) == 2 * len(backfill.windows(datetime.now(timezone.utc), 2))
    assert r["source"] == "paypal" and r["plans"][0]["id"] == "P-1" and r["customers"] == 2


def test_live_backfill_errors(monkeypatch):
    def no_creds(*a, **k):
        raise PayPalError(0, "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET missing in .env")
    monkeypatch.setattr(backfill, "paypal_request", no_creds)
    assert client.post("/connect/backfill").status_code == 400

    def forbidden(*a, **k):
        raise PayPalError(403, "NOT_AUTHORIZED")
    monkeypatch.setattr(backfill, "paypal_request", forbidden)
    assert client.post("/connect/backfill").status_code == 403


# ---- decision memory -----------------------------------------------------------------------

def test_guardrail_pause_is_logged_and_explained():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    d = client.post(f"/experiments/{eid}/decide").json()
    assert d["action"] == "stop_arm"
    arm = d["arm_id"]
    logged = client.get(f"/decisions?experiment_id={eid}&kind=experiment").json()
    assert logged["count"] == 1 and logged["decisions"][0]["arm_id"] == arm
    w = client.post("/decisions/why", json={"question": f"why did arm {arm} pause?",
                                            "experiment_id": eid}).json()
    assert w["engine"] == "local" and w["matches"][0]["arm_id"] == arm
    assert f"Paused arm {arm}" in w["answer"]


def test_winner_and_rollout_are_logged():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "winner"})
    assert client.post(f"/experiments/{eid}/decide").json()["action"] == "winner"
    client.post(f"/experiments/{eid}/rollout/propose")
    client.post(f"/experiments/{eid}/rollout/approve")
    kinds = [d["kind"] for d in client.get(f"/decisions?experiment_id={eid}").json()["decisions"]]
    assert "rollout" in kinds and "experiment" in kinds
    w = client.post("/decisions/why", json={"question": "why did it roll out?",
                                            "experiment_id": eid}).json()
    assert w["matches"][0]["kind"] == "rollout"


def test_save_actions_are_indexed_once_and_searchable():
    ctx = SubscriberContext(subscriber_id="sub-42", subscription_id="I-42", trigger="PAYMENT_FAILED",
                            tenure_days=90, payments_made=3, price=10.0, simulated=True)
    client.post("/save/handle", json=ctx.model_dump(mode="json"))
    first = client.get("/decisions?kind=save").json()["count"]
    assert first == 1 and client.get("/decisions?kind=save").json()["count"] == 1
    w = client.post("/decisions/why", json={"question": "what happened with sub-42?"}).json()
    assert w["matches"][0]["subject"] == "sub-42"


def test_why_with_no_match():
    assert client.post("/decisions/why", json={"question": "zebra"}).json()["matches"] == []


def test_elastic_index_and_search(monkeypatch):
    monkeypatch.setenv("ELASTIC_URL", "http://es.test:9200")
    monkeypatch.setenv("ELASTIC_API_KEY", "k")
    stored = {}

    def put(url, json=None, headers=None, timeout=None):
        assert headers["Authorization"] == "ApiKey k" and "/planlab-decisions/_doc/" in url
        stored[json["id"]] = json
        return httpx.Response(201, json={"result": "created"})

    def post(url, json=None, headers=None, timeout=None):
        assert url.endswith("/planlab-decisions/_search")
        assert json["query"]["bool"]["must"][0]["multi_match"]["query"] == "why winner"
        return httpx.Response(200, json={"hits": {"hits": [{"_source": d} for d in stored.values()]}})
    monkeypatch.setattr(decision_log.httpx, "put", put)
    monkeypatch.setattr(decision_log.httpx, "post", post)
    doc = decision_log.record("experiment", "winner", "Declared arm x the winner.", arm_id="x")
    assert doc["indexed_in_elastic"] is True
    w = decision_log.why("why winner")
    assert w["engine"] == "elastic" and w["matches"][0]["arm_id"] == "x"


def test_elastic_down_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("ELASTIC_URL", "http://es.test:9200")

    def boom(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(decision_log.httpx, "put", boom)
    monkeypatch.setattr(decision_log.httpx, "post", boom)
    doc = decision_log.record("experiment", "stop_arm", "Paused arm y: a guardrail fired.", arm_id="y")
    assert doc["indexed_in_elastic"] is False
    w = decision_log.why("why did y pause")
    assert w["engine"] == "local" and w["matches"][0]["arm_id"] == "y"


# ---- agent tools ---------------------------------------------------------------------------

def test_tool_manifest_is_read_only_and_well_formed():
    tools = client.get("/agent/tools").json()["tools"]
    names = {t["name"] for t in tools}
    assert {"list_experiments", "get_arm_metrics", "explain_decision", "get_cohorts"} <= names
    assert all(t["readOnly"] and t["inputSchema"]["type"] == "object" for t in tools)
    assert not names & {"decide", "approve_rollout", "override_save"}


def test_tools_run_end_to_end():
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    client.post(f"/experiments/{eid}/decide")
    run = lambda n, a=None: client.post(f"/agent/tools/{n}", json=a or {})  # noqa: E731
    assert any(e["id"] == eid for e in run("list_experiments").json()["result"])
    rows = run("get_arm_metrics", {"experiment_id": eid}).json()["result"]["rows"]
    assert {"arm_id", "prob_beats_baseline", "allocation"} <= set(rows[0])
    f = run("filter_arms", {"experiment_id": eid, "question": "arms with churn above baseline"})
    assert f.status_code == 200 and "rows" in f.json()["result"]
    assert run("get_guardrails", {"experiment_id": eid}).json()["result"]["arms"]
    why = run("explain_decision", {"question": "why did it pause", "experiment_id": eid})
    assert why.json()["result"]["matches"]
    assert "actions" in run("get_save_log", {"limit": 3}).json()["result"]
    client.post("/connect/backfill/sample")
    assert run("get_cohorts").json()["result"]["customers"] > 0


def test_tool_errors():
    assert client.post("/agent/tools/nope", json={}).status_code == 404
    assert client.post("/agent/tools/get_arm_metrics", json={}).status_code == 422
