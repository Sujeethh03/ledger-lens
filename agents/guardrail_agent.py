"""M5 Guardrail: deterministic citation-coverage check on the draft answer.

Checks (no LLM involved — that's the point):
1. Every [Cn] in the answer refers to a chunk that was actually retrieved.
2. Every sentence carries at least one citation marker.

What this deliberately does NOT do yet (recorded honestly): verify that the
cited chunk semantically *supports* the sentence — that needs a second LLM
pass and is the known next hardening step. Coverage checking alone already
kills the most common failure (confident prose citing nothing).
"""

import re
from dataclasses import dataclass, field

CITATION_RE = re.compile(r"\[C(\d+)\]")
# Sentence split that won't break on "Inc." / "No. 5" style abbreviations badly
# enough to matter for a coverage check.
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class GuardrailVerdict:
    ok: bool
    violations: list[str] = field(default_factory=list)


def check_citations(answer: str, valid_labels: set[str]) -> GuardrailVerdict:
    if answer.strip() == INSUFFICIENT:
        return GuardrailVerdict(ok=True)  # honest refusal is a valid outcome

    violations: list[str] = []

    for match in CITATION_RE.finditer(answer):
        label = f"C{match.group(1)}"
        if label not in valid_labels:
            violations.append(f"cites [{label}] which was not among the retrieved sources")

    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(answer.strip()) if s.strip()]
    for sentence in sentences:
        if not CITATION_RE.search(sentence):
            preview = sentence[:80] + ("..." if len(sentence) > 80 else "")
            violations.append(f'uncited sentence: "{preview}"')

    return GuardrailVerdict(ok=not violations, violations=violations)
