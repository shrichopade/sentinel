"""
db.py — creates the Supabase client and provides small helpers for safe queries.

This file exists because our backend runs many Supabase requests.
On Windows, the underlying HTTP client can sometimes fail with transient network errors.
We add a simple retry helper so one flaky request doesn't crash a whole API call or agent run.
"""

import asyncio
import os
import random

import httpx
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)


def _is_transient_supabase_network_error(e: Exception) -> bool:
    """
    Decide if an exception is probably a short-lived network/protocol glitch.
    Takes: e (Exception).
    Returns: True if we should retry, False if it looks like a real/logic error.
    """
    # httpx/httpcore sometimes wraps Windows socket issues into ReadError/RemoteProtocolError.
    if isinstance(e, (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError, httpx.TimeoutException)):
        return True

    # Windows can surface "non-blocking socket operation..." as an OSError (WinError 10035).
    # This is the exact issue we saw in the logs during long agent runs.
    if isinstance(e, OSError) and "10035" in str(e):
        return True

    # Fallback: if the message explicitly mentions connection termination/protocol, retry.
    msg = str(e).lower()
    if "connectionterminated" in msg or "remoteprotocolerror" in msg:
        return True

    return False


async def supabase_execute_with_retry(execute_fn, *, attempts: int = 4, base_delay_s: float = 0.35):
    """
    Run a Supabase `.execute()` call with retries for transient network errors.

    Takes:
    - execute_fn: a zero-arg function that performs the sync Supabase call and returns a result
                 (example: lambda: supabase.table('documents').select('*').execute()).
    - attempts: how many total tries (default 4).
    - base_delay_s: initial sleep time before retrying (default 0.35s).

    Returns: the result from execute_fn if successful.
    Raises: the last exception if all attempts fail.
    """
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            # Run the sync Supabase call in a thread so we don't block the async event loop.
            return await asyncio.to_thread(execute_fn)
        except Exception as e:
            last_error = e

            # If it doesn't look transient, don't waste time retrying.
            if not _is_transient_supabase_network_error(e):
                raise

            # If this was the final attempt, bubble the error up.
            if attempt >= attempts:
                raise

            # Exponential backoff with a little random jitter so multiple requests don't retry in lockstep.
            delay = (base_delay_s * (2 ** (attempt - 1))) + random.uniform(0, 0.15)
            await asyncio.sleep(delay)

    # Safety: if the loop exits unexpectedly, raise the last error.
    raise last_error
