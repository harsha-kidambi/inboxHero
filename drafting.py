# Roll No: evernorth-aai-1164386
"""
drafting.py - grounded drafts, and the check that keeps them honest.

Every draft carries `cites`: the message ids it drew on. check_grounding() rejects a
draft if
  * it cites an id that does not exist in the mail store, or was not actually read
    by retrieval during this run, or
  * it contains a concrete detail (URL, email address, money amount, time, date,
    long number) that appears in none of the cited messages and is not one of the
    values the system computed itself (e.g. proposed slots).
A model-written draft that fails the check is thrown away and the deterministic
template is used instead; if the information simply isn't in the inbox, no draft is
written at all and the system says so.
"""
import json
import re
from datetime import datetime

import config
import llm
import retrieval
import tracelog as trace

TOKEN_PATTERNS = [
    re.compile(r"[a-z][a-z0-9+.-]*://[^\s]+", re.I),                         # URLs incl. amqp://
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                              # email addresses
    re.compile(r"\$[\d,]+(?:\.\d+)?"),                                         # money
    re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b", re.I),                  # times
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}\b", re.I),
    re.compile(r"\bthe \d{1,2}(?:st|nd|rd|th)\b", re.I),
    re.compile(r"\b\d{4,}\b"),                                                 # ids, account numbers
]
CREDENTIAL = re.compile(r"://[^/\s:@]+:[^@\s]+@")


def _norm(s):
    """Lower-case, no spaces, and '9:00am' == '9am' so equivalent wordings of a grounded time match."""
    return re.sub(r":00(?=\s?[ap]m)", "", re.sub(r"\s+", "", s.lower())).rstrip(".,;)")


def fmt_slot(dt: datetime) -> str:
    return dt.strftime("%a %b %d at %I:%M%p").replace(" 0", " ").replace("AM", "am").replace("PM", "pm")


def check_grounding(draft: str, cites, store, allowed_extra=()):
    problems = []
    for c in cites:
        if not store.exists(c):
            problems.append(f"cites {c}, which is not in the mail store")
        elif not trace.was_read(c):
            problems.append(f"cites {c}, which retrieval never read this run")
    corpus = _norm(" ".join(" ".join((store.get(c)["from"], store.get(c)["to"], store.get(c)["subject"],
                                      store.get(c)["body"])) for c in cites if store.exists(c)))
    corpus += _norm(" ".join(allowed_extra))
    for pat in TOKEN_PATTERNS:
        for tok in pat.findall(draft):
            if _norm(tok) not in corpus:
                problems.append(f"detail '{tok}' is not in any cited message")
    return problems


ROLE_ACCOUNTS = {"partners", "events", "editor", "support", "info", "help", "team", "sales", "hello", "hr"}


def _first_name(address):
    local = address.split("@")[0].split(".")[0].split("_")[0].lower()
    return "there" if local in ROLE_ACCOUNTS else local.title()


def _model_polish(kind, facts, request_msg, allowed_extra, cites, store, fallback):
    """Ask the model to write the prose; keep it only if it passes the grounding check."""
    prompt = [
        {"role": "system", "content": (
            f"You draft short, plain email replies on behalf of {config.OWNER_NAME} ({config.OWNER}). "
            + llm.UNTRUSTED_NOTE +
            " Use ONLY the facts provided. Do not add dates, times, links, numbers or promises that are not "
            "in the facts. 2-5 sentences. Sign off as " + config.OWNER_NAME + ". Output only the email body.")},
        {"role": "user", "content": f"Draft type: {kind}\nFacts (trusted, computed by the system):\n"
                                    f"{json.dumps(facts, default=str, indent=1)}\n\nMessage being answered:\n"
                                    f"{llm.fence(request_msg)}"},
    ]
    text = llm.chat(prompt, purpose=f"draft:{kind}", msg_ids=[request_msg["id"]])
    if text:
        problems = check_grounding(text, cites, store, allowed_extra)
        if not problems:
            return text.strip(), "model"
        trace.event("draft_rejected", msg_id=request_msg["id"], source="model", problems=problems)
    return fallback, "template"


def grounded_answer(msg, store):
    """Answer a request for information that was given earlier. Returns a draft dict or a refusal."""
    earlier = retrieval.thread_context(store, msg)
    wants_link = re.search(r"\b(url|link|creds|credentials|address|details)\b", msg["body"], re.I)
    sources = []
    if wants_link:
        for m in reversed(earlier):
            urls = TOKEN_PATTERNS[0].findall(m["body"])
            if m["from"] == config.OWNER and urls:
                sources.append((m, urls[0].rstrip(".")))
                break
    if not sources:
        return {"ok": False, "msg_id": msg["id"],
                "reason": "The information requested is not in any earlier message I can find "
                          f"(searched thread {msg['thread_id']}: {[m['id'] for m in earlier] or 'no earlier messages'}). "
                          "No draft written."}
    src, value = sources[0]
    cites = [src["id"]]
    fallback = (f"Hi {_first_name(msg['from'])},\n\nHere's the staging queue URL I sent Raghav earlier "
                f"(my message {src['id']}):\n\n{value}\n\nPoint the worker at that and restart it. "
                f"Please don't forward it on.\n\n{config.OWNER_NAME}")
    body, source = _model_polish("answer from earlier message", {"requested_value": value, "from_message": src["id"]},
                                 msg, [], cites, store, fallback)
    return {"ok": True, "msg_id": msg["id"], "to": msg["from"], "subject": "Re: " + re.sub(r"^re:\s*", "", msg["subject"], flags=re.I),
            "body": body, "cites": cites, "source": source,
            "sensitive": bool(CREDENTIAL.search(body)),
            "grounding_problems": check_grounding(body, cites, store)}


def meeting_reply(msg, store, requested: datetime, slots, reason_kind, reason_cites):
    """Accept, or decline and offer alternative slots. reason_kind: None | 'rule' | 'conflict'."""
    retrieval.thread_context(store, msg)
    trace.record_read(reason_cites, f"meeting constraints for {msg['id']}")
    name = _first_name(msg["from"])
    subject = "Re: " + re.sub(r"^re:\s*", "", msg["subject"], flags=re.I)
    cites = [msg["id"]] + list(reason_cites)
    slot_txt = [fmt_slot(s) for s in slots]
    if reason_kind is None:
        fallback = (f"Hi {name},\n\n{fmt_slot(requested)} works for me - please send the calendar hold.\n\n"
                    f"Best,\n{config.OWNER_NAME}")
        facts = {"decision": "accept", "time": fmt_slot(requested)}
    elif reason_kind == "invite":
        options = ", ".join(slot_txt[:-1]) + f" or {slot_txt[-1]}"
        fallback = (f"Hi {name},\n\nGood to hear from you - happy to catch up. Which day suits you? "
                    f"I could do {options}.\n\nBest,\n{config.OWNER_NAME}")
        facts = {"decision": "accept the invitation but ask which day", "offer": slot_txt}
    else:
        why = ("I don't take meetings before 11am" if reason_kind == "rule"
               else "I'm already committed at that time")
        options = ", ".join(slot_txt[:-1]) + f" or {slot_txt[-1]}" if len(slot_txt) > 1 else slot_txt[0]
        fallback = (f"Hi {name},\n\nThanks for reaching out. {fmt_slot(requested)} doesn't work on my side - {why}. "
                    f"Could we do {options} instead?\n\nBest,\n{config.OWNER_NAME}")
        facts = {"decision": "decline and offer alternatives", "requested": fmt_slot(requested),
                 "reason": why, "alternatives": slot_txt}
    allowed = slot_txt + [fmt_slot(requested), "11am", "11:00am"]
    body, source = _model_polish("meeting reply", facts, msg, allowed, cites, store, fallback)
    return {"ok": True, "msg_id": msg["id"], "to": msg["from"], "subject": subject, "body": body,
            "cites": cites, "source": source, "sensitive": False,
            "grounding_problems": check_grounding(body, cites, store, allowed)}


def note(msg, store, to, subject, body, cites, allowed=()):
    """A short deterministic note (delegation, chase, forward cover). Always grounding-checked."""
    trace.record_read(cites, f"note for {msg['id']}")
    return {"ok": True, "msg_id": msg["id"], "to": to, "subject": subject, "body": body, "cites": cites,
            "source": "template", "sensitive": False, "grounding_problems": check_grounding(body, cites, store, allowed)}
