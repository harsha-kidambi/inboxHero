# Roll No: evernorth-aai-1164386
"""
mailstore.py - read-only access to inbox.json.

inbox.json is the source of truth and is NEVER written to. Dispositions, labels and
archive state live in run/decisions.json, so every organising action is reversible
and nothing - including a hostile message - can be deleted from the store.
"""
import hashlib
import json
from datetime import datetime

import config

REQUIRED_KEYS = {"id", "thread_id", "from", "to", "subject", "timestamp", "body", "unread"}


class MailStore:
    def __init__(self, path=None):
        self.path = path or config.INBOX_FILE
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        messages = json.loads(raw)
        problems = []
        for m in messages:
            missing = REQUIRED_KEYS - set(m)
            if missing:
                problems.append(f"{m.get('id', '?')}: missing {sorted(missing)}")
        if problems:
            raise ValueError("inbox.json format problems: " + "; ".join(problems))
        self.messages = sorted(messages, key=lambda m: m["timestamp"])
        self.by_id = {m["id"]: m for m in self.messages}
        if len(self.by_id) != len(self.messages):
            raise ValueError("duplicate message ids in inbox.json")
        self.threads = {}
        for m in self.messages:
            self.threads.setdefault(m["thread_id"], []).append(m)
        # "Now" for this mailbox = the newest message. The inbox is a snapshot, so we
        # reason relative to it rather than to the wall clock.
        self.now = datetime.fromisoformat(self.messages[-1]["timestamp"])

    def get(self, msg_id):
        return self.by_id.get(msg_id)

    def exists(self, msg_id) -> bool:
        return msg_id in self.by_id

    def thread(self, thread_id):
        return list(self.threads.get(thread_id, []))

    def earlier_in_thread(self, msg):
        return [m for m in self.threads[msg["thread_id"]] if m["timestamp"] < msg["timestamp"]]

    def later_in_thread(self, msg):
        return [m for m in self.threads[msg["thread_id"]] if m["timestamp"] > msg["timestamp"]]

    @staticmethod
    def ts(msg) -> datetime:
        return datetime.fromisoformat(msg["timestamp"])

    @staticmethod
    def domain(address: str) -> str:
        return address.split("@", 1)[-1].lower()
