"""Run one scheduled job against the PlanLab API. Used by the Render cron services.

    python scripts/run_job.py save-sweep        # or: checkpoint, history-refresh

Env: API_URL (e.g. https://planlab-api.onrender.com), JOBS_TOKEN (same value as the API's).
Retries while a sleeping free-tier API wakes up; exits non-zero on failure so Render marks the run failed.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

JOBS = ("save-sweep", "checkpoint", "history-refresh")


def run(job: str, api: str, token: str | None, attempts: int = 4, wait: float = 20.0,
        client: httpx.Client | None = None) -> dict:
    c = client or httpx.Client(timeout=300)
    headers = {"X-Jobs-Token": token} if token else {}
    last = ""
    for i in range(attempts):
        try:
            r = c.post(f"{api.rstrip('/')}/jobs/{job}", headers=headers)
            if r.status_code < 300:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (401, 404, 422):   # retrying will not help
                break
        except httpx.HTTPError as e:
            last = str(e)
        if i < attempts - 1:
            time.sleep(wait)
    raise RuntimeError(f"job {job} failed: {last}")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in JOBS:
        print(f"usage: run_job.py {{{'|'.join(JOBS)}}}")
        return 2
    api = os.getenv("API_URL")
    if not api:
        print("API_URL is not set")
        return 2
    try:
        out = run(sys.argv[1], api, os.getenv("JOBS_TOKEN"))
    except RuntimeError as e:
        print(e)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
