"""LlamaIndex-backed retriever over an Abstract corpus.

Each abstract becomes one Document. We use VectorStoreIndex with OpenAI
embeddings, returning RetrievedDoc objects that the agent and eval consume.
"""
from __future__ import annotations

from dataclasses import dataclass

from .corpus import Abstract
from .llm import (
    get_chat_model_name,
    get_embed_model_name,
    require_openai_key,
)


@dataclass
class RetrievedDoc:
    pmid: str
    title: str
    abstract: str
    journal: str
    year: str
    score: float = 0.0

    @property
    def display(self) -> str:
        venue = f"{self.journal} {self.year}".strip()
        head = f"[PMID {self.pmid}]" + (f" ({venue})" if venue else "")
        return f"{head} {self.title}\n{self.abstract}"


def _configure_settings() -> None:
    from llama_index.core import Settings
    from llama_index.embeddings.openai import OpenAIEmbedding
    from llama_index.llms.openai import OpenAI

    Settings.llm = OpenAI(model=get_chat_model_name(), temperature=0.1)
    Settings.embed_model = OpenAIEmbedding(model=get_embed_model_name())


def _to_documents(abstracts: list[Abstract]):
    from llama_index.core import Document

    return [
        Document(
            text=a.text,
            metadata={
                "pmid": a.pmid,
                "title": a.title,
                "journal": a.journal,
                "year": a.year,
            },
        )
        for a in abstracts
    ]


class AbstractRetriever:
    """Wraps a LlamaIndex VectorStoreIndex retriever for our agent.

    Built once per agent run from a fixed list of abstracts. The agent calls
    `.retrieve(query, top_k)` zero or more times during a reflection loop.
    """

    def __init__(self, abstracts: list[Abstract], *, top_k_default: int = 5):
        require_openai_key()
        _configure_settings()

        from llama_index.core import VectorStoreIndex

        self._abstracts = abstracts
        self._by_pmid = {a.pmid: a for a in abstracts}
        self._index = VectorStoreIndex.from_documents(_to_documents(abstracts))
        self._top_k_default = top_k_default

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedDoc]:
        k = top_k or self._top_k_default
        retriever = self._index.as_retriever(similarity_top_k=k)
        nodes = retriever.retrieve(query)
        out: list[RetrievedDoc] = []
        for n in nodes:
            md = n.node.metadata if hasattr(n, "node") else n.metadata
            pmid = md.get("pmid", "")
            src = self._by_pmid.get(pmid)
            if src is None:
                # Fall back to node content
                out.append(
                    RetrievedDoc(
                        pmid=pmid,
                        title=md.get("title", ""),
                        abstract=n.node.get_content() if hasattr(n, "node") else "",
                        journal=md.get("journal", ""),
                        year=md.get("year", ""),
                        score=getattr(n, "score", 0.0) or 0.0,
                    )
                )
            else:
                out.append(
                    RetrievedDoc(
                        pmid=src.pmid,
                        title=src.title,
                        abstract=src.abstract,
                        journal=src.journal,
                        year=src.year,
                        score=getattr(n, "score", 0.0) or 0.0,
                    )
                )
        return out
