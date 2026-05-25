"""Golden Q/A set built from project 01's CUAD-labeled sample corpus.

Each question targets ONE labeled clause in ONE contract. The expected_text
is the verbatim labeled span. The harness scores:
  - retrieval (clause-match, citation-correct)
  - generation (verbatim-quote, faithfulness)

Because the source labels are real CUAD spans (not LLM-generated), the
golden is stable: re-running it next week or next month gives the same
expectations.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings


@dataclass(frozen=True)
class GoldenQA:
    qid: str
    question: str
    contract_doc_id: str
    category: str
    expected_text: str
    expected_start: int
    expected_end: int


_TEMPLATES: dict[str, str] = {
    "Change Of Control": "Is there a change of control clause in this contract, and what does it say?",
    "Anti-Assignment": "Are there restrictions on assignment in this contract?",
    "Exclusivity": "Does this contract grant exclusivity to either party?",
    "Most Favored Nation": "Does this contract include a most favored nation clause?",
    "Non-Compete": "Is there a non-compete provision in this contract?",
    "License Grant": "What license is being granted in this contract?",
    "Cap On Liability": "Is there a cap on liability or indemnification in this contract?",
    "Governing Law": "Which jurisdiction's law governs this contract?",
}


def load_golden() -> list[GoldenQA]:
    """Build the golden set by importing project 01's corpus + label data."""
    s = get_settings()
    if str(s.project_01_path) not in sys.path:
        sys.path.insert(0, str(s.project_01_path))
    from importlib import import_module
    # Temporarily evict the harness's `src.*` so project 01's `src` loads
    # from its own directory rather than ours.
    harness_src = {
        k: v for k, v in sys.modules.items()
        if k == "src" or k.startswith("src.")
    }
    for k in harness_src:
        del sys.modules[k]
    try:
        corpus_mod = import_module("src.shared.corpus")
        contracts = corpus_mod.load_sample()
    finally:
        # Restore the harness's modules.
        for k in [k for k in sys.modules if k == "src" or k.startswith("src.")]:
            if k not in harness_src:
                del sys.modules[k]
        for k, v in harness_src.items():
            sys.modules[k] = v

    out: list[GoldenQA] = []
    qid = 0
    for c in contracts:
        for category, labels in c.labels.items():
            template = _TEMPLATES.get(category, f'What does this contract say about "{category}"?')
            for lab in labels:
                qid += 1
                out.append(
                    GoldenQA(
                        qid=f"q{qid:03d}",
                        question=template,
                        contract_doc_id=c.doc_id,
                        category=category,
                        expected_text=lab.answer,
                        expected_start=lab.start,
                        expected_end=lab.end,
                    )
                )
    return out
