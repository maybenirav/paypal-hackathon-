"""Alerts about automatic stops: they reach people, survive failures, and never leak their settings."""
import email
import json
import logging
import socketserver
import threading
import time
from datetime import datetime, timedelta
from email import policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app import alerts, config, watcher
from app.events import utcnow
from app.main import app
from app.persistence import Persistence
from app.store import store

client = TestClient(app)
SECRET = "T000/B000/SECRETPATH"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(alerts, "_last_test", float("-inf"))


class Webhook:
    """A real local HTTP server standing in for Slack. Set `answers` to make its next replies fail."""

    def __init__(self):
        self.received, self.answers, self.calls = [], [], 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.calls += 1
                status = owner.answers.pop(0) if owner.answers else 200
                if status == 200:
                    owner.received.append(json.loads(body))
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def slack(monkeypatch):
    hook = Webhook()
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", f"http://127.0.0.1:{hook.server.server_port}/services/{SECRET}")
    yield hook
    hook.server.shutdown()
    hook.server.server_close()


class MailServer:
    """A real local SMTP server, without encryption, that records what it's told."""

    def __init__(self):
        self.commands, self.messages = [], []
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def say(self, line):
                self.wfile.write(line.encode() + b"\r\n")

            def handle(self):
                self.say("220 fake mail")
                while line := self.rfile.readline().decode(errors="replace").strip():
                    verb = line.split()[0].upper()
                    owner.commands.append(verb)
                    if verb == "DATA":
                        self.say("354 go")
                        data = b""
                        while (chunk := self.rfile.readline()) != b".\r\n":
                            data += chunk
                        owner.messages.append(email.message_from_bytes(data, policy=policy.default))
                    elif verb == "QUIT":
                        self.say("221 bye")
                        return
                    self.say("250 ok")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def mail(monkeypatch):
    fake = MailServer()
    monkeypatch.setattr(config, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SMTP_PORT", fake.server.server_address[1])
    monkeypatch.setattr(config, "SMTP_USERNAME", "")
    monkeypatch.setattr(config, "SMTP_FROM", "planlab@example.com")
    monkeypatch.setattr(config, "ALERT_EMAIL_TO", "ops@example.com, owner@example.com")
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def make(goal="raise annual uptake"):
    return client.post("/experiments", json={"goal": goal, "num_arms": 3}).json()["id"]


def real_events(eid, arm, count, refunds=0):
    events = []
    for i in range(count):
        base = {"experiment_id": eid, "arm_id": arm, "subscriber_id": f"{arm}-{i}", "ts": "2026-10-01T10:00:00", "source": "real"}
        events += [{**base, "type": kind, "amount": amount} for kind, amount in (("VISIT", 0), ("SIGNUP", 0), ("PAYMENT", 10))]
        if i < refunds:
            events.append({**base, "type": "REFUND", "amount": 10})
    return events


def add_real_data(eid, refunds=30):
    client.post(f"/experiments/{eid}/events", json=real_events(eid, "baseline", 60) + real_events(eid, "annual_discount", 60, refunds))


def risky(goal="raise annual uptake"):
    """An experiment on real data where the automatic check has just stopped the annual plan."""
    eid = make(goal)
    add_real_data(eid)
    assert watcher.check_experiment(store.get(eid)) == ["annual_discount"]
    return eid


def alert_of(eid, arm="annual_discount"):
    return store.get(eid).stops[arm]["alert"]


def later(minutes):
    return utcnow() + timedelta(minutes=minutes)


def test_an_automatic_stop_on_real_data_reaches_slack(slack):
    eid = risky()
    assert alerts.deliver_pending()
    assert len(slack.received) == 1
    text = slack.received[0]["text"]
    assert "Annual, 2 months free" in text and "raise annual uptake" in text and "Refund rate 50.0%" in text
    assert f"/?exp={eid}" in text and "keep their plan" in text
    alert = client.get(f"/experiments/{eid}").json()["stops"][0]["alert"]
    assert alert["status"] == "sent" and alert["sent"] == ["webhook"] and alert["sent_at"] and alert["error"] is None
    assert not alerts.deliver_pending() and len(slack.received) == 1    # once only


def test_the_same_alert_reaches_email(mail):
    eid = risky()
    alerts.deliver_pending()
    message = mail.messages[0]
    assert message["Subject"] == 'PlanLab stopped "Annual, 2 months free"'
    assert message["To"] == "ops@example.com, owner@example.com" and message["From"] == "planlab@example.com"
    assert "Refund rate 50.0%" in message.get_content() and f"/?exp={eid}" in message.get_content()
    assert mail.commands.count("RCPT") == 2
    assert alert_of(eid)["sent"] == ["email"]


def test_changing_a_limit_alerts_too(slack):
    eid = make()
    add_real_data(eid, refunds=8)
    assert watcher.check_experiment(store.get(eid)) == []
    client.put(f"/experiments/{eid}/guardrails", json={"refund_ceiling": 0.01})
    alerts.deliver_pending()
    assert "Annual, 2 months free" in slack.received[0]["text"]


def test_simulated_runs_alert_nobody(slack):
    eid = make()
    client.post(f"/experiments/{eid}/demo", json={"scenario": "guardrail"})
    assert store.get(eid).stops["monthly_trial"]["alert"] is None
    client.put(f"/experiments/{eid}/guardrails", json={"retention_floor": 0.9999})     # stops another, on day 60
    assert not alerts.deliver_pending() and slack.calls == 0


def test_a_stop_from_the_analysis_alerts_nobody(slack):
    eid = make()
    client.put(f"/experiments/{eid}/guardrails", json={"auto_stop": False})
    add_real_data(eid)
    assert client.post(f"/experiments/{eid}/decide").json()["action"] == "stop_arm"
    assert not alerts.deliver_pending() and slack.calls == 0


def test_without_settings_stops_still_work_and_nothing_is_sent():
    eid = risky()
    assert alert_of(eid) is None and store.get(eid).stopped_arms == ["annual_discount"]
    assert not alerts.deliver_pending()
    assert client.get(f"/experiments/{eid}/metrics").json()["auto_checks"]["alerts"] == []


def test_the_page_can_see_which_channels_are_on_but_not_their_addresses(slack, mail):
    eid = make()
    body = client.get(f"/experiments/{eid}/metrics")
    assert body.json()["auto_checks"]["alerts"] == ["webhook", "email"]
    assert SECRET not in body.text and "ops@example.com" not in body.text


def test_the_stop_never_waits_for_the_alert(monkeypatch):
    # An address that would hang: if the stop waited for it, this would take the whole timeout
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "http://10.255.255.1/hook")
    eid = make()
    add_real_data(eid)
    started = time.monotonic()
    assert watcher.sweep() == {eid: ["annual_discount"]}
    assert time.monotonic() - started < 2
    assert alert_of(eid)["status"] == "pending"


def test_slack_markup_in_free_text_cant_ping_a_channel(slack):
    risky("Test <!channel> & <https://evil.example|click>")
    alerts.deliver_pending()
    text = slack.received[0]["text"]
    assert "<!channel>" not in text and "&lt;!channel&gt;" in text and "&amp;" in text and "<https" not in text


def test_a_line_break_in_a_name_cant_add_an_email_header(mail):
    eid = risky("goal\nBcc: attacker@example.com")
    store.get(eid).arms[1].name = "Annual\r\nBcc: attacker@example.com"
    alerts.deliver_pending()
    message = mail.messages[0]
    assert message["Bcc"] is None and mail.commands.count("RCPT") == 2
    assert "\n" not in message["Subject"]


def test_a_failed_send_is_retried_later_and_only_when_due(slack):
    slack.answers = [500]
    eid = risky()
    assert alerts.deliver_pending()
    alert = alert_of(eid)
    assert alert["status"] == "pending" and alert["attempts"] == 1 and alert["error"] == "webhook: the server answered 500"
    assert datetime.fromisoformat(alert["next_try"]) > utcnow()
    assert not alerts.deliver_pending() and slack.calls == 1     # not due yet
    assert alerts.deliver_pending(later(2))
    assert alert_of(eid)["status"] == "sent" and alert_of(eid)["error"] is None and len(slack.received) == 1


def test_it_gives_up_after_the_last_retry_and_says_so(slack):
    slack.answers = [503] * 20
    eid = risky()
    alerts.deliver_pending()
    minutes = 0
    for wait in alerts.RETRY_MINUTES:
        minutes += wait + 1
        assert alerts.deliver_pending(later(minutes))
    alert = alert_of(eid)
    assert alert["status"] == "failed" and alert["attempts"] == len(alerts.RETRY_MINUTES) + 1
    assert alert["next_try"] is None and "503" in alert["error"]
    assert not alerts.deliver_pending(later(100_000))    # no more tries
    assert slack.calls == alert["attempts"]


def test_a_channel_that_worked_isnt_repeated_while_another_retries(slack, mail, monkeypatch):
    real = alerts._SENDERS["email"]
    down = {"yes": True}

    def flaky(subject, text):
        if down["yes"]:
            raise OSError("no route")
        real(subject, text)
    monkeypatch.setitem(alerts._SENDERS, "email", flaky)
    eid = risky()
    alerts.deliver_pending()
    assert alert_of(eid)["sent"] == ["webhook"] and alert_of(eid)["error"] == "email: couldn't reach the server"
    down["yes"] = False
    alerts.deliver_pending(later(2))
    assert alert_of(eid)["status"] == "sent" and sorted(alert_of(eid)["sent"]) == ["email", "webhook"]
    assert len(slack.received) == 1 and len(mail.messages) == 1


def test_the_webhook_address_is_never_shown_or_logged(slack, caplog):
    caplog.set_level(logging.INFO)
    slack.answers = [404]
    eid = risky()
    alerts.deliver_pending()
    assert SECRET not in caplog.text
    assert SECRET not in client.get(f"/experiments/{eid}").text


def test_an_unreachable_webhook_gives_a_plain_reason(monkeypatch):
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", f"http://127.0.0.1:9/services/{SECRET}")
    eid = risky()
    alerts.deliver_pending()
    assert alert_of(eid)["error"] == "webhook: couldn't reach the server"


def test_a_bad_webhook_setting_gives_a_plain_reason(monkeypatch):
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "hooks.slack.com/services/SECRET")
    eid = risky()
    alerts.deliver_pending()
    assert alert_of(eid)["error"] == "webhook: the webhook setting isn't a valid URL"


def test_a_password_isnt_sent_to_a_mail_server_without_encryption(mail, monkeypatch):
    monkeypatch.setattr(config, "SMTP_USERNAME", "user")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "hunter2")
    eid = risky()
    alerts.deliver_pending()
    assert "AUTH" not in mail.commands and mail.messages == []
    assert "encryption" in alert_of(eid)["error"] and "hunter2" not in alert_of(eid)["error"]


def test_encryption_starts_before_the_password_is_sent(monkeypatch):
    steps = []

    class Smtp:
        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def has_extn(self, name):
            return name == "starttls"

        def starttls(self, context):
            steps.append("starttls")

        def login(self, username, password):
            steps.append("login")

        def send_message(self, message):
            steps.append("send")
    monkeypatch.setattr(alerts.smtplib, "SMTP", Smtp)
    for name, value in (("SMTP_HOST", "mail.example.com"), ("SMTP_PORT", 587), ("SMTP_USERNAME", "user"),
                        ("SMTP_PASSWORD", "pw"), ("SMTP_FROM", "a@example.com"), ("ALERT_EMAIL_TO", "b@example.com")):
        monkeypatch.setattr(config, name, value)
    assert alerts.send_test() == {"sent": ["email"], "failed": {}}
    assert steps == ["starttls", "login", "send"]


def test_resuming_before_it_goes_out_cancels_the_alert(slack):
    eid = risky()
    client.post(f"/experiments/{eid}/arms/annual_discount/resume")
    assert not alerts.deliver_pending() and slack.calls == 0


def test_removing_the_settings_fails_the_alert_instead_of_leaving_it_waiting(slack, monkeypatch):
    eid = risky()
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "")
    alerts.deliver_pending()
    assert alert_of(eid)["status"] == "failed" and "no alert channels" in alert_of(eid)["error"]


def test_one_bad_alert_doesnt_stop_the_others(slack, monkeypatch):
    bad, good = make(), make()      # both first, because a stop teaches the next design to drop that price
    for eid in (bad, good):
        add_real_data(eid)
    watcher.sweep()
    real = alerts._describe

    def flaky(exp, stop):
        if exp.id == bad:
            raise RuntimeError("boom")
        return real(exp, stop)
    monkeypatch.setattr(alerts, "_describe", flaky)
    alerts.deliver_pending()
    assert alert_of(bad)["status"] == "failed" and alert_of(good)["status"] == "sent"


def test_an_alert_waiting_during_a_restart_still_goes_out_once(slack, tmp_path):
    saved = Persistence(str(tmp_path / "test.db"))
    eid = risky()
    saved.save()
    saved.close()
    store._exps.clear()
    reopened = Persistence(str(tmp_path / "test.db"))
    reopened.load()
    assert alert_of(eid)["status"] == "pending"
    alerts.deliver_pending()
    alerts.deliver_pending()
    assert len(slack.received) == 1
    reopened.close()


def test_the_background_sender_delivers_by_itself_and_saves(slack):
    saves = []
    stop = alerts.start(lambda: saves.append(1), seconds=0.02)
    try:
        eid = risky()
        deadline = time.monotonic() + 5
        while not (saves and slack.received) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(slack.received) == 1 and saves and alert_of(eid)["status"] == "sent"
    finally:
        stop.set()


def test_the_background_check_and_sender_work_together_with_no_clicks(slack):
    eid = make()
    add_real_data(eid)
    stop_checks = watcher.start(lambda: None, seconds=0.02)
    stop_alerts = alerts.start(lambda: None, seconds=0.02)
    try:
        deadline = time.monotonic() + 5
        while not slack.received and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop_checks.set()
        stop_alerts.set()
    assert "Annual, 2 months free" in slack.received[0]["text"]
    assert store.get(eid).stopped_arms == ["annual_discount"]


def test_the_test_button_reports_each_channel(slack, mail):
    body = client.post("/alerts/test").json()
    assert sorted(body["sent"]) == ["email", "webhook"] and body["failed"] == {}
    assert "test from PlanLab" in slack.received[0]["text"] and mail.messages[0]["Subject"] == "PlanLab test alert"


def test_the_test_button_explains_a_failure_without_the_address(slack):
    slack.answers = [403]
    response = client.post("/alerts/test")
    assert response.json() == {"sent": [], "failed": {"webhook": "the server answered 403"}}
    assert SECRET not in response.text


def test_the_test_button_needs_settings_and_cant_flood_a_channel(slack, monkeypatch):
    assert client.post("/alerts/test").status_code == 200
    assert client.post("/alerts/test").status_code == 429
    assert slack.calls == 1
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "")
    assert client.post("/alerts/test").status_code == 409
