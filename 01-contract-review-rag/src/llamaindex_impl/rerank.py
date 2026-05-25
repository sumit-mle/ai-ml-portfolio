"""LlamaIndex cross-encoder reranking — TODO.

Pattern: query_engine.as_query_engine(node_postprocessors=[SentenceTransformerRerank(...)])
or use FlagEmbeddingReranker for BGE rerankers.
"""
from __future__ import annotations


def run(question: str, contracts, *, top_k: int = 4):  # noqa: D401
    raise NotImplementedError(
        "TODO: SentenceTransformerRerank(top_n=top_k, "
        "model='cross-encoder/ms-marco-MiniLM-L-6-v2') as a node_postprocessor "
        "on the query engine."
    )
