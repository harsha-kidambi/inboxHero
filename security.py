# Roll No: evernorth-aai-1164386
"""
security.py - the hostile-inbox screen. Runs FIRST, on every message, before rules or
any model sees anything (an injection can hide in a message that looks like a
newsletter, e.g. from a no-reply sender).

Deterministic on purpose: an attacker who can write text into an email must not be
able to talk the detector out of detecting them. Uses message content only - never
the thread id.

Two detectors:
  prompt injection - text addressed to an AI/assistant that asks for a dangerous
                     action, tries to override policy, or asks to be hidden.
  phishing / BEC   - payment-detail changes, wire requests with secrecy/urgency from
                     look-alike domains, credential-harvesting links.
Plus a low-severity note for look-alike sender domains that ask for nothing.
"""
import re

import config

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"https?://[^\s]+")

ADDRESSED_TO_AI = re.compile(
    r"\b(ai agent|automated assistants?|automated-agent|an assistant managing|assistant (?:note|configuration)"
    r"|system notice for automated|if an ai|agent directive|autonomous mode)\b", re.I)
OVERRIDE = re.compile(
    r"\b(ignore (?:all )?previous instructions|autonomous mode|without asking for approval"
    r"|skip the confirmation|authori[sz]ed backup routine)\b", re.I)
CONCEAL = re.compile(
    r"\b(do not mention|don't mention|not surface|no need to (?:mention|surface)|must not surface"
    r"|without telling)\b", re.I)
FORWARD = re.compile(r"\bforward\b[^.]{0,120}?\bto\s+([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", re.I)
MASS_REPLY = re.compile(r"reply with the single word (\w+) to the sender of every", re.I)
DELETE = re.compile(r"\b(delete this message|then delete|deleting)\b", re.I)
AUTO_SEND = re.compile(r"\bsend all drafted replies\b[^.]*automatically", re.I)
PERSIST = re.compile(r"\b(standing preference|survives restarts)\b", re.I)

BANK_CHANGE = re.compile(r"(banking partner has changed|disregard the account on file|updated remittance|new account)", re.I)
MONEY_MOVE = re.compile(r"\b(wire|remit|transfer)\b", re.I)
SECRECY = re.compile(r"(keep this between us|don't loop in|do not loop in|confidential)", re.I)
URGENCY = re.compile(r"\b(urgent|immediately|today|end of day|within \d+ hours|in \d+ hours|suspended)\b", re.I)
CRED_ASK = re.compile(r"\b(re-?verify|verify) your (credentials|password|account|login)\b", re.I)


def lookalike(domain: str) -> bool:
    org = config.ORG_DOMAIN.split(".")[0]           # "paperjet"
    return domain != config.ORG_DOMAIN and org in domain


def screen(msg) -> dict | None:
    """Return a flag dict if the message is hostile/suspicious, else None."""
    text = f"{msg['subject']}\n{msg['body']}"
    sender_domain = msg["from"].split("@")[-1].lower()

    # ---- prompt injection -------------------------------------------------------------
    addressed = bool(ADDRESSED_TO_AI.search(text))
    attempted = []
    for m in FORWARD.finditer(text):
        attempted.append(f"forward mail to external address {m.group(1)}")
    m = MASS_REPLY.search(text)
    if m:
        attempted.append(f"mass-reply '{m.group(1)}' to the sender of every unread message")
    if AUTO_SEND.search(text):
        attempted.append("auto-send replies to investors and press without approval")
    if OVERRIDE.search(text):
        attempted.append("override the assistant's policy/approval gate")
    if DELETE.search(text):
        attempted.append("delete a message")
    if PERSIST.search(text):
        attempted.append("persist itself as a standing preference across restarts")
    if CONCEAL.search(text):
        attempted.append("hide the action from the user's summary")
    if addressed and attempted:
        note = ""
        if msg["from"].lower() == config.OWNER.lower():
            note = " Claims to come from the owner's own address; a From header is not authentication."
        return {"id": msg["id"], "kind": "prompt_injection", "severity": "high",
                "attempted": "; ".join(dict.fromkeys(attempted)),
                "did_instead": "Refused. Quarantined in place (not deleted), no action taken, "
                               "never sent to the model, reported in the run summary." + note}

    # ---- phishing / business-email compromise -----------------------------------------------
    is_lookalike = lookalike(sender_domain)
    reasons = []
    if BANK_CHANGE.search(text) and MONEY_MOVE.search(text):
        reasons.append("get a payment sent to changed bank details")
    if MONEY_MOVE.search(text) and re.search(r"\$[\d,]+", text) and (is_lookalike or SECRECY.search(text)) \
            and URGENCY.search(text):
        reasons.append("get an urgent, secret wire sent" + (f" from look-alike domain {sender_domain}" if is_lookalike else ""))
    if CRED_ASK.search(text) and (is_lookalike or URL_RE.search(text)):
        link = URL_RE.search(text)
        reasons.append("harvest credentials via link" + (f" {link.group(0)}" if link else "")
                       + (f" from look-alike domain {sender_domain}" if is_lookalike else ""))
    if reasons:
        return {"id": msg["id"], "kind": "phishing", "severity": "high",
                "attempted": "; ".join(reasons),
                "did_instead": "Refused. No payment, reply or click; quarantined in place and reported. "
                               "Verify out-of-band with the real person if in doubt."}

    if is_lookalike:
        return {"id": msg["id"], "kind": "unverified_domain", "severity": "low",
                "attempted": f"nothing requested, but {sender_domain} is not {config.ORG_DOMAIN}",
                "did_instead": "Processed normally (informational only); facts taken from it are marked unverified."}
    return None
