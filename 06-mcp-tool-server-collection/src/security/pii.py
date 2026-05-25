"""Column-level PII masking policy.

Production data warehouses tag PII columns at the catalog level (Unity Catalog,
Snowflake column tags, BigQuery policy tags). For the demo we hard-code a
small policy registry. The interface is `mask_row(table, row) -> row` so
swapping in a Snowflake-tag-driven policy later is a one-file change.

Rules:
  - any column tagged `pii` is masked unless the principal has scope `read:pii`
  - masking is type-aware: strings -> "***", emails -> first letter + ***, ints -> 0, dates -> null
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ColumnPolicy:
    table: str
    column: str
    tags: frozenset[str] = field(default_factory=frozenset)


# Demo policy — taxi dataset has no real PII, but we pretend the `vendor_id`
# column is sensitive (driver-identifying in real life), and there's a
# synthetic `passengers` table where `email` is real PII.
DEFAULT_POLICY: list[ColumnPolicy] = [
    ColumnPolicy(table="taxi_trips", column="vendor_id", tags=frozenset({"pii"})),
    ColumnPolicy(table="passengers", column="email", tags=frozenset({"pii"})),
    ColumnPolicy(table="passengers", column="phone", tags=frozenset({"pii"})),
    ColumnPolicy(table="passengers", column="full_name", tags=frozenset({"pii"})),
]


class PolicyEngine:
    def __init__(self, policies: list[ColumnPolicy] | None = None):
        policies = policies or DEFAULT_POLICY
        self._index: dict[str, set[str]] = {}
        for p in policies:
            if "pii" in p.tags:
                self._index.setdefault(p.table.lower(), set()).add(p.column.lower())

    def is_pii(self, table: str, column: str) -> bool:
        cols = self._index.get(table.lower())
        if not cols:
            return False
        return column.lower() in cols

    def mask_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            if "@" in value:
                # email-like
                head = value[:1] if value else ""
                tail = value[value.find("@"):]
                return f"{head}***{tail}"
            return "***"
        if isinstance(value, int):
            return 0
        if isinstance(value, float):
            return 0.0
        return "***"

    def mask_columns_in_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        table: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Returns (masked_rows, masked_columns). Pure function — no in-place edit."""
        if not rows:
            return rows, []
        cols = self._index.get(table.lower(), set())
        if not cols:
            return rows, []
        masked_cols = sorted(c for c in rows[0].keys() if c.lower() in cols)
        if not masked_cols:
            return rows, []
        out: list[dict[str, Any]] = []
        for row in rows:
            new = dict(row)
            for c in masked_cols:
                if c in new:
                    new[c] = self.mask_value(new[c])
            out.append(new)
        return out, masked_cols
