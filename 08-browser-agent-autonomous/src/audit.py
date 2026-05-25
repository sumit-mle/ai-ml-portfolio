"""Append-only JSONL audit log for the browser agent.

Each event records:
  - ts (UTC ISO)
  - run_id (one per pull-evidence call)
  - vendor_id
  - principal (operator who launched the run)
  - action (navigate | login | click | extract | download | screenshot | error)
  - url (current page URL after action)
  - description (human-readable)
  - screenshot_path (relative)
  - duration_ms

Compliance teams care about the action history more than the final result —
this is the trail that says "the agent did X, then Y, then downloaded Z".
"""
from __future__ import annotations

import json
import logging
import threading
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
        run_id: str,
        vendor_id: str,
        principal: str,
        action: str,
        outcome: str,                  # ok | warn | error
        url: str | None = None,
        description: str = "",
        screenshot_path: str | None = None,
        duration_ms: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": run_id,
            "vendor_id": vendor_id,
            "principal": principal,
            "action": action,
            "outcome": outcome,
            "url": url,
            "description": description,
            "screenshot_path": screenshot_path,
            "duration_ms": duration_ms,
        }
        if extra:
            record["extra"] = extra
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
