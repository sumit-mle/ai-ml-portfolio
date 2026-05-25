"""Golden test cases for the MCP tools.

We test five things that matter in production:
  1. AUTH         missing/invalid token -> denied
  2. AUTHZ        valid token without required scope -> denied
  3. SQL GATE     DDL/DML attempts -> denied with clear error
  4. ROW LIMIT    queries without LIMIT get one injected
  5. PII          masking applied for read:public-only principals; unmasked for read:pii

Plus golden questions for the natural-language SQL tool that should succeed
and produce a non-empty result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolCase:
    name: str
    tool: str                                 # tool name on the MCP server
    arguments: dict[str, Any]
    principal: str                            # token id (e.g. "tok_analyst_a")
    expect: Literal["ok", "denied", "error"]
    expect_substring: str | None = None       # phrase that must appear in error/result
    expect_no_pii_in_columns: tuple[str, ...] = field(default_factory=tuple)
    expect_referenced_tables: tuple[str, ...] = field(default_factory=tuple)
    expect_min_rows: int | None = None


def all_cases() -> list[ToolCase]:
    return [
        # 1. AUTH ------------------------------------------------------------
        ToolCase(
            name="auth_missing_token",
            tool="list_tables",
            arguments={"bearer": ""},
            principal="<none>",
            expect="denied",
            expect_substring="missing bearer token",
        ),
        ToolCase(
            name="auth_invalid_token",
            tool="list_tables",
            arguments={"bearer": "not_a_real_token"},
            principal="<none>",
            expect="denied",
            expect_substring="invalid token",
        ),
        # 2. AUTHZ -----------------------------------------------------------
        ToolCase(
            name="authz_missing_query_nl_scope",
            tool="natural_language_query",
            arguments={"question": "How many trips?"},
            principal="tok_analyst_public",   # has read:public, NOT query:nl
            expect="denied",
            expect_substring="missing required scope",
        ),
        # 3. SQL GATE --------------------------------------------------------
        ToolCase(
            name="sql_gate_drop",
            tool="run_select_sql",
            arguments={"sql": "DROP TABLE taxi_trips"},
            principal="tok_analyst_public",
            expect="denied",
            expect_substring="not allowed",
        ),
        ToolCase(
            name="sql_gate_insert",
            tool="run_select_sql",
            arguments={"sql": "INSERT INTO taxi_trips SELECT * FROM taxi_trips LIMIT 1"},
            principal="tok_analyst_public",
            expect="denied",
            expect_substring="not allowed",
        ),
        ToolCase(
            name="sql_gate_multi_statement",
            tool="run_select_sql",
            arguments={"sql": "SELECT 1; DROP TABLE taxi_trips"},
            principal="tok_analyst_public",
            expect="denied",
            expect_substring="multiple statements",
        ),
        # 4. ROW LIMIT INJECTION --------------------------------------------
        ToolCase(
            name="row_limit_injected",
            tool="run_select_sql",
            arguments={"sql": "SELECT * FROM taxi_trips"},
            principal="tok_analyst_public",
            expect="ok",
            expect_referenced_tables=("taxi_trips",),
            expect_min_rows=1,
        ),
        ToolCase(
            name="select_count_no_limit_needed",
            tool="run_select_sql",
            arguments={"sql": "SELECT count(*) AS n FROM taxi_trips"},
            principal="tok_analyst_public",
            expect="ok",
            expect_min_rows=1,
        ),
        ToolCase(
            name="select_join_aggregate",
            tool="run_select_sql",
            arguments={
                "sql": (
                    "SELECT vendor_id, count(*) AS trips, "
                    "round(avg(fare_amount), 2) AS avg_fare "
                    "FROM taxi_trips GROUP BY vendor_id ORDER BY trips DESC"
                ),
            },
            principal="tok_analyst_public",
            expect="ok",
            expect_min_rows=1,
        ),
        # 5. PII -------------------------------------------------------------
        ToolCase(
            name="pii_masked_for_public_principal",
            tool="sample_rows",
            arguments={"table": "passengers", "n": 3},
            principal="tok_analyst_public",   # no read:pii
            expect="ok",
            expect_no_pii_in_columns=("email", "full_name", "phone"),
            expect_min_rows=3,
        ),
        ToolCase(
            name="pii_unmasked_for_privileged_principal",
            tool="sample_rows",
            arguments={"table": "passengers", "n": 3},
            principal="tok_analyst_pii",      # has read:pii
            expect="ok",
            expect_min_rows=3,
        ),
        ToolCase(
            name="describe_table_marks_pii",
            tool="describe_table",
            arguments={"table": "passengers"},
            principal="tok_analyst_public",
            expect="ok",
        ),
        # 6. NL QUERY HAPPY PATH ---------------------------------------------
        ToolCase(
            name="nl_query_happy_path",
            tool="natural_language_query",
            arguments={
                "question": "How many trips per payment_type are there in taxi_trips? Sort by count desc.",
                "table_hint": "taxi_trips",
            },
            principal="tok_data_scientist",   # has read:public + query:nl
            expect="ok",
            expect_min_rows=1,
        ),
    ]
