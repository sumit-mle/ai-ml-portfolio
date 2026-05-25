"""High-level ingestion pipeline.

ingest_company(cik) -> for the most-recent N filings of selected forms:
    1. Fetch (with on-disk cache to skip re-downloads)
    2. Slice into sections
    3. Extract entities/relations via LLM
    4. Embed and store chunks
    5. MERGE everything into Neo4j

Idempotent end-to-end. Re-running on the same CIK is safe and cheap.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from .extract import ExtractionResult, extract_from_section
from .loader import upsert_chunks, upsert_extraction, upsert_filing
from .sec_edgar import (
    FilingMeta,
    cache_path,
    fetch_filing_text,
    list_filings,
    slice_10k_sections,
    slice_proxy_sections,
)
from ..shared.config import get_settings

logger = logging.getLogger(__name__)


def _load_or_fetch_text(meta: FilingMeta) -> str:
    cp = cache_path(meta)
    if cp.exists():
        logger.info("Cache hit: %s", cp.name)
        return cp.read_text(encoding="utf-8")
    text = fetch_filing_text(meta)
    cp.write_text(text, encoding="utf-8")
    return text


def _extraction_cache_path(meta: FilingMeta) -> Path:
    s = get_settings()
    base = Path(s.data_dir) / "extracted"
    base.mkdir(parents=True, exist_ok=True)
    safe_acc = meta.accession_no.replace("-", "")
    return base / f"{safe_acc}.json"


def _load_or_extract(text_by_section: dict[str, str], meta: FilingMeta) -> ExtractionResult:
    cp = _extraction_cache_path(meta)
    if cp.exists():
        logger.info("Extraction cache hit: %s", cp.name)
        try:
            return ExtractionResult.model_validate_json(cp.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Cache invalid (%s); re-extracting", e)

    # Concatenate sections so a single extraction sees the full filing context.
    # The extractor chunks internally if too long.
    combined = "\n\n".join(f"## {k}\n{v}" for k, v in text_by_section.items())
    result = extract_from_section(combined)
    cp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def ingest_filing(meta: FilingMeta, *, embed_chunks: bool = True) -> dict:
    text = _load_or_fetch_text(meta)
    if meta.form.upper() == "10-K":
        sections = slice_10k_sections(text)
        if not sections:
            sections = {"Body": text[:30000]}
    elif meta.form.upper() == "DEF 14A":
        sections = slice_proxy_sections(text)
    else:
        sections = {"Body": text[:30000]}

    upsert_filing(meta)
    extraction = _load_or_extract(sections, meta)
    upsert_extraction(extraction, meta)

    n_chunks = 0
    if embed_chunks:
        n_chunks = upsert_chunks(sections, meta)

    return {
        "accession_no": meta.accession_no,
        "form": meta.form,
        "n_entities": len(extraction.entities),
        "n_relations": len(extraction.relations),
        "n_chunks": n_chunks,
    }


def ingest_company(
    cik: str,
    *,
    forms: tuple[str, ...] = ("10-K", "DEF 14A"),
    limit: int = 3,
    embed_chunks: bool = True,
) -> list[dict]:
    metas = list_filings(cik, forms=forms, limit=limit)
    out: list[dict] = []
    for m in metas:
        try:
            out.append(ingest_filing(m, embed_chunks=embed_chunks))
        except Exception as e:
            logger.exception("Ingest failed for %s: %s", m.accession_no, e)
    return out
