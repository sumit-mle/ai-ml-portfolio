"""Typed runtime configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    realtime_model: str
    gen_model: str
    judge_model: str

    host: str
    port: int

    db_path: Path
    data_dir: Path

    audit_log_path: Path

    require_identity_verification: bool


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        realtime_model=os.getenv("REALTIME_MODEL", "gpt-realtime"),
        gen_model=os.getenv("GEN_MODEL", "gpt-4o-mini"),
        judge_model=os.getenv("JUDGE_MODEL", "gpt-4o-mini"),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "7777")),
        db_path=Path(os.getenv("DB_PATH", "./data/helpdesk.sqlite")),
        data_dir=Path(os.getenv("DATA_DIR", "./data")),
        audit_log_path=Path(os.getenv("AUDIT_LOG_PATH", "./logs/audit.jsonl")),
        require_identity_verification=os.getenv(
            "REQUIRE_IDENTITY_VERIFICATION", "true"
        ).lower() == "true",
    )


def require_openai_key() -> None:
    if not get_settings().openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
