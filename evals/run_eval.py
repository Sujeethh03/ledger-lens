"""M6 eval harness: run the golden QA set through the full agent pipeline.

Metrics (all computed from the pipeline's actual outputs, no self-grading):
- refusal_correctness: unanswerable questions must be refused; answerable
  ones must not be. The single most important number — it measures whether
  the system knows what it doesn't know.
- citation_validity: re-run the guardrail check on every returned answer;
  must be 1.0 by construction (the pipeline refuses otherwise) — if it ever
  isn't, the pipeline itself is broken, so this is a tripwire, not a score.
- keyword_coverage: fraction of expected keywords present in answerable
  answers — a cheap relevance proxy, deliberately loose.

Deviation from the dossier (recorded): this is a custom harness, not RAGAS.
RAGAS adds heavy deps and LLM-judged metrics; the plan is to measure with
this deterministic harness first and layer RAGAS faithfulness scoring on top
later, so the judged metrics land on an already-instrumented base.

Usage:
    python -m evals.run_eval            # full pipeline, needs OPENAI_API_KEY
"""

import json
from dataclasses import dataclass
from pathlib import Path

import structlog
from dotenv import load_dotenv

load_dotenv()

from agents.graph import ask  # noqa: E402
from agents.guardrail_agent import check_citations  # noqa: E402
from agents.llm import OpenAIChat  # noqa: E402
from retrieval.embeddings import OpenAIEmbedder  # noqa: E402

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
log = structlog.get_logger(__name__)

GOLDEN_PATH = Path(__file__).parent / "golden_qa.jsonl"
REFUSAL_CORRECTNESS_GATE = 0.7  # starting bar, tighten as the corpus grows


@dataclass
class CaseResult:
    case_id: str
    category: str
    refused: bool
    refusal_correct: bool
    citation_valid: bool
    keyword_hits: int
    keyword_total: int


def run() -> int:
    cases = [json.loads(line) for line in GOLDEN_PATH.read_text().splitlines() if line.strip()]
    chat, embedder = OpenAIChat(), OpenAIEmbedder()

    results: list[CaseResult] = []
    for case in cases:
        outcome = ask(case["question"], chat, embedder)
        labels = {c.label for c in outcome.citations}
        expected_refusal = case["category"] == "unanswerable"

        keywords = case.get("expected_keywords", [])
        hits = sum(1 for kw in keywords if kw.lower() in outcome.answer.lower()) if not outcome.refused else 0

        result = CaseResult(
            case_id=case["id"],
            category=case["category"],
            refused=outcome.refused,
            refusal_correct=(outcome.refused == expected_refusal),
            citation_valid=outcome.refused or check_citations(outcome.answer, labels).ok,
            keyword_hits=hits,
            keyword_total=len(keywords),
        )
        results.append(result)
        log.info(
            "case_done",
            id=result.case_id,
            refused=result.refused,
            refusal_correct=result.refusal_correct,
            keywords=f"{result.keyword_hits}/{result.keyword_total}",
        )

    n = len(results)
    refusal_correctness = sum(r.refusal_correct for r in results) / n
    citation_validity = sum(r.citation_valid for r in results) / n
    kw_total = sum(r.keyword_total for r in results)
    keyword_coverage = (sum(r.keyword_hits for r in results) / kw_total) if kw_total else 1.0

    print(f"\n{'=' * 60}")
    print(f"cases:               {n}")
    print(f"refusal_correctness: {refusal_correctness:.2f}  (gate: {REFUSAL_CORRECTNESS_GATE})")
    print(f"citation_validity:   {citation_validity:.2f}  (tripwire: must be 1.00)")
    print(f"keyword_coverage:    {keyword_coverage:.2f}")
    print(f"{'=' * 60}\n")

    failed = citation_validity < 1.0 or refusal_correctness < REFUSAL_CORRECTNESS_GATE
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
