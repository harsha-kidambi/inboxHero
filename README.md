# inboxHero

**GitHub:** https://github.com/harsha-kidambi/inboxHero
**Student:** Harsha Kidambi, evernorth-aai-1164386
**Course:** Agentic AI: From Concepts to Practice (IIIT Hyderabad), Assignment 06

inboxHero takes Sam's 100-message inbox (`inbox.json`) from unread to empty:

- **Every message gets a decision.** It gets exactly one disposition and a reason.
- **Safe work is done automatically.** That means archiving, deferring, drafting, and building the calendar.
- **Anything irreversible waits for a human** at a single gate.
- **Hostile mail is refused, flagged, kept and reported.**

The graded manifest is `CAPABILITIES.md` / `capabilities.json`.

---

## Quick start

```bash
python demo.py --all                 # every capability in manifest order (no setup, no pip install)
python demo.py --cap R3 --dry-run    # any single capability
```

- **Python version:** Python 3.10+ is required. Only the standard library is used.
- **Running without a model:** everything works with no `.env`, using the deterministic offline analyst.
- **Running with a model:** copy `.env.example` to `.env` and set `MODEL_PROVIDER`, `<PROVIDER>_API_KEY`, `MODEL_BASE_URL` and `MODEL_NAME`. NVIDIA NIM, Gemini and a local Ollama all work.

**The full run** that produced the shipped `outbox/`, `gate_log.jsonl`, `trace.jsonl`, `run/` and `dashboard.html` (console output saved in `evidence/`):

```bash
python demo.py --all
python inboxhero.py run --approve fwd-m015-hartwellcho.com,chase-m044,reply-m046,reply-m043 --reject reply-m008
```

The operator CLI has these commands:

| Command | What it does |
|---|---|
| `inboxhero.py run --dry-run` / `--interactive` / `--approve IDS --reject IDS` | Triage the inbox, then put each gated action in front of you |
| `inboxhero.py learn-prefs --approve IDS` | Save the standing preferences stated in the inbox |
| `inboxhero.py handle m043` | Show how one message is handled right now |
| `inboxhero.py prefs [list\|clear]` | List or clear saved preferences |
| `inboxhero.py unarchive m083` | Restore an archived message |
| `inboxhero.py dashboard` | Rebuild the dashboard from the last run |

## Architecture

```
inbox.json (read-only)
   |
   v
[1] security.screen   every message, first. Injection + phishing detectors (deterministic)
   |                  -> quarantine (7). Look-alike domains that ask for nothing -> note only.
   v
[2] rules.classify    automated senders (receipts, SaaS, alerts) -> archive (58)
   |
   v
[3] commitments.build dates from every non-hostile message -> calendar, merge, derive, conflicts
   |
   v
[4] triage.policy     owner-sent mail, resolved threads, standing instructions, law-firm mail,
   |                  meeting requests vs calendar + preferences (15)
   v
[5] model_triage      the ambiguous remainder (20), batched, fenced as <untrusted_email>;
   |                  fixed vocabulary; guard rails; offline analyst on failure / 429 / no key
   v
drafting.*            grounded drafts with cited ids + check_grounding()
   |
   v
gate.review()  <-- the ONLY path to outbox/ or prefs.json: --approve / --interactive / --dry-run,
   |               plus gate.check(): no quarantined message, no recipient the mail store can't justify
   v
outbox/*.json, gate_log.jsonl, run/*.json, dashboard.html, trace.jsonl
```

| File | Role |
|---|---|
| `config.py` | Every env setting (model provider/key/url/name, rate limits, owner, paths) |
| `mailstore.py` | Loads and validates inbox.json; never writes it |
| `security.py` | Prompt-injection and phishing screen (content only; ignores thread ids) |
| `rules.py` | Automated-sender rules (the "no agent needed" lane) |
| `dates.py` / `commitments.py` | Date extraction, cross-message derivation and merging, conflicts, slot proposals |
| `prefs.py` | Standing preferences (reuses A5 `memory.py`'s design; allow-listed types) |
| `retrieval.py` | Thread-walk + keyword fallback; logs every read |
| `drafting.py` | Grounded drafts + grounding check |
| `triage.py` | The router: screen, then rules, then policy, then model; builds proposed actions |
| `llm.py` | OpenAI-compatible client, rate-limit backoff, record/replay cache, untrusted fencing |
| `gate.py` | The only executor; approval / dry-run; recipient and quarantine checks; gate log |
| `dashboard.py` | Three-pane HTML + JSON from run files |
| `extras.py` | X1-X5 |
| `demo.py` / `inboxhero.py` | Manifest entry point / operator CLI |
| `tracelog.py` | `trace.jsonl`: every event tagged with run id and capability |

**Reused from earlier assignments:**

- A5's persistent memory design (JSON file, atomic write, last-write-wins with history) became `prefs.py`.
- The stdlib OpenAI-compatible client and the `<PROVIDER>_API_KEY` convention come from A4/A5.
- The trace-per-event idea also comes from A4/A5.

## Framework choice

**None.** A framework's core offer is "give the agent tools and let it call them". The most important property of this system is that the model holds **no** tool that can act. So the pieces a framework would have provided are exactly the pieces I needed to control by hand: the router, the gate, the trust boundary, and the replayable trace. See Final Report Q4.

## Dispositions

| Disposition | Meaning |
|---|---|
| **reply** | Draft prepared and held at the gate |
| **clarify** | Ask, don't guess |
| **archive** | Nothing needed; a reversible label |
| **defer** | Later, against a date |
| **delegate** | Hand-off note held at the gate |
| **escalate** | Only the owner can decide |
| **quarantine** | Hostile or phishing: refused, flagged, kept, reported |

Counts in the recorded full run (preferences saved): archive 74, escalate 8, quarantine 7, reply 4, defer 4, clarify 2, delegate 1. **Undecided: 0.** On a fresh copy with no preferences yet, m038 is defer rather than escalate.

## Reversible vs irreversible, and the gate

- **Irreversible: `send`, `forward`.** Each one writes one JSON file to `outbox/` and nowhere else.
- **Delete is also irreversible,** because the store has no trash. So delete **does not exist** in the code; no path can remove a message.
- **Reversible:** draft, archive, defer and label. These are labels in `run/decisions.json`.
- **`set_preference` is gated too.** It is reversible, but it changes every future run, so it needs approval.
- **The gate offers both** per-action approval and dry-run. Every gated decision is logged to `gate_log.jsonl` with what was proposed, what the human said, and what happened. The possible results are EXECUTED, REJECTED, HELD and BLOCKED.
- **Escalation line:** only 9-10 actions reach the gate (7 replies or hand-offs, 1 bundled forward, and 2 preference proposals until saved), and the three law-firm forwards are bundled into one approval. Decisions only Sam can make (legal signatures, hiring, pricing sign-off, double-bookings) are listed without a pre-written "yes". The trade-offs are covered in `CAPABILITIES.md`.

## Retrieval

**Thread-walk first, keyword search as the fallback.**

- **Thread-walk:** m008 ("resend the URL you gave Raghav") is answered from m003 in the same thread.
- **Keyword search:** m040 ("two days before the board review") is resolved through m038, a different thread, giving a deck deadline of the 16th that cites both.
- **When the answer isn't there:** if the answer is not in the inbox (m012 "that thing", m019 "the date you locked in"), the system says so and drafts nothing.

---

## Final Report

**1. What did you refuse to automate?**
**m008**, Devika's request to "just resend the URL you gave Raghav earlier". The system can answer it perfectly: `grounded_answer()` finds the AMQP URL in m003, cites it, and passes the grounding check. That's exactly why the line matters. The draft is correct, but it copies a live broker password into another inbox and onto a new machine, and a sent email can't be unsent. Devika also explicitly says she doesn't want to rotate anything. So the draft is labelled high-risk ("contains a live credential - consider the secrets manager") and held. In the recorded run it was **rejected** at the gate. Being able to ground an answer is not the same as it being safe to send.

**2. Where does untrusted text enter the system?**
Every byte of every email is untrusted and enters through `mailstore.py`. It then crosses four boundaries, and each one is code rather than prompt wording:

1. **`security.screen()` runs first on all 100 messages.** It is deterministic, so text can't argue with it. The 7 quarantined messages never reach the model or any drafter.
2. **Anything that does reach the model** is wrapped by `llm.fence()` as `<untrusted_email>` data. The model has no tools; its only output is a disposition from `MODEL_CHOICES`, validated in `model_triage()`. A `delegate_to` must be a known internal sender, and "archive" on a direct request is overridden.
3. **Drafts must pass `check_grounding()`.**
4. **Only `gate._execute()` can act,** and only after `gate.check()` confirms the message isn't quarantined and every recipient is justified by the mail store. Then a human has to approve that specific action id.

To make the system act on their behalf, an attacker would have to beat all of these at once:

- evade the screen's detectors
- steer a fixed-vocabulary classifier toward an action
- put a recipient in front of the gate who is already a legitimate correspondent, because the gate refuses anyone else
- get Sam to approve it after reading the body

Two further holes are closed. The attacker can't turn the gate off through memory, because `prefs.py` has no preference type for "auto-send" (m039's exact attempt). And the attacker's own address can never be a recipient: R5 shows `finance-sync@ext-audit.co` being BLOCKED even when smuggled onto an ordinary message.

**3. Who is accountable when it sends the wrong thing?**
**Sam is, and the design makes that fair.** Nothing leaves in his name unless he approved that exact action id, after seeing its full body, recipient, citations and a "why this needs you" line. I, as the builder, am accountable for what the system *proposes*, and for making sure the proposal really is what gets sent. Tracing a failure back works like this:

- **Each `outbox/` file** records `in_reply_to`, `cites` and `approved_by`.
- **`gate_log.jsonl`** records the proposed text and the human's decision.
- **`trace.jsonl`** records the decision path (screen, rule, policy, model or offline), the `read` events behind every citation, whether the wording came from the model or a template, and any rejected drafts.
- **`llm_cache.json`** holds the model's exact output.

With that, you can tell which kind of failure it was: a wrong fact (grounding), a wrong person (recipient check or delegation), bad wording (model), or approval without reading (human). `python demo.py --cap X5 --msg <id>` rebuilds the chain for any message.

**4. Name your own machinery.**

- **Agents:** the specialists are `security.screen`, `rules.classify`, `triage.policy`, `model_triage` (the LLM classifier), `drafting` (the writer) and `commitments` (the scheduler).
- **Tasks:** each Task is a `Run.decide()` record (a disposition and its reason) or a `Run.propose()` record (an action awaiting the gate).
- **Crew:** `triage.run_triage()` is the Crew. It runs the lanes in a fixed order and assembles their outputs.
- **Router:** the lane order plus `_act_on_model_decision()`.

Something I built that a framework would have given me is the **human-in-the-loop gate**. CrewAI has `human_input` and ADK has tool confirmation. Here a framework would have **hurt**. Its model is "the agent holds tools and calls them", but the key safety property is that the model holds no acting tool at all, and the gate re-checks recipients against the mail store no matter what the model said. A framework would have helped with plumbing (retries, a tracing UI), but it would have blurred the one boundary this assignment is about. It would also have made the offline, replayable, fully deterministic test runs much harder to guarantee.
