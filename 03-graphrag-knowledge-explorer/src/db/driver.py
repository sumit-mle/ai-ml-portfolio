"""Neo4j driver singleton with retry-aware sessions.

Production code uses a single driver per process. Sessions are short-lived,
created per logical unit of work, and routed to the configured database.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from neo4j import Driver, GraphDatabase, Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..shared.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    s = get_settings()
    driver = GraphDatabase.driver(
        s.neo4j_uri,
        auth=(s.neo4j_user, s.neo4j_password),
        # Connection pool tuned for a single-app workload. Bump for multi-worker
        # services.
        max_connection_pool_size=20,
        connection_acquisition_timeout=30.0,
    )
    driver.verify_connectivity()
    logger.info("Neo4j driver connected to %s", s.neo4j_uri)
    return driver


@contextmanager
def session() -> Iterator[Session]:
    s = get_settings()
    sess = get_driver().session(database=s.neo4j_database)
    try:
        yield sess
    finally:
        sess.close()


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4.0),
    retry=retry_if_exception_type(Exception),
)
def run_query(cypher: str, params: dict | None = None) -> list[dict]:
    """Execute a read/write query and return all records as dicts.

    Retries up to 3 times on transient errors. Use `session()` directly for
    streaming or transaction-scoped work.
    """
    with session() as s:
        result = s.run(cypher, params or {})
        return [dict(rec) for rec in result]


def close_driver() -> None:
    get_driver().close()
    get_driver.cache_clear()
