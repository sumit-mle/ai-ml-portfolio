"""SQLite warehouse for the IT helpdesk demo.

Three tables:
  - users           Active Directory mock: employee_id, name, email, last4_phone,
                    account_locked (bool), password_reset_required (bool)
  - vpn_status      per-user VPN reachability (random on bootstrap)
  - tickets         ServiceNow incidents created by the agent

Bootstrap inserts 50 synthetic users so eval scenarios can target real records.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)


_DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        employee_id   TEXT PRIMARY KEY,
        full_name     TEXT NOT NULL,
        email         TEXT NOT NULL,
        last4_phone   TEXT NOT NULL,
        account_locked        INTEGER NOT NULL DEFAULT 0,
        password_reset_required INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vpn_status (
        employee_id   TEXT PRIMARY KEY REFERENCES users(employee_id),
        last_connected_at TEXT,
        is_connected  INTEGER NOT NULL DEFAULT 0,
        client_version TEXT,
        last_error    TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id     TEXT PRIMARY KEY,
        employee_id   TEXT NOT NULL,
        category      TEXT NOT NULL,
        priority      TEXT NOT NULL,
        summary       TEXT NOT NULL,
        details       TEXT,
        status        TEXT NOT NULL DEFAULT 'open',
        created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (employee_id) REFERENCES users(employee_id)
    )
    """,
]


_FIRST_NAMES = [
    "Alex", "Jordan", "Sam", "Casey", "Taylor", "Morgan", "Riley",
    "Avery", "Quinn", "Drew", "Robin", "Jamie", "Cameron", "Devin",
]
_LAST_NAMES = [
    "Smith", "Lee", "Patel", "Garcia", "Chen", "Johnson", "Brown",
    "Davis", "Miller", "Wilson", "Khan", "Singh", "Lopez", "Williams",
]


def _seed_users(conn: sqlite3.Connection, n: int = 50) -> None:
    import random

    rng = random.Random(42)
    rows: list[tuple] = []
    vpn_rows: list[tuple] = []
    for i in range(1, n + 1):
        emp = f"E{i:05d}"
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        name = f"{first} {last}"
        email = f"{first.lower()}.{last.lower()}{i}@acme.com"
        last4 = f"{rng.randint(0, 9999):04d}"
        locked = 1 if rng.random() < 0.10 else 0
        pwd_req = 1 if rng.random() < 0.20 else 0
        rows.append((emp, name, email, last4, locked, pwd_req))

        is_conn = 1 if rng.random() < 0.6 else 0
        last_conn = "2026-05-25T08:00:00Z" if is_conn else None
        version = rng.choice(["AnyConnect 5.1.2", "AnyConnect 5.1.3", "AnyConnect 5.0.9"])
        last_err = None if is_conn else rng.choice([
            "TLS handshake timeout",
            "MFA token expired",
            "Could not reach VPN gateway",
            None,
        ])
        vpn_rows.append((emp, last_conn, is_conn, version, last_err))

    conn.executemany(
        "INSERT OR IGNORE INTO users "
        "(employee_id, full_name, email, last4_phone, account_locked, password_reset_required) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO vpn_status "
        "(employee_id, last_connected_at, is_connected, client_version, last_error) "
        "VALUES (?,?,?,?,?)",
        vpn_rows,
    )


def init_db(*, force: bool = False) -> dict:
    s = get_settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    if force and s.db_path.exists():
        s.db_path.unlink()

    with sqlite3.connect(s.db_path) as conn:
        for stmt in _DDL:
            conn.execute(stmt)
        existing = conn.execute("SELECT count(*) FROM users").fetchone()[0]
        if existing == 0:
            _seed_users(conn)
        n_users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
        n_locked = conn.execute(
            "SELECT count(*) FROM users WHERE account_locked = 1"
        ).fetchone()[0]
        n_vpn_down = conn.execute(
            "SELECT count(*) FROM vpn_status WHERE is_connected = 0"
        ).fetchone()[0]

    logger.info(
        "Helpdesk DB ready: %d users (%d locked), %d VPN-down at %s",
        n_users, n_locked, n_vpn_down, s.db_path,
    )
    return {
        "users": n_users,
        "locked_accounts": n_locked,
        "vpn_down": n_vpn_down,
        "path": str(s.db_path),
    }
