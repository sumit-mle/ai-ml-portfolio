"""LLM-based entity + relationship extraction with Pydantic structured output.

Approach:
  - Chunk the section text (~2000 tokens per chunk, generous overlap)
  - For each chunk, call gpt-4o-mini with response_format=ExtractionResult
  - Merge and dedupe across chunks for a filing

Structured output gives us validated JSON for free — no flaky regex on LLM
prose. Cheap retries via tenacity for transient OpenAI errors.
"""
from __future__ import annotations

import logging
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from ..shared.config import get_settings, require_openai_key
from ..shared.llm import EXTRACTION_SYSTEM, extract_model_name

logger = logging.getLogger(__name__)


EntityType = Literal["Company", "Person", "Location"]
RelType = Literal[
    "HAS_SUBSIDIARY",
    "OWNS_STAKE_IN",
    "ACQUIRED",
    "EXECUTIVE_OF",
    "BOARD_MEMBER_OF",
    "FORMER_EXECUTIVE_OF",
    "SUPPLIES",
    "PARTNER_WITH",
    "ADVISED",
    "HEADQUARTERED_IN",
    "SANCTIONED_BY",
]


class Entity(BaseModel):
    name: str = Field(..., description="Canonical legal/full name")
    type: EntityType


class Relation(BaseModel):
    source: str = Field(..., description="Source entity name")
    type: RelType
    target: str = Field(..., description="Target entity name")
    role: str | None = Field(
        default=None,
        description="For EXECUTIVE_OF / FORMER_EXECUTIVE_OF (CEO, CFO, COO, Chair, Director, etc.)",
    )
    pct: float | None = Field(
        default=None,
        description="For OWNS_STAKE_IN: ownership percentage if disclosed",
    )
    year: int | None = Field(
        default=None,
        description="For ACQUIRED: year of acquisition if disclosed",
    )
    evidence: str = Field(
        default="",
        description="Short verbatim phrase from the source supporting this fact",
    )


class ExtractionResult(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Chunking — naive word-windowing is sufficient. The LLM tolerates broken
# sentences at boundaries; structured output makes errors recoverable.
# ---------------------------------------------------------------------------

def _chunk_words(text: str, *, max_words: int = 1500, overlap: int = 200) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    out: list[str] = []
    step = max_words - overlap
    pos = 0
    while pos < len(words):
        out.append(" ".join(words[pos : pos + max_words]))
        pos += step
        if pos + overlap >= len(words):
            # Final partial chunk
            if pos < len(words):
                out.append(" ".join(words[pos:]))
            break
    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def _extract_chunk(client: OpenAI, model: str, chunk: str) -> ExtractionResult:
    """One LLM call with structured output. Retries transient errors."""
    completion = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": chunk},
        ],
        response_format=ExtractionResult,
        temperature=0.0,
    )
    msg = completion.choices[0].message
    if msg.refusal:
        logger.warning("LLM refused extraction: %s", msg.refusal)
        return ExtractionResult()
    return msg.parsed or ExtractionResult()


def _dedupe_entities(entities: list[Entity]) -> list[Entity]:
    seen: dict[tuple[str, str], Entity] = {}
    for e in entities:
        key = (e.name.strip(), e.type)
        if key not in seen:
            seen[key] = e
    return list(seen.values())


def _dedupe_relations(relations: list[Relation]) -> list[Relation]:
    seen: dict[tuple[str, str, str, str | None], Relation] = {}
    for r in relations:
        key = (r.source.strip(), r.type, r.target.strip(), r.role)
        if key not in seen:
            seen[key] = r
    return list(seen.values())


def extract_from_section(text: str) -> ExtractionResult:
    """Extract entities/relations from one filing section.

    Returns a single merged, deduped ExtractionResult.
    """
    require_openai_key()
    client = OpenAI(api_key=get_settings().openai_api_key)
    model = extract_model_name()

    chunks = _chunk_words(text)
    logger.info("Extracting from %d chunk(s) (model=%s)", len(chunks), model)

    all_entities: list[Entity] = []
    all_relations: list[Relation] = []
    for i, chunk in enumerate(chunks, 1):
        logger.debug("Chunk %d/%d (%d words)", i, len(chunks), len(chunk.split()))
        try:
            res = _extract_chunk(client, model, chunk)
        except Exception as e:
            logger.error("Chunk %d failed: %s", i, e)
            continue
        all_entities.extend(res.entities)
        all_relations.extend(res.relations)

    return ExtractionResult(
        entities=_dedupe_entities(all_entities),
        relations=_dedupe_relations(all_relations),
    )
