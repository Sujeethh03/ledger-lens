"""M2: detect documents whose parsed structure doesn't match what we expect.

The dossier frames this as "the source changes document structure under you" —
each normalizer assumes a structure, so the concrete, testable version of
drift detection here is structural expectation checking per document type:

- 10-K / 10-Q filings are Item-structured by regulation. If normalization
  produced no Item sections (just the "Full Text" fallback) or suspiciously
  few, the parser likely failed to recognize the document's structure —
  flag it rather than silently indexing a bad parse.
- 8-K filings are short and sometimes legitimately unstructured, so only an
  effectively-empty parse is drift for those.
- Drug labels must state what the drug is *for* — every legitimate label
  carries "Indications and Usage" (prescription) or "Purpose" (OTC Drug
  Facts). A normalized label missing both means openFDA's field vocabulary
  moved under us (or the record is junk); either way it must not be indexed.

Flagged documents get ingestion_status='schema_drift_flagged' and keep their
raw sections so a human (or a later, smarter parser) can triage — the status
lifecycle is the point: never silently mis-parse.
"""

from dataclasses import dataclass

from ingestion.normalize import NormalizedDocument

# 10-Ks have ~15 mandated Items, 10-Qs ~10. Seeing fewer than this many parsed
# sections means the header regex almost certainly missed the document's real
# structure (threshold deliberately loose — false *negatives* here are worse
# than false positives, since a flag just routes to review, not deletion).
MIN_SECTIONS = {"10-K": 5, "10-Q": 4, "8-K": 1}
MIN_TOTAL_CHARS = 500  # anything below this parsed from a real document is a failed parse

DRUG_LABEL_DOC_TYPE = "drug_label"
DRUG_LABEL_MIN_SECTIONS = 3
DRUG_LABEL_PURPOSE_SECTIONS = {"Indications and Usage", "Purpose"}


@dataclass(frozen=True)
class DriftCheck:
    ok: bool
    reason: str | None = None


def _check_drug_label(doc: NormalizedDocument) -> DriftCheck:
    if len(doc.sections) < DRUG_LABEL_MIN_SECTIONS:
        return DriftCheck(
            ok=False,
            reason=(
                f"drug label normalized into {len(doc.sections)} sections, "
                f"expected >= {DRUG_LABEL_MIN_SECTIONS} — openFDA field vocabulary not recognized"
            ),
        )
    names = {s.name for s in doc.sections}
    if not names & DRUG_LABEL_PURPOSE_SECTIONS:
        return DriftCheck(
            ok=False,
            reason="drug label has neither 'Indications and Usage' nor 'Purpose' — every real label states what the drug is for",
        )
    return DriftCheck(ok=True)


def check_structure(doc: NormalizedDocument) -> DriftCheck:
    total_chars = sum(len(s.text) for s in doc.sections)
    if total_chars < MIN_TOTAL_CHARS:
        return DriftCheck(ok=False, reason=f"near-empty parse: {total_chars} chars total")

    if doc.doc_type == DRUG_LABEL_DOC_TYPE:
        return _check_drug_label(doc)

    expected_min = MIN_SECTIONS.get(doc.doc_type)
    if expected_min is None:
        # Unknown document type is itself a kind of drift — we're parsing
        # something the pipeline was never designed for.
        return DriftCheck(ok=False, reason=f"unknown document type: {doc.doc_type}")

    item_sections = [s for s in doc.sections if s.name != "Full Text"]
    if len(item_sections) < expected_min:
        return DriftCheck(
            ok=False,
            reason=(
                f"{doc.doc_type} parsed into {len(item_sections)} Item sections, "
                f"expected >= {expected_min} — header structure not recognized"
            ),
        )
    return DriftCheck(ok=True)
