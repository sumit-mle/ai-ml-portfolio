"""LangChain naive RAG over a contract corpus.

Pipeline (LCEL):
    chunks → OpenAIEmbeddings → FAISS → retriever (top_k cosine)
    question + retrieved → ChatPromptTemplate → ChatOpenAI → answer
"""
from __future__ import annotations

from dataclasses import dataclass

from ..shared.chunking import Chunk, chunk_corpus
from ..shared.corpus import Contract
from ..shared.llm import (
    SYSTEM_PROMPT,
    build_user_prompt,
    get_chat_model_name,
    get_embed_model_name,
    require_openai_key,
)


@dataclass
class RagResult:
    answer: str
    retrieved: list[Chunk]
    technique: str = "langchain.naive"


def _to_lc_documents(chunks: list[Chunk]):
    from langchain_core.documents import Document  # noqa: WPS433

    return [
        Document(
            page_content=c.text,
            metadata={
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "title": c.title,
                "section": c.section,
            },
        )
        for c in chunks
    ]


def _build_retriever(chunks: list[Chunk], top_k: int):
    from langchain_community.vectorstores import FAISS  # noqa: WPS433
    from langchain_openai import OpenAIEmbeddings  # noqa: WPS433

    docs = _to_lc_documents(chunks)
    embeddings = OpenAIEmbeddings(model=get_embed_model_name())
    store = FAISS.from_documents(docs, embeddings)
    return store.as_retriever(search_kwargs={"k": top_k})


def _to_chunk(lc_doc) -> Chunk:
    md = lc_doc.metadata
    return Chunk(
        chunk_id=md.get("chunk_id", ""),
        doc_id=md.get("doc_id", ""),
        title=md.get("title", ""),
        section=md.get("section", ""),
        text=lc_doc.page_content,
        start=md.get("start", 0),
        end=md.get("end", 0),
    )


def run(
    question: str,
    contracts: list[Contract],
    *,
    top_k: int = 4,
) -> RagResult:
    require_openai_key()
    chunks = chunk_corpus(contracts)
    retriever = _build_retriever(chunks, top_k=top_k)
    retrieved_docs = retriever.invoke(question)
    retrieved = [_to_chunk(d) for d in retrieved_docs]

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

    return RagResult(answer=answer, retrieved=retrieved)
