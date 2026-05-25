"""Typed runtime config, loaded from .env once per process."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal


Risk = Literal["low", "medium", "high"]
_RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    gen_model: str
    auto_approve_below: Risk
    output_dir: str

    def is_auto_approved(self, risk: Risk) -> bool:
        """True if `risk` is strictly less than the auto-approve threshold."""
        return _RISK_ORDER[risk] < _RISK_ORDER[self.auto_approve_below]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    raw = os.getenv("AUTO_APPROVE_BELOW", "medium").lower()
    if raw not in _RISK_ORDER:
        raw = "medium"
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        gen_model=os.getenv("GEN_MODEL", "gpt-4o-mini"),
        auto_approve_below=raw,  # type: ignore[arg-type]
        output_dir=os.getenv("OUTPUT_DIR", "./output"),
    )


def require_openai_key() -> None:
    if not get_settings().openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
