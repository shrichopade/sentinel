# actions.py — FastAPI routes for the human-in-the-loop action queue (list, review, approve, reject, edit, send).
# These endpoints let users control risky actions before any external communication is sent.

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from postgrest.exceptions import APIError

from api.db import supabase, supabase_execute_with_retry
from agents.orchestrator import orchestrate
from agents.step_logger import log_activity
from memory.long_term import LongTermMemory

# Create one memory client instance for the API process.
memory = LongTermMemory()

try:
    # Use real long-term memory storage when available.
    from memory.long_term import store_preference as _store_preference_impl
except Exception:
    # Fallback stub keeps endpoint behavior working until memory module is fully wired.
    async def _store_preference_impl(user_id: str, key: str, value: Dict[str, Any]) -> None:
        print(f"[PreferenceStub] store_preference called: user_id={user_id}, key={key}, value={value}")

try:
    # Email sending module will be added next step; this import is intentionally optional for now.
    from api.email import send_letter as _send_letter_impl
except Exception:
    async def _send_letter_impl(action_id: str) -> Dict[str, Any]:
        raise RuntimeError("api.email.send_letter is not available yet.")


router = APIRouter(prefix="/actions", tags=["actions"])


# Reject payload: reason is optional so users can reject quickly.
class RejectRequest(BaseModel):
    reason: Optional[str] = None


# Edit payload: only the draft text is editable in this endpoint.
class EditDraftRequest(BaseModel):
    draft_content: str


# Keep severity sorting deterministic for queue prioritization.
def _severity_rank(severity: str) -> int:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return order.get((severity or "").lower(), 4)


# Keep backward compatibility when generated_by column is not migrated yet.
def _resolve_generated_by(row: Dict[str, Any]) -> str:
    explicit = (row.get("generated_by") or "").strip().lower()
    if explicit in {"model", "fallback"}:
        return explicit
    reasoning_text = str(row.get("reasoning") or "")
    summary_text = str(row.get("summary") or "")
    combined = f"{reasoning_text}\n{summary_text}".lower()
    if "fallback action created" in combined or "automatic fallback action" in combined:
        return "fallback"
    return "model"


# Build a deterministic key so repeated analysis can reuse prior results.
# This is duplicated here (instead of importing from api.main) to avoid circular imports.
def _build_analysis_key(payload: dict) -> str:
    # Convert the payload into stable JSON so identical payloads generate identical keys.
    payload_json = json.dumps(payload, sort_keys=True)
    # Hash keeps keys short and fixed-length.
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


# Helper to load one action row with joined document metadata.
async def _get_action_with_document(action_id: str) -> Optional[Dict[str, Any]]:
    def _query() -> Any:
        return (
            supabase.table("actions")
            .select(
                "id, action_type, severity, title, summary, draft_content, reasoning, sources, warnings, "
                "escalate, escalation_reason, generated_by, status, created_at, actioned_at, document_id, "
                "documents(vendor_name, doc_type, domain)"
            )
            .eq("id", action_id)
            .single()
            .execute()
        )

    try:
        result = await supabase_execute_with_retry(_query)
        return result.data
    except APIError as e:
        # Backward-compatible fallback when newer guardrail columns are missing in DB schema.
        if "column actions." in str(e):
            def _query_legacy() -> Any:
                return (
                    supabase.table("actions")
                    .select(
                        "id, action_type, severity, title, summary, draft_content, reasoning, sources, "
                        "status, created_at, actioned_at, document_id, documents(vendor_name, doc_type, domain)"
                    )
                    .eq("id", action_id)
                    .single()
                    .execute()
                )

            legacy_result = await supabase_execute_with_retry(_query_legacy)
            data = legacy_result.data or {}
            data["warnings"] = data.get("warnings", [])
            data["escalate"] = data.get("escalate", False)
            data["escalation_reason"] = data.get("escalation_reason", "")
            data["generated_by"] = _resolve_generated_by(data)
            return data
        raise


# GET /actions — returns pending queue summaries without draft bodies.
@router.get("")
async def list_actions() -> List[Dict[str, Any]]:
    def _query() -> Any:
        return (
            supabase.table("actions")
            .select(
                "id, severity, title, summary, created_at, escalate, generated_by, documents(vendor_name, doc_type, domain)"
            )
            .eq("status", "pending")
            .execute()
        )

    try:
        result = await supabase_execute_with_retry(_query)
    except APIError as e:
        # Backward-compatible fallback when escalate column is not present yet.
        if "column actions." in str(e):
            def _query_legacy() -> Any:
                return (
                    supabase.table("actions")
                    .select("id, severity, title, summary, created_at, documents(vendor_name, doc_type, domain)")
                    .eq("status", "pending")
                    .execute()
                )

            result = await supabase_execute_with_retry(_query_legacy)
        else:
            raise
    rows = result.data or []

    # Sort by severity first, then keep newer items first within each severity band.
    rows.sort(key=lambda row: (_severity_rank(row.get("severity", "")), row.get("created_at", "")), reverse=False)

    summaries: List[Dict[str, Any]] = []
    for row in rows:
        doc = row.get("documents") or {}
        summaries.append(
            {
                "id": row.get("id"),
                "severity": row.get("severity"),
                "title": row.get("title"),
                "summary": row.get("summary"),
                "vendor_name": doc.get("vendor_name"),
                "doc_type": doc.get("doc_type"),
                "created_at": row.get("created_at"),
                "escalate": row.get("escalate", False),
                "generated_by": _resolve_generated_by(row),
            }
        )
    return summaries


# GET /actions/activity — returns autonomous step history with optional document filter.
@router.get("/activity")
async def get_activity(document_id: Optional[str] = None) -> List[Dict[str, Any]]:
    def _query_activity_log() -> Any:
        query = (
            supabase.table("activity_log")
            .select("id, document_id, actor_name, event_type, summary, created_at, event_source, metadata")
            .order("created_at", desc=True)
        )
        if document_id:
            query = query.eq("document_id", document_id)
        return query.execute()

    try:
        result = await supabase_execute_with_retry(_query_activity_log)
        rows = result.data or []
        activity: List[Dict[str, Any]] = []
        for row in rows:
            row_document_id = row.get("document_id")
            event_source = row.get("event_source") or "agent"
            event_type = row.get("event_type") or "event"
            metadata = row.get("metadata") or {}
            tool_called = metadata.get("tool_called") or event_type
            activity.append(
                {
                    "id": row.get("id"),
                    "document_id": row_document_id,
                    "agent_name": row.get("actor_name"),
                    "tool_called": tool_called,
                    "summary": row.get("summary"),
                    "created_at": row.get("created_at"),
                    "link": f"/vault?doc={row_document_id}" if row_document_id else None,
                    "is_autonomous": event_source == "agent",
                }
            )
        return activity
    except Exception:
        # Backward-compatible fallback to old agent_steps stream if activity_log isn't migrated yet.
        def _query_agent_steps() -> Any:
            query = (
                supabase.table("agent_steps")
                .select("id, document_id, agent_name, tool_called, summary, created_at")
                .order("created_at", desc=True)
            )
            if document_id:
                query = query.eq("document_id", document_id)
            return query.execute()

        result = await supabase_execute_with_retry(_query_agent_steps)
        rows = result.data or []

        activity: List[Dict[str, Any]] = []
        for row in rows:
            row_document_id = row.get("document_id")
            activity.append(
                {
                    "id": row.get("id"),
                    "document_id": row_document_id,
                    "agent_name": row.get("agent_name"),
                    "tool_called": row.get("tool_called"),
                    "summary": row.get("summary"),
                    "created_at": row.get("created_at"),
                    "link": f"/vault?doc={row_document_id}" if row_document_id else None,
                    "is_autonomous": row.get("tool_called") != "create_action_item",
                }
            )
        return activity


# GET /actions/{action_id} — returns full queue item details for one action.
@router.get("/{action_id}")
async def get_action(action_id: str) -> Dict[str, Any]:
    row = await _get_action_with_document(action_id)
    if not row:
        raise HTTPException(status_code=404, detail="Action not found")

    doc = row.get("documents") or {}
    return {
        "id": row.get("id"),
        # document_id is required for “Continue analysis” so the UI can resume orchestration safely.
        "document_id": row.get("document_id"),
        "severity": row.get("severity"),
        "title": row.get("title"),
        "summary": row.get("summary"),
        "draft_content": row.get("draft_content"),
        "reasoning": row.get("reasoning"),
        "sources": row.get("sources", []),
        "warnings": row.get("warnings", []),
        "escalate": row.get("escalate", False),
        "escalation_reason": row.get("escalation_reason", ""),
        "generated_by": _resolve_generated_by(row),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "vendor_name": doc.get("vendor_name"),
        "doc_type": doc.get("doc_type"),
    }


# POST /actions/{action_id}/continue — resumes analysis for a fallback/incomplete action.
@router.post("/{action_id}/continue")
async def continue_action(action_id: str, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """
    Resume the analysis process for an existing pending action.
    Takes: action_id (string).
    Returns: a small status payload immediately; analysis runs in the background.
    """
    action = await _get_action_with_document(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if (action.get("status") or "").strip().lower() != "pending":
        raise HTTPException(status_code=403, detail="Only pending actions can be continued")

    document_id = action.get("document_id")
    if not document_id:
        raise HTTPException(status_code=400, detail="Action is missing document_id")

    # Try to reuse a previously completed analysis snapshot when available (idempotency-friendly).
    # This avoids re-calling the LLM if the analysis already succeeded earlier.
    try:
        doc = (action.get("documents") or {}) if isinstance(action.get("documents"), dict) else {}
        trigger = {
            "document_id": document_id,
            "vendor_name": doc.get("vendor_name") or "Unknown vendor",
            "document_type": doc.get("doc_type") or "contract",
            "domain": (doc.get("domain") or "subscription") if isinstance(doc, dict) else "subscription",
        }
        analysis_key = _build_analysis_key({"trigger": trigger, "orchestrator_version": "day2-v2"})

        def _lookup_run() -> Any:
            return (
                supabase.table("analysis_runs")
                .select("id, status, result_snapshot")
                .eq("analysis_key", analysis_key)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

        run_lookup = await supabase_execute_with_retry(_lookup_run)
        run_rows = run_lookup.data or []
        if run_rows:
            latest = run_rows[0] or {}
            if latest.get("status") == "completed" and latest.get("result_snapshot"):
                snapshot = latest.get("result_snapshot") or {}
                action_item = snapshot.get("action_item") if isinstance(snapshot, dict) else None
                if isinstance(action_item, dict):
                    # Overwrite this existing action so the user only sees ONE queue item (per your UI preference).
                    updates = {
                        "severity": action_item.get("severity"),
                        "title": action_item.get("title"),
                        "summary": action_item.get("summary"),
                        "draft_content": action_item.get("draft_content"),
                        "reasoning": action_item.get("reasoning"),
                        "sources": action_item.get("sources", []),
                        "warnings": action_item.get("warnings", []),
                        "generated_by": action_item.get("generated_by", "model"),
                    }

                    def _update_action() -> Any:
                        return (
                            supabase.table("actions")
                            .update(updates)
                            .eq("id", action_id)
                            .execute()
                        )

                    await supabase_execute_with_retry(_update_action)
                    log_activity(
                        document_id=document_id,
                        action_id=action_id,
                        event_source="system",
                        event_type="analysis_continued_from_cache",
                        actor_name="api",
                        summary=f"Action {action_id} updated from cached analysis snapshot.",
                        metadata={"analysis_run_id": latest.get("id")},
                    )
                    return {"status": "updated_from_cache", "action_id": action_id, "analysis_run_id": latest.get("id")}
    except Exception as cache_error:
        # Cache is best-effort; if the table/migration does not exist, we still allow a rerun.
        print(f"[/actions/{action_id}/continue] cache lookup skipped: {cache_error}")

    # If no cache is available, queue a fresh orchestration run and overwrite this action when done.
    # We pass existing_action_id so create_action_item can update the existing row.
    trigger = {
        "document_id": document_id,
        "vendor_name": (action.get("documents") or {}).get("vendor_name") or "Unknown vendor",
        "document_type": (action.get("documents") or {}).get("doc_type") or "contract",
        "domain": "subscription",
        "existing_action_id": action_id,
        "note": "User requested continue analysis from Action Queue",
    }
    background_tasks.add_task(orchestrate, trigger, "dev")
    log_activity(
        document_id=document_id,
        action_id=action_id,
        event_source="user",
        event_type="analysis_continue_requested",
        actor_name="user",
        summary=f"User requested continue analysis for action {action_id}.",
        metadata={"trigger": {"existing_action_id": action_id}},
    )
    return {"status": "queued", "action_id": action_id}


# PUT /actions/{action_id}/approve — marks action approved and records action time.
@router.put("/{action_id}/approve")
async def approve_action(action_id: str) -> Dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat()

    def _update() -> Any:
        return (
            supabase.table("actions")
            .update({"status": "approved", "actioned_at": timestamp})
            .eq("id", action_id)
            .execute()
        )

    updated = await asyncio.to_thread(_update)
    rows = updated.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Action not found")

    # Fetch vendor name + action_type for memory logging (join through documents table).
    action_row = await _get_action_with_document(action_id)
    doc = (action_row or {}).get("documents") or {}
    vendor_name = doc.get("vendor_name") or "Unknown vendor"
    action_type = (action_row or {}).get("action_type") or "review"

    # Store a small preference event for future personalization.
    await _store_preference_impl(
        user_id="dev",
        key="action_decision",
        value={"action_id": action_id, "decision": "approved"},
    )

    # Fire-and-forget memory writes so a memory failure never breaks approvals.
    try:
        asyncio.create_task(
            memory.store_user_preference(
                user_id="dev",
                context=f"{vendor_name} {action_type}",
                decision="approved",
                outcome="user approved the proposed action",
            )
        )
        asyncio.create_task(
            memory.store_vendor_observation(
                user_id="dev",
                vendor=vendor_name,
                observation={
                    "event": "action_approved",
                    "action_type": action_type,
                    "date": datetime.now().isoformat(),
                },
            )
        )
    except Exception as memory_error:
        print(f"[Memory] approve_action enqueue failed: {memory_error}")

    log_activity(
        document_id=rows[0].get("document_id"),
        action_id=action_id,
        event_source="user",
        event_type="approve_action",
        actor_name="user",
        summary=f"User approved action {action_id}.",
        metadata={"status": "approved"},
    )
    return rows[0]


# PUT /actions/{action_id}/reject — rejects action and optionally stores human reason.
@router.put("/{action_id}/reject")
async def reject_action(action_id: str, body: Optional[RejectRequest] = None) -> Dict[str, Any]:
    existing = await _get_action_with_document(action_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Action not found")

    reason = (body.reason.strip() if body and body.reason else "")
    new_reasoning = existing.get("reasoning") or ""
    if reason:
        new_reasoning = f"{new_reasoning}\n\nRejection reason: {reason}".strip()

    timestamp = datetime.now(timezone.utc).isoformat()

    def _update() -> Any:
        return (
            supabase.table("actions")
            .update({"status": "rejected", "actioned_at": timestamp, "reasoning": new_reasoning})
            .eq("id", action_id)
            .execute()
        )

    updated = await asyncio.to_thread(_update)
    rows = updated.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Action not found")

    # Fetch vendor name + action_type for memory logging (join through documents table).
    doc = (existing or {}).get("documents") or {}
    vendor_name = doc.get("vendor_name") or "Unknown vendor"
    action_type = (existing or {}).get("action_type") or "review"

    await _store_preference_impl(
        user_id="dev",
        key="action_decision",
        value={"action_id": action_id, "decision": "rejected", "reason": reason},
    )

    # Fire-and-forget memory writes so a memory failure never breaks rejections.
    try:
        asyncio.create_task(
            memory.store_user_preference(
                user_id="dev",
                context=f"{vendor_name} {action_type}",
                decision="rejected",
                outcome=f"user rejected the proposed action. reason: {reason}",
            )
        )
        asyncio.create_task(
            memory.store_vendor_observation(
                user_id="dev",
                vendor=vendor_name,
                observation={
                    "event": "action_rejected",
                    "action_type": action_type,
                    "date": datetime.now().isoformat(),
                },
            )
        )
    except Exception as memory_error:
        print(f"[Memory] reject_action enqueue failed: {memory_error}")

    log_activity(
        document_id=rows[0].get("document_id"),
        action_id=action_id,
        event_source="user",
        event_type="reject_action",
        actor_name="user",
        summary=f"User rejected action {action_id}.",
        metadata={"status": "rejected", "reason": reason},
    )
    return rows[0]


# PUT /actions/{action_id}/edit — lets user edit draft text while keeping pending status.
@router.put("/{action_id}/edit")
async def edit_action_draft(action_id: str, body: EditDraftRequest) -> Dict[str, Any]:
    def _update() -> Any:
        return (
            supabase.table("actions")
            .update({"draft_content": body.draft_content})
            .eq("id", action_id)
            .execute()
        )

    updated = await asyncio.to_thread(_update)
    rows = updated.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Action not found")
    log_activity(
        document_id=rows[0].get("document_id"),
        action_id=action_id,
        event_source="user",
        event_type="edit_draft",
        actor_name="user",
        summary=f"User edited draft for action {action_id}.",
        metadata={"draft_length": len(body.draft_content or "")},
    )
    return rows[0]


# POST /actions/{action_id}/send — sends only approved actions.
@router.post("/{action_id}/send")
async def send_action(action_id: str) -> Dict[str, Any]:
    action = await _get_action_with_document(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action.get("status") != "approved":
        raise HTTPException(status_code=403, detail="Action must be approved before sending")

    try:
        # Sending is delegated to the email module; any error keeps DB state unchanged.
        await _send_letter_impl(action_id)
    except Exception as send_error:
        return {"status": "error", "detail": str(send_error)}

    timestamp = datetime.now(timezone.utc).isoformat()

    def _mark_sent() -> Any:
        return (
            supabase.table("actions")
            .update({"status": "sent", "actioned_at": timestamp})
            .eq("id", action_id)
            .execute()
        )

    await asyncio.to_thread(_mark_sent)

    # Fire-and-forget outcome memory write after a successful send.
    try:
        asyncio.create_task(
            memory.store_outcome(
                user_id="dev",
                action_id=action_id,
                result="letter_sent",
                financial_impact=0.0,
            )
        )
    except Exception as memory_error:
        print(f"[Memory] send_action enqueue failed: {memory_error}")

    log_activity(
        document_id=action.get("document_id"),
        action_id=action_id,
        event_source="user",
        event_type="send_action",
        actor_name="user",
        summary=f"User sent approved action {action_id}.",
        metadata={"status": "sent"},
    )
    return {"status": "sent", "action_id": action_id}
