"""Read-only SQL enforcement (sqlglot-based).

Identical pattern to project 06's enterprise data-platform server. We
duplicate it here rather than depend across projects so each project
remains stand-alone for portfolio review.

Allowed: SELECT, WITH ... SELECT, UNION/INTERSECT, SHOW, DESCRIBE
Denied: any DDL/DML/COPY, multiple statements, dangerous PRAGMAs.

We also inject a LIMIT if the outermost SELECT lacks one — the agent
sometimes asks for "show me everything" and an unbounded scan over a
50k-row table is fine here, but in production the cap matters.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import expressions as exp


_READ_ONLY_ROOTS: tuple[type, ...] = (
    exp.Select, exp.Union, exp.Subquery, exp.Show, exp.Describe, exp.With,
)
_FORBIDDEN: tuple[type, ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
    exp.Alter, exp.AlterColumn, exp.TruncateTable, exp.Merge, exp.Copy,
)


class SqlPolicyError(Exception):
    pass


@dataclass(frozen=True)
class GateResult:
    safe_sql: str
    referenced_tables: tuple[str, ...]
    injected_limit: int | None


def enforce_read_only(sql: str, *, dialect: str = "duckdb",
                      max_rows: int = 2000) -> GateResult:
    sql = (sql or "").strip().rstrip(";").rstrip()
    if not sql:
        raise SqlPolicyError("empty query")
    if ";" in sql:
        raise SqlPolicyError("multiple statements not allowed")

    try:
        parsed = sqlglot.parse(sql, dialect=dialect)
    except Exception as e:
        raise SqlPolicyError(f"could not parse SQL: {e}") from None
    if not parsed or len(parsed) != 1 or parsed[0] is None:
        raise SqlPolicyError("expected exactly one statement")

    root = parsed[0]
    for klass in _FORBIDDEN:
        if list(root.find_all(klass)):
            raise SqlPolicyError(f"statement type {klass.__name__} is not allowed")
    if not isinstance(root, _READ_ONLY_ROOTS):
        raise SqlPolicyError(
            f"only read-only statements allowed (got {root.__class__.__name__})"
        )

    injected: int | None = None
    if isinstance(root, (exp.Select, exp.Union)) and root.args.get("limit") is None:
        root = root.limit(max_rows)
        injected = max_rows

    tables = tuple(
        sorted({t.name for t in root.find_all(exp.Table) if t.name})
    )
    return GateResult(safe_sql=root.sql(dialect=dialect),
                      referenced_tables=tables,
                      injected_limit=injected)
