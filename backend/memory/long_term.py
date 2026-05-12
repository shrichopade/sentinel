# long_term.py — stores and recalls long-term user memory in Supabase
# This is used so the system can remember vendors, preferences, and outcomes across sessions.

import os, json
from datetime import datetime, timezone

# Voyage is used to create embeddings (numbers that represent “meaning”) for semantic recall.
# Note: this import/initialization matches the project spec for Day 4.
from voyageai import Client

from api.db import supabase
from dotenv import load_dotenv

load_dotenv()
voyage = Client(api_key=os.getenv("VOYAGE_API_KEY"))


def _embed(text: str) -> list[float]:
    """
    Turn text into an embedding vector using Voyage.
    Takes: plain text.
    Returns: a list of floats (the embedding) or [] if something goes wrong.
    """
    try:
        # Ask Voyage to convert the text into a meaning vector.
        # Note: Voyage returns an object with an `.embeddings` list in this SDK.
        # This project standardizes on 1024-dimensional embeddings (same as chunks).
        # `voyage-4-large` returns 1024-dimensional vectors.
        response = voyage.embed(texts=[text], model="voyage-4-large")

        # Pull the first embedding out of the response.
        return response.embeddings[0] if getattr(response, "embeddings", None) else []
    except Exception as e:
        # Embedding failures should not crash the app; we just store without semantic recall.
        print(f"[Memory] embed failed: {e}")
        return []


class LongTermMemory:
    """
    Simple long-term memory wrapper around the Supabase `memory` table.
    Stores structured JSON plus an embedding so we can recall by meaning later.
    """

    async def store_vendor_observation(self, user_id: str, vendor: str, observation: dict) -> str:
        """
        Save one vendor observation (e.g., a price increase notice).
        Takes: user_id, vendor name, and an observation dict.
        Returns: the inserted row id, or "" on error.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            key = (vendor or "").lower().strip()

            # Build embedding text so future recall can match by meaning, not just exact wording.
            emb = _embed(f"{vendor}: {json.dumps(observation)}")

            row = {
                "user_id": user_id,
                "memory_type": "vendor",
                "key": key,
                "value": observation,
                "created_at": now,
                "updated_at": now,
            }
            # Only include embedding when we have one (empty lists break vector columns).
            if emb:
                row["embedding"] = emb

            try:
                result = supabase.table("memory").insert(row).execute()
            except Exception as insert_error:
                # If the DB rejects the vector (dimension mismatch, etc.), retry without embedding.
                print(f"[Memory] store_vendor_observation insert failed: {insert_error}")
                row.pop("embedding", None)
                result = supabase.table("memory").insert(row).execute()
            data = getattr(result, "data", None) or []
            return (data[0].get("id") if data else "") or ""
        except Exception as e:
            print(f"[Memory] store_vendor_observation failed: {e}")
            return ""

    async def store_user_preference(self, user_id: str, context: str, decision: str, outcome: str) -> str:
        """
        Save one user preference (what the user chose, and how it turned out).
        Takes: user_id, context text, decision string, outcome string.
        Returns: the inserted row id, or "" on error.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            key = (context or "").lower().strip()
            value = {"decision": decision, "outcome": outcome, "context": context}

            emb = _embed(f"{context} {decision} {outcome}")

            row = {
                "user_id": user_id,
                "memory_type": "preference",
                "key": key,
                "value": value,
                "created_at": now,
                "updated_at": now,
            }
            # Only include embedding when we have one (empty lists break vector columns).
            if emb:
                row["embedding"] = emb

            try:
                result = supabase.table("memory").insert(row).execute()
            except Exception as insert_error:
                print(f"[Memory] store_user_preference insert failed: {insert_error}")
                row.pop("embedding", None)
                result = supabase.table("memory").insert(row).execute()
            data = getattr(result, "data", None) or []
            return (data[0].get("id") if data else "") or ""
        except Exception as e:
            print(f"[Memory] store_user_preference failed: {e}")
            return ""

    async def store_outcome(
        self, user_id: str, action_id: str, result: str, financial_impact: float = 0.0
    ) -> str:
        """
        Save one outcome event (what happened after an action).
        Takes: user_id, action_id, result text, optional financial impact number.
        Returns: the inserted row id, or "" on error.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            value = {
                "result": result,
                "financial_impact": financial_impact,
                "action_id": action_id,
            }

            emb = _embed(f"outcome {result} financial impact {financial_impact}")

            row = {
                "user_id": user_id,
                "memory_type": "outcome",
                "key": action_id,
                "value": value,
                "created_at": now,
                "updated_at": now,
            }
            # Only include embedding when we have one (empty lists break vector columns).
            if emb:
                row["embedding"] = emb

            try:
                result_row = supabase.table("memory").insert(row).execute()
            except Exception as insert_error:
                print(f"[Memory] store_outcome insert failed: {insert_error}")
                row.pop("embedding", None)
                result_row = supabase.table("memory").insert(row).execute()
            data = getattr(result_row, "data", None) or []
            return (data[0].get("id") if data else "") or ""
        except Exception as e:
            print(f"[Memory] store_outcome failed: {e}")
            return ""

    async def recall_vendor_history(self, user_id: str, vendor: str) -> list:
        """
        Fetch the last 10 vendor observations for a vendor.
        Takes: user_id and vendor name.
        Returns: list of memory rows (or [] on error).
        """
        try:
            key = (vendor or "").lower().strip()
            result = (
                supabase.table("memory")
                .select("*")
                .eq("user_id", user_id)
                .eq("memory_type", "vendor")
                .eq("key", key)
                .order("created_at", desc=True)
                .limit(10)
                .execute()
            )
            return getattr(result, "data", None) or []
        except Exception as e:
            print(f"[Memory] recall_vendor_history failed: {e}")
            return []

    async def recall_relevant_preferences(self, user_id: str, context: str) -> list:
        """
        Recall up to 5 preference memories that match the meaning of the given context.
        Takes: user_id and a context string.
        Returns: list of best matches from the match_memory RPC (or [] on error).
        """
        try:
            embedding = _embed(context or "")
            result = (
                supabase.rpc(
                    "match_memory",
                    {
                        "query_embedding": embedding,
                        "match_threshold": 0.65,
                        "match_count": 5,
                        "p_user_id": user_id,
                        "p_memory_type": "preference",
                    },
                )
                .execute()
            )
            return getattr(result, "data", None) or []
        except Exception as e:
            print(f"[Memory] recall_relevant_preferences failed: {e}")
            return []

