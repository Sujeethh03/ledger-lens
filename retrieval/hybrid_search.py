"""M3: hybrid retrieval — BM25-style lexical + dense vector, fused with RRF.

Two ranked lists come back from the same table (that's the pgvector payoff):
  lexical: websearch_to_tsquery over the generated text_tsv column, GIN index
  dense:   cosine distance over the embedding column, HNSW index
Reciprocal rank fusion (k=60, the standard constant from the Cormack et al.
paper) combines them without needing the two score scales to be comparable —
which is the entire reason to prefer RRF over weighted score sums here.

Cross-encoder reranking is deliberately not implemented yet: it needs a torch
install, and RRF-only quality should be measured first so the reranker's win
is a number, not an assumption (see PROGRESS.md).
"""

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select

from db.models import DocChunk, Document
from db.session import get_session
from retrieval.embeddings import Embedder

log = structlog.get_logger(__name__)

RRF_K = 60
CANDIDATES_PER_ARM = 30
DEFAULT_TOP_K = 8


@dataclass(frozen=True)
class SearchHit:
    chunk_id: uuid.UUID
    source_key: str  # SEC accession number / openFDA set_id — what citations resolve to
    entity_name: str  # company or drug name
    doc_type: str
    year: int | None
    section: str
    text: str
    ocr_confidence: float | None
    rrf_score: float


# NOTE (2026-07-10): a zero-hit lexical fallback (OR-ed query keywords, then a
# rarity-filtered variant) was implemented, measured, and reverted. Both
# variants let ts_rank's term-frequency bias (Postgres FTS has no IDF) fill
# the lexical list with long common-word chunks, and RRF's "present in both
# arms" boost then displaced the dense arm's correct top hits — eval q13
# (lisinopril indications) went from 4/5 correct chunks to 0/5. Full-question
# queries stay dense-carried on purpose; the lexical arm earns its keep on
# exact rare-token matches under strict websearch semantics.


def _fuse(ranked_lists: list[list[uuid.UUID]]) -> dict[uuid.UUID, float]:
    scores: dict[uuid.UUID, float] = {}
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


def hybrid_search(query: str, embedder: Embedder, top_k: int = DEFAULT_TOP_K) -> list[SearchHit]:
    query_vector = embedder.embed([query])[0]

    with get_session() as session:
        tsquery = func.websearch_to_tsquery("english", query)
        lexical_ids = list(
            session.scalars(
                select(DocChunk.id)
                .where(DocChunk.text_tsv.op("@@")(tsquery))
                .order_by(func.ts_rank(DocChunk.text_tsv, tsquery).desc())
                .limit(CANDIDATES_PER_ARM)
            )
        )
        dense_ids = list(
            session.scalars(
                select(DocChunk.id)
                .where(DocChunk.embedding.is_not(None))
                .order_by(DocChunk.embedding.cosine_distance(query_vector))
                .limit(CANDIDATES_PER_ARM)
            )
        )

        fused = _fuse([lexical_ids, dense_ids])
        top_ids = sorted(fused, key=fused.get, reverse=True)[:top_k]
        if not top_ids:
            return []

        rows = session.execute(
            select(DocChunk, Document)
            .join(Document, DocChunk.document_id == Document.id)
            .where(DocChunk.id.in_(top_ids))
        ).all()
        by_id = {chunk.id: (chunk, document) for chunk, document in rows}

        hits = [
            SearchHit(
                chunk_id=chunk.id,
                source_key=document.source_key,
                entity_name=document.entity_name,
                doc_type=document.doc_type,
                year=document.year,
                section=chunk.section,
                text=chunk.chunk_text,
                ocr_confidence=float(chunk.ocr_confidence) if chunk.ocr_confidence is not None else None,
                rrf_score=round(fused[chunk_id], 5),
            )
            for chunk_id in top_ids
            for chunk, document in [by_id[chunk_id]]
        ]
        log.info("hybrid_search_done", query=query, lexical=len(lexical_ids), dense=len(dense_ids), returned=len(hits))
        return hits
