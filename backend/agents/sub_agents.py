# sub_agents.py — Day 2 sub-agents: each async function calls Claude (and sometimes Supabase) and returns a structured dict.

import asyncio
import os
import json
import hashlib
from datetime import datetime, timezone
import anthropic
from voyageai import Client
from dotenv import load_dotenv
from api.db import supabase, supabase_execute_with_retry

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
# The voyageai Python package exposes Client (not a class named "voyageai"). Same key name as .env: VOYAGE_API_KEY.
voyage_client = Client(api_key=os.getenv("VOYAGE_API_KEY"))
# Retry delays for temporary rate-limit spikes from the API.
RATE_LIMIT_RETRY_DELAYS = [8, 16, 32]


# Call Anthropic safely so temporary TPM spikes do not fail the full workflow.
async def _call_claude_with_retry(request_builder, call_name: str):
    for attempt, delay_seconds in enumerate(RATE_LIMIT_RETRY_DELAYS, start=1):
        try:
            # SDK call is synchronous, so we move it to a worker thread.
            return await asyncio.to_thread(request_builder)
        except anthropic.RateLimitError:
            # Final failure should still surface so API response is honest.
            if attempt == len(RATE_LIMIT_RETRY_DELAYS):
                raise
            print(f"[Retry] {call_name} hit rate limit. Waiting {delay_seconds}s before retry...")
            await asyncio.sleep(delay_seconds)
        except Exception:
            # Other errors are not fixed by retries, so bubble them up.
            raise


# Read one document and extract structured risk findings from it.
async def contract_analyst(document_id: str, focus_areas: list | None = None) -> dict:
    """
    Fetches a document from Supabase and returns a structured list of risk findings.
    Each finding: {clause_text, risk_type, severity, explanation, recommended_action}
    """
    if focus_areas is None:
        focus_areas = []

    # Step 1 — load the document row from Supabase (we need the raw contract text to analyse)
    row_result = (
        supabase.table("documents")
        .select("raw_text, vendor_name, doc_type, domain")
        .eq("id", document_id)
        .single()
        .execute()
    )
    data = row_result.data
    if not data:
        return {"error": "Document not found", "findings": [], "summary": ""}

    raw_text = data.get("raw_text") or ""

    # Step 2 — build the instruction we send to Claude (UK consumer lens).
    # We prefer a tool schema so Claude is forced to return machine-readable JSON.
    focus_str = ", ".join(focus_areas) if focus_areas else "all consumer risk areas"
    prompt = f"""You are a UK consumer rights expert analysing the following document.

Focus especially on these areas: {focus_str}.

IMPORTANT OUTPUT RULE:
- You MUST call the tool contract_analyst_report exactly once.
- Do NOT output JSON in normal text.

Document text (first 5000 characters):
{raw_text[:5000]}"""

    # This tool schema is how we “force” valid JSON output.
    # Claude must return the structured object as tool input, not as free-form text.
    contract_analyst_tool = {
        "name": "contract_analyst_report",
        "description": "Return structured consumer risk findings for the analysed document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "clause_text": {"type": "string"},
                            "risk_type": {
                                "type": "string",
                                "enum": [
                                    "auto_renewal",
                                    "price_increase",
                                    "cancellation",
                                    "liability",
                                    "data_sharing",
                                    "hidden_fee",
                                    "notice_period",
                                ],
                            },
                            "severity": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "critical"],
                            },
                            "explanation": {"type": "string"},
                            "recommended_action": {"type": "string"},
                        },
                        "required": [
                            "clause_text",
                            "risk_type",
                            "severity",
                            "explanation",
                            "recommended_action",
                        ],
                    },
                },
                "summary": {"type": "string"},
            },
            "required": ["findings", "summary"],
        },
    }

    # Step 3 — ask Claude for the structured analysis.
    # Tool forcing drastically reduces parse errors compared to “JSON-only text” prompts.
    response = await _call_claude_with_retry(
        lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1400,
            tools=[contract_analyst_tool],
            tool_choice={"type": "tool", "name": "contract_analyst_report"},
            messages=[{"role": "user", "content": prompt}],
        ),
        call_name="contract_analyst",
    )

    # Step 4 — preferred path: read the tool_use block (machine-readable JSON).
    for block in (response.content or []):
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "contract_analyst_report":
            tool_input = getattr(block, "input", None) or {}
            # Normalise keys so callers always see findings + summary + status
            if "findings" not in tool_input:
                tool_input["findings"] = []
            if "summary" not in tool_input:
                tool_input["summary"] = ""
            tool_input["status"] = "ok"
            return tool_input

    # Step 4b — fallback path: if the tool call did not happen (rare), attempt best-effort JSON parsing.
    # We retry parsing once with a stricter "JSON only" correction prompt before failing.
    raw_text = ""
    for block in (response.content or []):
        if hasattr(block, "text"):
            raw_text = block.text or raw_text

    def _try_parse_json(candidate_text: str):
        cleaned = (candidate_text or "").replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    parsed = _try_parse_json(raw_text)
    if parsed is None:
        correction_prompt = (
            "Convert the following content to STRICT valid JSON only using this schema keys exactly: "
            "{findings: [...], summary: string}. Do not add explanations.\n\n"
            f"Content:\n{raw_text[:6000]}"
        )
        correction_response = await _call_claude_with_retry(
            lambda: client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=800,
                messages=[{"role": "user", "content": correction_prompt}],
            ),
            call_name="contract_analyst_json_repair",
        )
        corrected_text = correction_response.content[0].text if correction_response.content else ""
        parsed = _try_parse_json(corrected_text)
        if parsed is None:
            return {
                "status": "parse_error",
                "findings": [],
                "summary": "Could not parse analyst response",
            }

    # Normalise keys so callers always see findings + summary
    if "findings" not in parsed:
        parsed["findings"] = []
    if "summary" not in parsed:
        parsed["summary"] = ""
    parsed["status"] = "ok"
    return parsed


# Run legal web research and return clean JSON for downstream steps.
async def research_agent(query: str, jurisdiction: str = "GB") -> dict:
    """
    Searches for regulations and consumer rights relevant to the query.
    Uses Claude's built-in web_search tool — this is the first sub-agent to use an external tool.
    Returns: {regulations: list, sources: list, summary: str}
    """
    # Import here (inside the function) so this agent stays lightweight on startup.
    from rag.regulatory import retrieve_regulatory_context

    # Step 1 — query the regulatory corpus first (fast, local, and consistent).
    regulatory_results = retrieve_regulatory_context(query=query, jurisdiction=jurisdiction)

    # Step 2 — if we found enough local coverage, return early and skip web search.
    if len(regulatory_results) >= 2:
        return {
            "regulations": [
                {
                    "name": r["regulation_name"],
                    "section": r["section_ref"],
                    "relevance": r["content"][:200],
                }
                for r in regulatory_results
            ],
            "sources": [f"{r['regulation_name']} {r['section_ref']}" for r in regulatory_results],
            "summary": (
                f"Found {len(regulatory_results)} relevant regulations in the corpus. "
                + " ".join([r["content"][:100] for r in regulatory_results[:2]])
            ),
            "source": "regulatory_rag",
        }

    # Trim long orchestrator queries so one call cannot blow the per-minute token budget.
    safe_query = (query or "")[:1200]

    # Ask Claude to search the web, then answer in strict JSON we can parse downstream
    prompt = f"""You are a UK consumer rights researcher. Use web search to find current statutes, regulations, or official guidance in jurisdiction "{jurisdiction}" that are relevant to this query:

The local regulatory corpus did not have sufficient coverage for this query.

{safe_query}

Return ONLY valid JSON — no markdown fences, no extra text — in this exact structure:

{{
  "regulations": [
    {{
      "name": "name of the Act or Regulation",
      "section": "relevant section or article",
      "relevance": "how this applies to the query, 1 sentence"
    }}
  ],
  "sources": ["URL or citation 1", "URL or citation 2"],
  "summary": "2-3 sentence plain English summary of what rights apply"
}}

Use web search results for names, sections, and URLs where possible. If you cannot verify a URL, still include the legal name and section as a citation string."""

    # Web search runs on Anthropic's servers; we still wrap the sync client in a thread so the event loop stays responsive
    def _call_research():
        return client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

    response = await _call_claude_with_retry(_call_research, call_name="research_agent")

    # The reply may include server tool blocks plus one or more text blocks — we want the last assistant text (the JSON answer)
    final_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            final_text = block.text

    if not (final_text or "").strip():
        return {"regulations": [], "sources": [], "summary": "No context found"}

    text = final_text.replace("```json", "").replace("```", "").strip()

    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                parsed = None

    if parsed is None:
        return {
            "regulations": [],
            "sources": [],
            "summary": final_text[:500] if final_text else "No context found",
        }

    # Sometimes the model puts the full answer JSON inside the "summary" string — try to recover when top-level regulations is empty
    sum_val = parsed.get("summary", "")
    if (
        isinstance(sum_val, str)
        and "{" in sum_val
        and "}" in sum_val
        and not (parsed.get("regulations") or [])
    ):
        inner_slice = sum_val[sum_val.find("{") : sum_val.rfind("}") + 1]
        try:
            inner = json.loads(inner_slice)
            if isinstance(inner, dict) and inner.get("regulations"):
                parsed = inner
        except json.JSONDecodeError:
            pass

    if "regulations" not in parsed:
        parsed["regulations"] = []
    if "sources" not in parsed:
        parsed["sources"] = []
    if "summary" not in parsed:
        parsed["summary"] = ""
    parsed["source"] = "web_search"
    return parsed


# Turn many findings into one numeric risk score and short rationale.
async def risk_scorer(findings: list, document_type: str) -> dict:
    """
    Scores the overall consumer risk of a document based on analyst findings.
    Simple synthesis step — no tools, just a short Claude call.
    Returns: {score: int (0-10), severity: str, justification: str, top_risks: list[str]}
    """
    # Nothing to score if the analyst returned no rows
    if not findings:
        return {
            "score": 0,
            "severity": "low",
            "justification": "No risk findings identified.",
            "top_risks": [],
        }

    # Turn each structured finding into one readable bullet for the prompt
    lines = []
    for f in findings:
        sev = f.get("severity", "unknown")
        rtype = f.get("risk_type", "unknown")
        expl = f.get("explanation", "")
        lines.append(f"- [{sev}] {rtype}: {expl}")
    findings_text = "\n".join(lines)

    prompt = f"""You are scoring overall consumer risk for a document of type: {document_type}.

Here are the analyst's findings (each line is one issue):

{findings_text}

Scoring guide (use these bands when picking the numeric score):
- 0–2: minimal (standard terms, no surprises)
- 3–5: moderate (some problematic clauses worth knowing)
- 6–8: high (significant consumer protection concerns)
- 9–10: critical (potentially unfair or unlawful terms)

Return ONLY valid JSON — no markdown fences, no extra text — in this exact structure:

{{
  "score": 7,
  "severity": "high",
  "justification": "2 sentence plain English explanation",
  "top_risks": ["most urgent risk 1", "most urgent risk 2"]
}}

"severity" must be one of: low, medium, high, critical (aligned with the score).
"top_risks" should list up to 3 short strings (plain English), most urgent first."""

    def _call_score():
        return client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )

    response = await _call_claude_with_retry(_call_score, call_name="risk_scorer")

    # With no tools enabled, the assistant reply is usually a single text block — read its text
    raw = response.content[0].text if response.content else ""
    raw = raw.replace("```json", "").replace("```", "").strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return {
            "score": 5,
            "severity": "medium",
            "justification": "Score unavailable.",
            "top_risks": [],
        }

    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {
            "score": 5,
            "severity": "medium",
            "justification": "Score unavailable.",
            "top_risks": [],
        }

    # Normalise types so downstream code always sees the same shape
    score = int(parsed.get("score", 5))
    score = max(0, min(10, score))
    return {
        "score": score,
        "severity": str(parsed.get("severity", "medium")),
        "justification": str(parsed.get("justification", "Score unavailable.")),
        "top_risks": list(parsed.get("top_risks") or []),
    }


# Draft a user-reviewable letter based on findings and legal context.
async def negotiation_drafter(action_type: str, context: dict) -> dict:
    """
    Drafts a formal letter to the vendor on behalf of the consumer.
    action_type: one of cancel | complain | negotiate | gdpr_sar | dispute
    context: dict containing vendor_name, findings (list), relevant_regulations (dict with "summary" key)
    Returns: {subject_line: str, letter: str, tone: str, placeholders_to_fill: list[str]}
    """
    # Import inside the function so this sub-agent stays lightweight at import time.
    from skills.draft_letter import draft_letter

    # Delegate the prompt + parsing logic to the reusable skill module.
    return await draft_letter(action_type=action_type, context=context, user_profile={})


# Save the final pending action so the user can approve it in the queue.
async def create_action_item(tool_input: dict, user_id: str, document_id: str) -> dict:
    """
    Writes a pending action to the Supabase actions table.
    This is ALWAYS the last function called by the orchestrator.
    Returns: {action_id: str, status: str, severity: str}

    user_id is kept for future multi-user / audit fields; the row shape below matches the current Day 2 insert contract.
    """
    action_fingerprint_source = {
        "document_id": document_id,
        "title": (tool_input.get("title", "Action required") or "")[:200],
        "summary": tool_input.get("summary", ""),
        "severity": tool_input.get("severity", "medium"),
    }
    # Fingerprint lets us identify semantically identical actions and avoid duplicates.
    action_fingerprint = hashlib.sha256(
        json.dumps(action_fingerprint_source, sort_keys=True).encode("utf-8")
    ).hexdigest()

    row = {
        "document_id": document_id,
        "action_type": "review",
        "severity": tool_input.get("severity", "medium"),
        "title": (tool_input.get("title", "Action required") or "")[:200],
        "summary": tool_input.get("summary", ""),
        "draft_content": tool_input.get("draft_content", ""),
        "status": "pending",
        "reasoning": tool_input.get("reasoning", ""),
        "sources": [],
        # Track whether this action came from model finalization or deterministic fallback.
        "generated_by": tool_input.get("generated_by", "model"),
        "action_fingerprint": action_fingerprint,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Step -1 — if the orchestrator is resuming, overwrite the existing pending action instead of inserting a new row.
    # This keeps the UI clean: one queue item that gets upgraded from “fallback” to “model-generated”.
    existing_action_id = (tool_input.get("existing_action_id") or "").strip()
    if existing_action_id:
        try:
            updates = {
                "severity": row["severity"],
                "title": row["title"],
                "summary": row["summary"],
                "draft_content": row["draft_content"],
                "reasoning": row["reasoning"],
                "sources": row.get("sources", []),
                "generated_by": row.get("generated_by", "model"),
            }

            def _update_existing():
                return (
                    supabase.table("actions")
                    .update(updates)
                    .eq("id", existing_action_id)
                    .eq("status", "pending")
                    .execute()
                )

            updated = await supabase_execute_with_retry(_update_existing)
            updated_rows = updated.data or []
            if updated_rows:
                return {
                    "action_id": existing_action_id,
                    "status": "updated_existing",
                    "severity": row["severity"],
                    "generated_by": row.get("generated_by", "model"),
                }
        except Exception as overwrite_error:
            # If overwrite fails (missing columns, RLS, etc.), fall back to insert/dedupe behavior.
            print(f"  [Action] overwrite existing action skipped: {overwrite_error}")

    # Step 0 — return existing pending action if this same fingerprint already exists.
    try:
        def _existing():
            return (
                supabase.table("actions")
                .select("id, severity")
                .eq("document_id", document_id)
                .eq("status", "pending")
                .eq("action_fingerprint", action_fingerprint)
                .limit(1)
                .execute()
            )

        existing = await supabase_execute_with_retry(_existing)
        existing_rows = existing.data or []
        if existing_rows:
            existing_action = existing_rows[0]
            return {
                "action_id": existing_action["id"],
                "status": "deduped_existing",
                "severity": existing_action.get("severity", row["severity"]),
                "generated_by": row["generated_by"],
            }
    except Exception as fingerprint_error:
        # Keep backward compatibility when action_fingerprint column is not added yet.
        print(f"  [Action] fingerprint dedupe skipped: {fingerprint_error}")
        try:
            def _legacy_match():
                return (
                    supabase.table("actions")
                    .select("id, severity")
                    .eq("document_id", document_id)
                    .eq("status", "pending")
                    .eq("title", row["title"])
                    .eq("summary", row["summary"])
                    .limit(1)
                    .execute()
                )

            legacy_match = await supabase_execute_with_retry(_legacy_match)
            legacy_rows = legacy_match.data or []
            if legacy_rows:
                existing_action = legacy_rows[0]
                return {
                    "action_id": existing_action["id"],
                    "status": "deduped_existing",
                    "severity": existing_action.get("severity", row["severity"]),
                    "generated_by": row["generated_by"],
                }
        except Exception as legacy_error:
            print(f"  [Action] legacy dedupe skipped: {legacy_error}")

    try:
        def _insert():
            return supabase.table("actions").insert(row).execute()

        result = await supabase_execute_with_retry(_insert)
    except Exception as e:
        # Backward compatibility: retry insert without action_fingerprint if column is not migrated yet.
        if "action_fingerprint" in row:
            legacy_row = dict(row)
            legacy_row.pop("action_fingerprint", None)

            try:
                def _insert_legacy():
                    return supabase.table("actions").insert(legacy_row).execute()

                result = await supabase_execute_with_retry(_insert_legacy)
            except Exception as legacy_insert_error:
                return {"error": f"Failed to insert action item: {legacy_insert_error}", "status": "error"}
        else:
            return {"error": f"Failed to insert action item: {e}", "status": "error"}

    data = getattr(result, "data", None)
    if data:
        action_id = data[0]["id"]
        print(f"  [Action] Created pending action: {action_id}")
        return {
            "action_id": action_id,
            "status": "created",
            "severity": row["severity"],
            "generated_by": row["generated_by"],
        }

    return {"error": "Failed to insert action item", "status": "error"}
