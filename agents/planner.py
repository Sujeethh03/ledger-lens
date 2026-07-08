"""M5 Planner: decompose a question into 1-3 atomic retrieval sub-queries.

Multi-hop questions ("how did Apple's supply-chain risk language change
between 2025 and 2026?") retrieve badly as a single query — each hop needs
its own search. Single-hop questions pass through unchanged (the planner is
told not to decompose for the sake of it). Graph-routing (RAG vs Cypher)
arrives with M4; until then every sub-query goes to hybrid search.
"""

from pydantic import BaseModel, Field

from agents.llm import CHEAP_MODEL, Chat, structured_call

SYSTEM = """You decompose questions about SEC filings into retrieval sub-queries.
Return JSON: {"sub_queries": ["...", ...]} with 1-3 entries.
Rules:
- Each sub-query must be independently searchable (no pronouns referring to other sub-queries).
- Keep the company name and fiscal year in every sub-query that needs them.
- If the question is already atomic, return it as the single sub-query. Do not decompose needlessly."""


class Plan(BaseModel):
    sub_queries: list[str] = Field(min_length=1, max_length=3)


def plan(question: str, chat: Chat) -> Plan:
    return structured_call(chat, SYSTEM, question, Plan, model=CHEAP_MODEL)
