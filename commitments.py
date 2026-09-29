# Roll No: evernorth-aai-1164386
"""
commitments.py - dates, deadlines and obligations -> one calendar, with citations.

1. extract   every dated mention (dates.py) from every non-hostile message
2. derive    relative references across messages: "two days before the board review"
             (m040) + "board review ... the 18th" (m038) -> deck due the 16th, citing both
3. merge     the same event mentioned twice (launch on the 20th in m026 and m036;
             board meeting in m038 and m048) -> one entry citing all sources
4. check     timed items at the same time -> CONFLICT; meetings before the owner's
             earliest-meeting preference -> RULE CONFLICT
Every entry cites message ids; validate() re-checks that each cited message exists and
really contains the quoted date text.
"""
import re
from datetime import date, datetime, time, timedelta

import config
import dates
import prefs
import retrieval
import tracelog as trace

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "a": 1}
P_RELATIVE = re.compile(r"\b(\w+) (day|week)s? before the ([a-z][a-z ]+?)[.?!,]", re.I)
GENERIC = retrieval.STOP | {"sam", "please", "hi", "thanks", "reminder", "confirming", "set", "will", "have",
                            "date", "week", "day", "next", "ahead", "draft", "flag", "any", "corrections",
                            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
PREFIXES = re.compile(r"^(hi sam\s*--\s*|sam,\s*|confirming\s+|this is a reminder of\s+|reminder\s+|heads up,\s*)", re.I)


def _kind(sentence, has_time, rule_archived):
    s = sentence.lower()
    if rule_archived:
        return "fyi"
    if re.search(r"\b(by|due|deadline|expires|month-end)\b", s):
        return "deadline"
    if has_time or re.search(r"\b(meeting|review|call|demo|appointment|1:1)\b", s):
        return "meeting"
    return "event"


def _status(sentence):
    s = sentence.strip().lower()
    if "?" in s or re.match(r"^(can|could|does|would|want)\b", s):
        return "proposed"
    return "confirmed"


def _who(sentence, msg):
    """Whose obligation is it? The sender's if they speak in first person (this or the previous sentence)."""
    s = (dates.previous_sentence(msg["body"], sentence) + " " + sentence).lower()
    if re.search(r"\b(i'll|i will|i'm|i am|i have|i need)\b", s) and msg["from"] != config.OWNER:
        return msg["from"].split("@")[0].split(".")[0].title()
    return config.OWNER_NAME


def _title(sentence, subject):
    t = PREFIXES.sub("", sentence.strip()).strip()
    t = t[:1].upper() + t[1:]
    if len(t) < 40:                                  # "Target is the 20th." needs its subject
        clean_subject = re.sub(r"^(re|fwd):\s*", "", subject, flags=re.I)
        t = f"{clean_subject}: {t}"
    return t[:110]


def build(store, hostile_ids, rule_archived_ids, unverified_ids=()):
    items = []
    for msg in store.messages:
        if msg["id"] in hostile_ids:
            continue
        for d in dates.extract(msg):
            items.append({
                "date": d["date"], "time": d["time"], "quote": d["text"], "sentence": d["sentence"],
                "kind": _kind(d["sentence"], d["time"] is not None, msg["id"] in rule_archived_ids),
                "status": _status(d["sentence"]), "who": _who(d["sentence"], msg),
                "title": _title(d["sentence"], msg["subject"]), "cites": [msg["id"]], "thread": msg["thread_id"],
                "from": msg["from"], "unverified": msg["id"] in unverified_ids,
            })

    # ---- derive relative deadlines across messages ------------------------------------------
    for msg in store.messages:
        if msg["id"] in hostile_ids:
            continue
        for m in P_RELATIVE.finditer(msg["body"]):
            n = NUMBER_WORDS.get(m.group(1).lower()) or (int(m.group(1)) if m.group(1).isdigit() else None)
            if not n:
                continue
            anchor_phrase = m.group(3).strip()
            anchor_words = retrieval.keywords(anchor_phrase) - GENERIC
            hits = retrieval.keyword_search(store, anchor_phrase, exclude_ids={msg["id"]},
                                            purpose=f"resolve '{anchor_phrase}' for {msg['id']}")
            anchor = next((it for it in items for h in hits if h["id"] in it["cites"]
                           and anchor_words <= retrieval.keywords(it["sentence"])), None)
            if not anchor:
                continue
            days = n * (7 if m.group(2).lower() == "week" else 1)
            sentence = dates._sentence(msg["body"], m.start(), m.end())
            items.append({
                "date": anchor["date"] - timedelta(days=days), "time": None, "quote": m.group(0).rstrip(".?!,"),
                "sentence": sentence, "kind": "deadline", "status": "confirmed", "who": _who(sentence, msg),
                "title": _title(sentence, msg["subject"]), "cites": [msg["id"]] + anchor["cites"], "thread": msg["thread_id"],
                "from": msg["from"], "unverified": anchor["unverified"],
                "derived": f"{days} day(s) before '{anchor_phrase}' ({anchor['date']:%b %d}, {anchor['cites'][0]})",
            })

    # ---- merge the same event mentioned in several messages ------------------------------------
    merged = []
    for it in sorted(items, key=lambda x: (x["date"], x["time"] or time(0))):
        twin = None
        for other in merged:
            if other["date"] != it["date"] or set(other["cites"]) & set(it["cites"]) or "derived" in it:
                continue
            if other["time"] and it["time"] and other["time"] != it["time"]:
                continue
            same_thread = other["thread"] == it["thread"]
            overlap = (retrieval.keywords(other["sentence"]) & retrieval.keywords(it["sentence"])) - GENERIC
            if same_thread or overlap:
                twin = other
                break
        if twin:
            twin["cites"] += [c for c in it["cites"] if c not in twin["cites"]]
            twin["time"] = twin["time"] or it["time"]
            twin["quotes"] = twin.get("quotes", [twin["quote"]]) + [it["quote"]]
            twin["unverified"] = twin["unverified"] and it["unverified"]
            if it["time"] and not twin.get("_timed_title"):   # the timed mention defines the event
                twin.update(title=it["title"], kind=it["kind"], status=it["status"], _timed_title=True)
        else:
            merged.append(dict(it))

    for n, it in enumerate(merged, 1):
        it["cid"] = f"C{n:02d}"
        it.pop("_timed_title", None)
    return merged


def conflicts(items):
    """Overlapping timed items (within 60 min) and meetings that break the owner's rule."""
    out = []
    timed = [i for i in items if i["time"] and i["kind"] in ("meeting", "fyi", "deadline")]
    for a_idx, a in enumerate(timed):
        for b in timed[a_idx + 1:]:
            if a["date"] != b["date"]:
                continue
            gap = abs((datetime.combine(a["date"], a["time"]) - datetime.combine(b["date"], b["time"])).total_seconds())
            if gap < 3600 and "meeting" in (a["kind"], b["kind"]):
                out.append({"type": "double-booked", "when": f"{a['date']:%a %b %d} {a['time']:%H:%M}",
                            "items": [a["cid"], b["cid"]], "cites": a["cites"] + b["cites"],
                            "detail": f"{a['title'][:60]}  vs  {b['title'][:60]}"})
    (earliest, source) = prefs.earliest_meeting_time()
    if earliest:
        for i in items:
            if i["kind"] == "meeting" and i["time"] and i["time"] < time(*earliest):
                out.append({"type": "breaks-standing-rule", "when": f"{i['date']:%a %b %d} {i['time']:%H:%M}",
                            "items": [i["cid"]], "cites": i["cites"] + [source],
                            "detail": f"{i['title'][:70]} is before {earliest[0]:02d}:{earliest[1]:02d} "
                                      f"(rule from {source})"})
    return out


def validate(items, store):
    """Part 3/7 check: every cited id exists and contains the quoted date text."""
    problems = []
    for it in items:
        quotes = it.get("quotes", [it["quote"]])
        for cid in it["cites"]:
            if not store.exists(cid):
                problems.append(f"{it['cid']}: cites unknown message {cid}")
        own = [q for q in quotes if any(q.lower() in store.get(c)["body"].lower() for c in it["cites"] if store.exists(c))]
        if len(own) != len(quotes):
            problems.append(f"{it['cid']}: quoted text not found in cited messages")
    return problems


def busy_slots(items):
    return [datetime.combine(i["date"], i["time"]) for i in items if i["time"]]


def propose_slots(requested: datetime, items, after: datetime, n=3, duration_min=30, offered=None):
    """Three slots honouring the owner's earliest-meeting rule and avoiding known commitments."""
    earliest, _ = prefs.earliest_meeting_time()
    earliest = time(*earliest) if earliest else time(9, 0)
    busy = busy_slots(items) + list(offered or [])     # never offer the same slot in two drafts
    slots, day = [], requested.date()
    for _ in range(10):
        if day.weekday() < 5:
            for hh in (11, 14, 16):
                cand = datetime.combine(day, time(hh, 0))
                if cand.time() < earliest or cand < after + timedelta(hours=18):   # no "in 20 minutes" offers
                    continue
                if any(abs((cand - b).total_seconds()) < 3600 for b in busy):
                    continue
                if cand == requested:
                    continue
                slots.append(cand)
                break                      # at most one slot per day -> spread the options
        if len(slots) >= n:
            break
        day += timedelta(days=1)
    if offered is not None:
        offered.extend(slots)
    return slots


def serialise(items):
    out = []
    for i in items:
        j = {k: v for k, v in i.items() if k not in ("sentence",)}
        j["date"] = i["date"].isoformat()
        j["time"] = i["time"].strftime("%H:%M") if i["time"] else None
        out.append(j)
    return out


def log(items, confl):
    for i in items:
        trace.event("commitment", cid=i["cid"], date=i["date"].isoformat(),
                    time=i["time"].strftime("%H:%M") if i["time"] else None, cites=i["cites"], title=i["title"])
    for c in confl:
        trace.event("conflict", **c)
