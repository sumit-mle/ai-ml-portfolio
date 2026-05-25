"""LlamaIndex sentence-window retrieval — TODO.

Pattern: index per-sentence nodes but at retrieval time replace the matched
sentence with a window of N surrounding sentences (via
MetadataReplacementPostProcessor). Excellent for legal Q&A where you must
cite the *full clause* even if a single phrase matched.
"""
from __future__ import annotations


def run(question: str, contracts, *, top_k: int = 4):  # noqa: D401
    raise NotImplementedError(
        "TODO: use SentenceWindowNodeParser(window_size=3) and "
        "MetadataReplacementPostProcessor(target_metadata_key='window') in "
        "the response synthesizer."
    )
