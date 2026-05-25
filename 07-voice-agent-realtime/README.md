# 07 — Voice Helpdesk Agent (OpenAI Realtime)

A production-pattern **voice agent** for first-line IT helpdesk: account unlocks, password resets, VPN troubleshooting, and ticket escalation. Speech in, speech out, real backend writes, with the security gates that separate a portfolio demo from a real call-center deployment.

**Speech path:** browser mic → FastAPI WebSocket → OpenAI Realtime API → speech-to-speech with tool calls intercepted server-side and dispatched to local handlers, results streamed back as audio.

## The business problem

Phone is the **most expensive IT support channel** at every Fortune 500. ServiceNow's published numbers:

- **42%** of help desk contacts are phone calls (70% in healthcare/retail/finance)
- A phone interaction costs **~50× more** than self-service
- ServiceNow's own internal voice agent now resolves 90% of inbound IT tickets

This project shows the production shape of that capability: voice-first, tool-using, identity-gating, fully audited.

| Metric | Manual L1 baseline | With this agent |
|--------|--------------------:|----------------:|
| Avg call duration (lock + reset) | 4-6 min | **~30s** in text-mode eval |
| Identity verification before privileged action | Manual, often skipped | **Server-enforced — bypass impossible** |
| Audit trail | Recording transcripts later | **Every tool call: principal + verified flag + outcome** |
| Cost per resolved call | $5-15 | < $0.05 (gpt-realtime + gpt-4o-mini judge) |

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| Speech-to-speech | **OpenAI Realtime API (`gpt-realtime`)** | One model handles STT + reasoning + TTS. Per the 2026 production guides we surveyed, this collapses the latency stack from ~2.5s to <800ms TTFT. |
| Web framework | **FastAPI 0.136 + uvicorn** | Native WebSocket; same stack as the rest of the portfolio. |
| Browser audio | Web Audio API + AudioWorklet | Captures mic at 24kHz PCM16, decodes 24kHz PCM16 playback — no LiveKit/WebRTC dependency for the demo. |
| Backend store | SQLite | Simulates AD + ServiceNow; clean swap for a real ITSM API behind `db/store.py`. |
| Eval framework | OpenAI ChatCompletions text-mode replay with the same tool catalog | Deterministic, cheap, asserts the security properties we care about. |
| Audit log | JSONL (same pattern as project 06) | Every tool call: `session_id, principal_id, identity_verified, outcome, duration_ms`. |

We deliberately **did not** use LiveKit/Pipecat. They're the right answer for thousands of concurrent telephony calls, but they add ~1500 lines of WebRTC plumbing that distracts from the security and tool-use story this project is making.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                  Browser (single-page UI)                               │
│   Mic ─▶ AudioWorklet ─▶ PCM16 24kHz ─▶ base64 frames ─▶ WebSocket     │
│   ◀─ PCM16 24kHz ◀─ base64 frames ◀─ WebSocket ◀─ TTS chunks            │
└────────────────────────────┬────────────────────────────────────────────┘
                             │  /ws/voice
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  FastAPI realtime_bridge.py                              │
│  ┌──────────────────────────────────────────────────────────────┐        │
│  │  forward(browser → OpenAI):                                  │        │
│  │     user.audio        → input_audio_buffer.append            │        │
│  │     user.commit_audio → input_audio_buffer.commit + response.create
│  │  forward(OpenAI → browser):                                  │        │
│  │     response.output_audio.delta      → playback              │        │
│  │     response.output_audio_transcript → transcript pane       │        │
│  │     response.function_call_*         → INTERCEPT, dispatch   │        │
│  │                                        locally, push back    │        │
│  │                                        function_call_output  │        │
│  └──────────────────────────────────────────────────────────────┘        │
└────────────────────────────┬────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  Tool catalog (catalog.py)                               │
│  • lookup_user, verify_identity, check_vpn_status (read-only)           │
│  • unlock_account, reset_password (PRIVILEGED — server-side gate)       │
│  • create_incident_ticket                                                │
│                                                                          │
│  Dispatcher refuses every privileged tool unless the SAME session has    │
│  already passed verify_identity for the SAME employee_id.                │
└────────────────────────────┬────────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│      SQLite store + JSONL audit log (every call timed and recorded)     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Security gates

1. **Server-side identity gate.** `unlock_account` and `reset_password` refuse unless `Session.is_verified_for(employee_id)` is True. The LLM cannot bypass this — the dispatcher checks before the handler runs.
2. **Cross-user defense.** Verifying yourself does NOT authorize actions on a different `employee_id`. The dispatcher requires `session.verified_for == args.employee_id`.
3. **Two-strike escalation.** After two failed `verify_identity` calls in a session the agent is instructed to open a P3 ticket instead of trying again. (Verified in eval.)
4. **Output redaction.** `lookup_user` masks `last4_phone` before returning to the LLM ("**05" not "0405"). The audit log redacts `last4_phone`, `password`, `pin`, `secret`, `token`.
5. **Append-only JSONL audit.** Every tool call gets a row with `session_id`, `principal_id`, `identity_verified`, `outcome`, `duration_ms`. Compatible with Splunk/Datadog/Loki.

## Quick start

```sh
copy .env.example .env
# Edit .env: set OPENAI_API_KEY

# 1. Build the synthetic helpdesk DB (50 users, 8 locked, 20 VPN-down)
python -m src.cli init-db

# 2. Run the text-mode eval (5 scenarios, ~$0.02 OpenAI, ~45s)
python -m src.cli eval

# 3. Start the live voice server
python -m src.cli serve
# Open http://127.0.0.1:7777 in Chrome / Edge / Safari and click "Start call".
# (Browser will prompt for mic permission.)

# 4. View the audit log after a call
python -m src.cli show-audit --n 20
```

## Verified results

5/5 eval scenarios pass on real ChatCompletions roundtrips with the live tool catalog. See [`results/README.md`](./results/README.md) for the full breakdown.

| Scenario | What it proves |
|----------|----------------|
| `happy_path_unlock` | verify_identity must succeed BEFORE unlock_account; both in correct order |
| `wrong_last4_no_unlock` | After 2 failed verifications, agent opens a ticket instead of retrying |
| `vpn_status_no_verification_needed` | Read-only tool runs without identity check |
| `happy_path_password_reset` | Caller's stated intent (reset, not unlock) is honored |
| `reject_attempt_to_act_on_other_user` | Verified caller can't trigger privileged tool on someone else's `employee_id` |

## Project layout

```
src/
├── cli.py                       # init-db / serve / eval / show-audit / show-user
├── config.py                    # typed Settings from .env
├── audit.py                     # JSONL audit log + AuditTimer
├── session.py                   # per-call Session with verified_for tracking
├── db/
│   ├── bootstrap.py             # SQLite warehouse builder (users, vpn_status, tickets)
│   └── store.py                 # all DB operations behind a clean Python API
├── tools/
│   ├── catalog.py               # 6-tool registry + Realtime/ChatCompletions specs + dispatcher
│   └── handlers.py              # tool implementations
├── server/
│   ├── prompts.py               # system + eval primer
│   ├── realtime_bridge.py       # WebSocket bridge to OpenAI Realtime API
│   ├── app.py                   # FastAPI app
│   └── static/index.html        # browser UI (mic capture + playback + transcript)
└── eval/
    ├── golden.py                # 5 scenarios with per-turn and end-state assertions
    └── runner.py                # ChatCompletions-mode replay + scoring
```

## Production design choices

1. **LLM never authors privileged action without server-side gate.** The agent prompt says "verify first", but the dispatcher refuses regardless of what the LLM tries. This is the only correct posture for a production system.
2. **Bearer-style session over per-call auth.** A real deployment would issue a short-lived session token at call setup (Twilio/Telnyx), bind it to the `Session`, and forward it on every tool dispatch. The structure is here.
3. **Text-mode eval for CI, audio for demo.** The 2026 production guides agree: full WebRTC test harnesses are slow and flaky for assertion-based testing. Text replays catch the security regressions; the live audio path is its own UAT.
4. **Audit redaction is centralized.** `_REDACT_KEYS` in `audit.py` is the only allow-list — any new sensitive arg gets redacted automatically.
5. **No real PHI / PII used.** Per the production guides we surveyed, OpenAI Realtime is **not yet HIPAA-eligible** under the BAA (May 2026). Synthetic users only.
6. **Telemetry off by default.** No Realtime tracing or telemetry without explicit opt-in.

## Inspiration (motivation only — no code copied)

- [`livekit/agents`](https://github.com/livekit/agents) — the canonical real-time agent framework
- [Pipecat](https://github.com/pipecat-ai/pipecat) — alternative orchestration model
- ServiceNow's [AI Voice Agents announcement](https://www.servicenow.com/community/now-assist-articles/ai-voice-agents-are-here-autonomous-service-that-delights/ta-p/3448126) — the enterprise reference
- The Forasoft [Realtime API production guide (2026)](https://www.forasoft.com/blog/article/openai-realtime-api-voice-agent-production-guide-2026) — informed our architecture choices
- Hamming.ai's [LiveKit voice testing guide](https://hamming.ai/blog/testing-livekit-voice-agents-complete-guide) — informed the text-vs-WebRTC eval split

## Status

- [x] FastAPI server with WebSocket bridge to OpenAI Realtime API
- [x] Browser UI with AudioWorklet mic capture + 24kHz PCM playback
- [x] 6 tools: lookup_user, verify_identity, check_vpn_status, unlock_account, reset_password, create_incident_ticket
- [x] Server-side identity gate enforced in the dispatcher (LLM cannot bypass)
- [x] Cross-user privilege defense
- [x] Two-strike escalation rule (failed verify x2 → ticket)
- [x] Append-only JSONL audit log
- [x] Text-mode eval (5 scenarios) — 5/5 pass
- [x] Live HTTP smoke test (server boots, UI loads, /health 200)
- [ ] LiveKit transport adapter (for telephony scale)
- [ ] OpenTelemetry traces with TTFT histogram
- [ ] Real ServiceNow ticket-create adapter
- [ ] OAuth-based session token (replace bearer-passed-as-arg pattern)
