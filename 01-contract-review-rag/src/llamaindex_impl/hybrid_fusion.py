"""LlamaIndex hybrid fusion via QueryFusionRetriever — TODO.

Pattern: combine BM25Retriever and a vector retriever inside a
QueryFusionRetriever with mode='reciprocal_rerank'. Adds an LLM-driven query
expansion step compared to LangChain's EnsembleRetriever.
"""
from __future__ import annotations


def run(question: str, contracts, *, top_k: int = 4):  # noqa: D401
    raise NotImplementedError(
        "TODO: BM25Retriever + VectorIndexRetriever inside "
        "QueryFusionRetriever(mode='reciprocal_rerank', num_queries=4)."
    )
