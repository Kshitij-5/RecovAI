# RecovAI

An AI-assisted B2B invoice recovery system built for the Razorpay AI Buildathon 2026 (AI Revenue Recovery track).

## What it does

Given a batch of overdue invoices, the system:
1. Detects which invoices are overdue and computes a priority score
2. Checks hard stopping rules (max reminders reached, or a promise-to-pay already logged) -- these run first, in code, before the model is ever involved
3. For invoices that pass those checks, an LLM **determines the right intervention** from a fixed, bounded menu: draft a reminder, escalate to a human, or hold for now
4. If the decision is to draft a reminder, the LLM drafts a tone-appropriate message (tone escalates based on how many reminders have already been ignored)
5. Requires explicit human approval before any message is "sent" (simulated)
6. Logs every decision and action to an append-only audit trail
7. Reports a recovery simulation summary: how much of the total overdue amount was approved for recovery

## Why these design choices

- **Rule-based priority scoring, not ML.** With no historical outcome data to train on in a short build window, a transparent formula (`amount x days_overdue x (previous_reminders + 1)`) is both a reasonable heuristic and fully auditable -- important for a finance-adjacent system.
- **The intervention decision is bounded, not open-ended.** The LLM picks one of exactly three actions (`draft_reminder`, `escalate_to_human`, `hold`) -- it cannot invent a fourth. If its response can't be parsed into one of those three, the system falls back to the safe default (`draft_reminder`) rather than failing silently or taking an undefined action.
- **Hard stopping rules run in code, before the model is involved at all.** `MAX_REMINDERS` and the promise-to-pay flag are plain Python checks in `workflow.py`. The LLM never gets a say on an invoice that's already been stopped -- this keeps the compliance-critical boundary fully deterministic.
- **Message tone escalation policy also lives in code, not the prompt.** Once the model decides to draft, our code (not the model) decides which tone tier to use based on `previous_reminders` -- keeping that decision auditable too.
- **Human approval gate on every message.** Nothing is sent without an explicit yes from a person reviewing the actual drafted text -- this is also the safety net for cases where the LLM's wording drifts firmer than intended.
- **LLM provider fallback chain.** Tries Gemini first, falls back to Groq, falls back to a plain template if both are unavailable -- so a rate limit or provider outage never breaks the pipeline.

## Architecture

```
<img width="1536" height="1024" alt="image" src="https://github.com/user-attachments/assets/d22f376f-eaf7-4749-ba06-7893012418dc" />
d
```

## Setup

```
pip install google-genai groq
```

Set at least one LLM provider key (both recommended, for the fallback chain to matter):
```
export GEMINI_API_KEY=your-key-here
export GROQ_API_KEY=your-key-here
```

## Run

```
python3 main.py
```

You'll be prompted to approve, reject, or log a promise-to-pay for each overdue invoice. A recovery summary prints at the end.

To reset local test data:
```
python3 reset_db.py            # full wipe
python3 reset_db.py INV005     # clear promise-to-pay flag for one invoice
```

## Known limitations

- "Sending" is simulated, not wired to a real email/SMS provider
- Priority scoring is a heuristic, not a trained model -- a clear future upgrade path once outcome data exists
- Single synthetic batch of 5 invoices; not tested against larger/real datasets
- No web deployment; runs locally via CLI + optional Streamlit viewer
