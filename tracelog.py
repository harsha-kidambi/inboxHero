# Roll No: evernorth-aai-1164386
"""
trace.py - append-only event log (trace.jsonl). Every event carries the run id and the
capability it belongs to (cap=R1 ...), so a grader can filter one capability's evidence.

Event types used: run_start, read, decision, refusal, flag, draft, draft_rejected,
gate, send, preference, commitment, conflict, llm_call, llm_retry, llm_fallback.
"""
import json
import uuid
from datetime import datetime, timezone

import config

_state = {"run_id": None, "cap": "run", "reads": set()}


def start_run(cap: str = "run", **info):
    _state["run_id"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    _state["cap"] = cap
    _state["reads"] = set()
    event("run_start", model=config.describe_model(), **info)
    return _state["run_id"]


def set_cap(cap: str):
    _state["cap"] = cap


def event(_event: str, **data):
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "run": _state["run_id"], "cap": _state["cap"], "event": _event, **data}
    with open(config.TRACE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


def record_read(msg_ids, purpose: str):
    """Retrieval must log what it read; drafts may only cite ids that were read."""
    ids = [m for m in msg_ids if m]
    _state["reads"].update(ids)
    if ids:
        event("read", ids=ids, purpose=purpose)


def was_read(msg_id) -> bool:
    return msg_id in _state["reads"]


def events_for(msg_id=None, cap=None):
    if not config.TRACE_FILE.exists():
        return []
    out = []
    for line in config.TRACE_FILE.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if cap and e.get("cap") != cap:
            continue
        if msg_id and msg_id not in json.dumps(e):
            continue
        out.append(e)
    return out
