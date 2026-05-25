"""Typed runtime config."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    judge_model: str

    project_01_path: Path

    results_dir: Path
    baselines_dir: Path
    reports_dir: Path

    tol_faithfulness: float
    tol_citation: float
    tol_clause_match: float


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        judge_model=os.getenv("JUDGE_MODEL", "gpt-4o-mini"),
        project_01_path=Path(os.getenv("PROJECT_01_PATH", "../01-contract-review-rag")).resolve(),
        results_dir=Path(os.getenv("RESULTS_DIR", "./results")),
        baselines_dir=Path(os.getenv("BASELINES_DIR", "./baselines")),
        reports_dir=Path(os.getenv("REPORTS_DIR", "./reports")),
        tol_faithfulness=float(os.getenv("REGRESSION_TOLERANCE_FAITHFULNESS", "0.05")),
        tol_citation=float(os.getenv("REGRESSION_TOLERANCE_CITATION", "0.05")),
        tol_clause_match=float(os.getenv("REGRESSION_TOLERANCE_CLAUSE_MATCH", "0.05")),
    )


def require_openai_key() -> None:
    if not get_settings().openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Required for the LLM-as-judge faithfulness metric."
        )
