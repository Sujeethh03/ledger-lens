# Progress

## Status: DEPLOYED — live cloud URL, eval at 1.00/1.00/0.94, q3 root-caused and fixed

**Live URL: https://api-production-efa5.up.railway.app** (Railway, deployed 2026-07-10).
All five DEMO.md arc steps verified against it: cited drug answer, graph multi-hop
("Aspirin and Ibuprofen [C1]"), refusal (acetaminophen → INSUFFICIENT_EVIDENCE), SEC
answer, async ingestion. See DEMO.md Path B for layout + the Neo4j-volume caveat.

**What GroundedAI is now:** a verifiable agentic retrieval platform with two document sources —
FDA drug labels (headline) and SEC filings (kept as proof of source-agnosticism). Renamed from
"Ledger Lens" on 2026-07-10; see CLAUDE.md's history note for the recorded rationale.

**Eval numbers (2026-07-10 evening, 20 cases, two consecutive runs identical):**
- **refusal_correctness 1.00, citation_validity 1.00, keyword_coverage 0.94.**
- The jump from 0.95/1.00/0.88: q3's "over-refusal" was root-caused to a **guardrail false
  positive** — the sentence splitter broke at "U.S. District Court" (period+space+capital),
  creating an uncited fragment from a fully-cited sentence. Fixed with a negative lookbehind
  for single-capital abbreviations + regression tests. A synthesis-prompt rule ("each sentence
  must carry its own [Cn], even mid-paragraph") killed the other flaky failure mode.
- These numbers fill the resume bullet placeholders. Re-run with `python -m evals.run_eval`.

**Retrieval experiment, measured and reverted (2026-07-10):** the recorded candidate fix for
the lexical arm's 0-hits-on-full-questions (OR-ed keyword fallback, then a rarity-filtered
variant) was implemented and **made retrieval worse**: ts_rank has no IDF, so common-word
matches flooded the lexical list and RRF's both-arms boost displaced the dense arm's correct
top-5 (eval q13 lisinopril-indications went from 4/5 correct chunks to 0/5, three consecutive
eval failures). Reverted; the decision + measurement live in a NOTE in
`retrieval/hybrid_search.py`. Full-question queries stay dense-carried by design. This is a
good interview story: the eval harness caught a plausible "improvement" being a regression.

**Corpus (local DB):** 12 SEC filings (Apple, JPMorgan, Tesla) → 156 chunks; 17 drug labels
(warfarin, aspirin, ibuprofen, lisinopril, metformin, atorvastatin, omeprazole, fluoxetine,
amlodipine) → 425 chunks. Graph: 3 Companies, 12 Filings, 18 DISCUSSES; 9 Drugs, 11 Conditions
max, 47 TREATS, 22 INTERACTS_WITH.

## The pivot, in commit order (all landed 2026-07-10)

1. **Schema generalization** (`a930058`): filings → source-agnostic `documents` +
   `document_sections` with `source_type` ('sec_filing'|'drug_label'), generic
   entity_id/entity_name/doc_type/source_key/year, `meta` JSONB. Hand-written Alembic
   migration incl. constraint/index renames; live data survived (verified).
2. **openFDA source adapter** (`99b58da`): fetcher (rate-limited, retry/backoff,
   404-NOT_FOUND = "no such drug" never retried, dedupe to newest per set_id), normalizer
   (JSON fields → unified sections, PLR + OTC vocabularies, `canonical_drug_name` strips salt
   suffixes so WARFARIN SODIUM ≡ warfarin), drift rules (label must have ≥3 sections and
   Indications/Purpose), pipeline with two-tier idempotency (same version skips, newer version
   replaces via cascade), Celery task, POST /api/v1/ingest/drug/{name}. Proven live end-to-end
   including through the worker (amlodipine).
3. **Drug graph arm** (`fb257bd`): Drug + Condition nodes only (5 node types total, per-domain
   budget in CLAUDE.md). Deterministic extraction: drug-name lexicon built from the corpus's
   own openFDA metadata; 11-condition taxonomy. INTERACTS_WITH = one edge per unordered pair,
   queried undirected, source set_id as provenance. Three new parameterized Cypher lookups in
   graph_agent incl. the justifying multi-hop (treats X AND interacts with Y); planner prompt
   is now domain-neutral with a two-arg lookup (arg + arg2). **Live proof:** "Which drugs that
   treat pain have a labeled interaction with warfarin?" → "Aspirin and Ibuprofen [C1]" via a
   zero-sub-query pure-graph plan.
   - Bug found & fixed en route: `MERGE ... ON CREATE SET display_name` left the property null
     when an interaction edge created the Drug node before its own label was processed →
     loader now plain-SETs, Cypher coalesce()s.
4. **Eval doubled** (see numbers above).
5. **Rename to GroundedAI**: pyproject (grounded-ai 0.2.0), README, CLAUDE.md, this file,
   DEMO.md, .env.example, scripts/ingest_drug.py CLI added. API surface renamed:
   /api/v1/ingest/sec/{cik}, /api/v1/ingest/drug/{name}, /api/v1/documents (?source_type=).

## What was already built (pre-pivot, all still working)

- **M1/M2 SEC ingestion**: EDGAR client (8 req/s, tenacity retry, 403=config error), HTML→Item
  sections, OCR fallback (real Tesseract, confidence propagated to chunks), drift flagging.
- **M3 retrieval**: paragraph-packing chunker (~3000/300), OpenAI embeddings behind a Protocol,
  one doc_chunks table serving tsvector+GIN and pgvector+HNSW, RRF fusion (k=60).
- **M5 agents**: JSON-mode + pydantic + one-repair LLM harness; planner → retriever ([Cn]
  labels) → synthesis (every-sentence-cited or INSUFFICIENT_EVIDENCE) → deterministic guardrail;
  LangGraph state machine with zero-chunk early refusal, one revision loop, hard refusal.
- **M6 evals + CI**: deterministic harness (gate 0.7 refusal, tripwire 1.0 citation); CI runs
  ruff + tests with tesseract.
- **API layer**: /healthz, /readyz (real dependency checks), /metrics (Prometheus), async
  ingest via Celery/Redis (acks_late, prefetch=1), task status, query endpoint.
- **Deploy prep**: working Dockerfiles, .dockerignore, read-only demo mode (INGEST_ENABLED).

**64 tests passing** (was 46 pre-pivot), ruff clean.

## NOT done yet (be honest about these)

1. **Neo4j cloud volume**: the free-tier 500MB volume couldn't hold Neo4j 5's default
   2×256MB tx-log preallocation ("No space left on device" crashloop). Preallocation is now
   disabled via service env vars (`NEO4J_db_tx__log_preallocate=false`, rotation 16MiB,
   retention "1 files"), but the broken volume sits in Railway's 48h pending-deletion state,
   so Neo4j currently runs **volume-less** (graph is ephemeral; rebuild =
   `ssh railway-api 'python -m scripts.load_graph'`, ~5s). **After ~2026-07-12**: attach a
   fresh volume (`railway volume --service <neo4j-id> add --mount-path /data`), redeploy,
   re-run load_graph.
2. **Cross-encoder reranking**: baseline numbers exist; needs torch/sentence-transformers.
3. **RAGAS faithfulness** on top of the deterministic harness.
4. **Semantic support-checking in guardrail**: coverage-only today; "does the cited chunk
   actually support the sentence" needs a second LLM pass — next hardening step.
5. **Lexical arm returns 0 hits on full-question queries** — retained as a *known,
   deliberate* limitation after the keyword-fallback experiment measurably hurt top-5
   (see status section above). Dense arm carries full questions; revisit only alongside
   reranking.
6. **Frontend**: a deliberate scope-down from the dossier's Next.js + WebSocket plan — a
   single-file vanilla HTML/JS citation viewer (`frontend/index.html`) is live at `GET /`,
   served by FastAPI itself. Clickable [Cn] chips jump to source cards with real chunk text
   (`/api/v1/query` now returns a 700-char `snippet` per source). Rationale: every line must
   be defensible by a fresher, and the citation guarantee is the thing worth making visible.
   Streaming + framework UI remain unstarted dossier scope.
7. **Condition taxonomy is 11 topics** — fine for 9 drugs; revisit if the corpus grows.
8. **Old commits carry a wrong author email** (`satya@…MacBook-Pro.local`) — they won't count
   toward Sujeeth's GitHub profile. Fixing needs a history rewrite + force push (Sujeeth's
   call). Identity is corrected globally as of 2026-07-10; new commits are fine.
9. **Cloud corpus is bigger than local** (38 docs / 758 chunks vs 29 / 581): cloud drug
   ingestion used default limit=3 per drug → 26 labels vs 17 local. Harmless (same drugs,
   more label versions), but numbers differ between environments.

## Next session should

1. If past 2026-07-12: re-attach the Neo4j volume (see item 1 above).
2. **Sujeeth must read the whole codebase and be quizzed on it** — the repo is further ahead
   of his ability to defend it than ever after the pivot + deploy. Priority reading order in
   DEMO.md. The q3 guardrail-splitter story and the reverted retrieval experiment are
   prime interview material — read both diffs.
3. Optional hardening: semantic support-check in the guardrail (item 4) is the next
   architecture-level step.

## Log

- **2026-07-09** — repo scaffolded as ledger-lens, pushed to github.com/Sujeethh03/ledger-lens.
  M1-M6 + API layer built and live-proven against real EDGAR data; first eval numbers.
- **2026-07-09** — deploy prep: Dockerfiles verified, DEMO.md runbook, read-only demo mode.
- **2026-07-10** — **GroundedAI pivot**: schema generalized to multi-source documents; openFDA
  drug-label adapter built + live-proven; drug graph arm (Drug/Condition, TREATS/INTERACTS_WITH)
  with live multi-hop; eval set doubled to 20 (0.95/1.00/0.88); renamed from Ledger Lens.
- **2026-07-10 (later)** — rename finished everywhere: GitHub repo is
  github.com/Sujeethh03/grounded-ai (redirects preserved), local folder → ~/grounded-ai,
  venv rebuilt (old one had absolute shebangs), 64 tests green after the move. Git identity
  set to Sujeeth's real email for future commits (older commits still carry the .local email).
  Railway CLI installed; deploy blocked only on interactive `railway login`.
- **2026-07-10 (evening)** — **DEPLOYED to Railway**: pgvector Postgres + Redis + Neo4j +
  one combined api/worker service (free-tier 5-service cap → uvicorn + Celery in one
  container, `infra/railway.Dockerfile`; start script creates the pgvector extension and
  runs Alembic idempotently). Volumes attached to Postgres/Redis; Neo4j volume saga in
  "NOT done". Corpus ingested **through the deployed API itself** (9 drugs + 3 CIKs → 38
  docs, 758 chunks), graph loaded via `railway ssh`. All five demo-arc steps verified live.
  **q3 root-caused**: guardrail sentence-splitter false positive on "U.S." → fixed +
  regression tests; synthesis prompt hardened; **eval now 1.00/1.00/0.94** (two identical
  consecutive runs). Lexical keyword-fallback experiment measured, found harmful (q13),
  reverted with the reasoning recorded in `retrieval/hybrid_search.py`. 66 tests green.
- **2026-07-10 (night)** — **citation-viewer UI shipped**: one vanilla HTML/JS file at
  `GET /` (see "NOT done" #6 for the scope-down rationale), `snippet` added to query
  sources, verified locally end-to-end and redeployed to Railway. 67 tests green.

## Open decisions / deviations from the dossier

- **The pivot itself** — recorded in CLAUDE.md's history note (drug labels headline, SEC kept).
- `DocumentSection` table (normalized text needs a home before chunking) — M1, name updated.
- `ingestion_status` lifecycle: pending → indexed only after real chunking/embedding — M1.
- Custom eval harness instead of RAGAS (deterministic first, judged metrics later) — M6.
- Guardrail is coverage-only, not support-checking, for now — M5.
- Graph extraction stays deterministic (lexicon/taxonomy, no LLM) in both domains — reliability
  over flexibility for graph ground truth.
- Postgres role/db still named `ledgerlens` post-rename — implementation detail, not worth a
  migration.
- pip on this machine needs `--index-url https://pypi.org/simple` (global CodeArtifact index
  with expired token; left untouched deliberately).
