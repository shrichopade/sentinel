# chat.py — takes a user question, finds relevant document chunks, and asks Claude to answer
# This is the core of the RAG (Retrieval-Augmented Generation) chat feature.
# It ensures Claude only uses information from the user's actual uploaded documents.

import os
import anthropic
from rag.retrieval import retrieve
from dotenv import load_dotenv

# Load secret keys from .env
load_dotenv()

# Create the Claude AI client
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# This is the instruction we give Claude before every conversation.
# It tells Claude to only use the provided document context, never to make things up,
# and always to cite its sources with [1], [2] etc.
SYSTEM_PROMPT = """You are a document assistant.
Answer questions using ONLY the context provided below.
For every factual claim in your answer, include a citation in square brackets, e.g. [1] or [2].
You MUST include at least one citation in every answer.
If the answer cannot be found in the context, respond with exactly:
"I couldn't find that in the uploaded documents."
Be concise and precise. Never invent information not in the context."""


def format_context(chunks: list) -> str:
    """
    Takes a list of retrieved document chunks and formats them as a numbered
    reference list that Claude can read and cite.

    Example output:
      [1] This agreement is for a 24-month mobile plan...

      [2] Early termination fees apply if cancelled before...
    """
    # Take up to 10 chunks and number them starting from 1
    entries = [f"[{i + 1}] {chunk['content'][:400]}" for i, chunk in enumerate(chunks[:10])]

    # Join them with a blank line between each for readability
    return "\n\n".join(entries)


async def chat(message: str) -> dict:
    """
    Main chat function. Takes the user's question and returns an answer
    grounded in their uploaded documents, with cited sources.

    Steps:
      1. Search the document database for relevant chunks
      2. Format those chunks as numbered context for Claude
      3. Ask Claude to answer using only that context
      4. Return the answer plus a list of source snippets

    Example return value:
      {
        "answer": "Your contract expires on 2026-04-30 [1].",
        "sources": [{"index": 1, "content": "...expiry date April 2026..."}]
      }
    """
    # Step 1: Search the database for document chunks relevant to the question.
    # If we find nothing, we return a safe “not found” response without calling Claude.
    chunks = await retrieve(message)
    if not chunks:
        return {
            "answer": "I couldn't find that in the uploaded documents.",
            "sources": [],
        }

    # Step 2: Format those chunks as a numbered reference list for Claude
    context = format_context(chunks)

    # Step 3: Ask Claude to answer the question using only the retrieved context.
    # The Anthropic client is synchronous, so this call blocks the current thread.
    # (This is fine for local dev; we can move to a worker thread later if needed.)
    response = client.messages.create(
        model="claude-sonnet-4-6",  # the model available on this API key
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                # We pass the context and the question together in one message.
                "content": f"Context:\n{context}\n\nQuestion: {message}",
            }
        ],
    )

    # Step 4: Return the answer text plus the top 5 source snippets.
    # We also include document_id so the frontend can look up the filename
    # and show "From: contract.pdf" on the source card.
    return {
        "answer": response.content[0].text,
        "sources": [
            {
                "index": i + 1,
                "content": c["content"][:400],          # enough text to show a meaningful excerpt
                "document_id": c.get("document_id"),    # used by the frontend to fetch the filename
                "similarity": round(c.get("similarity", 0), 3),  # relevance score (0–1)
            }
            for i, c in enumerate(chunks[:5])
        ],
    }
