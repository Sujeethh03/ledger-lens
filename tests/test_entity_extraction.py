from ingestion.entity_extraction import extract_conditions, extract_topics, find_drug_mentions


def test_matches_supply_chain_topic():
    text = "The Company faces supply chain disruption and relies on outsourcing partners."
    topics = {m.topic for m in extract_topics(text)}
    assert "supply_chain" in topics


def test_evidence_count_orders_topics():
    text = (
        "Supply chain issues. Supply chain constraints. Component shortage persists. "
        "Some litigation exists."
    )
    matches = extract_topics(text)
    assert matches[0].topic == "supply_chain"
    assert matches[0].evidence_count >= 3


def test_min_evidence_filters_passing_mentions():
    text = "One passing mention of inflation."
    assert extract_topics(text, min_evidence=2) == []


def test_no_topics_in_unrelated_text():
    assert extract_topics("The quick brown fox jumps over the lazy dog.") == []


def test_extract_conditions_matches_clinical_and_otc_phrasing():
    prescription = "Indicated for the treatment of hypertension in adults."
    otc = "Temporarily relieves minor aches and pain and reduces fever."
    assert "hypertension" in {m.topic for m in extract_conditions(prescription)}
    matched = {m.topic for m in extract_conditions(otc)}
    assert {"pain", "fever"} <= matched


LEXICON = {
    "warfarin": "warfarin",
    "warfarin sodium": "warfarin",
    "ibuprofen": "ibuprofen",
    "aspirin": "aspirin",
}


def test_find_drug_mentions_counts_and_canonicalizes():
    text = "Concomitant use of warfarin sodium with aspirin increases bleeding risk. Warfarin requires monitoring."
    counts = find_drug_mentions(text, LEXICON, exclude="ibuprofen")
    assert counts["aspirin"] == 1
    assert counts["warfarin"] >= 2  # brand/generic variants aggregate to one canonical id


def test_find_drug_mentions_excludes_own_drug():
    text = "Warfarin interacts with ibuprofen."
    counts = find_drug_mentions(text, LEXICON, exclude="warfarin")
    assert "warfarin" not in counts
    assert counts == {"ibuprofen": 1}


def test_find_drug_mentions_respects_word_boundaries():
    # 'aspirin' must not fire inside an unrelated longer token.
    counts = find_drug_mentions("aspirinlike compounds are unrelated", LEXICON, exclude="")
    assert counts == {}
