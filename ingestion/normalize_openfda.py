"""Normalize an openFDA drug-label JSON record into the unified Document/Section shape.

Where the SEC normalizer parses structure *out of* HTML, openFDA labels arrive
pre-sectioned as JSON fields — so normalization here is field mapping, not
parsing. The mess lives elsewhere: which fields are present varies wildly by
label format. Modern prescription labels (PLR, 2006+) carry
`warnings_and_cautions`; older ones carry `warnings`; OTC labels carry
`purpose`/`active_ingredient` and often no interactions section at all. The
mapping below covers both vocabularies and keeps the label's clinical reading
order; the schema-drift checker (not this module) decides whether what came
out is structurally plausible.

Every value is a list of strings in openFDA (SPL allows repeated sections) —
they're joined, not truncated.
"""

from ingestion.fetch_openfda import DrugLabelRecord
from ingestion.normalize import NormalizedDocument, NormalizedSection

DRUG_LABEL_DOC_TYPE = "drug_label"

# (openFDA field, display name) in clinical reading order. Covers both the
# PLR prescription vocabulary and the OTC Drug Facts vocabulary — a given
# label populates a subset of these, never all.
LABEL_SECTION_FIELDS: list[tuple[str, str]] = [
    ("boxed_warning", "Boxed Warning"),
    ("active_ingredient", "Active Ingredient"),
    ("purpose", "Purpose"),
    ("indications_and_usage", "Indications and Usage"),
    ("dosage_and_administration", "Dosage and Administration"),
    ("contraindications", "Contraindications"),
    ("warnings_and_cautions", "Warnings and Precautions"),
    ("warnings", "Warnings"),
    ("do_not_use", "Do Not Use"),
    ("ask_doctor", "Ask a Doctor Before Use"),
    ("stop_use", "Stop Use"),
    ("drug_interactions", "Drug Interactions"),
    ("adverse_reactions", "Adverse Reactions"),
    ("use_in_specific_populations", "Use in Specific Populations"),
    ("pregnancy", "Pregnancy"),
    ("overdosage", "Overdosage"),
    ("description", "Description"),
    ("clinical_pharmacology", "Clinical Pharmacology"),
]

MIN_SECTION_CHARS = 20  # some real OTC fields are one short sentence; keep those

# Salt/ester suffixes on generic names ("WARFARIN SODIUM", "METFORMIN
# HYDROCHLORIDE"). The active moiety is the drug identity — one Drug node per
# moiety, not per salt form — so entity_id strips these deterministically.
SALT_SUFFIXES = {
    "sodium", "potassium", "calcium", "magnesium", "hydrochloride", "hcl",
    "sulfate", "citrate", "tartrate", "bitartrate", "maleate", "mesylate",
    "besylate", "succinate", "fumarate", "acetate", "phosphate", "nitrate",
    "carbonate", "dihydrate", "monohydrate", "anhydrous",
}


def canonical_drug_name(generic_name: str) -> str:
    """'WARFARIN SODIUM' -> 'warfarin'; 'METFORMIN HYDROCHLORIDE' -> 'metformin'."""
    tokens = generic_name.strip().lower().split()
    while len(tokens) > 1 and tokens[-1] in SALT_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _field_text(raw: dict, field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, list):
        return ""
    return "\n\n".join(part.strip() for part in value if isinstance(part, str) and part.strip())


def normalize_drug_label(record: DrugLabelRecord) -> NormalizedDocument:
    sections: list[NormalizedSection] = []
    for field_name, display_name in LABEL_SECTION_FIELDS:
        text = _field_text(record.raw, field_name)
        if len(text) < MIN_SECTION_CHARS:
            continue
        sections.append(NormalizedSection(name=display_name, index=len(sections), text=text))

    return NormalizedDocument(
        source_key=record.set_id,
        doc_type=DRUG_LABEL_DOC_TYPE,
        sections=sections,
    )
