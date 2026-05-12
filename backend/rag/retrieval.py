# retrieval.py — finds the most relevant document chunks for a given user query
# Uses two strategies: semantic search (meaning-based) and keyword search (exact text match),
# then combines and deduplicates the results.

import os
import time
from voyageai import Client
from api.db import supabase
from dotenv import load_dotenv
from postgrest.exceptions import APIError

# Load environment variables from the .env file
load_dotenv()

# Create the Voyage AI client for converting query text into embeddings
# (same client pattern as ingestion.py — one instance, reused throughout)
voyage = Client(api_key=os.getenv("VOYAGE_API_KEY"))

# How long to wait (seconds) before retrying after a Voyage AI rate limit error.
# The free tier allows 3 requests per minute, so 20 seconds is a safe gap.
RATE_LIMIT_WAIT = 20

# How many times to retry before giving up
MAX_RETRIES = 3


def embed(text: str) -> list[float]:
    """
    Converts a single query string into a 1024-number vector (embedding).
    This vector captures the *meaning* of the query so we can find
    document chunks with similar meaning, not just matching words.

    Automatically retries up to 3 times if Voyage AI rate-limits us,
    waiting 20 seconds between attempts (free tier limit: 3 req/min).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Send the query text to Voyage AI and get back a list of 1024 floats
            # We standardize on `voyage-4-large` so embeddings match the rest of the system.
            response = voyage.embed([text], model="voyage-4-large")
            return response.embeddings[0]
        except Exception as e:
            # Check if this is a rate limit error by looking at the error message
            if "RateLimitError" in type(e).__name__ or "rate" in str(e).lower():
                if attempt < MAX_RETRIES:
                    # Wait before retrying — Voyage AI free tier resets every 20 seconds
                    print(f"Voyage AI rate limit hit. Waiting {RATE_LIMIT_WAIT}s before retry {attempt}/{MAX_RETRIES - 1}...")
                    time.sleep(RATE_LIMIT_WAIT)
                else:
                    # All retries exhausted — raise so the API returns a clear error
                    raise RuntimeError(
                        "Voyage AI rate limit exceeded after retries. "
                        "Please wait 20 seconds and try again, or add a payment method at dashboard.voyageai.com to unlock higher limits."
                    ) from e
            else:
                # Not a rate limit error — raise immediately, no point retrying
                raise


def semantic_search(query_embedding: list[float], threshold: float = 0.7, count: int = 10) -> list:
    """
    Searches the database for chunks whose meaning is similar to the query.
    Uses a Supabase stored function (match_chunks) that compares vectors using
    cosine similarity. Only returns chunks above the similarity threshold.
    """
    # Call the match_chunks function we created in Supabase
    # It compares the query embedding against all stored chunk embeddings
    try:
        result = supabase.rpc(
            "match_chunks",
            {
                "query_embedding": query_embedding,
                "match_threshold": threshold,
                "match_count": count,
                # Explicitly pass the 4th argument name so PostgREST can pick the correct RPC
                # when multiple overloaded `match_chunks` functions exist in the database.
                "filter_doc_ids": None,
            },
        ).execute()
    except APIError as e:
        # If the database function expects a different embedding dimension than the code is producing,
        # we fail "softly" by returning no semantic results (keyword fallback can still work).
        msg = str(e)
        if "different vector dimensions" in msg.lower():
            print(f"[RAG] semantic_search skipped due to embedding dimension mismatch: {e}")
            return []
        raise

    # Return the matching chunks, or an empty list if nothing matched
    return result.data if result.data else []


def keyword_search(query: str, limit: int = 5) -> list:
    """
    Searches for chunks containing the exact query text (case-insensitive).
    This is important for finding specific dates, amounts, or company names
    that might not score well in semantic search.
    """
    # Use ILIKE (case-insensitive LIKE) to find chunks containing the query text.
    # Full-sentence matches are often too strict, so we also try a few “keyword” terms.
    query_clean = (query or "").strip()
    if not query_clean:
        return []

    # First attempt: full query substring.
    try:
        result = (
            supabase.table("chunks")
            .select("id, document_id, content, metadata")
            .ilike("content", f"%{query_clean}%")
            .limit(limit)
            .execute()
        )
        rows = result.data or []
        if rows:
            return rows
    except Exception:
        # If Supabase errors, fall through to the term-based approach below.
        rows = []

    # Second attempt: term-based search (helps queries like “financial obligations”).
    # We keep only longer words to avoid matching everything (e.g. “are”, “my”, “the”).
    terms = [t for t in query_clean.lower().replace("?", " ").split() if len(t) >= 5]
    if not terms:
        return rows

    seen = set()
    merged = []
    for term in terms[:4]:
        try:
            term_result = (
                supabase.table("chunks")
                .select("id, document_id, content, metadata")
                .ilike("content", f"%{term}%")
                .limit(limit)
                .execute()
            )
            for r in (term_result.data or []):
                rid = r.get("id")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                merged.append(r)
                if len(merged) >= limit:
                    break
        except Exception:
            continue
        if len(merged) >= limit:
            break

    return merged


def rerank(semantic: list, keyword: list, query: str) -> list:
    """
    Combines results from semantic and keyword search into a single deduplicated list.
    Semantic results come first (they're higher confidence). Keyword-only results
    are appended at the end with a fixed similarity score of 0.5.
    Returns at most 10 results.
    """
    # Track which chunk IDs we've already added to avoid duplicates
    seen_ids = set()
    merged = []

    # Add all semantic results first, tagging them with their source
    for chunk in semantic:
        seen_ids.add(chunk["id"])
        merged.append({**chunk, "source": "semantic"})

    # Add keyword results that weren't already found by semantic search
    for chunk in keyword:
        if chunk["id"] not in seen_ids:
            seen_ids.add(chunk["id"])
            # Keyword-only results get a fixed similarity score since we don't have a vector score for them
            merged.append({**chunk, "source": "keyword", "similarity": 0.5})

    # Return the top 10 results
    return merged[:10]


async def retrieve(query: str) -> list:
    """
    Main public function — given a user's question, returns the most relevant
    document chunks from the database. Combines semantic and keyword search
    for robust retrieval.

    Example: retrieve("When does my Horizon Telecom contract expire?")
    Returns a list of chunks containing expiry date information.
    """
    # Step 1: Convert the query text into a vector embedding
    query_embedding = embed(query)

    # Step 2: Find chunks with similar meaning using vector similarity.
    # We do a “high confidence” pass first, then a looser pass if nothing is found.
    semantic = semantic_search(query_embedding, threshold=0.7, count=10)

    # Step 3: Find chunks containing the exact query words (fallback for dates/amounts)
    keyword = keyword_search(query)

    # Step 4: Merge and deduplicate both result sets, best results first
    merged = rerank(semantic, keyword, query)

    # If we still have nothing, relax the semantic threshold.
    # This helps broad questions like “financial obligations” that may not match any one chunk strongly.
    if not merged:
        semantic_loose = semantic_search(query_embedding, threshold=0.45, count=10)
        merged = rerank(semantic_loose, keyword, query)

    return merged
