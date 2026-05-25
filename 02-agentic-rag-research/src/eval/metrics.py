"""Eval metrics for agentic-RAG literature review.

Metrics computed per question:
  - context_precision: |retrieved ∩ relevant| / |retrieved|       (0 if empty)
  - context_recall:    |retrieved ∩ relevant| / |relevant|        (1 if relevant empty)
  - citation_recall:   |cited_in_answer ∩ relevant| / |relevant|  (1 if relevant empty)
  - must_mention_hit:  fraction of required substrings present in the answer
  - honest_abstain:    for unanswerable questions, did we admit it?
"""
from __future__ import annotations

import re

from .golden import GoldenQA


_PMID_RE = re.compile(r"PMID[:\s]*([0-9]{6,10})", re.IGNORECASE)


def cited_pmids(answer: str) -> list[str]:
    return _PMID_RE.findall(answer or "")


def context_precision(retrieved_pmids: list[str], gold: GoldenQA) -> float:
    if not retrieved_pmids:
        return 0.0
    rel = set(gold.relevant_pmids)
    if not rel:
        return 1.0
    hits = sum(1 for p in retrieved_pmids if p in rel)
    return hits / len(retrieved_pmids)


def context_recall(retrieved_pmids: list[str], gold: GoldenQA) -> float:
    rel = set(gold.relevant_pmids)
    if not rel:
        return 1.0
    return sum(1 for p in rel if p in retrieved_pmids) / len(rel)


def citation_recall(answer: str, gold: GoldenQA) -> float:
    rel = set(gold.relevant_pmids)
    if not rel:
        return 1.0
    cited = set(cited_pmids(answer))
    return sum(1 for p in rel if p in cited) / len(rel)


def must_mention_hit(answer: str, gold: GoldenQA) -> float:
    if not gold.must_mention:
        return 1.0
    text = (answer or "").lower()
    hits = sum(1 for s in gold.must_mention if s.lower() in text)
    return hits / len(gold.must_mention)


def honest_abstain(answer: str, gold: GoldenQA) -> int:
    if gold.answerable:
        return 1
    text = (answer or "").lower()
    return int("does not address" in text or "not address" in text)
