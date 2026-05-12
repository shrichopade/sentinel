# generate_gdpr_sar.py — generate a UK GDPR Article 15 Subject Access Request (SAR)
# This skill drafts a SAR letter with placeholders and a clear 30-day response deadline.

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


async def generate_gdpr_sar(company: str, user_details: dict) -> dict:
    """
    Draft a UK GDPR Article 15 Subject Access Request (SAR) letter.
    Takes: company (string) and user_details (dict with name, address, email).
    Returns: {subject_line: str, letter: str, deadline_days: int, placeholders_to_fill: list}
    Returns a safe fallback dict on failure.
    """
    fallback = {
        "subject_line": "Subject Access Request (UK GDPR Article 15)",
        "letter": "",
        "deadline_days": 30,
        "placeholders_to_fill": ["[PLACEHOLDER]"],
    }

    try:
        safe_company = (company or "the company")[:200]
        details = user_details or {}
        # Ensure required keys exist (even if empty) so the prompt is stable.
        details_norm = {
            "name": details.get("name", "[PLACEHOLDER]"),
            "address": details.get("address", "[PLACEHOLDER]"),
            "email": details.get("email", "[PLACEHOLDER]"),
        }
        details_text = json.dumps(details_norm, ensure_ascii=False)

        prompt = f"""Draft a UK GDPR Article 15 Subject Access Request letter to:
{safe_company}

User details:
{details_text}

Requirements:
- Must cite "UK GDPR Article 15 (Right of access)" by name.
- Must state the organisation has 30 days to respond.
- Use [PLACEHOLDER] for anything the user must fill in.
- Keep it clear and professional.

Return ONLY valid JSON — no markdown fences, no extra text:
{{
  "subject_line": "Subject Access Request (UK GDPR Article 15) — [Your Name]",
  "letter": "Dear [PLACEHOLDER],\\n\\n[full SAR letter]\\n\\nYours faithfully,\\n[Your Name]\\n[Your Address]\\n[Your Email]\\n[Date: PLACEHOLDER]",
  "deadline_days": 30,
  "placeholders_to_fill": ["list every PLACEHOLDER field the user must complete"]
}}"""

        def _call():
            return client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=900,
                messages=[{"role": "user", "content": prompt}],
            )

        response = await asyncio.to_thread(_call)
        raw = response.content[0].text if getattr(response, "content", None) else ""
        parsed = _parse_json_object(raw)
        if not parsed:
            return fallback

        return {
            "subject_line": str(parsed.get("subject_line", fallback["subject_line"])),
            "letter": str(parsed.get("letter", "")),
            "deadline_days": int(parsed.get("deadline_days", 30) or 30),
            "placeholders_to_fill": list(parsed.get("placeholders_to_fill") or []),
        }
    except Exception as e:
        print(f"[Skill] generate_gdpr_sar failed: {e}")
        return fallback

