"""M4 Graph agent: deterministic Cypher lookups exposed as citable sources.

No LLM writes Cypher here — the planner picks a topic from the fixed taxonomy
and these functions run parameterized queries. That's a deliberate trade-off:
free-form text-to-Cypher is flexible but injects an unauditable generation
step into what's supposed to be the *reliable* arm of retrieval. A fact like
"3 companies discuss supply_chain risk" must be exactly what the graph says.

Graph facts are rendered as SearchHit-shaped sources (section="Knowledge
Graph") so synthesis cites them with the same [Cn] labels and the guardrail
validates them identically — one citation system, two evidence types.
"""

import os
import uuid

import structlog

from ingestion.entity_extraction import CONDITION_TAXONOMY, RISK_TAXONOMY
from ingestion.normalize_openfda import canonical_drug_name
from retrieval.hybrid_search import SearchHit

log = structlog.get_logger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "ledgerlens"))

VALID_TOPICS = set(RISK_TAXONOMY)
VALID_CONDITIONS = set(CONDITION_TAXONOMY)


def _run(query: str, **params) -> list[dict]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    try:
        with driver.session() as session:
            return [dict(record) for record in session.run(query, **params)]
    finally:
        driver.close()


def _fact_hit(text: str) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(),
        source_key="knowledge-graph",
        entity_name="(graph fact)",
        doc_type="KG",
        year=None,
        section="Knowledge Graph",
        text=text,
        ocr_confidence=None,
        rrf_score=1.0,  # graph facts are exact, rank them first
    )


def companies_discussing(topic: str) -> list[SearchHit]:
    if topic not in VALID_TOPICS:
        return []
    rows = _run(
        """
        MATCH (c:Company)-[:FILED]->(f:Filing)-[d:DISCUSSES]->(r:RiskFactor {topic: $topic})
        RETURN c.name AS company, collect(DISTINCT f.form_type + ' FY' + toString(f.fiscal_year)) AS filings
        ORDER BY company
        """,
        topic=topic,
    )
    if not rows:
        return []
    lines = [f"{r['company']} (in {', '.join(r['filings'])})" for r in rows]
    text = f"Knowledge graph: companies whose filings discuss {topic} risk — " + "; ".join(lines)
    return [_fact_hit(text)]


def topics_for_company(company_name_fragment: str) -> list[SearchHit]:
    rows = _run(
        """
        MATCH (c:Company)-[:FILED]->(f:Filing)-[d:DISCUSSES]->(r:RiskFactor)
        WHERE toLower(c.name) CONTAINS toLower($fragment)
        RETURN r.topic AS topic, sum(d.evidence_count) AS evidence
        ORDER BY evidence DESC
        """,
        fragment=company_name_fragment,
    )
    if not rows:
        return []
    listing = ", ".join(f"{r['topic']} (evidence {r['evidence']})" for r in rows)
    text = f"Knowledge graph: risk topics discussed in {company_name_fragment} filings — {listing}"
    return [_fact_hit(text)]


def companies_sharing_topics() -> list[SearchHit]:
    rows = _run(
        """
        MATCH (a:Company)-[:FILED]->(:Filing)-[:DISCUSSES]->(r:RiskFactor)
              <-[:DISCUSSES]-(:Filing)<-[:FILED]-(b:Company)
        WHERE a.cik < b.cik
        RETURN a.name AS company_a, b.name AS company_b, collect(DISTINCT r.topic) AS shared
        ORDER BY size(shared) DESC LIMIT 5
        """
    )
    if not rows:
        return []
    lines = [f"{r['company_a']} & {r['company_b']}: {', '.join(r['shared'])}" for r in rows]
    text = "Knowledge graph: companies whose filings discuss the same risk topics — " + "; ".join(lines)
    return [_fact_hit(text)]


# ---------------------------------------------------------------------------
# Drug-label arm — same contract: parameterized Cypher only, facts as [Cn]
# sources. INTERACTS_WITH is stored one edge per unordered pair, so every
# query matches it undirected.
# ---------------------------------------------------------------------------


def drugs_interacting_with(drug_name: str) -> list[SearchHit]:
    drug = canonical_drug_name(drug_name)
    rows = _run(
        """
        MATCH (d:Drug {name: $drug})-[i:INTERACTS_WITH]-(other:Drug)
        RETURN coalesce(other.display_name, other.name) AS drug, i.source_set_id AS set_id
        ORDER BY drug
        """,
        drug=drug,
    )
    if not rows:
        return []
    listing = "; ".join(f"{r['drug']} (per label {r['set_id']})" for r in rows)
    text = f"Knowledge graph: drugs with a labeled interaction with {drug} — {listing}"
    return [_fact_hit(text)]


def drugs_treating(condition: str) -> list[SearchHit]:
    if condition not in VALID_CONDITIONS:
        return []
    rows = _run(
        """
        MATCH (d:Drug)-[t:TREATS]->(c:Condition {name: $condition})
        RETURN coalesce(d.display_name, d.name) AS drug ORDER BY drug
        """,
        condition=condition,
    )
    if not rows:
        return []
    text = (
        f"Knowledge graph: drugs in the corpus indicated for {condition} — "
        + "; ".join(r["drug"] for r in rows)
    )
    return [_fact_hit(text)]


def drugs_treating_condition_interacting_with(condition: str, drug_name: str) -> list[SearchHit]:
    """The drug arm's justifying multi-hop: a whole-corpus join (treats X AND
    interacts with Y) that top-k text retrieval structurally can't answer."""
    if condition not in VALID_CONDITIONS:
        return []
    drug = canonical_drug_name(drug_name)
    rows = _run(
        """
        MATCH (d:Drug)-[:TREATS]->(:Condition {name: $condition}),
              (d)-[i:INTERACTS_WITH]-(y:Drug {name: $drug})
        RETURN coalesce(d.display_name, d.name) AS drug, i.source_set_id AS set_id
        ORDER BY drug
        """,
        condition=condition,
        drug=drug,
    )
    if not rows:
        text = (
            f"Knowledge graph: no drug in the corpus both treats {condition} and has a "
            f"labeled interaction with {drug}."
        )
        return [_fact_hit(text)]
    listing = "; ".join(f"{r['drug']} (interaction per label {r['set_id']})" for r in rows)
    text = (
        f"Knowledge graph: drugs treating {condition} with a labeled interaction "
        f"with {drug} — {listing}"
    )
    return [_fact_hit(text)]
