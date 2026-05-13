# Challenges & Resolutions (sentinel — Day 1–5)

This document captures the biggest challenges we hit while building sentinel and how we resolved them, in an interview-ready format.

## 1) Functional — Orchestrator “classified but failed” / partial agent runs
- **Problem**: A document could be ingested/classified, but the orchestrator would stop after the first tool (or not run at all), leaving the UI with missing reasoning/drafts.
- **Options**:
  - **A**: Debug each agent step ad‑hoc from logs, re-run manually.
  - **B**: Make orchestration resilient: explicit statuses, retries/backoff, and guaranteed action creation.
  - **C**: Remove the agent loop and run a fixed pipeline (less flexible).
- **Chosen**: **B**
- **Why**: Keeps the agentic design, but prevents “silent failure” by guaranteeing a user-facing action and making incomplete runs recoverable.

## 2) Technical — Windows network/socket instability to Supabase (`WinError 10035`)
- **Problem**: Intermittent `httpx/httpcore` read/protocol errors caused API endpoints to 500, breaking dashboard/activity pages.
- **Options**:
  - **A**: Ignore/transiently retry by reloading UI (manual).
  - **B**: Centralize retry with exponential backoff + jitter around Supabase `.execute()` calls.
  - **C**: Replace supabase-py usage with direct async Postgres driver.
- **Chosen**: **B**
- **Why**: Fastest reliability win without rewriting the persistence layer; isolates transient network failures from user-visible crashes.

## 3) Data/ML — Embedding dimension mismatch (1536 vs 1024) breaking chunk inserts + retrieval
- **Problem**: Code used `voyage-4-large` (1024-d) while DB/RPC expected 1536-d → inserts failed, `match_chunks` errors, retrieval returned empty.
- **Options**:
  - **A**: Switch embeddings back to a 1536-d model (code change).
  - **B**: Migrate DB vector columns + RPCs to 1024-d (schema change).
  - **C**: Keep mismatch but “soft fail” retrieval (bad UX).
- **Chosen**: **B** (plus **temporary soft-fail** protection in retrieval while migrating)
- **Why**: Standardizes the system on one embedding model and prevents recurring production-style drift.

## 4) Database/API — PostgREST RPC overloading ambiguity (`PGRST203`)
- **Problem**: Multiple `match_chunks` function signatures in Supabase caused PostgREST to fail choosing a candidate.
- **Options**:
  - **A**: Rename RPC functions and update callers.
  - **B**: Disambiguate by passing named params for every argument.
  - **C**: Avoid RPC and use direct SQL queries only.
- **Chosen**: **B**
- **Why**: Minimal change; stable for PostgREST; avoids schema churn during a 5‑day build.

## 5) Security/Permissions — RLS blocking inserts (regulatory seeding)
- **Problem**: Supabase Row Level Security blocked writes to `regulatory_chunks` during seeding.
- **Options**:
  - **A**: Disable RLS entirely (unsafe pattern).
  - **B**: Add dev-only permissive policies for anon/authenticated.
  - **C**: Use service role key only for seeding (more production-like).
- **Chosen**: **B** (dev), with notes to tighten later
- **Why**: Local single-user mode needed fast iteration; policies are explicit and reversible.

## 6) Architectural — “Fallback action created” with no reasoning/draft (rate limits/incomplete loops)
- **Problem**: Anthropic 429s (TPM limits) or incomplete loops caused orchestrator to end without `create_action_item`, resulting in generic fallback actions.
- **Options**:
  - **A**: Increase API limits / paywall around usage only.
  - **B**: Add clearer failure modes + “continue” from UI + overwrite existing action.
  - **C**: Remove tool loop to reduce calls.
- **Chosen**: **B**
- **Why**: Converts failure into a user-controllable recovery path; reduces confusion and avoids duplicate queue items.

## 7) Functional/UX — Drive sync missing TXT files
- **Problem**: Drive listing filtered only PDF/DOCX MIME types, so `.txt` documents never appeared/ingested.
- **Options**:
  - **A**: Only support PDFs (simpler, but blocks real email notices).
  - **B**: Add TXT support end-to-end (Drive query + ingestion + vault).
  - **C**: Convert TXT to PDF externally.
- **Chosen**: **B**
- **Why**: TXT is critical for real-world price-rise emails; minimal engineering cost once ingestion supported TXT extraction.

## 8) Reliability — Model name drift / “model not found” (404) in skills
- **Problem**: Skills used a model string not available to the configured Anthropic account, causing silent skill failures (e.g., obligations extraction).
- **Options**:
  - **A**: Hardcode a single known-good model everywhere.
  - **B**: Centralize model selection via env var with a safe default.
  - **C**: Detect available models dynamically (extra API surface).
- **Chosen**: **B**
- **Why**: Keeps deployments flexible and prevents future “model rename” incidents from breaking core flows.

## 9) Agent correctness — Analyst “parse_error” due to best-effort JSON
- **Problem**: The Contract Analyst relied on “return JSON only” prompts, but LLM responses can include prose, invalid escapes, or truncation → JSON parsing fails → downstream steps become unavailable.
- **Options**:
  - **A**: Keep best-effort JSON + more repair prompts.
  - **B**: Force structured output using an Anthropic tool schema (tool_use) and treat text parsing as fallback only.
  - **C**: Switch to a different model/provider for stronger JSON adherence.
- **Chosen**: **B**
- **Why**: Tool schemas eliminate most parse failures by making the model emit machine-readable output by construction.

## 10) Product/DevEx — Port conflicts + stale processes on Windows (8000/8002)
- **Problem**: Ports were frequently occupied by stale processes, causing confusing “server won’t start” loops.
- **Options**:
  - **A**: Keep default ports and manually kill processes.
  - **B**: Standardize the project on a less-conflicted port and update UI base URLs.
  - **C**: Add dynamic port selection logic.
- **Chosen**: **B**
- **Why**: Simple, stable convention (8003) reduced “environment friction” and sped up iteration.

## 11) RAG quality — Chat returning “not found” despite data existing
- **Problem**: Keyword fallback used the full sentence string (`ILIKE "%full query%"`), which almost never matches; semantic threshold was too strict for broad queries → retrieval returned 0 chunks.
- **Options**:
  - **A**: Ask users to phrase queries more specifically (UX workaround).
  - **B**: Improve retrieval: term-based keyword fallback + relaxed semantic threshold when empty.
  - **C**: Add reranking model / eval harness (more work).
- **Chosen**: **B**
- **Why**: Big practical win for end-user experience with minimal complexity; keeps future eval/rerank as a next step.

