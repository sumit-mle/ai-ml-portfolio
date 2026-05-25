"""LlamaIndex naive RAG over a contract corpus.

Pipeline:
    Documents → VectorStoreIndex (in-memory) → as_query_engine(top_k)
The engine handles retrieval + answer synthesis. Same OpenAI models as the
LangChain pipeline so results are directly comparable.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..shared.chunking import Chunk, chunk_corpus
from ..shared.corpus import Contract
from ..shared.llm import (
    get_chat_model_name,
    get_embed_model_name,
    require_openai_key,
)


@dataclass
class RagResult:
    answer: str
    retrieved: list[Chunk]
    technique: str = "llamaindex.naive"


def _to_li_documents(chunks: list[Chunk]):
    from llama_index.core import Document  # noqa: WPS433

    return [
        Document(
            text=c.text,
            metadata={
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "title": c.title,
                "section": c.section,
            },
        )
        for c in chunks
    ]


def _configure_settings():
    from llama_index.core import Settings  # noqa: WPS433
    from llama_index.embeddings.openai import OpenAIEmbedding  # noqa: WPS433
    from llama_index.llms.openai import OpenAI  # noqa: WPS433

    Settings.llm = OpenAI(model=get_chat_model_name(), temperature=0.1)
    Settings.embed_model = OpenAIEmbedding(model=get_embed_model_name())


def _to_chunk(node) -> Chunk:
    md = node.metadata or {}
    return Chunk(
        chunk_id=md.get("chunk_id", ""),
        doc_id=md.get("doc_id", ""),
        title=md.get("title", ""),
        section=md.get("section", ""),
        text=node.get_content() if hasattr(node, "get_content") else getattr(node, "text", ""),
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
    _configure_settings()

    from llama_index.core import VectorStoreIndex  # noqa: WPS433

    chunks = chunk_corpus(contracts)
    docs = _to_li_documents(chunks)
    # We pass already-chunked Documents; LlamaIndex's default node parser will
    # leave them as-is for short texts and split if needed.
    index = VectorStoreIndex.from_documents(docs)
    engine = index.as_query_engine(similarity_top_k=top_k)
    response = engine.query(question)

    retrieved = []
    for sn in getattr(response, "source_nodes", []) or []:
        retrieved.append(_to_chunk(sn.node if hasattr(sn, "node") else sn))

    answer = str(response)
    return RagResult(answer=answer, retrieved=retrieved)
