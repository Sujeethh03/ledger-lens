"""Deterministic entity extraction feeding the knowledge graph — both domains.

Scoped deviation from the dossier (recorded): the dossier sketched LLM-based
entity extraction. LLM extraction adds cost + a hallucination surface to a
component whose whole value is being *reliable* graph ground truth, so every
extractor here is a keyword/lexicon matcher: cheap, testable,
zero-hallucination.

SEC arm:  RISK_TAXONOMY -> (Filing)-[:DISCUSSES]->(RiskFactor)
Drug arm: CONDITION_TAXONOMY -> (Drug)-[:TREATS]->(Condition)
          find_drug_mentions  -> (Drug)-[:INTERACTS_WITH]->(Drug), matched
          against a lexicon built from the ingested corpus itself (generic +
          brand names from openFDA metadata) — the graph never names a drug
          the pipeline hasn't ingested.

Taxonomies are small on purpose — topics broad enough to recur across
entities, which is what makes cross-entity graph queries meaningful.
"""

import re
from dataclasses import dataclass

RISK_TAXONOMY: dict[str, list[str]] = {
    "supply_chain": ["supply chain", "supply constraint", "component shortage", "outsourcing partner", "single source supplier"],
    "semiconductor": ["semiconductor", "chip shortage", "nand", "dram", "foundry"],
    "currency": ["foreign exchange", "currency", "exchange rate", "hedging"],
    "litigation": ["litigation", "legal proceeding", "lawsuit", "class action", "antitrust"],
    "cybersecurity": ["cybersecurity", "cyber attack", "data breach", "ransomware", "information security"],
    "regulation": ["regulatory", "regulation", "compliance", "government investigation", "digital markets act"],
    "competition": ["competition", "competitive pressure", "price competition", "market share"],
    "interest_rate": ["interest rate", "monetary policy", "federal reserve"],
    "macroeconomic": ["inflation", "recession", "macroeconomic", "economic downturn", "consumer demand"],
    "talent": ["key personnel", "attract and retain", "talent", "workforce"],
}


@dataclass(frozen=True)
class TopicMatch:
    topic: str
    evidence_count: int  # how many taxonomy phrases matched — crude signal strength


def _match_taxonomy(text: str, taxonomy: dict[str, list[str]], min_evidence: int) -> list[TopicMatch]:
    lowered = text.lower()
    matches = []
    for topic, phrases in taxonomy.items():
        count = sum(len(re.findall(re.escape(phrase), lowered)) for phrase in phrases)
        if count >= min_evidence:
            matches.append(TopicMatch(topic=topic, evidence_count=count))
    return sorted(matches, key=lambda m: m.evidence_count, reverse=True)


def extract_topics(text: str, min_evidence: int = 1) -> list[TopicMatch]:
    return _match_taxonomy(text, RISK_TAXONOMY, min_evidence)


# Conditions phrased the way Indications and Usage / Purpose sections phrase
# them (prescription labels use the clinical term, OTC Drug Facts the plain
# one — both spellings must be here or half the corpus goes dark).
CONDITION_TAXONOMY: dict[str, list[str]] = {
    "hypertension": ["hypertension", "high blood pressure", "lowering blood pressure"],
    "pain": ["pain", "analgesic", "minor aches"],
    "fever": ["fever", "antipyretic"],
    "inflammation": ["inflammation", "anti-inflammatory", "arthritis"],
    "diabetes": ["diabetes", "glycemic control", "blood sugar"],
    "high_cholesterol": ["hyperlipidemia", "cholesterol", "triglyceride", "dyslipidemia"],
    "depression": ["major depressive disorder", "depression", "obsessive-compulsive", "panic disorder", "bulimia"],
    "acid_reflux": ["gastroesophageal reflux", "gerd", "duodenal ulcer", "gastric ulcer", "heartburn", "erosive esophagitis"],
    "blood_clots": ["thromboembolic", "thrombosis", "embolism", "blood clot", "atrial fibrillation", "myocardial infarction", "reinfarction"],
    "heart_failure": ["heart failure"],
    "stroke_prevention": ["stroke"],
}


def extract_conditions(text: str, min_evidence: int = 1) -> list[TopicMatch]:
    return _match_taxonomy(text, CONDITION_TAXONOMY, min_evidence)


def find_drug_mentions(text: str, lexicon: dict[str, str], exclude: str = "") -> dict[str, int]:
    """Count mentions of known drugs in `text`.

    `lexicon` maps a searchable name (generic or brand, lowercase) to its
    canonical drug id; counts aggregate per canonical id. `exclude` drops the
    label's own drug — every label mentions itself constantly, and a
    (Drug)-[:INTERACTS_WITH]->(itself) edge is always extraction noise.
    Word-boundary matching: 'aspirin' must not fire inside a longer token.
    """
    lowered = text.lower()
    counts: dict[str, int] = {}
    for name, canonical in lexicon.items():
        if canonical == exclude:
            continue
        n = len(re.findall(rf"\b{re.escape(name)}\b", lowered))
        if n:
            counts[canonical] = counts.get(canonical, 0) + n
    return counts
