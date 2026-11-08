"""Person D: KERNEL checkout bot - real PayPal sandbox sign-ups in KERNEL cloud browsers.

Used by:
  * scripts/kernel_checkout.py   (command line)
  * app/demo_routes.py           (the one-call "Run demo")
(Named kernel_signups.py so it never clashes with the Save Agent's app/kernel_bot.py.)

For each sign-up it:
  1. calls our API  POST /experiments/{id}/paypal/subscribe   -> subscription_id + approve_url
  2. opens approve_url in a KERNEL cloud browser
  3. logs in as a sandbox Personal buyer (US accounts!) and clicks Agree & Subscribe
  4. calls our API  POST /experiments/{id}/paypal/sync/{subscription_id}  until ACTIVE

Needs in backend/.env: KERNEL_API_KEY, PAYPAL_SANDBOX_BUYER_1_EMAIL / _PASSWORD (2, 3 ...)
Optional: DEMO_API_URL (default http://127.0.0.1:$PORT, which also works on Render)
On failure a screenshot is saved in backend/kernel_logs/ (not committed).
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from . import config  # noqa: F401  (loads backend/.env)

BACKEND = Path(__file__).resolve().parent.parent
LOG_DIR = BACKEND / "kernel_logs"
DEFAULT_API = os.getenv("DEMO_API_URL") or f"http://127.0.0.1:{os.getenv('PORT', '8000')}"

# PayPal changes its pages sometimes, so each step tries several selectors.
EMAIL_FIELDS = ["#email", "input[name='login_email']", "input[type='email']"]
NEXT_BUTTONS = ["#btnNext", "button:has-text('Next')"]
PASSWORD_FIELDS = ["#password", "input[name='login_password']", "input[type='password']"]
LOGIN_BUTTONS = ["#btnLogin", "button:has-text('Log In')", "button[type='submit']"]
SUBSCRIBE_BUTTONS = [
    "#confirmButtonTop",                                   # PayPal's classic checkout button
    "input[type='submit'][value*='Subscribe' i]",          # <input> buttons keep text in value=
    "input[type='submit'][value*='Agree' i]",
    "#consentButton",
    "button:has-text('Agree & Subscribe')",
    "text=Agree & Subscribe",
    "[data-testid='submit-button-initial']",
    "button:has-text('Subscribe')",
    "button:has-text('Agree & Continue')",
    "button:has-text('Continue')",                         # intermediate step, not the final click
]
STEP_BUTTONS = {"button:has-text('Agree & Continue')", "button:has-text('Continue')"}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _short(e: Exception) -> str:
    """Playwright errors are huge; keep the first line."""
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:200]


def load_buyers() -> list[tuple[str, str]]:
    buyers = []
    for n in range(1, 10):
        email = os.getenv(f"PAYPAL_SANDBOX_BUYER_{n}_EMAIL")
        password = os.getenv(f"PAYPAL_SANDBOX_BUYER_{n}_PASSWORD")
        if email and password:
            buyers.append((email, password))
    return buyers


def api_call(client: httpx.Client, method: str, path: str, json: dict | None = None,
             tries: int = 5) -> dict:
    """Call our backend. It answers 502 when PayPal hiccups (e.g. sandbox 503), so retry those."""
    for i in range(tries):
        try:
            r = client.request(method, path, json=json)
        except httpx.TransportError as e:
            log(f"  {path} -> network error {type(e).__name__}, retrying ...")
            time.sleep(3 * (i + 1))
            continue
        if r.status_code < 400:
            return r.json()
        if r.status_code < 500:
            raise RuntimeError(f"{path} -> {r.status_code}: {r.text[:300]}")
        log(f"  {path} -> {r.status_code} (PayPal busy?), retrying in {3 * (i + 1)}s ...")
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"{path} still failing after {tries} tries")


def first_visible(page, selectors: list[str], timeout: float):
    """Return (selector, locator) for the first selector visible within `timeout` s, else None."""
    end = time.time() + timeout
    while time.time() < end:
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if loc.is_visible():
                    return sel, loc
            except Exception:
                pass
        time.sleep(0.5)
    return None


def screenshot(page, name: str) -> None:
    try:
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"{name}_{datetime.now():%H%M%S}.png"
        page.screenshot(path=str(path), full_page=True)
        log(f"  screenshot saved: {path}")
    except Exception as e:
        log(f"  (could not save screenshot: {_short(e)})")


def _text(loc) -> str:
    try:
        return (loc.inner_text(timeout=2000) or loc.get_attribute("value") or "").strip()[:40]
    except Exception:
        return "?"


def approve_in_browser(page, approve_url: str, email: str, password: str, tag: str) -> None:
    page.goto(approve_url, wait_until="domcontentloaded", timeout=60_000)
    log(f"{tag} PayPal page opened")

    found = first_visible(page, EMAIL_FIELDS, 30)
    if found:
        found[1].fill(email)
        nxt = first_visible(page, NEXT_BUTTONS, 3)
        if nxt:
            nxt[1].click()
        pw = first_visible(page, PASSWORD_FIELDS, 20)
        if not pw:
            raise RuntimeError("password field not found")
        pw[1].fill(password)
        btn = first_visible(page, LOGIN_BUTTONS, 5)
        if not btn:
            raise RuntimeError("login button not found")
        btn[1].click()
        log(f"{tag} logged in as buyer")

    # Usually two clicks: "Continue", then "Agree & Subscribe".
    for rnd in range(1, 4):
        found = first_visible(page, SUBSCRIBE_BUTTONS, 45 if rnd == 1 else 20)
        if not found:
            if "sorry" in page.content().lower():
                raise RuntimeError("PayPal showed its 'We're sorry' error page")
            raise RuntimeError("Subscribe button not found")
        sel, btn = found
        log(f"{tag} click {rnd}: {_text(btn)!r}")
        page.wait_for_timeout(1500)   # let PayPal finish loading before clicking
        btn.click()
        final = sel not in STEP_BUTTONS
        try:
            page.wait_for_url(lambda u: "paypal.com" not in u, timeout=15_000 if final else 6_000)
            log(f"{tag} redirected back from PayPal")
            return
        except Exception:
            if final:   # approved; the redirect to our frontend just didn't load - sync confirms
                return
    raise RuntimeError("never reached the Agree & Subscribe button")


def checkout_one(kernel, playwright, client, exp_id: str, arm_id: str | None,
                 email: str, password: str, show: bool, tag: str) -> dict:
    sub = api_call(client, "POST", f"/experiments/{exp_id}/paypal/subscribe",
                   {"arm_id": arm_id} if arm_id else {})
    sid, url = sub["subscription_id"], sub["approve_url"]
    log(f"{tag} subscription {sid} on arm '{sub['arm_id']}'")
    if not url:
        raise RuntimeError("no approve_url returned")

    kb = kernel.browsers.create(stealth=True)
    if show:
        log(f"{tag} live view: {kb.browser_live_view_url}")
    browser = None
    try:
        browser = playwright.chromium.connect_over_cdp(kb.cdp_ws_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            approve_in_browser(page, url, email, password, tag)
        except Exception:
            screenshot(page, sid)
            raise
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        try:
            kernel.browsers.delete_by_id(kb.session_id)
        except Exception:
            pass

    for _ in range(10):  # PayPal can take a few seconds to mark it ACTIVE
        res = api_call(client, "POST", f"/experiments/{exp_id}/paypal/sync/{sid}")
        log(f"{tag} sync: status={res['status']} added={res['added']}")
        if res["status"] == "ACTIVE":
            return {"subscription_id": sid, "arm_id": sub["arm_id"], "ok": True}
        time.sleep(3)
    return {"subscription_id": sid, "arm_id": sub["arm_id"], "ok": False}


def missing_packages() -> list[str]:
    """Packages the bot needs that aren't installed (pip install -r requirements.txt)."""
    import importlib.util
    return [p for p in ("kernel", "playwright") if importlib.util.find_spec(p) is None]


def _worker(i: int, count: int, buyers, exp_id, api, arm, show, out: list) -> None:
    email, password = buyers[i % len(buyers)]
    tag = f"[{i + 1}/{count}]"
    log(f"{tag} sign-up as buyer {i % len(buyers) + 1}")
    res: dict = {"ok": False, "error": "not started"}
    try:
        from kernel import Kernel
        from playwright.sync_api import sync_playwright

        kernel = Kernel()  # reads KERNEL_API_KEY
        with httpx.Client(base_url=api, timeout=60) as client, sync_playwright() as pw:
            for attempt in (1, 2):
                try:
                    res = checkout_one(kernel, pw, client, exp_id, arm, email, password, show, tag)
                    break
                except Exception as e:
                    log(f"{tag} attempt {attempt} failed: {_short(e)}")
                    res = {"ok": False, "error": _short(e)}
    except Exception as e:
        res = {"ok": False, "error": _short(e)}
    out[i] = res


def run_signups(exp_id: str, count: int, api: str | None = None, arm: str | None = None,
                show: bool = False, parallel: bool = True) -> list[dict]:
    """Do `count` real sign-ups (in parallel by default). Returns one result dict per sign-up."""
    if not os.getenv("KERNEL_API_KEY"):
        raise RuntimeError("KERNEL_API_KEY missing in backend/.env")
    if missing_packages():
        raise RuntimeError(f"missing packages {missing_packages()} - run: pip install -r requirements.txt")
    if count < 1:
        return []
    buyers = load_buyers()
    if not buyers:
        raise RuntimeError("No PAYPAL_SANDBOX_BUYER_N_EMAIL / _PASSWORD found in backend/.env")
    api = (api or DEFAULT_API).rstrip("/\\")
    out: list = [None] * count
    args = (count, buyers, exp_id, api, arm, show, out)
    if parallel:
        threads = [threading.Thread(target=_worker, args=(i, *args)) for i in range(count)]
        for t in threads:
            t.start()
            time.sleep(1)   # stagger so PayPal/KERNEL aren't hit at the same instant
        for t in threads:
            t.join()
    else:
        for i in range(count):
            _worker(i, *args)
    return out
