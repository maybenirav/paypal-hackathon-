"""Tells people when PlanLab stops a price by itself, by Slack and email.

A stop can happen while nobody has the workspace open, so this is what gets it noticed. An alert waits in
the stop's own record and goes out on a background thread: a slow or broken channel can't delay or undo the
stop, and one that's down gets tried again, even after a restart.

Only automatic stops on real data alert anyone. Simulated runs are demos, and someone running the analysis
is already looking at the result.
"""
from __future__ import annotations

import html
import logging
import smtplib
import ssl
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Callable

import httpx

from . import config
from .events import utcnow
from .store import Experiment, store

log = logging.getLogger("planlab.alerts")
logging.getLogger("httpx").setLevel(logging.WARNING)    # it logs every URL at INFO, and a Slack webhook URL is a secret

TIMEOUT_SECONDS = 10
RETRY_MINUTES = (1, 5, 15, 60)      # waits between tries, after which the alert counts as failed
WORKER_SECONDS = 30
TEST_GAP_SECONDS = 10
MAX_TEXT = 500

_wake = threading.Event()
_sending = threading.Lock()
_last_test = float("-inf")


class _NoTls(Exception):
    pass


def channels() -> list[str]:
    """The channels set up on this server. Their addresses are secrets, so only the names leave this module."""
    found = []
    if config.ALERT_WEBHOOK_URL:
        found.append("webhook")
    if config.ALERT_EMAIL_TO and config.SMTP_HOST:
        found.append("email")
    return found


def queue(exp: Experiment, arm_id: str) -> None:
    """Ask for an alert about a stop that was just recorded. Sending happens on the background thread."""
    if not channels():
        return
    exp.stops[arm_id]["alert"] = {"status": "pending", "attempts": 0, "sent": [], "sent_at": None,
                                  "next_try": None, "error": None}
    _wake.set()


def _clean(value: str) -> str:
    """One short line. Names and goals are free text, and a line break in an email header would add a header."""
    return " ".join(str(value).split())[:MAX_TEXT]


def _describe(exp: Experiment, stop: dict) -> tuple[str, str]:
    name = _clean(next((a.name for a in exp.arms if a.arm_id == stop["arm_id"]), stop["arm_id"]))
    text = (f'PlanLab stopped the price option "{name}" automatically.\n\n'
            f"Experiment: {_clean(exp.goal)}\n"
            f"Why: {_clean(stop['reasons'])}\n\n"
            "New customers now get the other options. Existing subscribers keep their plan.\n"
            f"Review or undo it: {config.FRONTEND_URL}/?exp={exp.id}")
    return f'PlanLab stopped "{name}"', text


def _send_webhook(_subject: str, text: str) -> None:
    # Slack treats & < > as markup, which would let a price name ping the whole channel
    body = {"text": html.escape(text, quote=False)}
    httpx.post(config.ALERT_WEBHOOK_URL, json=body, timeout=TIMEOUT_SECONDS).raise_for_status()


def _send_email(subject: str, text: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.SMTP_FROM
    message["To"] = config.ALERT_EMAIL_TO
    message.set_content(text)
    tls = ssl.create_default_context()
    direct_tls = config.SMTP_PORT == 465
    client = (smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=TIMEOUT_SECONDS, context=tls) if direct_tls
              else smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=TIMEOUT_SECONDS))
    with client as smtp:
        if not direct_tls:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls(context=tls)
                smtp.ehlo()
            elif config.SMTP_USERNAME:
                raise _NoTls("the mail server doesn't offer encryption, so the password wasn't sent")
        if config.SMTP_USERNAME:
            smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        smtp.send_message(message)


_SENDERS = {"webhook": _send_webhook, "email": _send_email}


def _why(exc: Exception) -> str:
    """Why a send failed, in words that can't leak the webhook URL or a password. These get shown and stored."""
    if isinstance(exc, _NoTls):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"the server answered {exc.response.status_code}"
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return "the webhook setting isn't a valid URL"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "the mail server refused the username or password"
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return "the mail server refused the address"
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "it timed out"
    if isinstance(exc, (httpx.TransportError, OSError, smtplib.SMTPException)):
        return "couldn't reach the server"
    return "something unexpected went wrong"


def _send(names: list[str], subject: str, text: str) -> dict[str, str]:
    """Try each channel, so one failing doesn't stop the other. Returns the ones that failed, and why."""
    failed = {}
    for name in names:
        try:
            _SENDERS[name](subject, text)
        except Exception as exc:
            failed[name] = _why(exc)
            log.warning("Couldn't send the %s alert: %s (%s)", name, failed[name], type(exc).__name__)
    return failed


def _deliver(exp: Experiment, stop: dict, now: datetime) -> None:
    alert = stop["alert"]
    waiting = [name for name in channels() if name not in alert["sent"]]    # never repeat one that worked
    failed = _send(waiting, *_describe(exp, stop))
    alert["sent"] += [name for name in waiting if name not in failed]
    alert["attempts"] += 1
    alert["error"] = "; ".join(f"{name}: {why}" for name, why in failed.items()) or None
    alert["next_try"] = None
    if not alert["sent"] and not waiting:
        alert["status"], alert["error"] = "failed", "no alert channels are set up any more"
    elif not failed:
        alert["status"], alert["sent_at"] = "sent", now.isoformat()
    elif alert["attempts"] > len(RETRY_MINUTES):
        alert["status"] = "failed"
    else:
        alert["next_try"] = (now + timedelta(minutes=RETRY_MINUTES[alert["attempts"] - 1])).isoformat()


def deliver_pending(now: datetime | None = None) -> bool:
    """Send every alert that's due. Returns whether anything changed, so the caller knows to save."""
    now = now or utcnow()
    changed = False
    with _sending:
        for exp in store.list():
            for stop in list(exp.stops.values()):
                alert = stop.get("alert")
                if not alert or alert["status"] != "pending":
                    continue
                if alert["next_try"] and datetime.fromisoformat(alert["next_try"]) > now:
                    continue
                changed = True
                try:
                    _deliver(exp, stop, now)
                except Exception:   # one bad alert must not stop the others
                    log.exception("Couldn't send the alert for experiment %s", exp.id)
                    alert["status"], alert["error"] = "failed", "something unexpected went wrong"
    return changed


def may_test() -> bool:
    """A test alert posts to a real channel, so a stuck button or a stranger can't flood it."""
    global _last_test
    if time.monotonic() - _last_test < TEST_GAP_SECONDS:
        return False
    _last_test = time.monotonic()
    return True


def send_test() -> dict:
    names = channels()
    failed = _send(names, "PlanLab test alert",
                   "This is a test from PlanLab. When a price option is stopped automatically, the alert comes here.")
    return {"sent": [name for name in names if name not in failed], "failed": failed}


def start(on_change: Callable[[], None], seconds: float = WORKER_SECONDS) -> threading.Event:
    """Send alerts in the background, starting with any a restart interrupted. Set the returned event to stop it."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                if deliver_pending():
                    on_change()
            except Exception:
                log.exception("Couldn't send alerts")
            _wake.wait(seconds)
            _wake.clear()

    threading.Thread(target=loop, name="alert-sender", daemon=True).start()
    return stop
