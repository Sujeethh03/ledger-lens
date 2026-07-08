"""End-to-end graph tests under fakes — no network, no DB.

`gather_context` is monkeypatched (it needs the real DB) with canned labeled
chunks; the Chat fake returns scripted JSON per call. This exercises the real
LangGraph wiring: plan → retrieve → synthesize → guardrail, including the
revision loop and the refusal path.
"""

import json
import uuid

import pytest

import agents.graph as graph_module
from agents.graph import REFUSAL, ask
from agents.retriever_agent import LabeledChunk
from retrieval.hybrid_search import SearchHit


def _chunk(label: str) -> LabeledChunk:
    return LabeledChunk(
        label=label,
        hit=SearchHit(
            chunk_id=uuid.uuid4(),
            filing_accession="0000320193-26-000013",
            company_name="Apple Inc.",
            form_type="10-Q",
            fiscal_year=2026,
            section="Item 1A. Risk Factors",
            text="Supply chain disruption may adversely affect results.",
            ocr_confidence=None,
            rrf_score=0.03,
        ),
    )


class ScriptedChat:
    """Returns queued JSON responses in order; records every call."""

    def __init__(self, responses: list[dict]):
        self._queue = [json.dumps(r) for r in responses]
        self.calls: list[str] = []

    def __call__(self, system: str, user: str, model: str) -> str:
        self.calls.append(user)
        return self._queue.pop(0)


class FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 1536 for _ in texts]


@pytest.fixture
def two_chunks(monkeypatch):
    monkeypatch.setattr(graph_module, "gather_context", lambda queries, embedder: [_chunk("C1"), _chunk("C2")])


def test_happy_path_returns_cited_answer(two_chunks):
    chat = ScriptedChat(
        [
            {"sub_queries": ["apple supply chain risk"]},
            {"answer": "Apple flags supply chain disruption as a material risk [C1]."},
        ]
    )
    result = ask("What supply chain risks does Apple disclose?", chat, FakeEmbedder())
    assert not result.refused
    assert "[C1]" in result.answer
    assert len(chat.calls) == 2  # plan + one synthesis, no revision needed


def test_uncited_draft_gets_one_revision_then_passes(two_chunks):
    chat = ScriptedChat(
        [
            {"sub_queries": ["apple supply chain risk"]},
            {"answer": "Apple faces supply chain risk."},  # uncited -> violation
            {"answer": "Apple faces supply chain risk [C2]."},  # revised
        ]
    )
    result = ask("What supply chain risks?", chat, FakeEmbedder())
    assert not result.refused
    assert "[C2]" in result.answer
    assert len(chat.calls) == 3
    # The revision prompt must carry the violation feedback to the model.
    assert "uncited sentence" in chat.calls[2]


def test_persistent_violations_end_in_refusal_not_bad_answer(two_chunks):
    chat = ScriptedChat(
        [
            {"sub_queries": ["q"]},
            {"answer": "Confident uncited claim."},
            {"answer": "Still confidently uncited."},
        ]
    )
    result = ask("question", chat, FakeEmbedder())
    assert result.refused
    assert result.answer == REFUSAL


def test_no_retrieved_chunks_refuses_without_synthesis(monkeypatch):
    monkeypatch.setattr(graph_module, "gather_context", lambda queries, embedder: [])
    chat = ScriptedChat([{"sub_queries": ["something obscure"]}])
    result = ask("question about nothing indexed", chat, FakeEmbedder())
    assert result.refused
    assert len(chat.calls) == 1  # plan only — no synthesis call wasted


def test_model_admitting_insufficient_evidence_is_honored(two_chunks):
    chat = ScriptedChat(
        [
            {"sub_queries": ["q"]},
            {"answer": "INSUFFICIENT_EVIDENCE"},
        ]
    )
    result = ask("unanswerable question", chat, FakeEmbedder())
    assert result.refused
    assert result.answer == "INSUFFICIENT_EVIDENCE"
