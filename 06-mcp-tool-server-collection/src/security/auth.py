"""Token-based authentication and per-tool scope authorization.

Real deployments would terminate OAuth/OIDC at the edge (Auth0, Okta, Descope,
Scalekit, etc.) and forward verified claims as headers. For a self-contained
portfolio demo we use signed bearer tokens loaded from a JSON file, with
explicit scopes per token. The interface is the same — every tool call asks
'does this principal have scope X?' — so swapping in a real OAuth verifier is
a one-file change.

Scopes used by this server (declared by tools at registration time):
  - read:public      — list tables, run safe SELECTs against non-PII tables
  - read:pii         — same SELECTs but on tables with PII columns (masked unless this scope is present)
  - query:nl         — call the natural-language SQL tool (LLM-translated)
  - admin:catalog    — refresh the catalog / re-bind parquet files

Each token also has a name (for audit) and an optional rate-limit override.
"""
from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    """A successfully authenticated client."""
    token_id: str        # short stable id like 'tok_analyst_a'
    name: str            # human label for audit logs
    scopes: frozenset[str]
    rate_limit_per_hour: int | None = None


class AuthError(Exception):
    pass


class TokenStore:
    """Loads tokens from JSON and looks them up by raw bearer string.

    JSON shape (example):
        {
            "tokens": [
                {
                    "id": "tok_analyst_a",
                    "name": "Sales Analyst (read-only)",
                    "secret": "edp_live_3f1aa...",
                    "scopes": ["read:public"]
                },
                ...
            ]
        }
    """

    def __init__(self, path: Path):
        self.path = path
        self._by_secret: dict[str, Principal] = {}
        self._reload()

    def _reload(self) -> None:
        if not self.path.exists():
            logger.warning("Auth token file not found: %s — server will refuse all calls", self.path)
            self._by_secret = {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Failed to parse %s: %s", self.path, e)
            self._by_secret = {}
            return
        out: dict[str, Principal] = {}
        for row in data.get("tokens", []):
            secret = row.get("secret")
            if not secret:
                continue
            out[secret] = Principal(
                token_id=row.get("id", "anon"),
                name=row.get("name", "anon"),
                scopes=frozenset(row.get("scopes", [])),
                rate_limit_per_hour=row.get("rate_limit_per_hour"),
            )
        self._by_secret = out
        logger.info("Loaded %d auth tokens from %s", len(out), self.path)

    def authenticate(self, bearer: str | None) -> Principal:
        if not bearer:
            raise AuthError("missing bearer token")
        bearer = bearer.strip()
        if bearer.lower().startswith("bearer "):
            bearer = bearer[7:].strip()
        principal = self._by_secret.get(bearer)
        if principal is None:
            raise AuthError("invalid token")
        return principal


def require_scopes(principal: Principal, scopes: Iterable[str]) -> None:
    needed = frozenset(scopes)
    if not needed.issubset(principal.scopes):
        missing = sorted(needed - principal.scopes)
        raise AuthError(f"missing required scope(s): {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Token generation helper (used by the bootstrap CLI)
# ---------------------------------------------------------------------------


def generate_token(prefix: str = "edp_live_") -> str:
    return prefix + secrets.token_urlsafe(24)
