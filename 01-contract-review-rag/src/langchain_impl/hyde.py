"""LangChain HyDE (hypothetical document embeddings) — TODO.

Pattern: ask the LLM to draft a *hypothetical* clause that would answer the
question, embed *that* draft, then retrieve. Strong for concept queries where
the literal text in contracts is dense legalese.
"""
from __future__ import annotations


def run(question: str, contracts, *, top_k: int = 4):  # noqa: D401
    raise NotImplementedError(
        "TODO: use HypotheticalDocumentEmbedder from langchain.chains, "
        "configure with a 'web_search' template tuned for contract clauses."
    )
