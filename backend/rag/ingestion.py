# ingestion.py — reads PDF files and sends their text to Claude AI for classification
# Part of the RAG (Retrieval-Augmented Generation) pipeline in Sentinel.AI

import fitz        # PyMuPDF — the library that reads PDF files (installed as "pymupdf", imported as "fitz")
import anthropic   # Anthropic SDK — used to talk to Claude AI
import json        # built-in Python tool for reading/writing JSON data
import os          # built-in Python tool for reading environment variables
import hashlib     # built-in hashing tool used to generate a stable fingerprint for dedupe checks
from datetime import datetime, timezone  # used for explicit created_at timestamps in inserted rows
from voyageai import Client   # Voyage AI SDK — used to create text embeddings (numerical representations of text)
from dotenv import load_dotenv  # reads our .env file so secret keys are available as environment variables
from postgrest.exceptions import APIError  # lets us gracefully fallback when new DB columns are not migrated yet
from skills.extract_obligations import extract_obligations as extract_obligations_skill

# Load all the secret keys from sentinel.ai/backend/.env into memory
load_dotenv()

# Create a single Claude AI client we'll reuse throughout this file
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Create a single Voyage AI client for generating embeddings later
voyage = Client(api_key=os.getenv("VOYAGE_API_KEY"))


def extract_text(file_path: str) -> str:
    """
    Opens a PDF file and pulls out all the text from every page.
    Returns a single string with all pages joined together.
    """
    # Open the PDF file at the given path
    doc = fitz.open(file_path)

    # Loop through every page and extract its text
    pages = [page.get_text() for page in doc]

    # Close the file once we're done reading it
    doc.close()

    # Join all pages with a newline between them, and remove leading/trailing whitespace
    return "\n".join(pages).strip()


# Read plain text uploads and return normalized UTF-8 text.
def extract_text_from_txt(file_bytes: bytes) -> str:
    # Decode with UTF-8 and replace invalid bytes so uploads never crash on encoding issues.
    return file_bytes.decode("utf-8", errors="replace").strip()


def classify_document(raw_text: str) -> dict:
    """
    Sends the document text to Claude AI and asks it to identify key details —
    like what type of document it is, which company it's from, and important dates.
    Returns a Python dictionary with those details.
    """
    # Build the instruction we'll send to Claude, including the first 3000 characters of the document
    prompt = f"""Analyse this document and return ONLY valid JSON — no markdown fences, no extra text — in this exact structure:

{{
  "domain": "one of: subscription | employment | tax | gdpr | housing | insurance",
  "doc_type": "one of: contract | policy | correspondence | receipt",
  "vendor_name": "company name as string, or null",
  "effective_date": "YYYY-MM-DD or null",
  "expiry_date": "YYYY-MM-DD or null",
  "jurisdiction": "one of: GB | US | EU",
  "summary": "2-sentence plain English summary",
  "flagged_clause_count": 0
}}

Document text (first 3000 characters):
{raw_text[:3000]}"""

    # Send the prompt to Claude and wait for its response
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract the text from Claude's reply
    text = response.content[0].text

    # Remove any markdown code fences Claude might have added (e.g. ```json ... ```)
    text = text.replace("```json", "").replace("```", "").strip()

    # Attempt 1: try to parse the whole response as JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: if that failed, find the first "{" and last "}" and parse just that portion
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    # If both attempts failed, return an empty dict so the caller can handle the failure gracefully
    return {}


def chunk_document(raw_text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """
    Splits a long document into smaller, overlapping pieces called "chunks".
    Each chunk is roughly 500 words. The 50-word overlap means neighbouring chunks
    share some words — this prevents important context from being lost at the boundary
    between two chunks when we later search through them.
    """
    # Split the full text into individual words
    words = raw_text.split()

    chunks = []
    i = 0  # i is our current position in the word list

    # Keep taking slices of 'size' words until we've covered the whole document
    while i < len(words):
        # Grab a window of words starting at position i
        chunk_words = words[i : i + size]

        # Join the words back into a single string and save it
        chunks.append(" ".join(chunk_words))

        # Move forward by (size - overlap) so the next chunk shares 'overlap' words with this one
        i += size - overlap

    return chunks


def embed_chunks(chunks: list[str]) -> list[list[float]]:
    """
    Converts a list of text chunks into embeddings — lists of 1024 numbers that
    capture the meaning of each chunk. Similar texts will have similar numbers,
    which is what allows semantic (meaning-based) search to work.
    The embeddings are sent to the Supabase database and stored in the vector(1024) column.
    """
    # Send all chunks to Voyage AI in one API call and get back their numerical representations
    # We standardize on voyage-4-large so chunk embeddings match query embeddings (1024 dims).
    response = voyage.embed(chunks, model="voyage-4-large")

    # Extract just the embedding (list of floats) from each result item
    return [item for item in response.embeddings]


async def extract_obligations(raw_text: str, metadata: dict) -> list:
    """
    Extract obligations using the reusable skill module.
    Takes: raw_text (string) and metadata (dict).
    Returns: list of obligation dicts (or [] on failure).
    """
    doc_type = (metadata or {}).get("doc_type", "document")
    return await extract_obligations_skill(raw_text, doc_type)


# Build a stable hash so the same document content can be detected on future uploads.
def build_content_hash(raw_text: str) -> str:
    # Normalize whitespace and casing so tiny formatting differences do not bypass dedupe.
    normalized = " ".join((raw_text or "").lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def ingest_document(
    file_bytes: bytes,
    filename: str,
    user_id: str = "dev",
    source_fingerprint: "str | None" = None,
) -> dict:
    """
    The main pipeline function — takes a raw PDF file and runs it through every step:
    extract text → classify → store in database → chunk → embed → store chunks → extract obligations.
    Returns a summary of what was stored.
    """
    # These imports are placed here to avoid circular imports at module load time
    from api.db import supabase
    import tempfile
    import os

    # Decide extraction mode from filename extension.
    ext = os.path.splitext((filename or "").lower())[1]
    is_pdf = ext == ".pdf"
    is_txt = ext == ".txt"

    # Reject unsupported file types early with a clear message.
    if not is_pdf and not is_txt:
        raise ValueError("Unsupported file type. Please upload a PDF or TXT file.")

    # Only PDFs need a temporary file for PyMuPDF.
    tmp_path = None
    if is_pdf:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(file_bytes)
        tmp.close()
        tmp_path = tmp.name  # save the file path so we can clean it up later

    try:
        # Step 0 (optional): if we have a stable upstream source id, dedupe on that first.
        # Example: Google Drive file id or email message id.
        if source_fingerprint:
            try:
                existing_by_source = (
                    supabase.table("documents")
                    .select("id, domain, doc_type, vendor_name")
                    .eq("user_id", user_id)
                    .eq("source_fingerprint", source_fingerprint)
                    .limit(1)
                    .execute()
                )
                rows = existing_by_source.data or []
                if rows:
                    existing_doc = rows[0]
                    return {
                        "doc_id": existing_doc["id"],
                        "metadata": {
                            "domain": existing_doc.get("domain"),
                            "doc_type": existing_doc.get("doc_type"),
                            "vendor_name": existing_doc.get("vendor_name"),
                        },
                        "chunk_count": 0,
                        "obligation_count": 0,
                        "is_duplicate": True,
                        "duplicate_of_doc_id": existing_doc["id"],
                    }
            except Exception as source_dedupe_error:
                # Keep ingestion working even if the source_fingerprint column is not migrated yet.
                print(f"[Ingest] source fingerprint dedupe skipped: {source_dedupe_error}")

        # Step 1: Extract all text from the uploaded file.
        if is_pdf:
            raw_text = extract_text(tmp_path)
        else:
            raw_text = extract_text_from_txt(file_bytes)
        # Step 1b: fingerprint the extracted content for dedupe checks.
        content_hash = build_content_hash(raw_text)

        # Step 1c: if this exact content already exists for the same user, return that existing document.
        try:
            existing = (
                supabase.table("documents")
                .select("id, domain, doc_type, vendor_name")
                .eq("user_id", user_id)
                .eq("content_hash", content_hash)
                .limit(1)
                .execute()
            )
            existing_rows = existing.data or []
            if existing_rows:
                existing_doc = existing_rows[0]
                return {
                    "doc_id": existing_doc["id"],
                    "metadata": {
                        "domain": existing_doc.get("domain"),
                        "doc_type": existing_doc.get("doc_type"),
                        "vendor_name": existing_doc.get("vendor_name"),
                    },
                    "chunk_count": 0,
                    "obligation_count": 0,
                    "is_duplicate": True,
                    "duplicate_of_doc_id": existing_doc["id"],
                }
        except Exception as dedupe_error:
            # Keep ingestion working even if schema migration (content_hash column) is not applied yet.
            print(f"[Ingest] dedupe lookup skipped: {dedupe_error}")
            # Legacy fallback: fetch recent documents and compare normalized content hash in Python.
            # This still blocks duplicates even before the DB column migration is applied.
            try:
                legacy_existing = (
                    supabase.table("documents")
                    .select("id, domain, doc_type, vendor_name, raw_text")
                    .eq("user_id", user_id)
                    .order("created_at", desc=True)
                    .limit(200)
                    .execute()
                )
                for row in (legacy_existing.data or []):
                    row_hash = build_content_hash(row.get("raw_text") or "")
                    if row_hash == content_hash:
                        return {
                            "doc_id": row["id"],
                            "metadata": {
                                "domain": row.get("domain"),
                                "doc_type": row.get("doc_type"),
                                "vendor_name": row.get("vendor_name"),
                            },
                            "chunk_count": 0,
                            "obligation_count": 0,
                            "is_duplicate": True,
                            "duplicate_of_doc_id": row["id"],
                        }
            except Exception as legacy_dedupe_error:
                print(f"[Ingest] legacy dedupe lookup skipped: {legacy_dedupe_error}")

        # Step 2: Ask Claude to classify the document and pull out key metadata
        metadata = classify_document(raw_text)

        # Step 3: Save the document record to the Supabase "documents" table.
        # Claude returns some extra fields (like "summary" and "flagged_clause_count") that
        # don't exist as columns in our database table, so we filter to only the columns we need.
        allowed_columns = {
            "domain",
            "doc_type",
            "vendor_name",
            "effective_date",
            "expiry_date",
            "jurisdiction",
            "risk_score",
            "status",
            "summary",
            "flagged_clause_count",
        }
        doc_row = {k: v for k, v in metadata.items() if k in allowed_columns}
        doc_row.update(
            {
                "user_id": user_id,
                "filename": filename,
                # Stable upstream ID (Drive/email/etc.). Null for manual uploads.
                "source_fingerprint": source_fingerprint,
                "raw_text": raw_text,
                "content_hash": content_hash,
                # Placeholder during insert; updated after obligations are extracted.
                "obligation_count": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Insert with content_hash when available; fallback for environments where migration is not applied yet.
        try:
            result = supabase.table("documents").insert(doc_row).execute()
        except APIError as insert_error:
            if "content_hash" in str(insert_error):
                legacy_row = dict(doc_row)
                legacy_row.pop("content_hash", None)
                result = supabase.table("documents").insert(legacy_row).execute()
            else:
                raise

        # Grab the auto-generated UUID that Supabase assigned to this document row
        doc_id = result.data[0]["id"]

        # Step 4: Split the full text into overlapping 500-word chunks
        chunks = chunk_document(raw_text)

        # Step 5: Convert each chunk into a 1024-dimensional embedding vector
        embeddings = embed_chunks(chunks)

        # Step 6: Build the list of rows to insert into the "chunks" table
        rows = [
            {
                "document_id": doc_id,   # links this chunk back to its parent document
                "content": chunk,         # the raw text of this chunk
                "embedding": embedding,   # the 1024-float vector representation
                "chunk_index": i,         # position of this chunk within the document
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
        ]

        # Step 7: Insert all chunk rows in a single database call (more efficient than one-by-one)
        supabase.table("chunks").insert(rows).execute()

        # Step 8: Extract any obligations (deadlines, payments, renewals) from the document
        obligations = await extract_obligations(raw_text, metadata)

        # Step 9: If any obligations were found, save them to the "obligations" table
        if obligations:
            supabase.table("obligations").insert([
                {
                    "document_id": doc_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    **o,
                }  # attach the document ID and creation timestamp to each obligation
                for o in obligations
            ]).execute()
        # Persist obligation count on the parent document so vault listing can show it later.
        supabase.table("documents").update({"obligation_count": len(obligations)}).eq("id", doc_id).execute()

        # Return a summary so the API endpoint can tell the user what happened
        return {
            "doc_id": doc_id,
            "metadata": metadata,
            "chunk_count": len(chunks),
            "obligation_count": len(obligations),
            "is_duplicate": False,
        }

    finally:
        # Always delete the temporary PDF file, even if something went wrong above.
        if tmp_path:
            os.unlink(tmp_path)
