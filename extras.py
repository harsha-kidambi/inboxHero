# Roll No: evernorth-aai-1164386
"""
extras.py - Part 8 capabilities (X1-X5). Each is runnable on its own via demo.py.

X1 (A) mail from a sender          one lookup, one output
X2 (B) thread -> the open question multi-message reasoning, cited
X3 (B) follow-up tracking          owner-sent mail nobody answered + a drafted chase
X4 (C) meeting negotiator          conflicts + standing rule -> 3 slots -> draft held at the gate
X5 (B) "why did you do that?"      rebuilds the decision chain for one message from the trace
"""
import json
import re

import config
import llm
import retrieval
import tracelog as trace
import triage
from mailstore import MailStore


def x1_sender(sender: str):
    store = MailStore()
    decisions = {}
    if (config.RUN_DIR / "decisions.json").exists():
        decisions = {d["id"]: d for d in json.loads((config.RUN_DIR / "decisions.json").read_text())}
    hits = [m for m in store.messages if sender.lower() in m["from"].lower()]
    trace.record_read([m["id"] for m in hits], f"sender lookup '{sender}'")
    print(f"{len(hits)} message(s) from '{sender}':")
    for m in hits:
        disp = decisions.get(m["id"], {}).get("disposition", "-")
        print(f"  {m['id']}  {m['timestamp'][:16]}  {'UNREAD' if m['unread'] else 'read  '}  "
              f"[{disp}]  {m['subject']}")
    return hits


def x2_open_question(thread_id: str):
    store = MailStore()
    msgs = store.thread(thread_id)
    if not msgs:
        print(f"No thread {thread_id}")
        return None
    trace.record_read([m["id"] for m in msgs], f"thread summary {thread_id}")
    result = None
    prompt = [
        {"role": "system", "content": (
            f"{llm.UNTRUSTED_NOTE} Summarise this email thread for {config.OWNER_NAME} in one line per message, then "
            f"identify the open question(s) that need {config.OWNER_NAME} personally. Return ONLY JSON: "
            "{\"status\": [{\"id\": \"m000\", \"line\": \"...\"}], \"open_questions\": [{\"id\": \"m000\", "
            "\"question\": \"exact sentence from that message\", \"deadline\": \"text or null\"}]}")},
        {"role": "user", "content": "\n\n".join(llm.fence(m) for m in msgs)},
    ]
    parsed = llm.extract_json(llm.chat(prompt, purpose="thread-summary", msg_ids=[m["id"] for m in msgs]))
    if isinstance(parsed, dict) and parsed.get("open_questions"):
        ids = {m["id"] for m in msgs}
        good = [q for q in parsed["open_questions"] if q.get("id") in ids
                and q.get("question", "")[:40].lower() in store.get(q["id"])["body"].lower()]
        if good:
            result = {"source": "model", "open_questions": good, "status": parsed.get("status", [])}
        else:
            trace.event("draft_rejected", msg_id=thread_id, source="model",
                        problems=["open question not found verbatim in the cited message"])
    if not result:                                    # offline analyst / model answer failed the check
        questions, status = [], []
        for m in msgs:
            sentences = re.split(r"(?<=[.?!])\s+", m["body"])
            ask = [s for s in sentences if re.search(rf"\b{config.OWNER_NAME}\b", s) and
                   (s.strip().endswith("?") or triage.OWNER_ASK.search(s))]
            if ask:
                dl = re.search(r"\bby (the \d{1,2}(?:st|nd|rd|th)|\w+day)\b", ask[0])
                questions.append({"id": m["id"], "question": ask[0].strip(), "deadline": dl.group(1) if dl else None})
            status.append({"id": m["id"], "line": f"{m['from'].split('@')[0]}: {sentences[0][:80]}"})
        result = {"source": "offline analyst", "open_questions": questions, "status": status}
    print(f"Thread {thread_id}: {len(msgs)} messages ({result['source']})")
    for s in result["status"]:
        print(f"  {s['id']}  {s['line']}")
    print("\nOPEN QUESTION(S) FOR YOU:")
    for q in result["open_questions"]:
        print(f"  [{q['id']}] {q['question']}  (deadline: {q.get('deadline')})")
    trace.event("thread_summary", thread=thread_id, open_questions=result["open_questions"])
    return result


def x3_followups():
    run = triage.run_triage()
    rows = []
    for a in run.actions:
        if a["aid"].startswith("chase-"):
            rows.append({"message_id": a["msg_ids"][0], "to": a["to"], "days_waiting": a["days_waiting"],
                         "draft": a["body"], "gate": "held for approval"})
    answered = [d for d in run.decisions.values() if d.get("rule") == "P-OWNER-ANSWERED"]
    print(json.dumps(rows, indent=2))
    for d in answered:
        print(f"(not listed: {d['id']} - {d['reason']})")
    return rows


def x4_negotiate():
    run = triage.run_triage()
    meeting = [d for d in run.decisions.values() if str(d.get("rule", "")).startswith("P-MEETING")]
    for d in sorted(meeting, key=lambda x: x["id"]):
        print(f"\n{d['id']}  {d['subject']}  ->  {d['disposition'].upper()}")
        print(f"   reason: {d['reason']}")
        act = next((a for a in run.actions if d["id"] in a["msg_ids"] and a["type"] == "send_reply"), None)
        if act:
            print(f"   draft (held for approval, cites {act['cites']}):")
            print("      " + act["body"].replace("\n", "\n      "))
    if not any(p["type"] == "no_meetings_before" for p in __import__("prefs").load().values()):
        print("\n(note: no 'no meetings before' rule is saved yet - run `python demo.py --cap R4` to see m043 change)")
    return meeting


def x5_explain(msg_id: str):
    path = config.RUN_DIR / "decisions.json"
    if not path.exists():
        triage.run_triage()
    decisions = {d["id"]: d for d in json.loads(path.read_text())}
    summary = json.loads((config.RUN_DIR / "summary.json").read_text())
    d = decisions.get(msg_id)
    if not d:
        print(f"No decision recorded for {msg_id}")
        return None
    store = MailStore()
    m = store.get(msg_id)
    print(f"{msg_id}  from {m['from']}  \"{m['subject']}\"")
    print(f"  decision : {d['disposition']}  (path: {d['path']}{', rule ' + d['rule'] if d.get('rule') else ''})")
    print(f"  because  : {d['reason']}")
    print(f"  model saw it: {'yes' if msg_id in summary['reached_model'] else 'no'}")
    pending = json.loads((config.RUN_DIR / "pending.json").read_text())
    for a in pending:
        if msg_id in a["msg_ids"]:
            print(f"  proposed : {a['aid']} ({a['type']}) - needs a human because: {a['why_human']}")
    flags = json.loads((config.RUN_DIR / "flags.json").read_text())
    for f in flags["flags"] + flags["notes"]:
        if f["id"] == msg_id:
            print(f"  flagged  : {f['kind']} - attempted: {f['attempted']}")
            print(f"             did instead: {f['did_instead']}")
    events = [e for e in trace.events_for(msg_id) if e["event"] in ("decision", "refusal", "gate", "draft",
                                                                   "draft_rejected", "policy_override", "send")]
    last_run = events[-1]["run"] if events else None
    print(f"  trace events (latest run {last_run}):")
    for e in [e for e in events if e["run"] == last_run]:
        extra = {k: v for k, v in e.items() if k not in ("ts", "run", "cap", "event", "msg_id")}
        print(f"    - {e['event']}: {json.dumps(extra)[:150]}")
    return d
