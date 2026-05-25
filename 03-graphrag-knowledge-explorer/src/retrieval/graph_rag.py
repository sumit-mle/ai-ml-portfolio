"""Graph RAG retrieval over Neo4j.

Strategy (production pattern):
  1. Vector search the Chunk index to find the K most relevant filing chunks
  2. From those chunks, hop to their parent Filings and Companies
  3. Expand the entity neighborhood: companies, persons, subsidiaries,
     board members, suppliers, sanctions — up to k_hops away
  4. Format triples + chunk excerpts as structured context
  5. LLM generates with citations

This is the hybrid 'vector + graph' pattern: vectors find the entry point,
the graph supplies the structure that makes multi-hop questions answerable.
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
from ..shared.llm import GRAPH_RAG_SYSTEM, chat_model_name, embed_model_name

logger = logging.getLogger(__name__)


@dataclass
class GraphRagResult:
    answer: str
    seed_filings: list[str] = field(default_factory=list)   # accession_nos
    triples: list[str] = field(default_factory=list)
    chunk_excerpts: list[str] = field(default_factory=list)
    technique: str = "graph_rag_neo4j"


def _embed_query(query: str) -> list[float]:
    require_openai_key()
    client = OpenAI(api_key=get_settings().openai_api_key)
    resp = client.embeddings.create(model=embed_model_name(), input=[query])
    return resp.data[0].embedding


# ---------------------------------------------------------------------------
# Cypher: vector search → seed companies → expand neighborhood
# ---------------------------------------------------------------------------

_VECTOR_SEARCH = f"""
CALL db.index.vector.queryNodes($index, $k, $embedding)
YIELD node, score
MATCH (node)-[:PART_OF]->(f:Filing)<-[:FILED]-(c:Company)
RETURN node.id AS chunk_id, node.text AS text, node.section AS section,
       f.accession_no AS accession_no, f.form AS form, f.filing_date AS filing_date,
       c.name AS company, score
ORDER BY score DESC
LIMIT $k
"""


_NEIGHBORHOOD = """
// Seed companies: from chunks (their author) AND any companies whose name
// appears as a substring of the question (cheap entity linking).
WITH $seed_companies AS seeds, $question AS q
UNWIND seeds AS seed
MATCH (s:Company {name: seed})
// Outgoing edges (one or two hops, broad type set)
OPTIONAL MATCH p1 = (s)-[r1]->(n1)
WHERE type(r1) IN [
    'HAS_SUBSIDIARY','OWNS_STAKE_IN','ACQUIRED','SUPPLIES','PARTNER_WITH',
    'ADVISED','HEADQUARTERED_IN','SANCTIONED_BY'
]
OPTIONAL MATCH p2 = (s)<-[r2]-(p:Person)
WHERE type(r2) IN ['EXECUTIVE_OF','BOARD_MEMBER_OF','FORMER_EXECUTIVE_OF']
// One more hop on the person side: which OTHER companies do they sit at?
OPTIONAL MATCH p3 = (p)-[r3]->(c2:Company)
WHERE type(r3) IN ['EXECUTIVE_OF','BOARD_MEMBER_OF','FORMER_EXECUTIVE_OF']
  AND c2 <> s
// Two hops on the company side, only the most informative types
OPTIONAL MATCH p4 = (s)-[r1b]->(n1)-[r4]->(n2)
WHERE type(r1b) IN ['HAS_SUBSIDIARY','OWNS_STAKE_IN','ACQUIRED']
  AND type(r4)  IN ['SUPPLIES','SANCTIONED_BY','HAS_SUBSIDIARY','PARTNER_WITH']
WITH collect(DISTINCT p1) + collect(DISTINCT p2) + collect(DISTINCT p3) + collect(DISTINCT p4) AS paths
UNWIND paths AS p
WITH DISTINCT p WHERE p IS NOT NULL
UNWIND relationships(p) AS r
WITH DISTINCT r, startNode(r) AS sn, endNode(r) AS en
RETURN sn.name AS source,
       type(r) AS rel,
       en.name AS target,
       coalesce(r.role, '') AS role,
       coalesce(r.pct, 0) AS pct,
       coalesce(r.year, 0) AS year,
       coalesce(r.evidence, '') AS evidence,
       coalesce(r.filings, []) AS filings
LIMIT 200
"""


_NAME_INDEX_SEARCH = """
// Cheap entity-link: any Company or Person whose name appears as a
// case-insensitive substring of the question.
MATCH (c:Company)
WHERE toLower($question) CONTAINS toLower(c.name)
RETURN c.name AS name, 'Company' AS type
UNION
MATCH (p:Person)
WHERE toLower($question) CONTAINS toLower(p.name)
RETURN p.name AS name, 'Person' AS type
"""


def _format_triple(rec: dict) -> str:
    base = f"{rec['source']} -[{rec['rel']}"
    extras: list[str] = []
    if rec.get("role"):
        extras.append(f"role={rec['role']}")
    if rec.get("pct"):
        extras.append(f"pct={rec['pct']}")
    if rec.get("year"):
        extras.append(f"year={rec['year']}")
    if extras:
        base += " (" + ", ".join(extras) + ")"
    base += f"]-> {rec['target']}"
    if rec.get("filings"):
        # Show up to 2 supporting accession numbers
        fl = rec["filings"][:2]
        base += "  " + ", ".join(f"[{x}]" for x in fl)
    return base


def retrieve(
    question: str,
    *,
    k_chunks: int = 5,
    extra_companies: list[str] | None = None,
) -> dict:
    """Run vector search + neighborhood expansion. Returns a dict bundle the
    generator turns into a prompt."""
    embedding = _embed_query(question)

    # Step 1: vector search over chunks
    with session() as s:
        chunk_recs = list(
            s.run(
                _VECTOR_SEARCH,
                index=VECTOR_INDEX_NAME,
                k=k_chunks,
                embedding=embedding,
            )
        )

        # Step 2: derive seed companies — both from chunk authors and from
        # name-mention links in the question.
        seed_companies: set[str] = set()
        chunk_excerpts: list[str] = []
        seed_filings: list[str] = []
        for rec in chunk_recs:
            if rec["company"]:
                seed_companies.add(rec["company"])
            if rec["accession_no"] and rec["accession_no"] not in seed_filings:
                seed_filings.append(rec["accession_no"])
            txt = rec["text"] or ""
            chunk_excerpts.append(
                f"[{rec['accession_no']}] ({rec['company']} - {rec['section']})\n"
                f"{txt[:1200]}"
            )

        for c in extra_companies or []:
            seed_companies.add(c)

        # Cheap entity-link to catch company/person names mentioned in the
        # question that the vector search didn't surface
        for rec in s.run(_NAME_INDEX_SEARCH, question=question):
            if rec["type"] == "Company":
                seed_companies.add(rec["name"])

        # Step 3: expand the graph neighborhood around seed companies
        triple_recs: list[dict] = []
        if seed_companies:
            triple_recs = list(
                s.run(
                    _NEIGHBORHOOD,
                    seed_companies=list(seed_companies),
                    question=question,
                )
            )

    triples = [_format_triple(dict(r)) for r in triple_recs]

    return {
        "seed_companies": sorted(seed_companies),
        "seed_filings": seed_filings,
        "chunk_excerpts": chunk_excerpts,
        "triples": triples,
    }


def run(
    question: str,
    *,
    k_chunks: int = 5,
    extra_companies: list[str] | None = None,
) -> GraphRagResult:
    require_openai_key()
    bundle = retrieve(question, k_chunks=k_chunks, extra_companies=extra_companies)

    parts: list[str] = []
    if bundle["triples"]:
        parts.append(
            "GRAPH RELATIONSHIPS (cite filings in [...] when using these):\n"
            + "\n".join(f"  - {t}" for t in bundle["triples"])
        )
    if bundle["chunk_excerpts"]:
        parts.append("FILING EXCERPTS:\n\n" + "\n\n---\n\n".join(bundle["chunk_excerpts"]))
    context_str = "\n\n".join(parts) if parts else "(no relevant context found)"

    llm = ChatOpenAI(model=chat_model_name(), temperature=0.1)
    user = (
        f"Question: {question}\n\n"
        f"Context:\n{context_str}\n\n"
        "Answer (cite accession numbers in square brackets):"
    )
    msg = llm.invoke([
        SystemMessage(content=GRAPH_RAG_SYSTEM),
        HumanMessage(content=user),
    ])

    return GraphRagResult(
        answer=msg.content or "",
        seed_filings=bundle["seed_filings"],
        triples=bundle["triples"],
        chunk_excerpts=bundle["chunk_excerpts"],
    )
