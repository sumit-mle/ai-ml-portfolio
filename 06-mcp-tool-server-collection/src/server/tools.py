"""MCP tool implementations.

Each tool:
  1. Accepts a `bearer` string for auth (kept as an explicit arg to keep the
     transport simple and inspectable; production with HTTP would pull it
     from Authorization headers via FastMCP middleware).
  2. Authenticates -> Principal.
  3. Checks required scopes (declared in the @tool decorator's docstring as
     well as enforced in code).
  4. Rate-limits per principal.
  5. Audits the call.
  6. Applies SQL gate + PII masking where relevant.

The same five tools shipped here are the standard "internal data platform"
toolkit every Fortune 500 ends up wanting:
  - list_tables
  - describe_table
  - sample_rows
  - run_select_sql
  - natural_language_query   (LLM -> SQL, gated)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import catalog
from ..db.driver import cursor
from ..security.audit import AuditLog, AuditTimer
from ..security.auth import AuthError, Principal, TokenStore, require_scopes
from ..security.pii import PolicyEngine
from ..security.rate_limit import RateLimiter, RateLimitError
from ..security.sql_gate import SqlGateResult, SqlPolicyError, enforce_read_only

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output schemas — every tool returns a typed Pydantic object so MCP clients
# (Claude, Cursor) get clean structured results.
# ---------------------------------------------------------------------------


class TableInfo(BaseModel):
    table_name: str
    n_columns: int


class ListTablesResult(BaseModel):
    tables: list[TableInfo]


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool
    is_pii: bool = False


class DescribeTableResult(BaseModel):
    table_name: str
    exists: bool
    row_count: int
    columns: list[ColumnInfo]


class SampleRowsResult(BaseModel):
    table_name: str
    rows: list[dict[str, Any]]
    n_rows: int
    masked_columns: list[str] = Field(default_factory=list)


class RunSqlResult(BaseModel):
    rows: list[dict[str, Any]]
    n_rows: int
    columns: list[str]
    referenced_tables: list[str]
    injected_limit: int | None
    rewritten_sql: str
    duration_ms: float
    masked_columns: list[str] = Field(default_factory=list)


class NlQueryResult(BaseModel):
    question: str
    generated_sql: str
    explanation: str
    result: RunSqlResult


# ---------------------------------------------------------------------------
# Shared service object used by all tool entry points.
# ---------------------------------------------------------------------------


class ToolService:
    """Holds the long-lived dependencies the tools share."""

    def __init__(self):
        s = get_settings()
        self.settings = s
        self.tokens = TokenStore(s.auth_token_file)
        self.audit = AuditLog(s.audit_log_path)
        self.policy = PolicyEngine()
        self.rate = RateLimiter()

    # -- authn / authz / rate-limit / audit ---------------------------------

    def authorize(
        self,
        bearer: str | None,
        *,
        tool: str,
        scopes: list[str],
        arguments: dict[str, Any],
    ) -> tuple[Principal, AuditTimer]:
        """Enforce auth + scopes + rate limit. Returns the principal and an
        AuditTimer the caller MUST use as a context manager.
        """
        timer = AuditTimer(
            self.audit,
            principal_id=None,
            principal_name=None,
            tool=tool,
            arguments=arguments,
        )
        try:
            principal = self.tokens.authenticate(bearer)
        except AuthError as e:
            timer.denied(str(e))
            with timer:
                pass
            raise
        timer._principal_id = principal.token_id  # type: ignore[attr-defined]
        timer._principal_name = principal.name    # type: ignore[attr-defined]
        try:
            require_scopes(principal, scopes)
        except AuthError as e:
            timer.denied(str(e))
            with timer:
                pass
            raise
        try:
            limit = principal.rate_limit_per_hour or self.settings.rate_limit_per_hour
            self.rate.check_and_record(principal.token_id, limit)
        except RateLimitError as e:
            timer.denied(str(e))
            with timer:
                pass
            raise
        return principal, timer

    # -- the five tools -----------------------------------------------------

    def list_tables(self, bearer: str) -> ListTablesResult:
        principal, timer = self.authorize(
            bearer, tool="list_tables", scopes=["read:public"], arguments={},
        )
        with timer:
            tables = catalog.list_tables()
            timer.ok({"n_tables": len(tables)})
        return ListTablesResult(
            tables=[TableInfo(**t) for t in tables],
        )

    def describe_table(self, bearer: str, table: str) -> DescribeTableResult:
        principal, timer = self.authorize(
            bearer, tool="describe_table", scopes=["read:public"],
            arguments={"table": table},
        )
        with timer:
            info = catalog.describe_table(table)
            cols = []
            for c in info.get("columns", []):
                is_pii = self.policy.is_pii(table, c["name"])
                cols.append(ColumnInfo(
                    name=c["name"], type=c["type"],
                    nullable=c["nullable"], is_pii=is_pii,
                ))
            timer.ok({"row_count": info.get("row_count", 0), "n_columns": len(cols)})
        return DescribeTableResult(
            table_name=info["table_name"],
            exists=info["exists"],
            row_count=info.get("row_count", 0),
            columns=cols,
        )

    def sample_rows(self, bearer: str, table: str, n: int = 5) -> SampleRowsResult:
        principal, timer = self.authorize(
            bearer, tool="sample_rows", scopes=["read:public"],
            arguments={"table": table, "n": n},
        )
        with timer:
            rows = catalog.sample_rows(table, n=n)
            if "read:pii" not in principal.scopes:
                rows, masked = self.policy.mask_columns_in_rows(rows, table=table)
            else:
                masked = []
            timer.ok({"n_rows": len(rows), "masked_columns": masked})
        return SampleRowsResult(
            table_name=table, rows=rows, n_rows=len(rows), masked_columns=masked,
        )

    def run_select_sql(self, bearer: str, sql: str) -> RunSqlResult:
        principal, timer = self.authorize(
            bearer, tool="run_select_sql", scopes=["read:public"],
            arguments={"sql": sql},
        )
        with timer:
            try:
                gate = enforce_read_only(
                    sql, dialect="duckdb",
                    max_rows=self.settings.max_rows_returned,
                )
            except SqlPolicyError as e:
                timer.denied(f"sql policy: {e}")
                # Re-raise as a denial via the AuthError exception type so
                # the audit row records `outcome=denied` consistently with
                # auth/authz failures. We use AuthError here as the canonical
                # "policy rejection" error class.
                raise SqlPolicyError(str(e)) from None
            t0 = time.perf_counter()
            with cursor() as conn:
                rel = conn.execute(gate.safe_sql)
                cols = [d[0] for d in rel.description]
                rows = [dict(zip(cols, r)) for r in rel.fetchall()]
            duration_ms = (time.perf_counter() - t0) * 1000

            # Apply PII masking if needed. We mask per referenced table so
            # the same column name from different tables is handled cleanly.
            masked_total: list[str] = []
            if "read:pii" not in principal.scopes:
                for tbl in gate.referenced_tables:
                    rows, masked = self.policy.mask_columns_in_rows(rows, table=tbl)
                    masked_total.extend(masked)
            timer.ok({
                "n_rows": len(rows),
                "duration_ms": round(duration_ms, 2),
                "masked_columns": masked_total,
                "injected_limit": gate.injected_limit,
            })
        return RunSqlResult(
            rows=rows,
            n_rows=len(rows),
            columns=cols,
            referenced_tables=list(gate.referenced_tables),
            injected_limit=gate.injected_limit,
            rewritten_sql=gate.safe_sql,
            duration_ms=round(duration_ms, 2),
            masked_columns=sorted(set(masked_total)),
        )

    def natural_language_query(
        self, bearer: str, question: str, table_hint: str | None = None,
    ) -> NlQueryResult:
        # Requires both query:nl AND read:public — NL queries always run as
        # read-only SELECTs.
        principal, timer = self.authorize(
            bearer, tool="natural_language_query",
            scopes=["read:public", "query:nl"],
            arguments={"question": question, "table_hint": table_hint},
        )
        with timer:
            generated_sql, explanation = self._llm_generate_sql(question, table_hint)
            try:
                gate = enforce_read_only(
                    generated_sql, dialect="duckdb",
                    max_rows=self.settings.max_rows_returned,
                )
            except SqlPolicyError as e:
                timer.denied(f"sql policy: {e}")
                raise
            t0 = time.perf_counter()
            with cursor() as conn:
                rel = conn.execute(gate.safe_sql)
                cols = [d[0] for d in rel.description]
                rows = [dict(zip(cols, r)) for r in rel.fetchall()]
            duration_ms = (time.perf_counter() - t0) * 1000

            masked_total: list[str] = []
            if "read:pii" not in principal.scopes:
                for tbl in gate.referenced_tables:
                    rows, masked = self.policy.mask_columns_in_rows(rows, table=tbl)
                    masked_total.extend(masked)
            timer.ok({
                "n_rows": len(rows),
                "duration_ms": round(duration_ms, 2),
                "masked_columns": masked_total,
                "generated_sql": generated_sql,
            })
        run_result = RunSqlResult(
            rows=rows,
            n_rows=len(rows),
            columns=cols,
            referenced_tables=list(gate.referenced_tables),
            injected_limit=gate.injected_limit,
            rewritten_sql=gate.safe_sql,
            duration_ms=round(duration_ms, 2),
            masked_columns=sorted(set(masked_total)),
        )
        return NlQueryResult(
            question=question,
            generated_sql=generated_sql,
            explanation=explanation,
            result=run_result,
        )

    # -- internal helpers ---------------------------------------------------

    def _llm_generate_sql(self, question: str, table_hint: str | None) -> tuple[str, str]:
        """Ask the LLM for a single SELECT plus a one-sentence explanation.

        We give it the full schema as context. The LLM uses OpenAI structured
        output so we don't have to parse free-form prose.
        """
        s = self.settings
        if not s.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for natural_language_query")
        # Build schema description from catalog
        tables = catalog.list_tables()
        schema_lines: list[str] = []
        for t in tables:
            info = catalog.describe_table(t["table_name"])
            cols = ", ".join(f"{c['name']} {c['type']}" for c in info["columns"])
            schema_lines.append(f"- {t['table_name']}({cols})")
        schema_text = "\n".join(schema_lines)

        client = OpenAI(api_key=s.openai_api_key)
        system = (
            "You translate a business question into a single read-only DuckDB "
            "SELECT statement. NEVER produce DDL or DML. Use only the tables "
            "and columns in the schema. Use lower_snake_case in output. Always "
            "include a sensible LIMIT (<= 1000). Reply with strict JSON only, "
            "matching the requested schema."
        )
        user = (
            f"SCHEMA:\n{schema_text}\n\n"
            f"QUESTION: {question}\n"
            + (f"\nUSER HINT: focus on table `{table_hint}`." if table_hint else "")
        )

        class _NlOut(BaseModel):
            sql: str
            explanation: str

        completion = client.beta.chat.completions.parse(
            model=s.gen_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=_NlOut,
            temperature=0.0,
        )
        msg = completion.choices[0].message
        if msg.refusal or msg.parsed is None:
            raise RuntimeError(f"NL->SQL refused: {msg.refusal}")
        return msg.parsed.sql, msg.parsed.explanation
