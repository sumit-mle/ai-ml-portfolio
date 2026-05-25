"""Typed runtime config, loaded from .env once per process."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    tavily_api_key: str
    sec_user_agent: str

    gen_model: str
    judge_model: str

    output_dir: str

    # Production guardrails — see Mark AI's CrewAI production guide.
    # Without these CrewAI tasks can run unbounded.
    agent_max_iter: int = 12
    agent_max_rpm: int = 30
    task_timeout_seconds: int = 180


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        sec_user_agent=os.getenv(
            "SEC_USER_AGENT", "SalesResearch-Demo example@example.com"
        ),
        gen_model=os.getenv("GEN_MODEL", "gpt-4o-mini"),
        judge_model=os.getenv("JUDGE_MODEL", "gpt-4o-mini"),
        output_dir=os.getenv("OUTPUT_DIR", "./output"),
    )


def require_openai_key() -> None:
    if not get_settings().openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )


def require_tavily_key() -> None:
    if not get_settings().tavily_api_key:
        raise RuntimeError(
            "TAVILY_API_KEY is not set. Sign up free at https://tavily.com/ "
            "and add it to .env."
        )
