# Roll No: evernorth-aai-1164386
"""
retrieval.py - thread-walk first, keyword search second.

An inbox already carries its structure in thread_id, so walking the thread is cheaper
and more precise than embeddings. Keyword search is the fallback for references that
cross threads ("two days before the board review" -> the board-review message).
Every retrieval logs a `read` event; drafts may only cite ids that were read.
"""
import re

import tracelog as trace

STOP = set("the a an and or to of in on for at by with is it this that be are was i you we our your "
           "can could just any me my from as if so do not no".split())


def thread_context(store, msg, purpose="thread-walk"):
    earlier = store.earlier_in_thread(msg)
    trace.record_read([m["id"] for m in earlier] + [msg["id"]], purpose)
    return earlier


def keywords(text):
    return {w for w in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower()) if w not in STOP}


def keyword_search(store, phrase, exclude_ids=(), before=None, purpose="keyword"):
    """Messages containing every keyword of `phrase`, newest first."""
    words = keywords(phrase)
    hits = []
    for m in store.messages:
        if m["id"] in exclude_ids or (before and m["timestamp"] >= before):
            continue
        text = (m["subject"] + " " + m["body"]).lower()
        if words and all(w in text for w in words):
            hits.append(m)
    hits.sort(key=lambda m: m["timestamp"], reverse=True)
    trace.record_read([m["id"] for m in hits], purpose)
    return hits
