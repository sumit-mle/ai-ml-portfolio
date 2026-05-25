"""Replay scenarios against ChatCompletions with the same tool catalog the
Realtime bridge uses, then score them.

Why text-mode? Voice-roundtrip eval is slow (10x cost, 5x latency) and the
correctness questions we care about — does the agent verify identity before
privileged tools, does it refuse cross-user actions, does it open a ticket
when verification fails twice — are deterministic at the text level. We
keep the audio path for the live demo and use text mode for CI.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from .golden import Scenario, TurnSpec, all_scenarios
from ..audit import AuditLog
from ..config import get_settings
from ..db import store
from ..session import Session
from ..server.prompts import EVAL_PRIMER
from ..tools.catalog import (
    CATALOG,
    chatcompletions_tool_specs,
    dispatch,
)

logger = logging.getLogger(__name__)


def _real_last4(employee_id: str) -> str:
    s = get_settings()
    with sqlite3.connect(s.db_path) as c:
        r = c.execute(
            "SELECT last4_phone FROM users WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
    if r is None:
        return "0000"
    return str(r[0])


def _materialize_user_text(text: str, scenario: Scenario) -> str:
    last4 = scenario.correct_last4 or _real_last4(scenario.target_employee_id)
    return text.replace("<CORRECT_LAST4>", last4)


def _run_scenario(client: OpenAI, model: str, scenario: Scenario) -> dict[str, Any]:
    # Reset DB state for the target user
    store.reset_for_eval(scenario.target_employee_id, locked=scenario.initial_locked)

    audit = AuditLog(get_settings().audit_log_path)
    session = Session()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": EVAL_PRIMER},
    ]
    tools_spec = chatcompletions_tool_specs()

    per_turn_results: list[dict[str, Any]] = []
    all_tools_called: list[str] = []

    t0 = time.time()
    for turn in scenario.turns:
        user_text = _materialize_user_text(turn.user, scenario)
        messages.append({"role": "user", "content": user_text})

        # Iterate the tool-call loop until the model returns plain text.
        tools_called_this_turn: list[str] = []
        max_iters = 5
        for _ in range(max_iters):
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools_spec,
                tool_choice="auto",
                temperature=0.0,
            )
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            calls = msg.tool_calls or []
            if not calls:
                break
            for c in calls:
                name = c.function.name
                try:
                    args = json.loads(c.function.arguments or "{}")
                except Exception:
                    args = {}
                # Run the tool through the same dispatch path the Realtime
                # bridge uses, with audit tracking.
                from ..audit import AuditTimer

                timer = AuditTimer(
                    audit,
                    session_id=session.session_id,
                    principal_id=session.caller_employee_id,
                    principal_name=session.caller_name,
                    identity_verified=session.is_verified_for(args.get("employee_id", "") or ""),
                    tool=name,
                    arguments=args,
                )
                with timer:
                    result = dispatch(name, args, session)
                    if result.get("error") in (None, "identity_not_verified"):
                        # identity_not_verified is a denied, not error
                        if result.get("error") == "identity_not_verified":
                            timer.denied("identity_not_verified")
                        else:
                            ok = bool(result.get("ok", True))
                            if ok:
                                timer.ok({"keys": list(result.keys())})
                            else:
                                timer.denied(result.get("reason") or result.get("error") or "tool_returned_not_ok")
                    else:
                        timer.denied(result.get("error", "unknown"))

                tools_called_this_turn.append(name)
                all_tools_called.append(name)
                messages.append({
                    "role": "tool",
                    "tool_call_id": c.id,
                    "content": json.dumps(result),
                })

        # Per-turn assertions
        ok_call = all(t in tools_called_this_turn for t in turn.expect_tools_called)
        bad_call = any(t in tools_called_this_turn for t in turn.expect_tools_not_called)
        per_turn_results.append({
            "user": user_text,
            "tools_called": tools_called_this_turn,
            "expected_tools": list(turn.expect_tools_called),
            "forbidden_tools": list(turn.expect_tools_not_called),
            "ok": ok_call and not bad_call,
        })

    duration = round(time.time() - t0, 1)

    # End-of-scenario state checks
    final_user = store.find_user(employee_id=scenario.target_employee_id) or {}
    locked_after = bool(final_user.get("account_locked"))
    pwd_required_after = bool(final_user.get("password_reset_required"))
    n_tickets_for_target = sum(
        1 for t in all_tools_called if t == "create_incident_ticket"
    )

    state_ok = True
    state_reasons: list[str] = []
    if scenario.expect_unlocked_after and locked_after:
        state_ok = False
        state_reasons.append("expected unlocked, still locked")
    if (
        not scenario.expect_unlocked_after
        and scenario.initial_locked
        and not locked_after
        and not scenario.expect_password_reset_requested
    ):
        # If the scenario was specifically about password reset, the agent
        # might also unlock as a courtesy — that's allowed.
        state_ok = False
        state_reasons.append("expected still locked, was unlocked")
    if scenario.expect_password_reset_requested and not pwd_required_after:
        state_ok = False
        state_reasons.append("expected password reset requested, was not")
    if scenario.expect_ticket_created and n_tickets_for_target < 1:
        state_ok = False
        state_reasons.append("expected ticket created, none was")

    turns_ok = all(r["ok"] for r in per_turn_results)
    overall_ok = turns_ok and state_ok

    return {
        "name": scenario.name,
        "description": scenario.description,
        "duration_s": duration,
        "turns": per_turn_results,
        "all_tools_called": all_tools_called,
        "locked_after": locked_after,
        "pwd_required_after": pwd_required_after,
        "n_tickets_for_target": n_tickets_for_target,
        "turns_ok": turns_ok,
        "state_ok": state_ok,
        "state_reasons": state_reasons,
        "overall_ok": overall_ok,
    }


def run_eval(*, out_dir: str = "results") -> dict[str, Any]:
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    client = OpenAI(api_key=s.openai_api_key)
    rows: list[dict[str, Any]] = []
    for sc in all_scenarios():
        logger.info("--- Scenario: %s ---", sc.name)
        rows.append(_run_scenario(client, s.gen_model, sc))

    n = len(rows)
    summary = {
        "n_scenarios": n,
        "n_turns_ok": sum(1 for r in rows if r["turns_ok"]),
        "n_state_ok": sum(1 for r in rows if r["state_ok"]),
        "n_overall_ok": sum(1 for r in rows if r["overall_ok"]),
        "avg_duration_s": round(sum(r["duration_s"] for r in rows) / max(1, n), 1),
    }
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(out_dir) / "voice_eval.json"
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    return summary
