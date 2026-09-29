# Roll No: evernorth-aai-1164386
"""
dates.py - deterministic extraction of dates/times from message text.

Dates are too important to let a model invent, so they come from patterns and every
match keeps the exact text it came from (so the dashboard can cite it).

Resolution rules (stated in the manifest):
  * "the 18th"            -> that day in the message's month (next month if already past)
  * "Tuesday the 15th"    -> same, weekday is a cross-check only
  * "Sep 12", "September 15" -> that date in the message's year
  * "Friday", "Monday at 9:00am" -> next occurrence ON OR AFTER the sending day
                                   ("next week" pushes it into the following week)
  * "before month-end"    -> last day of the message's month
  * "expires in 48 hours" -> timestamp + 48h
A bare weekday only counts if it is anchored ("by Friday", "for Thursday", "closed
Monday", "out Thursday", "on Wednesday at 2:00pm"), so "moved from Thursday" is ignored.
"""
import calendar
import re
from datetime import date, datetime, time, timedelta

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}

MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
WD_RE = "|".join(WEEKDAYS)
TIME_AFTER = re.compile(r"^[,\s]*(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)

P_MONTH_DAY = re.compile(rf"\b({MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
P_WD_NTH = re.compile(rf"\b({WD_RE})\s+the\s+(\d{{1,2}})(?:st|nd|rd|th)\b", re.I)
P_NTH = re.compile(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b", re.I)
P_WD = re.compile(rf"\b(by|for|on|closed|out|until|before)\s+({WD_RE})\b|\b({WD_RE})\s+at\s+\d", re.I)
P_MONTH_END = re.compile(r"\b(before |by )?month-end\b", re.I)
P_HOURS = re.compile(r"\bexpires in (\d+) hours\b", re.I)


def _time_after(text, end):
    m = TIME_AFTER.match(text[end:end + 25])
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return time(hour, minute)


def _sentence(text, start, end):
    """The sentence around [start, end). Splits on '. ' so '$2,140.55' and 'paperjet.io' stay whole."""
    lefts = [text.rfind(sep, 0, start) + len(sep) for sep in (". ", "? ", "! ", "\n") if text.rfind(sep, 0, start) != -1]
    left = max(lefts) if lefts else 0
    rights = [i + 1 for i in (text.find(". ", end), text.find("? ", end), text.find("! ", end), text.find("\n", end)) if i != -1]
    right = min(rights) if rights else len(text)
    return text[left:right].strip()


def previous_sentence(text, sentence):
    idx = text.find(sentence)
    if idx <= 0:
        return ""
    return _sentence(text, max(0, idx - 2), max(0, idx - 2))


def extract(msg):
    """Yield dicts {date, time, text, sentence} for every date mentioned in the body."""
    text = re.sub(r"\b(Dr|Mr|Mrs|Ms)\. ", r"\1 ", msg["body"])   # "Dr. Osei" is not a sentence end
    sent = datetime.fromisoformat(msg["timestamp"])
    found, taken = [], []

    def add(d, start, end, t=None):
        if any(s <= start < e for s, e in taken):
            return
        taken.append((start, end))
        if t is None:
            t = _time_after(text, end)
        if d < sent.date():               # already in the past when written
            return
        found.append({"date": d, "time": t, "text": text[start:end], "sentence": _sentence(text, start, end)})

    def day_in_month(day):
        y, mth = sent.year, sent.month
        if day < sent.day:                # "the 2nd" written on the 20th means next month
            mth = mth % 12 + 1
            y += 1 if mth == 1 else 0
        try:
            return date(y, mth, day)
        except ValueError:
            return None

    for m in P_WD_NTH.finditer(text):
        d = day_in_month(int(m.group(2)))
        if d:
            add(d, m.start(), m.end())
    for m in P_MONTH_DAY.finditer(text):
        try:
            add(date(sent.year, MONTHS[m.group(1).lower()], int(m.group(2))), m.start(), m.end())
        except ValueError:
            pass
    for m in P_NTH.finditer(text):
        d = day_in_month(int(m.group(1)))
        if d:
            add(d, m.start(), m.end())
    for m in P_WD.finditer(text):
        name = (m.group(2) or m.group(3)).lower()
        start = m.start(2) if m.group(2) else m.start(3)
        end = m.end(2) if m.group(2) else m.end(3)
        if re.match(rf"^,?\s*({MONTH_RE})\.?\s+\d", text[end:end + 20], re.I):
            continue                      # "Tuesday, September 15" - the explicit date wins
        delta = (WEEKDAYS[name] - sent.weekday()) % 7
        d = sent.date() + timedelta(days=delta)
        if re.search(r"next week", _sentence(text, start, end), re.I):
            next_monday = sent.date() + timedelta(days=7 - sent.weekday())
            d = next_monday + timedelta(days=WEEKDAYS[name])
        add(d, start, end)
    for m in P_MONTH_END.finditer(text):
        last = calendar.monthrange(sent.year, sent.month)[1]
        add(date(sent.year, sent.month, last), m.start(), m.end())
    for m in P_HOURS.finditer(text):
        dt = sent + timedelta(hours=int(m.group(1)))
        add(dt.date(), m.start(), m.end(), t=dt.time())
    return found
