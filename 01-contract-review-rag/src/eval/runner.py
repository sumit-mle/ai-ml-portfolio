"""Run a technique over the golden Q/A set and write per-question results."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from typing import Any

from .golden import GoldenQA, build_goldens
from .metrics import answer_quotes_clause, citation_correct, clause_match
from ..shared.corpus import Contract


def _load_technique(backend: str, technique: str):
    mod_name = f"src.{backend}_impl.{technique}"
    return import_module(mod_name)


def run_eval(
    contracts: list[Contract],
    *,
    backend: str,
    technique: str,
    top_k: int = 4,
    out_dir: str | os.PathLike[str] = "results",
) -> dict[str, Any]:
    mod = _load_technique(backend, technique)
    goldens: list[GoldenQA] = build_goldens(contracts)

    rows: list[dict[str, Any]] = []
    for g in goldens:
        # Restrict the corpus to the contract this golden belongs to so we
        # measure clause-finding within a doc, not whole-corpus disambiguation.
        single = [c for c in contracts if c.doc_id == g.doc_id]
        result = mod.run(g.question, single, top_k=top_k)
        retrieved = getattr(result, "retrieved", [])
        answer = getattr(result, "answer", str(result))
        row = {
            "question": g.question,
            "doc_id": g.doc_id,
            "category": g.category,
            "clause_match": clause_match(retrieved, g),
            "citation_correct": citation_correct(retrieved, g),
            "answer_quotes_clause": answer_quotes_clause(answer, g),
            "retrieved_ids": [c.chunk_id for c in retrieved],
            "answer": answer,
        }
        rows.append(row)

    n = max(1, len(rows))
    summary = {
        "backend": backend,
        "technique": technique,
        "n_questions": len(rows),
        "clause_match_rate": sum(r["clause_match"] for r in rows) / n,
        "citation_correct_rate": sum(r["citation_correct"] for r in rows) / n,
        "answer_quotes_rate": sum(r["answer_quotes_clause"] for r in rows) / n,
    }

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    fname = out_path / f"{backend}__{technique}.json"
    fname.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    return summary
