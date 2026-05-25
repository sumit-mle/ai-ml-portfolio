"""LangChain hybrid retrieval: BM25 + dense, fused with reciprocal rank fusion.

Why this matters in legal: precise terms ("MFN", "change of control",
"indemnification cap") need keyword matching. Dense alone tends to miss them.

LangChain v1 dropped its built-in EnsembleRetriever, so we implement RRF
directly here. The algorithm is small, well-known, and easier to reason about
than relying on a moving framework API.
"""
from __future__ import annotations

from .naive import RagResult, _to_chunk, _to_lc_documents
from ..shared.chunking import chunk_corpus
from ..shared.corpus import Contract
from ..shared.llm import (
    SYSTEM_PROMPT,
    build_user_prompt,
    get_chat_model_name,
    get_embed_model_name,
    require_openai_key,
)


def _bm25_retriever(lc_docs, k: int):
    from langchain_community.retrievers.bm25 import BM25Retriever  # noqa: WPS433

    r = BM25Retriever.from_documents(lc_docs)
    r.k = k
    return r


def _dense_retriever(lc_docs, k: int):
    from langchain_community.vectorstores import FAISS  # noqa: WPS433
    from langchain_openai import OpenAIEmbeddings  # noqa: WPS433

    embeddings = OpenAIEmbeddings(model=get_embed_model_name())
    return FAISS.from_documents(lc_docs, embeddings).as_retriever(search_kwargs={"k": k})


def _rrf_fuse(rankings: list[list], k: int = 60, top_k: int = 4):
    """Reciprocal Rank Fusion. `rankings` is a list of ranked Document lists."""
    scores: dict[str, float] = {}
    by_id: dict[str, object] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking):
            key = doc.metadata.get("chunk_id") or id(doc)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            by_id[key] = doc
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [by_id[key] for key, _ in ordered[:top_k]]


def run(
    question: str,
    contracts: list[Contract],
    *,
    top_k: int = 4,
    candidate_k: int = 8,
) -> RagResult:
    require_openai_key()
    chunks = chunk_corpus(contracts)
    lc_docs = _to_lc_documents(chunks)

    bm25 = _bm25_retriever(lc_docs, k=candidate_k)
    dense = _dense_retriever(lc_docs, k=candidate_k)
    bm25_hits = bm25.invoke(question)
    dense_hits = dense.invoke(question)
    fused = _rrf_fuse([bm25_hits, dense_hits], top_k=top_k)
    retrieved = [_to_chunk(d) for d in fused]

    from langchain_core.prompts import ChatPromptTemplate  # noqa: WPS433
    from langchain_openai import ChatOpenAI  # noqa: WPS433

    prompt = ChatPromptTemplate.from_messages(
        [("system", SYSTEM_PROMPT), ("user", "{user}")]
    )
    llm = ChatOpenAI(model=get_chat_model_name(), temperature=0.1)
    chain = prompt | llm

    context_blocks = [
        f"[{c.chunk_id}] (section: {c.section})\n{c.text}" for c in retrieved
    ]
    user = build_user_prompt(question, context_blocks)
    msg = chain.invoke({"user": user})
    answer = msg.content if hasattr(msg, "content") else str(msg)

    return RagResult(answer=answer, retrieved=retrieved, technique="langchain.hybrid")
