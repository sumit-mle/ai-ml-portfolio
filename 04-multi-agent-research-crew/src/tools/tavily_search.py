"""CrewAI tool: Tavily web search.

Tavily is a search API designed for LLM agents — returns clean text and a
`answer` summary alongside results. We use it for company research and
news/signal discovery.

We wrap it as a typed CrewAI BaseTool with strict args validation rather than
free-form strings.
"""
from __future__ import annotations

import logging
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from tavily import TavilyClient

from ..config import get_settings, require_tavily_key

logger = logging.getLogger(__name__)


class _SearchArgs(BaseModel):
    query: str = Field(..., description="The search query")
    max_results: int = Field(
        default=5, ge=1, le=10, description="Number of results to return"
    )
    days: int = Field(
        default=365,
        ge=1,
        le=365,
        description="Restrict to results from the last N days",
    )


class TavilyWebSearchTool(BaseTool):
    name: str = "Tavily Web Search"
    description: str = (
        "Search the public web for company information, products, leadership, "
        "industry context, etc. Use for general account research. Returns a "
        "concise answer plus the top result snippets."
    )
    args_schema: Type[BaseModel] = _SearchArgs

    def _run(
        self,
        query: str,
        max_results: int = 5,
        days: int = 365,
    ) -> str:
        require_tavily_key()
        client = TavilyClient(api_key=get_settings().tavily_api_key)

        try:
            resp = client.search(
                query=query,
                max_results=max_results,
                days=days,
                include_answer=True,
                search_depth="advanced",
            )
        except Exception as e:
            logger.exception("Tavily search failed: %s", e)
            return f"Search failed: {e}"

        out: list[str] = []
        if ans := resp.get("answer"):
            out.append(f"ANSWER: {ans}")

        for i, r in enumerate(resp.get("results", []), 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = (r.get("content") or "")[:400]
            out.append(f"\n[{i}] {title}\n    {url}\n    {content}")

        return "\n".join(out) if out else "No results."


class _NewsArgs(BaseModel):
    company: str = Field(..., description="The company to find news about")
    days: int = Field(default=90, ge=1, le=365)
    max_results: int = Field(default=8, ge=1, le=15)


class TavilyNewsTool(BaseTool):
    name: str = "Tavily Recent News"
    description: str = (
        "Find recent news about a company — earnings, leadership changes, "
        "layoffs, acquisitions, product launches, regulatory issues. "
        "Restrict the time window with `days`. Use for signal discovery."
    )
    args_schema: Type[BaseModel] = _NewsArgs

    def _run(self, company: str, days: int = 90, max_results: int = 8) -> str:
        require_tavily_key()
        client = TavilyClient(api_key=get_settings().tavily_api_key)

        try:
            resp = client.search(
                query=f"{company} news earnings layoffs leadership acquisition product",
                max_results=max_results,
                days=days,
                topic="news",
                include_answer=True,
                search_depth="advanced",
            )
        except Exception as e:
            logger.exception("Tavily news failed: %s", e)
            return f"News search failed: {e}"

        out: list[str] = []
        if ans := resp.get("answer"):
            out.append(f"SUMMARY: {ans}\n")

        for i, r in enumerate(resp.get("results", []), 1):
            title = r.get("title", "")
            url = r.get("url", "")
            published = r.get("published_date") or "unknown"
            content = (r.get("content") or "")[:400]
            out.append(f"[{i}] {title}\n    Published: {published}\n    URL: {url}\n    {content}\n")

        return "\n".join(out) if out else "No recent news."
