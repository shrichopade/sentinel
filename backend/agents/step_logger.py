# step_logger.py — writes each orchestrator tool call to the database for the Activity Log (Day 3 UI).
# Failures here are swallowed so a logging bug never stops the main agent loop.

from api.db import supabase
from datetime import datetime, timezone


def log_activity(
    document_id: str | None,
    event_source: str,
    event_type: str,
    actor_name: str,
    summary: str,
    user_id: str = "dev",
    action_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Writes one normalized activity event (agent/user/system) to the activity_log table.
    Must never raise — activity logging should never break product flows.
    """
    try:
        row = {
            "document_id": document_id,
            "action_id": action_id,
            "user_id": user_id,
            "event_source": event_source,
            "event_type": event_type,
            "actor_name": actor_name,
            "summary": (summary or "")[:1000],
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("activity_log").insert(row).execute()
    except Exception as e:
        print(f"[Logger] log_activity failed: {e}")


def log_step(document_id: str, tool_name: str, summary: str, user_id: str = "dev") -> None:
    """
    Writes a single orchestrator step to the agent_steps table.
    Must never raise — a logging failure must not crash the orchestrator.
    """
    try:
        # Build one row describing what just happened (who, which document, which tool, short text)
        row = {
            "document_id": document_id,
            "user_id": user_id,
            "agent_name": "orchestrator",
            "tool_called": tool_name,
            "summary": (summary or "")[:500],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Supabase Python client is synchronous — no await
        supabase.table("agent_steps").insert(row).execute()
        # Mirror agent steps to the unified activity log stream for UI timeline consistency.
        log_activity(
            document_id=document_id,
            event_source="agent",
            event_type="tool_call",
            actor_name="orchestrator",
            summary=summary,
            user_id=user_id,
            metadata={"tool_called": tool_name},
        )

    except Exception as e:
        # Never let logging break the pipeline — print and continue
        print(f"[Logger] log_step failed: {e}")
