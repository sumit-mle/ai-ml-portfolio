"""Retrieval-order drift detection.

Two runs of the same SUT on the same questions should produce similar
retrieval orders. Big swings between runs indicate silent embedding drift,
index re-build issues, or non-determinism creeping in. We measure with
Kendall's tau over the per-question retrieved chunk lists.

  tau ~ 1.0   identical order
  tau ~ 0.0   uncorrelated
  tau ~ -1.0  reversed

Production threshold: tau < 0.7 on >10% of questions usually means
something changed. We surface exactly that signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scipy.stats import kendalltau


@dataclass
class DriftReport:
    n_questions: int
    n_with_drift: int           # questions where tau < 0.7
    avg_tau: float
    per_question: list[dict]


def _list_to_rank(items: list[str]) -> list[int]:
    """Map a chunk_id list to ranks {chunk_id: rank}."""
    return list(range(len(items)))


def compute_drift(run_a: dict[str, Any], run_b: dict[str, Any]) -> DriftReport:
    """Compare two run records (typically same SUT, different time).

    For each shared qid we compute Kendall-tau over the intersection of
    their retrieved chunk lists. Questions with no overlap get tau=0.0.
    """
    a_rows = {r["qid"]: r for r in run_a.get("rows", [])}
    b_rows = {r["qid"]: r for r in run_b.get("rows", [])}
    qids = sorted(set(a_rows) & set(b_rows))

    per: list[dict] = []
    n_drift = 0
    taus: list[float] = []
    for qid in qids:
        a_ids = a_rows[qid].get("retrieved_chunk_ids", [])
        b_ids = b_rows[qid].get("retrieved_chunk_ids", [])
        common = [x for x in a_ids if x in b_ids]
        if len(common) < 2:
            tau = 0.0 if not common else 1.0
        else:
            a_ranks = [a_ids.index(x) for x in common]
            b_ranks = [b_ids.index(x) for x in common]
            res = kendalltau(a_ranks, b_ranks)
            tau = float(res.statistic) if res.statistic is not None else 0.0
            if tau != tau:                  # NaN check (single-element edge)
                tau = 0.0
        taus.append(tau)
        if tau < 0.7:
            n_drift += 1
        per.append({
            "qid": qid,
            "tau": round(tau, 3),
            "a_chunks": a_ids[:6],
            "b_chunks": b_ids[:6],
            "shared": len(common),
        })

    return DriftReport(
        n_questions=len(qids),
        n_with_drift=n_drift,
        avg_tau=round(sum(taus) / max(1, len(taus)), 3),
        per_question=per,
    )
