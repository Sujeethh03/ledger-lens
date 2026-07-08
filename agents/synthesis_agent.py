"""M5 Synthesis: draft an answer where every sentence carries a [Cn] citation.

The citation contract is enforced downstream by the guardrail — this agent is
*asked* to comply, the guardrail *checks* compliance. Keeping the writer and
the checker separate is the dossier's core hallucination-prevention design.
"""

from pydantic import BaseModel

from agents.llm import SYNTHESIS_MODEL, Chat, structured_call
from agents.retriever_agent import LabeledChunk

SYSTEM = """You answer questions about SEC filings using ONLY the provided source chunks.
Return JSON: {"answer": "..."}.
Hard rules:
- Every sentence in the answer MUST end with at least one citation like [C1] or [C2][C3].
- Only cite labels that appear in the provided sources.
- If the sources do not contain the information needed, say exactly that (with no citation needed
  for that single admission sentence is NOT allowed either — instead return:
  {"answer": "INSUFFICIENT_EVIDENCE"}).
- Never use outside knowledge, even if you are confident."""


class Draft(BaseModel):
    answer: str


def _render_context(chunks: list[LabeledChunk]) -> str:
    blocks = []
    for c in chunks:
        h = c.hit
        ocr_note = f" (OCR text, confidence {h.ocr_confidence})" if h.ocr_confidence is not None else ""
        blocks.append(
            f"[{c.label}] {h.company_name} {h.form_type} FY{h.fiscal_year} — {h.section}{ocr_note}\n{h.text}"
        )
    return "\n\n---\n\n".join(blocks)


def synthesize(question: str, chunks: list[LabeledChunk], chat: Chat, feedback: str | None = None) -> Draft:
    user = f"Sources:\n\n{_render_context(chunks)}\n\nQuestion: {question}"
    if feedback:
        user += (
            f"\n\nYour previous draft violated the citation rules:\n{feedback}\n"
            "Rewrite the answer so EVERY sentence ends with a valid [Cn] citation."
        )
    return structured_call(chat, SYSTEM, user, Draft, model=SYNTHESIS_MODEL)
