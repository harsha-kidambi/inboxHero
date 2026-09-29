# Roll No: evernorth-aai-1164386
"""
prefs.py - standing instructions that survive restarts (prefs.json).

Built on Assignment 5's memory.py idea (JSON file, atomic write, one key = one current
value, last write wins, old value kept in history) with one addition that matters for
safety: an ALLOWLIST of preference types. A preference can only change *what the
assistant proposes* (which times to offer, who to CC). No preference type exists that
could switch off the approval gate, enable auto-send, or allow deletes - so an email
that "saves itself as a standing preference" (m039) has nothing to write to.

Supported types:
  no_meetings_before   {"time": "11:00"}                      key: no_meetings_before
  cc_on_sender_domain  {"domain": "x.com", "cc": "a@b.com"}   key: cc_on_sender_domain:x.com

Preferences found in mail are only PROPOSED; they become active after approval
(see gate.py), because a standing instruction is a policy change.
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone

import config

ALLOWED_TYPES = {"no_meetings_before", "cc_on_sender_domain"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    try:
        return json.loads(config.PREFS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(prefs):
    fd, tmp = tempfile.mkstemp(dir=config.BASE_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2)
    os.replace(tmp, config.PREFS_FILE)


def key_for(pref) -> str:
    if pref["type"] == "cc_on_sender_domain":
        return f"cc_on_sender_domain:{pref['domain']}"
    return pref["type"]


def set_pref(pref: dict, source: str, approved_by: str) -> dict:
    if pref.get("type") not in ALLOWED_TYPES:
        raise ValueError(f"preference type {pref.get('type')!r} is not supported")
    prefs = load()
    key = key_for(pref)
    old = prefs.get(key)
    entry = {**pref, "source": source, "approved_by": approved_by, "updated_at": _now(),
             "history": (old or {}).get("history", [])}
    if old and {k: old[k] for k in pref if k in old} != pref:
        entry["history"].append({k: v for k, v in old.items() if k != "history"})
    prefs[key] = entry
    _save(prefs)
    return entry


def clear():
    if config.PREFS_FILE.exists():
        config.PREFS_FILE.unlink()


def earliest_meeting_time():
    p = load().get("no_meetings_before")
    if not p:
        return None, None
    h, m = map(int, p["time"].split(":"))
    return (h, m), p["source"]


def cc_for(sender: str):
    """(cc_address, source_msg_id) if a CC rule applies to this sender's domain."""
    domain = sender.split("@")[-1].lower()
    p = load().get(f"cc_on_sender_domain:{domain}")
    return (p["cc"], p["source"]) if p else (None, None)


# ---- proposing preferences from mail ---------------------------------------------------
P_NO_MEET = re.compile(r"(?:do not|don't|never) take meetings before (\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.I)
P_CC = re.compile(r"make sure I'm CC'd on anything that comes in from (?:our \w+ at )?([A-Z][\w&. ]+?)[.,]", re.I)


def is_active(p) -> bool:
    cur = load().get(key_for(p))
    return bool(cur) and all(cur.get(k) == v for k, v in p.items() if k != "summary")


def propose_from_message(msg, store):
    """Return preference proposals stated in a (non-hostile) message (all, active or not)."""
    text = msg["body"]
    proposals = []
    m = P_NO_MEET.search(text)
    if m and msg["from"].lower() == config.OWNER.lower():
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        proposals.append({"type": "no_meetings_before", "time": f"{h:02d}:{int(m.group(2) or 0):02d}",
                          "summary": f"Never accept meetings before {m.group(1)}:{m.group(2) or '00'}{m.group(3)}; "
                                     f"offer {m.group(1)}:{m.group(2) or '00'}{m.group(3)} or later instead"})
    m = P_CC.search(text)
    if m and msg["from"].lower().endswith("@" + config.ORG_DOMAIN):
        org = m.group(1).strip()
        # Ground the firm name in the mailbox: which sender domain signs as that firm?
        domains = sorted({x["from"].split("@")[1] for x in store.messages if org.lower() in x["body"].lower()
                          and x["from"].split("@")[1] != config.ORG_DOMAIN})
        for d in domains:
            proposals.append({"type": "cc_on_sender_domain", "domain": d, "cc": msg["from"],
                              "summary": f"Always CC {msg['from']} on mail from {org} ({d})"})
    return proposals
