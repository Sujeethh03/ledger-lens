"""M5 Retriever: run hybrid search per sub-query, merge + dedupe the results.

Each hit gets a stable [Cn] label here — synthesis cites those labels, and the
guardrail validates against this exact list, so the label assignment is the
single source of truth for what "a real citation" means downstream.
"""

from dataclasses import dataclass

from retrieval.embeddings import Embedder
from retrieval.hybrid_search import SearchHit, hybrid_search

PER_QUERY_TOP_K = 5
MAX_CONTEXT_CHUNKS = 10


@dataclass(frozen=True)
class LabeledChunk:
    label: str  # "C1", "C2", ...
    hit: SearchHit


def gather_context(sub_queries: list[str], embedder: Embedder) -> list[LabeledChunk]:
    seen: set = set()
    merged: list[SearchHit] = []
    for query in sub_queries:
        for hit in hybrid_search(query, embedder, top_k=PER_QUERY_TOP_K):
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            merged.append(hit)

    merged.sort(key=lambda h: h.rrf_score, reverse=True)
    merged = merged[:MAX_CONTEXT_CHUNKS]
    return [LabeledChunk(label=f"C{i + 1}", hit=hit) for i, hit in enumerate(merged)]
