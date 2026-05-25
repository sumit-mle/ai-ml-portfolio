"""Golden Q/A set for medical-affairs literature review.

Each item maps a question to:
  - relevant_pmids: PMIDs that *should* be retrieved (from the synthetic sample)
  - must_mention: substrings the answer must contain to be considered correct

Designed to exercise the reflection loop:
  - some questions are answerable from one abstract (single-hop)
  - some require two abstracts (multi-hop, forces a follow-up retrieval)
  - one is unanswerable from the corpus (tests "I don't know" honesty)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GoldenQA:
    qid: str
    question: str
    relevant_pmids: tuple[str, ...]
    must_mention: tuple[str, ...] = field(default_factory=tuple)
    answerable: bool = True


GOLDEN: list[GoldenQA] = [
    GoldenQA(
        qid="q1",
        question="What does the evidence show about cardiovascular outcomes with SGLT2 inhibitors in type 2 diabetes?",
        relevant_pmids=("990000001",),
        must_mention=("MACE", "0.86"),
    ),
    GoldenQA(
        qid="q2",
        question="Does empagliflozin increase the risk of acute kidney injury in real-world use?",
        relevant_pmids=("990000002",),
        must_mention=("AKI", "0.76"),
    ),
    GoldenQA(
        qid="q3",
        question="Are GLP-1 receptor agonists associated with pancreatitis?",
        relevant_pmids=("990000003",),
        must_mention=("1.05",),
    ),
    GoldenQA(
        qid="q4",
        question="What weight loss can be expected with semaglutide 2.4 mg in adults with obesity?",
        relevant_pmids=("990000004",),
        must_mention=("14.9", "semaglutide"),
    ),
    GoldenQA(
        qid="q5",
        question="Compare the safety and efficacy of GLP-1 agonists for both pancreatitis risk and weight management.",
        relevant_pmids=("990000003", "990000004"),
        must_mention=("semaglutide",),
    ),
    GoldenQA(
        qid="q6",
        question="How should apixaban be dosed in elderly patients with atrial fibrillation, and how does it compare to warfarin?",
        relevant_pmids=("990000005", "990000006"),
        must_mention=("apixaban",),
    ),
    GoldenQA(
        qid="q7",
        question="What is known about statin-induced myopathy?",
        relevant_pmids=("990000007",),
        must_mention=("rhabdomyolysis",),
    ),
    GoldenQA(
        qid="q8",
        question="When should PCSK9 inhibitors be considered after statin therapy?",
        relevant_pmids=("990000008",),
        must_mention=("evolocumab",),
    ),
    GoldenQA(
        qid="q9",
        question="What is the efficacy of metformin monotherapy versus placebo for HbA1c reduction in newly diagnosed T2DM?",
        relevant_pmids=(),
        must_mention=("does not address",),
        answerable=False,
    ),
]


def load_golden() -> list[GoldenQA]:
    return list(GOLDEN)
