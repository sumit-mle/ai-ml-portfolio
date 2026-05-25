"""LangChain MultiQueryRetriever — TODO.

Pattern: ask the LLM to generate N rephrasings of the user question, retrieve
for each, dedupe and union. Useful for analyst-style queries that don't match
the literal phrasing in contracts ("can the buyer get out?" → "termination,
material adverse change, force majeure").
"""
from __future__ import annotations


def run(question: str, contracts, *, top_k: int = 4):  # noqa: D401
    raise NotImplementedError(
        "TODO: use langchain.retrievers.multi_query.MultiQueryRetriever wrapping "
        "the dense retriever, with a prompt that asks for 4 contract-review "
        "specific rephrasings."
    )
