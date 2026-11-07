"""Person D: KERNEL checkout bot - real PayPal sandbox sign-ups in KERNEL cloud browsers.

For each sign-up it:
  1. calls our API  POST /experiments/{id}/paypal/subscribe   -> subscription_id + approve_url
  2. opens approve_url in a KERNEL cloud browser
  3. logs in as a sandbox Personal buyer and clicks Subscribe
  4. calls our API  POST /experiments/{id}/paypal/sync/{subscription_id}  until ACTIVE

Usage (from the backend folder, with the server running):
  python scripts\\kernel_checkout.py --exp <experiment_id> --count 3
Options:
  --api http://localhost:8000   where our backend runs (or the Render URL later)
  --arm <arm_id>                force one arm (default: Thompson allocation picks)
  --show                        print the KERNEL live-view link to watch the browser

Needs in backend/.env: KERNEL_API_KEY, PAYPAL_SANDBOX_BUYER_1_EMAIL / _PASSWORD (2, 3 ...)
On failure a screenshot is saved in backend/kernel_logs/ (do not commit that folder).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND / ".env")
LOG_DIR = BACKEND / "kernel_logs"

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
    "button:has-text('Continue')",                         # last resort: intermediate step
]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


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
        log(f"  (could not save screenshot: {e})")


def _text(loc) -> str:
    try:
        return (loc.inner_text(timeout=2000) or loc.get_attribute("value") or "").strip()[:40]
    except Exception:
        return "?"


def approve_in_browser(page, approve_url: str, email: str, password: str, tag: str) -> None:
    page.goto(approve_url, wait_until="domcontentloaded", timeout=60_000)
    log("  PayPal page opened")

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
        log("  logged in as buyer")

    # PayPal may show more than one step (e.g. review page, then Agree & Subscribe),
    # so keep clicking the consent button until PayPal sends us back.
    for rnd in range(1, 4):
        found = first_visible(page, SUBSCRIBE_BUTTONS, 45 if rnd == 1 else 15)
        if not found:
            if "sorry" in page.content().lower():
                raise RuntimeError("PayPal showed its 'We're sorry' error page")
            if rnd == 1:
                raise RuntimeError("Subscribe button not found")
            break
        sel, btn = found
        log(f"  click {rnd}: {sel} (text: {_text(btn)!r})")
        page.wait_for_timeout(1500)   # let PayPal finish loading before clicking
        btn.click()
        try:
            page.wait_for_url(lambda u: "paypal.com" not in u, timeout=25_000)
            log("  redirected back from PayPal")
            return
        except Exception:
            screenshot(page, f"{tag}_after_click{rnd}")
    log("  still on PayPal after clicking (see screenshots in kernel_logs)")


def checkout_one(kernel, playwright, client, exp_id: str, arm_id: str | None,
                 email: str, password: str, show: bool) -> dict:
    sub = api_call(client, "POST", f"/experiments/{exp_id}/paypal/subscribe",
                   {"arm_id": arm_id} if arm_id else {})
    sid, url = sub["subscription_id"], sub["approve_url"]
    log(f"  subscription {sid} on arm '{sub['arm_id']}'")
    if not url:
        raise RuntimeError("no approve_url returned")

    kb = kernel.browsers.create(stealth=True)
    if show:
        log(f"  live view: {kb.browser_live_view_url}")
    browser = None
    try:
        browser = playwright.chromium.connect_over_cdp(kb.cdp_ws_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            approve_in_browser(page, url, email, password, sid)
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

    for _ in range(8):  # PayPal can take a few seconds to mark it ACTIVE
        res = api_call(client, "POST", f"/experiments/{exp_id}/paypal/sync/{sid}")
        log(f"  sync: status={res['status']} added={res['added']}")
        if res["status"] == "ACTIVE":
            return {"subscription_id": sid, "arm_id": sub["arm_id"], "ok": True}
        time.sleep(5)
    return {"subscription_id": sid, "arm_id": sub["arm_id"], "ok": False}


def main() -> int:
    ap = argparse.ArgumentParser(description="Real PayPal sandbox sign-ups via KERNEL")
    ap.add_argument("--exp", required=True, help="experiment id")
    ap.add_argument("--count", type=int, default=3, help="how many sign-ups")
    ap.add_argument("--api", default="http://localhost:8000", help="backend base URL")
    ap.add_argument("--arm", default=None, help="force this arm_id")
    ap.add_argument("--show", action="store_true", help="print KERNEL live-view links")
    args = ap.parse_args()

    if not os.getenv("KERNEL_API_KEY"):
        log("KERNEL_API_KEY missing in backend/.env")
        return 1
    buyers = load_buyers()
    if not buyers:
        log("No PAYPAL_SANDBOX_BUYER_N_EMAIL / _PASSWORD found in backend/.env")
        return 1

    from kernel import Kernel
    from playwright.sync_api import sync_playwright

    kernel = Kernel()  # reads KERNEL_API_KEY
    results = []
    with httpx.Client(base_url=args.api, timeout=60) as client, sync_playwright() as pw:
        for i in range(args.count):
            email, password = buyers[i % len(buyers)]
            log(f"Sign-up {i + 1}/{args.count} as buyer {i % len(buyers) + 1}")
            for attempt in (1, 2):
                try:
                    results.append(checkout_one(kernel, pw, client, args.exp, args.arm,
                                                email, password, args.show))
                    break
                except Exception as e:
                    log(f"  attempt {attempt} failed: {e}")
                    if attempt == 2:
                        results.append({"ok": False, "error": str(e)})

    ok = sum(r["ok"] for r in results)
    log(f"Done: {ok}/{len(results)} real sign-ups ACTIVE")
    for r in results:
        log(f"  {r}")
    return 0 if ok == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
