# Roll No: evernorth-aai-1164386
"""
rules.py - the "not every time do we need an agent" layer.

Receipts, newsletters, SaaS notifications and monitoring alerts are recognised from the
sender address alone and archived without a model call. A message only reaches this
layer after security.screen() has cleared it.
"""
import re

AUTOMATED_LOCALPART = re.compile(
    r"^(no-?reply|no_reply|noreply|notifications?|notify|alerts?|receipts?|billing|invoice.*|info|updates?"
    r"|digest|newsletter|hello|feedback|insights|orders|checkin|ship-confirm|calendar-notification"
    r"|security|status|support|success|notes)([-+_.][\w.+-]*)?$", re.I)

CATEGORIES = [
    ("receipt/billing", re.compile(r"receipt|invoice|bill|charged|payment|payout|statement|renews", re.I)),
    ("security notice", re.compile(r"sign-?in|password|verification code|security", re.I)),
    ("monitoring alert", re.compile(r"incident|monitor|uptime|sentry|datadog|pagerduty|resolved", re.I)),
    ("newsletter/digest", re.compile(r"digest|newsletter|top stories|top 5|weekly|recommendations|posts", re.I)),
]


def classify(msg):
    """Return (rule_id, category) if a rule settles this message, else None."""
    local = msg["from"].split("@")[0].lower()
    if AUTOMATED_LOCALPART.match(local) or local.startswith("mailer-daemon"):
        text = msg["subject"] + " " + msg["body"]
        for name, pattern in CATEGORIES:
            if pattern.search(text):
                return "RULE-AUTOMATED", name
        return "RULE-AUTOMATED", "notification"
    return None
