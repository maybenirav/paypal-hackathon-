"""Validate PlanLab's own OpenAPI spec with APIMatic's Validation API.

    cd backend && python scripts/apimatic_validate.py      # writes ../docs/apimatic-validation.md

Needs APIMATIC_API_KEY in backend/.env. Uses POST https://api.apimatic.io/validation/validate-via-file
(the same call APIMatic's own SDK makes). Exit code 1 if APIMatic reports errors, so it can gate CI.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

os.environ.setdefault("SEED_DEMO", "false")
os.environ.setdefault("PLANLAB_DB", ":memory:")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402,F401  (loads .env)
from app.main import app  # noqa: E402

URL = "https://api.apimatic.io/validation/validate-via-file"
OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "apimatic-validation.md"


def validate(spec: dict, key: str, client: httpx.Client | None = None) -> dict:
    c = client or httpx.Client(timeout=60)
    r = c.post(URL, headers={"Authorization": f"X-Auth-Key {key}", "Accept": "application/json"},
               files={"file": ("planlab-openapi.json", json.dumps(spec).encode(), "application/json")})
    if r.status_code >= 400:
        raise RuntimeError(f"APIMatic returned {r.status_code}: {r.text[:300]}")
    return r.json()


def _plain(msg: str) -> str:
    """APIMatic messages carry HTML links; keep the text and the rule URL."""
    link = re.search(r'href="([^"]+)"', msg)
    text = html.unescape(re.sub(r"<[^>]+>", " ", msg.split("(<a")[0])).strip()
    return f"{text} ([rule]({link.group(1)}))" if link else text


def report(result: dict, spec: dict) -> str:
    ops = sum(len(v) for v in spec["paths"].values())
    lines = [
        "# APIMatic validation of PlanLab's API",
        "",
        f"Run on {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `backend/scripts/apimatic_validate.py` "
        f"against APIMatic's Validation API, for {len(spec['paths'])} paths / {ops} operations.",
        "",
        f"- **Valid:** {'yes' if result.get('success') else 'no'}",
        f"- **Errors:** {len(result.get('errors', []))}",
        f"- **Warnings:** {len(result.get('warnings', []))}",
        f"- **Messages:** {len(result.get('messages', []))}",
        "",
    ]
    for title, key in (("Errors", "errors"), ("Warnings", "warnings"), ("Messages", "messages")):
        items = Counter(_plain(m) for m in result.get(key, []))
        if items:
            lines += [f"## {title}", ""] + [f"- {n} x {m}" if n > 1 else f"- {m}" for m, n in items.items()] + [""]
    return "\n".join(lines)


def main() -> int:
    key = os.getenv("APIMATIC_API_KEY")
    if not key:
        print("APIMATIC_API_KEY is not set in backend/.env")
        return 2
    spec = app.openapi()
    result = validate(spec, key)
    OUT.write_text(report(result, spec), encoding="utf-8")
    print(f"valid={result.get('success')} errors={len(result.get('errors', []))} "
          f"warnings={len(result.get('warnings', []))} -> {OUT}")
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
