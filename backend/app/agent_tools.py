"""PlanLab as tools for outside agents (AG Studio custom agent, WebMCP, any MCP-style client).

  GET  /agent/tools              manifest: name, description, JSON-Schema input per tool
  POST /agent/tools/{name}       run one tool: body = the tool's arguments

Only read-only tools are exposed. Anything that moves money or changes an experiment
(decide, rollout, Save Agent overrides) stays behind the merchant's own buttons, so an outside
agent can explain and chart PlanLab but cannot act for the merchant.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/agent/tools", tags=["agent-tools"])

_EXP = {"experiment_id": {"type": "string", "description": "Experiment id from list_experiments"}}


def _main():
    from . import main  # late import: main includes this router
    return main


def _list_experiments(_: dict) -> Any:
    return _main().list_experiments()


def _arm_metrics(a: dict) -> Any:
    m = _main().metrics(a["experiment_id"])
    keep = ("arm_id", "name", "is_baseline", "status", "visitors", "signups", "paid", "conversion",
            "paid_conversion", "churn", "mrr", "mrr_per_visitor", "prob_beats_baseline",
            "prob_best", "allocation", "conv_ci", "refund_rate", "dispute_rate", "projected_ltv")
    return {"status": m.get("status"), "winner": m.get("winner"),
            "rows": [{k: r[k] for k in keep if k in r} for r in m.get("rows", [])]}


def _filter_arms(a: dict) -> Any:
    main = _main()
    return main.experiment_ask(a["experiment_id"], main.AskRequest(question=a["question"]))


def _guardrails(a: dict) -> Any:
    return _main().get_guardrails(a["experiment_id"])


def _why(a: dict) -> Any:
    from .decision_log import why
    return why(a["question"], a.get("experiment_id"))


def _save_log(a: dict) -> Any:
    log = _main().get_save_log()
    return {"summary": log["summary"], "actions": log["actions"][: int(a.get("limit") or 20)]}


def _cohorts(_: dict) -> Any:
    from .backfill import cohorts
    return cohorts()


TOOLS: dict[str, dict] = {
    "list_experiments": {
        "description": "List the merchant's pricing experiments with their status.",
        "input_schema": {"type": "object", "properties": {}}, "fn": _list_experiments},
    "get_arm_metrics": {
        "description": "Per-arm results of one experiment: conversion, paid conversion, MRR, churn, "
                       "probability of beating the baseline, traffic share, refund and dispute rates.",
        "input_schema": {"type": "object", "properties": _EXP, "required": ["experiment_id"]},
        "fn": _arm_metrics},
    "filter_arms": {
        "description": "Ask a question about an experiment's arms in plain English, e.g. 'arms with "
                       "churn above baseline'. Returns matching rows plus a grid filter and chart hint.",
        "input_schema": {"type": "object", "required": ["experiment_id", "question"],
                         "properties": {**_EXP, "question": {"type": "string"}}},
        "fn": _filter_arms},
    "get_guardrails": {
        "description": "Guardrail status per arm: retention floor, refund and dispute ceilings.",
        "input_schema": {"type": "object", "properties": _EXP, "required": ["experiment_id"]},
        "fn": _guardrails},
    "explain_decision": {
        "description": "Answer 'why' questions from the log of agent decisions, e.g. 'why did arm "
                       "monthly_trial pause?' or 'why was subscriber X offered a discount?'.",
        "input_schema": {"type": "object", "required": ["question"],
                         "properties": {"question": {"type": "string"}, **_EXP}},
        "fn": _why},
    "get_save_log": {
        "description": "Recent Save Agent actions (pause, downgrade, discount, let go) with reasons.",
        "input_schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
        "fn": _save_log},
    "get_cohorts": {
        "description": "Monthly cohort retention and revenue from the merchant's imported PayPal history.",
        "input_schema": {"type": "object", "properties": {}}, "fn": _cohorts},
}


def manifest() -> list[dict]:
    return [{"name": n, "description": t["description"], "inputSchema": t["input_schema"],
             "readOnly": True} for n, t in TOOLS.items()]


def call(name: str, args: dict) -> Any:
    tool = TOOLS.get(name)
    if tool is None:
        raise HTTPException(404, f"unknown tool '{name}'")
    missing = [k for k in tool["input_schema"].get("required", []) if not args.get(k)]
    if missing:
        raise HTTPException(422, f"missing argument(s): {', '.join(missing)}")
    fn: Callable[[dict], Any] = tool["fn"]
    return fn(args)


@router.get("")
def list_tools():
    return {"tools": manifest()}


@router.post("/{name}")
def run_tool(name: str, args: dict | None = None):
    return {"tool": name, "result": call(name, args or {})}
