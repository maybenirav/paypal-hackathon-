"""Generate the PlanLab Postman collection (v2.1) and environment from the live OpenAPI schema.

    cd backend && python scripts/export_postman.py            # writes ../postman/*.json

The collection has one folder per API area (generated from every route, so it never drifts
from the code) and a "Demo flow" folder that runs the whole story in order with the Postman
Collection Runner: create an experiment, run the guardrail scenario, decide, ask why, run the
Save Agent, import history. Each step saves the ids the next step needs.

To publish: Postman > Import both files into a public workspace, set the environment's
baseUrl to the Render URL, and share the workspace link.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SEED_DEMO", "false")
os.environ.setdefault("PLANLAB_DB", ":memory:")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "postman"
SCHEMA_ID = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


def _resolve(schema: dict, spec: dict) -> dict:
    while "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = spec["components"]["schemas"][name]
    return schema


def example(schema: dict, spec: dict, depth: int = 0) -> object:
    """A plausible example value for a JSON schema (defaults first, then by type)."""
    schema = _resolve(schema, spec)
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [s for s in schema[key] if s.get("type") != "null"]
            return example(options[0], spec, depth) if options else None
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        if depth > 3:
            return {}
        required = set(schema.get("required", []))
        return {k: example(v, spec, depth + 1) for k, v in schema.get("properties", {}).items()
                if k in required or "default" in _resolve(v, spec)}
    if t == "array":
        return [example(schema.get("items", {}), spec, depth + 1)]
    return {"string": "string", "integer": 0, "number": 0, "boolean": False}.get(t)


def _url(path: str, query: list[dict] | None = None) -> dict:
    # /experiments/{exp_id}/decide -> {{baseUrl}}/experiments/:exp_id/decide
    # ({{postmanVariable}} segments stay as they are; "?a=b" becomes a query entry)
    if "?" in path:
        path, qs = path.split("?", 1)
        query = (query or []) + [{"key": k, "value": v} for k, v in
                                 (pair.split("=", 1) for pair in qs.split("&"))]
    parts = [":" + p[1:-1] if p.startswith("{") and not p.startswith("{{") else p
             for p in path.strip("/").split("/")]
    variables = [{"key": p[1:], "value": "{{experimentId}}" if p[1:] == "exp_id" else ""}
                 for p in parts if p.startswith(":")]
    url = {"raw": "{{baseUrl}}/" + "/".join(parts), "host": ["{{baseUrl}}"], "path": parts}
    if variables:
        url["variable"] = variables
    if query:
        url["query"] = query
        enabled = [q for q in query if not q.get("disabled")]
        if enabled:
            url["raw"] += "?" + "&".join(f"{q['key']}={q['value']}" for q in enabled)
    return url


def _request(method: str, path: str, body: object = None, query: list[dict] | None = None,
             description: str = "") -> dict:
    req = {"method": method.upper(), "header": [], "url": _url(path, query),
           "description": description}
    if body is not None:
        req["header"] = [{"key": "Content-Type", "value": "application/json"}]
        req["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2),
                       "options": {"raw": {"language": "json"}}}
    return req


def generated_folders(spec: dict) -> list[dict]:
    folders: dict[str, list[dict]] = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            tag = (op.get("tags") or [path.strip("/").split("/")[0] or "root"])[0]
            body = None
            content = (op.get("requestBody") or {}).get("content", {}).get("application/json")
            if content:
                body = example(content["schema"], spec)
            query = [{"key": p["name"], "value": str(example(p.get("schema", {}), spec) or ""),
                      "disabled": not p.get("required", False)}
                     for p in op.get("parameters", []) if p.get("in") == "query"]
            folders.setdefault(tag, []).append({
                "name": f"{method.upper()} {path}",
                "request": _request(method, path, body, query or None,
                                    op.get("summary") or op.get("description") or "")})
    return [{"name": name, "item": items} for name, items in sorted(folders.items())]


def _step(name: str, method: str, path: str, body: object = None, *, save: dict | None = None,
          checks: list[str] | None = None, description: str = "") -> dict:
    lines = ["pm.test('status is 2xx', () => pm.expect(pm.response.code).to.be.below(300));"]
    lines += checks or []
    for var, expr in (save or {}).items():
        lines.append(f"pm.collectionVariables.set('{var}', {expr});")
    return {"name": name, "request": _request(method, path, body, description=description),
            "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": lines}}]}


def demo_flow() -> dict:
    exp = "/experiments/{{experimentId}}"
    return {"name": "Demo flow (run in order)", "description": "The 3-minute demo as API calls.", "item": [
        _step("1. Health", "GET", "/health"),
        _step("2. Agent designs an experiment", "POST", "/experiments",
              {"goal": "Raise annual-plan uptake without hurting 60-day retention.",
               "current_price": 15, "num_arms": 3, "monthly_visitors": 12000},
              save={"experimentId": "pm.response.json().id"},
              checks=["pm.test('2-4 arms', () => pm.expect(pm.response.json().arms.length).to.be.within(2, 4));"]),
        _step("3. Run the guardrail scenario (synthetic cohort)", "POST", f"{exp}/demo",
              {"scenario": "guardrail"}),
        _step("4. Metrics, credible intervals, traffic split", "GET", f"{exp}/metrics"),
        _step("5. Guardrail status", "GET", f"{exp}/guardrails"),
        _step("6. Decide: the agent pauses the risky arm", "POST", f"{exp}/decide",
              save={"pausedArm": "pm.response.json().arm_id"},
              checks=["pm.test('an arm was stopped', () => pm.expect(pm.response.json().action).to.eql('stop_arm'));"]),
        _step("7. Ask why it paused", "POST", "/decisions/why",
              {"question": "why did arm {{pausedArm}} pause?", "experiment_id": "{{experimentId}}"},
              checks=["pm.test('answer found', () => pm.expect(pm.response.json().matches).to.not.be.empty);"]),
        _step("8. Ask the grid", "POST", f"{exp}/ask", {"question": "arms with churn above baseline"}),
        _step("9. Save Agent: handle failed payments and cancel intents", "POST", "/save/sweep?limit=10"),
        _step("10. Save Agent audit log", "GET", "/save/log"),
        _step("11. Import 12 months of history (sample)", "POST", "/connect/backfill/sample?months=12"),
        _step("12. Cohorts", "GET", "/connect/cohorts"),
        _step("13. Tools for outside agents", "GET", "/agent/tools"),
    ]}


def build() -> tuple[dict, dict]:
    spec = app.openapi()
    collection = {
        "info": {"name": "PlanLab API", "schema": SCHEMA_ID,
                 "description": "AI pricing experiments and a Save Agent on PayPal Subscriptions. "
                                "Set baseUrl in the PlanLab environment, then run 'Demo flow'."},
        "variable": [{"key": "experimentId", "value": ""}, {"key": "pausedArm", "value": ""}],
        "item": [demo_flow(), *generated_folders(spec)],
    }
    environment = {"name": "PlanLab", "values": [
        {"key": "baseUrl", "value": "http://localhost:8000", "type": "default", "enabled": True}]}
    return collection, environment


def main() -> None:
    collection, environment = build()
    OUT.mkdir(exist_ok=True)
    (OUT / "PlanLab.postman_collection.json").write_text(json.dumps(collection, indent=2), encoding="utf-8")
    (OUT / "PlanLab.postman_environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")
    n = sum(len(f["item"]) for f in collection["item"])
    print(f"wrote {n} requests to {OUT}")


if __name__ == "__main__":
    main()
