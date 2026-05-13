# Alternative technical approaches (top 5)

This notes **five credible ways** sentinel could have been built differently, focusing on **agentic AI**, **orchestration**, **MCP/tooling**, **observability**, **guardrails**, and **skills/modules**. Each option includes pros/cons and how it would change the product compared to our current FastAPI + Supabase + pgvector + custom orchestrator/skills approach.

## 1) LangGraph (LangChain) for orchestration + tool routing
**What it is**: A graph/state-machine framework for agent workflows (nodes, edges, conditional routing), typically paired with LangChain tool abstractions.

- **How it would change sentinel**
  - Orchestrator becomes an explicit **graph** (e.g., `ingest → analyst → (research?) → risk → draft → action_create`), instead of a “loop until done” pattern.
  - “Skills” become **nodes** with typed inputs/outputs; retries and fallbacks become graph edges.
  - Tool calls (web search, DB, email) become standardized LangChain tools.

- **Pros**
  - **Deterministic control flow**: easier to reason about than an open-ended loop; fewer “ended without create_action_item” cases.
  - **Built-in state tracking**: the graph state naturally becomes a traceable run artifact.
  - **Better testability**: you can unit test nodes and integration-test subgraphs.

- **Cons**
  - Adds a heavy dependency + learning curve; risk of “framework wrestling” in a 5‑day build.
  - You still need to solve **schema drift**, **RLS**, and **rate limits**—framework doesn’t remove infra realities.

- **Net effect**
  - **Improves**: orchestration reliability, debuggability, modular skills.
  - **Degrades**: speed-to-ship, and you still need custom guardrails + DB policies.

## 2) LlamaIndex for RAG + document ingestion + evaluation
**What it is**: A RAG-first framework with ingestion pipelines, chunking strategies, retrieval/rerank components, and evaluation tooling.

- **How it would change sentinel**
  - Ingestion (parse → chunk → embed → store) could become a LlamaIndex pipeline with configurable chunkers and metadata extractors.
  - Retrieval could use built-in **query transforms**, **hybrid search**, **rerankers**, and **evaluation harnesses**.
  - Regulatory corpus could be a second index with routing (personal vs regulatory).

- **Pros**
  - **Higher RAG quality faster**: less custom retrieval logic; easier to add rerank or query expansion.
  - **Evaluation tooling**: makes “RAG quality” measurable earlier (we listed this as a gap).

- **Cons**
  - Harder to keep “one service” simplicity if you want LlamaIndex-managed stores vs direct Supabase RPCs.
  - You still need careful **grounding/citations** and guardrails; “better retrieval” ≠ “safe output.”

- **Net effect**
  - **Improves**: retrieval robustness, future eval, faster iteration on chunking/retrieval.
  - **Degrades**: introduces a second abstraction layer over Supabase that can complicate ops.

## 3) Semantic Kernel (Microsoft) for skills/plugins + planner
**What it is**: A “skills” and “planner” oriented framework where capabilities are registered as functions and composed by a planner.

- **How it would change sentinel**
  - Our `backend/skills/*.py` map well to **Semantic Kernel skills** (first-class plugin functions).
  - The planner could choose which skills to call (analyst/research/risk/draft) based on the task.

- **Pros**
  - **Strong skills mental model**: matches interview language (“capabilities”, “plugins”, “composition”).
  - Cleaner interface contracts: typed inputs/outputs reduce brittle parsing.

- **Cons**
  - Planner behavior can be opaque; you can reintroduce the same “LLM chose not to do the last step” risk unless you enforce state-machine constraints.
  - Python ecosystem support is improving but can be more enterprise-leaning than startup-leaning.

- **Net effect**
  - **Improves**: modularity of skills, reusability, cleaner capability boundaries.
  - **Degrades**: less control unless paired with explicit orchestration rules.

## 4) Temporal / Durable orchestration for long-running reliable runs (AI-native “workflow engine”)
**What it is**: A workflow engine (Temporal, Durable Functions, etc.) for reliable, resumable, observable long-running processes with retries and state persistence.

- **How it would change sentinel**
  - Each analysis becomes a durable workflow with steps persisted outside the app process.
  - “Continue analysis” becomes a **first-class resume** concept, not a custom endpoint.
  - Retries/backoff/rate-limit handling become workflow policies.

- **Pros**
  - **Production-grade reliability**: resilient to crashes, restarts, network blips.
  - **Excellent observability**: workflow history is effectively a trace.
  - Natural fit for background monitoring loops and scheduled tasks.

- **Cons**
  - Significant operational overhead for a Week 1 build (extra service, infra, deployment).
  - You still need good prompts/guardrails; the workflow engine doesn’t fix hallucinations.

- **Net effect**
  - **Improves**: resiliency, resumability, debugging/tracing, HITL handoffs.
  - **Degrades**: simplicity and local-dev velocity.

## 5) Dedicated observability + guardrails stack (Langfuse/LangSmith + Guardrails AI / NeMo Guardrails)
**What it is**: Purpose-built platforms/libraries for LLM traces, prompt/version management, evaluations, red-teaming, and policy enforcement.

- **How it would change sentinel**
  - Every LLM call (analyst/research/risk/draft/chat) gets a **trace** with latency, tokens, prompt versions, and outputs.
  - Guardrails become policy objects (PII detection, refusal rules, citation requirements), not ad-hoc post-processing.
  - “Skills” can be versioned and A/B tested.

- **Pros**
  - **Immediate debug power**: quickly answer “why did this run fail?” with evidence.
  - **Safer outputs**: structured policies reduce harmful or ungrounded drafts.
  - Enables proper eval harnesses (retrieval quality, factuality, citation coverage).

- **Cons**
  - Extra integration + costs; can slow iteration if over-instrumented too early.
  - Some guardrail systems still require careful tuning to avoid over-blocking legitimate outputs.

- **Net effect**
  - **Improves**: interview-ready “LLMOps” story, reliability, safety, auditing.
  - **Degrades**: more moving parts early; need discipline to keep it lightweight.

---

## AI-native vs traditional architecture (what changes in practice)
- **Traditional**: API calls drive deterministic business logic; ML is “a service” called at the edges. Great for predictability, but struggles with open-ended tasks like interpreting contracts and drafting letters with citations.
- **AI-native / agentic**: the “core” is a **reasoning loop** (or workflow graph) that plans and uses tools, and the system is judged on **traceability, grounding, and safe escalation**, not just CRUD correctness.

For sentinel specifically, the most “AI-native” upgrades would be: **workflow orchestration (LangGraph/Temporal)** + **LLM observability (Langfuse/LangSmith)** + **schema-forced outputs** + **strong guardrails**. These changes mostly improve reliability and debuggability, at the cost of complexity and operational overhead.

