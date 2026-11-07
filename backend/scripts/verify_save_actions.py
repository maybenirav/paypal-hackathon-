"""Prove every Save Agent action against the real PayPal sandbox.

Creates a base plan plus the Save Agent's Lite and discount plans, signs a sandbox buyer up
through a KERNEL browser, then runs pause (suspend + activate), downgrade (revise to Lite),
discount (revise to the discount plan) and let-go (cancel), checking PayPal's status after
each step. Prints a JSON report.

    cd backend && python scripts/verify_save_actions.py [--screens DIR] [--skip-approve-revise]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, kernel_bot, paypal_sdk  # noqa: E402
from app.paypal_plans import build_discount_plan_payload, build_plan_payload  # noqa: E402
from app.schemas import Arm  # noqa: E402

report: dict = {"steps": []}


def step(name: str, ok: bool, **info) -> None:
    report["steps"].append({"step": name, "ok": ok, **info})
    print(f"[{'OK' if ok else 'FAIL'}] {name} {json.dumps(info, default=str)[:300]}", flush=True)


def wait_status(sub_id: str, want: str, secs: int = 60) -> dict:
    sub = {}
    for _ in range(secs // 3):
        sub = paypal_sdk.get_subscription(sub_id)
        if sub.get("status") == want:
            break
        time.sleep(3)
    return sub


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screens", default=None, help="folder for KERNEL screenshots")
    ap.add_argument("--skip-approve-revise", action="store_true")
    args = ap.parse_args()
    pid = config.PAYPAL_PRODUCT_ID
    base = Arm(arm_id="verify_base", name="Verify base", price=15.0)
    lite = base.model_copy(update={"arm_id": "verify_lite", "name": "Verify Lite", "price": 9.0})

    base_plan = paypal_sdk.create_plan(build_plan_payload(base, pid, "verify"))
    lite_plan = paypal_sdk.create_plan(build_plan_payload(lite, pid, "verify"))
    disc_plan = paypal_sdk.create_plan(build_discount_plan_payload(base, 20, 3, pid))
    step("create plans via APIMatic SDK", True, base=base_plan["id"], lite=lite_plan["id"],
         discount=disc_plan["id"])

    sub = paypal_sdk.create_subscription({
        "plan_id": base_plan["id"], "custom_id": "verify:verify_base",
        "application_context": {"brand_name": config.PAYPAL_BRAND_NAME, "user_action": "SUBSCRIBE_NOW",
                                "shipping_preference": "NO_SHIPPING",
                                "return_url": config.PAYPAL_RETURN_URL,
                                "cancel_url": config.PAYPAL_CANCEL_URL}})
    sub_id = sub["id"]
    step("create subscription", True, id=sub_id, status=sub.get("status"))

    res = kernel_bot.approve(paypal_sdk.approve_url(sub), screenshot_dir=args.screens)
    sub = wait_status(sub_id, "ACTIVE")
    step("KERNEL buyer approves checkout", sub.get("status") == "ACTIVE",
         status=sub.get("status"), kernel=res)
    if sub.get("status") != "ACTIVE":
        print(json.dumps(report, indent=2, default=str))
        return 1

    paypal_sdk.suspend(sub_id, "Customer-requested pause")
    s = wait_status(sub_id, "SUSPENDED", 30)
    step("pause = suspend", s.get("status") == "SUSPENDED", status=s.get("status"))
    paypal_sdk.activate(sub_id, "Pause ended")
    s = wait_status(sub_id, "ACTIVE", 30)
    step("resume = activate", s.get("status") == "ACTIVE", status=s.get("status"))

    for kind, plan in (("downgrade", lite_plan), ("discount", disc_plan)):
        try:
            r = paypal_sdk.revise(sub_id, {"plan_id": plan["id"]})
        except Exception as e:  # report and continue
            step(f"{kind} = revise to {plan['id']}", False, error=str(e)[:400])
            continue
        url = paypal_sdk.approve_url(r)
        step(f"{kind} = revise to {plan['id']}", True, needs_buyer_approval=bool(url),
             plan_changed=r.get("plan_overridden"))
        if url and not args.skip_approve_revise:
            a = kernel_bot.approve(url, screenshot_dir=args.screens)
            time.sleep(5)
            s = paypal_sdk.get_subscription(sub_id)
            step(f"{kind}: KERNEL buyer approves plan change", s.get("plan_id") == plan["id"],
                 plan_id=s.get("plan_id"), status=s.get("status"), kernel=a)

    paypal_sdk.cancel(sub_id, "Subscriber chose to leave")
    s = wait_status(sub_id, "CANCELLED", 30)
    step("let go = cancel", s.get("status") == "CANCELLED", status=s.get("status"))
    print(json.dumps(report, indent=2, default=str))
    return 0 if all(x["ok"] for x in report["steps"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
