# GroundedAI — A Verifiable Agentic Retrieval Platform

Citation-grounded question answering over regulated documents, currently two sources:
**FDA drug labels** (openFDA — the headline domain) and **SEC filings** (EDGAR — the original
domain, kept as proof the architecture is source-agnostic). One fault-tolerant ingestion
pipeline (retry/backoff, OCR fallback, schema-drift detection), hybrid RAG (BM25 + vector +
RRF), a multi-agent LangGraph pipeline, and a small, deliberately-scoped Neo4j knowledge
graph — enforcing citation-grounded answers only: every sentence cites a real chunk or graph
fact, or the system refuses.

**Why this project exists:** replaces a group project ("Signify") on the resume that couldn't
be defended in an interview. Every line of this must be built solo and understood end-to-end —
see "Non-negotiables" below before adding anything.

**History note (2026-07-10):** the project began as "Ledger Lens", SEC-only, per the
[Portfolio Dossier](https://claude.ai/code/artifact/fe962bed-b7aa-4334-8893-90dc5c8c070f).
The pivot to drug labels as the headline domain (with SEC kept as second source) is a
deliberate, recorded divergence: the drug domain makes the refusal guarantee visceral
(a hallucinated interaction is dangerous, not just wrong), its multi-hop graph query works
without extra ingestion (the SEC one needed DEF 14A proxy statements), and two unrelated
domains on one pipeline prove the design better than either alone. The dossier remains the
source of truth for *architecture* decisions; domain-specific sections read through the lens
of this note.

## Start here every session

1. Read `PROGRESS.md` for current status and next step.
2. Run `git log --oneline -20` to see what actually landed (more reliable than any summary).

## Architecture at a glance

```
openFDA API ─┐                                   ┌─ documents/document_sections (Postgres)
             ├─ source adapter (fetch+normalize) ┤    source_type discriminator, meta JSONB
SEC EDGAR ───┘   retry+backoff, rate limit,      ├─ hybrid index: doc_chunks
                 OCR fallback (SEC), schema-     │    (tsvector+GIN ∥ pgvector+HNSW)
                 drift detector per doc type     └─ Neo4j graph loader (deterministic
                                                      lexicon/taxonomy extraction only)

Graph schema (5 node types, 2 domains):
  (Company)-[:FILED]->(Filing)-[:DISCUSSES]->(RiskFactor)
  (Drug)-[:TREATS]->(Condition)   (Drug)-[:INTERACTS_WITH]-(Drug)   edges carry source ids

Query time: Planner → parallel (Retriever [hybrid+RRF] + Graph [parameterized Cypher]) →
  Synthesis (every sentence cites [Cn]) → Guardrail (deterministic coverage check,
  one revision loop, refuse over shipping unverified)
```

## Non-negotiables (from the portfolio strategy this project is part of)

- **Build every layer yourself**, including the boring parts (migrations, retry logic, auth).
  No copied boilerplate you can't explain line-by-line.
- **The knowledge graph stays tiny and query-justified.** Budget: ≤3 node types per domain,
  each domain earning its place with one multi-hop query text-RAG structurally can't answer.
  Currently 5 types total; the drug arm's justifying query — "which drugs treating condition X
  have a labeled interaction with drug Y?" — runs live. A bigger graph without a bigger
  justification is a resume liability, not an asset.
- **Citation-forced generation is the core guarantee.** Every synthesized answer must trace to
  a real chunk or graph fact; the guardrail agent checks this programmatically, separate from
  the agent that wrote the answer. Graph extraction is deterministic (lexicon/taxonomy match,
  no LLM) for the same reason: the reliable arm must stay reliable.
- **Docker/CI/eval-harness are not stretch goals.** They're graded as required baseline.
- **Metrics before resume bullets.** Every "X%" on the resume comes from this repo's own eval
  run — never estimated. Current (20-case live run, 2026-07-10, post guardrail-splitter fix):
  refusal_correctness 1.00, citation_validity 1.00, keyword_coverage 0.94.

## Datastore & model decisions (locked in 2026-07-09)

- **Relational + vector store:** PostgreSQL + `pgvector` — one datastore, not a separate
  vector DB. Locally: Homebrew Postgres 17, `ledgerlens` role/database (name kept through the
  rename — it's an implementation detail), `pgvector` 0.8.4.
- **Graph store:** Neo4j (Homebrew).
- **Embedding model:** OpenAI `text-embedding-3-small` (1536 dims — matches
  `doc_chunks.embedding VECTOR(1536)`; don't change one without the other).
- **Chat/completion model:** OpenAI, cost-tiered (CHEAP_MODEL for planner, SYNTHESIS_MODEL
  for synthesis) — see `.env.example`.
- **openFDA:** keyless works (40 req/min ceiling, we throttle to 30); `OPENFDA_API_KEY`
  raises the ceiling. A no-match search returns 404-NOT_FOUND = "no such drug", not an error.

## Conventions

- Python, FastAPI (async), SQLAlchemy + Alembic migrations, Celery + Redis for ingestion workers.
- Structured logging (structlog), OpenTelemetry tracing with `trace_id` propagated end-to-end.
- Tests live next to what they test; eval cases live in `evals/golden_qa.jsonl`.
- pip on this machine must use `--index-url https://pypi.org/simple` (global CodeArtifact
  config with expired token — left untouched deliberately).

## Update this file / PROGRESS.md at the end of every session

A future session (different day, different machine, different Claude interface) should be able
to read `PROGRESS.md` + `git log` and continue with zero re-explanation. If a design decision
diverges from the dossier, record *why* here or in a commit message — don't just silently
drift from the plan.
