"""Run a SUT over the golden set, compute all metrics, persist a Result.

Result file layout:
    results/runs/<sut_name>__<timestamp>.json
        {
            "sut": "...",
            "ts": "2026-05-25T...",
            "n_questions": int,
            "summary": { metric: mean, ... },
            "rows": [ per-question row ],
        }

Baselines live under baselines/<sut_name>.json — same schema as a single
run. The CLI's `gate` command compares a fresh run to the matching
baseline and exits non-zero if any metric is below tolerance.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_settings
from .golden import GoldenQA, load_golden
from .metrics import (
    answer_quotes_clause,
    citation_correct,
    clause_match,
    context_recall,
    llm_judge,
)
from .sut.interface import RunOutput, get_sut

logger = logging.getLogger(__name__)


# Project 01's chunk_id format: "<doc_id>::<section_idx>::<chunk_idx>"
# We can't recover absolute char offsets from that alone, but the substring
# fallback in metrics.clause_match handles both cases.
_CHUNK_ID_RE = re.compile(r"^(?P<doc_id>[^:]+)::\d+::\d+$")


def _parse_chunk_offsets(out: RunOutput) -> list[tuple[str, int, int]]:
    """Best-effort offset reconstruction.

    Project 01's chunker stamps Chunk.start/end into chunk metadata when it
    crosses through the LangChain Document round-trip, but the technique
    `run()` wrappers don't always surface those fields. We fall back to
    (doc_id, 0, 0) and let the substring path do the work.
    """
    out_list: list[tuple[str, int, int]] = []
    for cid, doc_id in zip(out.retrieved_chunk_ids, out.retrieved_doc_ids):
        # No reliable offset from chunk_id; use placeholder so the metric
        # falls through to substring matching.
        out_list.append((doc_id, 0, 0))
    return out_list


def _row_for_question(sut, golden: GoldenQA, *, use_judge: bool) -> dict[str, Any]:
    t0 = time.perf_counter()
    out: RunOutput = sut.run(golden.question, golden.contract_doc_id)
    duration_ms = (time.perf_counter() - t0) * 1000

    chunk_offsets = _parse_chunk_offsets(out)

    row: dict[str, Any] = {
        "qid": golden.qid,
        "category": golden.category,
        "doc_id": golden.contract_doc_id,
        "duration_ms": round(duration_ms, 1),
        "answer": out.answer,
        "retrieved_chunk_ids": out.retrieved_chunk_ids,
        "clause_match": clause_match(chunk_offsets, out.retrieved_text, golden),
        "citation_correct": citation_correct(out.retrieved_doc_ids, golden),
        "context_recall": context_recall(out.retrieved_text, golden),
        "answer_quotes_clause": answer_quotes_clause(out.answer, golden),
    }

    if use_judge:
        scores = llm_judge(golden.question, out.answer, out.retrieved_text)
        row["faithfulness"] = scores.faithfulness
        row["answer_relevancy"] = scores.answer_relevancy
        row["judge_rationale"] = scores.rationale[:300]
    else:
        row["faithfulness"] = None
        row["answer_relevancy"] = None
        row["judge_rationale"] = "(skipped)"

    return row


def _summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    n = max(1, len(rows))
    keys = ("clause_match", "citation_correct", "context_recall",
            "answer_quotes_clause", "faithfulness", "answer_relevancy")
    out: dict[str, float] = {}
    for k in keys:
        vals = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        out[k] = round(sum(vals) / max(1, len(vals)), 3) if vals else 0.0
    out["avg_duration_ms"] = round(
        sum(r["duration_ms"] for r in rows) / n, 1
    )
    return out


def run_sut(sut_name: str, *, use_judge: bool = True) -> dict[str, Any]:
    s = get_settings()
    sut = get_sut(sut_name)
    goldens = load_golden()
    if not goldens:
        raise RuntimeError(
            "Golden set is empty. Make sure PROJECT_01_PATH points to "
            "01-contract-review-rag and that its sample corpus loads."
        )
    rows: list[dict[str, Any]] = []
    for g in goldens:
        try:
            rows.append(_row_for_question(sut, g, use_judge=use_judge))
        except Exception as e:
            logger.exception("Question %s failed", g.qid)
            rows.append({
                "qid": g.qid,
                "category": g.category,
                "doc_id": g.contract_doc_id,
                "error": f"{type(e).__name__}: {e}",
                "clause_match": 0.0, "citation_correct": 0.0,
                "context_recall": 0.0, "answer_quotes_clause": 0.0,
                "faithfulness": 0.0 if use_judge else None,
                "answer_relevancy": 0.0 if use_judge else None,
            })
    summary = _summarize(rows)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record = {
        "sut": sut_name,
        "ts": ts,
        "n_questions": len(rows),
        "use_judge": use_judge,
        "summary": summary,
        "rows": rows,
    }
    out_dir = s.results_dir / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sut_name.replace('.', '_')}__{ts}.json"
    out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    logger.info("Wrote %s", out_path)
    return record


# ---------------------------------------------------------------------------
# Baselines + regression gate
# ---------------------------------------------------------------------------


def baseline_path(sut_name: str) -> Path:
    s = get_settings()
    s.baselines_dir.mkdir(parents=True, exist_ok=True)
    return s.baselines_dir / f"{sut_name.replace('.', '_')}.json"


def save_as_baseline(record: dict[str, Any]) -> Path:
    path = baseline_path(record["sut"])
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def load_baseline(sut_name: str) -> dict[str, Any] | None:
    path = baseline_path(sut_name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def detect_regressions(
    current: dict[str, Any], baseline: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return a list of regression issues (empty list = pass)."""
    s = get_settings()
    cur_sum = current["summary"]
    base_sum = baseline["summary"]
    issues: list[dict[str, Any]] = []
    checks = [
        ("faithfulness", s.tol_faithfulness),
        ("citation_correct", s.tol_citation),
        ("clause_match", s.tol_clause_match),
        ("context_recall", s.tol_clause_match),
        ("answer_quotes_clause", s.tol_citation),
    ]
    for metric, tol in checks:
        cur = cur_sum.get(metric)
        base = base_sum.get(metric)
        if cur is None or base is None:
            continue
        delta = cur - base
        if delta < -tol:
            issues.append({
                "metric": metric,
                "baseline": round(base, 3),
                "current": round(cur, 3),
                "delta": round(delta, 3),
                "tolerance": tol,
            })
    return issues
