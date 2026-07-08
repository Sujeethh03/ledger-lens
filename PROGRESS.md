# Progress

## Status: M1 complete — EDGAR ingestion working end-to-end against real data

## What's built

- Local dev stack: Postgres 17 (Homebrew) + pgvector 0.8.4, both running locally (no Docker
  available in this environment — `infra/docker-compose.yml` is written but untested; verify it
  once Docker's available).
- `db/models.py` — Filing, FilingSection (M1), DocChunk (M3, unused so far), GoldenQA.
- Alembic configured (`DATABASE_URL` env var wins over `alembic.ini`), initial migration applied.
- `ingestion/fetch_edgar.py` — async SEC EDGAR client, rate-limited to 8 req/s, exponential
  backoff+jitter retry (tenacity) on 429/5xx, 403 treated as a fatal config error (bad User-Agent),
  not retried.
- `ingestion/normalize.py` — HTML → text (BeautifulSoup/lxml) → sections split on "Item N." headers,
  falls back to a single "Full Text" section when no headers match. Text-native only — no OCR,
  no schema-drift detection yet (M2).
- `ingestion/pipeline.py` + `scripts/ingest.py` — synchronous, directly-callable ingestion
  (not a Celery task yet — proving the logic first, queueing comes later).
- **Verified against real data**: `python -m scripts.ingest --cik 0000320193 --limit 5` pulled 5
  real Apple filings (two 10-Qs, three 8-Ks), correctly split 10-Qs into ~17 Item sections each
  with real content (MD&A section: ~22k characters), re-running is idempotent (skips existing
  accession numbers, verified — 0 duplicates).
- 8 tests passing (`pytest -q`), `ruff check .` clean.

## Decisions locked in this session

- Database + vector store: PostgreSQL + pgvector (not Pinecone/Chroma) — see CLAUDE.md.
- Embedding model: OpenAI `text-embedding-3-small` (1536 dims, matches the schema).
- Chat model: OpenAI, cost-tiered (cheap model for simple steps, stronger model for
  synthesis/guardrail reasoning) — not needed until M3/M5.

## Next step: M2

- `ingestion/ocr_fallback.py` — Tesseract fallback for scanned exhibits (image-only PDFs).
- `ingestion/schema_drift.py` — flag filings whose structure doesn't match the known parser
  (`ingestion_status = 'schema_drift_flagged'`) instead of silently mis-parsing them.
- Note: haven't yet hit a filing that actually needs either of these — M1 testing only covered
  Apple's recent 10-Q/8-Ks, which are all clean text-native HTML. Worth testing against an older
  or smaller-filer's filings (more likely to have scanned exhibits) before assuming M2 works.

## Log

- **2026-07-09** — repo scaffolded (folder structure, CLAUDE.md, PROGRESS.md, pyproject.toml,
  docker-compose skeleton, .gitignore). No app code yet. Pushed to GitHub
  (github.com/Sujeethh03/ledger-lens, public).
- **2026-07-09** — M1 built and verified end-to-end against real SEC EDGAR data (see above).

## Open decisions / deviations from the dossier

- Added `FilingSection` table (not in the original dossier schema) to hold normalized
  Document/Section output ahead of chunking — the dossier's schema jumped straight to `DocChunk`
  without an intermediate representation. Recorded in `db/models.py` docstring too.
- `ingestion_status` stays `'pending'` after M1 ingestion (not `'indexed'`) — `'indexed'` is
  reserved for once chunking + embedding (M3) actually happens, to keep the status field honest.
