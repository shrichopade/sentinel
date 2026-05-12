# regulatory.py — regulatory knowledge base (separate from user documents)
# This file seeds a small “starter” set of UK regulations and lets the app retrieve them by similarity.

import os, json
import time

# VoyageAI SDK import note:
# There are multiple VoyageAI Python SDK shapes in the wild.
# Some docs use `from voyageai import Client`, while other examples use a `voyageai(...)` factory.
# We support both so the backend doesn't crash depending on which package version is installed.
try:
    # Newer/alternate SDK shape (factory function).
    from voyageai import voyageai as _voyageai_factory  # type: ignore
except Exception:
    _voyageai_factory = None
from voyageai import Client
from api.db import supabase
from dotenv import load_dotenv

load_dotenv()

# Create one VoyageAI client we can reuse for all embedding calls.
# We read the API key from the environment so secrets never live in code.
# Accept both VOYAGE_API_KEY (project standard) and voyageai_API_KEY (older naming).
_voyage_key = os.getenv("VOYAGE_API_KEY") or os.getenv("voyageai_API_KEY") or ""

# Prefer the factory style if it exists; otherwise use the official Client() class.
if _voyageai_factory:
    voyage_client = _voyageai_factory(api_key=_voyage_key)
else:
    voyage_client = Client(api_key=_voyage_key)


# Section 1 — a small built-in “regulatory corpus” we can seed into Supabase.
# Each entry is a plain-English summary we can cite later in agent outputs.
REGULATORY_CORPUS = [
    # Consumer Contracts (Information, Cancellation and Additional Charges) Regulations 2013
    {
        "regulation_name": "Consumer Contracts Regulations 2013",
        "jurisdiction": "GB",
        "domain": "subscription",
        "section_ref": "Section 28",
        "content": "If you entered a distance contract (for example, online or by phone), you usually have a 14-day right to cancel. The trader must tell you about this right. If the trader does not provide the required cancellation information, the cancellation window may be extended.",
    },
    {
        "regulation_name": "Consumer Contracts Regulations 2013",
        "jurisdiction": "GB",
        "domain": "subscription",
        "section_ref": "Section 29",
        "content": "The 14-day cancellation period is normally calculated from the day after you receive the goods, or from the day the contract is made for many services. If delivery happens in parts, the clock can start from receipt of the last item or last part. The exact start date depends on the contract type and how it is supplied.",
    },
    {
        "regulation_name": "Consumer Contracts Regulations 2013",
        "jurisdiction": "GB",
        "domain": "subscription",
        "section_ref": "Section 34",
        "content": "When you cancel under the cancellation rules, the trader must reimburse payments received from you within 14 days. The refund deadline is typically 14 days from the day the trader is informed of your decision to cancel. The trader may need to use the same payment method unless you agree otherwise.",
    },

    # Consumer Rights Act 2015
    {
        "regulation_name": "Consumer Rights Act 2015",
        "jurisdiction": "GB",
        "domain": "subscription",
        "section_ref": "Section 9",
        "content": "Goods must be of satisfactory quality, which generally means meeting the standard a reasonable person would consider acceptable. This includes being safe, durable, and free from defects. If goods are not satisfactory, you may have remedies like repair, replacement, or refund depending on timing and circumstances.",
    },
    {
        "regulation_name": "Consumer Rights Act 2015",
        "jurisdiction": "GB",
        "domain": "subscription",
        "section_ref": "Section 49",
        "content": "Services must be performed with reasonable care and skill. This means a trader must do the service to a competent professional standard. If they do not, you can be entitled to remedies such as repeating the service or a price reduction.",
    },
    {
        "regulation_name": "Consumer Rights Act 2015",
        "jurisdiction": "GB",
        "domain": "subscription",
        "section_ref": "Section 56",
        "content": "If a service does not have a specific completion time agreed, it must be performed within a reasonable time. What is “reasonable” depends on the service and context. If it is not done in a reasonable time, you may be entitled to a price reduction or other remedies.",
    },

    # UK GDPR (selected articles)
    {
        "regulation_name": "UK GDPR",
        "jurisdiction": "GB",
        "domain": "gdpr",
        "section_ref": "Article 15",
        "content": "You have the right to access your personal data held by an organisation. This usually includes getting confirmation that processing is happening and receiving a copy of the data. You can also request information about why the data is being processed and who it is shared with.",
    },
    {
        "regulation_name": "UK GDPR",
        "jurisdiction": "GB",
        "domain": "gdpr",
        "section_ref": "Article 17",
        "content": "You can request erasure of your personal data in specific situations, often called the “right to be forgotten.” This can apply when the data is no longer needed, consent is withdrawn, or processing is unlawful. There are exceptions where an organisation can keep data for legal obligations or other valid reasons.",
    },
    {
        "regulation_name": "UK GDPR",
        "jurisdiction": "GB",
        "domain": "gdpr",
        "section_ref": "Article 20",
        "content": "You may have the right to receive your personal data in a structured, commonly used, machine-readable format. In some cases you can ask for the data to be transmitted directly to another organisation. This right generally applies when processing is based on consent or contract and is carried out by automated means.",
    },
    {
        "regulation_name": "UK GDPR",
        "jurisdiction": "GB",
        "domain": "gdpr",
        "section_ref": "Article 21",
        "content": "You can object to processing of your personal data in certain situations. This includes a strong right to object to direct marketing at any time. When you object, the organisation must stop processing for that purpose unless it can show compelling legitimate grounds (which does not apply to direct marketing).",
    },

    # Tenancy Deposit Scheme (UK tenancy deposit protection rules)
    {
        "regulation_name": "Tenancy Deposit Scheme",
        "jurisdiction": "GB",
        "domain": "housing",
        "section_ref": "Deposit protection (30 days)",
        "content": "A landlord (or agent) generally must protect a tenant’s deposit in an approved tenancy deposit scheme within 30 days of receiving it. This is designed to reduce disputes and ensure the deposit is handled fairly. If the deposit is not protected properly, the landlord can face penalties and restrictions.",
    },
    {
        "regulation_name": "Tenancy Deposit Scheme",
        "jurisdiction": "GB",
        "domain": "housing",
        "section_ref": "Prescribed information (30 days)",
        "content": "The landlord (or agent) generally must provide the tenant with “prescribed information” about the deposit protection within 30 days. This includes details about the scheme used and how the deposit will be returned or disputed. Missing or late information can count as non-compliance.",
    },
    {
        "regulation_name": "Tenancy Deposit Scheme",
        "jurisdiction": "GB",
        "domain": "housing",
        "section_ref": "Failure to protect (penalty)",
        "content": "If a landlord fails to protect the deposit in time, a court can order them to pay the tenant a penalty. The penalty is commonly described as between 1 and 3 times the deposit amount, depending on the seriousness of the failure. This can also affect the landlord’s ability to use certain eviction routes until fixed.",
    },
]


# Section 2 — embed text with VoyageAI so we can do vector search in Supabase.
def embed_text(text: str) -> list[float]:
    """
    Convert a piece of text into a numeric vector embedding for similarity search.
    Takes: text (string).
    Returns: a list of floats (the embedding vector).
    """
    # Voyage can rate-limit embedding requests (especially without a payment method).
    # We retry a few times so one temporary limit does not break the whole seed or retrieval flow.
    MAX_RETRIES = 3
    WAIT_SECONDS = 20

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # We support two Voyage SDK response shapes:
            # - `client.embeddings.create(...)` returning `response.data[0].embedding`
            # - `Client().embed(...)` returning `response.embeddings[0]`
            if hasattr(voyage_client, "embeddings"):
                # Embeddings-create API shape.
                response = voyage_client.embeddings.create(model="voyage-4-large", input=[text])
                return response.data[0].embedding

            # Official Voyage Client() shape.
            response = voyage_client.embed(texts=[text], model="voyage-4-large")
            return response.embeddings[0]
        except Exception as e:
            # If we are being rate-limited, waiting is usually enough.
            is_rate_limit = "rate" in str(e).lower() or "billing" in str(e).lower()
            if is_rate_limit and attempt < MAX_RETRIES:
                print(f"[Regulatory] embed rate-limited. Waiting {WAIT_SECONDS}s before retry {attempt}/{MAX_RETRIES - 1}...")
                time.sleep(WAIT_SECONDS)
                continue

            # Not a retryable error (or retries exhausted) — re-raise so caller can handle.
            raise


# Section 3 — seed the built-in corpus into Supabase so retrieval can use it.
async def seed_regulatory_corpus() -> int:
    """
    Insert REGULATORY_CORPUS rows into the `regulatory_chunks` table.
    Returns: how many rows were inserted (int). Returns 0 if something fails.
    """
    inserted = 0

    # We seed entry-by-entry so one failure does not stop the whole corpus.
    for entry in REGULATORY_CORPUS:
        try:
            print(f"Seeding: {entry['regulation_name']} — {entry['section_ref']}")

            # “Safe to re-run”: skip if an identical key already exists.
            existing = (
                supabase.table("regulatory_chunks")
                .select("id")
                .eq("regulation_name", entry["regulation_name"])
                .eq("jurisdiction", entry.get("jurisdiction") or "GB")
                .eq("domain", entry.get("domain"))
                .eq("section_ref", entry.get("section_ref"))
                .limit(1)
                .execute()
            )
            if (existing.data or []):
                continue

            # Create the embedding from the content summary (the thing we want to retrieve later).
            embedding = embed_text(entry["content"])

            # Insert one row into Supabase (table created in Supabase SQL editor).
            supabase.table("regulatory_chunks").insert(
                {
                    "regulation_name": entry["regulation_name"],
                    "jurisdiction": entry.get("jurisdiction") or "GB",
                    "domain": entry.get("domain"),
                    "section_ref": entry.get("section_ref"),
                    "content": entry["content"],
                    "embedding": embedding,
                    "metadata": entry.get("metadata") or {},
                }
            ).execute()

            inserted += 1
        except Exception as e:
            # Log and continue so later entries still seed.
            print(f"[Regulatory] seed entry failed ({entry.get('regulation_name')} {entry.get('section_ref')}): {e}")

    return inserted


# Section 4 — retrieve regulatory context relevant to a user question.
def retrieve_regulatory_context(query: str, jurisdiction: str = "GB", domain: str = None) -> list:
    """
    Retrieve the most relevant regulatory chunks for a query using vector similarity search.
    Takes: query (string), jurisdiction (string), and an optional domain (string).
    Returns: a list of matching rows (each row includes content + similarity score).
    """
    # Embed the user query so we can compare it to stored regulatory embeddings.
    embedding = embed_text(query)

    # Call the SQL function created in Supabase to do a pgvector similarity search.
    result = supabase.rpc(
        "match_regulatory_chunks",
        {
            "query_embedding": embedding,
            "match_threshold": 0.4,
            "match_count": 5,
            "p_jurisdiction": jurisdiction,
            "p_domain": domain,
        },
    ).execute()

    # Return rows (or an empty list) so callers can loop safely.
    return result.data or []

