# orchestrator.py — defines the Day 2 orchestrator: Claude with tools (agentic loop pattern).
# Claude chooses which tool to call; separate Python code will run each tool later.

import asyncio
import os
import json
import anthropic
from dotenv import load_dotenv
from agents.guardrails import apply_guardrails

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Tool definitions passed to Claude. Names must stay stable — the dispatcher will map them to Python functions.
ORCHESTRATOR_TOOLS = [
    {
        "name": "invoke_contract_analyst",
        "description": "Analyse a document for risky clauses, missing consumer protections, and obligations. Always call this first for any new document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "UUID of the document row in Supabase"},
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional topics to emphasise, e.g. cancellation, auto-renewal, price increases",
                },
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "invoke_research_agent",
        "description": "Research current UK consumer rights, regulations, or statutory rates relevant to a finding. Use after contract analysis to find applicable law.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look up (plain English)"},
                "jurisdiction": {
                    "type": "string",
                    "description": "Legal region code; default GB if omitted",
                    "default": "GB",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "invoke_risk_scorer",
        "description": "Score the overall consumer risk of a document on a scale of 0-10, given analyst findings. Call after invoke_contract_analyst.",
        "input_schema": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Structured findings from the contract analyst",
                },
                "document_type": {"type": "string", "description": "e.g. contract, policy"},
            },
            "required": ["findings", "document_type"],
        },
    },
    {
        "name": "invoke_negotiation_drafter",
        "description": "Draft a letter on behalf of the consumer — cancellation, complaint, negotiation, GDPR SAR, or dispute. Call after risk scoring when score is 4 or above.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["cancel", "complain", "negotiate", "gdpr_sar", "dispute"],
                    "description": "Kind of letter to draft",
                },
                "context": {
                    "type": "object",
                    "description": "Must include vendor_name, findings, relevant_regulations",
                    "properties": {
                        "vendor_name": {"type": "string"},
                        "findings": {"type": "array", "items": {"type": "object"}},
                        "relevant_regulations": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "required": ["action_type", "context"],
        },
    },
    {
        "name": "create_action_item",
        "description": "Create a human-in-the-loop action item for the user to review and approve. This is ALWAYS the last tool called in every orchestration run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                },
                "title": {"type": "string", "maxLength": 80},
                "summary": {"type": "string", "description": "One sentence for the queue"},
                "draft_content": {"type": "string", "description": "Full letter or action text"},
                "reasoning": {"type": "string", "description": "Why the agent recommends this action"},
            },
            "required": ["severity", "title", "summary", "draft_content", "reasoning"],
        },
    },
]

# Instructions given to Claude on every orchestration turn — defines order, grounding, and when to draft vs only log low risk.
ORCHESTRATOR_SYSTEM = """You are a compliance agent analysing documents on behalf of a UK consumer.

Always work through this sequence: (1) invoke_contract_analyst, (2) invoke_research_agent for any finding needing regulatory context, (3) invoke_risk_scorer, (4) invoke_negotiation_drafter if risk score is 4 or above, (5) create_action_item — always last.

Never invent regulatory references not returned by the research agent.

If risk score is below 4, still call create_action_item with severity=low.

Be concise — one sentence of reasoning per finding is sufficient."""


# Keep each tool result small so prompt size stays under rate limits.
MAX_TOOL_RESULT_CHARS = 1800
# Keep only a short recent chat tail (plus state snapshot) for each model turn.
MAX_RECENT_MESSAGES = 4
# Keep planner output modest to reduce token-per-minute pressure.
MAX_ORCHESTRATOR_OUTPUT_TOKENS = 900
# Retry delays for temporary rate-limit spikes from the API.
RATE_LIMIT_RETRY_DELAYS = [8, 16, 32]


# Build a compact one-line state snapshot so Claude can continue planning without full history.
def _build_state_snapshot(working_memory: dict) -> str:
    trigger = working_memory.get("trigger", {})
    findings = working_memory.get("findings", [])
    risk_score = working_memory.get("risk_score")
    action_item = working_memory.get("action_item")

    top_findings = []
    for finding in findings[:3]:
        # Pull a short label even if the finding schema changes between tools.
        label = finding.get("title") or finding.get("issue") or finding.get("summary") or str(finding)
        top_findings.append(str(label)[:120])

    return (
        "Current state:\n"
        f"- document_id: {trigger.get('document_id')}\n"
        f"- analysis_status: {working_memory.get('analysis_status')}\n"
        f"- findings_count: {len(findings)}\n"
        f"- top_findings: {top_findings}\n"
        f"- risk_score: {risk_score}\n"
        f"- action_created: {bool(action_item)}"
    )


# Shrink large tool output to only the fields the orchestrator needs for next-step decisions.
def _summarize_tool_result(tool_name: str, result: dict) -> dict:
    if not isinstance(result, dict):
        return {"summary": str(result)[:MAX_TOOL_RESULT_CHARS]}

    if tool_name == "invoke_contract_analyst":
        findings = result.get("findings", [])
        compact_findings = []
        for finding in findings[:3]:
            compact_findings.append(
                {
                    "title": finding.get("title") or finding.get("issue"),
                    "severity": finding.get("severity"),
                    "summary": (finding.get("summary") or "")[:220],
                }
            )
        return {
            "status": result.get("status"),
            "findings_count": len(findings),
            "findings_preview": compact_findings,
        }

    if tool_name == "invoke_research_agent":
        refs = result.get("references", [])
        return {
            "status": result.get("status"),
            "summary": (result.get("summary") or "")[:420],
            "references": refs[:3],
        }

    if tool_name == "invoke_risk_scorer":
        return {
            "score": result.get("score"),
            "severity": result.get("severity"),
            "justification": (result.get("justification") or "")[:320],
            "top_risks": result.get("top_risks", [])[:3],
        }

    if tool_name == "invoke_negotiation_drafter":
        return {
            "action_type": result.get("action_type"),
            "subject": (result.get("subject") or "")[:140],
            "preview": (result.get("body") or "")[:420],
        }

    if tool_name == "create_action_item":
        return {
            "status": result.get("status"),
            "action_id": result.get("action_id"),
            "severity": result.get("severity"),
        }

    # Generic fallback for any future tool.
    compact = {}
    for key, value in list(result.items())[:8]:
        compact[key] = str(value)[:220]
    return compact


# Convert numeric score to queue severity so fallback action matches the same risk bands.
def _severity_from_score(score: int | None) -> str:
    if score is None:
        return "medium"
    if score <= 2:
        return "low"
    if score <= 5:
        return "medium"
    if score <= 8:
        return "high"
    return "critical"


# Build a deterministic action payload when the model forgets to call create_action_item.
def _build_fallback_action_input(working_memory: dict) -> dict:
    trigger = working_memory.get("trigger", {})
    vendor_name = trigger.get("vendor_name") or "Unknown vendor"
    findings_count = len(working_memory.get("findings", []))
    risk_score = working_memory.get("risk_score")
    # Parse failures and rate limits are high-priority unknown-risk scenarios, not low-risk outcomes.
    if working_memory.get("analysis_status") in {"failed", "rate_limited"}:
        severity = "high"
    else:
        severity = _severity_from_score(risk_score)

    if working_memory.get("analysis_status") == "failed":
        draft_content = (
            "Sentinel.AI could not reliably parse the analyst output for this document. "
            "Please run a manual review before taking action."
        )
    elif working_memory.get("analysis_status") == "rate_limited":
        draft_content = (
            "Sentinel.AI started analysis, but hit an AI rate limit before it could finish. "
            "You can click Continue analysis in the Action Queue to resume when limits reset."
        )
    else:
        draft_content = working_memory.get("draft_content") or (
            "Please review the findings generated by Sentinel.AI and decide whether to cancel, complain, "
            "negotiate, or monitor this contract."
        )

    return {
        "severity": severity,
        "title": (
            f"Manual review required for {vendor_name}"
            if working_memory.get("analysis_status") == "failed"
            else f"Review risk findings for {vendor_name}"
        ),
        "summary": (
            "Automatic fallback action: analysis failed to parse structured findings."
            if working_memory.get("analysis_status") == "failed"
            else (
                "Automatic fallback action: analysis paused due to AI rate limits."
                if working_memory.get("analysis_status") == "rate_limited"
                else f"Automatic fallback action. Risk score: {risk_score if risk_score is not None else 'unknown'} "
                f"across {findings_count} findings."
            )
        ),
        "draft_content": draft_content,
        "reasoning": (
            "Fallback action created because orchestration finished without calling create_action_item."
            if working_memory.get("analysis_status") not in {"rate_limited", "incomplete"}
            else (
                "Fallback action created because analysis could not complete. "
                "Use Continue analysis in the Action Queue to resume and generate a full draft."
            )
        ),
        # Mark provenance so UI can distinguish fallback-created actions.
        "generated_by": "fallback",
    }


# Pick a deterministic letter type so fallback drafting can run without model planning.
def _pick_fallback_action_type(working_memory: dict) -> str:
    domain = (working_memory.get("trigger", {}).get("domain") or "").lower()
    if domain == "subscription":
        return "cancel"
    return "negotiate"


# Ensure we still have a draft letter when the model skipped invoke_negotiation_drafter.
async def _ensure_fallback_draft(working_memory: dict, user_id: str) -> None:
    score = working_memory.get("risk_score")
    if score is None or score < 4:
        return
    if working_memory.get("draft_content"):
        return

    draft_input = {
        "action_type": _pick_fallback_action_type(working_memory),
        "context": {
            "vendor_name": working_memory.get("trigger", {}).get("vendor_name"),
            "findings": working_memory.get("findings", []),
            "relevant_regulations": [working_memory.get("research_summary", "")],
        },
    }

    draft_result = await execute_tool(
        tool_name="invoke_negotiation_drafter",
        tool_input=draft_input,
        working_memory=working_memory,
        user_id=user_id,
    )
    working_memory["steps"].append(
        {
            "tool": "invoke_negotiation_drafter_fallback",
            "input": draft_input,
            "output_summary": str(draft_result)[:200],
        }
    )


# Call Anthropic with retries so short TPM spikes do not crash the whole run.
async def _call_claude_with_retry(request_builder, call_name: str):
    for attempt, delay_seconds in enumerate(RATE_LIMIT_RETRY_DELAYS, start=1):
        try:
            # Anthropic SDK is synchronous, so run it in a background thread.
            return await asyncio.to_thread(request_builder)
        except anthropic.RateLimitError as err:
            # On the final attempt, raise the error so API layer can report failure.
            if attempt == len(RATE_LIMIT_RETRY_DELAYS):
                raise
            print(f"[Retry] {call_name} hit rate limit. Waiting {delay_seconds}s before retry...")
            await asyncio.sleep(delay_seconds)
        except Exception:
            # Non-rate-limit errors should bubble up immediately.
            raise


# Run one short finalization pass that is only allowed to call create_action_item.
async def _finalize_action_with_model(working_memory: dict, user_id: str) -> bool:
    finalization_prompt = (
        "You must now finish this run by calling create_action_item exactly once.\n"
        "Do not call any other tool.\n"
        "Use current state and include concise reasoning."
    )
    response = await _call_claude_with_retry(
        lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=ORCHESTRATOR_SYSTEM,
            tools=[next(tool for tool in ORCHESTRATOR_TOOLS if tool["name"] == "create_action_item")],
            messages=[
                {"role": "user", "content": _build_state_snapshot(working_memory)},
                {"role": "user", "content": finalization_prompt},
            ],
        ),
        call_name="orchestrator_finalize_action",
    )

    if response.stop_reason != "tool_use":
        return False

    from agents.step_logger import log_step

    for block in response.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        if block.name != "create_action_item":
            continue

        # Stamp provenance for UI/audit so we know this came from model finalization.
        tool_input = dict(block.input or {})
        tool_input["generated_by"] = "model"
        result = await execute_tool("create_action_item", tool_input, working_memory, user_id)
        log_step(
            document_id=working_memory["trigger"]["document_id"],
            tool_name="create_action_item_finalized",
            summary=str(result)[:200],
            user_id=user_id,
        )
        working_memory["steps"].append(
            {
                "tool": "create_action_item_finalized",
                "input": tool_input,
                "output_summary": str(result)[:200],
            }
        )
        working_memory["action_generation_mode"] = "model_finalized"
        return True

    return False


# Run the main planning loop and keep memory of tool outputs.
async def orchestrate(trigger: dict, user_id: str = "dev") -> dict:
    """
    Main agentic loop: ask Claude with tools, run whichever tools it requests,
    send JSON results back, repeat until Claude ends the turn or the safety limit is hit.

    trigger must include: document_id, vendor_name, document_type, domain (strings).
    Returns working_memory with steps, findings, risk_score, and optional action_item.
    """
    # Scratch pad that accumulates everything we learn across tool calls
    working_memory = {
        "trigger": trigger,
        "steps": [],
        "findings": [],
        "risk_score": None,
        "action_item": None,
        # Track extracted research and draft text for deterministic fallback behavior.
        "research_summary": "",
        "draft_content": "",
        # Cache avoids calling the same tool with the same input multiple times in one run.
        "tool_cache": {},
        # Research can be expensive; cap calls per run to protect token budget.
        "research_calls": 0,
        # Explicitly track analysis completeness so parse failures are never treated as low risk.
        "analysis_status": "completed",
        # Lets UI/analytics separate model-created actions from fallback-created ones.
        "action_generation_mode": "",
    }

    # First message tells Claude which document to work on (IDs and labels from the trigger)
    initial_user_text = (
        f"Analyse this document for compliance.\n\n"
        f"document_id: {trigger['document_id']}\n"
        f"document_type: {trigger['document_type']}\n"
        f"vendor_name: {trigger['vendor_name']}\n"
        f"domain: {trigger['domain']}\n\n"
        f"Follow the orchestration sequence using tools until complete, ending with create_action_item."
    )
    messages = [{"role": "user", "content": initial_user_text}]

    # Safety cap so a bug or confused model cannot loop forever
    MAX_ITERATIONS = 10
    iteration = 0

    while iteration < MAX_ITERATIONS:
        iteration += 1

        # Claude runs in a thread pool because the Anthropic client is synchronous
        # Rebuild a compact context every loop so token usage stays predictable.
        state_snapshot = _build_state_snapshot(working_memory)
        model_messages = [
            {"role": "user", "content": initial_user_text},
            {"role": "user", "content": state_snapshot},
            *messages[-MAX_RECENT_MESSAGES:],
        ]

        try:
            response = await _call_claude_with_retry(
                lambda: client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=MAX_ORCHESTRATOR_OUTPUT_TOKENS,
                    system=ORCHESTRATOR_SYSTEM,
                    tools=ORCHESTRATOR_TOOLS,
                    messages=model_messages,
                ),
                call_name="orchestrator_planner",
            )
        except anthropic.RateLimitError as rl_err:
            # Mark rate limiting explicitly so UI can guide the user to “Continue analysis”.
            print(f"[Orchestrator] Rate limited: {rl_err}")
            working_memory["analysis_status"] = "rate_limited"
            break

        # Keep the full assistant turn (text + tool_use blocks) in the conversation history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            from agents.step_logger import log_step

            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) == "tool_use":
                    print(f"  [Loop] Calling tool: {block.name}")
                    if block.name == "invoke_research_agent" and working_memory["research_calls"] >= 1:
                        # Skip extra research calls to avoid rate-limit spikes.
                        result = {
                            "status": "skipped",
                            "summary": "Research call skipped because one research call already ran in this orchestration.",
                            "regulations": [],
                            "sources": [],
                        }
                    else:
                        # Build a stable cache key so repeated tool calls can reuse earlier results.
                        tool_signature = f"{block.name}:{json.dumps(block.input, sort_keys=True)}"
                        if tool_signature in working_memory["tool_cache"]:
                            # Reuse earlier output to prevent duplicate expensive LLM calls.
                            result = working_memory["tool_cache"][tool_signature]
                        else:
                            result = await execute_tool(block.name, block.input, working_memory, user_id)
                            working_memory["tool_cache"][tool_signature] = result
                        if block.name == "invoke_research_agent":
                            working_memory["research_calls"] += 1
                    log_step(
                        document_id=trigger["document_id"],
                        tool_name=block.name,
                        summary=str(result)[:200],
                        user_id=user_id,
                    )
                    working_memory["steps"].append(
                        {
                            "tool": block.name,
                            "input": block.input,
                            "output_summary": str(result)[:200],
                        }
                    )
                    summary_payload = _summarize_tool_result(block.name, result)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(summary_payload)[:MAX_TOOL_RESULT_CHARS],
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
        else:
            # e.g. max_tokens — stop rather than spin
            break

    # Guarantee a pending action exists even if the model ended early.
    if working_memory.get("action_item") is None:
        # If we hit safety caps or ended without a clear “end_turn”, treat as incomplete (not successful).
        if working_memory.get("analysis_status") == "completed":
            working_memory["analysis_status"] = "incomplete"
        # Give the model one constrained chance to finalize the required action.
        finalized = await _finalize_action_with_model(working_memory, user_id)
        if finalized:
            pass
        else:
            # If risk is meaningful, create a deterministic draft first.
            await _ensure_fallback_draft(working_memory, user_id)
            fallback_input = _build_fallback_action_input(working_memory)
            fallback_result = await execute_tool(
                tool_name="create_action_item",
                tool_input=fallback_input,
                working_memory=working_memory,
                user_id=user_id,
            )
            working_memory["steps"].append(
                {
                    "tool": "create_action_item_fallback",
                    "input": fallback_input,
                    "output_summary": str(fallback_result)[:200],
                }
            )
            # Preserve the fallback result as the run action output.
            working_memory["action_item"] = fallback_result
            working_memory["action_generation_mode"] = "fallback"

    # Post-loop guardrail pass: sanitize final action draft and annotate findings with warnings.
    action_item = working_memory.get("action_item")
    if isinstance(action_item, dict) and action_item.get("draft_content"):
        guarded_action = apply_guardrails(
            {"content": action_item.get("draft_content", ""), "warnings": action_item.get("warnings", [])},
            output_type="letter_draft",
        )
        action_item["draft_content"] = guarded_action.get("content", action_item.get("draft_content", ""))
        action_item["warnings"] = guarded_action.get("warnings", [])
        action_item["escalate"] = guarded_action.get("escalate", False)
        action_item["escalation_reason"] = guarded_action.get("escalation_reason", "")

    findings = working_memory.get("findings", [])
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            guarded_finding = apply_guardrails(
                {"content": finding.get("summary", ""), "warnings": finding.get("warnings", [])},
                output_type="risk_assessment",
            )
            finding["warnings"] = guarded_finding.get("warnings", [])

    return working_memory


async def execute_tool(tool_name: str, tool_input: dict, working_memory: dict, user_id: str) -> dict:
    """
    Dispatches orchestrator tool names to the matching sub-agent function.
    Imports are inside the body to avoid circular imports while the package grows.
    """
    from agents.sub_agents import (
        contract_analyst,
        research_agent,
        risk_scorer,
        negotiation_drafter,
        create_action_item,
    )

    if tool_name == "invoke_contract_analyst":
        result = await contract_analyst(
            document_id=tool_input["document_id"],
            focus_areas=tool_input.get("focus_areas", []),
        )
        working_memory["findings"] = result.get("findings", [])
        # Mark parse failures so downstream scoring and UI can treat this as unresolved risk.
        if result.get("status") == "parse_error":
            working_memory["analysis_status"] = "failed"
        return result

    if tool_name == "invoke_research_agent":
        result = await research_agent(
            query=tool_input["query"],
            jurisdiction=tool_input.get("jurisdiction", "GB"),
        )
        working_memory["research_summary"] = result.get("summary", "")
        return result

    if tool_name == "invoke_risk_scorer":
        # If analyst parsing failed, do not emit a fake "0 low risk" score.
        if working_memory.get("analysis_status") == "failed":
            return {
                "status": "unavailable",
                "score": None,
                "severity": "unknown",
                "justification": "Risk score unavailable because contract analysis failed to parse.",
                "top_risks": [],
            }
        result = await risk_scorer(
            findings=tool_input["findings"],
            document_type=tool_input["document_type"],
        )
        working_memory["risk_score"] = result.get("score")
        return result

    if tool_name == "invoke_negotiation_drafter":
        result = await negotiation_drafter(
            action_type=tool_input["action_type"],
            context=tool_input["context"],
        )
        working_memory["draft_content"] = result.get("letter") or result.get("body") or ""
        return result

    if tool_name == "create_action_item":
        if "generated_by" not in tool_input:
            # Default provenance for direct model tool calls during main planning loop.
            tool_input = {**tool_input, "generated_by": "model"}
        # If the trigger carries an existing action id (resume from UI), pass it through.
        existing_action_id = (working_memory.get("trigger") or {}).get("existing_action_id")
        if existing_action_id and "existing_action_id" not in tool_input:
            tool_input = {**tool_input, "existing_action_id": existing_action_id}
        result = await create_action_item(
            tool_input=tool_input,
            user_id=user_id,
            document_id=working_memory["trigger"]["document_id"],
        )
        working_memory["action_item"] = result
        working_memory["action_generation_mode"] = (
            "fallback" if tool_input.get("generated_by") == "fallback" else "model"
        )
        return result

    return {"error": f"Unknown tool: {tool_name}"}
