# extract_obligations.py — extract dated obligations from a document
# This skill asks Claude to find deadlines/payments/renewals and returns structured obligations.

import os
import json
import asyncio
import anthropic
from dotenv import load_dotenv

load_dotenv()

# Create one Anthropic client using the API key from the environment.
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Pick a default model that exists for this account/environment.
# If you need to swap models later, set ANTHROPIC_MODEL in backend/.env.
DEFAULT_MODEL = (os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-6").strip()


def _parse_json_array(text: str) -> list:
    """
    Parse a JSON array from a Claude response that may include markdown fences.
    Takes: text (string).
    Returns: list (possibly empty) for safety.
    """
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()

    # Attempt 1: parse whole string as JSON.
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        pass

    # Attempt 2: parse the first [...] slice.
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    return []


async def extract_obligations(text: str, doc_type: str) -> list:
    """
    Extract all dated obligations from a document.
    Takes: text (full document text) and doc_type (string like "contract" or "policy").
    Returns: a list of dicts:
      [{obligation_type, due_date, description, financial_amount, currency}]
    Returns [] on failure.
    """
    try:
        safe_text = (text or "")[:6000]
        safe_doc_type = (doc_type or "document")[:80]

        prompt = f"""You are reviewing a {safe_doc_type}. Extract ALL dated obligations — payments, renewals, cancellation deadlines, and notice requirements.

Return ONLY a valid JSON array — no markdown fences, no extra text:

[
  {{
    "obligation_type": "one of: renewal | cancellation | payment | notice",
    "due_date": "YYYY-MM-DD or null",
    "description": "what must happen, 1 sentence",
    "financial_amount": 0.0,
    "currency": "GBP"
  }}
]

If there are no obligations, return an empty array: []

Document text:
{safe_text}"""

        # Anthropic SDK call is synchronous, so we run it in a worker thread.
        def _call():
            return client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )

        response = await asyncio.to_thread(_call)
        raw = response.content[0].text if getattr(response, "content", None) else ""

        obligations = _parse_json_array(raw)

        # Normalise to the exact list shape the rest of the pipeline expects.
        cleaned = []
        for o in obligations:
            if not isinstance(o, dict):
                continue
            cleaned.append(
                {
                    "obligation_type": o.get("obligation_type"),
                    "due_date": o.get("due_date"),
                    "description": o.get("description"),
                    "financial_amount": o.get("financial_amount", 0.0),
                    "currency": o.get("currency", "GBP"),
                }
            )

        return cleaned
    except Exception as e:
        print(f"[Skill] extract_obligations failed: {e}")
        return []

