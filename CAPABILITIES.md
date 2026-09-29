# CAPABILITIES.md

**Student:** Harsha Kidambi, evernorth-aai-1164386
**Repository:** https://github.com/REPLACE-WITH-YOUR-USERNAME/inboxhero

Run everything through one entry point:

```
python demo.py --cap R1        # one capability
python demo.py --all           # all of them, in the order below
```

No `pip install` is needed (standard library only, Python 3.10+). With no `.env`, every command runs on the
deterministic offline analyst. With a model configured (see `.env.example`), the ambiguous remainder goes to the model.

---

## The system, in one paragraph

inboxHero is a single Python pipeline with no framework and four lanes. First, a **security screen** reads all 100 messages before anything else does. It quarantines prompt injections and phishing. It has to run first because m024's injection hides inside something that looks like a newsletter. Second, **sender rules** archive receipts, notifications and alerts. Third, **deterministic policy** handles the workflow cases:

- mail Sam sent
- threads that already resolved themselves
- standing instructions
- law-firm mail
- meeting requests, checked against a calendar built from the inbox

Only the ambiguous remainder, 20 messages, reaches the **model**, in batches of 8 with the email text fenced as untrusted data. The model has no tools. It returns a disposition from a fixed list, and code decides what that disposition may lead to. Drafts cite the message ids they drew on, and a grounding check rejects any draft containing a detail found in no cited message. Everything irreversible (send, forward, saving a standing preference) is proposed to **one gate** (`gate.py`). The gate needs per-action approval or shows a dry-run, and it independently refuses quarantined messages and recipients the mail store can't justify. The dashboard, decisions and outbox are all generated from the run files. State that outlives a run is kept on disk: `prefs.json`, `trace.jsonl`, `gate_log.jsonl`, `llm_cache.json`.

## Design choices you were asked to state

- **Messages processed: 100** (all of inbox.json). **Never reached a model: 77.** That's 7 screen + 58 rule + 15 policy = 80 decided without a model. Of those, 3 (m010, m016, m043) still send their reply *wording* through the model, which leaves 77 messages that never appear in any prompt.
- **Data format assumptions:**
  - Each message has exactly the keys `id, thread_id, from, to, subject, timestamp, body, unread`. `mailstore.py` validates this and fails loudly otherwise.
  - Timestamps are naive local ISO times.
  - There is one recipient per message and no cc or attachment fields.
  - The owner is `sam@paperjet.io`, and the organisation domain is `paperjet.io`.
  - "Now" is the newest message (2026-09-09 17:17), not the wall clock.
  - Weekday names resolve to the next occurrence *on or after* the sending day. So m016's "Wednesday", sent on a Wednesday, means that same day, which collides with m013's "Wednesday at 2:00pm this week".
  - The descriptive thread ids (`t-inj1`, `t-phish2` ...) are **not** used by any detector. Detection reads content only.
  - `inbox.json` is never written to.
- **Framework: none.** The work is a router with four lanes and one gate. The hard parts are the boundaries: which text is data, which code may act, and what counts as proof. A framework would have hidden exactly those. More in Final Report Q4.
- **Model:** NVIDIA NIM `nvidia/nemotron-3-super-120b-a12b` over the OpenAI-compatible API, used for batched triage of the ambiguous remainder, reply wording, and thread summaries. It was developed against the offline analyst and a local fake endpoint that returns HTTP 429s, drops ids and writes ungrounded drafts, so every failure path is exercised.
  - **Rate limits:** calls are spaced by `LLM_MIN_INTERVAL`, a 429 is retried with backoff honouring `Retry-After`, and after the retries the system falls back to the offline analyst rather than crashing.
  - **Cache:** `llm_cache.json` records every model answer, so a second run costs zero requests and reproduces the first.
- **Dispositions:**

  | disposition | meaning |
  |---|---|
  | reply | a response is warranted; a draft is prepared and **held at the gate** |
  | clarify | ambiguous; ask, don't guess (a question is drafted only if it can be grounded) |
  | archive | nothing needed (noise, FYI, resolved, already recorded); a reversible label |
  | defer | needs the owner later; parked against a date (a deadline or awaiting a reply) |
  | delegate | someone else should own it; a hand-off note is drafted and held |
  | escalate | only the owner can decide: money, legal, hiring, public statements, time commitments, conflicts |
  | quarantine | hostile or phishing; refused, flagged, left in place, reported |

- **Retrieval: thread-walk**, with keyword search as the cross-thread fallback. `thread_id` is the inbox's own structure, so walking it is cheaper and more precise than embeddings for "resend the URL you gave Raghav" (m008 → m003, same thread). Keyword search handles references between threads: "two days before the board review" (m040) finds m038. Every retrieval logs a `read` event, and a draft may only cite ids that were read.
- **Reversible vs irreversible.**
  - **Irreversible:** `send` and `forward`, which write one file per message to `outbox/`. Also `delete`: nothing in the design could undo it, because `inbox.json` has no trash. For that reason delete is **not implemented at all**; there is no delete function for anything, including a hostile email, to reach.
  - **Reversible:** `draft`, `archive`, `defer` and `label`. They are labels in `run/decisions.json` (`python inboxhero.py unarchive m083` restores one), and the mail store is untouched.
  - **Gated even though reversible:** `set_preference`. It changes behaviour for every future run, so it is a policy change and needs approval like a send.
- **Where the gate sits.** Only `gate._execute()` can write to `outbox/` or `prefs.json`, and only `gate.review()` calls it. That happens after approval (`--approve IDs`, `--interactive`), or never under `--dry-run`. Before executing, `gate.check()` independently refuses any action that touches a quarantined message, and any recipient not justified by the mail store: the sender being answered, an internal colleague, or a CC from an approved preference. An address that only appears *inside* an email body can never become a recipient. R5 demonstrates this by pushing two simulated attacker actions straight at the gate; both are BLOCKED. This is the Part 6 defence as architecture rather than a prompt line: the model can influence a draft's wording, but it cannot reach a send.
- **Escalation line, and what it costs.**
  - **What goes to the gate:** only 9-10 actions out of 100 messages: 7 replies or hand-offs, 1 bundled forward once the CC rule is saved, and 2 preference proposals until they are saved.
  - **What runs without a prompt:** archives and defers. The cost is that a wrongly archived FYI is possible, although it is reversible.
  - **Batched approval:** the standing CC rule produces **one** forward of three law-firm messages instead of three separate approvals. The cost is less granularity: Sam approves the bundle or none of it.
  - **Decisions only Sam can make** (legal signatures, the hiring reply, the pricing sign-off, both double-bookings) get no drafted reply at all. They are listed as decisions, because a pre-written "yes" is the kind of thing that gets approved without being read.
  - **Risk labels:** high-risk sends (external recipients, the live credential in m008) are labelled so they are read first.
- **Preference honoured:**
  - **m041** "I do not take meetings before 11:00am, ever" changes **m043** (Aria, Monday 9:00am). Before the preference is saved, it is accepted. After a restart, it is declined with three 11:00-or-later slots.
  - **m015** "CC me on anything from Hartwell & Cho" changes **m018/m048/m055**. The law firm's domain is grounded by finding which sender signs as "Hartwell & Cho LLP".
  - **m039**, a spoofed "from Sam" message trying to save "autonomous mode" as a preference, is quarantined. There is also no preference type it could write to.

## Capabilities

| id | name | tier | one-line claim |
|----|------|------|----------------|
| R1 | Zero the inbox | B | every message gets one disposition + reason; undecided: 0; 77 never reach a model |
| R2 | Grounded reply | B | m008's draft cites m003 (checked); m012 and m019 get no draft because the answer isn't in the inbox |
| R3 | Gate the irreversible | C | no send, forward or preference without approval or --dry-run; no delete exists |
| R4 | Persistent preference | C | m041's rule, saved in one process, changes m043's reply in a later process |
| R5 | Refuse embedded instructions | C | 4 injections + 3 phishing refused, flagged, kept, reported; the gate blocks attacker recipients |
| R6 | Dashboard | C | three panes; commitments cited, merged across messages, conflicts called out |
| X1 | Mail from a sender | A | every message from a sender/domain with its disposition |
| X2 | Thread to open question | B | 9-message launch thread reduced to the one question for Sam (m030) |
| X3 | Follow-up tracking | B | m044 unanswered for 6 days gets a chase draft; m003 (answered) doesn't |
| X4 | Meeting negotiator | C | conflicts + standing rule, then first claim, then 3 non-overlapping slots, then a draft held at the gate |
| X5 | Why did you do that? | B | rebuilds any message's decision chain from the run files and the trace |

The exact command, observable outcome and evidence for each is in `capabilities.json`. That file is the machine-readable version, and it is what a marking script reads. This file is for a human. The two are kept in step.

## Final Report

See `README.md` ("Final Report"). The four answers are reproduced there in full.
