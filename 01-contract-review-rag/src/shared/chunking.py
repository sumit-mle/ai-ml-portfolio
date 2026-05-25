"""Clause-aware chunker.

Contracts are structured as numbered sections ("1. License Grant.", "2. Term ...").
We split on numbered headers when we can find them and fall back to recursive
character splitting on long sections.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .corpus import Contract


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    section: str        # heading or "preamble"
    text: str
    start: int          # char offset into the contract text
    end: int


# Matches lines like "1. License Grant." or "10. Term and Termination."
_NUMBERED_HEADER = re.compile(r"^\s*(\d+)\.\s+([^\n.]{2,80})\.", re.MULTILINE)


def _split_long_section(
    text: str, *, base_offset: int, max_chars: int, overlap: int
) -> list[tuple[str, int, int]]:
    if len(text) <= max_chars:
        return [(text, base_offset, base_offset + len(text))]
    out: list[tuple[str, int, int]] = []
    step = max_chars - overlap
    if step <= 0:
        step = max_chars
    pos = 0
    while pos < len(text):
        end = min(pos + max_chars, len(text))
        out.append((text[pos:end], base_offset + pos, base_offset + end))
        if end == len(text):
            break
        pos += step
    return out


def chunk_contract(
    contract: Contract,
    *,
    max_chars: int = 1200,
    overlap: int = 150,
) -> list[Chunk]:
    text = contract.text
    headers = [(m.start(), m.group(1), m.group(2).strip()) for m in _NUMBERED_HEADER.finditer(text)]

    sections: list[tuple[str, int, int]] = []  # (heading, start, end)
    if not headers:
        sections.append(("body", 0, len(text)))
    else:
        # Preamble before the first header
        if headers[0][0] > 0:
            sections.append(("preamble", 0, headers[0][0]))
        for i, (start, _num, title) in enumerate(headers):
            end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
            sections.append((title, start, end))

    chunks: list[Chunk] = []
    for s_idx, (heading, s_start, s_end) in enumerate(sections):
        section_text = text[s_start:s_end].strip()
        if not section_text:
            continue
        for c_idx, (piece, p_start, p_end) in enumerate(
            _split_long_section(
                section_text, base_offset=s_start, max_chars=max_chars, overlap=overlap
            )
        ):
            chunks.append(
                Chunk(
                    chunk_id=f"{contract.doc_id}::{s_idx:02d}::{c_idx:02d}",
                    doc_id=contract.doc_id,
                    title=contract.title,
                    section=heading,
                    text=piece,
                    start=p_start,
                    end=p_end,
                )
            )
    return chunks


def chunk_corpus(contracts: list[Contract], **kwargs) -> list[Chunk]:
    out: list[Chunk] = []
    for c in contracts:
        out.extend(chunk_contract(c, **kwargs))
    return out
