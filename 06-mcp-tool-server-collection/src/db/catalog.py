"""Read-only catalog inspection helpers for the MCP tools."""
from __future__ import annotations

from typing import Any

from .driver import cursor


def list_tables() -> list[dict[str, Any]]:
    sql = (
        "SELECT table_name, "
        "       (SELECT count(*) FROM information_schema.columns c "
        "        WHERE c.table_name = t.table_name AND c.table_schema = 'main') AS n_columns "
        "FROM information_schema.tables t "
        "WHERE t.table_schema = 'main' "
        "ORDER BY table_name"
    )
    with cursor() as conn:
        rows = conn.execute(sql).fetchall()
    return [{"table_name": r[0], "n_columns": r[1]} for r in rows]


def describe_table(table: str) -> dict[str, Any]:
    schema_sql = (
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'main' AND table_name = ? "
        "ORDER BY ordinal_position"
    )
    count_sql = f'SELECT count(*) FROM "{table}"'
    with cursor() as conn:
        cols = conn.execute(schema_sql, [table]).fetchall()
        if not cols:
            return {"table_name": table, "exists": False, "columns": [], "row_count": 0}
        try:
            row_count = conn.execute(count_sql).fetchone()[0]
        except Exception:
            row_count = -1
    return {
        "table_name": table,
        "exists": True,
        "row_count": int(row_count),
        "columns": [
            {"name": r[0], "type": r[1], "nullable": r[2] == "YES"} for r in cols
        ],
    }


def sample_rows(table: str, n: int = 5) -> list[dict[str, Any]]:
    n = max(1, min(100, n))
    with cursor() as conn:
        rel = conn.execute(f'SELECT * FROM "{table}" USING SAMPLE {n} ROWS')
        cols = [d[0] for d in rel.description]
        rows = rel.fetchall()
    return [dict(zip(cols, r)) for r in rows]
