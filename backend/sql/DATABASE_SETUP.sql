-- DATABASE_SETUP.sql — one-stop database setup for Sentinel.AI (Supabase/Postgres)
-- This script is designed for LOCAL DEV on Supabase with pgvector.
-- It includes: extensions, tables, indexes, RPC functions, and dev-friendly RLS policies.
-- Safe to re-run (uses IF NOT EXISTS / DROP IF EXISTS patterns where possible).

-- =============================================================================
-- 0) Extensions (dependencies)
-- =============================================================================
-- pgcrypto provides gen_random_uuid() used as a default primary key.
create extension if not exists pgcrypto;

-- pgvector provides the vector type + similarity operators for semantic search.
create extension if not exists vector;

-- =============================================================================
-- 1) Core tables
-- =============================================================================

-- documents — one row per uploaded/synced document with extracted text + metadata
create table if not exists public.documents (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  filename text not null,
  source text default 'upload', -- upload | google_drive | other
  domain text, -- subscription | employment | tax | gdpr | housing | insurance
  doc_type text, -- contract | policy | correspondence | receipt
  vendor_name text,
  effective_date date,
  expiry_date date,
  jurisdiction text default 'GB',
  risk_score numeric,
  status text default 'processing',
  raw_text text,
  summary text,
  flagged_clause_count integer default 0,
  obligation_count integer default 0,
  content_hash text, -- normalized content hash for dedupe (optional but recommended)
  source_fingerprint text, -- stable upstream id for dedupe (e.g. gdrive:{file_id})
  created_at timestamptz not null default now()
);

-- chunks — RAG chunks for user documents (vector search)
create table if not exists public.chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references public.documents(id) on delete cascade,
  content text not null,
  embedding vector(1024), -- voyage-4-large
  chunk_index integer,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- obligations — dated obligations extracted from documents
create table if not exists public.obligations (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references public.documents(id) on delete cascade,
  obligation_type text, -- renewal | cancellation | payment | notice (skill output)
  due_date date,
  description text,
  status text default 'pending',
  financial_amount numeric,
  currency text default 'GBP',
  created_at timestamptz not null default now()
);

-- actions — human-in-the-loop queue items (approve/reject/send)
create table if not exists public.actions (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references public.documents(id) on delete set null,
  action_type text, -- cancel | negotiate | complain | gdpr_sar | dispute | review
  severity text default 'medium',
  title text,
  summary text,
  draft_content text,
  status text default 'pending', -- pending | approved | rejected | sent
  reasoning text,
  sources jsonb default '[]'::jsonb,
  warnings jsonb default '[]'::jsonb,
  escalate boolean default false,
  escalation_reason text default '',
  generated_by text default 'model', -- model | fallback
  action_fingerprint text,
  financial_amount numeric(10,2),
  created_at timestamptz not null default now(),
  actioned_at timestamptz
);

-- agent_steps — legacy step log table (Activity Log UI can fall back to this)
create table if not exists public.agent_steps (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references public.documents(id) on delete set null,
  user_id text default 'dev',
  agent_name text,
  tool_called text,
  summary text,
  created_at timestamptz not null default now()
);

-- activity_log — unified timeline table for both user + agent/system events
create table if not exists public.activity_log (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references public.documents(id) on delete set null,
  action_id uuid references public.actions(id) on delete set null,
  user_id text default 'dev',
  event_source text not null check (event_source in ('user','agent','system')),
  event_type text not null,
  actor_name text not null,
  summary text not null,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- emails — outbound email audit records (Resend integration)
create table if not exists public.emails (
  id uuid primary key default gen_random_uuid(),
  subject text not null,
  from_email text not null,
  to_email text not null,
  body text not null,
  resend_id text,
  status text not null default 'queued',
  error text,
  created_at timestamptz not null default now()
);

-- analysis_runs — idempotency + caching for /analyse (avoid re-running identical analysis)
create table if not exists public.analysis_runs (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  analysis_key text not null unique,
  status text not null default 'running', -- running | completed | failed
  result_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- memory — long-term memory for vendors/preferences/outcomes
create table if not exists public.memory (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  memory_type text, -- vendor | preference | outcome
  key text,
  value jsonb default '{}'::jsonb,
  embedding vector(1024),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- regulatory_chunks — separate regulatory corpus for research-agent RAG
create table if not exists public.regulatory_chunks (
  id uuid primary key default gen_random_uuid(),
  regulation_name text not null,
  regulation_version text default '2025',
  jurisdiction text default 'GB',
  domain text,
  section_ref text,
  content text not null,
  embedding vector(1024),
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- =============================================================================
-- 2) Indexes (performance + dedupe)
-- =============================================================================

-- Newest-first listing speed
create index if not exists idx_documents_created_at_desc on public.documents (created_at desc);
create index if not exists idx_actions_created_at_desc on public.actions (created_at desc);
create index if not exists idx_agent_steps_created_at_desc on public.agent_steps (created_at desc);
create index if not exists idx_emails_created_at_desc on public.emails (created_at desc);
create index if not exists idx_analysis_runs_created_at_desc on public.analysis_runs (created_at desc);

-- Activity log timeline speed
create index if not exists idx_activity_log_created_at_desc on public.activity_log (created_at desc);
create index if not exists idx_activity_log_document_id_created_at_desc on public.activity_log (document_id, created_at desc);
create index if not exists idx_activity_log_action_id_created_at_desc on public.activity_log (action_id, created_at desc);

-- Dedupe: content_hash (same content for same user)
create unique index if not exists idx_documents_user_content_hash
on public.documents (user_id, content_hash)
where content_hash is not null;

-- Dedupe: upstream source fingerprint (Drive/email/etc.)
create unique index if not exists idx_documents_user_source_fingerprint
on public.documents (user_id, source_fingerprint)
where source_fingerprint is not null;

-- Action dedupe helpers
create index if not exists idx_actions_doc_status_fingerprint
on public.actions (document_id, status, action_fingerprint);
create index if not exists idx_actions_generated_by
on public.actions (generated_by);

-- analysis_runs lookups
create index if not exists idx_analysis_runs_document_id on public.analysis_runs (document_id);
create index if not exists idx_analysis_runs_status on public.analysis_runs (status);

-- Vector indexes (ivfflat) — requires embeddings present + ANALYZE for best results.
-- NOTE: ivfflat requires setting lists based on table size; 50 is a reasonable dev default.
drop index if exists public.idx_chunks_embedding_ivfflat;
create index if not exists idx_chunks_embedding_ivfflat
on public.chunks using ivfflat (embedding vector_cosine_ops) with (lists = 50);

drop index if exists public.idx_regulatory_chunks_embedding_ivfflat;
create index if not exists idx_regulatory_chunks_embedding_ivfflat
on public.regulatory_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 50);

drop index if exists public.idx_memory_embedding_ivfflat;
create index if not exists idx_memory_embedding_ivfflat
on public.memory using ivfflat (embedding vector_cosine_ops) with (lists = 50);

-- =============================================================================
-- 3) RPC functions (pgvector similarity search)
-- =============================================================================

-- match_chunks — semantic search over user document chunks
drop function if exists public.match_chunks(vector, float, int, uuid[]);
create or replace function public.match_chunks(
  query_embedding vector(1024),
  match_threshold float,
  match_count int,
  filter_doc_ids uuid[] default null
)
returns table (
  id uuid,
  document_id uuid,
  content text,
  chunk_index int,
  metadata jsonb,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    c.id,
    c.document_id,
    c.content,
    c.chunk_index,
    c.metadata,
    1 - (c.embedding <=> query_embedding) as similarity
  from public.chunks c
  where
    (filter_doc_ids is null or c.document_id = any(filter_doc_ids))
    and c.embedding is not null
    and 1 - (c.embedding <=> query_embedding) > match_threshold
  order by c.embedding <=> query_embedding
  limit match_count;
end;
$$;

-- match_regulatory_chunks — semantic search over regulatory corpus
drop function if exists public.match_regulatory_chunks(vector, float, int, text, text);
create or replace function public.match_regulatory_chunks(
  query_embedding vector(1024),
  match_threshold float,
  match_count int,
  p_jurisdiction text default 'GB',
  p_domain text default null
)
returns table (
  id uuid,
  regulation_name text,
  jurisdiction text,
  domain text,
  section_ref text,
  content text,
  metadata jsonb,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    r.id,
    r.regulation_name,
    r.jurisdiction,
    r.domain,
    r.section_ref,
    r.content,
    r.metadata,
    1 - (r.embedding <=> query_embedding) as similarity
  from public.regulatory_chunks r
  where
    r.embedding is not null
    and (p_jurisdiction is null or r.jurisdiction = p_jurisdiction)
    and (p_domain is null or r.domain = p_domain)
    and 1 - (r.embedding <=> query_embedding) > match_threshold
  order by r.embedding <=> query_embedding
  limit match_count;
end;
$$;

-- match_memory — semantic recall for long-term memory
drop function if exists public.match_memory(vector, float, int, text, text);
create or replace function public.match_memory(
  query_embedding vector(1024),
  match_threshold float,
  match_count int,
  p_user_id text,
  p_memory_type text default null
)
returns table (
  id uuid,
  user_id text,
  memory_type text,
  key text,
  value jsonb,
  created_at timestamptz,
  updated_at timestamptz,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    m.id,
    m.user_id,
    m.memory_type,
    m.key,
    m.value,
    m.created_at,
    m.updated_at,
    1 - (m.embedding <=> query_embedding) as similarity
  from public.memory m
  where
    m.embedding is not null
    and m.user_id = p_user_id
    and (p_memory_type is null or m.memory_type = p_memory_type)
    and 1 - (m.embedding <=> query_embedding) > match_threshold
  order by m.embedding <=> query_embedding
  limit match_count;
end;
$$;

-- =============================================================================
-- 4) Row Level Security (RLS) + dev policies
-- =============================================================================
-- These policies are intentionally permissive for single-user local development.
-- Tighten before production (e.g., per-user checks, service role only for writes).

alter table if exists public.documents enable row level security;
alter table if exists public.chunks enable row level security;
alter table if exists public.obligations enable row level security;
alter table if exists public.actions enable row level security;
alter table if exists public.agent_steps enable row level security;
alter table if exists public.activity_log enable row level security;
alter table if exists public.emails enable row level security;
alter table if exists public.analysis_runs enable row level security;
alter table if exists public.memory enable row level security;
alter table if exists public.regulatory_chunks enable row level security;

-- documents
drop policy if exists "Allow anon read/write documents (dev)" on public.documents;
create policy "Allow anon read/write documents (dev)"
on public.documents for all to anon, authenticated using (true) with check (true);

-- chunks
drop policy if exists "Allow anon read/write chunks (dev)" on public.chunks;
create policy "Allow anon read/write chunks (dev)"
on public.chunks for all to anon, authenticated using (true) with check (true);

-- obligations
drop policy if exists "Allow anon read/write obligations (dev)" on public.obligations;
create policy "Allow anon read/write obligations (dev)"
on public.obligations for all to anon, authenticated using (true) with check (true);

-- actions
drop policy if exists "Allow anon read/write actions (dev)" on public.actions;
create policy "Allow anon read/write actions (dev)"
on public.actions for all to anon, authenticated using (true) with check (true);

-- agent_steps
drop policy if exists "Allow anon insert to agent_steps (dev)" on public.agent_steps;
drop policy if exists "Allow anon read agent_steps (dev)" on public.agent_steps;
create policy "Allow anon insert to agent_steps (dev)"
on public.agent_steps for insert to anon, authenticated with check (true);
create policy "Allow anon read agent_steps (dev)"
on public.agent_steps for select to anon, authenticated using (true);

-- activity_log
drop policy if exists "Allow anon insert/read activity_log (dev)" on public.activity_log;
create policy "Allow anon insert/read activity_log (dev)"
on public.activity_log for all to anon, authenticated using (true) with check (true);

-- emails
drop policy if exists "Allow anon read emails (dev)" on public.emails;
drop policy if exists "Allow anon write emails (dev)" on public.emails;
create policy "Allow anon read emails (dev)"
on public.emails for select to anon, authenticated using (true);
create policy "Allow anon write emails (dev)"
on public.emails for all to anon, authenticated using (true) with check (true);

-- analysis_runs
drop policy if exists "Allow anon read analysis_runs (dev)" on public.analysis_runs;
drop policy if exists "Allow anon write analysis_runs (dev)" on public.analysis_runs;
create policy "Allow anon read analysis_runs (dev)"
on public.analysis_runs for select to anon, authenticated using (true);
create policy "Allow anon write analysis_runs (dev)"
on public.analysis_runs for all to anon, authenticated using (true) with check (true);

-- memory
drop policy if exists "Allow anon insert/read to memory (dev)" on public.memory;
create policy "Allow anon insert/read to memory (dev)"
on public.memory for all to anon, authenticated using (true) with check (true);

-- regulatory_chunks
drop policy if exists "Allow anon read regulatory_chunks (dev)" on public.regulatory_chunks;
drop policy if exists "Allow anon write regulatory_chunks (dev)" on public.regulatory_chunks;
create policy "Allow anon read regulatory_chunks (dev)"
on public.regulatory_chunks for select to anon, authenticated using (true);
create policy "Allow anon write regulatory_chunks (dev)"
on public.regulatory_chunks for all to anon, authenticated using (true) with check (true);

-- =============================================================================
-- 5) Optional: housekeeping
-- =============================================================================
analyze public.documents;
analyze public.chunks;
analyze public.regulatory_chunks;
analyze public.memory;

