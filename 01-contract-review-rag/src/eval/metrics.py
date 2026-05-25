"""Eval metrics for contract-review RAG.

- clause_match: did any retrieved chunk overlap the labeled clause span in the
  same document?
- citation_correct: does the retrieved set include a chunk from the right
  document?
- answer_quotes_clause: does the generated answer contain a substring of the
  labeled clause? Cheap proxy for faithfulness.
"""
from __future__ import annotations

from .golden import GoldenQA
from ..shared.chunking import Chunk


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def clause_match(retrieved: list[Chunk], golden: GoldenQA) -> int:
    for ch in retrieved:
        if ch.doc_id != golden.doc_id:
            continue
        if _overlap(ch.start, ch.end, golden.expected_start, golden.expected_end) > 0:
            return 1
    # Fall back to substring check (handles framework chunkers that don't
    # preserve start/end offsets).
    for ch in retrieved:
        if ch.doc_id != golden.doc_id:
            continue
        if golden.expected_text and golden.expected_text[:60] in ch.text:
            return 1
    return 0


def citation_correct(retrieved: list[Chunk], golden: GoldenQA) -> int:
    return int(any(c.doc_id == golden.doc_id for c in retrieved))


def answer_quotes_clause(answer: str, golden: GoldenQA) -> int:
    if not answer or not golden.expected_text:
        return 0
    snippet = golden.expected_text[:40].lower()
    return int(snippet in answer.lower())
