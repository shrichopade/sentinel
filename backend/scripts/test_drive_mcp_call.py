# test_drive_mcp_call.py — quick sanity check for Drive MCP tool calls
# Run this to see whether the MCP endpoint returns JSON or HTML errors.

import asyncio
import json
import os
import sys

# Ensure the backend folder is on sys.path so our integration module can be imported when running as a script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from integrations.google_drive import _call_mcp_tool


async def main() -> None:
    # Keep the query simple: list one PDF file.
    result = await _call_mcp_tool(
        "gdrive_search",
        {"query": "mimeType='application/pdf'", "page_size": 1},
    )
    print(json.dumps(result)[:2000])


if __name__ == "__main__":
    asyncio.run(main())
