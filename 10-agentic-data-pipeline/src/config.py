"""Typed runtime config."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    gen_model: str
    db_path: Path
    data_dir: Path

    max_repair_attempts: int
    query_timeout_s: int
    max_rows_returned: int

    trace_dir: Path
    results_dir: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        gen_model=os.getenv("GEN_MODEL", "gpt-4o-mini"),
        db_path=Path(os.getenv("DB_PATH", "./data/marketing.duckdb")),
        data_dir=Path(os.getenv("DATA_DIR", "./data")),
        max_repair_attempts=int(os.getenv("MAX_REPAIR_ATTEMPTS", "3")),
        query_timeout_s=int(os.getenv("QUERY_TIMEOUT_S", "15")),
        max_rows_returned=int(os.getenv("MAX_ROWS_RETURNED", "2000")),
        trace_dir=Path(os.getenv("TRACE_DIR", "./output/traces")),
        results_dir=Path(os.getenv("RESULTS_DIR", "./results")),
    )


def require_openai_key() -> None:
    if not get_settings().openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Copy .env.example to .env.")
