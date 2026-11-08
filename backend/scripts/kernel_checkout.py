"""Person D: command line for the KERNEL checkout bot (the logic lives in app/kernel_signups.py).

Usage (from the backend folder, with the server running):
  python scripts\\kernel_checkout.py --exp <experiment_id> --count 3 --show
Options: --api http://127.0.0.1:8000   --arm <arm_id>   --sequential
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so "app" can be imported

from app import kernel_signups as kernel_bot  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Real PayPal sandbox sign-ups via KERNEL")
    ap.add_argument("--exp", required=True, help="experiment id")
    ap.add_argument("--count", type=int, default=3, help="how many sign-ups")
    ap.add_argument("--api", default="http://127.0.0.1:8000", help="backend base URL")
    ap.add_argument("--arm", default=None, help="force this arm_id")
    ap.add_argument("--show", action="store_true", help="print KERNEL live-view links")
    ap.add_argument("--sequential", action="store_true", help="one at a time instead of parallel")
    args = ap.parse_args()

    try:
        results = kernel_bot.run_signups(args.exp, args.count, args.api, args.arm,
                                         args.show, parallel=not args.sequential)
    except RuntimeError as e:
        kernel_bot.log(str(e))
        return 1
    ok = sum(bool(r and r.get("ok")) for r in results)
    kernel_bot.log(f"Done: {ok}/{len(results)} real sign-ups ACTIVE")
    for r in results:
        kernel_bot.log(f"  {r}")
    return 0 if ok == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
