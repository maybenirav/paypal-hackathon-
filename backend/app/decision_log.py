"""Decision memory: every agent decision, searchable, so the agent can answer "why did arm B pause?".

Decisions come from three places: the experiment engine (/decide: stop, winner, keep testing),
rollout approvals, and the Save Agent log (indexed lazily from `save_log`, so the Save Agent does
not depend on this module).

Storage is always the local SQLite document store. When ELASTIC_URL is set, every decision is also
indexed in Elasticsearch (index ELASTIC_INDEX, default `planlab-decisions`) and searches use its
full-text ranking; any Elastic error falls back to the local keyword search, so the demo never
depends on Elastic being up.

  GET  /decisions?experiment_id=&kind=&limit=
  POST /decisions/why {question, experiment_id?}
  GET  /decisions/status
"""
from __future__ import annotations

import base64
import logging
import os
import re
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from .local_db import db

log = logging.getLogger("planlab.decisions")
router = APIRouter(prefix="/decisions", tags=["decisions"])

KIND = "decision"
STOP = {"why", "did", "does", "do", "the", "a", "an", "is", "was", "get", "got", "arm", "of", "on",
        "for", "to", "and", "what", "happened", "with", "this", "that", "it", "be", "been", "my"}
SYNONYMS = {"pause": ["stop_arm", "stopped", "pause", "paused", "guardrail"],
            "paused": ["stop_arm", "stopped", "pause", "paused", "guardrail"],
            "stop": ["stop_arm", "stopped"], "stopped": ["stop_arm", "stopped"],
            "win": ["winner"], "won": ["winner"], "winner": ["winner"],
            "save": ["save", "pause", "discount", "downgrade"], "saved": ["save"],
            "cancel": ["let_go", "cancel"], "rollout": ["rollout"], "rolled": ["rollout"]}


# ---- Elastic (optional) ------------------------------------------------------------------

def _es() -> tuple[str, dict] | None:
    url = os.getenv("ELASTIC_URL")
    if not url:
        return None
    headers = {"Content-Type": "application/json"}
    key = (os.getenv("ELASTIC_API_KEY") or "").strip()
    if key:
        headers["Authorization"] = f"ApiKey {encode_api_key(key)}"
    return url.rstrip("/"), headers


def encode_api_key(key: str) -> str:
    """Elastic shows API keys as `id:api_key` or already base64-encoded; the header needs base64."""
    if ":" in key:
        return base64.b64encode(key.encode()).decode()
    return key


def _index() -> str:
    return os.getenv("ELASTIC_INDEX") or "planlab-decisions"


def _es_put(doc: dict) -> bool:
    es = _es()
    if not es:
        return False
    try:
        r = httpx.put(f"{es[0]}/{_index()}/_doc/{doc['id']}", json=doc, headers=es[1], timeout=5)
        return r.status_code < 300
    except httpx.HTTPError as e:
        log.warning("elastic index failed: %s", e)
        return False


def _es_search(question: str, experiment_id: str | None, size: int) -> list[dict] | None:
    es = _es()
    if not es:
        return None
    must: list[dict] = [{"multi_match": {"query": question,
                                         "fields": ["summary^3", "reason^2", "arm_id^4", "kind^2",
                                                    "action^2", "subject"]}}]
    if experiment_id:
        must.append({"term": {"experiment_id.keyword": experiment_id}})
    try:
        r = httpx.post(f"{es[0]}/{_index()}/_search", headers=es[1], timeout=5,
                       json={"size": size, "query": {"bool": {"must": must}}})
        if r.status_code >= 300:
            log.warning("elastic search returned %s, using local search", r.status_code)
            return None
        return [h["_source"] for h in r.json().get("hits", {}).get("hits", [])]
    except (httpx.HTTPError, ValueError) as e:
        log.warning("elastic search failed, using local search: %s", e)
        return None


# ---- recording -----------------------------------------------------------------------------

def record(kind: str, action: str, summary: str, *, experiment_id: str | None = None,
           arm_id: str | None = None, reason: str = "", subject: str | None = None,
           details: dict | None = None, ts: datetime | None = None, id: str | None = None) -> dict:
    doc = {"id": id or uuid.uuid4().hex[:12], "ts": (ts or datetime.now(timezone.utc)).isoformat(),
           "kind": kind, "action": action, "experiment_id": experiment_id, "arm_id": arm_id,
           "subject": subject, "summary": summary, "reason": reason, "details": details or {}}
    db.put(KIND, doc["id"], doc)
    doc["indexed_in_elastic"] = _es_put(doc)
    return doc


def record_decision(exp_id: str, payload: dict) -> None:
    """Called by /experiments/{id}/decide with the response it is about to return."""
    action, arm = payload.get("action"), payload.get("arm_id")
    reasons = payload.get("reasons") or {}
    if action == "keep_testing":
        summary = "Kept testing: no arm is confident enough to win yet."
    elif action == "stop_arm":
        summary = f"Paused arm {arm}: a guardrail fired."
    else:
        summary = f"Declared arm {arm} the winner."
    record("experiment", action, summary, experiment_id=exp_id, arm_id=arm,
           reason=(reasons.get(arm) if arm else " ".join(reasons.values())) or "",
           details={"explanation": payload.get("explanation"), "reasons": reasons})


def record_rollout(exp_id: str, rollout: dict) -> None:
    ok = sum(1 for r in rollout.get("results", []) if r.get("status") != "failed")
    record("rollout", "rollout_approved",
           f"Rolled out arm {rollout.get('winner')} ({rollout.get('kind')}): "
           f"{ok}/{len(rollout.get('requests', []))} PayPal request(s) succeeded.",
           experiment_id=exp_id, arm_id=rollout.get("winner"), details={"kind": rollout.get("kind")})


def _sync_saves() -> None:
    from .save_agent import save_log  # late import: no dependency the other way
    known = {d["id"] for d in db.all(KIND)}
    for a in save_log.actions:
        rid = f"save-{a.id}"
        if rid in known:
            continue
        verb = "let go" if a.offer.kind == "let_go" else f"offered a {a.offer.kind}"
        extra = f" (override of {a.overridden_by}: {a.override_note})" if a.overridden_by else ""
        record("save", a.offer.kind,
               f"Save Agent {verb} to subscriber {a.context.subscriber_id} after "
               f"{a.context.trigger.lower().replace('_', ' ')}{extra}.",
               arm_id=a.context.arm_id, subject=a.context.subscriber_id, reason=a.offer.rationale,
               details={"status": a.status, "reason_text": a.context.reason_text},
               ts=a.ts if a.ts.tzinfo else a.ts.replace(tzinfo=timezone.utc), id=rid)


def all_decisions(experiment_id: str | None = None, kind: str | None = None) -> list[dict]:
    _sync_saves()
    docs = db.all(KIND)
    if experiment_id:  # Save Agent actions are merchant-wide, so they stay in every view
        docs = [d for d in docs if d.get("experiment_id") == experiment_id or d["kind"] == "save"]
    if kind:
        docs = [d for d in docs if d["kind"] == kind]
    return sorted(docs, key=lambda d: d["ts"], reverse=True)


# ---- retrieval -----------------------------------------------------------------------------

def _terms(q: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9_\-]+", q.lower()) if w not in STOP]
    out = []
    for w in words:
        out.append(w)
        out.extend(SYNONYMS.get(w, []))
    return out


def local_search(question: str, docs: list[dict], size: int = 5) -> list[dict]:
    terms = _terms(question)
    if not terms:
        return docs[:size]
    scored = []
    for d in docs:
        hay = " ".join(str(d.get(k) or "") for k in ("summary", "reason", "kind", "action",
                                                     "arm_id", "subject")).lower()
        score = sum(hay.count(t) for t in terms)
        # an exact arm / subscriber match dominates
        score += sum(10 for t in terms if t in (str(d.get("arm_id") or "").lower(),
                                               str(d.get("subject") or "").lower()))
        if score:
            scored.append((score, d))
    scored.sort(key=lambda x: (x[0], x[1]["ts"]), reverse=True)
    return [d for _, d in scored[:size]]


def why(question: str, experiment_id: str | None = None, size: int = 5) -> dict:
    docs = all_decisions(experiment_id)
    hits = _es_search(question, experiment_id, size)
    engine = "elastic"
    if hits is None:
        hits, engine = local_search(question, docs, size), "local"
    if not hits:
        return {"question": question, "answer": "I have no recorded decision that matches.",
                "matches": [], "engine": engine}
    top = hits[0]
    answer = f"{top['summary']} {top.get('reason') or ''}".strip()
    when = top["ts"][:10]
    answer = f"On {when}: {answer}"
    try:
        from .agent import llm
        if llm.enabled():
            ctx = "\n".join(f"- {h['ts'][:10]} {h['summary']} {h.get('reason', '')}" for h in hits)
            answer = llm.generate(
                "Answer the merchant's question in 1-2 sentences using only these logged agent "
                f"decisions. Quote the numbers they contain; do not invent any.\nQuestion: {question}\n"
                f"Decisions:\n{ctx}", temperature=0.2) or answer
    except Exception:  # the template answer stands
        pass
    return {"question": question, "answer": answer, "matches": hits, "engine": engine}


# ---- routes --------------------------------------------------------------------------------

class WhyRequest(BaseModel):
    question: str
    experiment_id: str | None = None


@router.get("")
def list_decisions(experiment_id: str | None = None, kind: str | None = None, limit: int = 50):
    docs = all_decisions(experiment_id, kind)
    return {"count": len(docs), "decisions": docs[:limit]}


@router.post("/why")
def ask_why(req: WhyRequest):
    return why(req.question, req.experiment_id)


@router.get("/status")
def status():
    es = _es()
    if not es:
        return {"elastic": False, "index": None, "note": "ELASTIC_URL not set; local search only"}
    try:
        r = httpx.get(f"{es[0]}/{_index()}/_count", headers=es[1], timeout=5)
        return {"elastic": r.status_code < 300, "index": _index(),
                "count": r.json().get("count") if r.status_code < 300 else None}
    except httpx.HTTPError as e:
        return {"elastic": False, "index": _index(), "error": str(e)}
