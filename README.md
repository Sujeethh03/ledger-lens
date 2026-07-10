# GroundedAI — cited answers over regulated documents, or an honest refusal

[![CI](https://github.com/Sujeethh03/grounded-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/Sujeethh03/grounded-ai/actions)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![Live demo](https://img.shields.io/badge/demo-live-brightgreen)](https://api-production-efa5.up.railway.app)

Ask questions over **FDA drug labels** and **SEC filings**. Every sentence in every answer
cites a real source chunk or knowledge-graph fact — verified by a separate deterministic
checker — **or the system refuses to answer**. In the drug domain a hallucinated interaction
isn't just a wrong answer, it's a dangerous one; the whole architecture is built around
making that impossible to ship.

**▶ Try it: https://api-production-efa5.up.railway.app** — click a sample question, then
click any `[C1]` citation chip to see the exact label/filing text it points at.
(Swagger at [`/docs`](https://api-production-efa5.up.railway.app/docs).)

## Measured, not estimated

Every number below comes from this repo's own 20-case golden-QA eval
(`python -m evals.run_eval`), run live against real OpenAI models. The harness enforces
hard gates (refusal ≥ 0.7; citation validity is a tripwire that must be exactly 1.00):

| metric | score | meaning |
|---|---|---|
| refusal_correctness | **1.00** | answers what the corpus supports, refuses what it doesn't (incl. trap questions) |
| citation_validity | **1.00** | every `[Cn]` resolves to a chunk that was actually retrieved |
| keyword_coverage | **0.94** | answers contain the facts the gold cases expect |

## Why it's interesting to an engineer

- **The writer and the checker are different systems.** An LLM drafts the answer; a
  deterministic guardrail (regex + set membership, no LLM) verifies every sentence carries a
  valid citation. One revision loop, then refuse — a checker that can't be sweet-talked.
- **A knowledge graph that earns its place.** Deliberately tiny (5 node types, 2 domains),
  populated by deterministic lexicon/taxonomy extraction — no LLM anywhere near the graph, so
  it can't hallucinate structure. It exists for one thing top-k retrieval structurally can't
  do: whole-corpus joins like *"which drugs that treat pain have a labeled interaction with
  warfarin?"* (Try it — the answer is a graph fact with provenance.)
- **One pipeline, two unrelated domains.** Drug labels were added *after* SEC filings as a
  ~3-file source adapter (fetch + normalize + drift expectations) on an unchanged core —
  the architecture claim, proven rather than asserted.
- **Production posture, not a notebook.** Async ingestion via Celery/Redis (idempotent by
  source version — label revisions replace, never duplicate), retry/backoff + rate limiting
  per source API, OCR fallback with confidence propagated to chunks, schema-drift detection,
  Alembic migrations written by hand, structured logs, Prometheus `/metrics`, real dependency
  checks on `/readyz`, Dockerized, deployed.
- **Negative results are kept.** A plausible retrieval "improvement" (keyword-OR fallback for
  the lexical arm) was implemented, caught *reducing* retrieval quality by the eval harness,
  reverted, and written up in `retrieval/hybrid_search.py` — the eval exists precisely so
  changes like that can't sneak in on vibes.

## Architecture

```
openFDA API ─┐                                    ┌─ documents / document_sections (Postgres)
             ├─ source adapter (fetch+normalize) ─┤   source_type discriminator, meta JSONB
SEC EDGAR ───┘  retry+backoff, rate limits,       ├─ hybrid index: doc_chunks
                OCR fallback, schema-drift        │   (tsvector+GIN ∥ pgvector+HNSW → RRF)
                detection per doc type            └─ Neo4j graph (deterministic extraction)

Query:  Planner ──► Retriever (hybrid+RRF) ─┬─► Synthesis (every sentence cites [Cn])
                ──► Graph agent (param.     │        │
                    Cypher only)  ──────────┘        ▼
                                             Guardrail (deterministic coverage check)
                                             ok → cited answer      violation → 1 revision
                                                                    still failing → REFUSE
```

## Stack

FastAPI · Celery + Redis · PostgreSQL + **pgvector** (one store serves both retrieval arms —
hybrid search is a SQL query away, no second vector DB to keep in sync) · Neo4j ·
LangGraph · OpenAI (embeddings + cost-tiered chat) · Tesseract OCR · Alembic · Prometheus ·
Docker · GitHub Actions CI · Railway

## Repo map

```
ingestion/   source adapters (fetch_edgar, fetch_openfda + normalizers), schema-drift
             detection, OCR fallback, Celery tasks, deterministic graph extraction/loading
retrieval/   chunking, embeddings (Protocol-typed), hybrid search (BM25 ∥ pgvector → RRF),
             indexer
agents/      query-time LangGraph pipeline: planner → retriever + graph agent (parameterized
             Cypher) → synthesis → deterministic guardrail; llm.py is the JSON-mode harness
db/          SQLAlchemy models, session, Alembic migrations (hand-written)
api/         FastAPI app: query, async ingest, task status, health/metrics; serves the UI
frontend/    index.html — the whole UI, one dependency-free file served by the API at /
evals/       golden_qa.jsonl (20 cases) + deterministic harness with CI gates
scripts/     CLI entry points (ingest, ingest_drug, index, load_graph, ask, search)
tests/       pytest suite (network-free; OCR test runs real Tesseract)
infra/       Dockerfiles (api, worker, combined-for-Railway), compose, start script
```

Each module's header docstring carries the "why" for that layer (start with
`agents/graph.py` and `retrieval/hybrid_search.py`) — the code is meant to be read.
Design decisions and honest open items are tracked in `PROGRESS.md`.

## Quickstart (local)

```bash
# services: Postgres 17 + pgvector, Redis, Neo4j (brew services start postgresql@17 redis neo4j)
cp .env.example .env         # add your OPENAI_API_KEY + a real SEC_EDGAR_USER_AGENT
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head

# drug labels
python -m scripts.ingest_drug warfarin ibuprofen aspirin   # fetch + normalize openFDA labels
python -m scripts.index                                    # chunk + embed
python -m scripts.load_graph                               # sync Drug/Condition graph to Neo4j
python -m scripts.ask "What does the warfarin label say about NSAID interactions?"
python -m scripts.ask "Which drugs that treat pain have a labeled interaction with warfarin?"

# SEC filings — same pipeline, different adapter
python -m scripts.ingest --cik 320193 --limit 5
python -m scripts.ask "What supply chain risks does Apple disclose?"

python -m evals.run_eval                           # the 20-case eval behind the numbers above

# API + async ingestion
celery -A ingestion.celery_app worker --loglevel=info &
uvicorn api.main:app --port 8000                   # UI at localhost:8000, Swagger at /docs
```

## Tests

```bash
pytest -q        # 67 tests, network-free; OCR test runs real Tesseract (brew install tesseract)
ruff check .
```

---

Built solo, end to end — ingestion to UI, migrations to deployment — as a portfolio project
that can be defended line by line. Questions welcome: sujeeth.godavarthi@gmail.com
