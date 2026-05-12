# check_price_drift.py — detect price drift (increases) from transaction history
# This skill asks Claude to compare the current amount vs prior bills and explain any increase.

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


def _parse_json_object(text: str) -> dict:
    """
    Parse a JSON object from a Claude response that may include markdown fences.
    Takes: text (string).
    Returns: dict (possibly empty) for safety.
    """
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def check_price_drift(vendor: str, current_amount: float, transaction_history: list) -> dict:
    """
    Detect whether a vendor's price has drifted upward compared to prior transactions.
    Takes: vendor (string), current_amount (float), transaction_history (list of {date, amount, description}).
    Returns: {drift_detected: bool, drift_pct: float, explanation: str, recommended_action: str}
    Returns a safe fallback dict on failure.
    """
    if not transaction_history:
        return {
            "drift_detected": False,
            "drift_pct": 0.0,
            "explanation": "No history available",
            "recommended_action": "Monitor future bills",
        }

    try:
        safe_vendor = (vendor or "Unknown vendor")[:120]
        try:
            amt = float(current_amount)
        except Exception:
            amt = 0.0

        # Keep prompt small and safe by limiting history length.
        history_slice = transaction_history[:30]
        history_text = json.dumps(history_slice, ensure_ascii=False)

        prompt = f"""You are helping detect if a subscription price has increased.

Vendor: {safe_vendor}
Current amount: {amt}
Transaction history (most recent first if possible):
{history_text}

Detect if the current amount represents a meaningful price increase compared to the typical historical amount.

Return ONLY valid JSON — no markdown fences, no extra text:
{{
  "drift_detected": true,
  "drift_pct": 12.5,
  "explanation": "1-2 sentence plain English explanation",
  "recommended_action": "1 sentence recommendation"
}}"""

        def _call():
            return client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

        response = await asyncio.to_thread(_call)
        raw = response.content[0].text if getattr(response, "content", None) else ""
        parsed = _parse_json_object(raw)
        if not parsed:
            raise ValueError("parse_failed")

        drift_detected = bool(parsed.get("drift_detected", False))
        try:
            drift_pct = float(parsed.get("drift_pct", 0.0))
        except Exception:
            drift_pct = 0.0

        return {
            "drift_detected": drift_detected,
            "drift_pct": drift_pct,
            "explanation": str(parsed.get("explanation", "")),
            "recommended_action": str(parsed.get("recommended_action", "")),
        }
    except Exception as e:
        print(f"[Skill] check_price_drift failed: {e}")
        return {
            "drift_detected": False,
            "drift_pct": 0.0,
            "explanation": "Parse failed",
            "recommended_action": "Monitor future bills",
        }

