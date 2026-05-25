"""Append-only audit log for every tool call.

JSON Lines format. Every record contains:
  - ts (UTC ISO)
  - principal (token_id and name; never the secret)
  - tool name
  - arguments (after PII redaction; SQL strings logged in full because they're
    already gated by the read-only enforcer)
  - outcome ("ok" | "denied" | "error")
  - error string if outcome != "ok"
  - row_count or value-shape summary on success
  - duration_ms

This file is the single source of truth for compliance review.
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


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        *,
        principal_id: str | None,
        principal_name: str | None,
        tool: str,
        arguments: dict[str, Any],
        outcome: str,                 # "ok" | "denied" | "error"
        duration_ms: float,
        result_summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "principal_id": principal_id,
            "principal_name": principal_name,
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


_REDACT_KEYS = {"bearer", "token", "secret", "password", "api_key"}


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


# ---------------------------------------------------------------------------
# Decorator helper used inside tools — measures duration and writes the log
# entry without each tool repeating the boilerplate.
# ---------------------------------------------------------------------------


class AuditTimer:
    """Context manager that emits one audit record on exit."""

    def __init__(
        self,
        log: AuditLog,
        *,
        principal_id: str | None,
        principal_name: str | None,
        tool: str,
        arguments: dict[str, Any],
    ):
        self._log = log
        self._principal_id = principal_id
        self._principal_name = principal_name
        self._tool = tool
        self._arguments = arguments
        self._t0: float = 0.0
        self.outcome: str = "error"
        self.result_summary: dict[str, Any] = {}
        self.error: str | None = None

    def __enter__(self) -> "AuditTimer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None and self.outcome != "denied":
            # Only treat as 'error' if a tool didn't already classify it as
            # 'denied' (e.g. policy rejections do that explicitly).
            self.outcome = "error"
            self.error = f"{exc_type.__name__}: {exc}"
        duration_ms = (time.perf_counter() - self._t0) * 1000
        self._log.log(
            principal_id=self._principal_id,
            principal_name=self._principal_name,
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
