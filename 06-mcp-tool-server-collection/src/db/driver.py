"""DuckDB driver used by the MCP tools.

Single connection per process. Opened READ_ONLY by default — the SQL gate is
the application-layer enforcer; this is the engine-level enforcer. Belt and
braces.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import duckdb

from ..config import get_settings

logger = logging.getLogger(__name__)


_CONN_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def get_conn() -> duckdb.DuckDBPyConnection:
    s = get_settings()
    s.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    if not s.duckdb_path.exists():
        # Create empty DB file so read_only=True won't fail
        with duckdb.connect(str(s.duckdb_path)) as init:
            init.execute("CREATE TABLE IF NOT EXISTS _bootstrap (x INT)")
    conn = duckdb.connect(str(s.duckdb_path), read_only=True)
    logger.info("DuckDB opened read-only at %s", s.duckdb_path)
    return conn


@contextmanager
def cursor() -> Iterator[duckdb.DuckDBPyConnection]:
    """Threadsafe cursor wrapper. DuckDB connections aren't async-safe so we
    serialize. Production with high concurrency would use a connection pool.
    """
    with _CONN_LOCK:
        yield get_conn()
