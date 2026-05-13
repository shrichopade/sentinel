# ARCHITECTURE.md — sentinel (learning artefact)

## Section 1 — What it does (2–3 sentences)
sentinel is a personal compliance and “admin debt” agent for people who have contracts, subscriptions, tenancy docs, and policy paperwork they struggle to track. You upload or sync documents, it extracts key obligations and risks, then drafts safe, reviewable actions (like cancellation or complaints) with sources. It is built for a single user workflow with human approval before anything is sent.

## Section 2 — Architecture layers (1–2 sentences each)
1. **Ingestion pipeline** — Accepts PDF/TXT → extracts raw text → classifies domain/type/vendor → chunks → embeds → stores `documents` + `chunks` + `obligations` in Supabase.
2. **Personal RAG store** — Retrieves from `chunks` using pgvector semantic search plus keyword fallback, then combines results for more reliable answers (dates/names still work even when embeddings miss).
3. **Regulatory RAG corpus** — Separate `regulatory_chunks` table + `match_regulatory_chunks` RPC; the Research Agent queries this first and only falls back to web search when coverage is weak.
4. **Orchestrator** — Claude-driven agentic loop with a fixed tool set (sub-agents + action creation), capped to a small iteration limit to prevent runaway loops.
5. **Sub-agents** — Contract Analyst finds risky clauses/obligations; Research Agent gathers applicable rights (regulatory RAG → web); Risk Scorer rates risk; Negotiation Drafter writes a user-facing letter.
6. **Skills library** — 6 reusable, typed async prompt modules (extract obligations, score risk, draft letter, summarise, price drift, GDPR SAR) to keep prompts consistent and testable.
7. **Guardrail layer** — Post-processes outputs for safer UX: citation expectations, PII redaction, legal caveat injection, and “scope escalation” flags for high-stakes advice.
8. **HITL system** — Creates action items in an Action Queue; user must review/edit and double-confirm before send; decisions can be written back as “outcomes” memory.
9. **Long-term memory** — Stores vendor history, preferences, and outcomes in Supabase so future runs can adapt to the user’s patterns (retrieval via vector + structured fields).
10. **Monitoring loop** — APScheduler runs periodic cycles: Drive sync ingest, deadline alerts, stale action escalation, plus basic bookkeeping (last/next run surfaced on Dashboard).
11. **Google Drive MCP** — Lists and fetches files via MCP HTTP calls (folder-restricted), then passes bytes into the same ingestion pipeline as manual uploads.
12. **Frontend** — React + Tailwind with 5 surfaces: Dashboard, Document Vault, Action Queue, Chat, Activity Log (wired to backend on port 8003 to avoid local conflicts).

## Section 3 — Data flow (one sentence per step)
Document uploaded/synced → text extracted and embedded → document stored (with dedupe: content hash and optional `source_fingerprint` like `gdrive:{file_id}`) → orchestrator triggered → memory + regulatory context loaded → sub-agents run analysis and drafting → guardrails applied → action item created → user reviews in queue → approves with double-confirm → email sent via Resend → memory updated with the user’s decision/outcome.

## Section 4 — Technology choices (markdown table)
| Component | Technology | Reason |
|---|---|---|
| LLM | Claude Sonnet 4 | Strong long-document reasoning; consistent with the agent/tooling approach used here |
| Vector store | Supabase pgvector | One service for relational + vector storage (documents, actions, memory) |
| Backend | Python FastAPI | Async-friendly, clean for orchestration + background tasks |
| Frontend | React + Tailwind | Fast iteration for 5 UI surfaces with decent UX |
| Email | Resend API | Simple API, good deliverability, generous free tier |
| Embeddings | voyageai `voyage-4-large` | Stable 1024-d embeddings; no Anthropic embedding endpoint available |
| Scheduling | APScheduler | Simple interval jobs; integrates well with the backend process |
| Document parsing | PyMuPDF | Robust PDF text extraction for real-world contracts |
| MCP | Google Drive MCP | Avoids building a custom Drive connector; reuses existing MCP capability |

## Section 5 — Honest gap analysis (3–4 specific gaps)
- No RAG evaluation harness, so retrieval quality/regressions are not measurable.
- No structured observability (token usage, latency per tool call, trace IDs), making production debugging hard.
- No streaming outputs to the UI; users wait for full completion rather than seeing incremental progress.
- No parallel sub-agent fan-out; sub-agents run sequentially, which increases end-to-end latency.

