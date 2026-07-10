# GroundedAI

**A Verifiable Agentic Retrieval Platform** — ask questions over regulated documents and get
answers where **every sentence is cited to a real source, or the system refuses**.

Two document sources prove the architecture is source-agnostic:

- **FDA drug labels** (openFDA) — "Which drugs that treat pain have a labeled interaction
  with warfarin?" → answered from a knowledge graph, with citations. A hallucinated drug
  interaction isn't a wrong answer, it's a dangerous one — which is exactly why the refusal
  guarantee exists.
- **SEC filings** (EDGAR) — 10-K/10-Q/8-K with OCR fallback for scanned exhibits and
  schema-drift flagging when the SEC changes form structure.

One pipeline serves both: fault-tolerant async ingestion (retry/backoff, rate limiting,
idempotent re-runs), hybrid retrieval (BM25 + pgvector, RRF fusion), a deliberately small
Neo4j knowledge graph (5 node types across both domains, parameterized Cypher only), and a
LangGraph agent pipeline whose deterministic guardrail verifies every citation before an
answer ships.

**Live demo:** https://api-production-efa5.up.railway.app — citation-viewer UI at `/`,
Swagger at `/docs`.

Current status: `PROGRESS.md`. Architecture and conventions: `CLAUDE.md`.

## Measured (20-case golden QA eval, live run)

refusal_correctness **1.00** · citation_validity **1.00** · keyword_coverage **0.94**

## Stack

FastAPI · Celery + Redis · PostgreSQL + pgvector (one store, both retrieval arms) ·
Neo4j (Drug/Condition + Company/Filing/RiskFactor entities, parameterized-Cypher-only agent
arm) · LangGraph · OpenAI (embeddings + cost-tiered chat) · Tesseract OCR · Prometheus metrics

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

## Quickstart (local)

```bash
# services: Postgres 17 + pgvector, Redis, Neo4j (brew services start postgresql@17 redis neo4j)
cp .env.example .env         # add your OPENAI_API_KEY + a real SEC_EDGAR_USER_AGENT
python3 -m venv .venv && source .venv/bin/activate
pip install --index-url https://pypi.org/simple -e ".[dev]"
alembic upgrade head

# CLI path — drug labels
python -m scripts.ingest_drug warfarin ibuprofen aspirin   # fetch + normalize openFDA labels
python -m scripts.index                                    # chunk + embed (needs OPENAI_API_KEY)
python -m scripts.load_graph                               # sync Drug/Condition graph to Neo4j
python -m scripts.ask "What does the warfarin label say about NSAID interactions?"
python -m scripts.ask "Which drugs that treat pain have a labeled interaction with warfarin?"

# CLI path — SEC filings
python -m scripts.ingest --cik 320193 --limit 5    # fetch + normalize Apple filings
python -m scripts.ask "What supply chain risks does Apple disclose?"

python -m evals.run_eval                           # 20-case golden-QA eval

# API path
celery -A ingestion.celery_app worker --loglevel=info &
uvicorn api.main:app --port 8000
curl -X POST "localhost:8000/api/v1/ingest/drug/lisinopril?limit=2"   # async ingest via queue
curl -X POST "localhost:8000/api/v1/ingest/sec/1318605?limit=3"
curl "localhost:8000/api/v1/documents?source_type=drug_label"
curl -X POST localhost:8000/api/v1/query -H 'content-type: application/json' \
     -d '{"question": "Which drugs that treat pain interact with warfarin?"}'
```

## Tests

```bash
pytest -q        # 67 tests; OCR test runs real Tesseract (brew install tesseract)
ruff check .
```
