"""All SQLite read/write operations behind a clean Python API.

Voice tools call these functions, never raw SQL. Keeps the persistence
layer swappable (real ServiceNow, real LDAP) without changing tool code.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from ..config import get_settings

logger = logging.getLogger(__name__)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    s = get_settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(s.db_path, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def _row_to_user(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "employee_id": r["employee_id"],
        "full_name": r["full_name"],
        "email": r["email"],
        "last4_phone": r["last4_phone"],
        "account_locked": bool(r["account_locked"]),
        "password_reset_required": bool(r["password_reset_required"]),
    }


def find_user(*, employee_id: str | None = None, email: str | None = None) -> dict[str, Any] | None:
    with _conn() as c:
        if employee_id:
            r = c.execute("SELECT * FROM users WHERE employee_id = ?", (employee_id,)).fetchone()
        elif email:
            r = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        else:
            return None
    return _row_to_user(r)


def verify_identity(*, employee_id: str, last4_phone: str) -> bool:
    with _conn() as c:
        r = c.execute(
            "SELECT last4_phone FROM users WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
    if r is None:
        return False
    return str(r["last4_phone"]).strip() == str(last4_phone).strip()


def unlock_account(employee_id: str) -> dict[str, Any]:
    with _conn() as c:
        r = c.execute(
            "SELECT account_locked FROM users WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        if r is None:
            return {"ok": False, "reason": "user_not_found"}
        if not r["account_locked"]:
            return {"ok": True, "already": True, "message": "Account was already unlocked."}
        c.execute(
            "UPDATE users SET account_locked = 0 WHERE employee_id = ?",
            (employee_id,),
        )
    return {"ok": True, "already": False, "message": "Account unlocked."}


def request_password_reset(employee_id: str) -> dict[str, Any]:
    """Marks the password as 'reset required'. In a real system this would
    enqueue an AD task. For the demo it just sets the flag and returns a
    fake one-time link the caller would receive in email.
    """
    with _conn() as c:
        r = c.execute(
            "SELECT email FROM users WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        if r is None:
            return {"ok": False, "reason": "user_not_found"}
        c.execute(
            "UPDATE users SET password_reset_required = 1 WHERE employee_id = ?",
            (employee_id,),
        )
    reset_link = f"https://acme-okta.example.com/reset/{uuid4().hex[:12]}"
    return {
        "ok": True,
        "email": r["email"],
        "reset_link": reset_link,
        "message": (
            f"Password reset link emailed to {r['email']}. The link expires in 30 minutes."
        ),
    }


def vpn_status(employee_id: str) -> dict[str, Any]:
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM vpn_status WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
    if r is None:
        return {"ok": False, "reason": "no_vpn_record"}
    return {
        "ok": True,
        "is_connected": bool(r["is_connected"]),
        "last_connected_at": r["last_connected_at"],
        "client_version": r["client_version"],
        "last_error": r["last_error"],
    }


def create_ticket(
    *,
    employee_id: str,
    category: str,
    priority: str,
    summary: str,
    details: str | None = None,
) -> dict[str, Any]:
    ticket_id = "INC" + uuid4().hex[:8].upper()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            "INSERT INTO tickets (ticket_id, employee_id, category, priority, summary, details, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ticket_id, employee_id, category, priority, summary, details or "", "open", now),
        )
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "category": category,
        "priority": priority,
        "summary": summary,
        "status": "open",
        "created_at": now,
    }


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,),
        ).fetchone()
    if r is None:
        return None
    return dict(r)


def reset_for_eval(employee_id: str, *, locked: bool = True) -> None:
    """Reset a single user back to a known state between eval scenarios.

    Idempotent. Used by the eval runner so each scenario starts from a clean
    slate without rebuilding the whole DB.
    """
    with _conn() as c:
        c.execute(
            "UPDATE users SET account_locked = ?, password_reset_required = 0 "
            "WHERE employee_id = ?",
            (1 if locked else 0, employee_id),
        )
        c.execute(
            "DELETE FROM tickets WHERE employee_id = ?",
            (employee_id,),
        )
