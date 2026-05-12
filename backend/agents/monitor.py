# monitor.py — scheduled “background brain” for Sentinel.AI
# This file runs periodic checks (Drive sync, deadlines, stale actions) to trigger analysis without user prompting.

import asyncio
from datetime import datetime, timezone, timedelta

from api.db import supabase
from mcp.google_drive import list_new_documents, fetch_document, set_last_sync_time, get_last_sync_time
from rag.ingestion import ingest_document
from agents.orchestrator import orchestrate


async def get_obligations_due_within_days(user_id: str, days: int) -> list:
    """
    Find obligations that are due soon so the agent can warn the user early.
    Takes: user_id (string) and days (int).
    Returns: a list of obligation dicts (each includes document details when available).
    """
    try:
        # Calculate the latest due date we care about (today + N days).
        cutoff_date = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()

        # Supabase client is synchronous, so run it in a thread to avoid blocking the server.
        def _query():
            return (
                supabase.table("obligations")
                .select("id, document_id, due_date, description, status")
                .not_.is_("due_date", "null")
                .lte("due_date", cutoff_date)
                .eq("status", "pending")
                .execute()
            )

        result = await asyncio.to_thread(_query)
        obligations = getattr(result, "data", None) or []
        if not obligations:
            return []

        # Load the related documents and filter to this user.
        doc_ids = list({o.get("document_id") for o in obligations if o.get("document_id")})
        if not doc_ids:
            return []

        def _docs():
            return (
                supabase.table("documents")
                .select("id, user_id, vendor_name, doc_type, domain")
                .in_("id", doc_ids)
                .execute()
            )

        doc_result = await asyncio.to_thread(_docs)
        docs = getattr(doc_result, "data", None) or []
        docs_by_id = {d.get("id"): d for d in docs if isinstance(d, dict)}

        # Merge document details into each obligation record so downstream code is simpler.
        merged = []
        for o in obligations:
            doc = docs_by_id.get(o.get("document_id")) or {}
            if doc.get("user_id") != user_id:
                continue
            merged.append(
                {
                    **o,
                    "vendor_name": doc.get("vendor_name"),
                    "doc_type": doc.get("doc_type"),
                    "domain": doc.get("domain"),
                }
            )
        return merged
    except Exception as e:
        # If something goes wrong, return empty list so the monitor keeps running.
        print(f"[Monitor] get_obligations_due_within_days failed: {e}")
        return []


async def obligation_has_pending_action(obligation_id: str) -> bool:
    """
    Check whether we already created a pending action for the obligation’s document.
    Takes: obligation_id (string).
    Returns: True if a pending action exists, otherwise False.
    """
    try:
        # First fetch the obligation so we know which document it belongs to.
        def _get_obligation():
            return (
                supabase.table("obligations")
                .select("id, document_id")
                .eq("id", obligation_id)
                .single()
                .execute()
            )

        o_res = await asyncio.to_thread(_get_obligation)
        obligation = getattr(o_res, "data", None) or {}
        doc_id = obligation.get("document_id")
        if not doc_id:
            return False

        # Then check if there is any pending action for that document.
        def _query_actions():
            return (
                supabase.table("actions")
                .select("id")
                .eq("document_id", doc_id)
                .eq("status", "pending")
                .limit(1)
                .execute()
            )

        a_res = await asyncio.to_thread(_query_actions)
        rows = getattr(a_res, "data", None) or []
        return len(rows) > 0
    except Exception as e:
        print(f"[Monitor] obligation_has_pending_action failed: {e}")
        return False


async def create_deadline_alert(obligation: dict, user_id: str) -> None:
    """
    Trigger analysis for a document when a deadline is approaching.
    Takes: obligation (dict) and user_id (string).
    Returns: None.
    """
    # Build a trigger in the same shape the orchestrator expects.
    trigger = {
        "document_id": obligation.get("document_id"),
        "vendor_name": obligation.get("vendor_name") or "Unknown vendor",
        "document_type": obligation.get("doc_type") or "contract",
        "domain": obligation.get("domain") or "subscription",
        "note": f"Deadline approaching: {obligation.get('description')} due {obligation.get('due_date')}",
    }

    # Ask the orchestrator to create an action item for the user to review.
    await orchestrate(trigger, user_id)
    print(f"[Monitor] Created deadline alert for {obligation.get('description', 'unknown')}")


async def get_stale_actions(user_id: str, days: int) -> list:
    """
    Find actions that have been pending for too long so we can increase urgency.
    Takes: user_id (string) and days (int).
    Returns: a list of action dicts.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Pull pending actions.
        def _query():
            return (
                supabase.table("actions")
                .select("id, document_id, title, severity, created_at")
                .eq("status", "pending")
                .lt("created_at", cutoff)
                .execute()
            )

        result = await asyncio.to_thread(_query)
        actions = getattr(result, "data", None) or []
        if not actions:
            return []

        # Filter to the user by looking up each action's document owner.
        doc_ids = list({a.get("document_id") for a in actions if a.get("document_id")})
        if not doc_ids:
            return []

        def _docs():
            return (
                supabase.table("documents")
                .select("id, user_id")
                .in_("id", doc_ids)
                .execute()
            )

        doc_result = await asyncio.to_thread(_docs)
        docs = getattr(doc_result, "data", None) or []
        allowed_doc_ids = {d.get("id") for d in docs if d.get("user_id") == user_id}

        return [a for a in actions if a.get("document_id") in allowed_doc_ids]
    except Exception as e:
        print(f"[Monitor] get_stale_actions failed: {e}")
        return []


async def escalate_urgency(action: dict) -> None:
    """
    Increase action severity if the user hasn’t responded in time.
    Takes: action (dict).
    Returns: None.
    """
    try:
        current = (action.get("severity") or "").lower().strip()
        new_sev = current

        # Escalate one step up.
        if current == "medium":
            new_sev = "high"
        elif current == "high":
            new_sev = "critical"

        # If there is nothing to change, do nothing.
        if not action.get("id") or new_sev == current or not new_sev:
            return

        def _update():
            return (
                supabase.table("actions")
                .update({"severity": new_sev})
                .eq("id", action["id"])
                .execute()
            )

        await asyncio.to_thread(_update)
        print(f"[Monitor] Escalated urgency for action: {action.get('title', action['id'])}")
    except Exception as e:
        print(f"[Monitor] escalate_urgency failed: {e}")


async def run_monitoring_cycle(user_id: str = "dev") -> dict:
    """
    Runs one full monitoring cycle. Called by the scheduler every 6 hours.
    Returns a summary dict of what was done.
    """
    summary = {"drive_synced": 0, "deadline_alerts": 0, "escalated": 0, "errors": []}
    print(f"\n[Monitor] Starting monitoring cycle for user: {user_id}")

    # Step 1 — Drive sync (ingest new docs).
    try:
        last_sync = await get_last_sync_time(user_id)
        new_files = await list_new_documents(since=last_sync)
        for file in new_files:
            file_bytes = await fetch_document(file["id"], file["name"])
            if file_bytes:
                # Use Drive file id as a stable upstream fingerprint for dedupe.
                await ingest_document(
                    file_bytes,
                    file["name"],
                    user_id,
                    source_fingerprint=f"gdrive:{file.get('id')}",
                )
                summary["drive_synced"] += 1
        await set_last_sync_time(user_id)
        print(f"[Monitor] Drive sync: {summary['drive_synced']} new documents")
    except Exception as e:
        summary["errors"].append(f"drive_sync: {e}")

    # Step 2 — Deadline alerts (create actions for due obligations).
    try:
        upcoming = await get_obligations_due_within_days(user_id, days=30)
        for obligation in upcoming:
            has_action = await obligation_has_pending_action(obligation.get("id"))
            if not has_action:
                await create_deadline_alert(obligation, user_id)
                summary["deadline_alerts"] += 1
        print(f"[Monitor] Deadline alerts: {summary['deadline_alerts']} created")
    except Exception as e:
        summary["errors"].append(f"deadline_alerts: {e}")

    # Step 3 — Stale action escalation (nudge severity upward).
    try:
        stale = await get_stale_actions(user_id, days=7)
        for action in stale:
            await escalate_urgency(action)
            summary["escalated"] += 1
        print(f"[Monitor] Escalated: {summary['escalated']} stale actions")
    except Exception as e:
        summary["errors"].append(f"stale_escalation: {e}")

    # Step 4 — Memory consolidation (stub for now).
    print("[Monitor] Memory consolidation: skipped (not yet implemented)")

    print(f"[Monitor] Cycle complete. Summary: {summary}")
    return summary

