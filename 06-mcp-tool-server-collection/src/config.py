"""Typed runtime configuration loaded once from .env.

Centralizing config is a small thing that pays off the moment you swap stdio
for HTTP, or DuckDB for Snowflake. Every other module reads from `get_settings()`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

Transport = Literal["stdio", "http"]


@dataclass(frozen=True)
class Settings:
    server_name: str
    server_version: str
    transport: Transport
    http_host: str
    http_port: int

    duckdb_path: Path
    data_dir: Path
    parquet_dir: Path

    auth_token_file: Path

    query_timeout_s: int
    max_rows_returned: int
    rate_limit_per_hour: int

    audit_log_path: Path

    openai_api_key: str
    gen_model: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    raw_transport = os.getenv("TRANSPORT", "stdio").lower()
    if raw_transport not in ("stdio", "http"):
        raw_transport = "stdio"

    return Settings(
        server_name=os.getenv("SERVER_NAME", "enterprise-data-platform"),
        server_version=os.getenv("SERVER_VERSION", "0.1.0"),
        transport=raw_transport,  # type: ignore[arg-type]
        http_host=os.getenv("HTTP_HOST", "127.0.0.1"),
        http_port=int(os.getenv("HTTP_PORT", "7878")),
        duckdb_path=Path(os.getenv("DUCKDB_PATH", "./data/warehouse.duckdb")),
        data_dir=Path(os.getenv("DATA_DIR", "./data")),
        parquet_dir=Path(os.getenv("PARQUET_DIR", "./data/parquet")),
        auth_token_file=Path(os.getenv("AUTH_TOKEN_FILE", "./auth/tokens.json")),
        query_timeout_s=int(os.getenv("QUERY_TIMEOUT_S", "30")),
        max_rows_returned=int(os.getenv("MAX_ROWS_RETURNED", "10000")),
        rate_limit_per_hour=int(os.getenv("RATE_LIMIT_PER_HOUR", "100")),
        audit_log_path=Path(os.getenv("AUDIT_LOG_PATH", "./logs/audit.jsonl")),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        gen_model=os.getenv("GEN_MODEL", "gpt-4o-mini"),
    )


def require_openai_key() -> None:
    if not get_settings().openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Required for the natural-language SQL tool."
        )
