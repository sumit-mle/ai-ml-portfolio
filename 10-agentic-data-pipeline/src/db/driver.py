"""Read-only DuckDB connection singleton."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

import duckdb

from ..config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_conn() -> duckdb.DuckDBPyConnection:
    s = get_settings()
    if not s.db_path.exists():
        raise RuntimeError(
            f"DuckDB warehouse not found at {s.db_path}. "
            "Run: python -m src.cli init-db"
        )
    return duckdb.connect(str(s.db_path), read_only=True)


@contextmanager
def cursor() -> Iterator[duckdb.DuckDBPyConnection]:
    yield get_conn()
