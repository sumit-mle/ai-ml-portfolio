"""LangChain cross-encoder reranking.

Strategy: pull a wider candidate set with hybrid retrieval (BM25 + dense, RRF
fused), then rerank with a cross-encoder
(`cross-encoder/ms-marco-MiniLM-L-6-v2` by default) and keep the top_k. This
is the precision lever that matters most for legal Q&A.
"""
from __future__ import annotations

from .hybrid import _rrf_fuse
from .naive import RagResult, _to_chunk, _to_lc_documents
from ..shared.chunking import chunk_corpus
from ..shared.corpus import Contract
from ..shared.llm import (
    SYSTEM_PROMPT,
    build_user_prompt,
    get_chat_model_name,
    get_embed_model_name,
    get_reranker_model_name,
    require_openai_key,
)


def _candidates(question: str, lc_docs, candidate_k: int):
    from langchain_community.retrievers.bm25 import BM25Retriever  # noqa: WPS433
    from langchain_community.vectorstores import FAISS  # noqa: WPS433
    from langchain_openai import OpenAIEmbeddings  # noqa: WPS433

    bm25 = BM25Retriever.from_documents(lc_docs)
    bm25.k = candidate_k
    embeddings = OpenAIEmbeddings(model=get_embed_model_name())
    dense = FAISS.from_documents(lc_docs, embeddings).as_retriever(
        search_kwargs={"k": candidate_k}
    )
    return _rrf_fuse([bm25.invoke(question), dense.invoke(question)], top_k=candidate_k)


def _rerank(query: str, lc_docs, top_k: int):
    from sentence_transformers import CrossEncoder  # noqa: WPS433

    model = CrossEncoder(get_reranker_model_name())
    pairs = [(query, d.page_content) for d in lc_docs]
    scores = model.predict(pairs)
    ranked = sorted(zip(lc_docs, scores), key=lambda p: float(p[1]), reverse=True)
    return [d for d, _s in ranked[:top_k]]


def run(
    question: str,
    contracts: list[Contract],
    *,
    top_k: int = 4,
    candidate_k: int = 12,
) -> RagResult:
    require_openai_key()
    chunks = chunk_corpus(contracts)
    lc_docs = _to_lc_documents(chunks)
    candidates = _candidates(question, lc_docs, candidate_k=candidate_k)
    reranked = _rerank(question, candidates, top_k=top_k)
    retrieved = [_to_chunk(d) for d in reranked]

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

    return RagResult(answer=answer, retrieved=retrieved, technique="langchain.rerank")
