import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import backfill
from app.main import app

client = TestClient(app)

_spec = importlib.util.spec_from_file_location(
    "run_job", Path(__file__).resolve().parent.parent / "scripts" / "run_job.py")
run_job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_job)


def make(scenario):
    eid = client.post("/experiments", json={"goal": "raise annual uptake", "num_arms": 3}).json()["id"]
    client.post(f"/experiments/{eid}/demo", json={"scenario": scenario})
    return eid


def test_jobs_need_the_token_when_set(monkeypatch):
    monkeypatch.setenv("JOBS_TOKEN", "s3cret")
    assert client.post("/jobs/save-sweep").status_code == 401
    assert client.post("/jobs/save-sweep", headers={"X-Jobs-Token": "wrong"}).status_code == 401
    assert client.post("/jobs/save-sweep", headers={"X-Jobs-Token": "s3cret"}).status_code == 200
    assert client.get("/jobs").json()["protected"] is True


def test_checkpoint_stops_risky_arm_and_skips_winners():
    risky, won = make("guardrail"), make("winner")
    client.post(f"/experiments/{won}/decide")                 # already has a winner
    out = client.post("/jobs/checkpoint").json()["result"]
    ids = {d["experiment_id"]: d for d in out["decisions"]}
    assert won not in ids and ids[risky]["action"] == "stop_arm"
    last = {j["name"]: j["last_run"] for j in client.get("/jobs").json()["jobs"]}
    assert last["checkpoint"]["result"]["checked"] == out["checked"]


def test_save_sweep_job_handles_cancel_attempts():
    make("winner")
    out = client.post("/jobs/save-sweep").json()["result"]
    assert out["handled"] > 0


def test_history_refresh_only_after_paypal_import(monkeypatch):
    assert "skipped" in client.post("/jobs/history-refresh").json()["result"]
    client.post("/connect/backfill/sample")                    # sample data is not PayPal: still skipped
    assert "skipped" in client.post("/jobs/history-refresh").json()["result"]

    def fake(method, path, json=None, params=None):
        if path == "/v1/billing/plans":
            return {"plans": [], "total_pages": 1}
        return {"total_pages": 1, "transaction_details": []}
    monkeypatch.setattr(backfill, "paypal_request", fake)
    client.post("/connect/backfill?months=1")
    assert client.post("/jobs/history-refresh?months=1").json()["result"] == {"customers": 0, "transactions": 0}


def test_runner_retries_then_succeeds_and_stops_on_auth_error():
    calls = []

    def flaky(request):
        calls.append(request.headers.get("X-Jobs-Token"))
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json={"ok": True})
    c = httpx.Client(transport=httpx.MockTransport(flaky))
    assert run_job.run("checkpoint", "http://api/", "tok", wait=0, client=c) == {"ok": True}
    assert calls == ["tok", "tok"]

    denied = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    with pytest.raises(RuntimeError, match="401"):
        run_job.run("checkpoint", "http://api", None, wait=0, client=denied)
