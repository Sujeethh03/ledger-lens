# Progress

## Status: M1, M2, M3, M5, M6 built — M4 (Neo4j graph) deferred; real LLM runs pending API key

## What's built

**M1 — ingestion (verified against real EDGAR data)**
- `ingestion/fetch_edgar.py`: async EDGAR client, 8 req/s rate limit, tenacity retry
  (backoff+jitter) on 429/5xx, 403 = fatal config error. Returns bytes (PDF sniffing needs raw).
- `ingestion/normalize.py`: HTML → text → Item-header sections, Full Text fallback.
- `ingestion/pipeline.py` + `scripts/ingest.py`: idempotent by accession number.
- Real data in local DB: Apple (5 filings) + JPMorgan (4 filings).

**M2 — ingestion hardening**
- `ingestion/schema_drift.py`: structural expectation check per form type; bad parses get
  `schema_drift_flagged`, never silently indexed.
- `ingestion/ocr_fallback.py`: PDF sniff → pypdfium2 render → Tesseract OCR with mean word
  confidence persisted (filings.ocr_confidence → propagates to chunks).
- OCR test generates a real image-only PDF and runs actual Tesseract (not mocked).

**M3 — retrieval**
- `retrieval/chunking.py`: paragraph-packing chunker (~3000 chars, 300 overlap).
- `retrieval/embeddings.py`: OpenAI text-embedding-3-small behind an `Embedder` Protocol.
- Migration 3: `doc_chunks` has generated tsvector column + GIN index + HNSW (cosine) index —
  both hybrid arms served by one Postgres table.
- `retrieval/hybrid_search.py`: websearch_to_tsquery + cosine, RRF fusion (k=60).
- `retrieval/indexer.py`: indexes pending/ocr_fallback filings, refuses drift-flagged ones.
- CLIs: `scripts/index.py`, `scripts/search.py`.

**M5 — agents (LangGraph)**
- `agents/llm.py`: mini harness — JSON-mode + pydantic validation + one error-fed repair
  attempt; env-driven model tiers (CHEAP_MODEL / SYNTHESIS_MODEL, defaults gpt-4o-mini).
- `agents/planner.py` → `agents/retriever_agent.py` ([Cn] labeling) → `agents/synthesis_agent.py`
  (every-sentence-cited contract or INSUFFICIENT_EVIDENCE) → `agents/guardrail_agent.py`
  (deterministic coverage check).
- `agents/graph.py`: StateGraph — zero-chunk early refusal, one revision loop fed real
  violations, hard refusal over returning a failed answer. CLI: `scripts/ask.py`.

**M6 — evals + CI**
- `evals/golden_qa.jsonl`: 10 cases (7 answerable, 3 refusal-expected) matched to the corpus.
- `evals/run_eval.py`: refusal_correctness (gate 0.7), citation_validity (tripwire 1.0),
  keyword_coverage. Runs locally: `python -m evals.run_eval`.
- CI installs tesseract, runs ruff + 32 tests.

## NOT done yet (be honest about these)

1. **Real LLM runs**: OPENAI_API_KEY not yet in `.env`. Once added:
   `python -m scripts.index` → `python -m scripts.search "supply chain"` →
   `python -m scripts.ask "What supply chain risks does Apple disclose?"` →
   `python -m evals.run_eval`.
2. **M4 — Neo4j knowledge graph**: deferred by explicit decision (2026-07-09). Also deferred
   with it: graph agent node in the LangGraph pipeline, entity extraction, graph_loader.
3. **Cross-encoder reranking**: deferred until RRF-only quality is measured (needs torch).
4. **RAGAS**: custom deterministic harness first (deviation from dossier — recorded); layer
   RAGAS faithfulness on top later.
5. **Semantic support-checking in guardrail**: coverage-only today; "does the cited chunk
   actually support the sentence" needs a second LLM pass — known next hardening step.
6. **Docker compose stack**: written, untested (no Docker on this machine yet).
7. **FastAPI endpoints beyond /healthz, Celery workers, OTel wiring**: dossier scope not yet built.

## Next session should

1. Confirm OPENAI_API_KEY present, run the four commands above, fix whatever breaks.
2. Record real eval numbers here (they become the resume-bullet metrics).
3. Then either M4 (install Neo4j, graph loader + graph agent) or API layer (FastAPI routes +
   WS streaming per dossier §2 API design) — API layer probably first; it makes the demo real.

## Log

- **2026-07-09** — repo scaffolded, pushed to github.com/Sujeethh03/ledger-lens (public).
- **2026-07-09** — M1 built + verified end-to-end against real SEC EDGAR data.
- **2026-07-09** — M2 (drift + OCR), M3 (chunk/embed/hybrid), M5 (agent graph), M6 (evals + CI)
  built with 32 passing tests. Real LLM runs + eval numbers pending API key. gh CLI installed
  and authenticated; Postgres 17 + pgvector + tesseract installed locally via Homebrew.

## Open decisions / deviations from the dossier

- `FilingSection` table added (normalized text needs a home before chunking) — M1.
- `ingestion_status` lifecycle: pending → indexed only after real chunking/embedding — M1.
- Custom eval harness instead of RAGAS (deterministic first, judged metrics later) — M6.
- Guardrail is coverage-only, not support-checking, for now — M5.
- pip on this machine has a global QuantJo CodeArtifact index configured
  (~/.config/pip/pip.conf) with an expired token — every pip install in this repo must use
  `--index-url https://pypi.org/simple`. Left the global config untouched deliberately.
