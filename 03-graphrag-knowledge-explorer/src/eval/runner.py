"""Run both retrievers over the dynamic golden set and compare."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .golden import GoldenQA, load_golden
from .metrics import answered, cited_filings, filing_recall, must_mention_hit
from ..retrieval import graph_rag, vector_rag

logger = logging.getLogger(__name__)


def _eval_one(technique: str, q: GoldenQA, answer: str) -> dict[str, Any]:
    return {
        "qid": q.qid,
        "pattern": q.pattern,
        "technique": technique,
        "question": q.question,
        "hops": q.hops,
        "must_mention_hit": must_mention_hit(answer, q),
        "filing_recall": filing_recall(answer, q),
        "answered": answered(answer),
        "cited_filings": cited_filings(answer),
        "expected_filings": list(q.expected_filings),
        "answer": answer,
    }


def run_eval(
    *,
    k_chunks: int = 5,
    top_k: int = 5,
    out_dir: str = "results",
) -> dict[str, Any]:
    goldens = load_golden()
    if not goldens:
        raise RuntimeError(
            "Golden set is empty. Ingest some filings first: "
            "`python -m src.cli ingest --cik 0000320193 --limit 2`"
        )
    logger.info("Built %d golden questions from current graph", len(goldens))

    graph_rows: list[dict[str, Any]] = []
    vector_rows: list[dict[str, Any]] = []

    for g in goldens:
        gr = graph_rag.run(g.question, k_chunks=k_chunks)
        graph_rows.append(_eval_one("graph_rag", g, gr.answer))

        vr = vector_rag.run(g.question, top_k=top_k)
        vector_rows.append(_eval_one("vector_rag", g, vr.answer))

    def _summarize(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
        n = max(1, len(rows))
        return {
            "technique": label,
            "n_questions": len(rows),
            "must_mention_hit": sum(r["must_mention_hit"] for r in rows) / n,
            "filing_recall": sum(r["filing_recall"] for r in rows) / n,
            "answered_rate": sum(r["answered"] for r in rows) / n,
        }

    by_pattern_graph: dict[str, list[float]] = {}
    by_pattern_vector: dict[str, list[float]] = {}
    for gr, vr in zip(graph_rows, vector_rows):
        by_pattern_graph.setdefault(gr["pattern"], []).append(gr["must_mention_hit"])
        by_pattern_vector.setdefault(vr["pattern"], []).append(vr["must_mention_hit"])

    by_pattern: dict[str, dict[str, float]] = {}
    for pat in sorted(set(by_pattern_graph) | set(by_pattern_vector)):
        gs = by_pattern_graph.get(pat, [])
        vs = by_pattern_vector.get(pat, [])
        by_pattern[pat] = {
            "n": len(gs),
            "graph_must_mention": sum(gs) / max(1, len(gs)),
            "vector_must_mention": sum(vs) / max(1, len(vs)),
        }

    wins = sum(1 for g, v in zip(graph_rows, vector_rows)
               if g["must_mention_hit"] > v["must_mention_hit"])
    ties = sum(1 for g, v in zip(graph_rows, vector_rows)
               if g["must_mention_hit"] == v["must_mention_hit"])
    losses = len(graph_rows) - wins - ties

    summary = {
        "graph_summary": _summarize(graph_rows, "graph_rag"),
        "vector_summary": _summarize(vector_rows, "vector_rag"),
        "by_pattern": by_pattern,
        "comparison": {
            "graph_wins": wins,
            "ties": ties,
            "vector_wins": losses,
            "graph_win_rate": wins / max(1, len(graph_rows)),
        },
    }

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(out_dir) / "graphrag_vs_vector.json"
    out_path.write_text(json.dumps({**summary,
                                    "graph_rows": graph_rows,
                                    "vector_rows": vector_rows}, indent=2))
    return summary
