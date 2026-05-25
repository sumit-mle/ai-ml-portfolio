"""Per-call session state.

A Session is tied to one phone call (or one WebSocket connection). It tracks:
  - session_id (uuid)
  - the caller (employee_id, name) once lookup_user has resolved them
  - which employee_id (if any) has been identity-verified this session

Privileged tools refuse to run unless verification matches the action's
target employee_id — so verifying yourself doesn't let you reset someone
else's password.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: f"sess_{uuid4().hex[:12]}")
    caller_employee_id: Optional[str] = None
    caller_name: Optional[str] = None
    verified_for: Optional[str] = None      # employee_id this session is verified for

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def set_caller(self, *, employee_id: str, full_name: str) -> None:
        # Don't overwrite an already-verified caller with a different lookup.
        if self.caller_employee_id and self.caller_employee_id != employee_id:
            # We allow this — agent might switch context — but we INVALIDATE
            # any previous verification.
            self.verified_for = None
        self.caller_employee_id = employee_id
        self.caller_name = full_name

    def mark_verified(self, employee_id: str) -> None:
        self.verified_for = employee_id

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_verified_for(self, employee_id: str) -> bool:
        return bool(employee_id) and self.verified_for == employee_id
