# score_risk.py — score the risk of one clause
# This skill asks Claude to score a clause and return a structured risk assessment.

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

    # Attempt 1: parse whole string.
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    # Attempt 2: parse first {...} slice.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {}


async def score_clause_risk(clause: str, jurisdiction: str, doc_type: str) -> dict:
    """
    Score one clause for risk (0–10) with a short explanation.
    Takes: clause text, jurisdiction (e.g. "GB"), and doc_type (e.g. "contract").
    Returns: {score: int, severity: str, explanation: str, recommended_action: str}
    Returns a safe fallback dict on failure.
    """
    fallback = {
        "score": 5,
        "severity": "medium",
        "explanation": "Parse failed",
        "recommended_action": "",
    }

    try:
        safe_clause = (clause or "")[:2500]
        safe_j = (jurisdiction or "GB")[:10]
        safe_doc_type = (doc_type or "document")[:80]

        prompt = f"""You are a consumer compliance risk scorer.

Jurisdiction: {safe_j}
Document type: {safe_doc_type}

Score the following clause from 0 to 10 (0 = no risk, 10 = critical consumer risk).

Clause:
{safe_clause}

Return ONLY valid JSON — no markdown fences, no extra text:
{{
  "score": 5,
  "severity": "low|medium|high|critical",
  "explanation": "1-2 sentence plain English explanation",
  "recommended_action": "1 sentence suggestion for what the user should do"
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
            return fallback

        # Normalise score to 0–10 int.
        try:
            score = int(parsed.get("score", 5))
        except Exception:
            score = 5
        score = max(0, min(10, score))

        return {
            "score": score,
            "severity": str(parsed.get("severity", "medium")),
            "explanation": str(parsed.get("explanation", "")),
            "recommended_action": str(parsed.get("recommended_action", "")),
        }
    except Exception as e:
        print(f"[Skill] score_clause_risk failed: {e}")
        return fallback

