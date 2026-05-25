"""Tool handler implementations. Each returns a JSON-serializable dict."""
from __future__ import annotations

import logging
from typing import Any

from ..db import store
from ..session import Session

logger = logging.getLogger(__name__)


def lookup_user(args: dict[str, Any], session: Session) -> dict[str, Any]:
    user = store.find_user(
        employee_id=args.get("employee_id"),
        email=args.get("email"),
    )
    if user is None:
        return {"ok": False, "error": "user_not_found"}
    # Mask last4_phone so the LLM can't leak it back to the caller.
    masked = dict(user)
    masked["last4_phone"] = "***" + user["last4_phone"][-2:]
    session.set_caller(employee_id=user["employee_id"], full_name=user["full_name"])
    return {"ok": True, "user": masked}


def verify_identity(args: dict[str, Any], session: Session) -> dict[str, Any]:
    emp = (args.get("employee_id") or "").strip()
    last4 = (args.get("last4_phone") or "").strip()
    ok = store.verify_identity(employee_id=emp, last4_phone=last4)
    if ok:
        # Mark this session as verified for this specific employee_id.
        session.mark_verified(emp)
        return {"ok": True, "employee_id": emp, "verified": True}
    return {
        "ok": False,
        "verified": False,
        "error": "identity_mismatch",
        "message": (
            "The last 4 digits don't match the phone on file for that employee. "
            "Try again or escalate to a human agent."
        ),
    }


def check_vpn_status(args: dict[str, Any], session: Session) -> dict[str, Any]:
    emp = (args.get("employee_id") or "").strip()
    info = store.vpn_status(emp)
    return info


def unlock_account(args: dict[str, Any], session: Session) -> dict[str, Any]:
    emp = (args.get("employee_id") or "").strip()
    return store.unlock_account(emp)


def reset_password(args: dict[str, Any], session: Session) -> dict[str, Any]:
    emp = (args.get("employee_id") or "").strip()
    return store.request_password_reset(emp)


def create_incident_ticket(args: dict[str, Any], session: Session) -> dict[str, Any]:
    return store.create_ticket(
        employee_id=(args.get("employee_id") or "").strip(),
        category=args["category"],
        priority=args["priority"],
        summary=args["summary"],
        details=args.get("details") or "",
    )
