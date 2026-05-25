"""SEC EDGAR fetcher.

Pulls real 10-K, 10-Q, 8-K, S-1, and DEF 14A filings via SEC's official REST
APIs:
  - submissions API: list of filings per company (by CIK)
  - Archives:        the actual filing documents (HTML/iXBRL)

SEC fair-access rules:
  - SEC_USER_AGENT env var must identify you (name + email)
  - Max 10 requests/second across all SEC endpoints
  - We sleep between calls to be a polite citizen

We extract two prose-rich sections from a 10-K that are most useful for a
knowledge graph: Item 1 Business (subsidiaries, structure) and Item 1A Risk
Factors (counterparty/sanction/supplier risks). DEF 14A proxy statements are
goldmines for board membership.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from ..shared.config import get_settings

logger = logging.getLogger(__name__)


SEC_BASE = "https://www.sec.gov"
SEC_DATA_BASE = "https://data.sec.gov"


@dataclass
class FilingMeta:
    cik: str
    company_name: str
    accession_no: str
    form: str               # "10-K", "DEF 14A", etc.
    filing_date: str        # ISO yyyy-mm-dd
    primary_document: str   # path component for the main filing doc
    primary_doc_url: str    # full https URL


def _headers() -> dict[str, str]:
    return {
        "User-Agent": get_settings().sec_user_agent,
        "Accept": "application/json",
        "Host": "data.sec.gov",
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def _get_json(url: str) -> dict:
    h = _headers()
    if "data.sec.gov" not in url:
        h["Host"] = "www.sec.gov"
    r = requests.get(url, headers=h, timeout=20)
    r.raise_for_status()
    time.sleep(0.12)  # polite throttle (~8 req/s)
    return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def _get_text(url: str) -> str:
    h = {
        "User-Agent": get_settings().sec_user_agent,
        "Host": "www.sec.gov",
    }
    r = requests.get(url, headers=h, timeout=30)
    r.raise_for_status()
    time.sleep(0.12)
    return r.text


def _padded_cik(cik: str) -> str:
    return str(cik).lstrip("0").zfill(10)


def list_filings(
    cik: str,
    *,
    forms: Iterable[str] = ("10-K", "DEF 14A"),
    limit: int = 5,
) -> list[FilingMeta]:
    """List the most-recent N filings of the requested forms for a CIK.

    The submissions API returns the most-recent batch. For deep history we'd
    follow `filings.files[*]` to older shards; not needed for the demo.
    """
    pcik = _padded_cik(cik)
    url = f"{SEC_DATA_BASE}/submissions/CIK{pcik}.json"
    data = _get_json(url)

    company_name = data.get("name", "Unknown")
    recent = data.get("filings", {}).get("recent", {}) or {}
    accs = recent.get("accessionNumber", []) or []
    forms_list = recent.get("form", []) or []
    dates = recent.get("filingDate", []) or []
    primary_docs = recent.get("primaryDocument", []) or []

    forms_set = {f.upper() for f in forms}
    out: list[FilingMeta] = []
    for acc, form, date, doc in zip(accs, forms_list, dates, primary_docs):
        if form.upper() not in forms_set:
            continue
        acc_no_dashes = acc.replace("-", "")
        primary_url = f"{SEC_BASE}/Archives/edgar/data/{int(pcik)}/{acc_no_dashes}/{doc}"
        out.append(
            FilingMeta(
                cik=str(int(pcik)),
                company_name=company_name,
                accession_no=acc,
                form=form,
                filing_date=date,
                primary_document=doc,
                primary_doc_url=primary_url,
            )
        )
        if len(out) >= limit:
            break

    logger.info(
        "EDGAR: cik=%s '%s' -> %d filings of forms %s",
        cik, company_name, len(out), forms,
    )
    return out


# ---------------------------------------------------------------------------
# HTML stripping. SEC filings are HTML/iXBRL — we want plain text. Avoiding
# heavy deps (no bs4); a minimal HTMLParser keeps the project portable.
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "head"}

    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(self._chunks)


_WS = re.compile(r"\s+")


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return _WS.sub(" ", p.text()).strip()


# ---------------------------------------------------------------------------
# Section extraction. 10-Ks have well-known item headers; we look for them
# and slice the text. This is heuristic — production would parse the iXBRL
# table-of-contents. Good enough for a demo over real filings.
# ---------------------------------------------------------------------------

# Order matters: longest/most-specific patterns first
_ITEM_PATTERNS_10K: list[tuple[str, str]] = [
    ("Item 1A", r"item\s*1a[\.\s]+risk\s*factors"),
    ("Item 1",  r"item\s*1[\.\s]+business"),
    ("Item 7",  r"item\s*7[\.\s]+management.s\s*discussion"),
]


def slice_10k_sections(text: str) -> dict[str, str]:
    """Return {section_name: section_text} for known 10-K items.

    We find the START of each item by regex and slice up to the next item
    header (or end of doc).
    """
    lower = text.lower()
    hits: list[tuple[str, int]] = []
    for name, pat in _ITEM_PATTERNS_10K:
        for m in re.finditer(pat, lower):
            hits.append((name, m.start()))
            break  # first match only (table of contents echoes confuse this)

    hits.sort(key=lambda t: t[1])
    out: dict[str, str] = {}
    for i, (name, start) in enumerate(hits):
        end = hits[i + 1][1] if i + 1 < len(hits) else len(text)
        snippet = text[start:end].strip()
        # Cap each section to keep extraction cost bounded
        out[name] = snippet[:30000]
    return out


def slice_proxy_sections(text: str) -> dict[str, str]:
    """DEF 14A proxy statements: extract the directors/officers section.

    Heuristic: cap the first 40k chars where board info lives.
    """
    return {"Proxy": text[:40000]}


def fetch_filing_text(meta: FilingMeta) -> str:
    """Download the primary document and convert to text."""
    html = _get_text(meta.primary_doc_url)
    return html_to_text(html)


def fetch_and_slice(meta: FilingMeta) -> dict[str, str]:
    text = fetch_filing_text(meta)
    if meta.form.upper() == "10-K":
        sections = slice_10k_sections(text)
        if not sections:
            # Fallback: take the first 30k chars under a generic key
            sections = {"Body": text[:30000]}
        return sections
    if meta.form.upper() == "DEF 14A":
        return slice_proxy_sections(text)
    return {"Body": text[:30000]}


def cache_path(meta: FilingMeta) -> Path:
    s = get_settings()
    base = Path(s.data_dir) / "edgar" / meta.cik
    base.mkdir(parents=True, exist_ok=True)
    safe_acc = meta.accession_no.replace("-", "")
    return base / f"{safe_acc}_{meta.form.replace(' ', '')}.txt"
