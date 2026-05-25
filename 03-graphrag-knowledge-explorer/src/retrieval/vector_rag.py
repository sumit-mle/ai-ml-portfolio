"""Pure vector RAG baseline against the same Neo4j chunk index.

Same data, same embeddings as Graph RAG — just no graph traversal. This is the
honest baseline that shows what Graph RAG adds on multi-hop queries.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import OpenAI

from ..db.driver import session
from ..db.schema import VECTOR_INDEX_NAME
from ..shared.config import get_settings, require_openai_key
from ..shared.llm import VECTOR_RAG_SYSTEM, chat_model_name, embed_model_name

logger = logging.getLogger(__name__)


@dataclass
class VectorRagResult:
    answer: str
    retrieved_filings: list[str] = field(default_factory=list)
    technique: str = "vector_rag_neo4j"


_VECTOR_SEARCH = f"""
CALL db.index.vector.queryNodes($index, $k, $embedding)
YIELD node, score
MATCH (node)-[:PART_OF]->(f:Filing)<-[:FILED]-(c:Company)
RETURN node.text AS text, node.section AS section,
       f.accession_no AS accession_no, f.form AS form, f.filing_date AS filing_date,
       c.name AS company, score
ORDER BY score DESC
LIMIT $k
"""


def run(question: str, *, top_k: int = 5) -> VectorRagResult:
    require_openai_key()

    client = OpenAI(api_key=get_settings().openai_api_key)
    embedding = client.embeddings.create(
        model=embed_model_name(), input=[question]
    ).data[0].embedding

    with session() as s:
        recs = list(
            s.run(
                _VECTOR_SEARCH,
                index=VECTOR_INDEX_NAME,
                k=top_k,
                embedding=embedding,
            )
        )

    blocks: list[str] = []
    seen: list[str] = []
    for rec in recs:
        if rec["accession_no"] and rec["accession_no"] not in seen:
            seen.append(rec["accession_no"])
        blocks.append(
            f"[{rec['accession_no']}] ({rec['company']} {rec['form']} {rec['filing_date']} - {rec['section']})\n"
            f"{(rec['text'] or '')[:1200]}"
        )

    context_str = "\n\n---\n\n".join(blocks) if blocks else "(no excerpts found)"
    llm = ChatOpenAI(model=chat_model_name(), temperature=0.1)
    user = (
        f"Question: {question}\n\n"
        f"Filing excerpts:\n{context_str}\n\n"
        "Answer (cite accession numbers in square brackets):"
    )
    msg = llm.invoke([
        SystemMessage(content=VECTOR_RAG_SYSTEM),
        HumanMessage(content=user),
    ])
    return VectorRagResult(
        answer=msg.content or "",
        retrieved_filings=seen,
    )
