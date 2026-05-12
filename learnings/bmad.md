# The BMAD Method — applied to Sentinel.AI (detailed)

This document explains **The BMAD Method (Breakthrough Method for Agile AI‑Driven Development)** in practical terms and shows **exactly how it would change** how Sentinel.AI is designed, built, tested, operated, and commercialised.

It’s written as both:
- a **build playbook** you can follow for the next iteration of Sentinel.AI, and
- an **interview-ready explanation** of “how you ship AI features safely”.

---

## 1) What BMAD is (in plain English)

BMAD is a delivery method for products where **AI is part of the core behaviour**, not just a UI enhancement.

It treats AI capabilities (RAG, agents, classification, drafting) like **production software components** with:
- explicit requirements
- measurable quality targets
- controlled change management (prompt/model updates)
- safety and governance gates
- operational monitoring and incident response

BMAD’s key difference from “traditional Agile” is that **you cannot declare a feature done** just because the endpoint returns 200 and the UI renders. In AI systems, “works once” is not a pass. BMAD forces you to prove:
- the output is grounded
- the output is stable enough
- the cost/latency is acceptable
- failures are safe and recoverable

---

## 2) The BMAD loop (how work flows)

BMAD is a loop you run repeatedly:

1. **Breakthrough framing** (define the user outcome + risk)
2. **Model the behaviour** (inputs/outputs, schemas, tools, prompts)
3. **Assemble the system** (code + data + UX + workflows)
4. **Demonstrate with evidence** (evals + metrics + acceptance gates)

The “breakthrough” part is not hype—it's about deliberately choosing a narrow workflow that creates a step-change improvement for the user (e.g., “never miss a cancellation window”).

---

## 3) Why BMAD matters for Sentinel.AI specifically

Sentinel.AI is in a high-trust domain:
- it processes **sensitive documents**
- it drafts **letters that can have legal/financial impact**
- it runs **background monitoring** that must be reliable

That means:
- quality must be measurable (not vibes)
- failures must be user-visible and recoverable (not silent)
- safety gates must be explicit
- you need an audit trail

BMAD would have materially reduced the kinds of failures you hit (RAG returning empty, JSON parse errors, partial orchestrator runs, schema drift, unclear monitoring state) because BMAD requires:
- evals early
- structured outputs by default
- observability and failure taxonomy
- idempotency and reliability engineering as part of “done”

---

## 4) Core BMAD principles (translated into engineering rules)

### 4.1 Evidence beats opinions

Every AI feature change must be backed by:
- eval results (before/after)
- metrics (latency, cost, success rate)
- failure analysis (what got worse, what got better)

### 4.2 Contracts everywhere (schemas + invariants)

AI is treated as a component with strict contracts:
- typed inputs and typed outputs (JSON schema / tool schema)
- validated outputs (reject or repair)
- deterministic “safe fallback” behaviour

### 4.3 Make failure states first-class

BMAD expects AI systems to fail sometimes.
The difference is: failures are **classified**, **measured**, and **recoverable**.

For Sentinel.AI examples:
- `rate_limited`
- `incomplete_tool_loop`
- `parse_failed`
- `retrieval_empty`
- `db_schema_mismatch`

### 4.4 Ship guardrails as product features

Guardrails aren’t “nice to have”; they’re part of the product.
Users buy trust, not tokens.

### 4.5 Change control (prompts/models/data)

BMAD treats these as deployable artefacts:
- prompts are versioned
- model choice is explicit per task
- data sources have provenance
- regressions are caught before release

---

## 5) The BMAD artefacts (what you create every sprint)

BMAD is “Agile”, but it adds AI-specific artefacts.

### 5.1 AI Feature Spec (one-pager)

For each AI feature (e.g., “contract analyst”), define:
- user outcome (what changes for the user)
- scope and non-scope
- inputs (documents, metadata, user preferences)
- outputs (schemas, what fields, constraints)
- grounding rules (what is allowed to be asserted)
- citation rules (what must be cited and how)
- safety rules (PII, legal caution, escalation triggers)
- acceptance tests (examples that must pass)
- cost/latency budget (per run)

### 5.2 Dataset / eval set (small but real)

Minimum recommended:
- 20–50 real-ish documents (anonymised if needed)
- 50–200 questions/prompts mapped to expected sources or expected behaviour

For Sentinel.AI, you want 3 eval sets:
- **Retrieval eval**: “does retrieval return the right chunks?”
- **Drafting eval**: “does the letter include placeholders, tone, and grounding?”
- **Safety eval**: “does PII get redacted? does it avoid unsafe advice?”

### 5.3 Quality gates (release criteria)

Examples of hard gates:
- \( \ge 95\% \) of analyst outputs parse as valid schema
- \( \ge 90\% \) of letter drafts contain required placeholders
- 0 instances of “invented clause numbers” in eval set
- no increase in “retrieval empty” rate

### 5.4 Runbook + failure taxonomy

For each major failure class:
- how to detect it (metric/log)
- how to reproduce it
- what the user sees
- what the engineer does
- what the rollback is

---

## 6) BMAD quality metrics (what you measure)

Below are practical metrics BMAD expects you to track. These become your “AI reliability dashboard”.

### 6.1 Retrieval (RAG) metrics

- **Retrieval hit rate**: % of queries where at least 1 relevant chunk is returned
- **Top‑k precision** (lightweight): in an eval set, does top 5 contain any expected chunk?
- **Empty retrieval rate**: how often retrieval returns 0 chunks
- **Chunk citation coverage**: % of assistant factual claims supported by citations (approx via heuristics)
- **Latency**: time for retrieval (semantic + keyword) and time for rerank (if any)

### 6.2 Agent/orchestrator metrics

- **Action creation rate**: % of orchestrations that produce a non-fallback action
- **Tool loop completion rate**: % that hit “expected tool sequence” (or at least a minimum)
- **Failure reason distribution**: rate_limited vs parse_failed vs db_error vs retrieval_empty
- **Retries per run**: how many retries occurred and where
- **Cost per run**: tokens + embedding calls + web calls

### 6.3 Draft quality metrics (letters)

- **Required fields present**: subject line, tone, placeholders list
- **Placeholders completeness**: are missing details explicitly marked as placeholders?
- **Grounding**: does it avoid inventing contract terms?
- **Readability**: approx reading level / length within bounds

### 6.4 Safety metrics

- **PII leakage count** (in output)
- **Unsafe advice flags** (escalation triggers)
- **Legal caveat injection rate** (should be 100% for letters)

### 6.5 Operational metrics

- **Drive sync success rate**
- **Monitoring tick success rate**
- **Queue latency**: time from ingest to action available
- **DB error rate** (Supabase RPC errors, RLS violations)

---

## 7) BMAD “gates” for Sentinel.AI (what blocks a release)

BMAD uses explicit gates. Here’s what “production-ish” gates could look like.

### Gate A — Structured output reliability

Block release if:
- analyst tool output parse success < 95%
- skills return fallbacks too often (e.g., > 5% of calls)

### Gate B — Grounding and citations

Block release if:
- any eval case shows invented clause references
- “uncited claim” warnings exceed a threshold on drafts

### Gate C — Safety and trust

Block release if:
- PII redaction fails on known patterns (NI number, account number)
- high-risk advice is not escalated (e.g., “take legal action immediately”)

### Gate D — Cost and latency

Block release if:
- average cost per analysis exceeds plan budget
- p95 end-to-end latency exceeds acceptable UX (e.g., > 60–120s without progress UI)

### Gate E — Operational reliability

Block release if:
- monitoring tick fails silently (no last/next sync updates)
- Drive sync causes duplicate documents beyond expected dedupe rate

---

## 8) How BMAD would change the Sentinel.AI architecture (concrete deltas)

### 8.1 “Runs” become first-class objects

BMAD would strongly encourage an explicit `analysis_runs` model with:
- run_id
- inputs (document_ids, trigger source)
- tool steps executed
- outputs (action_id, sources)
- cost/latency
- status + failure_reason

You already moved in this direction; BMAD makes it non-optional and makes UI show it.

### 8.2 Prompt and skill versioning is a system feature

Instead of “prompt lives in code”, BMAD expects:
- `prompt_version` fields stored with each output
- a way to run the same input against a new version and compare results
- rollback to previous prompt/model version if regressions occur

### 8.3 Stronger boundaries between:

- ingestion (extract, classify, chunk, embed, store)
- retrieval (semantic + keyword + rerank)
- reasoning (LLM tools + structured outputs)
- safety (guardrails)
- actions (HITL + send + audit)

This reduces “spooky action at a distance” where one bug breaks everything.

### 8.4 Monitoring is engineered like a product surface

BMAD would force:
- “last successful tick” and “next scheduled tick” always visible
- alerting when ticks fail
- safe retry with idempotency keys

---

## 9) How BMAD would have changed your Day 1–5 build sequence

Below is a realistic “BMAD version” of your same 5-day plan.

### Day 1 (BMAD)

- Build ingestion + retrieval, but also:
  - create a tiny retrieval eval set (10 queries)
  - instrument retrieval empty rate
  - add a basic “why this answer” citation display from day 1

Result: You catch retrieval empties immediately instead of later.

### Day 2 (BMAD)

- Build orchestrator + tools, but:
  - force structured output for each tool from day 2
  - log every tool step and failure reason
  - define “success” as: action created + citations present + guardrails run

Result: You reduce “classified but failed” ambiguity early.

### Day 3 (BMAD)

- HITL + guardrails, but:
  - guardrails become a gate for certain actions (send)
  - add a safety eval set (PII + unsafe advice)

Result: You ship safety as a tested feature, not just a module.

### Day 4 (BMAD)

- Monitoring + memory, but:
  - monitoring reliability metrics are required
  - memory writes are tied to explicit user outcomes

Result: You avoid “is sync running?” confusion because it’s defined as a requirement.

### Day 5 (BMAD)

- Integration + docs, but:
  - run full eval suite before “demo ready”
  - track cost/latency against budgets
  - create a release checklist and rollback plan

Result: Fewer last-minute regressions, more confidence to ship.

---

## 10) Concrete BMAD playbook for the next Sentinel.AI iteration

This is an actionable way to run BMAD on your current codebase.

### Step 1 — Create “AI feature specs” for the 5 core capabilities

1) Ingestion + extraction
2) Personal RAG chat
3) Orchestrated analysis (multi-tool)
4) Draft letter generation
5) Monitoring + alerts

Each spec should include:
- schema contracts
- grounding rules
- acceptance examples
- gates (what blocks a merge)

### Step 2 — Build the eval harness (minimum viable)

Implement:
- `eval/retrieval_cases.jsonl`
- `eval/draft_cases.jsonl`
- `eval/safety_cases.jsonl`

Run them in CI (or locally) with a “known good” configuration.

### Step 3 — Add instrumentation (minimal but real)

Store per-run fields:
- status, failure_reason
- token usage (if available)
- latency per stage
- retrieval count and top similarity

Show the core indicators in:
- Activity Log
- a “Run details” view for debugging

### Step 4 — Enforce structured output everywhere it matters

Use tool schemas / strict JSON schema for:
- contract analyst output
- risk scoring output
- draft letter output
- obligations extraction output

### Step 5 — Define “gates” and make them part of PR review

Examples:
- any new skill must come with:
  - schema
  - fallback behaviour
  - at least 3 eval cases
- any change to retrieval must not worsen:
  - empty retrieval rate
  - retrieval eval hit rate

---

## 11) What BMAD would not solve (important)

BMAD improves your **process and reliability**, but it does not magically fix:
- low-quality source documents (bad scans, missing pages)
- ambiguous legal interpretation (still needs cautious UX)
- upstream provider outages (Claude/Voyage downtime)

What it *does* do is ensure the system:
- degrades gracefully
- makes uncertainty explicit
- keeps user control central

---

## 12) BMAD “talk track” (how to explain this in interviews)

If asked “How do you ship AI features reliably?”, a strong answer is:

- We use a BMAD-style loop where **every AI feature has a spec, a schema contract, evals, and release gates**.
- We measure retrieval and tool-loop success rates, plus safety metrics like PII leakage.
- We treat prompts/models as versioned artefacts and require eval evidence for changes.
- We design explicit failure modes and recovery paths so users are never stuck with silent failures.

---

## 13) Sentinel.AI-specific examples of BMAD gates and tests (quick list)

- Retrieval returns 0 chunks:
  - user sees “not found” + suggestion + no hallucinated answer
  - metric increments `retrieval_empty`
- Analyst output invalid:
  - tool schema forces structured output
  - if still fails, a typed fallback is returned and failure_reason is stored
- Orchestrator ends without action:
  - fallback action created with status + “continue analysis”
  - metric increments `orchestrator_incomplete`
- Monitoring fails:
  - dashboard shows stale last sync time
  - activity log contains failure event

---

*End of BMAD document.*

