"""Build a golden Q/A set from CUAD-style labels.

For each labeled clause in the corpus, generate a checklist-style question
("Does this contract have a [category] clause?") and use the labeled span as
the expected clause text. The eval harness then checks whether the retrieved
chunk overlaps the expected span and whether the answer quotes it.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..shared.corpus import Contract


@dataclass(frozen=True)
class GoldenQA:
    question: str
    doc_id: str
    category: str
    expected_text: str       # the labeled clause span
    expected_start: int
    expected_end: int


_QUESTION_TEMPLATES: dict[str, str] = {
    "Change Of Control": "Is there a change of control clause in this contract, and what does it say?",
    "Anti-Assignment": "Are there restrictions on assignment in this contract?",
    "Exclusivity": "Does this contract grant exclusivity to either party?",
    "Most Favored Nation": "Does this contract include a most favored nation clause?",
    "Non-Compete": "Is there a non-compete provision in this contract?",
    "License Grant": "What license is being granted in this contract?",
    "Cap On Liability": "Is there a cap on liability or indemnification in this contract?",
    "Governing Law": "Which jurisdiction's law governs this contract?",
}


def build_goldens(contracts: list[Contract]) -> list[GoldenQA]:
    out: list[GoldenQA] = []
    for c in contracts:
        for category, labels in c.labels.items():
            template = _QUESTION_TEMPLATES.get(
                category, f'What does this contract say about "{category}"?'
            )
            for lab in labels:
                out.append(
                    GoldenQA(
                        question=template,
                        doc_id=c.doc_id,
                        category=category,
                        expected_text=lab.answer,
                        expected_start=lab.start,
                        expected_end=lab.end,
                    )
                )
    return out
