from agents.guardrail_agent import INSUFFICIENT, check_citations

LABELS = {"C1", "C2", "C3"}


def test_fully_cited_answer_passes():
    answer = "Revenue grew 5% year over year [C1]. Supply chain risk was flagged [C2][C3]."
    assert check_citations(answer, LABELS).ok


def test_uncited_sentence_is_a_violation():
    answer = "Revenue grew 5% [C1]. The company is clearly doing very well."
    verdict = check_citations(answer, LABELS)
    assert not verdict.ok
    assert any("uncited sentence" in v for v in verdict.violations)


def test_hallucinated_citation_label_is_a_violation():
    answer = "Margins compressed in Q2 [C9]."
    verdict = check_citations(answer, LABELS)
    assert not verdict.ok
    assert any("[C9]" in v for v in verdict.violations)


def test_insufficient_evidence_refusal_is_valid():
    assert check_citations(INSUFFICIENT, LABELS).ok
    assert check_citations(f"  {INSUFFICIENT}  ", LABELS).ok


def test_us_style_abbreviation_does_not_split_sentence():
    # Regression: "U.S. District" used to split after "U.S.", leaving an
    # uncited fragment that made fully-cited legal answers refuse (eval q3).
    answer = "Epic Games, Inc. sued in the U.S. District Court for the Northern District [C1]."
    assert check_citations(answer, LABELS).ok


def test_abbreviation_fix_still_catches_real_uncited_sentence():
    answer = "The case was heard in the U.S. Supreme Court [C1]. Apple will certainly win."
    verdict = check_citations(answer, LABELS)
    assert not verdict.ok
    assert any("uncited sentence" in v for v in verdict.violations)
