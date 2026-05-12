## Future Roadmap — making Sentinel.AI consumer-ready (commercial + production)

This roadmap lists **business/product features** and **technical capabilities** needed to ship Sentinel.AI to real end consumers (not just a local demo). It’s written to match the current architecture: FastAPI backend, Supabase Postgres/pgvector, React UI, Claude (agents), Voyage (embeddings), Drive ingestion, and HITL action queue.

---

## What “production-ready” means (for this product)

- **Trustworthy outputs**: The app must be correct enough, cite sources, and fail safely.
- **Secure by default**: Sensitive documents + personal data must be protected end-to-end.
- **Reliable operations**: Background sync/monitoring must work without manual babysitting.
- **Legally/ethically safe**: Clear disclaimers, user control, and compliance posture.
- **Commercially viable**: Pricing, billing, support, and measurable value delivered.

---

## Phase 1 (0–4 weeks): “Usable beta” for real users

### Business / consumer features
- **Onboarding flow**: Welcome → connect Google Drive OR upload files → first “win” in 2 minutes.
- **Explainability UI**: Every risk/obligation/letter shows “Why?” + citations + confidence.
- **Action queue UX polish**: Edit drafts, approve/reject, “what happens next” guidance.
- **Basic preferences**: Tone (friendly/firm), default actions (negotiate vs cancel), contact details.
- **Pricing model (simple)**: Free trial + one paid tier (limit by documents/actions/month).
- **Support basics**: In-app “report an issue”, feedback capture, and lightweight FAQ page.

### Technical capabilities
- **Auth + single-tenant isolation**: Real user accounts (Supabase Auth or similar) and per-user data separation.
- **File storage strategy**: Decide and implement:
  - Store originals in **Supabase Storage/S3** (recommended), or
  - Only store extracted text (less useful for audit/debug).
- **PII controls v1**: Stronger detection + redaction at display/export boundaries.
- **Error handling discipline**: No “Network error breaks the whole app”; graceful fallbacks everywhere.
- **Idempotent ingestion**: Deduplicate uploads/sync reliably (content hash + source fingerprint).
- **Background job reliability (basic)**: Ensure sync/monitor runs don’t overlap and don’t silently die.
- **Prompt/version management**: Track prompt versions per skill/agent output for reproducibility.

---

## Phase 2 (1–3 months): “Production v1” (safe + scalable + measurable)

### Business / consumer features
- **Multi-channel input**: Email forwarding inbox (“send your bills/contracts here”), Drive, manual upload.
- **Notifications**: Email/push for deadlines (renewals, cancellation windows), drafts awaiting approval.
- **Templates library**: Pre-built letters (cancellation, complaint, price rise challenge, SAR).
- **Outcome tracking**: User marks “sent/accepted/refunded/cancelled” so the product can prove value.
- **Trust signals**: “What data we store”, “how we use AI”, and clear “limitations” copy.
- **Team / family mode (optional)**: Shared household admin with permissions (later).

### Technical capabilities
- **Observability (must-have)**:
  - Structured logs with correlation IDs per ingest/analyse/action
  - Latency and error metrics per endpoint and per background job
  - LLM usage metrics (tokens, cost, rate-limit events)
  - Trace viewer for “agent steps” (what tools ran, outputs, failures)
- **RAG quality program**:
  - A small eval harness: query set + expected sources, regression tests on retrieval
  - “Grounding gate”: block or warn when claims are uncited or unsupported
  - Better chunking and reranking (and measurable improvements)
- **Security hardening**:
  - RLS policies tightened (no dev-permissive policies)
  - Service role separation for background jobs only
  - Secrets management (no plain `.env` in production)
  - Rate limiting + abuse prevention on public endpoints
- **Compliance posture**:
  - Data retention controls (delete documents, delete account, export data)
  - Audit logs (who accessed what, when, and what was sent)
  - DPIA-style documentation for AI + sensitive document handling (if targeting UK/EU)
- **Async job system**:
  - Move long-running work off request thread (queue like Redis/RQ/Celery, or hosted jobs)
  - Retry policies + dead-letter queue + alerting
  - Backpressure when LLMs are rate limited
- **Caching + cost control**:
  - Cache embeddings, retrieval results (short TTL), and completed analysis runs
  - “Budget guardrails”: stop analysis when it exceeds user plan limits

---

## Phase 3 (3–6+ months): “Commercial scale” (defensible + differentiated)

### Business / consumer features
- **Jurisdiction packs**: UK first, then EU/US variants; show which pack is active per document.
- **Vendor intelligence**: “Known patterns” by vendor (price drift, renewal tactics) from aggregated outcomes (privacy-safe).
- **Human escalation marketplace**: Optional “connect to a solicitor/consumer advisor” workflow.
- **Consumer-grade trust**: Security page, incident policy, status page, and strong brand clarity.

### Technical capabilities
- **Multi-tenant architecture maturity**:
  - Organization/workspace model, roles, and data partitioning
  - Per-tenant encryption keys (advanced)
- **Advanced guardrails**:
  - Policy engine per output type (chat vs risk vs letter)
  - Safer prompt toolchains (structured outputs everywhere, schema validation)
  - “High-risk action” gating (double-confirm + optional cool-down)
- **Model routing**:
  - Use cheaper models for summarization, reserve best model for complex reasoning
  - Automatic fallbacks when one provider is down or rate limited
- **Attack resistance**:
  - Prompt injection defenses for untrusted documents
  - Content safety scanning for uploads
  - Strict tool-use restrictions with allowlists
- **Performance + scaling**:
  - Read replicas / indexing strategy for large chunk volumes
  - Vector index tuning (ivfflat/hnsw) and background re-embedding strategy

---

## Production readiness checklist (minimum bar to charge money)

### Product / business
- **Clear value metric**: “Money/time saved” per user (refunds won, cancellations completed, deadlines avoided).
- **Billing**: Subscription + invoices + cancellation handling.
- **Support**: Ticketing email + in-app bug reports + response SLA (even if informal).
- **Terms & privacy**: Terms of Service, Privacy Policy, and “AI limitations” disclosure.

### Technical
- **Authentication + authorization**: Real users, role checks, secure session handling.
- **Secure storage**: Encrypted at rest + secure access paths + least privilege keys.
- **Operational monitoring**: Alerts on failed sync/ingest, high error rates, and job backlogs.
- **Backups + disaster recovery**: Automated DB backups; restore procedure tested.
- **Test coverage where it matters**:
  - Retrieval regression tests
  - Guardrails tests
  - “Golden path” end-to-end tests (upload → analyse → action → approve → send)
- **Release process**: Staging environment + migrations + rollbacks.
- **Data lifecycle**: Delete/export, retention, and audit logs.

---

## Suggested next 3 concrete milestones (high leverage)

1) **Add real auth + per-user isolation** (frontend + backend + Supabase RLS tightened)
2) **Add job queue + observability** (background reliability + traces you can debug fast)
3) **Add RAG eval harness + grounding gates** (measurably improves correctness and trust)

