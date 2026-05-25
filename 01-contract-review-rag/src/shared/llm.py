"""LLM and embedding helpers, framework-agnostic.

Both LangChain and LlamaIndex pipelines route through these functions so the
choice of model lives in one place. Default is OpenAI (gpt-4o-mini for chat,
text-embedding-3-small for embeddings) — set OPENAI_API_KEY in .env.
"""
from __future__ import annotations

import os


def get_chat_model_name() -> str:
    return os.getenv("GEN_MODEL", "gpt-4o-mini")


def get_embed_model_name() -> str:
    return os.getenv("EMBED_MODEL", "text-embedding-3-small")


def get_reranker_model_name() -> str:
    return os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")


def require_openai_key() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )


SYSTEM_PROMPT = (
    "You are a careful contract-review assistant for M&A diligence. "
    "Answer only using the provided clauses. If the clauses do not contain the "
    "answer, say 'Not found in this contract.' "
    "Always quote the exact clause text you relied on, and cite it as "
    "[doc_id::section] using the metadata in the context."
)


def build_user_prompt(question: str, context_blocks: list[str]) -> str:
    context = "\n\n---\n\n".join(context_blocks)
    return (
        f"Question: {question}\n\n"
        f"Context (clauses retrieved from the contract):\n{context}\n\n"
        "Answer (quote the clause and cite as [doc_id::section]):"
    )
