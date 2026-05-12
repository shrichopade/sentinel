"""
mcp/google_drive.py — compatibility wrapper for Drive integration

We keep this file because some modules import Drive helpers from `mcp.google_drive`.
The real implementation lives in `integrations.google_drive` (REST-first, folder-restricted).
"""

# Re-export the public Drive functions under the historical import path.
from integrations.google_drive import (  # noqa: F401
    fetch_document,
    get_last_sync_time,
    list_new_documents,
    set_last_sync_time,
)

