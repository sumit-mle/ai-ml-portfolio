"""Bridge between a browser WebSocket and the OpenAI Realtime API.

Architecture:

    [browser <PCM16 audio>] <──> [our WS /ws/voice] <──> [OpenAI Realtime WS]
                                          │
                                          └─ tool calls intercepted here:
                                             dispatched to the local handler
                                             via tools.catalog.dispatch and
                                             the function_call_output is sent
                                             back to OpenAI.

The model speaks audio + text simultaneously. We forward audio deltas from
OpenAI back to the browser as base64 binary frames; the browser plays them
and renders the text transcript.

Latency targets (per the 2026 production guides we surveyed):
  - TTFT (first audio chunk) < 800 ms p50
  - end-of-turn detection < 500 ms after silence
We measure these and emit them in the audit log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from ..audit import AuditLog, AuditTimer
from ..config import get_settings
from ..session import Session
from ..tools.catalog import CATALOG, dispatch, realtime_tool_specs
from .prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


REALTIME_URL_TMPL = "wss://api.openai.com/v1/realtime?model={model}"


def _build_session_update() -> dict[str, Any]:
    """The session.update payload we send right after connecting.

    GA shape (Nov 2025+):
      - session has explicit `type: "realtime"`
      - `model` is set inside the session
      - audio config nested under `audio.input` / `audio.output`
      - turn_detection lives under audio.input

    No OpenAI-Beta header — that's preview-only and now disabled.
    """
    s = get_settings()
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": s.realtime_model,
            "instructions": SYSTEM_PROMPT,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": {"type": "server_vad", "threshold": 0.5},
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": "alloy",
                },
            },
            "tools": realtime_tool_specs(),
            "tool_choice": "auto",
        },
    }


async def _forward_to_browser(
    upstream: websockets.WebSocketClientProtocol,
    browser: WebSocket,
    session: Session,
    audit: AuditLog,
    metrics: dict[str, Any],
) -> None:
    """Read events from OpenAI, forward audio/text to the browser, intercept
    tool calls and run them locally, push the function_call_output back.
    """
    pending_calls: dict[str, dict[str, Any]] = {}     # call_id -> partial args
    first_audio_seen = False
    async for raw in upstream:
        try:
            evt = json.loads(raw)
        except Exception:
            continue
        etype = evt.get("type", "")

        # Useful debug for development; quiet in production.
        if etype.startswith("error"):
            logger.warning("OpenAI Realtime error: %s", evt)
            await browser.send_json({"type": "agent.error", "data": evt})
            continue

        # ---- Tool-call orchestration --------------------------------------
        if etype == "response.output_item.added":
            item = evt.get("item", {})
            if item.get("type") == "function_call":
                pending_calls[item["call_id"]] = {
                    "name": item.get("name", ""),
                    "arguments": "",
                    "started_at": time.perf_counter(),
                }

        elif etype == "response.function_call_arguments.delta":
            cid = evt.get("call_id")
            if cid in pending_calls:
                pending_calls[cid]["arguments"] += evt.get("delta", "")

        elif etype == "response.function_call_arguments.done":
            cid = evt.get("call_id")
            call = pending_calls.pop(cid, None)
            if call is None:
                continue
            try:
                args = json.loads(call["arguments"]) if call["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_name = call["name"]
            await _run_tool_and_push_output(
                upstream, browser, session, audit, tool_name, args, cid,
            )

        # ---- Audio + text streaming --------------------------------------
        elif etype == "response.output_audio.delta":
            if not first_audio_seen:
                first_audio_seen = True
                metrics["ttft_ms"] = round(
                    (time.perf_counter() - metrics["turn_started_at"]) * 1000, 1,
                )
            # Forward base64 PCM straight to the browser.
            await browser.send_json({
                "type": "agent.audio",
                "delta": evt.get("delta"),
            })
        elif etype == "response.output_audio_transcript.delta":
            await browser.send_json({
                "type": "agent.transcript",
                "delta": evt.get("delta", ""),
            })
        elif etype == "response.output_audio_transcript.done":
            await browser.send_json({
                "type": "agent.transcript_done",
                "transcript": evt.get("transcript", ""),
            })
        elif etype == "input_audio_buffer.speech_started":
            metrics["turn_started_at"] = time.perf_counter()
            first_audio_seen = False
            await browser.send_json({"type": "user.speaking"})
        elif etype == "input_audio_buffer.speech_stopped":
            await browser.send_json({"type": "user.silence"})
        elif etype == "conversation.item.input_audio_transcription.completed":
            await browser.send_json({
                "type": "user.transcript",
                "transcript": evt.get("transcript", ""),
            })
        elif etype == "response.done":
            # Could capture full usage here for cost monitoring.
            pass


async def _run_tool_and_push_output(
    upstream: websockets.WebSocketClientProtocol,
    browser: WebSocket,
    session: Session,
    audit: AuditLog,
    tool_name: str,
    args: dict[str, Any],
    call_id: str,
) -> None:
    """Run a tool locally, audit it, push the result back to OpenAI, and ask
    for a follow-up response.
    """
    timer = AuditTimer(
        audit,
        session_id=session.session_id,
        principal_id=session.caller_employee_id,
        principal_name=session.caller_name,
        identity_verified=session.is_verified_for(args.get("employee_id", "") or ""),
        tool=tool_name,
        arguments=args,
    )
    with timer:
        try:
            result = dispatch(tool_name, args, session)
            ok = bool(result.get("ok", True))
            if ok and result.get("error") != "identity_not_verified":
                timer.ok({"keys": list(result.keys())})
            else:
                timer.denied(result.get("error", "tool_returned_not_ok"))
        except Exception as e:
            logger.exception("Tool %s crashed", tool_name)
            result = {"ok": False, "error": f"tool_crashed: {e.__class__.__name__}"}

    # Show the tool call/result in the browser console for transparency.
    await browser.send_json({
        "type": "agent.tool_call",
        "name": tool_name,
        "arguments": args,
        "result": result,
    })

    # Tell OpenAI: here's the function output, please keep going.
    await upstream.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(result),
        },
    }))
    await upstream.send(json.dumps({"type": "response.create"}))


async def _forward_to_openai(
    upstream: websockets.WebSocketClientProtocol,
    browser: WebSocket,
) -> None:
    """Read messages from the browser, forward them upstream.

    Browser sends:
      { type: 'user.audio', delta: '<base64 PCM16>' }
      { type: 'user.text',  text: '...' }     -- optional text-only mode
      { type: 'user.commit_audio' }            -- end-of-turn marker
    """
    while True:
        try:
            msg = await browser.receive_json()
        except WebSocketDisconnect:
            return
        except Exception:
            continue
        mtype = msg.get("type")
        if mtype == "user.audio":
            await upstream.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": msg.get("delta"),
            }))
        elif mtype == "user.commit_audio":
            await upstream.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await upstream.send(json.dumps({"type": "response.create"}))
        elif mtype == "user.text":
            await upstream.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": msg.get("text", "")}],
                },
            }))
            await upstream.send(json.dumps({"type": "response.create"}))


async def handle_browser_connection(browser: WebSocket, audit: AuditLog) -> None:
    """Driver entry point: open OpenAI Realtime WS, fan I/O between it and the browser."""
    s = get_settings()
    if not s.openai_api_key:
        await browser.send_json({"type": "agent.error", "message": "OPENAI_API_KEY not configured"})
        await browser.close()
        return

    session = Session()
    metrics: dict[str, Any] = {"turn_started_at": time.perf_counter()}

    headers = {
        "Authorization": f"Bearer {s.openai_api_key}",
    }
    url = REALTIME_URL_TMPL.format(model=s.realtime_model)

    try:
        async with websockets.connect(
            url,
            additional_headers=headers,
            max_size=2**24,
        ) as upstream:
            # Configure the session before any audio flows
            await upstream.send(json.dumps(_build_session_update()))
            await browser.send_json({
                "type": "agent.ready",
                "session_id": session.session_id,
                "tools": [t.name for t in CATALOG.values()],
            })
            # Greet first
            await upstream.send(json.dumps({
                "type": "response.create",
                "response": {
                    "instructions": (
                        "Greet the caller briefly: 'Acme IT helpdesk, this is "
                        "Avery — what's going on?' Then wait for them to speak."
                    ),
                },
            }))
            await asyncio.gather(
                _forward_to_browser(upstream, browser, session, audit, metrics),
                _forward_to_openai(upstream, browser),
                return_exceptions=False,
            )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("Realtime bridge error")
        try:
            await browser.send_json({"type": "agent.error", "message": str(e)})
        except Exception:
            pass
