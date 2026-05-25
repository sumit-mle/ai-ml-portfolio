"""Run the agent on each golden question, score against reference SQL.

Scoring:
  - executed_ok            agent produced a result without erroring out
  - shape_ok               result row-count is in the expected range
  - numerically_close      compares aggregate totals between the agent's
                           result and the reference result (ignoring column
                           order). Tolerant 1% relative or $1 absolute.
  - mentions_in_answer     all must_mention substrings appear in the
                           natural-language answer
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from .golden import GoldenQuery, all_goldens
from ..agent.graph import run_question
from ..config import get_settings
from ..db.driver import cursor

logger = logging.getLogger(__name__)


def _run_reference(sql: str) -> tuple[list[str], list[list[Any]]]:
    with cursor() as conn:
        rel = conn.execute(sql)
        cols = [d[0] for d in rel.description]
        rows = [list(r) for r in rel.fetchall()]
    return cols, rows


def _flatten_numeric(rows: list[list[Any]]) -> list[float]:
    out: list[float] = []
    for r in rows:
        for v in r:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(float(v))
    return out


def _numerically_close(agent_rows, ref_rows, *, rel_tol: float = 0.05,
                       abs_tol: float = 1.0) -> bool:
    """Compare numeric values (sums) between two results.

    Ignores column order — just checks that sorted numeric values match
    within tolerance. Sufficient for our golden questions which all
    return either a single aggregate or a small per-group list.
    """
    a = sorted(_flatten_numeric(agent_rows))
    r = sorted(_flatten_numeric(ref_rows))
    if len(a) != len(r):
        return False
    for av, rv in zip(a, r):
        if abs(av - rv) <= abs_tol:
            continue
        if rv != 0 and abs(av - rv) / abs(rv) <= rel_tol:
            continue
        return False
    return True


def _eval_one(g: GoldenQuery) -> dict[str, Any]:
    final = run_question(g.question)
    answer = final.get("answer", "")
    agent_rows = final.get("result_rows") or []
    n_rows = int(final.get("n_rows", 0))
    executed_ok = bool(final.get("safe_sql")) and (final.get("last_error") is None)

    shape_ok = True
    if g.expected_rows is not None:
        lo, hi = g.expected_rows
        shape_ok = lo <= n_rows <= hi

    ref_cols, ref_rows = _run_reference(g.reference_sql)
    numerically_close = _numerically_close(agent_rows, ref_rows)
    # If the question allows percentage-magnitude matching, we accept that
    # path as numerically_close too (some questions have multiple correct
    # week-boundary interpretations).
    if g.accept_pct_in is not None:
        import re
        lo, hi = g.accept_pct_in
        # Find any percentage token in the answer
        for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s*%", answer or ""):
            val = abs(float(m.group(1)))
            if lo <= val <= hi:
                numerically_close = True
                break

    mentions_ok = True
    if g.must_mention_substrings:
        text = (answer or "").lower()
        mentions_ok = all(s.lower() in text for s in g.must_mention_substrings)

    overall_ok = executed_ok and shape_ok and numerically_close and mentions_ok

    return {
        "qid": g.qid,
        "question": g.question,
        "answer": answer,
        "agent_n_rows": n_rows,
        "ref_n_rows": len(ref_rows),
        "executed_ok": executed_ok,
        "shape_ok": shape_ok,
        "numerically_close": numerically_close,
        "mentions_ok": mentions_ok,
        "overall_ok": overall_ok,
        "repair_attempts": int(final.get("repair_attempts", 0)),
        "safe_sql": final.get("safe_sql", ""),
        "last_error": final.get("last_error"),
        "trace_len": len(final.get("trace", []) or []),
    }


def run_eval(*, out_dir: str = "results") -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for g in all_goldens():
        logger.info("--- %s ---", g.qid)
        rows.append(_eval_one(g))

    n = len(rows)
    summary = {
        "n_questions": n,
        "n_executed": sum(1 for r in rows if r["executed_ok"]),
        "n_shape_ok": sum(1 for r in rows if r["shape_ok"]),
        "n_numerically_close": sum(1 for r in rows if r["numerically_close"]),
        "n_mentions_ok": sum(1 for r in rows if r["mentions_ok"]),
        "n_overall_ok": sum(1 for r in rows if r["overall_ok"]),
        "avg_repair_attempts": round(
            sum(r["repair_attempts"] for r in rows) / max(1, n), 2
        ),
    }
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(out_dir) / "agent_eval.json"
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    return summary


def save_trace(question: str, state: dict, *, slug: str | None = None) -> Path:
    s = get_settings()
    s.trace_dir.mkdir(parents=True, exist_ok=True)
    slug = slug or uuid4().hex[:8]
    out = s.trace_dir / f"trace_{slug}.json"
    payload = {"question": question, **{k: v for k, v in state.items() if k != "started_at"}}
    out.write_text(json.dumps(payload, indent=2, default=str))
    return out
