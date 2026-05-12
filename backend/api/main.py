# sentinel.ai backend — main application entry point
# Run with: uvicorn api.main:app --reload
# Must be run from the sentinel.ai/backend/ directory with venv active

import asyncio
import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.db import supabase, supabase_execute_with_retry
from api.actions import router as actions_router
from agents.monitor import run_monitoring_cycle
from agents.step_logger import log_activity
from agents.orchestrator import orchestrate
from integrations.google_drive import list_new_documents, fetch_document, get_last_sync_time, set_last_sync_time
from integrations.google_drive import DRIVE_RESTRICT_FOLDER_ID
from rag.ingestion import ingest_document
from rag.chat import chat as chat_fn
from rag.regulatory import seed_regulatory_corpus

app = FastAPI()

scheduler = AsyncIOScheduler()

# Simple in-memory conversation store for chat sessions.
# NOTE: This resets whenever the backend restarts (intended for local dev only).
conversation_store: dict = {}


# Build a deterministic key so repeated analyse calls for the same input can reuse prior results.
def build_analysis_key(payload: dict) -> str:
    payload_json = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(actions_router)

@app.on_event("startup")
async def startup_event():
    scheduler.add_job(
        run_monitoring_cycle,
        trigger="interval",
        hours=6,
        id="monitoring_loop",
        replace_existing=True,
        kwargs={"user_id": "dev"}
    )
    scheduler.start()
    print("[Scheduler] Monitoring loop started — runs every 6 hours")


@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown()
    print("[Scheduler] Monitoring loop stopped")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    # Read the uploaded file's raw bytes into memory
    contents = await file.read()

    # Run the full ingestion pipeline: extract → classify → chunk → embed → store
    result = await ingest_document(contents, file.filename)
    log_activity(
        document_id=result.get("doc_id"),
        action_id=None,
        event_source="user",
        event_type="upload_document",
        actor_name="user",
        summary=f"User uploaded document {file.filename}.",
        metadata={"filename": file.filename, "is_duplicate": bool(result.get("is_duplicate"))},
    )

    if result.get("doc_id"):
        def _fetch_doc():
            return (
                supabase.table("documents")
                .select("id, vendor_name, doc_type, domain")
                .eq("id", result["doc_id"])
                .single()
                .execute()
            )

        doc_result = await asyncio.to_thread(_fetch_doc)
        doc = doc_result.data or {}
        trigger = {
            "document_id": doc.get("id") or result["doc_id"],
            "vendor_name": doc.get("vendor_name") or "Unknown vendor",
            "document_type": doc.get("doc_type") or "contract",
            "domain": doc.get("domain") or "subscription",
        }
        # Always queue analysis in the background after a successful ingest.
        # FastAPI gives us a BackgroundTasks object, so this is safe for local dev.
        background_tasks.add_task(orchestrate, trigger)
        result["analysis_triggered"] = True
        log_activity(
            document_id=result.get("doc_id"),
            action_id=None,
            event_source="system",
            event_type="analysis_queued",
            actor_name="api",
            summary=f"Analysis queued after ingest for document {result.get('doc_id')}.",
            metadata={"trigger": trigger},
        )

    # Return the summary (doc_id, metadata, chunk_count, obligation_count)
    return result


# Pydantic model — defines the expected shape of the JSON body for the /chat endpoint
class ChatRequest(BaseModel):
    message: str  # the user's question, e.g. "When does my contract expire?"
    session_id: Optional[str] = "default"  # optional chat session key for simple history


@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    # Record that the user initiated a chat request so audit history includes conversational actions.
    log_activity(
        document_id=None,
        action_id=None,
        event_source="user",
        event_type="chat_prompt",
        actor_name="user",
        summary="User asked a chat question.",
        metadata={"message_length": len((req.message or "").strip())},
    )

    # Pass the user's message to the chat function which retrieves context and asks Claude.
    # Wrap in try/except so transient upstream issues return a clean error to the UI.
    try:
        response = await chat_fn(req.message)
    except Exception as e:
        # Return a readable error to the frontend instead of a blank/broken response.
        return JSONResponse(status_code=500, content={"detail": f"Chat failed: {e}"})

    # Store this exchange in memory so the frontend can load/clear chat history by session id.
    session_id = (req.session_id or "default").strip() or "default"
    conversation_store.setdefault(session_id, [])
    conversation_store[session_id].append({"role": "user", "text": req.message})
    if isinstance(response, dict):
        conversation_store[session_id].append({"role": "assistant", "text": response.get("answer", "")})

    # Record completion metadata (not full message text) to keep logs lightweight and privacy-aware.
    answer_text = ""
    if isinstance(response, dict):
        answer_text = str(response.get("answer") or response.get("content") or "")
    log_activity(
        document_id=None,
        action_id=None,
        event_source="system",
        event_type="chat_response",
        actor_name="api",
        summary="System returned a chat response.",
        metadata={"answer_length": len(answer_text)},
    )
    return response


@app.get("/chat/history/{session_id}")
def get_chat_history(session_id: str):
    return {"messages": conversation_store.get(session_id, [])}


@app.delete("/chat/history/{session_id}")
def clear_chat_history(session_id: str):
    conversation_store.pop(session_id, None)
    return {"cleared": True}


@app.get("/documents/{doc_id}")
def get_document(doc_id: str):
    # Fetch a single document record by its UUID — used by the frontend to display the filename
    from api.db import supabase
    result = supabase.table("documents").select("id, filename, vendor_name, domain, status").eq("id", doc_id).single().execute()
    return result.data


# Return all documents so the vault table can show what is currently stored.
@app.get("/documents")
def list_documents():
    # Pull core classification fields needed for the frontend table.
    result = (
        supabase.table("documents")
        .select(
            "id, filename, vendor_name, doc_type, domain, effective_date, expiry_date, jurisdiction, "
            "summary, flagged_clause_count, obligation_count, created_at"
        )
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


@app.get("/dashboard/summary")
async def dashboard_summary(user_id: str = "dev"):
    """
    Returns aggregated compliance data for the Dashboard UI.
    Queries documents, obligations, and actions tables.
    """
    # Build a safe default response so we can return partial data even if a query fails.
    summary = {
        "domains": [],
        "actions_summary": {"total_pending": 0, "critical": 0, "high": 0, "medium": 0, "low": 0},
        "upcoming_obligations": [],
        "financial_exposure_gbp": 0.0,
        "total_documents": 0,
        # The last time we successfully synced Google Drive (read from the memory table).
        "last_drive_sync_at": None,
        # The next time our scheduled monitoring loop will run (APScheduler).
        "next_scheduled_sync_at": None,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    # We always return partial data instead of throwing a 500.
    try:
        # Step 0 — Sync status (Drive sync + scheduler).
        try:
            # Last Drive sync time is stored in the `memory` table by the Drive integration.
            last_sync = await get_last_sync_time(user_id)
            summary["last_drive_sync_at"] = last_sync.astimezone(timezone.utc).isoformat()
        except Exception as e:
            # Keep partial response if Drive sync status fails.
            print(f"[Dashboard] Step 0 last_drive_sync_at failed: {e}")

        try:
            # APScheduler keeps track of the next time each job will run.
            job = scheduler.get_job("monitoring_loop")
            next_run = getattr(job, "next_run_time", None) if job else None
            if next_run:
                # next_run_time is a datetime, so we can return an ISO string to the UI.
                summary["next_scheduled_sync_at"] = next_run.isoformat()
            else:
                # Fallback: if the scheduler job is missing (or hasn't computed next_run_time yet),
                # show "about 6 hours from now" so the UI isn't blank.
                summary["next_scheduled_sync_at"] = (
                    datetime.now(timezone.utc) + timedelta(hours=6)
                ).isoformat()
        except Exception as e:
            # Keep partial response if scheduler status fails.
            print(f"[Dashboard] Step 0 next_scheduled_sync_at failed: {e}")

        # Step 1 — Documents by domain.
        try:
            def _docs():
                return (
                    supabase.table("documents")
                    .select("id, user_id, domain, doc_type, vendor_name, expiry_date, risk_score, status")
                    .eq("user_id", user_id)
                    .execute()
                )

            docs_res = await asyncio.to_thread(_docs)
            docs = getattr(docs_res, "data", None) or []
            summary["total_documents"] = len(docs)

            # We always return these five domains for a stable UI.
            all_domains = ["subscription", "employment", "tax", "gdpr", "housing"]
            grouped = {d: [] for d in all_domains}
            for doc in docs:
                domain = (doc.get("domain") or "").strip()
                if domain in grouped:
                    grouped[domain].append(doc)

            domains_out = []
            for domain in all_domains:
                domain_docs = grouped.get(domain) or []

                # Calculate average risk using only documents that have a numeric risk_score.
                risks = []
                for d in domain_docs:
                    r = d.get("risk_score")
                    if isinstance(r, (int, float)):
                        risks.append(float(r))
                avg_risk = (sum(risks) / len(risks)) if risks else None

                # Status logic: red if any >= 7, amber if any >= 4, green otherwise.
                status = "green"
                for d in domain_docs:
                    r = d.get("risk_score")
                    if isinstance(r, (int, float)) and r >= 7:
                        status = "red"
                        break
                if status != "red":
                    for d in domain_docs:
                        r = d.get("risk_score")
                        if isinstance(r, (int, float)) and r >= 4:
                            status = "amber"
                            break

                domains_out.append({"domain": domain, "doc_count": len(domain_docs), "avg_risk": avg_risk, "status": status})

            summary["domains"] = domains_out
        except Exception as e:
            # Keep partial response if this section fails.
            print(f"[Dashboard] Step 1 failed: {e}")

        # Step 2 — Open actions count and severity breakdown.
        try:
            def _actions():
                # Note: Spec does not filter by user_id (actions table has no user_id column in our schema).
                return supabase.table("actions").select("severity, status").execute()

            actions_res = await asyncio.to_thread(_actions)
            actions = getattr(actions_res, "data", None) or []

            pending = [a for a in actions if (a.get("status") or "").strip().lower() == "pending"]
            counts = {"total_pending": len(pending), "critical": 0, "high": 0, "medium": 0, "low": 0}
            for a in pending:
                sev = (a.get("severity") or "").strip().lower()
                if sev in counts:
                    counts[sev] += 1
            summary["actions_summary"] = counts
        except Exception as e:
            print(f"[Dashboard] Step 2 failed: {e}")

        # Step 3 — Upcoming obligations (next 90 days).
        try:
            cutoff_date = (datetime.now(timezone.utc) + timedelta(days=90)).date().isoformat()

            def _obligations():
                return (
                    supabase.table("obligations")
                    .select("id, description, due_date, obligation_type, financial_amount, currency, document_id, status")
                    .execute()
                )

            obl_res = await asyncio.to_thread(_obligations)
            obligations = getattr(obl_res, "data", None) or []

            # Filter to obligations due within 90 days.
            upcoming = []
            for o in obligations:
                due = o.get("due_date")
                if not due:
                    continue
                # Due dates are stored as YYYY-MM-DD, so string compare is safe for ordering/filtering.
                if str(due) <= cutoff_date:
                    upcoming.append(o)

            # Sort by due date (earliest first).
            upcoming.sort(key=lambda o: str(o.get("due_date") or "9999-12-31"))

            # Join documents to get vendor_name and also filter results to this user.
            doc_ids = list({o.get("document_id") for o in upcoming if o.get("document_id")})
            docs_by_id = {}
            if doc_ids:
                def _docs_for_obligations():
                    return (
                        supabase.table("documents")
                        .select("id, user_id, vendor_name")
                        .in_("id", doc_ids)
                        .execute()
                    )

                docs_res = await asyncio.to_thread(_docs_for_obligations)
                docs = getattr(docs_res, "data", None) or []
                docs_by_id = {d.get("id"): d for d in docs if isinstance(d, dict)}

            items = []
            for o in upcoming:
                doc = docs_by_id.get(o.get("document_id")) or {}
                if doc and doc.get("user_id") != user_id:
                    continue
                items.append(
                    {
                        "description": o.get("description"),
                        "due_date": o.get("due_date"),
                        "obligation_type": o.get("obligation_type"),
                        "financial_amount": o.get("financial_amount"),
                        "currency": o.get("currency"),
                        "document_id": o.get("document_id"),
                        "vendor_name": doc.get("vendor_name"),
                    }
                )
                if len(items) >= 20:
                    break

            summary["upcoming_obligations"] = items
        except Exception as e:
            print(f"[Dashboard] Step 3 failed: {e}")

        # Step 4 — Financial exposure.
        try:
            exposure = 0.0

            # Sum pending obligation amounts.
            try:
                def _pending_amounts():
                    return (
                        supabase.table("obligations")
                        .select("financial_amount, status")
                        .execute()
                    )

                pa_res = await asyncio.to_thread(_pending_amounts)
                rows = getattr(pa_res, "data", None) or []
                for r in rows:
                    if (r.get("status") or "").strip().lower() != "pending":
                        continue
                    amt = r.get("financial_amount")
                    if isinstance(amt, (int, float)):
                        exposure += float(amt)
            except Exception as e:
                print(f"[Dashboard] Step 4 obligations sum failed: {e}")

            # Sum high/critical open action amounts (if the column exists).
            try:
                def _action_amounts():
                    return supabase.table("actions").select("severity, status, financial_amount").execute()

                aa_res = await asyncio.to_thread(_action_amounts)
                rows = getattr(aa_res, "data", None) or []
                for r in rows:
                    if (r.get("status") or "").strip().lower() != "pending":
                        continue
                    sev = (r.get("severity") or "").strip().lower()
                    if sev not in {"high", "critical"}:
                        continue
                    amt = r.get("financial_amount")
                    if isinstance(amt, (int, float)):
                        exposure += float(amt)
            except Exception as e:
                # Most likely the `actions.financial_amount` column does not exist yet.
                print(f"[Dashboard] Step 4 actions sum skipped: {e}")

            summary["financial_exposure_gbp"] = float(exposure)
        except Exception as e:
            print(f"[Dashboard] Step 4 failed: {e}")

    except Exception as e:
        # Catch-all so the endpoint never throws a 500.
        print(f"[Dashboard] dashboard_summary failed: {e}")

    # Step 5 — Return combined response.
    summary["last_updated"] = datetime.now(timezone.utc).isoformat()
    return summary


@app.post("/analyse")
async def analyse(document_id: str):
    """
    Triggers the orchestrator for a document that has already been ingested.
    Call this after /ingest returns a doc_id.
    """
    # Step 1 — load metadata from Supabase (thread pool: Supabase client is synchronous)
    def _fetch_doc():
        return (
            supabase.table("documents")
            .select("id, vendor_name, doc_type, domain")
            .eq("id", document_id)
            .single()
            .execute()
        )

    result = await supabase_execute_with_retry(_fetch_doc)
    doc = result.data
    if not doc:
        return JSONResponse(content={"error": "Document not found"}, status_code=404)

    # Step 2 — bundle the fields the orchestrator expects on every run
    trigger = {
        "document_id": doc["id"],
        "vendor_name": doc.get("vendor_name") or "Unknown vendor",
        "document_type": doc.get("doc_type") or "contract",
        "domain": doc.get("domain") or "subscription",
    }
    # Build a stable idempotency key so this exact request can be reused instead of rerun.
    # Bump orchestrator version whenever analysis semantics change so stale cached runs are not reused.
    analysis_key = build_analysis_key({"trigger": trigger, "orchestrator_version": "day2-v2"})

    # Optional idempotency lookup — returns cached run when the same analysis already finished.
    run_row = None
    try:
        def _lookup_run():
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
            latest = run_rows[0]
            if latest.get("status") == "running":
                return JSONResponse(
                    content={"status": "already_running", "run_id": latest.get("id")},
                    status_code=202,
                )
            if latest.get("status") == "completed" and latest.get("result_snapshot"):
                cached = latest["result_snapshot"]
                cached["idempotent_hit"] = True
                cached["analysis_run_id"] = latest.get("id")
                return cached
    except Exception as run_lookup_error:
        # Keep endpoint working if migration for analysis_runs is not applied yet.
        print(f"[/analyse] analysis_runs lookup skipped: {run_lookup_error}")

    # Create an analysis run row when supported so duplicate API calls can be deduped.
    try:
        def _create_run():
            return (
                supabase.table("analysis_runs")
                .insert(
                    {
                        "document_id": document_id,
                        "analysis_key": analysis_key,
                        "status": "running",
                        "result_snapshot": {},
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .execute()
            )

        run_insert = await supabase_execute_with_retry(_create_run)
        inserted_rows = run_insert.data or []
        run_row = inserted_rows[0] if inserted_rows else None
    except Exception as run_insert_error:
        print(f"[/analyse] analysis_runs insert skipped: {run_insert_error}")

    # Step 3 — console breadcrumb so server logs show when a run starts
    print(f"\n[/analyse] Starting orchestration for: {document_id}")
    log_activity(
        document_id=document_id,
        action_id=None,
        event_source="user",
        event_type="manual_analyse_request",
        actor_name="user",
        summary=f"User requested manual analysis for document {document_id}.",
        metadata={"analysis_key": analysis_key},
    )

    # Step 4 — run the full agentic loop (tools executed in Python, Claude plans the sequence)
    try:
        working_memory = await orchestrate(trigger)
    except Exception:
        # Mark failed runs so the same key can be retried later.
        if run_row and run_row.get("id"):
            try:
                def _mark_failed():
                    return (
                        supabase.table("analysis_runs")
                        .update({"status": "failed"})
                        .eq("id", run_row["id"])
                        .execute()
                    )

                await supabase_execute_with_retry(_mark_failed)
            except Exception as run_fail_error:
                print(f"[/analyse] analysis_runs failure update skipped: {run_fail_error}")
        raise

    # Step 5 — how many tool calls completed
    print(f"[/analyse] Done. Steps taken: {len(working_memory['steps'])}")

    # Step 6 — compact JSON for the API client (UI or curl)
    response_payload = {
        "doc_id": document_id,
        "steps_taken": len(working_memory["steps"]),
        "analysis_status": working_memory.get("analysis_status", "completed"),
        "action_generation_mode": working_memory.get("action_generation_mode", ""),
        "risk_score": working_memory.get("risk_score"),
        "findings_count": len(working_memory.get("findings", [])),
        "action_item": working_memory.get("action_item"),
        "idempotent_hit": False,
        "analysis_run_id": run_row.get("id") if run_row else None,
    }

    # Save completed snapshot for future idempotent replays.
    # IMPORTANT: only cache truly completed runs. Incomplete/rate-limited runs must be retryable.
    if run_row and run_row.get("id") and response_payload.get("analysis_status") == "completed":
        try:
            def _mark_completed():
                return (
                    supabase.table("analysis_runs")
                    .update({"status": "completed", "result_snapshot": response_payload})
                    .eq("id", run_row["id"])
                    .execute()
                )

            await supabase_execute_with_retry(_mark_completed)
        except Exception as run_complete_error:
            print(f"[/analyse] analysis_runs completion update skipped: {run_complete_error}")
    elif run_row and run_row.get("id") and response_payload.get("analysis_status") in {"incomplete", "rate_limited", "failed"}:
        # Mark these as failed so a user can click “Continue analysis” and get a fresh run later.
        try:
            def _mark_incomplete_failed():
                return (
                    supabase.table("analysis_runs")
                    .update({"status": "failed", "result_snapshot": response_payload})
                    .eq("id", run_row["id"])
                    .execute()
                )

            await supabase_execute_with_retry(_mark_incomplete_failed)
        except Exception as run_fail_mark_error:
            print(f"[/analyse] analysis_runs incomplete update skipped: {run_fail_mark_error}")

    log_activity(
        document_id=document_id,
        action_id=(response_payload.get("action_item") or {}).get("id") if isinstance(response_payload.get("action_item"), dict) else None,
        event_source="system",
        event_type="analysis_completed",
        actor_name="api",
        summary=f"Analysis completed for document {document_id}.",
        metadata={
            "steps_taken": response_payload.get("steps_taken"),
            "analysis_status": response_payload.get("analysis_status"),
            "action_generation_mode": response_payload.get("action_generation_mode"),
            "risk_score": response_payload.get("risk_score"),
            "findings_count": response_payload.get("findings_count"),
        },
    )

    return response_payload


@app.post("/sync/drive")
async def sync_drive(user_id: str = "dev", background_tasks: BackgroundTasks = None):
    """
    Manually triggers a Google Drive sync.
    Finds PDFs and DOCX files modified since the last sync,
    then downloads and ingests each one automatically.
    """
    # Step 1: Get the last sync timestamp.
    last_sync = await get_last_sync_time(user_id)
    print(f"[Drive Sync] Checking for documents modified since {last_sync.isoformat()}")

    # Step 2: List new documents from Drive.
    new_files = await list_new_documents(since=last_sync)
    print(f"[Drive Sync] Found {len(new_files)} new files")

    # Step 3: For each file, download and ingest it.
    ingested = []
    analysis_queued = 0
    for file in new_files:
        try:
            # Print a simple breadcrumb so logs show progress for each file.
            print(f"[Drive Sync] Ingesting: {file.get('name')}")

            # Download the bytes from Drive (folder restriction is enforced in the integration layer).
            file_bytes = await fetch_document(file.get("id"), file.get("name"))
            if not file_bytes:
                continue

            # Run the normal ingestion pipeline and store results in Supabase.
            # Use a stable upstream id so Drive files do not get re-ingested on repeat syncs.
            source_fingerprint = f"gdrive:{file.get('id')}" if file.get("id") else None
            result = await ingest_document(
                file_bytes,
                file.get("name"),
                user_id,
                source_fingerprint=source_fingerprint,
            )
            doc_id = result.get("doc_id")

            # Step 3b: If we ingested a real document (not just a failed row), queue the orchestrator.
            # This mirrors the /ingest behavior so the user sees risk + action queue updates.
            if doc_id:
                trigger = {
                    "document_id": doc_id,
                    "vendor_name": (result.get("vendor_name") or "Unknown vendor"),
                    "document_type": (result.get("doc_type") or "contract"),
                    "domain": (result.get("domain") or "subscription"),
                    "note": "Auto-analysis after Drive sync ingest",
                }
                if background_tasks:
                    background_tasks.add_task(orchestrate, trigger, user_id)
                else:
                    # Fallback: run without background_tasks if FastAPI didn't inject it.
                    background_tasks = BackgroundTasks()
                    background_tasks.add_task(orchestrate, trigger, user_id)
                analysis_queued += 1
                log_activity(
                    document_id=doc_id,
                    action_id=None,
                    event_source="system",
                    event_type="analysis_queued",
                    actor_name="monitor",
                    summary=f"Analysis queued after Drive sync ingest for document {doc_id}.",
                    metadata={"trigger": trigger},
                )

            ingested.append(
                {
                    "filename": file.get("name"),
                    "doc_id": doc_id,
                    "chunk_count": result.get("chunk_count"),
                }
            )
        except Exception as e:
            # If one file fails, keep going so other files still get ingested.
            ingested.append(
                {
                    "filename": file.get("name"),
                    "doc_id": None,
                    "chunk_count": None,
                    "error": str(e),
                }
            )

    # Step 4: After we attempt ingestion, advance the last sync time.
    await set_last_sync_time(user_id)

    # Step 5: Return a summary (including ingestion results).
    # Include the restricted folder ID so the UI/user can see what scope we searched.
    return {
        "found_count": len(new_files),
        "files": new_files,
        "ingested_count": len(ingested),
        "ingested": ingested,
        "analysis_queued": analysis_queued,
        "last_sync": last_sync.isoformat(),
        "restricted_folder_id": DRIVE_RESTRICT_FOLDER_ID or None,
    }


# Pydantic model — user selects which Drive files to ingest.
class DriveIngestRequest(BaseModel):
    file_ids: list[str]
    filenames: Optional[dict[str, str]] = None  # file_id -> filename (optional override)


@app.post("/sync/drive/ingest")
async def ingest_drive_files(body: DriveIngestRequest, user_id: str = "dev"):
    """
    Ingest only the Drive files the user selected.
    Downloads bytes from Drive MCP, ingests into Supabase, then updates last sync time.
    """
    ingested = []
    file_ids = body.file_ids or []
    filenames = body.filenames or {}

    for file_id in file_ids:
        try:
            name = filenames.get(file_id) or f"{file_id}.bin"

            # Download raw bytes from Drive MCP.
            file_bytes = await fetch_document(file_id=file_id, filename=name)
            if not file_bytes:
                ingested.append({"id": file_id, "name": name, "status": "download_failed"})
                continue

            # Feed bytes into the normal ingestion pipeline (same as a manual upload).
            ingest_result = await ingest_document(file_bytes, name)
            ingested.append(
                {
                    "id": file_id,
                    "name": name,
                    "status": "ingested",
                    "doc_id": ingest_result.get("doc_id"),
                    "is_duplicate": bool(ingest_result.get("is_duplicate")),
                }
            )
        except Exception as e:
            ingested.append({"id": file_id, "name": filenames.get(file_id), "status": "error", "error": str(e)})

    # Only after ingestion attempt: advance the last sync time.
    await set_last_sync_time(user_id)

    return {"ingested_count": len(ingested), "files": ingested}


@app.post("/monitor/run")
async def run_monitor(user_id: str = "dev"):
    """Manually triggers one full monitoring cycle. Use for testing."""
    print(f"\n[API] Manual monitoring cycle triggered for user: {user_id}")
    summary = await run_monitoring_cycle(user_id)
    return summary


@app.post("/regulatory/seed")
async def seed_regulatory(user_id: str = "dev"):
    """Seeds the regulatory knowledge base. Safe to re-run."""
    count = await seed_regulatory_corpus()
    return {"seeded": count, "message": f"Inserted {count} regulatory chunks"}
