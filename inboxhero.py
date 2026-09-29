# Roll No: evernorth-aai-1164386
"""
inboxhero.py - the operator's CLI.

  python inboxhero.py run --dry-run                    triage + show every gated action, write nothing
  python inboxhero.py run --interactive                triage + y/n per gated action
  python inboxhero.py run --approve A,B --reject C     triage + explicit per-action approvals
  python inboxhero.py learn-prefs --approve IDs        only the preference proposals go to the gate
  python inboxhero.py handle m043                      how one message is handled right now
  python inboxhero.py prefs [list|clear]
  python inboxhero.py unarchive m083                   archive is reversible: restore a message
  python inboxhero.py dashboard                        rebuild dashboard.html from the last run
"""
import argparse
import json
import os
import sys

import config
import dashboard
import gate
import prefs
import tracelog as trace
import triage


def _run(cap):
    trace.start_run(os.environ.get("INBOXHERO_CAP", cap))
    return triage.run_triage()


def print_summary(run):
    s = run.summary
    print(f"\n== Run summary ({s['model']}) ==")
    print(f"messages: {s['messages']}   undecided: {s['undecided']}   never reached a model: {s['never_reached_model']}")
    print("dispositions: " + ", ".join(f"{k}={v}" for k, v in s["by_disposition"].items()))
    print(f"proposed actions awaiting the gate: {s['pending_actions']}")
    if s["hostile"]:
        print(f"\n!! {len(s['hostile'])} hostile / phishing message(s) found - refused, left in place, NOT deleted:")
        for h in s["hostile"]:
            print(f"   {h['id']} [{h['kind']}] tried to: {h['attempted']}")


def main():
    ap = argparse.ArgumentParser(description="inboxHero operator CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--interactive", action="store_true")
    r.add_argument("--approve", default="")
    r.add_argument("--reject", default="")
    lp = sub.add_parser("learn-prefs")
    lp.add_argument("--approve", default="")
    lp.add_argument("--dry-run", action="store_true")
    h = sub.add_parser("handle")
    h.add_argument("msg_id")
    p = sub.add_parser("prefs")
    p.add_argument("action", nargs="?", default="list", choices=["list", "clear"])
    u = sub.add_parser("unarchive")
    u.add_argument("msg_id")
    sub.add_parser("dashboard")
    a = ap.parse_args()

    if a.cmd == "run":
        run = _run("run")
        quarantined = {d["id"] for d in run.decisions.values() if d["disposition"] == "quarantine"}
        mode = "dry-run" if a.dry_run else "interactive" if a.interactive else "approve"
        gate.review(run.store, run.actions, quarantined, mode, a.approve.split(","), a.reject.split(","))
        dashboard.build()
        print_summary(run)
        print(f"outbox/ files now: {len(gate.outbox_files())}   dashboard: dashboard.html")
    elif a.cmd == "learn-prefs":
        run = _run("learn-prefs")
        proposals = [x for x in run.actions if x["type"] == "set_preference"]
        print(f"[process {os.getpid()}] found {len(proposals)} preference proposal(s) in the inbox")
        gate.review(run.store, proposals, set(), "dry-run" if a.dry_run else "approve", a.approve.split(","), [])
        print(f"\nprefs.json now holds: {list(prefs.load())}")
        print(f"[process {os.getpid()}] exiting.")
    elif a.cmd == "handle":
        run = _run("handle")
        d = run.decisions[a.msg_id]
        m = run.store.get(a.msg_id)
        print(f"[process {os.getpid()}] prefs loaded from disk: {list(prefs.load()) or 'none'}")
        print(f"{a.msg_id} ({m['from']}): \"{m['body'][:100]}\"")
        print(f"  -> {d['disposition'].upper()}: {d['reason']}")
        for act in run.actions:
            if a.msg_id in act["msg_ids"]:
                print(f"  proposed {act['aid']} ({act['type']}), held for approval; cites {act.get('cites')}")
                if act.get("body"):
                    print(f"    To: {act['to']}\n    " + act["body"].replace("\n", "\n    "))
    elif a.cmd == "prefs":
        if a.action == "clear":
            prefs.clear()
            print("prefs.json removed")
        else:
            print(json.dumps(prefs.load(), indent=2) if prefs.load() else "(no preferences saved)")
    elif a.cmd == "unarchive":
        path = config.RUN_DIR / "decisions.json"
        decisions = json.loads(path.read_text())
        for d in decisions:
            if d["id"] == a.msg_id:
                d["previous"] = {"disposition": d["disposition"], "reason": d["reason"]}
                d["disposition"], d["reason"], d["path"] = "defer", "Restored from archive by the owner.", "owner"
                trace.start_run("unarchive")
                trace.event("decision", msg_id=a.msg_id, disposition="defer", path="owner", reason="unarchived")
        path.write_text(json.dumps(decisions, indent=1))
        print(f"{a.msg_id} restored (inbox.json untouched; only the label in run/decisions.json changed)")
    elif a.cmd == "dashboard":
        dashboard.build()
        print("dashboard.html rebuilt from run/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
