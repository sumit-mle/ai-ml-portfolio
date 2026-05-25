"""LangGraph node implementations."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from openai import OpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings, require_openai_key
from ..db.driver import cursor
from ..db.schema import render_partial_schema, sample_rows, select_tables
from ..safety.sql_gate import GateResult, SqlPolicyError, enforce_read_only
from .prompts import ANSWER_SYSTEM, DRAFT_SYSTEM, REPAIR_SYSTEM
from .state import AgentState

logger = logging.getLogger(__name__)


class _SqlOut(BaseModel):
    sql: str
    reason: str = ""


def _trace(state: AgentState, node: str, **fields) -> dict[str, Any]:
    """Append a trace event."""
    evt = {"ts": round(time.time(), 3), "node": node, **fields}
    trace = list(state.get("trace") or [])
    trace.append(evt)
    return {"trace": trace}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
def _llm_sql(system: str, user: str) -> _SqlOut:
    s = get_settings()
    client = OpenAI(api_key=s.openai_api_key)
    completion = client.beta.chat.completions.parse(
        model=s.gen_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format=_SqlOut,
        temperature=0.0,
    )
    msg = completion.choices[0].message
    if msg.refusal or msg.parsed is None:
        raise RuntimeError(f"refused: {msg.refusal}")
    return msg.parsed


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def node_plan(state: AgentState) -> dict[str, Any]:
    """Pick relevant tables and assemble the schema context for the drafter."""
    question = state["question"]
    tables = select_tables(question, top_k=4)
    # Build a sample-rows hint per chosen table
    samples: list[str] = []
    for t in tables:
        try:
            rows = sample_rows(t, n=2)
            samples.append(f"Sample rows from {t}: {json.dumps(rows, default=str)}")
        except Exception as e:
            samples.append(f"(could not sample {t}: {e})")
    return {
        "relevant_tables": tables,
        "schema_context": render_partial_schema(tables),
        "sample_rows_text": "\n".join(samples),
        "repair_attempts": 0,
        **_trace(state, "plan", tables=tables),
    }


def node_draft(state: AgentState) -> dict[str, Any]:
    """Ask the LLM to write the first SQL draft."""
    require_openai_key()
    user = (
        f"QUESTION: {state['question']}\n\n"
        f"SCHEMA:\n{state['schema_context']}\n\n"
        f"DATA SAMPLES:\n{state['sample_rows_text']}\n\n"
        "Write the SQL."
    )
    parsed = _llm_sql(DRAFT_SYSTEM, user)
    return {
        "proposed_sql": parsed.sql,
        **_trace(state, "draft", sql=parsed.sql, reason=parsed.reason),
    }


def node_validate(state: AgentState) -> dict[str, Any]:
    """Run the SQL through the read-only policy gate."""
    proposed = state.get("proposed_sql", "")
    s = get_settings()
    try:
        gate: GateResult = enforce_read_only(
            proposed, dialect="duckdb", max_rows=s.max_rows_returned,
        )
    except SqlPolicyError as e:
        return {
            "last_error": f"policy: {e}",
            "safe_sql": "",
            **_trace(state, "validate", outcome="denied", reason=str(e)),
        }
    return {
        "safe_sql": gate.safe_sql,
        "referenced_tables": list(gate.referenced_tables),
        "last_error": None,
        **_trace(
            state, "validate", outcome="ok",
            referenced_tables=list(gate.referenced_tables),
            injected_limit=gate.injected_limit,
        ),
    }


def node_execute(state: AgentState) -> dict[str, Any]:
    """Run the validated SQL on DuckDB."""
    safe = state.get("safe_sql") or ""
    if not safe:
        # validate already failed; let router send us to repair
        return _trace(state, "execute", outcome="skipped",
                      reason="no safe_sql to run")
    try:
        with cursor() as conn:
            rel = conn.execute(safe)
            cols = [d[0] for d in rel.description]
            rows = rel.fetchall()
    except Exception as e:
        return {
            "last_error": f"execute: {type(e).__name__}: {e}",
            **_trace(state, "execute", outcome="error", error=str(e)),
        }
    return {
        "result_columns": cols,
        "result_rows": [list(r) for r in rows],
        "n_rows": len(rows),
        "last_error": None,
        **_trace(state, "execute", outcome="ok", n_rows=len(rows)),
    }


def node_repair(state: AgentState) -> dict[str, Any]:
    """LLM repair pass when validate or execute failed."""
    require_openai_key()
    attempts = int(state.get("repair_attempts", 0)) + 1
    user = (
        f"QUESTION: {state['question']}\n\n"
        f"SCHEMA:\n{state['schema_context']}\n\n"
        f"PREVIOUS SQL:\n{state.get('proposed_sql', '')}\n\n"
        f"ERROR: {state.get('last_error', '(unknown)')}\n\n"
        "Write a corrected SQL."
    )
    parsed = _llm_sql(REPAIR_SYSTEM, user)
    return {
        "proposed_sql": parsed.sql,
        "repair_attempts": attempts,
        **_trace(state, "repair", attempt=attempts, sql=parsed.sql, reason=parsed.reason),
    }


def node_answer(state: AgentState) -> dict[str, Any]:
    """Synthesize a natural-language answer from the result rows."""
    require_openai_key()
    s = get_settings()
    cols = state.get("result_columns") or []
    rows = (state.get("result_rows") or [])[:25]
    rows_dump = json.dumps(
        [dict(zip(cols, r)) for r in rows], default=str, indent=2,
    )
    user = (
        f"QUESTION: {state['question']}\n\n"
        f"SQL:\n{state.get('safe_sql', '')}\n\n"
        f"RESULT (first 25 rows):\n{rows_dump}\n\n"
        f"Total rows in result: {state.get('n_rows', 0)}"
    )
    client = OpenAI(api_key=s.openai_api_key)
    resp = client.chat.completions.create(
        model=s.gen_model,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
    )
    answer = resp.choices[0].message.content or "(no answer)"
    return {
        "answer": answer,
        **_trace(state, "answer", chars=len(answer)),
    }


def node_give_up(state: AgentState) -> dict[str, Any]:
    """Reach this when repair budget is exhausted."""
    return {
        "answer": (
            f"I couldn't produce a valid query for this question after "
            f"{state.get('repair_attempts', 0)} repair attempt(s). "
            f"Last error: {state.get('last_error', 'unknown')}"
        ),
        **_trace(state, "give_up", attempts=state.get("repair_attempts", 0)),
    }
