# Roll No: evernorth-aai-1164386
"""
gate.py - the ONLY code path that can do something irreversible.

Irreversible: send_reply, forward (writes a file to outbox/ = "sent").
Gated policy change: set_preference (reversible, but changes every future run).
Delete: not implemented anywhere. There is no delete action for anything to call.

Three modes (at least one approval route is always required):
  dry-run      show exactly what would be written, write nothing
  interactive  y/n per action at the terminal
  approve      explicit per-action approval by id on the command line (--approve A,B)

Defence in depth (Part 6): before executing, every action is checked against the mail
store. It may not touch a quarantined message, and every recipient must be justified
by the store: the sender of the message being answered, an internal colleague, or a
CC address from an approved preference. An address that only appears *inside* a
message body (e.g. archive@mail-backup-service.info) can never be a recipient.
"""
import json
import re
from datetime import datetime, timezone

import config
import prefs
import tracelog as trace


def _log(action, human, result, detail=""):
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "aid": action["aid"],
             "type": action["type"], "msg_ids": action["msg_ids"], "proposed": _describe(action),
             "human": human, "result": result, "detail": detail}
    with open(config.GATE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    trace.event("gate", aid=action["aid"], human=human, result=result, detail=detail)
    return entry


def _describe(a):
    if a["type"] == "set_preference":
        return f"save preference: {a['summary']}"
    return f"{a['type']} to {a['to']}" + (f" (cc {a['cc']})" if a.get("cc") else "") + f": {a['subject']}"


def allowed_recipients(store, action, quarantined):
    ok = {m["from"].lower() for m in store.messages if m["from"].lower().endswith("@" + config.ORG_DOMAIN)}
    for mid in action["msg_ids"]:
        if mid in quarantined:
            return None
        m = store.get(mid)
        if m:
            ok.add(m["from"].lower())
            ok.add(m["to"].lower())
    for p in prefs.load().values():
        if p.get("cc"):
            ok.add(p["cc"].lower())
    ok.discard(config.OWNER.lower())
    return ok


def check(store, action, quarantined):
    if action["type"] == "set_preference":
        return None if action["preference"]["type"] in prefs.ALLOWED_TYPES else "unsupported preference type"
    allowed = allowed_recipients(store, action, quarantined)
    if allowed is None:
        return "action touches a quarantined message"
    recipients = [action["to"]] + ([action["cc"]] if action.get("cc") else [])
    bad = [r for r in recipients if r.lower() not in allowed]
    if bad:
        return f"recipient(s) {bad} are not justified by the mail store"
    return None


def _execute(store, action, approved_by):
    if action["type"] == "set_preference":
        p = {k: v for k, v in action["preference"].items() if k != "summary"}
        prefs.set_pref(p, source=action["msg_ids"][0], approved_by=approved_by)
        trace.event("preference", key=prefs.key_for(p), value=p, source=action["msg_ids"][0])
        return f"saved to {config.PREFS_FILE.name}"
    config.OUTBOX_DIR.mkdir(exist_ok=True)
    out = {"id": action["aid"], "from": config.OWNER, "to": action["to"], "cc": action.get("cc"),
           "subject": action["subject"], "body": action["body"], "in_reply_to": action["msg_ids"],
           "cites": action.get("cites", []), "approved_by": approved_by,
           "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if action["type"] == "forward":
        out["forwarded"] = [{k: store.get(i)[k] for k in ("id", "from", "subject", "timestamp", "body")}
                            for i in action.get("forwarded", [])]
    path = config.OUTBOX_DIR / f"{action['aid']}.json"
    path.write_text(json.dumps(out, indent=2))
    trace.event("send", aid=action["aid"], to=action["to"], file=str(path.relative_to(config.BASE_DIR)))
    return f"written to {path.relative_to(config.BASE_DIR)}"


def review(store, actions, quarantined, mode="dry-run", approve=(), reject=(), types=None, verbose=True):
    """Put each proposed action in front of the human. Returns a list of gate log entries."""
    approve = {a.strip() for a in approve if a.strip()}
    reject = {a.strip() for a in reject if a.strip()}
    results = []
    for a in actions:
        if types and a["type"] not in types:
            continue
        problem = check(store, a, quarantined)
        if verbose:
            print(f"\n[{a['aid']}] {_describe(a)}")
            print(f"    risk={a['risk']}  irreversible={a['irreversible']}  why a human: {a['why_human']}")
            if a.get("body"):
                print("    " + a["body"].replace("\n", "\n    "))
            if a.get("cites"):
                print(f"    cites: {a['cites']}")
        if problem:
            results.append(_log(a, "n/a", "BLOCKED", problem))
            if verbose:
                print(f"    BLOCKED by gate: {problem}")
            continue
        ids = {a["aid"], *a["msg_ids"]}
        if mode == "dry-run":
            where = "prefs.json" if a["type"] == "set_preference" else f"outbox/{a['aid']}.json"
            results.append(_log(a, "dry-run", "not executed", f"would write {where}"))
            if verbose:
                print(f"    DRY-RUN: would write {where}; nothing written.")
            continue
        if mode == "interactive":
            answer = input("    Approve? [y/N] ").strip().lower()
            human = "approved (interactive)" if answer == "y" else "rejected (interactive)"
        elif ids & reject:
            human = "rejected (--reject)"
        elif ids & approve:
            human = "approved (--approve)"
        else:
            human = "no decision"
        if human.startswith("approved"):
            result = _execute(store, a, human)
            results.append(_log(a, human, "EXECUTED", result))
        elif human.startswith("rejected"):
            results.append(_log(a, human, "REJECTED", "owner said no; nothing written"))
        else:
            results.append(_log(a, human, "HELD", "no decision yet; left in pending; nothing written"))
        if verbose:
            print(f"    {human.upper()} -> {results[-1]['result']} {results[-1]['detail']}")
    return results


def outbox_files():
    return sorted(config.OUTBOX_DIR.glob("*.json")) if config.OUTBOX_DIR.exists() else []


def gate_status():
    """Latest gate outcome per action id (for the dashboard)."""
    status = {}
    if config.GATE_LOG.exists():
        for line in config.GATE_LOG.read_text().splitlines():
            e = json.loads(line)
            status[e["aid"]] = e
    return status
