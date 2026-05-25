"""LlamaIndex auto-merging (parent-document) retrieval — TODO.

Pattern: HierarchicalNodeParser builds a tree of small leaf nodes under bigger
parent nodes. Retrieval scores leaves; AutoMergingRetriever returns the parent
when enough children of the same parent are retrieved. Good for clauses that
span multiple paragraphs.
"""
from __future__ import annotations


def run(question: str, contracts, *, top_k: int = 4):  # noqa: D401
    raise NotImplementedError(
        "TODO: HierarchicalNodeParser([2048, 512, 128]) → AutoMergingRetriever "
        "with simple_ratio_thresh=0.5."
    )
