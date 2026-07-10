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

## The idea

Large language models answer confidently even when they shouldn't. In most apps that's an
annoyance; over drug labels it's a hazard, and over financial filings it's a liability.
GroundedAI treats that as the central design problem rather than a footnote: **an answer is
only allowed to exist if every sentence of it can be traced to a real document.**

It works like a newsroom. One system *writes*: a planner breaks the question down, hybrid
search (keyword + semantic, fused) pulls real passages from labels and filings, a knowledge
graph answers the questions retrieval can't — whole-corpus joins like *"which drugs that
treat pain also interact with warfarin?"* — and a language model drafts an answer from that
evidence, citing a source for every sentence. A completely separate system *fact-checks*:
a deterministic verifier (no AI involved) confirms that every citation points at a passage
that was actually retrieved. A draft that fails gets one chance to fix itself; if it fails
again, the system refuses to answer — and says so plainly. Refusing is a feature here,
not an error state.

Underneath sits real infrastructure, not a notebook: asynchronous ingestion workers with
retry, rate-limiting, and OCR fallback; idempotent updates (a revised drug label replaces
its old version, never duplicates it); schema-drift detection for when a source quietly
changes shape; hand-written database migrations; structured logs, metrics, and health
checks; Docker, CI, and a cloud deployment you can click right now. And because claims
need receipts, the project grades itself: a golden-question eval suite produced every
number in the table above — including one experiment it caught making things *worse*,
which was reverted and documented in the code.

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
