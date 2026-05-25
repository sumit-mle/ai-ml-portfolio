"""CrewAI tool: SEC EDGAR lookup.

Fetches the most-recent 10-K business overview for a public company by name.
Builds on the same EDGAR API used in project 03 — no new dependencies, just
a thin adapter to CrewAI's BaseTool.

For private companies the lookup will return a clear "not a public filer"
message rather than failing silently. Sales research often hits private
targets, and we want the agent to fall back to web search gracefully.
"""
from __future__ import annotations

import logging
import re
import time
from html.parser import HTMLParser
from typing import Type

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings

logger = logging.getLogger(__name__)


SEC_BASE = "https://www.sec.gov"
SEC_DATA_BASE = "https://data.sec.gov"


def _headers(host: str) -> dict[str, str]:
    return {
        "User-Agent": get_settings().sec_user_agent,
        "Accept": "application/json" if host == "data.sec.gov" else "text/html",
        "Host": host,
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8))
def _get_json(url: str) -> dict:
    host = "data.sec.gov" if "data.sec.gov" in url else "www.sec.gov"
    r = requests.get(url, headers=_headers(host), timeout=20)
    r.raise_for_status()
    time.sleep(0.12)  # SEC fair-use throttle
    return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8))
def _get_text(url: str) -> str:
    r = requests.get(url, headers=_headers("www.sec.gov"), timeout=30)
    r.raise_for_status()
    time.sleep(0.12)
    return r.text


# ---------------------------------------------------------------------------
# Ticker lookup. The ticker -> CIK file is published by SEC and updated daily.
# ---------------------------------------------------------------------------


_TICKER_CACHE: dict[str, dict] | None = None


def _load_ticker_index() -> dict:
    global _TICKER_CACHE
    if _TICKER_CACHE is not None:
        return _TICKER_CACHE
    try:
        data = _get_json(f"{SEC_BASE}/files/company_tickers.json")
    except Exception as e:
        logger.warning("Could not load ticker index: %s", e)
        _TICKER_CACHE = {}
        return _TICKER_CACHE
    _TICKER_CACHE = data
    return data


def _find_cik(company_or_ticker: str) -> tuple[str, str] | None:
    """Return (cik_padded, canonical_name) or None."""
    idx = _load_ticker_index()
    if not idx:
        return None

    needle = company_or_ticker.strip().lower()
    # Try exact ticker first
    for _, row in idx.items():
        if row.get("ticker", "").lower() == needle:
            return str(row["cik_str"]).zfill(10), row.get("title", "")

    # Then substring match on title
    candidates = []
    for _, row in idx.items():
        title = row.get("title", "")
        if needle in title.lower():
            candidates.append((str(row["cik_str"]).zfill(10), title))
    if candidates:
        # Prefer shortest title (tends to be the parent vs. a subsidiary)
        candidates.sort(key=lambda x: len(x[1]))
        return candidates[0]
    return None


# ---------------------------------------------------------------------------
# 10-K fetch + section extraction (same approach as project 03)
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "head"}

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP and self._depth > 0:
            self._depth -= 1

    def handle_data(self, data):
        if self._depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


_WS = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return _WS.sub(" ", p.text()).strip()


_BUSINESS_RE = re.compile(r"item\s*1[\.\s]+business", re.IGNORECASE)
_RISK_RE = re.compile(r"item\s*1a[\.\s]+risk\s*factors", re.IGNORECASE)


def _slice_section(text: str, start_re: re.Pattern, end_re: re.Pattern | None) -> str:
    m = start_re.search(text)
    if not m:
        return ""
    start = m.start()
    if end_re:
        m2 = end_re.search(text, pos=start + 50)
        end = m2.start() if m2 else min(start + 30000, len(text))
    else:
        end = min(start + 30000, len(text))
    return text[start:end].strip()


# ---------------------------------------------------------------------------
# CrewAI tool
# ---------------------------------------------------------------------------


class _EdgarArgs(BaseModel):
    company_or_ticker: str = Field(
        ..., description="Company legal name or ticker (e.g. 'Apple Inc.' or 'AAPL')"
    )
    section: str = Field(
        default="business",
        description="Which section to return: 'business' (Item 1) or 'risk' (Item 1A).",
    )
    max_chars: int = Field(default=8000, ge=1000, le=20000)


class SECEdgarTool(BaseTool):
    name: str = "SEC EDGAR 10-K Lookup"
    description: str = (
        "Fetch the most-recent 10-K filing's Item 1 Business or Item 1A Risk "
        "Factors section for a US public company by name or ticker. Returns "
        "the section text plus the SEC accession number for citation. If the "
        "company is not a US public filer, returns a clear 'not found' message."
    )
    args_schema: Type[BaseModel] = _EdgarArgs

    def _run(
        self,
        company_or_ticker: str,
        section: str = "business",
        max_chars: int = 8000,
    ) -> str:
        match = _find_cik(company_or_ticker)
        if not match:
            return (
                f"'{company_or_ticker}' was not found in SEC EDGAR. "
                "It may be a private company, a non-US entity, or a subsidiary. "
                "Use web search to gather information instead."
            )

        cik, canonical_name = match
        try:
            data = _get_json(f"{SEC_DATA_BASE}/submissions/CIK{cik}.json")
        except Exception as e:
            return f"EDGAR submissions fetch failed for {canonical_name}: {e}"

        recent = data.get("filings", {}).get("recent", {}) or {}
        accs = recent.get("accessionNumber", []) or []
        forms = recent.get("form", []) or []
        primary_docs = recent.get("primaryDocument", []) or []
        dates = recent.get("filingDate", []) or []

        target = None
        for acc, form, doc, dt in zip(accs, forms, primary_docs, dates):
            if form.upper() == "10-K":
                target = (acc, doc, dt)
                break
        if target is None:
            return f"{canonical_name}: no 10-K found in recent filings."

        acc, doc, filing_date = target
        acc_no_dashes = acc.replace("-", "")
        doc_url = f"{SEC_BASE}/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{doc}"

        try:
            html = _get_text(doc_url)
        except Exception as e:
            return f"{canonical_name}: 10-K download failed: {e}"

        text = _html_to_text(html)

        if section.lower() == "risk":
            section_text = _slice_section(text, _RISK_RE, None)
            label = "Item 1A Risk Factors"
        else:
            section_text = _slice_section(text, _BUSINESS_RE, _RISK_RE)
            label = "Item 1 Business"

        if not section_text:
            section_text = text[:max_chars]
            label = "Body (sections not found)"

        section_text = section_text[:max_chars]
        return (
            f"COMPANY: {canonical_name}\n"
            f"CIK: {cik}\n"
            f"FILING: 10-K accession {acc} dated {filing_date}\n"
            f"SECTION: {label}\n"
            f"URL: {doc_url}\n\n"
            f"{section_text}"
        )
