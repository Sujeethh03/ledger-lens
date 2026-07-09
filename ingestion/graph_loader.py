"""Sync Postgres documents into the Neo4j knowledge graph — both domains.

Graph schema (scoped per CLAUDE.md — do not grow without a justifying query):
  SEC arm:
    (:Company {cik, name})
    (:Filing {accession, form_type, fiscal_year})
    (:RiskFactor {topic})
    (Company)-[:FILED]->(Filing)
    (Filing)-[:DISCUSSES {evidence_count}]->(RiskFactor)
  Drug arm (justifying multi-hop query: "which drugs that treat condition X
  have a labeled interaction with drug Y?" — a whole-corpus join top-k
  retrieval structurally can't do):
    (:Drug {name})                       name = canonical generic (salt-stripped)
    (:Condition {name})
    (Drug)-[:TREATS {evidence_count, source_set_id}]->(Condition)
    (Drug)-[:INTERACTS_WITH {evidence_count, source_set_id}]->(Drug)
        one edge per unordered pair (alphabetical direction), matched
        undirected at query time — interactions are symmetric.

No Label node: the label's set_id rides on each edge as provenance, which
keeps the node budget at 5 types across two domains.

Idempotent by construction: everything is MERGE, so re-running syncs rather
than duplicates — same property the ingestion pipeline has.
"""

import os

import structlog
from neo4j import GraphDatabase
from sqlalchemy import select

from db.models import Document, DocumentSection, SourceType
from db.session import get_session
from ingestion.entity_extraction import extract_conditions, extract_topics, find_drug_mentions

log = structlog.get_logger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "ledgerlens")

# Topics extracted only from sections likely to discuss risk — matching the
# whole filing would tag every company with every topic mentioned in passing.
RISK_SECTION_HINTS = ("risk factor", "management's discussion", "management’s discussion")
MIN_EVIDENCE = 2  # a single phrase hit in MD&A is noise; two+ is a discussion

# Same scoping idea for labels: TREATS comes only from the sections that state
# what a drug is for; INTERACTS_WITH only from sections that discuss combining
# drugs. Adverse Reactions mentions co-marketed drugs constantly — off limits.
INDICATION_SECTION_HINTS = ("indications and usage", "purpose")
INTERACTION_SECTION_HINTS = ("drug interactions", "do not use", "ask a doctor", "warnings")


def load_graph() -> dict[str, int]:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    summary = {
        "companies": 0,
        "filings": 0,
        "discusses_edges": 0,
        "drugs": 0,
        "treats_edges": 0,
        "interacts_edges": 0,
    }

    with get_session() as session, driver.session() as neo:
        filings = session.scalars(
            select(Document).where(
                Document.ingestion_status == "indexed",
                Document.source_type == SourceType.SEC_FILING.value,
            )
        ).all()

        for filing in filings:
            neo.run(
                """
                MERGE (c:Company {cik: $cik})
                  ON CREATE SET c.name = $name
                MERGE (f:Filing {accession: $accession})
                  ON CREATE SET f.form_type = $form_type, f.fiscal_year = $fiscal_year
                MERGE (c)-[:FILED]->(f)
                """,
                cik=filing.entity_id,
                name=filing.entity_name,
                accession=filing.source_key,
                form_type=filing.doc_type,
                fiscal_year=filing.year,
            )
            summary["filings"] += 1

            sections = session.scalars(
                select(DocumentSection).where(DocumentSection.document_id == filing.id)
            ).all()
            risk_text = "\n".join(
                s.text for s in sections if any(hint in s.section_name.lower() for hint in RISK_SECTION_HINTS)
            )
            if not risk_text:
                continue

            for match in extract_topics(risk_text, min_evidence=MIN_EVIDENCE):
                neo.run(
                    """
                    MATCH (f:Filing {accession: $accession})
                    MERGE (r:RiskFactor {topic: $topic})
                    MERGE (f)-[d:DISCUSSES]->(r)
                      SET d.evidence_count = $evidence
                    """,
                    accession=filing.source_key,
                    topic=match.topic,
                    evidence=match.evidence_count,
                )
                summary["discusses_edges"] += 1

        result = neo.run("MATCH (c:Company) RETURN count(c) AS n").single()
        summary["companies"] = result["n"]

        _load_drug_arm(session, neo, summary)

    driver.close()
    log.info("graph_loaded", **summary)
    return summary


def _sections_matching(session, document_id, hints: tuple[str, ...]) -> str:
    sections = session.scalars(
        select(DocumentSection).where(DocumentSection.document_id == document_id)
    ).all()
    return "\n".join(s.text for s in sections if any(h in s.section_name.lower() for h in hints))


def _load_drug_arm(session, neo, summary: dict[str, int]) -> None:
    labels = session.scalars(
        select(Document).where(
            Document.ingestion_status == "indexed",
            Document.source_type == SourceType.DRUG_LABEL.value,
        )
    ).all()
    if not labels:
        return

    # Lexicon from the corpus itself: searchable name -> canonical drug id.
    lexicon: dict[str, str] = {}
    for label in labels:
        meta = label.meta or {}
        lexicon[label.entity_id] = label.entity_id
        for name in (meta.get("generic_name", ""), meta.get("brand_name", "")):
            name = name.strip().lower()
            if len(name) >= 4:  # 3-letter brand names create word-boundary noise
                lexicon[name] = label.entity_id

    for label in labels:
        # Plain SET, not ON CREATE SET: an interaction edge from another label
        # may have MERGEd this Drug node first (bare, no display_name), and
        # the drug's own label is the authority on its display name.
        neo.run(
            "MERGE (d:Drug {name: $name}) SET d.display_name = $display",
            name=label.entity_id,
            display=label.entity_name,
        )

        indications = _sections_matching(session, label.id, INDICATION_SECTION_HINTS)
        for match in extract_conditions(indications):
            neo.run(
                """
                MATCH (d:Drug {name: $drug})
                MERGE (c:Condition {name: $condition})
                MERGE (d)-[t:TREATS]->(c)
                  SET t.evidence_count = $evidence, t.source_set_id = $set_id
                """,
                drug=label.entity_id,
                condition=match.topic,
                evidence=match.evidence_count,
                set_id=label.source_key,
            )
            summary["treats_edges"] += 1

        interactions_text = _sections_matching(session, label.id, INTERACTION_SECTION_HINTS)
        for other, count in find_drug_mentions(interactions_text, lexicon, exclude=label.entity_id).items():
            a, b = sorted([label.entity_id, other])  # one edge per unordered pair
            neo.run(
                """
                MERGE (x:Drug {name: $a})
                MERGE (y:Drug {name: $b})
                MERGE (x)-[i:INTERACTS_WITH]->(y)
                  SET i.evidence_count = $evidence, i.source_set_id = $set_id
                """,
                a=a,
                b=b,
                evidence=count,
                set_id=label.source_key,
            )
            summary["interacts_edges"] += 1

    summary["drugs"] = neo.run("MATCH (d:Drug) RETURN count(d) AS n").single()["n"]
