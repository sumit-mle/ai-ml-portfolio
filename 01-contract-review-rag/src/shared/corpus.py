"""Corpus loader.

Default mode: a tiny built-in 3-contract sample (~3 KB total) so the CLI runs
in seconds with zero downloads. Pass --full to fetch the real CUAD v1 dataset
from Hugging Face on demand.

Each "contract" is a Document with `doc_id`, `title`, and `text`. CUAD-style
clause labels are attached as `labels: dict[str, list[ClauseLabel]]` keyed by
the 41 clause categories. The eval harness uses these labels as ground truth.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClauseLabel:
    category: str          # e.g. "Change Of Control"
    answer: str            # the literal text the lawyer highlighted
    start: int             # char offset into the contract text
    end: int


@dataclass
class Contract:
    doc_id: str
    title: str
    text: str
    labels: dict[str, list[ClauseLabel]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Built-in sample. Three short, fictional contracts that look like CUAD format
# and have a few labeled clauses each. Good enough to exercise the full
# pipeline and the eval harness without any download.
# ---------------------------------------------------------------------------

_SAMPLE_CONTRACTS: list[Contract] = [
    Contract(
        doc_id="sample-001",
        title="Software License Agreement (Acme Corp / Beta Industries)",
        text=(
            "SOFTWARE LICENSE AGREEMENT\n\n"
            "This Software License Agreement (this \"Agreement\") is entered into as of "
            "January 15, 2024 by and between Acme Corp., a Delaware corporation "
            "(\"Licensor\"), and Beta Industries Inc., a New York corporation (\"Licensee\").\n\n"
            "1. License Grant. Subject to the terms of this Agreement, Licensor hereby grants "
            "to Licensee a non-exclusive, non-transferable, worldwide license to use the "
            "Software during the Term solely for Licensee's internal business purposes.\n\n"
            "2. Term and Termination. The initial term of this Agreement shall be three (3) "
            "years from the Effective Date and shall automatically renew for successive "
            "one (1) year periods unless either party provides written notice of "
            "non-renewal at least ninety (90) days prior to the end of the then-current term.\n\n"
            "3. Change of Control. In the event of a Change of Control of Licensee, Licensor "
            "shall have the right, exercisable within thirty (30) days of receiving written "
            "notice of such Change of Control, to terminate this Agreement upon thirty "
            "(30) days' written notice. \"Change of Control\" means any merger, consolidation, "
            "or sale of all or substantially all of the assets of Licensee.\n\n"
            "4. Assignment. Licensee may not assign or transfer this Agreement, in whole or "
            "in part, without the prior written consent of Licensor, which consent may be "
            "withheld in Licensor's sole discretion.\n\n"
            "5. Indemnification. Each party's aggregate liability under this Agreement shall "
            "not exceed the fees paid by Licensee to Licensor in the twelve (12) months "
            "preceding the event giving rise to the claim.\n\n"
            "6. Governing Law. This Agreement shall be governed by the laws of the State of "
            "Delaware, without regard to its conflict of laws principles.\n"
        ),
        labels={
            "License Grant": [
                ClauseLabel(
                    category="License Grant",
                    answer=(
                        "Licensor hereby grants to Licensee a non-exclusive, non-transferable, "
                        "worldwide license to use the Software"
                    ),
                    start=0, end=0,  # offsets recomputed below
                )
            ],
            "Change Of Control": [
                ClauseLabel(
                    category="Change Of Control",
                    answer=(
                        "In the event of a Change of Control of Licensee, Licensor shall have "
                        "the right"
                    ),
                    start=0, end=0,
                )
            ],
            "Anti-Assignment": [
                ClauseLabel(
                    category="Anti-Assignment",
                    answer=(
                        "Licensee may not assign or transfer this Agreement, in whole or in "
                        "part, without the prior written consent of Licensor"
                    ),
                    start=0, end=0,
                )
            ],
            "Cap On Liability": [
                ClauseLabel(
                    category="Cap On Liability",
                    answer=(
                        "Each party's aggregate liability under this Agreement shall not "
                        "exceed the fees paid by Licensee to Licensor in the twelve (12) months"
                    ),
                    start=0, end=0,
                )
            ],
            "Governing Law": [
                ClauseLabel(
                    category="Governing Law",
                    answer="This Agreement shall be governed by the laws of the State of Delaware",
                    start=0, end=0,
                )
            ],
        },
    ),
    Contract(
        doc_id="sample-002",
        title="Distribution Agreement (Gamma Foods / Delta Distributors)",
        text=(
            "DISTRIBUTION AGREEMENT\n\n"
            "This Distribution Agreement is dated March 1, 2023 between Gamma Foods LLC "
            "(\"Supplier\") and Delta Distributors Inc. (\"Distributor\").\n\n"
            "1. Appointment. Supplier appoints Distributor as its exclusive distributor of "
            "the Products in the Territory, and Distributor accepts such appointment.\n\n"
            "2. Exclusivity. During the Term, Distributor shall not, directly or indirectly, "
            "distribute, sell, or promote any product that competes with the Products in the "
            "Territory.\n\n"
            "3. Most Favored Nation. Supplier represents that the prices and terms offered "
            "to Distributor under this Agreement are no less favorable than those offered "
            "to any other distributor of similar size during the Term.\n\n"
            "4. Term. The initial term shall be five (5) years and shall renew for additional "
            "one (1) year periods unless terminated by either party with 180 days' notice.\n\n"
            "5. Non-Compete. For two (2) years following termination, Distributor shall not "
            "distribute any product that competes with the Products in the Territory.\n\n"
            "6. Governing Law. This Agreement shall be governed by the laws of the State of "
            "California.\n"
        ),
        labels={
            "Exclusivity": [
                ClauseLabel(
                    category="Exclusivity",
                    answer=(
                        "Supplier appoints Distributor as its exclusive distributor of the "
                        "Products in the Territory"
                    ),
                    start=0, end=0,
                )
            ],
            "Most Favored Nation": [
                ClauseLabel(
                    category="Most Favored Nation",
                    answer=(
                        "the prices and terms offered to Distributor under this Agreement are "
                        "no less favorable than those offered to any other distributor"
                    ),
                    start=0, end=0,
                )
            ],
            "Non-Compete": [
                ClauseLabel(
                    category="Non-Compete",
                    answer=(
                        "For two (2) years following termination, Distributor shall not "
                        "distribute any product that competes with the Products"
                    ),
                    start=0, end=0,
                )
            ],
            "Governing Law": [
                ClauseLabel(
                    category="Governing Law",
                    answer="This Agreement shall be governed by the laws of the State of California",
                    start=0, end=0,
                )
            ],
        },
    ),
    Contract(
        doc_id="sample-003",
        title="Master Services Agreement (Epsilon Tech / Zeta Holdings)",
        text=(
            "MASTER SERVICES AGREEMENT\n\n"
            "This Master Services Agreement is effective as of June 1, 2024 between Epsilon "
            "Tech Inc. (\"Provider\") and Zeta Holdings LLC (\"Client\").\n\n"
            "1. Services. Provider shall perform the services described in each Statement of "
            "Work executed under this Agreement.\n\n"
            "2. Assignment. Either party may assign this Agreement to a successor in connection "
            "with a merger, acquisition, or sale of substantially all of its assets, upon "
            "written notice to the other party.\n\n"
            "3. Indemnification. Provider shall indemnify and hold Client harmless from any "
            "third-party claim arising out of Provider's gross negligence or willful misconduct, "
            "subject to a cap equal to two (2) times the fees paid in the prior twelve months.\n\n"
            "4. Confidentiality. Each party shall protect the other party's Confidential "
            "Information using the same degree of care it uses to protect its own confidential "
            "information of like importance, but in no event less than reasonable care, for a "
            "period of five (5) years following termination.\n\n"
            "5. Governing Law. New York law governs this Agreement.\n"
        ),
        labels={
            "Anti-Assignment": [
                ClauseLabel(
                    category="Anti-Assignment",
                    answer=(
                        "Either party may assign this Agreement to a successor in connection "
                        "with a merger, acquisition, or sale of substantially all of its assets"
                    ),
                    start=0, end=0,
                )
            ],
            "Cap On Liability": [
                ClauseLabel(
                    category="Cap On Liability",
                    answer=(
                        "subject to a cap equal to two (2) times the fees paid in the prior "
                        "twelve months"
                    ),
                    start=0, end=0,
                )
            ],
            "Governing Law": [
                ClauseLabel(
                    category="Governing Law",
                    answer="New York law governs this Agreement",
                    start=0, end=0,
                )
            ],
        },
    ),
]


def _fix_label_offsets(contracts: list[Contract]) -> list[Contract]:
    """Recompute char offsets for the sample so eval can locate labels reliably."""
    out: list[Contract] = []
    for c in contracts:
        new_labels: dict[str, list[ClauseLabel]] = {}
        for cat, labels in c.labels.items():
            updated = []
            for lab in labels:
                idx = c.text.find(lab.answer)
                if idx >= 0:
                    updated.append(
                        ClauseLabel(
                            category=lab.category,
                            answer=lab.answer,
                            start=idx,
                            end=idx + len(lab.answer),
                        )
                    )
            if updated:
                new_labels[cat] = updated
        out.append(Contract(doc_id=c.doc_id, title=c.title, text=c.text, labels=new_labels))
    return out


def load_sample() -> list[Contract]:
    """Fast, offline-only built-in sample. Always available."""
    return _fix_label_offsets(_SAMPLE_CONTRACTS)


def load_full(cache_dir: str | None = None, max_contracts: int | None = None) -> list[Contract]:
    """Load CUAD v1 from Hugging Face. Requires `datasets` installed and network access.

    CUAD is structured as a SQuAD-style QA dataset, one row per (clause-category, contract).
    We invert it into one Contract per document with labels grouped by category.
    """
    from datasets import load_dataset  # noqa: WPS433 — lazy

    cache_dir = cache_dir or os.getenv("DATA_DIR")
    ds = load_dataset("theatticusproject/cuad", split="train", cache_dir=cache_dir)

    by_doc: dict[str, Contract] = {}
    for row in ds:
        title = row.get("title") or row.get("Document Name") or row.get("id")
        context = row.get("context") or ""
        question = row.get("question") or ""
        # CUAD questions look like "Highlight the parts ... related to \"Change Of Control\" ..."
        category = _extract_category(question)
        doc_id = title if title else f"cuad-{len(by_doc)}"

        if doc_id not in by_doc:
            by_doc[doc_id] = Contract(doc_id=doc_id, title=title, text=context)

        answers = row.get("answers", {}) or {}
        texts = answers.get("text", []) or []
        starts = answers.get("answer_start", []) or []
        for ans_text, start in zip(texts, starts):
            if not ans_text:
                continue
            label = ClauseLabel(
                category=category,
                answer=ans_text,
                start=int(start),
                end=int(start) + len(ans_text),
            )
            by_doc[doc_id].labels.setdefault(category, []).append(label)

    contracts = list(by_doc.values())
    if max_contracts is not None:
        contracts = contracts[:max_contracts]
    return contracts


def _extract_category(question: str) -> str:
    # Categories appear inside double quotes in CUAD questions.
    if '"' in question:
        parts = question.split('"')
        if len(parts) >= 2:
            return parts[1].strip()
    return question[:80]


def load_corpus(full: bool = False, max_contracts: int | None = None) -> list[Contract]:
    if full:
        return load_full(max_contracts=max_contracts)
    return load_sample()
