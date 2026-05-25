"""Neo4j schema setup: constraints, indexes, vector index.

Idempotent — safe to run on every startup. Constraints are the canonical way
to declare uniqueness in Neo4j 5.x and they create the backing index for free.
"""
from __future__ import annotations

import logging

from ..shared.config import get_settings
from .driver import session

logger = logging.getLogger(__name__)


# Uniqueness constraints. CIK is SEC's company id; accession_no is the unique
# id of an SEC filing; person + chunk get application-level UUIDs.
CONSTRAINTS: list[str] = [
    "CREATE CONSTRAINT company_cik IF NOT EXISTS FOR (c:Company) REQUIRE c.cik IS UNIQUE",
    "CREATE CONSTRAINT company_name IF NOT EXISTS FOR (c:Company) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT filing_accession IF NOT EXISTS FOR (f:Filing) REQUIRE f.accession_no IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (k:Chunk) REQUIRE k.id IS UNIQUE",
    "CREATE CONSTRAINT location_name IF NOT EXISTS FOR (l:Location) REQUIRE l.name IS UNIQUE",
]


# Free-text indexes for entity-linking by name (case-insensitive lookups).
TEXT_INDEXES: list[str] = [
    "CREATE INDEX company_name_text IF NOT EXISTS FOR (c:Company) ON (c.name)",
    "CREATE INDEX person_name_text IF NOT EXISTS FOR (p:Person) ON (p.name)",
]


VECTOR_INDEX_NAME = "chunk_embedding"


def ensure_vector_index() -> None:
    """Create the native Neo4j vector index over Chunk.embedding."""
    s = get_settings()
    cypher = f"""
    CREATE VECTOR INDEX {VECTOR_INDEX_NAME} IF NOT EXISTS
    FOR (c:Chunk) ON (c.embedding)
    OPTIONS {{
        indexConfig: {{
            `vector.dimensions`: {s.embed_dim},
            `vector.similarity_function`: 'cosine'
        }}
    }}
    """
    with session() as sess:
        sess.run(cypher)
    logger.info(
        "Vector index '%s' ensured (dim=%s, cosine)", VECTOR_INDEX_NAME, s.embed_dim
    )


def ensure_schema() -> None:
    """Apply all constraints and indexes. Idempotent."""
    with session() as sess:
        for stmt in CONSTRAINTS:
            sess.run(stmt)
        for stmt in TEXT_INDEXES:
            sess.run(stmt)
    ensure_vector_index()
    logger.info("Schema ensured: %d constraints, %d text indexes, 1 vector index",
                len(CONSTRAINTS), len(TEXT_INDEXES))


def drop_all() -> None:
    """Wipe everything. Use with care — typically only for `cli reset`."""
    with session() as sess:
        sess.run("MATCH (n) DETACH DELETE n")
    logger.warning("All nodes and relationships deleted.")


def stats() -> dict:
    """Return basic counts for `cli stats`."""
    queries = {
        "companies": "MATCH (c:Company) RETURN count(c) AS n",
        "persons": "MATCH (p:Person) RETURN count(p) AS n",
        "filings": "MATCH (f:Filing) RETURN count(f) AS n",
        "chunks": "MATCH (k:Chunk) RETURN count(k) AS n",
        "relationships_total": "MATCH ()-[r]->() RETURN count(r) AS n",
    }
    out: dict[str, int] = {}
    with session() as sess:
        for k, q in queries.items():
            out[k] = sess.run(q).single()["n"]
    return out
