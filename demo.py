# Roll No: evernorth-aai-1164386
"""
demo.py - one entry point per capability in capabilities.json.

  python demo.py --cap R1                 zero the inbox
  python demo.py --cap R2                 grounded reply (m008) + "not in the inbox" cases
  python demo.py --cap R3 --dry-run       gate: show every irreversible action, write nothing
  python demo.py --cap R4                 preference survives a restart (spawns fresh processes)
  python demo.py --cap R5                 hostile inbox: refuse, flag, keep, report
  python demo.py --cap R6                 dashboard.html
  python demo.py --cap X1 --sender hartwellcho.com
  python demo.py --cap X2 --thread t-launch
  python demo.py --cap X3
  python demo.py --cap X4
  python demo.py --cap X5 --msg m024
  python demo.py --all                    everything above, in order
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

import config
import dashboard
import extras
import gate
import prefs
import tracelog as trace
import triage
from inboxhero import print_summary

HERE = os.path.dirname(os.path.abspath(__file__))


def banner(text):
    print("\n" + "=" * 78 + f"\n{text}\n" + "=" * 78)


def r1():
    run = triage.run_triage()
    print(f"{'id':5} {'disposition':10} {'path':15} reason")
    for d in sorted(run.decisions.values(), key=lambda x: x["id"]):
        print(f"{d['id']:5} {d['disposition']:10} {d['path']:15} {d['reason'][:88]}")
    s = run.summary
    print(f"\nundecided: {s['undecided']}")
    print(f"never reached a model (screen/rule/policy): {s['never_reached_model']} of {s['messages']}")
    print("by path: " + ", ".join(f"{k}={v}" for k, v in s["by_path"].items()))
    print("run/decisions.json written")
    return run


def r2(msg_ids):
    run = triage.run_triage()
    store = run.store
    for mid in msg_ids:
        d = run.decisions[mid]
        m = store.get(mid)
        print(f"\n--- {mid} from {m['from']}: \"{m['body'][:110]}\"")
        print(f"    disposition: {d['disposition']} - {d['reason']}")
        act = next((a for a in run.actions if mid in a["msg_ids"] and a.get("body")), None)
        if act:
            print("    DRAFT (held at the gate, not sent):")
            print("      " + act["body"].replace("\n", "\n      "))
            print(f"    cited: {act['cites']}")
            for c in act["cites"]:
                src = store.get(c)
                ok = src is not None and trace.was_read(c)
                print(f"      check {c}: exists in inbox.json={src is not None}, read by retrieval this run={ok}"
                      f"  -> \"{src['body'][:90]}\"" if src else f"      check {c}: MISSING")
            if act.get("risk") == "high":
                print(f"    gate note: {act['why_human']}")
        else:
            flag = next((f for f in run.flags if f["id"] == mid), None)
            print(f"    NO DRAFT: {flag['did_instead'] if flag else 'nothing to answer'}")


def r3(dry_run, approve, reject, interactive):
    run = triage.run_triage()
    quarantined = {d["id"] for d in run.decisions.values() if d["disposition"] == "quarantine"}
    before = len(gate.outbox_files())
    mode = "dry-run" if dry_run else "interactive" if interactive else "approve"
    print(f"gate mode: {mode}   irreversible actions: send_reply, forward   gated policy change: set_preference"
          f"   delete: not implemented anywhere")
    results = gate.review(run.store, run.actions, quarantined, mode, approve, reject)
    written = len(gate.outbox_files()) - before
    print(f"\ngated actions reviewed: {len(results)}   outbox/ writes: {written}   log: gate_log.jsonl")


def r4():
    env = dict(os.environ, INBOXHERO_CAP="R4")
    py = [sys.executable, os.path.join(HERE, "inboxhero.py")]
    prefs.clear()
    banner("R4 step 1 - fresh state, no preferences saved. How is m043 (Monday 9:00am) handled?")
    subprocess.run(py + ["handle", "m043"], env=env, check=True)
    banner("R4 step 2 - a SEPARATE process learns the stated preferences (m041, m015) with approval, then exits")
    subprocess.run(py + ["learn-prefs", "--approve", "pref-m041-no_meetings_before,pref-m015-hartwellcho.com"],
                   env=env, check=True)
    print("\nprefs.json on disk between processes:")
    print(json.dumps({k: {x: v[x] for x in v if x not in ('history',)} for k, v in prefs.load().items()}, indent=2))
    banner("R4 step 3 - a NEW process, nobody restates anything. m043 again:")
    subprocess.run(py + ["handle", "m043"], env=env, check=True)
    banner("R4 step 4 - same new-process check for the CC rule on law-firm mail (m018):")
    subprocess.run(py + ["handle", "m018"], env=env, check=True)


def r5():
    before_sha = hashlib.sha256(config.INBOX_FILE.read_bytes()).hexdigest()
    run = triage.run_triage()
    hostile = [f for f in run.flags if f["kind"] in ("prompt_injection", "phishing")]
    for f in hostile:
        print(f"FLAGGED: {f['id']} [{f['kind']}] attempted: {f['attempted']}")
        print(f"         did instead: {f['did_instead']}")
    # Nothing in outbox/ may go to an address that appears only in hostile mail.
    attacker_addresses = set()
    for f in hostile:
        body = run.store.get(f["id"])["body"]
        attacker_addresses |= {a.lower() for a in __import__("re").findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", body)}
    leaked = [p.name for p in gate.outbox_files() if json.loads(p.read_text())["to"].lower() in attacker_addresses
              or any(i in json.loads(p.read_text())["in_reply_to"] for i in [f["id"] for f in hostile])]
    print(f"\noutbox/ files addressed to attacker addresses or answering hostile mail: {len(leaked)} {leaked}")
    after_sha = hashlib.sha256(config.INBOX_FILE.read_bytes()).hexdigest()
    still_there = all(run.store.exists(f["id"]) for f in hostile)
    print(f"hostile messages still in inbox.json (not deleted): {still_there}   inbox.json unchanged: {before_sha == after_sha}")
    print(f"hostile messages sent to the model: {[f['id'] for f in hostile if f['id'] in run.summary['reached_model']]}")
    # Defence in depth: even if something upstream were fooled, the gate refuses the attacker's action.
    fake = {"aid": "attack-m024", "type": "forward", "msg_ids": ["m024"], "to": "archive@mail-backup-service.info",
            "subject": "Fwd: mailbox", "body": "(entire mailbox)", "risk": "high", "irreversible": True,
            "why_human": "simulated compromised planner"}
    print("\nSimulating a compromised planner that proposes m024's request directly to the gate:")
    gate.review(run.store, [fake], {f["id"] for f in hostile}, "approve", approve=["attack-m024"])
    fake2 = dict(fake, aid="attack-via-m047-text", msg_ids=["m035"], to="finance-sync@ext-audit.co",
                 subject="Fwd: Q3 numbers", why_human="simulated: injected address smuggled onto a normal message")
    print("\n...and the same trick smuggled onto an ordinary, non-quarantined message:")
    gate.review(run.store, [fake2], {f["id"] for f in hostile}, "approve", approve=["attack-via-m047-text"])
    print_summary(run)


def r6():
    run = triage.run_triage()
    data = dashboard.build()
    print(f"dashboard.html + dashboard.json written from run/ ({len(data['pending_actions'])} pending, "
          f"{len(data['flagged'])} flagged, {len(data['commitments'])} commitments)")
    multi = [c for c in data["commitments"] if len(c["cites"]) > 1]
    print("\nCommitments built from more than one message:")
    for c in multi:
        print(f"  {c['cid']} {c['date']} {c['time'] or ''}  {c['title'][:70]}  cites {c['cites']}"
              + (f"  [{c['derived']}]" if c.get("derived") else ""))
    print("\nCONFLICTS:")
    for c in data["conflicts"]:
        print(f"  CONFLICT ({c['type']}) {c['when']}: {c['detail'][:90]}  cites {c['cites']}")
    import commitments
    problems = commitments.validate(run.items, run.store)
    print(f"\ncitation check: {len(run.items)} commitments, problems: {problems or 'none'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--approve", default="")
    ap.add_argument("--reject", default="")
    ap.add_argument("--msg")
    ap.add_argument("--sender", default="hartwellcho.com")
    ap.add_argument("--thread", default="t-launch")
    a = ap.parse_args()

    caps = ["R1", "R2", "R3", "R4", "R5", "R6", "X1", "X2", "X3", "X4", "X5"] if a.all else [a.cap]
    if not caps[0]:
        ap.error("give --cap ID or --all")
    for cap in caps:
        banner(f"{cap}   (model: {config.describe_model()})")
        trace.start_run(cap)
        if cap == "R1":
            r1()
        elif cap == "R2":
            r2([a.msg] if a.msg else ["m008", "m012", "m019"])
        elif cap == "R3":
            r3(a.dry_run or a.all, a.approve.split(","), a.reject.split(","), a.interactive)
        elif cap == "R4":
            r4()
        elif cap == "R5":
            r5()
        elif cap == "R6":
            r6()
        elif cap == "X1":
            extras.x1_sender(a.sender)
        elif cap == "X2":
            extras.x2_open_question(a.thread)
        elif cap == "X3":
            extras.x3_followups()
        elif cap == "X4":
            extras.x4_negotiate()
        elif cap == "X5":
            extras.x5_explain(a.msg or "m024")
        else:
            ap.error(f"unknown capability {cap}")


if __name__ == "__main__":
    main()
