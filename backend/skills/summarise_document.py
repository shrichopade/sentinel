# summarise_document.py — summarise a document into a short plain-English summary
# This skill asks Claude for a short summary string in either plain or bullet format.

import os
import asyncio
import anthropic
from dotenv import load_dotenv

load_dotenv()

# Create one Anthropic client using the API key from the environment.
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Pick a default model that exists for this account/environment.
# If you need to swap models later, set ANTHROPIC_MODEL in backend/.env.
DEFAULT_MODEL = (os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-6").strip()


async def summarise_document(text: str, max_length: int = 200, output_format: str = "plain") -> str:
    """
    Summarise a document in plain English.
    Takes: text (string), max_length (maximum words), output_format ("plain" or "bullets").
    Returns: summary string. Returns "" on failure.
    """
    try:
        safe_text = (text or "")[:8000]
        safe_max = int(max_length) if isinstance(max_length, int) else 200
        safe_max = max(20, min(400, safe_max))
        fmt = (output_format or "plain").strip().lower()
        if fmt not in {"plain", "bullets"}:
            fmt = "plain"

        format_instruction = (
            "Return a single plain paragraph." if fmt == "plain" else "Return a short bulleted list (3-6 bullets)."
        )

        prompt = f"""Summarise the following document in plain English in at most {safe_max} words.
{format_instruction}

Return ONLY the summary text. Do not return JSON. Do not add headings.

Document text:
{safe_text}"""

        def _call():
            return client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )

        response = await asyncio.to_thread(_call)
        return (response.content[0].text if getattr(response, "content", None) else "").strip()
    except Exception as e:
        print(f"[Skill] summarise_document failed: {e}")
        return ""

