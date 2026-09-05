import os
import json

try:
    from google import genai
    _gemini_key = os.environ.get("GEMINI_API_KEY")
    _gemini_client = genai.Client(api_key=_gemini_key) if _gemini_key else None
except ImportError:
    _gemini_client = None

try:
    from groq import Groq
    _groq_key = os.environ.get("GROQ_API_KEY")
    _groq_client = Groq(api_key=_groq_key) if _groq_key else None
except ImportError:
    _groq_client = None


# The fixed, bounded menu of interventions the model is allowed to choose
# from. This list is the actual guardrail, the model cannot invent a
# fourth action, it can only pick one of these three.
VALID_ACTIONS = {"draft_reminder", "escalate_to_human", "hold"}


def build_decision_prompt(invoice):
    """Ask the model to determine the right intervention for this invoice,
    from a small fixed menu. This is the 'agent' decision step: the model
    picks the action, not our code."""
    return f"""You are deciding the next recovery action for one overdue B2B invoice.
Choose exactly one action from this fixed set:

- "draft_reminder": the standard path. A reminder email is likely to help.
- "escalate_to_human": this situation looks high-risk or unusual (e.g. a
  high amount that has already been ignored through multiple reminders,
  suggesting a phone call or manual negotiation would work better than
  another automated email).
- "hold": reaching out again right now doesn't make sense (e.g. only
  marginally overdue, or a reminder was already sent very recently
  relative to how many total reminders this account has had).

Invoice:
- Invoice ID: {invoice['invoice_id']}
- Customer: {invoice['customer_name']}
- Amount due: Rs. {invoice['amount']:.0f}
- Days overdue: {invoice['days_overdue']}
- Previous reminders already sent: {invoice['previous_reminders']}

Respond with ONLY valid JSON, no other text, in exactly this shape:
{{"action": "draft_reminder", "reasoning": "one short sentence"}}"""


def _parse_decision(raw_text):
    """Parse the model's JSON response defensively -- models sometimes wrap
    JSON in markdown code fences despite instructions not to."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    data = json.loads(cleaned)
    action = data.get("action")
    reasoning = data.get("reasoning", "")

    if action not in VALID_ACTIONS:
        raise ValueError(f"model returned an action outside the allowed set: {action}")

    return action, reasoning


def decide_intervention(invoice):
    """Returns (action, reasoning). Falls back to the safe default
    ('draft_reminder') if every provider fails or returns something we
    can't parse -- an undecidable case should never silently drop an
    invoice from the workflow."""
    prompt = build_decision_prompt(invoice)

    for attempt in (_try_gemini, _try_groq):
        raw = attempt(prompt)
        if raw is None:
            continue
        try:
            return _parse_decision(raw)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  [Could not parse decision from response: {e} -- trying next provider]")
            continue

    return "draft_reminder", "fallback: no provider available or all responses unparseable"


def build_prompt(invoice):
    """Construct the instruction we send to the model for this invoice."""
    # Tone escalates with previous_reminders -- this is a deliberate policy
    # decision, not something we leave to the model to decide on its own.
    if invoice["previous_reminders"] == 0:
        tone = "polite and friendly, a first gentle nudge"
    elif invoice["previous_reminders"] <= 2:
        tone = "firm but professional, referencing that this is a follow-up"
    else:
        tone = "serious and direct, noting this is a final reminder before escalation, while remaining professional and non-threatening"

    return f"""Write a short payment reminder email (under 120 words) to {invoice['customer_name']}
for an overdue invoice.

Invoice ID: {invoice['invoice_id']}
Amount due: Rs. {invoice['amount']:.0f}
Days overdue: {invoice['days_overdue']}
Previous reminders sent: {invoice['previous_reminders']}

Tone: {tone}

Do not threaten legal action. Do not use aggressive language. Do not imply
consequences to the business relationship or escalation to management.
State facts and request payment only. Include the invoice ID and amount
clearly. Sign off as "Accounts Receivable Team"."""


def _fallback_message(invoice):
    """Used when no API key is configured -- keeps the pipeline runnable
    end-to-end even without live LLM access, e.g. for a quick local test."""
    return (
        f"[TEMPLATE - no LLM key set] Dear {invoice['customer_name']}, "
        f"invoice {invoice['invoice_id']} for Rs. {invoice['amount']:.0f} "
        f"is now {invoice['days_overdue']} days overdue. Please arrange "
        f"payment at your earliest convenience. - Accounts Receivable Team"
    )


def _try_gemini(prompt):
    if _gemini_client is None:
        return None
    try:
        response = _gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        # Rate limit, quota exhaustion, transient API error, etc. -- log
        # and let the caller fall through to the next provider rather than
        # crashing the whole pipeline over one drafting failure.
        print(f"  [Gemini unavailable: {e.__class__.__name__} -- falling back]")
        return None


def _try_groq(prompt):
    if _groq_client is None:
        return None
    try:
        response = _groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"  [Groq unavailable: {e.__class__.__name__} -- falling back]")
        return None


def draft_message(invoice):
    prompt = build_prompt(invoice)

    # Fallback chain: try each provider in order, use the first one that
    # actually returns a result. This is what makes the pipeline resilient
    # to any single provider's outage or rate limit.
    for attempt in (_try_gemini, _try_groq):
        result = attempt(prompt)
        if result is not None:
            return result

    return _fallback_message(invoice)