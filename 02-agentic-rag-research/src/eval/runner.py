"""Run the agent over the golden Q/A set and write results."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .golden import GoldenQA, load_golden
from .metrics import (
    cited_pmids,
    citation_recall,
    context_precision,
    context_recall,
    honest_abstain,
    must_mention_hit,
)
from ..agent.graph import run_agent
from ..shared.corpus import Abstract


def run_eval(
    abstracts: list[Abstract],
    *,
    top_k: int = 5,
    max_iterations: int = 2,
    out_dir: str | os.PathLike[str] = "results",
    label: str = "agentic_rag",
) -> dict[str, Any]:
    goldens: list[GoldenQA] = load_golden()
    rows: list[dict[str, Any]] = []

    for g in goldens:
        result = run_agent(
            g.question,
            abstracts,
            top_k=top_k,
            max_iterations=max_iterations,
        )
        retrieved_pmids = [d.pmid for d in result.retrieved]
        rows.append(
            {
                "qid": g.qid,
                "question": g.question,
                "relevant_pmids": list(g.relevant_pmids),
                "retrieved_pmids": retrieved_pmids,
                "cited_pmids": cited_pmids(result.final_answer),
                "context_precision": context_precision(retrieved_pmids, g),
                "context_recall": context_recall(retrieved_pmids, g),
                "citation_recall": citation_recall(result.final_answer, g),
                "must_mention_hit": must_mention_hit(result.final_answer, g),
                "honest_abstain": honest_abstain(result.final_answer, g),
                "iterations": result.iterations,
                "critique": result.critique,
                "trace": result.trace,
                "answer": result.final_answer,
            }
        )

    n = max(1, len(rows))
    summary = {
        "label": label,
        "n_questions": len(rows),
        "context_precision": sum(r["context_precision"] for r in rows) / n,
        "context_recall": sum(r["context_recall"] for r in rows) / n,
        "citation_recall": sum(r["citation_recall"] for r in rows) / n,
        "must_mention_hit": sum(r["must_mention_hit"] for r in rows) / n,
        "honest_abstain_rate": sum(r["honest_abstain"] for r in rows) / n,
        "avg_iterations": sum(r["iterations"] for r in rows) / n,
    }

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    fname = out_path / f"{label}.json"
    fname.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    return summary
