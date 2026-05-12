# probe_drive_mcp_variants.py — tries common MCP gateway URL/payload variants
# This helps diagnose why Google Drive MCP returns generic HTML 400.

import asyncio
import json
import os
import sys

import httpx

from dotenv import load_dotenv

load_dotenv()

# Ensure backend folder is on sys.path so local imports work.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from integrations.google_drive import _get_access_token, MCP_URL, GOOG_USER_PROJECT  # noqa: E402


async def _try_one(client: httpx.AsyncClient, url: str, payload: dict, headers: dict) -> dict:
    """
    Call one variant and return compact diagnostics.
    """
    try:
        r = await client.post(url, json=payload, headers=headers)
        text = r.text or ""
        return {
            "url": url,
            "status": r.status_code,
            "content_type": r.headers.get("content-type"),
            "server": r.headers.get("server"),
            "body_start": text[:200],
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


async def main() -> None:
    """
    Probe several path + payload formats and print the results.
    """
    token = await _get_access_token()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if GOOG_USER_PROJECT:
        headers["X-Goog-User-Project"] = GOOG_USER_PROJECT

    tool_name = os.getenv("DRIVE_MCP_SEARCH_TOOL") or "search_files"
    tool_args = {"query": "mimeType='application/pdf'", "page_size": 1}

    path_variants = [
        f"{MCP_URL}/tools/call",
        f"{MCP_URL}/tools:call",
        f"{MCP_URL}/v1/tools/call",
    ]

    payload_variants = [
        ("mcp_simple_arguments", {"name": tool_name, "arguments": tool_args}),
        ("mcp_simple_input", {"name": tool_name, "input": tool_args}),
        ("jsonrpc_tools_call_params_arguments", {"jsonrpc": "2.0", "id": "1", "method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}}),
        ("jsonrpc_tools_call_params_input", {"jsonrpc": "2.0", "id": "1", "method": "tools/call", "params": {"name": tool_name, "input": tool_args}}),
    ]

    results = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for url in path_variants:
            for label, payload in payload_variants:
                diag = await _try_one(client, url, payload, headers)
                diag["variant"] = label
                results.append(diag)

    # Print a readable summary first.
    for r in results:
        if "error" in r:
            print(f"{r['variant']} -> {r['url']} ERROR {r['error']}")
        else:
            print(
                f"{r['variant']} -> {r['url']} "
                f"status={r['status']} content_type={r['content_type']} server={r['server']} "
                f"body_start={json.dumps(r['body_start'])}"
            )


if __name__ == "__main__":
    asyncio.run(main())

