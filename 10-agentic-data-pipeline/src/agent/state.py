"""Agent state — the typed dict that flows through every LangGraph node."""
from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # Input
    question: str

    # Plan / retrieval phase
    relevant_tables: list[str]
    schema_context: str
    sample_rows_text: str

    # SQL phase
    proposed_sql: str
    safe_sql: str                       # post-gate
    referenced_tables: list[str]
    repair_attempts: int
    last_error: str | None

    # Execution phase
    result_columns: list[str]
    result_rows: list[list[Any]]
    n_rows: int

    # Final
    answer: str

    # Audit
    trace: list[dict[str, Any]]
    started_at: float
