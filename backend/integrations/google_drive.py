# google_drive.py — lists and downloads Google Drive files for Sentinel.AI
# This file is our Drive integration layer (it may call MCP via the official SDK, or REST as a fallback).

import os, httpx, base64, json, traceback
from datetime import datetime, timezone

from dotenv import load_dotenv

from api.db import supabase

# NOTE (important): We installed the official `mcp` SDK, but Google's Drive MCP gateway URL we have
# does not expose an SSE (Server-Sent Events) endpoint, so the SDK transports cannot connect.
# We therefore keep the raw `POST .../tools/call` request, but we *did* fix the namespace collision
# so we can use the SDK later if/when the gateway supports sessions.

load_dotenv()

# The connected Google Drive MCP endpoint (configured in Cursor/Claude).
# Note: In local backend execution, this endpoint has returned HTML 400 responses even with a valid OAuth token.
# To keep Day 4 unblocked, we fall back to the standard Google Drive REST API using the same OAuth token.
# Google MCP gateways often use a resource-based URL shape:
#   .../mcp/v1/projects/{project_id}/locations/{location}
# We default to the project/location you provided, but allow overriding via env.
DEFAULT_MCP_PROJECT = "SentinelGDrive"
DEFAULT_MCP_LOCATION = "us-central1"
MCP_PROJECT = os.getenv("DRIVE_MCP_PROJECT") or DEFAULT_MCP_PROJECT
MCP_LOCATION = os.getenv("DRIVE_MCP_LOCATION") or DEFAULT_MCP_LOCATION
MCP_URL = os.getenv("DRIVE_MCP_URL") or f"https://drivemcp.googleapis.com/mcp/v1/projects/{MCP_PROJECT}/locations/{MCP_LOCATION}"

# Some Google gateways require this quota-billing header when calling APIs with OAuth tokens.
# Set this to your numeric GCP project id or full project id string.
GOOG_USER_PROJECT = (
    os.getenv("GOOGLE_CLOUD_PROJECT")
    or os.getenv("GCP_PROJECT_ID")
    or os.getenv("X_GOOG_USER_PROJECT")
    or DEFAULT_MCP_PROJECT
)

# Tool names can differ between MCP implementations (some use full namespaces).
GDRIVE_SEARCH_TOOL = os.getenv("DRIVE_MCP_SEARCH_TOOL") or "search_files"
GDRIVE_READ_TOOL = os.getenv("DRIVE_MCP_READ_TOOL") or "read_file_content"

# Feature flag: when false, we skip MCP entirely and use REST only.
# This keeps progress moving while the MCP gateway URL/session endpoint is unresolved.
DRIVE_USE_MCP = (os.getenv("DRIVE_USE_MCP") or "0").strip().lower() in {"1", "true", "yes", "y"}

# Folder restriction: if set, we ONLY list/ingest files inside this one Drive folder.
# This is a safety/privacy control so Sentinel.AI never scans the whole Drive.
# Configure this in `backend/.env` as DRIVE_FOLDER_ID.
DRIVE_RESTRICT_FOLDER_ID = (os.getenv("DRIVE_FOLDER_ID") or "").strip()

# Standard Google Drive REST API endpoints (direct HTTP integration).
GOOGLE_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"

# OAuth settings (Option B): our backend exchanges refresh token -> access token.
# IMPORTANT: Keep secrets in backend/.env (never commit).
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_OAUTH_CLIENT_ID = os.getenv("DRIVE_OAUTH_CLIENT_ID") or ""
DRIVE_OAUTH_CLIENT_SECRET = os.getenv("DRIVE_OAUTH_CLIENT_SECRET") or ""
DRIVE_OAUTH_REFRESH_TOKEN = os.getenv("DRIVE_OAUTH_REFRESH_TOKEN") or ""

# Optional: if you already have a short-lived access token, you can set it directly.
MCP_AUTH_TOKEN = os.getenv("DRIVE_MCP_AUTH_TOKEN") or os.getenv("MCP_AUTH_TOKEN") or ""


async def _get_access_token() -> str:
    """
    Get an OAuth access token for Google APIs.
    Returns: access token string, or "" if we cannot obtain one.
    """
    try:
        # If caller set a token explicitly, use it first (fast path).
        if MCP_AUTH_TOKEN:
            return MCP_AUTH_TOKEN

        # Refresh-token flow is the expected long-lived approach for a backend service.
        if not (DRIVE_OAUTH_CLIENT_ID and DRIVE_OAUTH_CLIENT_SECRET and DRIVE_OAUTH_REFRESH_TOKEN):
            print("[Drive] missing OAuth env vars (client id/secret/refresh token).")
            return ""

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "client_id": DRIVE_OAUTH_CLIENT_ID,
                    "client_secret": DRIVE_OAUTH_CLIENT_SECRET,
                    "refresh_token": DRIVE_OAUTH_REFRESH_TOKEN,
                    "grant_type": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                print(f"[Drive] token refresh failed: HTTP {resp.status_code} body={(resp.text or '')[:300]}")
                return ""

            payload = resp.json() or {}
            return str(payload.get("access_token") or "")
    except Exception as e:
        print(f"[Drive] get_access_token failed: {e}")
        return ""


async def _call_mcp_tool(tool_name: str, tool_input: dict) -> dict:
    """
    Call one MCP tool by name with a JSON input payload.
    Takes: tool_name (string) and tool_input (dict).
    Returns: parsed JSON response dict, or {"error": "...", "content": []} on failure.
    """
    try:
        # MCP tool-call format expects `arguments` (not `input`) in many gateways.
        payload = {"name": tool_name, "arguments": tool_input}

        # Most MCP gateways require a Bearer token when called outside the Claude runtime.
        token = await _get_access_token()
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            # Ask explicitly for JSON to discourage generic HTML error pages.
            "Accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            # Still attempt the call (some environments may not require auth), but log it clearly.
            print("[Drive] no access token available; calling MCP without Authorization header.")

        # If provided, set the quota-billing project header.
        if GOOG_USER_PROJECT:
            headers["X-Goog-User-Project"] = GOOG_USER_PROJECT

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{MCP_URL}/tools/call",
                json=payload,
                headers=headers,
            )
            if response.status_code != 200:
                # Return a compact error payload so callers can decide how to handle it.
                body_preview = (response.text or "")[:2000]
                header_preview = {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower() in {"server", "content-type", "date", "www-authenticate", "location"}
                }
                print(
                    f"[Drive] tool call failed: HTTP {response.status_code} {response.reason_phrase} "
                    f"headers={header_preview} body={body_preview}"
                )
                return {"error": f"HTTP {response.status_code}", "content": []}

            return response.json()
    except Exception as e:
        # If MCP fails locally (common during setup), return a compact error payload so callers can fall back to REST.
        print(f"[Drive] MCP tool call failed: {e}")
        print((traceback.format_exc() or "")[:2000])
        return {"error": str(e), "content": []}


async def _assert_file_in_restricted_folder(file_id: str, token: str) -> bool:
    """
    Check whether a file belongs to the restricted folder.
    Takes: file_id (string) and OAuth token (string).
    Returns: True if allowed, False if it should be blocked.
    """
    try:
        # If no restriction is configured, allow by default.
        if not DRIVE_RESTRICT_FOLDER_ID:
            return True

        # Ask Drive for the file's parent folder IDs.
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "parents"},
            )
            if resp.status_code != 200:
                print(f"[Drive] Could not verify folder for file {file_id}: HTTP {resp.status_code}")
                return False

            parents = (resp.json() or {}).get("parents") or []
            return DRIVE_RESTRICT_FOLDER_ID in parents
    except Exception as e:
        print(f"[Drive] Folder restriction check failed for {file_id}: {e}")
        return False


async def list_new_documents(since: datetime, folder_id: str = None) -> list:
    """
    List the most recently modified Drive documents since a given timestamp.
    Takes: since (datetime) and optional folder_id (string).
    Returns: list of {id, name, mimeType, modifiedTime}.
    """
    try:
        # Enforce folder restriction even if the caller forgets to pass folder_id.
        # This prevents accidental scanning of the entire Drive.
        if DRIVE_RESTRICT_FOLDER_ID:
            folder_id = DRIVE_RESTRICT_FOLDER_ID

        # Build a query that targets PDF + DOCX + plain text files.
        #
        # IMPORTANT:
        # - Many Drive accounts report TXT as mimeType 'text/plain'
        # - Some report it as a generic binary type, so we also match on filename (".txt")
        # - We also exclude trashed files so we do not ingest deleted items
        query = (
            "trashed=false AND ("
            "mimeType='application/pdf' OR "
            "mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document' OR "
            "mimeType='text/plain' OR "
            "name contains '.txt'"
            ")"
        )

        # If a folder is provided, constrain results to that folder.
        if folder_id:
            query = f"({query}) and '{folder_id}' in parents"

        # Optional MCP path (disabled by default).
        if DRIVE_USE_MCP:
            raw = await _call_mcp_tool(
                GDRIVE_SEARCH_TOOL,
                {
                    "query": query,
                    "page_size": 20,
                },
            )

            # If MCP returned a usable JSON payload, parse it.
            content = raw.get("content") or []
            text_block = None
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_block = item.get("text") or ""
                    break

            if text_block:
                try:
                    parsed = json.loads(text_block)
                    files = parsed.get("files") if isinstance(parsed, dict) else parsed
                    if isinstance(files, list):
                        since_iso = since.astimezone(timezone.utc).isoformat()
                        results = []
                        for f in files:
                            if not isinstance(f, dict):
                                continue
                            modified = f.get("modifiedTime") or ""
                            if modified and modified > since_iso:
                                results.append(
                                    {
                                        "id": f.get("id"),
                                        "name": f.get("name"),
                                        "mimeType": f.get("mimeType"),
                                        "modifiedTime": modified,
                                    }
                                )
                        return results
                except Exception:
                    # If MCP parsing fails, continue to REST.
                    pass

        # REST path (default): list files from Google Drive API directly.
        token = await _get_access_token()
        if not token:
            return []

        # Drive API expects RFC3339 timestamps (UTC).
        since_rfc3339 = since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        drive_query = f"({query}) and modifiedTime > '{since_rfc3339}'"
        if folder_id:
            drive_query = f"({drive_query}) and '{folder_id}' in parents"

        # Print the final query so you can debug “why did Drive return 0?” quickly.
        print(f"[Drive] list_new_documents query: {drive_query}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GOOGLE_DRIVE_API_BASE}/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": drive_query,
                    "pageSize": 20,
                    "orderBy": "modifiedTime desc",
                    "fields": "files(id,name,mimeType,modifiedTime)",
                },
            )
            if resp.status_code != 200:
                print(f"[Drive] Drive REST list failed: HTTP {resp.status_code} body={(resp.text or '')[:300]}")
                return []

            payload = resp.json() or {}
            files = payload.get("files") or []
            results = []
            for f in files:
                if not isinstance(f, dict):
                    continue
                results.append(
                    {
                        "id": f.get("id"),
                        "name": f.get("name"),
                        "mimeType": f.get("mimeType"),
                        "modifiedTime": f.get("modifiedTime"),
                    }
                )
            return results
    except Exception as e:
        print(f"[Drive] list_new_documents failed: {e}")
        return []


async def fetch_document(file_id: str, filename: str) -> bytes:
    """
    Download one Drive file as raw bytes.
    Takes: file_id (string) and filename (string, used only for logging/future behavior).
    Returns: bytes, or b"" on error.
    """
    try:
        # Optional MCP path (disabled by default).
        if DRIVE_USE_MCP:
            raw = await _call_mcp_tool(GDRIVE_READ_TOOL, {"file_id": file_id})
            content = raw.get("content") or []
            if content:
                # The MCP response is expected to include base64 content in the first content item.
                first = content[0]
                if isinstance(first, dict):
                    content_text = first.get("text") or first.get("content") or ""
                else:
                    content_text = str(first)
                if content_text:
                    return base64.b64decode(content_text)

        # REST path (default): download the file bytes directly.
        token = await _get_access_token()
        if not token:
            return b""

        # Enforce folder restriction for downloads too (not just listing).
        allowed = await _assert_file_in_restricted_folder(file_id=file_id, token=token)
        if not allowed:
            print(f"[Drive] Blocked download outside restricted folder: {file_id} ({filename})")
            return b""

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"alt": "media"},
            )
            if resp.status_code != 200:
                print(f"[Drive] Drive REST download failed for {filename}: HTTP {resp.status_code}")
                return b""
            return resp.content
    except Exception as e:
        print(f"[Drive] fetch_document failed for {filename}: {e}")
        return b""


async def get_last_sync_time(user_id: str) -> datetime:
    """
    Read the last Drive sync timestamp from the `memory` table.
    Takes: user_id (string).
    Returns: datetime of last sync, or a safe default start date.
    """
    try:
        result = (
            supabase.table("memory")
            .select("value, created_at")
            .eq("user_id", user_id)
            .eq("memory_type", "drive_sync")
            .eq("key", "last_sync")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(result, "data", None) or []
        if not rows:
            return datetime(2025, 1, 1, tzinfo=timezone.utc)

        value = rows[0].get("value") or {}
        last = value.get("last_sync")
        if not last:
            return datetime(2025, 1, 1, tzinfo=timezone.utc)

        # Handle ISO strings like "2026-05-06T09:00:00+00:00".
        return datetime.fromisoformat(last).astimezone(timezone.utc)
    except Exception as e:
        print(f"[Drive] get_last_sync_time failed: {e}")
        return datetime(2025, 1, 1, tzinfo=timezone.utc)


async def set_last_sync_time(user_id: str) -> None:
    """
    Save the last Drive sync timestamp to the `memory` table.
    Takes: user_id (string).
    Returns: None (never raises).
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "user_id": user_id,
            "memory_type": "drive_sync",
            "key": "last_sync",
            "value": {"last_sync": now},
            "created_at": now,
            "updated_at": now,
        }

        # Upsert so repeated sync runs update the same logical record.
        supabase.table("memory").upsert(row, on_conflict="user_id,memory_type,key").execute()
    except Exception as e:
        print(f"[Drive] set_last_sync_time failed: {e}")

