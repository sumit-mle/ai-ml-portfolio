"""LangGraph wiring.

Pipeline:
    plan -> draft -> validate -> [route] -> execute -> [route] -> answer
                          ^         |             |
                          |         └─ on policy failure: repair
                          |                       └─ on execute failure: repair
                          └────── repair (loops up to MAX_REPAIR_ATTEMPTS)
"""
from __future__ import annotations

import time
from typing import Literal

from langgraph.graph import END, StateGraph

from ..config import get_settings
from . import nodes
from .state import AgentState


def _route_after_validate(
    state: AgentState,
) -> Literal["execute", "repair", "give_up"]:
    if state.get("last_error"):
        if int(state.get("repair_attempts", 0)) < get_settings().max_repair_attempts:
            return "repair"
        return "give_up"
    return "execute"


def _route_after_execute(
    state: AgentState,
) -> Literal["answer", "repair", "give_up"]:
    if state.get("last_error"):
        if int(state.get("repair_attempts", 0)) < get_settings().max_repair_attempts:
            return "repair"
        return "give_up"
    return "answer"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("plan", nodes.node_plan)
    g.add_node("draft", nodes.node_draft)
    g.add_node("validate", nodes.node_validate)
    g.add_node("execute", nodes.node_execute)
    g.add_node("repair", nodes.node_repair)
    g.add_node("answer", nodes.node_answer)
    g.add_node("give_up", nodes.node_give_up)

    g.set_entry_point("plan")
    g.add_edge("plan", "draft")
    g.add_edge("draft", "validate")
    g.add_conditional_edges(
        "validate",
        _route_after_validate,
        {"execute": "execute", "repair": "repair", "give_up": "give_up"},
    )
    g.add_conditional_edges(
        "execute",
        _route_after_execute,
        {"answer": "answer", "repair": "repair", "give_up": "give_up"},
    )
    g.add_edge("repair", "validate")
    g.add_edge("answer", END)
    g.add_edge("give_up", END)
    return g.compile()


def run_question(question: str) -> AgentState:
    graph = build_graph()
    initial: AgentState = {
        "question": question,
        "trace": [],
        "started_at": time.time(),
        "repair_attempts": 0,
    }
    return graph.invoke(initial)
