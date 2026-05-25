"""LangGraph agentic-RAG state machine with a reflection loop.

Nodes:
    plan      - rewrite the user question into a focused PubMed-style query
    retrieve  - LlamaIndex similarity search over the abstract corpus
    generate  - draft an answer grounded in retrieved abstracts
    reflect   - LLM-as-judge scores grounded/cited/complete; may request more

Edges:
    plan -> retrieve -> generate -> reflect
    reflect -> END if scores pass thresholds OR iteration budget exhausted
    reflect -> retrieve (with follow_up_query) otherwise

State carries: question, current_query, all retrieved docs (deduped by PMID),
draft, final critique, iterations counter, and a trace of node visits.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from ..shared.llm import (
    GENERATOR_SYSTEM,
    PLANNER_SYSTEM,
    REFLECTOR_SYSTEM,
    build_generator_user_prompt,
    build_reflector_user_prompt,
    get_chat_model_name,
    require_openai_key,
)
from ..shared.retriever import AbstractRetriever, RetrievedDoc


# Default thresholds — reflection loop continues while any score is below.
GROUNDED_THRESHOLD = 0.85
CITED_THRESHOLD = 0.85
COMPLETE_THRESHOLD = 0.7


class AgentState(TypedDict, total=False):
    question: str
    current_query: str
    retrieved: list[dict[str, Any]]   # serialized RetrievedDoc list (cumulative, deduped)
    draft: str
    critique: dict[str, Any]
    iterations: int
    max_iterations: int
    top_k: int
    trace: list[str]
    final_answer: str


@dataclass
class AgentRunResult:
    question: str
    final_answer: str
    retrieved: list[RetrievedDoc] = field(default_factory=list)
    iterations: int = 0
    critique: dict[str, Any] = field(default_factory=dict)
    trace: list[str] = field(default_factory=list)


def _llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=get_chat_model_name(), temperature=0.1)


def _doc_to_dict(d: RetrievedDoc) -> dict[str, Any]:
    return {
        "pmid": d.pmid,
        "title": d.title,
        "abstract": d.abstract,
        "journal": d.journal,
        "year": d.year,
        "score": d.score,
    }


def _dict_to_doc(d: dict[str, Any]) -> RetrievedDoc:
    return RetrievedDoc(
        pmid=d.get("pmid", ""),
        title=d.get("title", ""),
        abstract=d.get("abstract", ""),
        journal=d.get("journal", ""),
        year=d.get("year", ""),
        score=float(d.get("score", 0.0)),
    )


def _merge_retrieved(
    existing: list[dict[str, Any]], new: list[RetrievedDoc]
) -> list[dict[str, Any]]:
    seen = {d["pmid"] for d in existing}
    out = list(existing)
    for d in new:
        if d.pmid not in seen:
            out.append(_doc_to_dict(d))
            seen.add(d.pmid)
    return out


def _displays(docs_serialized: list[dict[str, Any]]) -> list[str]:
    return [_dict_to_doc(d).display for d in docs_serialized]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def make_plan_node():
    llm = _llm()

    def plan(state: AgentState) -> AgentState:
        from langchain_core.messages import HumanMessage, SystemMessage

        msg = llm.invoke(
            [
                SystemMessage(content=PLANNER_SYSTEM),
                HumanMessage(content=state["question"]),
            ]
        )
        query = (msg.content or "").strip().strip('"').strip("'")
        if not query:
            query = state["question"]
        trace = list(state.get("trace", []))
        trace.append(f"plan -> {query!r}")
        return {"current_query": query, "trace": trace}

    return plan


def make_retrieve_node(retriever: AbstractRetriever):
    def retrieve(state: AgentState) -> AgentState:
        query = state.get("current_query") or state["question"]
        top_k = state.get("top_k", 5)
        docs = retriever.retrieve(query, top_k=top_k)
        merged = _merge_retrieved(state.get("retrieved", []), docs)
        trace = list(state.get("trace", []))
        trace.append(f"retrieve({query!r}) -> {len(docs)} hits, {len(merged)} cumulative")
        return {"retrieved": merged, "trace": trace}

    return retrieve


def make_generate_node():
    llm = _llm()

    def generate(state: AgentState) -> AgentState:
        from langchain_core.messages import HumanMessage, SystemMessage

        contexts = _displays(state.get("retrieved", []))
        user = build_generator_user_prompt(state["question"], contexts)
        msg = llm.invoke(
            [SystemMessage(content=GENERATOR_SYSTEM), HumanMessage(content=user)]
        )
        draft = msg.content or ""
        trace = list(state.get("trace", []))
        trace.append(f"generate -> {len(draft)} chars")
        return {"draft": draft, "trace": trace}

    return generate


def _parse_critique(raw: str) -> dict[str, Any]:
    """Best-effort JSON extraction from the reflector output."""
    text = (raw or "").strip()
    if text.startswith("```"):
        # strip code fences
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    # find first { ... last }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except Exception:
        data = {}
    return {
        "grounded": float(data.get("grounded", 0.0)),
        "cited": float(data.get("cited", 0.0)),
        "complete": float(data.get("complete", 0.0)),
        "needs_more_evidence": bool(data.get("needs_more_evidence", False)),
        "follow_up_query": str(data.get("follow_up_query", "") or ""),
        "critique": str(data.get("critique", "") or ""),
    }


def make_reflect_node():
    llm = _llm()

    def reflect(state: AgentState) -> AgentState:
        from langchain_core.messages import HumanMessage, SystemMessage

        contexts = _displays(state.get("retrieved", []))
        user = build_reflector_user_prompt(
            state["question"], contexts, state.get("draft", "")
        )
        msg = llm.invoke(
            [SystemMessage(content=REFLECTOR_SYSTEM), HumanMessage(content=user)]
        )
        critique = _parse_critique(msg.content or "")
        iterations = int(state.get("iterations", 0)) + 1
        trace = list(state.get("trace", []))
        trace.append(
            f"reflect#{iterations} -> g={critique['grounded']:.2f} "
            f"c={critique['cited']:.2f} k={critique['complete']:.2f} "
            f"more={critique['needs_more_evidence']}"
        )
        return {
            "critique": critique,
            "iterations": iterations,
            "trace": trace,
        }

    return reflect


def reflect_router(state: AgentState) -> Literal["retrieve", "finalize"]:
    crit = state.get("critique", {})
    iterations = int(state.get("iterations", 0))
    max_iter = int(state.get("max_iterations", 2))

    if iterations >= max_iter:
        return "finalize"

    grounded = float(crit.get("grounded", 0.0))
    cited = float(crit.get("cited", 0.0))
    complete = float(crit.get("complete", 0.0))
    needs_more = bool(crit.get("needs_more_evidence", False))
    follow_up = str(crit.get("follow_up_query", "")).strip()

    passing = (
        grounded >= GROUNDED_THRESHOLD
        and cited >= CITED_THRESHOLD
        and complete >= COMPLETE_THRESHOLD
    )
    if passing or not needs_more or not follow_up:
        return "finalize"
    return "retrieve"


def make_replan_node():
    """Bridge node: copies the reflector's follow_up_query into current_query
    so the next retrieve step uses it.
    """

    def replan(state: AgentState) -> AgentState:
        crit = state.get("critique", {})
        follow_up = str(crit.get("follow_up_query", "")).strip()
        trace = list(state.get("trace", []))
        if follow_up:
            trace.append(f"replan -> {follow_up!r}")
            return {"current_query": follow_up, "trace": trace}
        trace.append("replan -> (no follow-up; reusing previous query)")
        return {"trace": trace}

    return replan


def make_finalize_node():
    def finalize(state: AgentState) -> AgentState:
        return {"final_answer": state.get("draft", "")}

    return finalize


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------


def build_graph(retriever: AbstractRetriever):
    g = StateGraph(AgentState)
    g.add_node("plan", make_plan_node())
    g.add_node("retrieve", make_retrieve_node(retriever))
    g.add_node("generate", make_generate_node())
    g.add_node("reflect", make_reflect_node())
    g.add_node("replan", make_replan_node())
    g.add_node("finalize", make_finalize_node())

    g.set_entry_point("plan")
    g.add_edge("plan", "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "reflect")
    g.add_conditional_edges(
        "reflect",
        reflect_router,
        {"retrieve": "replan", "finalize": "finalize"},
    )
    g.add_edge("replan", "retrieve")
    g.add_edge("finalize", END)
    return g.compile()


def run_agent(
    question: str,
    abstracts,
    *,
    top_k: int = 5,
    max_iterations: int = 2,
) -> AgentRunResult:
    """Build a graph, run it once, return a structured result."""
    require_openai_key()
    retriever = AbstractRetriever(abstracts, top_k_default=top_k)
    graph = build_graph(retriever)
    initial: AgentState = {
        "question": question,
        "retrieved": [],
        "iterations": 0,
        "max_iterations": max_iterations,
        "top_k": top_k,
        "trace": [],
    }
    final = graph.invoke(initial)
    return AgentRunResult(
        question=question,
        final_answer=final.get("final_answer") or final.get("draft", ""),
        retrieved=[_dict_to_doc(d) for d in final.get("retrieved", [])],
        iterations=int(final.get("iterations", 0)),
        critique=dict(final.get("critique", {}) or {}),
        trace=list(final.get("trace", []) or []),
    )
