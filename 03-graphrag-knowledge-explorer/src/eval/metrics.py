"""Eval metrics for Graph RAG vs Vector RAG over real SEC filings."""
from __future__ import annotations

import re

from .golden import GoldenQA


# SEC accession numbers look like 0001234567-24-000001 (10 + 2 + 6 digits).
_ACC_RE = re.compile(r"\d{10}-\d{2}-\d{6}")


def cited_filings(answer: str) -> list[str]:
    return list(set(_ACC_RE.findall(answer or "")))


def must_mention_hit(answer: str, gold: GoldenQA) -> float:
    if not gold.must_mention:
        return 1.0
    text = (answer or "").lower()
    hits = sum(1 for s in gold.must_mention if s.lower() in text)
    return hits / len(gold.must_mention)


def filing_recall(answer: str, gold: GoldenQA) -> float:
    expected = set(gold.expected_filings)
    if not expected:
        return 1.0
    cited = set(cited_filings(answer))
    return sum(1 for f in expected if f in cited) / len(expected)


def answered(answer: str) -> int:
    lower = (answer or "").lower()
    return int("not found" not in lower)
