# Ledger Lens

Multi-source financial-document intelligence pipeline: SEC filings (10-K/10-Q/8-K/earnings
transcripts) ingested through a fault-tolerant pipeline (OCR fallback for scanned exhibits,
schema-drift detection for changing SEC form fields), indexed with hybrid RAG (BM25 + vector +
reranking), and reasoned over by a multi-agent pipeline with a small, deliberately-scoped Neo4j
knowledge graph on top, enforcing citation-grounded answers only.

**Why this project exists:** replaces a group project ("Signify") on the resume that couldn't be
defended in an interview. Every line of this must be built solo and understood end-to-end — see
"Non-negotiables" below before adding anything.

**Full spec (architecture, schemas, API design, sequence diagrams, milestones, eval strategy,
interview prep):** the "Ledger Lens" section of the Portfolio Dossier artifact —
https://claude.ai/code/artifact/fe962bed-b7aa-4334-8893-90dc5c8c070f — treat that as the source of
truth for design decisions; this file tracks what's actually been built against that plan.

## Start here every session

1. Read `PROGRESS.md` for current status and next step.
2. Run `git log --oneline -20` to see what actually landed (more reliable than any summary).
3. Check the milestone table in the dossier (§5 Development plan under Ledger Lens) for what M-number comes next.

## Architecture at a glance

```
SEC EDGAR API → fetch_filing (retry+backoff, dead-letter on exhaustion)
   → source normalization (10-K/10-Q/8-K/transcript → unified schema)
   → Docling parse (or Tesseract OCR fallback for scanned exhibits)
   → schema-drift detector (known parser vs. best-effort fallback + alert)
   → hybrid index (BM25 + pgvector) + Neo4j graph loader (4 node types only:
     Company, Person, Filing, RiskFactor)

Query time: Planner → parallel (Retriever [hybrid search+rerank] + Graph [Cypher]) →
  Synthesis → Guardrail (citation-coverage check, refuse/revise if unsupported)
```

Full diagrams and the SQL/Cypher schema are in the dossier — don't redesign from scratch, extend
what's there or explicitly note in PROGRESS.md why a decision changed.

## Non-negotiables (from the portfolio strategy this project is part of)

- **Build every layer yourself**, including the boring parts (migrations, retry logic, auth). No
  copied boilerplate you can't explain line-by-line.
- **The knowledge graph stays scoped to 4 node types.** It's justified by one specific multi-hop
  query ("which companies share 2+ board members and both mention supply-chain risk in the same
  fiscal year"). Don't let it sprawl — a bigger graph without a bigger justification is a resume
  liability, not an asset.
- **Citation-forced generation is the core guarantee.** Every synthesized answer must trace to a
  real chunk or graph fact; the guardrail agent checks this programmatically, separate from the
  agent that wrote the answer.
- **Docker/CI/eval-harness are not stretch goals.** They're graded as required baseline — see the
  dossier's "non-negotiables" panel in the Recommendation section.
- **Metrics before resume bullets.** Every "X%" placeholder in the dossier's resume-bullet section
  gets replaced with a number measured from this repo's own eval run — never estimated.

## Datastore & model decisions (locked in 2026-07-09)

- **Relational + vector store:** PostgreSQL + `pgvector` — one datastore, not a separate vector DB.
  Locally: Homebrew Postgres 17, `ledgerlens` role/database, `pgvector` 0.8.4 extension enabled.
- **Graph store:** Neo4j (M4, not installed yet).
- **Embedding model:** OpenAI `text-embedding-3-small` (1536 dims — matches `doc_chunks.embedding
  VECTOR(1536)` already in the migration; don't change one without the other).
- **Chat/completion model:** OpenAI, cost-tiered — cheaper model (e.g. `gpt-4o-mini`) for simple
  agent steps, stronger model (e.g. `gpt-4o`) reserved for synthesis/guardrail reasoning. Requires
  `OPENAI_API_KEY` in `.env` once M3/M5 need it — not needed for M1 ingestion.

## Conventions

- Python, FastAPI (async), SQLAlchemy + Alembic migrations, Celery + Redis for ingestion workers.
- Structured logging (structlog), OpenTelemetry tracing with `trace_id` propagated end-to-end.
- Tests live next to what they test; eval cases live in `evals/golden_qa.jsonl`.

## Update this file / PROGRESS.md at the end of every session

A future session (different day, different machine, different Claude interface) should be able to
read `PROGRESS.md` + `git log` and continue with zero re-explanation. If a design decision diverges
from the dossier, record *why* here or in a commit message — don't just silently drift from the plan.
