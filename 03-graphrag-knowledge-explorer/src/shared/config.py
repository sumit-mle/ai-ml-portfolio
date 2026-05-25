"""Typed runtime config, loaded once from .env at process start.

Centralizing config means every module imports `Settings` instead of calling
`os.getenv` ad hoc. Easier to test and to swap for a hosted Neo4j or different
LLM provider later.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    # OpenAI
    openai_api_key: str
    gen_model: str
    extract_model: str
    embed_model: str
    embed_dim: int

    # Neo4j
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str

    # SEC EDGAR
    sec_user_agent: str

    # Paths
    data_dir: str


# text-embedding-3-small returns 1536-dimensional vectors; small/large differ.
_EMBED_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    embed_model = os.getenv("EMBED_MODEL", "text-embedding-3-small")
    embed_dim = _EMBED_DIMS.get(embed_model, 1536)

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        gen_model=os.getenv("GEN_MODEL", "gpt-4o-mini"),
        extract_model=os.getenv("EXTRACT_MODEL", "gpt-4o-mini"),
        embed_model=embed_model,
        embed_dim=embed_dim,
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "graphrag-pass"),
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
        sec_user_agent=os.getenv(
            "SEC_USER_AGENT", "GraphRAG-Demo example@example.com"
        ),
        data_dir=os.getenv("DATA_DIR", "./data"),
    )


def require_openai_key() -> None:
    if not get_settings().openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
