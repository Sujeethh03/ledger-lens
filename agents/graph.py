"""M5: the LangGraph pipeline — plan → retrieve → synthesize → guardrail.

Flow (matches the dossier's query-time sequence diagram, minus the M4 graph
agent):

    plan ──► retrieve ──┬─► synthesize ──► guardrail ──► ok? ──► END
                        │        ▲                        │
              (0 chunks)│        └── one revision pass ◄──┘ violations
                        ▼             (then refuse — never return an
                   refuse now          answer that failed the check)

State is a TypedDict so every node is a plain testable function; chat +
embedder are injected, so the whole graph runs under fakes in tests.
"""

from dataclasses import dataclass
from typing import TypedDict

import structlog
from langgraph.graph import END, StateGraph

from agents.guardrail_agent import INSUFFICIENT, check_citations
from agents.llm import Chat
from agents.planner import plan
from agents.retriever_agent import LabeledChunk, gather_context
from agents.synthesis_agent import synthesize
from retrieval.embeddings import Embedder

log = structlog.get_logger(__name__)

MAX_REVISIONS = 1

REFUSAL = (
    "I can't answer this reliably: the drafted answer failed citation checks "
    "even after revision, so returning it would risk unsupported claims."
)


def _run_graph_lookups(lookups: list[dict]) -> list:
    """Graph lookups are best-effort: if Neo4j is down or empty, the text-RAG
    arm still answers — the graph augments retrieval, it must never break it."""
    from agents import graph_agent

    hits = []
    for lookup in lookups:
        try:
            kind = lookup["kind"]
            if kind == "companies_discussing":
                hits.extend(graph_agent.companies_discussing(lookup["arg"]))
            elif kind == "topics_for_company":
                hits.extend(graph_agent.topics_for_company(lookup["arg"]))
            elif kind == "shared_topics":
                hits.extend(graph_agent.companies_sharing_topics())
            elif kind == "drugs_interacting_with":
                hits.extend(graph_agent.drugs_interacting_with(lookup["arg"]))
            elif kind == "drugs_treating":
                hits.extend(graph_agent.drugs_treating(lookup["arg"]))
            elif kind == "treats_and_interacts":
                hits.extend(
                    graph_agent.drugs_treating_condition_interacting_with(
                        lookup["arg"], lookup.get("arg2", "")
                    )
                )
        except Exception as exc:
            log.warning("graph_lookup_failed", kind=lookup.get("kind"), error=str(exc))
    return hits


class AskState(TypedDict, total=False):
    question: str
    sub_queries: list[str]
    graph_lookups: list[dict]
    chunks: list[LabeledChunk]
    draft: str
    violations: list[str]
    revisions: int
    final_answer: str
    refused: bool


@dataclass
class AskResult:
    answer: str
    refused: bool
    sub_queries: list[str]
    citations: list[LabeledChunk]


def build_graph(chat: Chat, embedder: Embedder):
    def plan_node(state: AskState) -> AskState:
        result = plan(state["question"], chat)
        log.info("planned", sub_queries=result.sub_queries, graph_lookups=len(result.graph_lookups))
        return {
            "sub_queries": result.sub_queries,
            "graph_lookups": [lookup.model_dump() for lookup in result.graph_lookups],
        }

    def retrieve_node(state: AskState) -> AskState:
        graph_hits = _run_graph_lookups(state.get("graph_lookups", []))
        chunks = gather_context(state["sub_queries"], embedder, extra_hits=graph_hits)
        log.info("retrieved", chunks=len(chunks), graph_facts=len(graph_hits))
        return {"chunks": chunks}

    def synthesize_node(state: AskState) -> AskState:
        feedback = "\n".join(state["violations"]) if state.get("violations") else None
        draft = synthesize(state["question"], state["chunks"], chat, feedback=feedback)
        return {"draft": draft.answer}

    def guardrail_node(state: AskState) -> AskState:
        labels = {c.label for c in state["chunks"]}
        verdict = check_citations(state["draft"], labels)
        if verdict.ok:
            refused = state["draft"].strip() == INSUFFICIENT
            return {"violations": [], "final_answer": state["draft"], "refused": refused}
        log.warning("guardrail_violations", n=len(verdict.violations))
        return {"violations": verdict.violations, "revisions": state.get("revisions", 0) + 1}

    def route_after_retrieve(state: AskState) -> str:
        # Nothing retrieved -> refuse now; a synthesis call over zero sources
        # can only produce hallucination or a wasted round-trip.
        return "empty_refuse" if not state["chunks"] else "synthesize"

    def empty_refuse_node(state: AskState) -> AskState:
        return {"final_answer": INSUFFICIENT, "refused": True}

    def route_after_guardrail(state: AskState) -> str:
        if state.get("final_answer"):
            return END
        if state.get("revisions", 0) > MAX_REVISIONS:
            return "refuse"
        return "synthesize"

    def refuse_node(state: AskState) -> AskState:
        return {"final_answer": REFUSAL, "refused": True}

    graph = StateGraph(AskState)
    graph.add_node("plan", plan_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("guardrail", guardrail_node)
    graph.add_node("refuse", refuse_node)
    graph.add_node("empty_refuse", empty_refuse_node)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "retrieve")
    graph.add_conditional_edges(
        "retrieve", route_after_retrieve, {"empty_refuse": "empty_refuse", "synthesize": "synthesize"}
    )
    graph.add_edge("empty_refuse", END)
    graph.add_edge("synthesize", "guardrail")
    graph.add_conditional_edges("guardrail", route_after_guardrail, {END: END, "synthesize": "synthesize", "refuse": "refuse"})
    graph.add_edge("refuse", END)
    return graph.compile()


def ask(question: str, chat: Chat, embedder: Embedder) -> AskResult:
    app = build_graph(chat, embedder)
    state: AskState = app.invoke({"question": question, "revisions": 0})
    return AskResult(
        answer=state["final_answer"],
        refused=state.get("refused", False),
        sub_queries=state.get("sub_queries", []),
        citations=state.get("chunks", []),
    )
