"""Append-only JSONL audit log for the voice helpdesk.

Same shape as project 06 — one event per line, structured fields:
  ts, session_id, principal_id, principal_name, identity_verified, tool,
  arguments (with PII redacted), outcome, duration_ms, error, result_summary.

Crucial for compliance: every privileged action (unlock, reset) needs a
record showing who triggered it, when, and whether the caller's identity
was verified at that moment.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_REDACT_KEYS = {"last4_phone", "password", "pin", "secret", "token", "api_key"}


def _redact(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k.lower() in _REDACT_KEYS:
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _redact(v)
        else:
            out[k] = v
    return out


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        *,
        session_id: str,
        principal_id: str | None,
        principal_name: str | None,
        identity_verified: bool,
        tool: str,
        arguments: dict[str, Any],
        outcome: str,
        duration_ms: float,
        result_summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "session_id": session_id,
            "principal_id": principal_id,
            "principal_name": principal_name,
            "identity_verified": identity_verified,
            "tool": tool,
            "arguments": _redact(arguments),
            "outcome": outcome,
            "duration_ms": round(duration_ms, 2),
            "result_summary": result_summary or {},
            "error": error,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


class AuditTimer:
    """Context manager that times one tool call and emits an audit row."""

    def __init__(
        self,
        log: AuditLog,
        *,
        session_id: str,
        principal_id: str | None,
        principal_name: str | None,
        identity_verified: bool,
        tool: str,
        arguments: dict[str, Any],
    ):
        self._log = log
        self._session_id = session_id
        self._principal_id = principal_id
        self._principal_name = principal_name
        self._identity_verified = identity_verified
        self._tool = tool
        self._arguments = arguments
        self._t0 = 0.0
        self.outcome = "error"
        self.result_summary: dict[str, Any] = {}
        self.error: str | None = None

    def __enter__(self) -> "AuditTimer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None and self.outcome != "denied":
            self.outcome = "error"
            self.error = f"{exc_type.__name__}: {exc}"
        duration_ms = (time.perf_counter() - self._t0) * 1000
        self._log.log(
            session_id=self._session_id,
            principal_id=self._principal_id,
            principal_name=self._principal_name,
            identity_verified=self._identity_verified,
            tool=self._tool,
            arguments=self._arguments,
            outcome=self.outcome,
            duration_ms=duration_ms,
            result_summary=self.result_summary,
            error=self.error,
        )

    def ok(self, summary: dict[str, Any] | None = None) -> None:
        self.outcome = "ok"
        if summary:
            self.result_summary = summary

    def denied(self, reason: str) -> None:
        self.outcome = "denied"
        self.error = reason
