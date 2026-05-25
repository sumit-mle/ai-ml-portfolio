"""Typed runtime configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    gen_model: str

    portal_host: str
    portal_port: int
    portal_base_url: str

    allowed_hosts: tuple[str, ...]

    data_dir: Path
    evidence_dir: Path
    audit_log_path: Path
    screenshot_dir: Path

    headless: bool
    browser_timeout_s: int
    agent_max_steps: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    raw_hosts = os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost")
    hosts = tuple(h.strip() for h in raw_hosts.split(",") if h.strip())
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        gen_model=os.getenv("GEN_MODEL", "gpt-4o-mini"),
        portal_host=os.getenv("PORTAL_HOST", "127.0.0.1"),
        portal_port=int(os.getenv("PORTAL_PORT", "7878")),
        portal_base_url=os.getenv("PORTAL_BASE_URL", "http://127.0.0.1:7878"),
        allowed_hosts=hosts,
        data_dir=Path(os.getenv("DATA_DIR", "./data")),
        evidence_dir=Path(os.getenv("EVIDENCE_DIR", "./output/evidence")),
        audit_log_path=Path(os.getenv("AUDIT_LOG_PATH", "./logs/audit.jsonl")),
        screenshot_dir=Path(os.getenv("SCREENSHOT_DIR", "./output/screenshots")),
        headless=os.getenv("HEADLESS", "true").lower() != "false",
        browser_timeout_s=int(os.getenv("BROWSER_TIMEOUT_S", "30")),
        agent_max_steps=int(os.getenv("AGENT_MAX_STEPS", "25")),
    )


def require_openai_key() -> None:
    if not get_settings().openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Required for the autonomous agent path."
        )
