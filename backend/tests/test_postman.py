"""Replays the Postman 'Demo flow' against the app, so the collection cannot drift from the API."""
import importlib.util
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

_spec = importlib.util.spec_from_file_location(
    "export_postman", Path(__file__).resolve().parent.parent / "scripts" / "export_postman.py")
export_postman = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_postman)

client = TestClient(app)


def _fill(text: str, env: dict) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(env.get(m.group(1), m.group(0))), text)


def test_demo_flow_runs_in_order():
    collection, environment = export_postman.build()
    assert collection["info"]["schema"].endswith("/v2.1.0/collection.json")
    assert environment["values"][0]["key"] == "baseUrl"
    flow = collection["item"][0]
    env: dict = {}
    for step in flow["item"]:
        req = step["request"]
        path = _fill(req["url"]["raw"].replace("{{baseUrl}}", ""), env)
        assert "{{" not in path, f"{step['name']}: unresolved variable in {path}"
        body = json.loads(_fill(req["body"]["raw"], env)) if "body" in req else None
        r = client.request(req["method"], path, json=body)
        assert r.status_code < 300, f"{step['name']}: {r.status_code} {r.text[:200]}"
        for line in step["event"][0]["script"]["exec"]:
            m = re.match(r"pm\.collectionVariables\.set\('(\w+)', pm\.response\.json\(\)\.(\w+)\);", line)
            if m:
                env[m.group(1)] = r.json()[m.group(2)]
    assert env["experimentId"] and env["pausedArm"]


def test_every_route_is_in_the_collection():
    collection, _ = export_postman.build()
    names = {i["name"] for f in collection["item"][1:] for i in f["item"]}
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            assert f"{method.upper()} {path}" in names


def test_operation_ids_are_short_and_unique():
    ids = [op["operationId"] for ops in app.openapi()["paths"].values() for op in ops.values()]
    assert len(ids) == len(set(ids))
    assert max(map(len, ids)) <= 40
