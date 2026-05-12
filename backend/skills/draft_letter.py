# draft_letter.py — draft a user-reviewable letter (cancel/complain/negotiate/etc.)
# This skill centralizes letter drafting so agents do not duplicate prompts and parsing logic.

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


async def draft_letter(action_type: str, context: dict, user_profile: dict = {}) -> dict:
    """
    Draft a human-editable letter for a given action type using the provided context.
    Takes: action_type (string), context (dict), user_profile (dict, optional).
    Returns: {subject_line: str, letter: str, tone: str, placeholders_to_fill: list}
    Returns a safe fallback dict on failure.
    """
    try:
        vendor = (context or {}).get("vendor_name", "the company")
        findings = (context or {}).get("findings", []) or []
        rr = (context or {}).get("relevant_regulations") or {}

        # Pull regulatory summary into a string so the model sees it clearly.
        if isinstance(rr, dict):
            reg_summary = rr.get("summary", "") or ""
        elif isinstance(rr, str):
            reg_summary = rr
        else:
            reg_summary = ""

        # Up to five short bullets so the model stays grounded.
        bullets = []
        for f in findings[:5]:
            if not isinstance(f, dict):
                continue
            bullets.append(f"- {f.get('risk_type', 'issue')}: {f.get('explanation', '')}")
        findings_text = "\n".join(bullets) if bullets else "None listed."

        action_map = {
            "cancel": "cancel the contract and request written confirmation",
            "complain": "formally complain about unfair terms and request remediation",
            "negotiate": "negotiate improved terms citing consumer rights",
            "gdpr_sar": "submit a Subject Access Request under UK GDPR Article 15",
            "dispute": "dispute a charge or service failure and request refund or remedy",
        }
        action_description = action_map.get(action_type, "address the matter with the vendor")

        system_prompt = f"""You are drafting a {action_type} letter to {vendor} for a UK consumer.
Rules you MUST follow:
1. Professional, firm tone — not aggressive.
2. Reference ONLY regulations mentioned in the provided context. Do NOT invent legal references.
3. Include a 14-day deadline for the recipient to respond.
4. Mark every field the consumer must fill in as [PLACEHOLDER].
5. Keep the letter under 350 words.
6. Do not make legal threats not supported by the provided context."""

        # User profile is optional context (name/address/email preferences).
        # We include it as JSON so the model can use it without guessing.
        profile_text = json.dumps(user_profile or {}, ensure_ascii=False)

        user_content = f"""The consumer wants to: {action_description}
Vendor: {vendor}
User profile (may be empty):
{profile_text}

Issues found in the contract:
{findings_text}

Applicable consumer rights:
{reg_summary}

Return ONLY valid JSON:
{{
  "subject_line": "Re: [action] of [service name] — [Your Name] — Account [PLACEHOLDER]",
  "letter": "Dear [PLACEHOLDER],\\n\\n[full letter body]\\n\\nYours sincerely,\\n[Your Name]\\n[Your Address]\\n[Date: PLACEHOLDER]",
  "tone": "one of: formal | firm | assertive",
  "placeholders_to_fill": ["list every PLACEHOLDER field the consumer must complete before sending"]
}}"""

        def _call():
            return client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=1200,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )

        response = await asyncio.to_thread(_call)
        raw_full = response.content[0].text if getattr(response, "content", None) else ""
        parsed = _parse_json_object(raw_full)

        if parsed:
            return {
                "subject_line": str(parsed.get("subject_line", f"Re: {action_type}")),
                "letter": str(parsed.get("letter", raw_full)),
                "tone": str(parsed.get("tone", "formal")),
                "placeholders_to_fill": list(parsed.get("placeholders_to_fill") or []),
            }

        return {
            "subject_line": f"Re: {action_type}",
            "letter": raw_full,
            "tone": "formal",
            "placeholders_to_fill": [],
        }
    except Exception as e:
        print(f"[Skill] draft_letter failed: {e}")
        return {
            "subject_line": f"Re: {action_type}",
            "letter": "",
            "tone": "formal",
            "placeholders_to_fill": [],
        }

