"""FastMCP 3.x server — registers the five enterprise data platform tools."""
from __future__ import annotations

import logging

from fastmcp import FastMCP

from ..config import get_settings
from .tools import (
    DescribeTableResult,
    ListTablesResult,
    NlQueryResult,
    RunSqlResult,
    SampleRowsResult,
    ToolService,
)

logger = logging.getLogger(__name__)


def build_app() -> FastMCP:
    s = get_settings()
    service = ToolService()
    app = FastMCP(
        name=s.server_name,
        version=s.server_version,
        instructions=(
            "Enterprise data platform: read-only SQL access to the analytics "
            "warehouse. Every tool requires a bearer token; tokens are scoped. "
            "Public table reads need read:public; PII unmask needs read:pii; "
            "natural-language SQL needs query:nl in addition to read:public."
        ),
    )

    @app.tool(
        name="list_tables",
        description="List all tables in the analytics warehouse. Requires scope: read:public.",
    )
    def list_tables(bearer: str) -> ListTablesResult:
        return service.list_tables(bearer)

    @app.tool(
        name="describe_table",
        description=(
            "Return the schema and row count of a table, including which "
            "columns are flagged PII. Requires scope: read:public."
        ),
    )
    def describe_table(bearer: str, table: str) -> DescribeTableResult:
        return service.describe_table(bearer, table)

    @app.tool(
        name="sample_rows",
        description=(
            "Return up to N random rows from a table. PII columns are masked "
            "unless the principal also has read:pii. Requires scope: read:public."
        ),
    )
    def sample_rows(bearer: str, table: str, n: int = 5) -> SampleRowsResult:
        return service.sample_rows(bearer, table, n=n)

    @app.tool(
        name="run_select_sql",
        description=(
            "Execute a read-only SELECT against the warehouse. The SQL gate "
            "rejects any DDL/DML and injects a LIMIT if missing. PII columns "
            "are masked unless the principal also has read:pii. Required "
            "scope: read:public."
        ),
    )
    def run_select_sql(bearer: str, sql: str) -> RunSqlResult:
        return service.run_select_sql(bearer, sql)

    @app.tool(
        name="natural_language_query",
        description=(
            "Translate a business question into a read-only SELECT, run it, "
            "and return the rows. Requires scopes: read:public, query:nl."
        ),
    )
    def natural_language_query(
        bearer: str,
        question: str,
        table_hint: str | None = None,
    ) -> NlQueryResult:
        return service.natural_language_query(bearer, question, table_hint=table_hint)

    return app


def run_stdio() -> None:
    app = build_app()
    app.run()


def run_http() -> None:
    s = get_settings()
    app = build_app()
    # FastMCP 3.x speaks "streamable-http" for the modern transport
    app.run(transport="http", host=s.http_host, port=s.http_port)
