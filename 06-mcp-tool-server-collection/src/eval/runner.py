"""Run the golden test cases directly against the in-process tool service.

We deliberately bypass the MCP transport so the eval is fast and stable. The
cases exercise every security control (auth, scopes, SQL gate, PII, row-limit
injection) plus shape checks on real query results.

For a separate transport-level smoke test, see `src.cli smoke-http`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .golden import ToolCase, all_cases
from ..security.auth import AuthError
from ..security.rate_limit import RateLimitError
from ..security.sql_gate import SqlPolicyError
from ..server.tools import ToolService

logger = logging.getLogger(__name__)


def _principal_token(service: ToolService, principal_id: str) -> str | None:
    """Look up the bearer secret for a principal id, given the demo token store."""
    if principal_id == "<none>":
        return None
    for secret, p in service.tokens._by_secret.items():  # noqa: SLF001
        if p.token_id == principal_id:
            return secret
    return None


def _resolve_bearer(service: ToolService, case: ToolCase) -> str | None:
    """Replace the placeholder bearer string in arguments with the real
    secret (or None for the auth-missing case)."""
    raw = case.arguments.get("bearer")
    if raw == "":  # explicit "missing bearer" case
        return ""
    if raw and not raw.startswith("tok_") and not raw.startswith("<"):
        # Already a real string (e.g. invalid_token case)
        return raw
    if case.principal == "<none>":
        return None
    return _principal_token(service, case.principal)


def _invoke(service: ToolService, case: ToolCase) -> dict[str, Any]:
    bearer = _resolve_bearer(service, case)
    args = {k: v for k, v in case.arguments.items() if k != "bearer"}
    try:
        if case.tool == "list_tables":
            r = service.list_tables(bearer or "")
            return {"outcome": "ok", "result": r.model_dump()}
        if case.tool == "describe_table":
            r = service.describe_table(bearer or "", **args)
            return {"outcome": "ok", "result": r.model_dump()}
        if case.tool == "sample_rows":
            r = service.sample_rows(bearer or "", **args)
            return {"outcome": "ok", "result": r.model_dump()}
        if case.tool == "run_select_sql":
            r = service.run_select_sql(bearer or "", **args)
            return {"outcome": "ok", "result": r.model_dump()}
        if case.tool == "natural_language_query":
            r = service.natural_language_query(bearer or "", **args)
            return {"outcome": "ok", "result": r.model_dump()}
        return {"outcome": "error", "error": f"unknown tool {case.tool}"}
    except (AuthError, SqlPolicyError, RateLimitError) as e:
        return {"outcome": "denied", "error": str(e)}
    except Exception as e:
        return {"outcome": "error", "error": f"{type(e).__name__}: {e}"}


def _check_case(case: ToolCase, observed: dict[str, Any]) -> tuple[bool, str]:
    if observed["outcome"] != case.expect:
        return False, f"expected {case.expect!r}, got {observed['outcome']!r}: {observed.get('error') or ''}"
    if case.expect_substring:
        haystack = observed.get("error") or json.dumps(observed.get("result", {}))
        if case.expect_substring.lower() not in haystack.lower():
            return False, f"expected substring {case.expect_substring!r} in result/error"
    if case.expect == "ok":
        result = observed.get("result", {})
        # NlQueryResult wraps a RunSqlResult under `.result`; unwrap once.
        inner = result.get("result", result)
        if case.expect_min_rows is not None:
            n = inner.get("n_rows")
            if n is None and "rows" in inner:
                n = len(inner["rows"])
            if n is None or n < case.expect_min_rows:
                return False, f"expected n_rows >= {case.expect_min_rows}, got {n}"
        if case.expect_referenced_tables:
            ref = set(inner.get("referenced_tables", []))
            if not set(case.expect_referenced_tables).issubset(ref):
                return False, f"expected referenced_tables to include {case.expect_referenced_tables}, got {sorted(ref)}"
        if case.expect_no_pii_in_columns:
            rows = inner.get("rows") or []
            for col in case.expect_no_pii_in_columns:
                for row in rows:
                    if col in row:
                        v = row[col]
                        if isinstance(v, str) and "***" not in v:
                            return False, f"PII column {col!r} value {v!r} not masked"
    return True, ""


def run_eval(*, out_dir: str = "results") -> dict[str, Any]:
    service = ToolService()
    cases = all_cases()
    rows: list[dict[str, Any]] = []
    for case in cases:
        observed = _invoke(service, case)
        passed, reason = _check_case(case, observed)
        rows.append({
            "name": case.name,
            "tool": case.tool,
            "principal": case.principal,
            "expected": case.expect,
            "observed_outcome": observed["outcome"],
            "passed": passed,
            "reason": reason,
            "error": observed.get("error"),
            "result_summary": _summarize_result(observed.get("result")),
        })

    n = len(rows)
    summary = {
        "n_cases": n,
        "n_passed": sum(1 for r in rows if r["passed"]),
        "n_failed": sum(1 for r in rows if not r["passed"]),
    }

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(out_dir) / "mcp_eval.json"
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    return summary


def _summarize_result(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if r is None:
        return None
    keep = {}
    for k in ("n_rows", "referenced_tables", "injected_limit", "masked_columns",
             "duration_ms", "rewritten_sql", "table_name", "n_columns"):
        if k in r:
            keep[k] = r[k]
    return keep
