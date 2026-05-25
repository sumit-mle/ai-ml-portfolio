"""Rubric metrics for RAG evaluation.

Six metrics, each 0.0–1.0:

  RETRIEVAL
    clause_match        retrieved chunks overlap the labeled clause span
    citation_correct    retrieved set contains a chunk from the right document
    context_recall      labeled span text appears (substring) in retrieved chunks

  GENERATION
    answer_quotes_clause answer contains a verbatim chunk of the labeled span
    faithfulness         every claim in the answer is supported by retrieved
                         context (LLM-as-judge with Pydantic structured output)
    answer_relevancy     LLM-as-judge: does the answer address the question?

The first four are deterministic substring/range checks (cheap, free).
The last two use the LLM judge.
"""
from __future__ import annotations

import logging
from typing import Iterable

from openai import OpenAI
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import get_settings, require_openai_key
from .golden import GoldenQA
from .sut.interface import RunOutput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic metrics — no LLM cost
# ---------------------------------------------------------------------------


def _overlap_chars(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def clause_match(retrieved_chunk_offsets: list[tuple[str, int, int]],
                 retrieved_text: list[str],
                 golden: GoldenQA) -> float:
    """Did any retrieved chunk overlap the labeled span on the same document?

    `retrieved_chunk_offsets` is a list of (doc_id, char_start, char_end)
    tuples. The harness's runner.py builds this from the SUT output by
    parsing the chunk_id pattern; failing that, falls back to substring
    detection on retrieved_text.
    """
    snippet = (golden.expected_text or "")[:60]
    for doc_id, start, end in retrieved_chunk_offsets:
        if doc_id != golden.contract_doc_id:
            continue
        if _overlap_chars(start, end, golden.expected_start, golden.expected_end) > 0:
            return 1.0
    # Substring fallback (works when the SUT doesn't expose offsets)
    if snippet:
        for text in retrieved_text:
            if snippet in text:
                return 1.0
    return 0.0


def citation_correct(retrieved_doc_ids: list[str], golden: GoldenQA) -> float:
    return 1.0 if any(d == golden.contract_doc_id for d in retrieved_doc_ids) else 0.0


def context_recall(retrieved_text: list[str], golden: GoldenQA) -> float:
    """Substring presence of the labeled span text in the retrieved chunks."""
    snippet = (golden.expected_text or "").strip()
    if not snippet:
        return 1.0
    needle = snippet[:80].lower()
    return 1.0 if any(needle in t.lower() for t in retrieved_text) else 0.0


def answer_quotes_clause(answer: str, golden: GoldenQA) -> float:
    if not answer or not golden.expected_text:
        return 0.0
    snippet = golden.expected_text[:40].lower()
    return 1.0 if snippet in answer.lower() else 0.0


# ---------------------------------------------------------------------------
# LLM-as-judge metrics: faithfulness + answer_relevancy
# ---------------------------------------------------------------------------


_FAITH_SYSTEM = (
    "You are a strict evaluator. Score the answer on two axes 0.0–1.0:\n"
    "  faithfulness: every factual claim in the answer is supported by the "
    "  retrieved context. If ANY claim is not in the context, score below 0.5.\n"
    "  answer_relevancy: the answer addresses the user's question directly. "
    "  Off-topic, evasive, or padded answers drop the score.\n"
    "Return JSON only matching the requested schema."
)


class _JudgeScores(BaseModel):
    faithfulness: float = Field(ge=0.0, le=1.0)
    answer_relevancy: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8))
def llm_judge(question: str, answer: str, retrieved_text: list[str]) -> _JudgeScores:
    require_openai_key()
    s = get_settings()
    client = OpenAI(api_key=s.openai_api_key)
    context = "\n\n---\n\n".join((t or "")[:2000] for t in retrieved_text) or "(no context)"
    user = (
        f"QUESTION: {question}\n\n"
        f"RETRIEVED CONTEXT:\n{context}\n\n"
        f"ANSWER:\n{answer}\n\n"
        "Score faithfulness and answer_relevancy."
    )
    completion = client.beta.chat.completions.parse(
        model=s.judge_model,
        messages=[
            {"role": "system", "content": _FAITH_SYSTEM},
            {"role": "user", "content": user},
        ],
        response_format=_JudgeScores,
        temperature=0.0,
    )
    msg = completion.choices[0].message
    if msg.refusal or msg.parsed is None:
        return _JudgeScores(faithfulness=0.0, answer_relevancy=0.0,
                            rationale=f"refused: {msg.refusal}")
    return msg.parsed
