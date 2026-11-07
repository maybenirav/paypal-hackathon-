"""KERNEL cloud browsers act as sandbox buyers.

PayPal subscriptions only become ACTIVE after a buyer approves them on PayPal's site. To get
real (sandbox) sign-ups into an experiment without a human clicking through every checkout,
a KERNEL-hosted Chromium logs in as one of the sandbox buyer accounts and approves the
subscription (or a Save Agent plan change). Playwright drives it over CDP.

Needs KERNEL_API_KEY and PAYPAL_SANDBOX_BUYER_<n>_EMAIL / _PASSWORD in .env. Sandbox only.
"""
from __future__ import annotations

import logging
import os
import re
import time

from app import config

log = logging.getLogger("planlab.kernel")

_APPROVE = re.compile(r"(agree|subscribe|continue|accept|approve|confirm|save)", re.I)


def buyer(n: int = 1) -> tuple[str, str]:
    email = os.getenv(f"PAYPAL_SANDBOX_BUYER_{n}_EMAIL", "")
    password = os.getenv(f"PAYPAL_SANDBOX_BUYER_{n}_PASSWORD", "")
    if not email or not password:
        raise RuntimeError(f"PAYPAL_SANDBOX_BUYER_{n}_EMAIL / _PASSWORD missing in .env")
    return email, password


def approve(approve_url: str, buyer_n: int = 1, screenshot_dir: str | None = None,
            timeout_s: int = 120, attempts: int = 2) -> dict:
    """Open `approve_url` in a KERNEL browser, log in as a sandbox buyer and approve.

    Cloud browser sessions occasionally hang on load; each retry uses a fresh browser.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            res = _approve_once(approve_url, buyer_n, screenshot_dir, timeout_s)
            res["attempt"] = i + 1
            return res
        except Exception as exc:
            last = exc
            log.warning("KERNEL approval attempt %d failed: %s", i + 1, str(exc)[:200])
    raise RuntimeError(f"KERNEL approval failed after {attempts} attempts: {last}")


def _approve_once(approve_url: str, buyer_n: int, screenshot_dir: str | None,
                  timeout_s: int) -> dict:
    if config.PAYPAL_ENV == "live":
        raise RuntimeError("refusing to automate a live PayPal checkout")
    from kernel import Kernel
    from playwright.sync_api import sync_playwright

    email, password = buyer(buyer_n)
    k = Kernel(api_key=os.getenv("KERNEL_API_KEY"))
    browser = k.browsers.create(stealth=True, timeout_seconds=timeout_s + 60)
    steps: list[str] = []

    def shot(page, name: str) -> None:
        steps.append(name)
        if screenshot_dir:  # diagnostics only: never fail the checkout over a screenshot
            try:
                os.makedirs(screenshot_dir, exist_ok=True)
                page.screenshot(path=os.path.join(screenshot_dir, f"{len(steps):02d}-{name}.png"),
                                timeout=10_000)
            except Exception:
                pass

    try:
        with sync_playwright() as pw:
            cdp = pw.chromium.connect_over_cdp(browser.cdp_ws_url)
            ctx = cdp.contexts[0] if cdp.contexts else cdp.new_context()
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(45_000)
            page.goto(approve_url, wait_until="domcontentloaded", timeout=60_000)
            shot(page, "opened")

            page.locator("#email").wait_for(state="visible", timeout=60_000)
            page.locator("#email").fill(email)
            nxt = page.locator("#btnNext")
            if nxt.count() and nxt.is_visible():
                nxt.click()
            page.locator("#password").fill(password)
            page.locator("#btnLogin").click()
            shot(page, "logged-in")

            deadline = time.time() + timeout_s
            clicked = False
            while time.time() < deadline:
                url = page.url
                if config.FRONTEND_URL in url or "/subscribe/success" in url or "ba_token" in url \
                        and "paypal.com" not in url:
                    break
                btn = page.get_by_role("button", name=_APPROVE)
                for sel in ("#consentButton", "[data-testid='submit-button-initial']",
                            "#confirmButtonTop"):
                    if page.locator(sel).count() and page.locator(sel).first.is_visible():
                        btn = page.locator(sel).first
                        break
                if btn.count() and btn.first.is_visible() and btn.first.is_enabled():
                    shot(page, "review")
                    btn.first.click()
                    clicked = True
                    page.wait_for_timeout(4000)
                    if "paypal.com" not in page.url:
                        break
                else:
                    page.wait_for_timeout(1500)
            shot(page, "done")
            final_url = page.url
            cdp.close()
        return {"approved": clicked, "final_url": final_url, "steps": steps,
                "browser": "kernel", "session_id": getattr(browser, "session_id", None)}
    finally:
        try:
            k.browsers.delete_by_id(browser.session_id)
        except Exception as exc:  # pragma: no cover - best effort cleanup
            log.warning("could not delete KERNEL browser: %s", exc)
