"""Tool catalog and dispatcher.

This module is the single source of truth for tools the agent can call,
in BOTH the Realtime API and ChatCompletions formats. Every tool has:
  - a JSON Schema for params (for the model)
  - a Python handler that returns a JSON-serializable dict
  - a `requires_verified_identity` flag (privileged tools require
    successful identity verification earlier in the same session)

The Realtime bridge and the eval runner both call `dispatch(tool_name, args, session)`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from . import handlers
from ..session import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]                # JSON Schema
    handler: Callable[[dict[str, Any], Session], dict[str, Any]]
    requires_verified_identity: bool = False


CATALOG: dict[str, Tool] = {
    "lookup_user": Tool(
        name="lookup_user",
        description=(
            "Find the caller's user record by employee_id OR email. "
            "Returns name, email, last4_phone (masked), and account/password state. "
            "Use this BEFORE any identity verification."
        ),
        parameters={
            "type": "object",
            "properties": {
                "employee_id": {"type": "string", "description": "Employee ID like E00042"},
                "email": {"type": "string", "description": "Corporate email"},
            },
            "additionalProperties": False,
        },
        handler=handlers.lookup_user,
    ),
    "verify_identity": Tool(
        name="verify_identity",
        description=(
            "Verify the caller is who they say they are. They must provide their "
            "employee_id AND the last 4 digits of the phone number on file. "
            "Returns ok=true on success and sets the session as identity-verified."
        ),
        parameters={
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
                "last4_phone": {
                    "type": "string",
                    "description": "Last 4 digits of phone on file, e.g. '0421'",
                },
            },
            "required": ["employee_id", "last4_phone"],
            "additionalProperties": False,
        },
        handler=handlers.verify_identity,
    ),
    "check_vpn_status": Tool(
        name="check_vpn_status",
        description=(
            "Look up the VPN connection status and last known error for an "
            "employee. Read-only; no identity verification required."
        ),
        parameters={
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
            },
            "required": ["employee_id"],
            "additionalProperties": False,
        },
        handler=handlers.check_vpn_status,
    ),
    "unlock_account": Tool(
        name="unlock_account",
        description=(
            "Unlock the caller's locked account. PRIVILEGED — only call after "
            "verify_identity has succeeded for the same employee_id this session."
        ),
        parameters={
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
            },
            "required": ["employee_id"],
            "additionalProperties": False,
        },
        handler=handlers.unlock_account,
        requires_verified_identity=True,
    ),
    "reset_password": Tool(
        name="reset_password",
        description=(
            "Send a one-time password reset link to the user's email. "
            "PRIVILEGED — only call after verify_identity has succeeded "
            "for the same employee_id this session."
        ),
        parameters={
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
            },
            "required": ["employee_id"],
            "additionalProperties": False,
        },
        handler=handlers.reset_password,
        requires_verified_identity=True,
    ),
    "create_incident_ticket": Tool(
        name="create_incident_ticket",
        description=(
            "Open a ServiceNow-style incident ticket for the caller. Use when "
            "the issue can't be resolved by the other tools. category should "
            "be one of: 'access', 'vpn', 'hardware', 'software', 'other'. "
            "priority: 'P1' | 'P2' | 'P3' | 'P4'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": ["access", "vpn", "hardware", "software", "other"],
                },
                "priority": {
                    "type": "string",
                    "enum": ["P1", "P2", "P3", "P4"],
                    "description": "P1 = service down for many users; P4 = nice-to-have",
                },
                "summary": {"type": "string", "description": "One-line incident summary"},
                "details": {"type": "string", "description": "Caller's verbatim description"},
            },
            "required": ["employee_id", "category", "priority", "summary"],
            "additionalProperties": False,
        },
        handler=handlers.create_incident_ticket,
    ),
}


def realtime_tool_specs() -> list[dict[str, Any]]:
    """Return the tools in OpenAI Realtime API format.

    Realtime expects: {type:'function', name, description, parameters}
    """
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in CATALOG.values()
    ]


def chatcompletions_tool_specs() -> list[dict[str, Any]]:
    """Return the tools in OpenAI ChatCompletions format (used by the eval).

    ChatCompletions expects: {type:'function', function:{name, description, parameters}}
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in CATALOG.values()
    ]


def dispatch(name: str, arguments: dict[str, Any], session: Session) -> dict[str, Any]:
    tool = CATALOG.get(name)
    if tool is None:
        return {"ok": False, "error": f"unknown_tool: {name}"}
    if tool.requires_verified_identity and not session.is_verified_for(
        arguments.get("employee_id", "")
    ):
        return {
            "ok": False,
            "error": "identity_not_verified",
            "message": (
                "I can't perform that action until I've verified the caller's "
                "identity for the matching employee_id."
            ),
        }
    return tool.handler(arguments, session)
