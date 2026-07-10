# Interview Demo Runbook — GroundedAI

Two demo paths. Practice Path A once; keep Path B warm as the fallback once deployed.
**Rule: never debug live in an interview.** If something breaks, switch paths and keep talking.

## Path A — local full stack (everything works today; zero dependencies on cloud)

### Pre-interview checklist (do 15 min before)

```bash
brew services start postgresql@17 redis neo4j     # all three must be running
cd ~/grounded-ai && source .venv/bin/activate
pytest -q                                          # 64 passed = green light
celery -A ingestion.celery_app worker --loglevel=warning &   # terminal 1
uvicorn api.main:app --port 8000 &                            # terminal 2
curl -s localhost:8000/readyz                      # {"status":"ready",...}
```

### The 5-minute demo arc (in this order — it tells a story)

1. **The one-liner**: "GroundedAI answers questions over FDA drug labels and SEC filings.
   Every sentence it produces is verified against a real source by a separate deterministic
   guardrail — it cites, or it refuses. In the drug domain a hallucinated answer isn't just
   wrong, it's dangerous, which is why the architecture is built around refusal."

2. **Cited drug answer** — the headline:
   ```bash
   python -m scripts.ask "What does the warfarin label say about interactions with NSAIDs like ibuprofen?"
   ```
   Say: planner decomposes → hybrid search (BM25 + pgvector fused with RRF) over real openFDA
   label text → synthesis must cite every sentence → deterministic guardrail verifies every
   [Cn] or the answer is refused. Point at citations resolving to real label set_ids.

3. **The graph multi-hop** — what plain RAG can't do:
   ```bash
   python -m scripts.ask "Which drugs that treat pain have a labeled interaction with warfarin?"
   ```
   Say: this is a whole-corpus join — treats X AND interacts with Y — top-k retrieval
   structurally can't answer it. The planner routed it to Neo4j: parameterized Cypher only,
   no LLM-written queries; extraction into the graph is deterministic (lexicon from the
   corpus's own metadata), so the graph can't hallucinate; the fact enters the same [Cn]
   citation system. Expected: "Aspirin and Ibuprofen [C1]".

4. **Refusal** — the thing most RAG demos can't do:
   ```bash
   python -m scripts.ask "What is the recommended pediatric dosage of acetaminophen?"
   ```
   Say: acetaminophen isn't in the corpus → the system says so instead of hallucinating a
   dosage. Eval: refusal_correctness 0.95, citation_validity 1.00 on a 20-case golden set,
   gate-checked in the harness.

5. **Source-agnosticism** — the platform claim, proven in one command:
   ```bash
   python -m scripts.ask "What supply chain risks does Apple disclose in its filings?"
   ```
   Say: same pipeline, same guardrail, completely different domain — SEC filings with OCR
   fallback and schema-drift detection. The document store is source-agnostic
   (`documents.source_type`); adding a source is one adapter (fetch + normalize + drift
   expectations), roughly 3 files.

6. **Async ingestion + production surface** (if time):
   ```bash
   curl -s -X POST "localhost:8000/api/v1/ingest/drug/naproxen?limit=1"   # live, new drug
   curl -s localhost:8000/api/v1/tasks/<task_id-from-above>
   curl -s "localhost:8000/api/v1/documents?source_type=drug_label" | python3 -m json.tool | head
   curl -s localhost:8000/metrics | head
   ```
   Say: 202-immediately, Celery worker does the slow work, idempotent by set_id+version
   (label revisions replace, not duplicate), openFDA rate-limited with backoff; Prometheus
   counters, structured JSON logs, /readyz does real dependency checks.

## Path B — cloud URL (Railway; LIVE as of 2026-07-10)

**Base URL: https://api-production-efa5.up.railway.app** — same arc, swap `localhost:8000`
for that URL (the `python -m scripts.ask` steps become `curl -X POST $URL/api/v1/query
-H 'content-type: application/json' -d '{"question":"..."}'`). All five arc steps were
verified live on deploy day, including the graph multi-hop and the refusal.

**Or just open the base URL in a browser**: `GET /` serves a single-file citation-viewer UI
(`frontend/index.html`, vanilla HTML/JS, served by FastAPI itself — no second deployment).
Type a question or click a sample chip; every [Cn] in the answer is clickable and jumps to
the source card showing the actual chunk text, so an interviewer can watch a citation being
spot-checked. Refusals render as a distinct amber state. `/docs` (Swagger) also works for
raw API poking.

**Warm it up 10 minutes before the interview** (hit /readyz and one /query). If the cloud
misbehaves mid-demo: "let me show you on the local stack — same containers" → Path A.

Cloud layout (Railway free tier, project `grounded-ai`): pgvector Postgres, Redis, Neo4j,
and one `api` service that runs uvicorn + the Celery worker in a single container
(`infra/railway.Dockerfile` + `infra/railway_start.sh`) because the free plan caps
provisioned services at 5 — say this out loud if asked, it's a deliberate recorded tradeoff.
Known caveat: the Neo4j service currently has **no volume** (free-tier volume was too small
for Neo4j's default 256MB-per-db tx-log preallocation; fixed via env vars, but the old volume
is in a 48h pending-deletion state until ~2026-07-12). If Neo4j restarts, rebuild the graph
with: `ssh railway-api 'python -m scripts.load_graph'` (~5 seconds).

## Questions to expect (short answers you must own)

- **Why pgvector over Pinecone?** One datastore for chunks + metadata + lexical search;
  hybrid = SQL join away; no second system to keep in sync at this scale.
- **Why RRF not weighted scores?** BM25 and cosine scores aren't on comparable scales;
  RRF fuses ranks, needs no tuning, is the standard baseline.
- **Why is the guardrail deterministic?** The writer and checker must be different systems;
  a regex over [Cn] labels can't be sweet-talked. Semantic support-checking is the known
  next layer.
- **Why no LLM anywhere near the graph?** The graph is the auditable arm — deterministic
  lexicon/taxonomy extraction in, parameterized Cypher out. It can't hallucinate structure
  or facts. Flexibility lives in the text-RAG arm.
- **Why is the multi-hop query the graph's justification?** "Treats X and interacts with Y"
  requires checking every drug in the corpus and joining two facts per drug. Top-k similarity
  retrieval returns the k most similar chunks — it structurally cannot enumerate-and-join.
- **Why did you pivot domains mid-project?** The architecture was domain-agnostic on paper;
  the pivot proved it in practice (the adapter was ~3 files). Drug labels also make the
  refusal guarantee visceral and the graph query answerable without extra document types.
- **How do you handle label revisions?** openFDA labels revise in place (same set_id, higher
  version) — ingestion skips same-version, replaces newer-version (cascade wipes stale chunks).
- **What breaks at 100x scale?** Embedding cost (cache + incremental), Postgres HNSW memory
  (partition by source/year), per-tenant rate limiting on the API, worker autoscaling by
  queue depth, lexicon matching goes O(drugs×labels) — swap to Aho-Corasick.
- **What's honestly unfinished?** Reranking (baseline measured first, deliberately), semantic
  support-check, RAGAS, deployment, one SEC eval case over-refuses, lexical arm zero-hits on
  full-question queries. Saying this unprompted builds more trust than hiding it.

## Before any interview: read the code

30 minutes minimum, in this order: `agents/graph.py` → `agents/guardrail_agent.py` →
`retrieval/hybrid_search.py` → `ingestion/graph_loader.py` → `ingestion/pipeline.py`.
Every docstring contains the "why" for that module. If you can re-draw the two-lane
(ingest/query) flow from memory on paper, you're ready.
