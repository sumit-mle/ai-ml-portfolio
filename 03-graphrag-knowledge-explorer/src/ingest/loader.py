"""Idempotent Neo4j loader.

Writes Filing, Company, Person, Location, Chunk nodes and the typed relations.
Every write uses MERGE so re-ingesting the same filing is a no-op (or a
controlled update).

Two main entry points:
  - upsert_filing()       creates the Filing node + Company author link
  - upsert_extraction()   adds entities and typed relations
  - upsert_chunks()       writes the Chunk nodes with embeddings for vector RAG
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Iterable

from openai import OpenAI

from ..shared.config import get_settings, require_openai_key
from ..shared.llm import embed_model_name
from ..db.driver import session
from .extract import Entity, ExtractionResult, Relation
from .sec_edgar import FilingMeta

logger = logging.getLogger(__name__)


# Cypher template per relation type. We keep these explicit (no APOC merge.relationship)
# because the relation properties differ per type (role / pct / year) and explicit
# Cypher is easier to read and debug than dynamic relationship creation.
_REL_CYPHER: dict[str, str] = {
    "HAS_SUBSIDIARY": """
        MERGE (s:Company {name: $source})
        MERGE (t:Company {name: $target})
        MERGE (s)-[r:HAS_SUBSIDIARY]->(t)
          ON CREATE SET r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "OWNS_STAKE_IN": """
        MERGE (s:Company {name: $source})
        MERGE (t:Company {name: $target})
        MERGE (s)-[r:OWNS_STAKE_IN]->(t)
          ON CREATE SET r.pct = $pct, r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.pct = coalesce($pct, r.pct),
                       r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "ACQUIRED": """
        MERGE (s:Company {name: $source})
        MERGE (t:Company {name: $target})
        MERGE (s)-[r:ACQUIRED]->(t)
          ON CREATE SET r.year = $year, r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.year = coalesce($year, r.year),
                       r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "EXECUTIVE_OF": """
        MERGE (p:Person {id: $person_id}) ON CREATE SET p.name = $source
        MERGE (c:Company {name: $target})
        MERGE (p)-[r:EXECUTIVE_OF]->(c)
          ON CREATE SET r.role = $role, r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.role = coalesce($role, r.role),
                       r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "BOARD_MEMBER_OF": """
        MERGE (p:Person {id: $person_id}) ON CREATE SET p.name = $source
        MERGE (c:Company {name: $target})
        MERGE (p)-[r:BOARD_MEMBER_OF]->(c)
          ON CREATE SET r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "FORMER_EXECUTIVE_OF": """
        MERGE (p:Person {id: $person_id}) ON CREATE SET p.name = $source
        MERGE (c:Company {name: $target})
        MERGE (p)-[r:FORMER_EXECUTIVE_OF]->(c)
          ON CREATE SET r.role = $role, r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.role = coalesce($role, r.role),
                       r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "SUPPLIES": """
        MERGE (s:Company {name: $source})
        MERGE (t:Company {name: $target})
        MERGE (s)-[r:SUPPLIES]->(t)
          ON CREATE SET r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "PARTNER_WITH": """
        MERGE (s:Company {name: $source})
        MERGE (t:Company {name: $target})
        MERGE (s)-[r:PARTNER_WITH]->(t)
          ON CREATE SET r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "ADVISED": """
        MERGE (s:Company {name: $source})
        MERGE (t:Company {name: $target})
        MERGE (s)-[r:ADVISED]->(t)
          ON CREATE SET r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "HEADQUARTERED_IN": """
        MERGE (s:Company {name: $source})
        MERGE (t:Location {name: $target})
        MERGE (s)-[r:HEADQUARTERED_IN]->(t)
          ON CREATE SET r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
    "SANCTIONED_BY": """
        MERGE (s:Company {name: $source})
        MERGE (t:Location {name: $target})
        MERGE (s)-[r:SANCTIONED_BY]->(t)
          ON CREATE SET r.evidence = $evidence, r.filings = [$accession_no]
          ON MATCH  SET r.filings = [x IN coalesce(r.filings, []) WHERE x <> $accession_no] + $accession_no
    """,
}


def _person_id(name: str) -> str:
    """Stable id for Person nodes. People don't have CIKs, so we hash the
    name. Same name across filings collapses to the same node — acceptable
    for a demo, swap for a disambiguation step in production.
    """
    return hashlib.sha1(name.strip().lower().encode()).hexdigest()[:16]


# Job titles, descriptors, and other tokens we never want as entity names.
# Keep this list focused — over-filtering throws away valid signal.
_BAD_ENTITY_TOKENS: set[str] = {
    "ceo", "cfo", "coo", "cto", "cio", "cso", "cmo", "cdo",
    "chair", "chairman", "chairwoman", "chairperson",
    "president", "director", "officer", "secretary", "treasurer",
    "vice president", "senior vice president", "executive vice president",
    "general counsel", "chief executive officer", "chief financial officer",
    "chief operating officer", "chief technology officer", "chief information officer",
    "the company", "the board", "the corporation",
    "executive", "board member", "trustee",
}


def _looks_like_role(name: str) -> bool:
    """Reject obvious role/title strings that shouldn't be entity names."""
    if not name:
        return True
    n = name.strip().lower()
    # Strip a leading possessive prefix like "Apple's " or "Apple Inc.'s "
    n = re.sub(r"^[a-z][a-z\s\.,]*?(?:’|')s\s+", "", n).strip()
    # Exact match against role tokens
    if n in _BAD_ENTITY_TOKENS:
        return True
    # Starts with a role token (e.g., "Senior Vice President of Software Engineering")
    role_prefixes = ("senior vice president", "vice president", "executive vice president",
                     "chief ", "general counsel")
    if any(n.startswith(p) for p in role_prefixes):
        return True
    return False


def _looks_like_person_name(name: str) -> bool:
    """Permissive: at least two whitespace-separated alpha-rich tokens.

    Returns False for company names with corporate suffixes (Inc., Corp., LLC,
    etc.) so the relation-direction check doesn't confuse them with persons.
    """
    s = name.strip()
    if not s:
        return False
    if _looks_like_role(s):
        return False
    # Corporate suffixes — if present, treat as company, not person.
    lower = s.lower()
    corp_markers = (
        "inc.", "inc", "corp.", "corp", "corporation", "company", "co.",
        "llc", "ltd", "ltd.", "lp", "l.p.", "plc", "n.v.", "ag",
        "holdings", "group", "partners", "capital", "advisors",
        "foundation", "trust", "fund", "bank", "association",
        "university", "college", "institute", "ventures",
    )
    tokens = lower.replace(",", " ").split()
    for marker in corp_markers:
        if marker in tokens:
            return False
    parts = [p for p in s.split() if any(c.isalpha() for c in p)]
    if len(parts) < 2:
        return False
    return True


def _sanitize_extraction(extraction: ExtractionResult) -> ExtractionResult:
    """Drop entities and relations that don't match our typing rules.

    Keeps the graph clean of LLM mistakes like Person="CEO",
    EXECUTIVE_OF source="Senior Vice President of Software Engineering",
    or relations with source/target swapped (Company -> Person for EXECUTIVE_OF).
    Also normalizes casing for known company aliases.
    """
    keep_entities: list[Entity] = []
    for e in extraction.entities:
        canonical = _canonicalize_name(e.name)
        if _looks_like_role(canonical):
            continue
        if e.type == "Person" and not _looks_like_person_name(canonical):
            continue
        keep_entities.append(Entity(name=canonical, type=e.type))

    person_relation_types = {"EXECUTIVE_OF", "BOARD_MEMBER_OF", "FORMER_EXECUTIVE_OF"}
    keep_relations: list[Relation] = []
    for r in extraction.relations:
        src = _canonicalize_name(r.source)
        tgt = _canonicalize_name(r.target)
        if _looks_like_role(src) or _looks_like_role(tgt):
            continue
        if r.type in person_relation_types:
            src_is_person = _looks_like_person_name(src)
            tgt_is_person = _looks_like_person_name(tgt)
            if not src_is_person and tgt_is_person:
                # Direction is reversed: Company -> Person. Flip it.
                src, tgt = tgt, src
                src_is_person, tgt_is_person = True, False
            if not src_is_person:
                # Source still isn't a real person (e.g., role string). Drop.
                continue
            if tgt_is_person:
                # Both look like people — ambiguous, drop.
                continue
        keep_relations.append(
            Relation(
                source=src,
                type=r.type,
                target=tgt,
                role=r.role,
                pct=r.pct,
                year=r.year,
                evidence=r.evidence,
            )
        )

    return ExtractionResult(entities=keep_entities, relations=keep_relations)


# Canonical aliases for companies whose 10-K vs proxy use different casing.
# Production would use a learned alias-resolver; this hard-coded list is fine
# for a 6-company demo and easy to extend.
_NAME_ALIASES: dict[str, str] = {
    "tesla, inc.": "Tesla, Inc.",
    "tesla inc.": "Tesla, Inc.",
    "tesla inc": "Tesla, Inc.",
    "berkshire hathaway inc.": "Berkshire Hathaway Inc.",
    "berkshire hathaway inc": "Berkshire Hathaway Inc.",
    "microsoft corporation": "Microsoft Corporation",
    "microsoft corp.": "Microsoft Corporation",
    "microsoft corp": "Microsoft Corporation",
    "microsoft": "Microsoft Corporation",
    "apple inc.": "Apple Inc.",
    "apple inc": "Apple Inc.",
    "alphabet inc.": "Alphabet Inc.",
    "alphabet inc": "Alphabet Inc.",
    "jpmorgan chase & co.": "JPMorgan Chase & Co.",
    "jpmorgan chase & co": "JPMorgan Chase & Co.",
}


def _canonicalize_name(name: str) -> str:
    if not name:
        return name
    key = name.strip().lower()
    return _NAME_ALIASES.get(key, name.strip())


def upsert_filing(meta: FilingMeta) -> None:
    canonical_name = _canonicalize_name(meta.company_name)
    cypher = """
    MERGE (c:Company {name: $company_name})
      ON CREATE SET c.cik = $cik
      ON MATCH  SET c.cik = coalesce(c.cik, $cik)
    MERGE (f:Filing {accession_no: $accession_no})
      ON CREATE SET f.form = $form,
                    f.filing_date = $filing_date,
                    f.url = $url
      ON MATCH  SET f.form = $form,
                    f.filing_date = $filing_date,
                    f.url = $url
    MERGE (c)-[:FILED]->(f)
    """
    with session() as s:
        s.run(
            cypher,
            company_name=canonical_name,
            cik=meta.cik,
            accession_no=meta.accession_no,
            form=meta.form,
            filing_date=meta.filing_date,
            url=meta.primary_doc_url,
        )
    logger.info(
        "Filing upserted: %s %s %s",
        canonical_name, meta.form, meta.accession_no,
    )


def upsert_extraction(extraction: ExtractionResult, meta: FilingMeta) -> None:
    """Write entities + relations attached to a filing."""
    sanitized = _sanitize_extraction(extraction)
    n_dropped_entities = len(extraction.entities) - len(sanitized.entities)
    n_dropped_relations = len(extraction.relations) - len(sanitized.relations)
    if n_dropped_entities or n_dropped_relations:
        logger.info(
            "Sanitization dropped %d entities and %d relations (likely role/title strings)",
            n_dropped_entities, n_dropped_relations,
        )

    extraction = sanitized

    n_rels = 0
    skipped: list[tuple[str, str]] = []

    with session() as s:
        # Pre-create entities so MERGEs in the relation step always find them
        for ent in extraction.entities:
            if ent.type == "Company":
                s.run("MERGE (c:Company {name: $name})", name=ent.name)
            elif ent.type == "Person":
                s.run(
                    "MERGE (p:Person {id: $id}) ON CREATE SET p.name = $name "
                    "ON MATCH SET p.name = coalesce(p.name, $name)",
                    id=_person_id(ent.name), name=ent.name,
                )
            elif ent.type == "Location":
                s.run("MERGE (l:Location {name: $name})", name=ent.name)

        # Relations
        for rel in extraction.relations:
            tmpl = _REL_CYPHER.get(rel.type)
            if tmpl is None:
                skipped.append((rel.type, f"{rel.source} -> {rel.target}"))
                continue
            params: dict = {
                "source": rel.source,
                "target": rel.target,
                "evidence": rel.evidence or "",
                "accession_no": meta.accession_no,
                "role": rel.role,
                "pct": rel.pct,
                "year": rel.year,
                "person_id": _person_id(rel.source) if rel.type in (
                    "EXECUTIVE_OF", "BOARD_MEMBER_OF", "FORMER_EXECUTIVE_OF"
                ) else None,
            }
            try:
                s.run(tmpl, **params)
                n_rels += 1
            except Exception as e:
                logger.warning("Failed to write %s %s -> %s: %s",
                               rel.type, rel.source, rel.target, e)

    logger.info(
        "Extraction loaded for %s: %d entities, %d/%d relations written%s",
        meta.accession_no,
        len(extraction.entities),
        n_rels,
        len(extraction.relations),
        f" ({len(skipped)} skipped)" if skipped else "",
    )


# ---------------------------------------------------------------------------
# Chunk + embedding loader (used by the vector RAG side)
# ---------------------------------------------------------------------------


def _chunk_text_for_embedding(text: str, *, max_chars: int = 1200, overlap: int = 150) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    step = max(1, max_chars - overlap)
    pos = 0
    while pos < len(text):
        end = min(pos + max_chars, len(text))
        out.append(text[pos:end])
        if end == len(text):
            break
        pos += step
    return out


def _embed(client: OpenAI, model: str, texts: list[str]) -> list[list[float]]:
    # OpenAI embeddings API accepts batches; we keep batches small to avoid
    # token-limit headaches.
    all_vecs: list[list[float]] = []
    BATCH = 64
    for i in range(0, len(texts), BATCH):
        resp = client.embeddings.create(model=model, input=texts[i : i + BATCH])
        all_vecs.extend([e.embedding for e in resp.data])
    return all_vecs


def upsert_chunks(
    sections: dict[str, str],
    meta: FilingMeta,
) -> int:
    """Embed each section into chunks and write them as :Chunk nodes.

    Each chunk links back to the Filing via :PART_OF so the retriever can
    hop chunk -> filing -> author company.
    """
    require_openai_key()
    client = OpenAI(api_key=get_settings().openai_api_key)
    model = embed_model_name()

    all_chunks: list[tuple[str, str, str]] = []  # (section, chunk_id, text)
    for section_name, section_text in sections.items():
        for i, chunk in enumerate(_chunk_text_for_embedding(section_text)):
            chunk_id = f"{meta.accession_no}::{section_name}::{i:03d}"
            all_chunks.append((section_name, chunk_id, chunk))

    if not all_chunks:
        return 0

    vectors = _embed(client, model, [c[2] for c in all_chunks])

    with session() as s:
        for (section, chunk_id, text), vec in zip(all_chunks, vectors):
            s.run(
                """
                MATCH (f:Filing {accession_no: $accession_no})
                MERGE (k:Chunk {id: $chunk_id})
                  ON CREATE SET k.text = $text, k.section = $section
                  ON MATCH  SET k.text = $text, k.section = $section
                SET k.embedding = $embedding
                MERGE (k)-[:PART_OF]->(f)
                """,
                accession_no=meta.accession_no,
                chunk_id=chunk_id,
                text=text,
                section=section,
                embedding=vec,
            )

    logger.info("Embedded %d chunks for filing %s", len(all_chunks), meta.accession_no)
    return len(all_chunks)
