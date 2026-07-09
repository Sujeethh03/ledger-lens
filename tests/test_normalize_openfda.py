"""Label normalization: field mapping, reading order, both label vocabularies."""

from ingestion.fetch_openfda import DrugLabelRecord
from ingestion.normalize_openfda import canonical_drug_name, normalize_drug_label
from ingestion.schema_drift import check_structure


def test_canonical_drug_name_strips_salt_forms():
    assert canonical_drug_name("WARFARIN SODIUM") == "warfarin"
    assert canonical_drug_name("METFORMIN HYDROCHLORIDE") == "metformin"
    assert canonical_drug_name("Atorvastatin Calcium") == "atorvastatin"
    assert canonical_drug_name("ibuprofen") == "ibuprofen"
    # never strips down to nothing
    assert canonical_drug_name("SODIUM") == "sodium"


def _record(raw: dict) -> DrugLabelRecord:
    return DrugLabelRecord(
        set_id="set-1",
        version="4",
        effective_time="20250617",
        brand_name="Brandex",
        generic_name="EXAMPLEDRUG",
        manufacturer="Acme Pharma",
        product_type="HUMAN PRESCRIPTION DRUG",
        raw=raw,
    )


PRESCRIPTION_RAW = {
    "boxed_warning": ["Serious bleeding risk. Monitor INR closely in all patients receiving therapy. " * 3],
    "indications_and_usage": ["Indicated for prophylaxis and treatment of venous thrombosis and embolism. " * 3],
    "drug_interactions": ["Concomitant use with NSAIDs increases bleeding risk substantially. " * 3],
    "warnings_and_cautions": ["Tissue necrosis has occurred; discontinue if lesions appear on skin. " * 3],
    "irrelevant_field": ["should never appear as a section"],
}


def test_prescription_label_maps_fields_in_reading_order():
    doc = normalize_drug_label(_record(PRESCRIPTION_RAW))
    assert doc.doc_type == "drug_label"
    assert doc.source_key == "set-1"
    assert [s.name for s in doc.sections] == [
        "Boxed Warning",
        "Indications and Usage",
        "Warnings and Precautions",
        "Drug Interactions",
    ]
    assert [s.index for s in doc.sections] == [0, 1, 2, 3]


def test_otc_label_uses_drug_facts_vocabulary():
    doc = normalize_drug_label(
        _record(
            {
                "active_ingredient": ["Ibuprofen 200 mg (NSAID) in each tablet for pain relief"],
                "purpose": ["Pain reliever / fever reducer for minor aches"],
                "warnings": ["Stomach bleeding warning: this product contains an NSAID."],
                "dosage_and_administration": ["Adults: take 1 tablet every 4 to 6 hours while symptoms persist."],
            }
        )
    )
    names = [s.name for s in doc.sections]
    assert "Purpose" in names
    assert "Active Ingredient" in names
    assert "Warnings" in names


def test_multi_part_fields_are_joined_not_truncated():
    doc = normalize_drug_label(
        _record({"indications_and_usage": ["First paragraph of the indication text here.", "Second paragraph, also kept."]})
    )
    assert "First paragraph" in doc.sections[0].text
    assert "Second paragraph" in doc.sections[0].text


def test_normalized_prescription_label_passes_drift_check():
    doc = normalize_drug_label(_record(PRESCRIPTION_RAW))
    assert check_structure(doc).ok


def test_label_without_purpose_or_indications_is_flagged():
    doc = normalize_drug_label(
        _record(
            {
                "description": ["A yellow crystalline compound, freely soluble in water and other things." * 10],
                "clinical_pharmacology": ["Extensive pharmacology text goes here to clear length thresholds." * 10],
                "overdosage": ["In case of overdose, call poison control immediately and monitor the patient." * 10],
            }
        )
    )
    result = check_structure(doc)
    assert not result.ok
    assert "what the drug is for" in result.reason


def test_junk_record_with_too_few_sections_is_flagged():
    doc = normalize_drug_label(_record({"indications_and_usage": ["Indicated for use in a certain condition." * 30]}))
    result = check_structure(doc)
    assert not result.ok
    assert "field vocabulary not recognized" in result.reason
