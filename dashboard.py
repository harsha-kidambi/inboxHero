# Roll No: evernorth-aai-1164386
"""
dashboard.py - one static HTML page (+ dashboard.json) generated from a completed run's
files in run/ and gate_log.jsonl. Never hand-assembled: rerun the triage and it rebuilds.

Pane 1  Pending actions   everything the system may not do alone (+ decisions only you can make)
Pane 2  Flagged           hostile mail, phishing, could-not-ground, unverified senders
Pane 3  Commitments       calendar with cited sources; conflicts called out at the top
"""
import calendar
import html
import json
from datetime import date

import config
import gate


def _load(name):
    return json.loads((config.RUN_DIR / name).read_text())


def build():
    decisions = _load("decisions.json")
    pending = _load("pending.json")
    flags = _load("flags.json")
    cal = _load("commitments.json")
    summary = _load("summary.json")
    status = gate.gate_status()

    needs_you = [d for d in decisions if d["disposition"] in ("escalate", "clarify")
                 and not any(d["id"] in a["msg_ids"] for a in pending)]
    data = {
        "generated_from": "run/*.json + gate_log.jsonl",
        "summary": summary,
        "pending_actions": [{"aid": a["aid"], "messages": a["msg_ids"], "proposed": a.get("summary") or
                             f"{a['type']} to {a.get('to')}: {a.get('subject')}", "why_human": a["why_human"],
                             "risk": a["risk"], "gate": status.get(a["aid"], {}).get("result", "awaiting approval")}
                            for a in pending],
        "decisions_for_you": [{"message": d["id"], "subject": d["subject"], "disposition": d["disposition"],
                               "why": d["reason"]} for d in needs_you],
        "flagged": flags["flags"], "notes": flags["notes"],
        "commitments": cal["items"], "conflicts": cal["conflicts"],
    }
    config.DASHBOARD_JSON.write_text(json.dumps(data, indent=1, default=str))
    config.DASHBOARD_HTML.write_text(render(data))
    return data


def _e(x):
    return html.escape(str(x))


def _cites(ids):
    return " ".join(f'<span class="cite">{_e(i)}</span>' for i in ids)


def render(d):
    conflict_cids = {c for x in d["conflicts"] for c in x["items"]}
    by_day = {}
    for it in d["commitments"]:
        by_day.setdefault(it["date"], []).append(it)

    rows_p = "".join(
        f"<tr><td>{_cites(a['messages'])}</td><td>{_e(a['proposed'])}</td><td>{_e(a['why_human'])}</td>"
        f"<td><span class='risk {a['risk']}'>{a['risk']}</span></td><td>{_e(a['gate'])}</td></tr>"
        for a in d["pending_actions"])
    rows_y = "".join(
        f"<tr><td>{_cites([x['message']])}</td><td>{_e(x['subject'])}</td><td>{_e(x['disposition'])}</td>"
        f"<td>{_e(x['why'])}</td></tr>" for x in d["decisions_for_you"])
    rows_f = "".join(
        f"<tr class='sev-{f['severity']}'><td>{_cites([f['id']])}</td><td>{_e(f['kind'].replace('_', ' '))}</td>"
        f"<td>{_e(f['attempted'])}</td><td>{_e(f['did_instead'])}</td></tr>" for f in d["flagged"] + d["notes"])
    rows_c = "".join(
        f"<li><b>{_e(c['type'])}</b> - {_e(c['when'])}: {_e(c['detail'])} {_cites(c['cites'])}</li>"
        for c in d["conflicts"])

    months = sorted({(date.fromisoformat(k).year, date.fromisoformat(k).month) for k in by_day})
    grids = ""
    for y, m in months:
        cells = ""
        for week in calendar.Calendar().monthdatescalendar(y, m):
            cells += "<tr>"
            for day in week:
                items = by_day.get(day.isoformat(), []) if day.month == m else []
                inner = "".join(
                    f"<div class='ev {it['kind']}{' clash' if it['cid'] in conflict_cids else ''}"
                    f"{' unverified' if it.get('unverified') else ''}' title='{_e(it['title'])}'>"
                    f"{_e(it['time'] or '')} {_e(it['title'][:48])} {_cites(it['cites'])}"
                    f"{'<br><i>' + _e(it['derived']) + '</i>' if it.get('derived') else ''}</div>" for it in items)
                cls = "out" if day.month != m else ""
                cells += f"<td class='{cls}'><div class='dn'>{day.day}</div>{inner}</td>"
            cells += "</tr>"
        grids += (f"<h3>{calendar.month_name[m]} {y}</h3><table class='cal'><tr>"
                  + "".join(f"<th>{d_}</th>" for d_ in calendar.day_abbr) + f"</tr>{cells}</table>")

    s = d["summary"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>inboxHero dashboard</title>
<style>
:root{{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e1dc;--red:#b3261e;--amber:#9a6700;--green:#1a7f37;--blue:#0b57d0}}
body{{margin:0;font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--ink)}}
header{{padding:20px 24px;border-bottom:1px solid var(--line);background:var(--card)}}
h1{{margin:0;font-size:20px}} .meta{{color:var(--mute);font-size:13px;margin-top:4px}}
main{{display:grid;gap:18px;padding:18px 24px;grid-template-columns:1fr}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow-x:auto}}
h2{{font-size:16px;margin:0 0 10px}} h3{{font-size:14px;margin:14px 0 6px;color:var(--mute)}}
table{{border-collapse:collapse;width:100%}} td,th{{border-top:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left}}
th{{font-size:12px;color:var(--mute);font-weight:600}}
.cite{{font:12px ui-monospace,Menlo,monospace;background:#eef2fb;color:var(--blue);padding:1px 5px;border-radius:4px;margin-right:2px;white-space:nowrap}}
.risk{{font-size:12px;padding:1px 6px;border-radius:4px}} .risk.high{{background:#fde7e6;color:var(--red)}}
.risk.medium{{background:#fff4d6;color:var(--amber)}} .risk.low{{background:#e6f4ea;color:var(--green)}}
.sev-high td:nth-child(2){{color:var(--red);font-weight:600}} .sev-medium td:nth-child(2){{color:var(--amber)}}
.conflicts{{border:1px solid #f1b5b1;background:#fdf1f0;border-radius:8px;padding:8px 12px;margin-bottom:10px}}
.conflicts b{{color:var(--red)}}
.cal td{{width:14.28%;height:88px;border:1px solid var(--line)}} .cal td.out{{background:#fafaf8}}
.dn{{font-size:12px;color:var(--mute)}} .ev{{font-size:11.5px;margin:3px 0;padding:3px 5px;border-radius:5px;background:#eef2fb;border-left:3px solid var(--blue)}}
.ev.deadline{{background:#fff4d6;border-color:var(--amber)}} .ev.fyi{{background:#f1f1ee;border-color:#aaa;color:var(--mute)}}
.ev.clash{{background:#fde7e6;border-color:var(--red);font-weight:600}} .ev.unverified{{outline:1px dashed var(--amber)}}
.legend span{{margin-right:12px;font-size:12px;color:var(--mute)}}
</style></head><body>
<header><h1>inboxHero - run dashboard</h1>
<div class="meta">{s['messages']} messages - undecided: {s['undecided']} - never reached a model: {s['never_reached_model']}
 - model: {_e(s['model'])} - hostile/phishing found: {len(s['hostile'])} - generated from run/*.json</div></header>
<main>
<section><h2>1 - Pending actions ({len(d['pending_actions'])})</h2>
<table><tr><th>Message</th><th>Proposed action</th><th>Why it needs a human</th><th>Risk</th><th>Gate</th></tr>{rows_p}</table>
<h3>Decisions only you can make ({len(d['decisions_for_you'])})</h3>
<table><tr><th>Message</th><th>Subject</th><th>Disposition</th><th>Why</th></tr>{rows_y}</table></section>
<section><h2>2 - Flagged ({len(d['flagged'])} refused / could not ground, {len(d['notes'])} notes)</h2>
<table><tr><th>Message</th><th>Kind</th><th>What was attempted</th><th>What the system did instead</th></tr>{rows_f}</table></section>
<section><h2>3 - Commitments ({len(d['commitments'])})</h2>
<div class="conflicts"><b>Conflicts ({len(d['conflicts'])})</b><ul>{rows_c or '<li>none</li>'}</ul></div>
<div class="legend"><span>blue = meeting/event</span><span>amber = deadline</span><span>grey = FYI (automated mail)</span>
<span>red = conflict</span><span>dashed = from an unverified look-alike domain</span></div>
{grids}</section>
</main></body></html>"""
