"""Read-only SQL enforcement.

Uses sqlglot to parse the query AST and reject anything that mutates state.
Belt-and-braces: even if the DuckDB connection is opened read-only, we want
to fail fast at the application layer with a clear error before the engine
ever sees the statement.

Allowed:
  - SELECT (with all clauses: JOIN, WITH, WINDOW, UNION, etc.)
  - SHOW, DESCRIBE, EXPLAIN, PRAGMA (read-only metadata)
  - WITH ... SELECT
Denied:
  - Any DDL: CREATE, DROP, ALTER, TRUNCATE
  - Any DML: INSERT, UPDATE, DELETE, MERGE, COPY (for writes), ATTACH, DETACH
  - Multiple statements (no SQL injection via semicolons)
  - PRAGMA writes (PRAGMA enable_external_access, etc.)

We also enforce a row limit by injecting LIMIT N if the query has none.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

import sqlglot
from sqlglot import expressions as exp

logger = logging.getLogger(__name__)


# Read-only roots we accept (sqlglot expression classes).
_READ_ONLY_ROOTS: tuple[type, ...] = (
    exp.Select,
    exp.Union,
    exp.Subquery,
    exp.Show,
    exp.Describe,
    exp.With,
)

# Forbidden expression classes — anything that writes or attaches must reject.
_FORBIDDEN: tuple[type, ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.AlterColumn,
    exp.TruncateTable,
    exp.Merge,
    exp.Copy,            # COPY ... TO/FROM
)

# PRAGMAs we explicitly allow (read-only catalog inspection only)
_ALLOWED_PRAGMA = {
    "show_tables",
    "table_info",
    "show_databases",
    "version",
    "database_size",
    "memory_limit",
}


class SqlPolicyError(Exception):
    """Raised when a query violates the read-only SQL policy."""


@dataclass(frozen=True)
class SqlGateResult:
    safe_sql: str            # the rewritten SQL we'll actually execute
    referenced_tables: tuple[str, ...]
    had_limit: bool
    injected_limit: int | None


def enforce_read_only(
    sql: str,
    *,
    dialect: str = "duckdb",
    max_rows: int = 10000,
) -> SqlGateResult:
    """Parse and validate. Returns a safe rewritten query or raises."""
    sql = (sql or "").strip()
    if not sql:
        raise SqlPolicyError("empty query")

    # No multi-statement. We strip a single trailing semicolon for ergonomics
    # but anything beyond that is rejected.
    stripped = sql.rstrip().rstrip(";").rstrip()
    if ";" in stripped:
        raise SqlPolicyError("multiple statements not allowed")

    # PRAGMA is special-cased; sqlglot's PRAGMA support is limited and
    # dialect-dependent.
    pragma_match = re.match(r"^\s*PRAGMA\s+([a-zA-Z_]+)", stripped, re.IGNORECASE)
    if pragma_match:
        name = pragma_match.group(1).lower()
        if name not in _ALLOWED_PRAGMA:
            raise SqlPolicyError(f"PRAGMA '{name}' is not in the allow-list")
        return SqlGateResult(
            safe_sql=stripped,
            referenced_tables=(),
            had_limit=True,  # PRAGMA results are bounded
            injected_limit=None,
        )

    try:
        parsed = sqlglot.parse(stripped, dialect=dialect)
    except Exception as e:
        raise SqlPolicyError(f"could not parse SQL: {e}") from e

    if not parsed:
        raise SqlPolicyError("no statements parsed")
    if len(parsed) != 1 or parsed[0] is None:
        raise SqlPolicyError("multiple statements not allowed")

    root = parsed[0]
    # Reject any forbidden expression anywhere in the tree.
    for forbidden_cls in _FORBIDDEN:
        if list(root.find_all(forbidden_cls)):
            raise SqlPolicyError(
                f"statement type {forbidden_cls.__name__} is not allowed"
            )

    # Root must be a read-only kind.
    if not isinstance(root, _READ_ONLY_ROOTS):
        raise SqlPolicyError(
            f"only SELECT/SHOW/DESCRIBE/WITH allowed at top level "
            f"(got {root.__class__.__name__})"
        )

    # Inject LIMIT if missing on the outermost SELECT-like root, so a
    # 'SELECT * FROM trips' can't return 50M rows by accident.
    had_limit = False
    injected: int | None = None
    if isinstance(root, (exp.Select, exp.Union)):
        existing = root.args.get("limit")
        if existing is None:
            root = root.limit(max_rows)
            injected = max_rows
        else:
            had_limit = True

    safe_sql = root.sql(dialect=dialect)
    referenced = tuple(_collect_table_names(root))
    return SqlGateResult(
        safe_sql=safe_sql,
        referenced_tables=referenced,
        had_limit=had_limit,
        injected_limit=injected,
    )


def _collect_table_names(root: exp.Expression) -> Iterable[str]:
    seen: set[str] = set()
    for tbl in root.find_all(exp.Table):
        name = tbl.name
        if name and name not in seen:
            seen.add(name)
            yield name
