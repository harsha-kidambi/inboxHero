# Roll No: evernorth-aai-1164386
"""
triage.py - the router. Every message goes down exactly one path and leaves with
exactly one disposition and a one-line reason.

  1. screen   security.screen()      hostile / phishing  -> quarantine    (no model)
  2. rule     rules.classify()       automated senders   -> archive       (no model)
  3. policy   deterministic workflow owner-sent mail, closed threads, standing
                                     instructions, law-firm mail, meeting requests
                                     (conflicts + preferences)          (no model for the decision)
  4. model    llm (batched)          the genuinely ambiguous remainder; offline
                                     analyst if no model is reachable
Then drafts (drafting.py) and proposed actions are built; nothing is executed here.
Execution of anything irreversible happens only in gate.py.
"""
import json
import re
from datetime import datetime, time, timedelta

import commitments
import config
import drafting
import llm
import prefs
import retrieval
import rules
import security
import tracelog as trace
from mailstore import MailStore

DISPOSITIONS = {
    "reply": "A response is warranted. A draft is prepared and held at the gate; nothing is sent automatically.",
    "clarify": "Ambiguous. The right move is to ask, not guess. A question is drafted only if it can be grounded.",
    "archive": "Nothing is needed from the owner (noise, FYI, resolved, or already recorded). Reversible label.",
    "defer": "Needs the owner later. Parked against a date (a deadline, or waiting on someone's reply).",
    "delegate": "Someone else should own it. A hand-off note is drafted and held at the gate.",
    "escalate": "Only the owner can decide: money, legal, hiring, public statements, time commitments, conflicts.",
    "quarantine": "Hostile or phishing. Refused, flagged, left in place (never deleted), reported in the summary.",
}
MODEL_CHOICES = ["reply", "clarify", "archive", "defer", "delegate", "escalate"]

OWNER_ASK = re.compile(r"\b(can you|could you|would you|please (?:approve|sign|review|confirm)|any read"
                       r"|next steps|where I stand|let me know)\b", re.I)


# ---------------------------------------------------------------------------------------------
class Run:
    """Everything one triage run produces."""

    def __init__(self, store):
        self.store = store
        self.decisions = {}            # msg_id -> decision
        self.actions = []              # proposed actions (to the gate)
        self.flags = []                # refused / suspicious / could-not-ground
        self.notes = []                # low-severity observations
        self.items = []                # commitments
        self.conflicts = []
        self.offered = []              # slots already offered in a draft this run

    def decide(self, msg, disposition, reason, path, **extra):
        assert disposition in DISPOSITIONS, disposition
        d = {"id": msg["id"], "from": msg["from"], "subject": msg["subject"], "disposition": disposition,
             "reason": reason, "path": path, **extra}
        self.decisions[msg["id"]] = d
        trace.event("decision", msg_id=msg["id"], disposition=disposition, path=path, reason=reason)
        return d

    def propose(self, aid, kind, msg_ids, why_human, risk, draft=None, **extra):
        a = {"aid": aid, "type": kind, "msg_ids": msg_ids, "why_human": why_human, "risk": risk,
             "irreversible": kind in ("send_reply", "forward", "send"), **extra}
        if draft:
            a.update({"to": draft["to"], "cc": draft.get("cc"), "subject": draft["subject"], "body": draft["body"],
                      "cites": draft["cites"], "draft_source": draft.get("source")})
        self.actions.append(a)
        trace.event("proposed", aid=aid, type=kind, msg_ids=msg_ids, risk=risk)
        return a


# ---------------------------------------------------------------------------------------------
def _meeting_items(run, msg):
    return [i for i in run.items if msg["id"] in i["cites"] and i["time"] and i["kind"] == "meeting"]


def _is_booking(msg):
    return bool(re.search(r"\b(your appointment|reply confirm)\b", msg["body"], re.I))


def policy(run, msg):
    """Deterministic workflow rules. Return True if the message was decided here."""
    store = run.store
    body = msg["body"]

    # --- mail the owner sent ---------------------------------------------------------------
    if msg["from"].lower() == config.OWNER.lower():
        proposals = prefs.propose_from_message(msg, store)
        if proposals:
            new = [p for p in proposals if not prefs.is_active(p)]
            run.decide(msg, "archive", "Owner's own standing instruction; " +
                       ("proposed as a preference (active only after approval)." if new
                        else "already saved as an active preference."), "policy", rule="P-OWNER-PREF")
            for p in new:
                run.propose(f"pref-{msg['id']}-{p['type']}", "set_preference", [msg["id"]],
                            "Changes standing policy for every future run.", "medium", preference=p,
                            summary=p["summary"])
            return True
        replies = [m for m in store.later_in_thread(msg) if m["from"].lower() != config.OWNER.lower()]
        if replies:
            run.decide(msg, "archive", f"Sent by you and already answered ({replies[0]['id']}).", "policy",
                       rule="P-OWNER-ANSWERED")
        else:
            waited = (store.now - store.ts(msg)).days
            run.decide(msg, "defer", f"Sent by you to {msg['to']}; no reply after {waited} days - tracked for follow-up.",
                       "policy", rule="P-OWNER-AWAITING", waiting_days=waited)
        return True

    # --- closed threads ------------------------------------------------------------------------
    later = store.later_in_thread(msg)
    closer = next((m for m in later if m["from"] == msg["from"] and re.search(r"\b(fixed|resolved|thanks)\b", m["body"], re.I)
                   and "?" not in m["body"]), None)
    if closer:
        run.decide(msg, "archive", f"Resolved later in the same thread ({closer['id']}: \"{closer['body'][:40]}...\").",
                   "policy", rule="P-THREAD-RESOLVED")
        return True
    if msg["subject"].lower().startswith("re:") and "?" not in body and \
            re.search(r"\b(that fixed it|thanks)\b", body, re.I):
        run.decide(msg, "archive", "Closing message of a resolved thread; nothing asked.", "policy",
                   rule="P-THREAD-CLOSER")
        return True

    # --- standing instructions from colleagues --------------------------------------------------------
    proposals = prefs.propose_from_message(msg, store)
    if proposals:
        new = [p for p in proposals if not prefs.is_active(p)]
        run.decide(msg, "archive", "Standing instruction from a colleague; " +
                   ("proposed as a preference (active only after approval)." if new
                    else "already saved as an active preference."), "policy", rule="P-STANDING-INSTRUCTION")
        for p in new:
            run.propose(f"pref-{msg['id']}-{p['domain'] if 'domain' in p else p['type']}", "set_preference",
                        [msg["id"]], "Changes standing policy for every future run.", "medium",
                        preference=p, summary=p["summary"])
        return True

    # --- law-firm mail ----------------------------------------------------------------------------------
    domain = store.domain(msg["from"])
    law_firm = any(store.domain(m["from"]) == domain and re.search(r"\bLLP\b", m["body"]) for m in store.messages)
    if law_firm:
        deadline = next((i for i in run.items if msg["id"] in i["cites"] and i["kind"] == "deadline"), None)
        when = f" by {deadline['date']:%a %b %d}" if deadline else ""
        cc, cc_src = prefs.cc_for(msg["from"])
        cc_note = f" {cc} must be looped in (standing rule {cc_src})." if cc else ""
        if re.search(r"not urgent", body, re.I):
            run.decide(msg, "defer", f"Legal action needed{when}, marked not urgent.{cc_note}", "policy",
                       rule="P-LEGAL", deadline=str(deadline["date"]) if deadline else None)
        else:
            run.decide(msg, "escalate", f"Legal document needs your review/signature{when}.{cc_note}", "policy",
                       rule="P-LEGAL", deadline=str(deadline["date"]) if deadline else None)
        return True

    # --- meeting requests: conflicts and standing rules ------------------------------------------------
    timed = _meeting_items(run, msg)
    if timed and msg["from"].lower() != config.OWNER.lower():
        item = timed[0]
        requested = datetime.combine(item["date"], item["time"])
        mine = [c for c in run.conflicts if item["cid"] in c["items"]]
        double = next((c for c in mine if c["type"] == "double-booked"), None)
        rule = next((c for c in mine if c["type"] == "breaks-standing-rule"), None)
        is_request = "?" in body or _is_booking(msg)
        if double:
            other_cid = next(x for x in double["items"] if x != item["cid"])
            other = next(i for i in run.items if i["cid"] == other_cid)
            other_msg = store.get(other["cites"][0])
            # First claim on the slot: an existing booking beats a request; between requests, the earlier one.
            i_have_claim = _is_booking(msg) or (not _is_booking(other_msg) and msg["timestamp"] < other_msg["timestamp"])
            if i_have_claim:
                run.decide(msg, "escalate", f"Double-booked {double['when']} with {other_msg['id']} "
                                            f"({other_msg['subject'][:40]}). This one has first claim; confirming it "
                                            f"is your call.", "policy", rule="P-MEETING-CONFLICT", conflict=double)
            else:
                slots = commitments.propose_slots(requested, run.items, store.now, offered=run.offered)
                draft = drafting.meeting_reply(msg, store, requested, slots, "conflict", [other_msg["id"]])
                run.decide(msg, "reply", f"Requested slot {double['when']} collides with {other_msg['id']}; "
                                         f"offering 3 alternatives.", "policy", rule="P-MEETING-CONFLICT", conflict=double)
                _propose_send(run, msg, draft, "Declines a time and offers alternatives in your name.")
            return True
        if rule:
            if is_request and msg["from"].split("@")[1] != config.ORG_DOMAIN and "?" in body:
                slots = commitments.propose_slots(requested, run.items, store.now, offered=run.offered)
                src = rule["cites"][-1]
                draft = drafting.meeting_reply(msg, store, requested, slots, "rule", [src])
                run.decide(msg, "reply", f"Requested {rule['when']} breaks your standing rule ({src}); "
                                         f"offering 3 slots at 11:00 or later.", "policy", rule="P-MEETING-RULE")
                _propose_send(run, msg, draft, "Declines a time and offers alternatives in your name.")
            else:
                run.decide(msg, "escalate", f"Already-scheduled meeting {rule['when']} breaks your standing rule "
                                            f"({rule['cites'][-1]}); not declined automatically.", "policy",
                           rule="P-MEETING-RULE")
            return True
        if is_request:
            draft = drafting.meeting_reply(msg, store, requested, [], None, [])
            run.decide(msg, "reply", f"Meeting request for {requested:%a %b %d %H:%M}; no conflict found - "
                                     f"acceptance drafted.", "policy", rule="P-MEETING-OK")
            _propose_send(run, msg, draft, "Commits your time in your name.")
        else:
            run.decide(msg, "defer", f"Scheduled for {requested:%a %b %d %H:%M}; on the calendar.", "policy",
                       rule="P-MEETING-CALENDAR")
        return True
    return False


def _propose_send(run, msg, draft, why):
    if not draft or not draft.get("ok"):
        return
    external = not draft["to"].endswith("@" + config.ORG_DOMAIN)
    risk = "high" if draft.get("sensitive") or external else "low"
    reasons = [why]
    if external:
        reasons.append("External recipient.")
    if draft.get("sensitive"):
        reasons.append("Contains a live credential - consider sharing via the secrets manager instead.")
    if draft.get("grounding_problems"):
        run.flags.append({"id": msg["id"], "kind": "ungrounded_draft", "severity": "medium",
                          "attempted": "; ".join(draft["grounding_problems"]),
                          "did_instead": "Draft discarded; not proposed."})
        return
    trace.event("draft", msg_id=msg["id"], cites=draft["cites"], source=draft["source"], to=draft["to"])
    run.propose(f"reply-{msg['id']}", "send_reply", [msg["id"]], " ".join(reasons), risk, draft)


# ---------------------------------------------------------------------------------------------
def offline_triage(msg, store):
    """Deterministic stand-in for the model on the ambiguous remainder."""
    b = msg["body"]
    if re.search(r"\b(that thing|the thing|what we (?:talked|discussed) about)\b", b, re.I):
        return "clarify", "Refers to 'the thing' with no identifying detail; ask rather than guess.", {}
    if re.search(r"\b(resend|send again|you gave|you sent)\b", b, re.I):
        return "reply", "Asks for information given earlier in the thread.", {}
    if re.search(r"\b(coverage|press|journalist|piece on)\b", b, re.I) and store.domain(msg["from"]) != config.ORG_DOMAIN:
        owner = retrieval.keyword_search(store, "press briefed", purpose=f"who owns press for {msg['id']}")
        to = owner[0]["from"] if owner else None
        return "delegate", "Press request; a public statement should come from whoever briefed press.", {"delegate_to": to,
                                                                                                          "delegate_cite": owner[0]["id"] if owner else None}
    if re.search(r"\b(booking|hold|contract)\b", b, re.I):
        return "escalate", "Asks you to confirm a booking/contract for a date not recorded anywhere in the inbox.", {}
    if re.search(r"\b(grab coffee|catch up|get together)\b", b, re.I):
        return "clarify", "Friendly invite with no day or time; ask which day.", {}
    if re.search(r"\b(can you have|reminder to|submit your)\b", b, re.I):
        return "defer", "A task for you with a deadline; parked on the calendar.", {}
    if OWNER_ASK.search(b):
        return "escalate", "Asks you directly for a decision or approval.", {}
    return "archive", "Status update / FYI; nothing is asked of you.", {}


def model_triage(msgs, store):
    """Batch the ambiguous messages to the model. Returns {id: (disposition, reason, extra)}."""
    results = {}
    for start in range(0, len(msgs), config.TRIAGE_BATCH_SIZE):
        batch = msgs[start:start + config.TRIAGE_BATCH_SIZE]
        blocks = []
        for m in batch:
            earlier = store.earlier_in_thread(m)
            ctx = "; ".join(f"{e['id']} from {e['from']}: \"{e['body'][:70]}\"" for e in earlier[-4:]) or "none"
            blocks.append(llm.fence(m) + f"\n(Earlier in this thread: {ctx})")
        prompt = [
            {"role": "system", "content": (
                f"You triage email for {config.OWNER_NAME} ({config.OWNER}). {llm.UNTRUSTED_NOTE}\n"
                "Choose exactly one disposition per message:\n" +
                "\n".join(f"- {k}: {DISPOSITIONS[k]}" for k in MODEL_CHOICES) +
                "\nReturn ONLY a JSON array: [{\"id\": \"m000\", \"disposition\": \"...\", \"reason\": \"one line\", "
                "\"delegate_to\": \"email or null\"}] covering every message id given.")},
            {"role": "user", "content": "\n\n".join(blocks)},
        ]
        text = llm.chat(prompt, purpose="triage", msg_ids=[m["id"] for m in batch])
        parsed = llm.extract_json(text)
        if isinstance(parsed, list):
            for row in parsed:
                if isinstance(row, dict) and row.get("id") in {m["id"] for m in batch} \
                        and row.get("disposition") in MODEL_CHOICES:
                    results[row["id"]] = (row["disposition"], str(row.get("reason", ""))[:200],
                                          {"delegate_to": row.get("delegate_to")})
    return results


def model_path(run, msgs):
    store = run.store
    from_model = model_triage(msgs, store) if msgs else {}
    for msg in msgs:
        if msg["id"] in from_model:
            disp, reason, extra = from_model[msg["id"]]
            path = "model"
            # Guard rails the model cannot talk its way past:
            if disp == "archive" and OWNER_ASK.search(msg["body"]):
                trace.event("policy_override", msg_id=msg["id"], model_said="archive", now="escalate")
                disp, reason = "escalate", "Model said archive, but the message asks you directly - escalated. " + reason
            if disp == "delegate":
                to = extra.get("delegate_to")
                if not to or not any(m["from"] == to for m in store.messages) or not to.endswith("@" + config.ORG_DOMAIN):
                    _, _, off = offline_triage(msg, store)
                    extra = off if off.get("delegate_to") else {}
                    if not extra:
                        disp, reason = "escalate", "Model suggested delegating to an unknown person - escalated. " + reason
        else:
            if config.model_enabled():
                trace.event("llm_fallback", msg_ids=[msg["id"]], reason="no valid model verdict; offline analyst used")
            disp, reason, extra = offline_triage(msg, store)
            path = "offline-analyst"
        _act_on_model_decision(run, msg, disp, reason, path, extra)


def _act_on_model_decision(run, msg, disp, reason, path, extra):
    store = run.store
    if disp == "reply" and re.search(r"\b(resend|send again|you gave|you sent|earlier)\b", msg["body"], re.I):
        draft = drafting.grounded_answer(msg, store)
        if not draft["ok"]:
            run.decide(msg, "escalate", draft["reason"], path)
            run.flags.append({"id": msg["id"], "kind": "not_in_inbox", "severity": "medium",
                              "attempted": "answer a request for information", "did_instead": draft["reason"]})
            return
        run.decide(msg, "reply", reason + f" Answer drafted from {', '.join(draft['cites'])}.", path)
        _propose_send(run, msg, draft, "Sends information in your name.")
        return
    if disp == "clarify":
        if re.search(r"\b(grab coffee|catch up|get together|next week)\b", msg["body"], re.I):
            start = datetime.combine(store.ts(msg).date() + timedelta(days=7 - store.ts(msg).weekday()), time(11))
            slots = commitments.propose_slots(start - timedelta(minutes=1), run.items, store.now, offered=run.offered)
            draft = drafting.meeting_reply(msg, store, start, slots, "invite", [])
            run.decide(msg, "clarify", reason, path)
            _propose_send(run, msg, draft, "Offers your time to an outside contact.")
        else:
            earlier = retrieval.thread_context(store, msg)
            run.decide(msg, "clarify", reason + " Nothing in the inbox identifies what is meant, so no draft was "
                                                "written - you need to answer.", path)
            run.flags.append({"id": msg["id"], "kind": "not_in_inbox", "severity": "low",
                              "attempted": "work out what is being referred to",
                              "did_instead": f"Searched thread {msg['thread_id']} ({len(earlier)} earlier messages) - "
                                             "no antecedent. No draft written; asked you instead."})
        return
    if disp == "delegate" and extra.get("delegate_to"):
        to, src = extra["delegate_to"], extra.get("delegate_cite")
        cites = [msg["id"]] + ([src] if src else [])
        name = to.split("@")[0].title()
        body = (f"Hi {name},\n\n{msg['from']} is asking about the launch for their coverage (\"{msg['subject']}\"). "
                f"You briefed press ({src}), so could you take this one and reply directly?\n\nThanks,\n{config.OWNER_NAME}")
        draft = drafting.note(msg, store, to, f"Can you take this? {msg['subject']}", body, cites)
        run.decide(msg, "delegate", reason + f" Hand-off to {to} drafted.", path, delegate_to=to)
        _propose_send(run, msg, draft, "Hands a public statement to a colleague in your name.")
        return
    if disp == "escalate" and re.search(r"\b(booking|hold|contract)\b", msg["body"], re.I):
        run.flags.append({"id": msg["id"], "kind": "not_in_inbox", "severity": "medium",
                          "attempted": "confirm a booking 'for the date you locked in'",
                          "did_instead": "No message records a venue or a locked date, so nothing was confirmed or "
                                         "drafted; escalated to you."})
    run.decide(msg, disp, reason, path, **({"delegate_to": extra["delegate_to"]} if extra.get("delegate_to") else {}))


# ---------------------------------------------------------------------------------------------
def legal_forward(run):
    """Standing rule 'CC Priya on all Hartwell & Cho mail' -> ONE bundled forward, one approval."""
    store = run.store
    groups = {}
    for m in store.messages:
        cc, src = prefs.cc_for(m["from"])
        if cc and run.decisions.get(m["id"], {}).get("disposition") != "quarantine":
            groups.setdefault((cc, src), []).append(m)
    for (cc, src), msgs in groups.items():
        cites = [src] + [m["id"] for m in msgs]
        lines = "\n".join(f"- {m['id']} ({m['timestamp'][:10]}) {m['subject']}" for m in msgs)
        body = (f"Hi {cc.split('@')[0].title()},\n\nPer your standing request ({src}), forwarding everything from "
                f"{msgs[0]['from'].split('@')[1]}:\n\n{lines}\n\nFull messages attached below.\n\n{config.OWNER_NAME}")
        draft = drafting.note(msgs[0], store, cc, f"Fwd: {len(msgs)} messages from {msgs[0]['from'].split('@')[1]}",
                              body, cites, allowed=[m["timestamp"][:10] for m in msgs])
        trace.event("draft", msg_id=msgs[0]["id"], cites=cites, source="template", to=cc)
        run.propose(f"fwd-{src}-{msgs[0]['from'].split('@')[1]}", "forward", [m["id"] for m in msgs],
                    f"Applies standing rule {src}: {len(msgs)} law-firm messages in ONE forward (one approval, not three).",
                    "low", draft, forwarded=[m["id"] for m in msgs])


def followups(run, min_days=3):
    store = run.store
    for d in run.decisions.values():
        if d.get("rule") == "P-OWNER-AWAITING" and d.get("waiting_days", 0) >= min_days:
            msg = store.get(d["id"])
            sent = store.ts(msg)
            name = msg["to"].split("@")[0].title()
            topic = re.sub(r"^re:\s*", "", msg["subject"], flags=re.I)
            body = (f"Hi {name},\n\nFollowing up on my note from {sent:%b} {sent.day} about {topic} - "
                    f"it's still open on my side. Could you take a look this week?\n\nThanks,\n{config.OWNER_NAME}")
            draft = drafting.note(msg, store, msg["to"], f"Re: {topic}", body, [msg["id"]],
                                  allowed=[f"{sent:%b} {sent.day}"])
            trace.event("draft", msg_id=msg["id"], cites=[msg["id"]], source="template", to=msg["to"])
            run.propose(f"chase-{msg['id']}", "send_reply", [msg["id"]],
                        f"Chases a colleague in your name after {d['waiting_days']} days of silence.", "low", draft,
                        days_waiting=d["waiting_days"])


# ---------------------------------------------------------------------------------------------
def run_triage(store=None, cap="R1"):
    store = store or MailStore()
    run = Run(store)
    llm.touched.clear()

    # 1. screen everything first
    hostile, unverified = set(), set()
    for m in store.messages:
        f = security.screen(m)
        if not f:
            continue
        if f["severity"] == "high":
            hostile.add(m["id"])
            run.flags.append(f)
            run.decide(m, "quarantine", f"{f['kind'].replace('_', ' ')}: attempted to {f['attempted']}.", "screen")
            trace.event("refusal", msg_id=m["id"], kind=f["kind"], attempted=f["attempted"], did_instead=f["did_instead"])
        else:
            unverified.add(m["id"])
            run.notes.append(f)

    # 2. rules
    rule_ids = set()
    for m in store.messages:
        if m["id"] in hostile:
            continue
        r = rules.classify(m)
        if r:
            rule_ids.add(m["id"])
            run.decide(m, "archive", f"Automated sender ({r[1]}); archived by rule {r[0]}.", "rule", rule=r[0])

    # 3. calendar first (policy needs it), then policy
    run.items = commitments.build(store, hostile, rule_ids, unverified)
    run.conflicts = commitments.conflicts(run.items)
    remainder = []
    for m in store.messages:
        if m["id"] in run.decisions:
            continue
        if not policy(run, m):
            remainder.append(m)

    # 4. model (or offline analyst) for the ambiguous remainder
    model_path(run, remainder)

    # 5. standing-rule forwards and follow-ups
    legal_forward(run)
    followups(run)

    commitments.log(run.items, run.conflicts)
    undecided = [m["id"] for m in store.messages if m["id"] not in run.decisions]
    run.summary = {
        "messages": len(store.messages), "undecided": len(undecided),
        "by_disposition": {k: sum(1 for d in run.decisions.values() if d["disposition"] == k) for k in DISPOSITIONS},
        "by_path": {p: sum(1 for d in run.decisions.values() if d["path"] == p)
                    for p in ("screen", "rule", "policy", "model", "offline-analyst")},
        "reached_model": sorted(llm.touched),
        "never_reached_model": len(store.messages) - len(llm.touched),
        "model": config.describe_model(),
        "hostile": [{"id": f["id"], "kind": f["kind"], "attempted": f["attempted"]} for f in run.flags
                    if f["kind"] in ("prompt_injection", "phishing")],
        "pending_actions": len(run.actions), "inbox_sha256": store.sha256,
    }
    save(run)
    return run


def save(run):
    config.RUN_DIR.mkdir(exist_ok=True)
    (config.RUN_DIR / "decisions.json").write_text(json.dumps(list(run.decisions.values()), indent=1, default=str))
    (config.RUN_DIR / "pending.json").write_text(json.dumps(run.actions, indent=1, default=str))
    (config.RUN_DIR / "flags.json").write_text(json.dumps({"flags": run.flags, "notes": run.notes}, indent=1, default=str))
    (config.RUN_DIR / "commitments.json").write_text(json.dumps(
        {"items": commitments.serialise(run.items), "conflicts": run.conflicts}, indent=1, default=str))
    (config.RUN_DIR / "summary.json").write_text(json.dumps(run.summary, indent=1, default=str))
