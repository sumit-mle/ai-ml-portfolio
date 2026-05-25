# 06 — Enterprise Data Platform MCP Server

A production-grade **Model Context Protocol** server that exposes your analytics warehouse to Claude / Cursor / Codex with the controls that separate a demo from a real enterprise system: token auth, scoped authorization, read-only SQL enforcement, PII masking, per-token rate limits, and a SOC-2-style audit log.

5 tools out of the box:

| Tool | Required scope(s) | What it does |
|------|-------------------|--------------|
| `list_tables` | `read:public` | List tables and column counts |
| `describe_table` | `read:public` | Schema + row count + per-column `is_pii` flag |
| `sample_rows` | `read:public` | Random rows; PII masked unless caller has `read:pii` |
| `run_select_sql` | `read:public` | Read-only SELECT with parser gate + LIMIT injection + PII masking |
| `natural_language_query` | `read:public` + `query:nl` | LLM translates question to SQL, then runs through the same gate |

## The business problem

Every Fortune 500 building agent platforms is asking: **"How do we let Claude / Codex / our internal copilot query the warehouse without giving them a Snowflake password and hoping?"**

The answer is an MCP server, but the demo MCP servers floating around GitHub miss the points that make it deployable:

- They forget auth (or do it wrong)
- They expose `execute_sql` with no parser gate (any LLM can be tricked into `DROP TABLE`)
- They have no concept of column-level data classification
- They have no audit log — so no SOC 2 sign-off, ever

This server has all four. Drop in a real OAuth verifier (Auth0 / Okta / Descope / Scalekit) and a Snowflake adapter and the same code works against your production warehouse.

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| MCP framework | **FastMCP 3.3** | Native streamable-HTTP and stdio transports; clean tool decorators with Pydantic schemas |
| Database | **DuckDB 1.5** | Real SQL semantics that map 1:1 to Snowflake/BigQuery/Postgres; opens `read_only=True` for engine-level safety; zero infra |
| SQL parser/gate | **sqlglot 30** | Multi-dialect AST parsing; lets us swap DuckDB for Snowflake without changing the gate |
| Auth | Static-token JSON file (demo) | Interface matches OAuth — production deployments swap in `TokenStore` with a JWT verifier |
| Audit log | JSONL append-only | Every line is one event with principal, tool, args, outcome, duration_ms |
| LLM (NL→SQL) | OpenAI `gpt-4o-mini` + structured output | Pydantic schema; never parses prose |
| Resilience | tenacity in tool clients | Network is a fact of life |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       MCP CLIENT (Claude / Cursor / Codex)              │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ MCP (stdio | streamable-HTTP)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      FastMCP 3.3 SERVER                                  │
│                                                                          │
│  Each tool call passes through:                                          │
│                                                                          │
│   ┌─────────────┐  ┌──────────────┐  ┌──────────────┐                    │
│   │  authn      │─▶│ authz        │─▶│ rate-limit   │─▶ tool body        │
│   │ (TokenStore)│  │ (scopes)     │  │ (per-token)  │                    │
│   └─────────────┘  └──────────────┘  └──────────────┘                    │
│           │                │                 │                           │
│           └────────────────┴─────────────────┴──┐                        │
│                                                 ▼                        │
│                                         JSONL audit log                  │
│                                  (every call, ok/denied/error)           │
│                                                                          │
│  In tool body:                                                           │
│    ┌──────────────────────┐    ┌──────────────────────┐                  │
│    │ SQL gate (sqlglot)   │───▶│ DuckDB (read-only)   │                  │
│    │ - reject DDL/DML     │    │ engine-level enforcer │                  │
│    │ - reject ; chains    │    │                      │                  │
│    │ - inject LIMIT       │    └──────────┬───────────┘                  │
│    └──────────────────────┘               │                              │
│                                            ▼                              │
│                                  ┌────────────────────┐                  │
│                                  │ PII policy engine   │                  │
│                                  │ mask cols unless    │                  │
│                                  │ caller has read:pii │                  │
│                                  └─────────┬──────────┘                  │
│                                            │                              │
│                                            ▼                              │
│                                  Pydantic response model                 │
└─────────────────────────────────────────────────────────────────────────┘
```

## Quick start

```sh
# 1. Configure
copy .env.example .env
# Edit .env: set OPENAI_API_KEY (only needed by natural_language_query)
#            set SEC_USER_AGENT (any "name email" pair)

# 2. Build the demo warehouse (DuckDB, ~50k rows, synthetic)
python -m src.cli init-db

# 3. Generate three demo bearer tokens with different scopes
python -m src.cli init-tokens

# 4. Run the in-process eval (13 cases)
python -m src.cli eval

# 5. Start the server (stdio for Claude Desktop config; http for live agents)
python -m src.cli serve --transport http
# now hit http://127.0.0.1:7878/mcp from any MCP client

# 6. Smoke test from another shell
python -m src.cli smoke-http --bearer <paste a token from auth/tokens.json>

# 7. Inspect the audit log
python -m src.cli show-audit --n 20
```

### Wiring into Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "enterprise-data-platform": {
      "command": "<absolute path to .venv/bin/python>",
      "args": ["-m", "src.cli", "serve", "--transport", "stdio"],
      "cwd": "<absolute path to this folder>",
      "env": {
        "OPENAI_API_KEY": "...",
        "AUTH_TOKEN_FILE": "...",
        "DUCKDB_PATH": "..."
      }
    }
  }
}
```

The bearer is passed by the LLM in the tool argument — you'd configure your assistant with a system prompt like "always pass the bearer `<token>` to every tool call". For HTTP deployments, the standard pattern is bearer in the `Authorization` header, parsed by FastMCP middleware (one-line swap from the in-arg approach).

## Verified results

13/13 eval cases pass. See [`results/README.md`](./results/README.md) for the breakdown.

| Category | Tests | Pass |
|----------|------:|-----:|
| Authentication | 2 | 2 |
| Authorization (scopes) | 1 | 1 |
| SQL gate | 3 | 3 |
| Row-limit injection | 3 | 3 |
| PII masking | 3 | 3 |
| NL→SQL roundtrip | 1 | 1 |

HTTP transport verified end-to-end: streamable-HTTP with MCP protocol v2025-11-25, session negotiation, structured Pydantic responses.

## Project layout

```
src/
├── cli.py                          # init-db / init-tokens / serve / eval / smoke-http / show-audit
├── config.py                       # typed Settings from .env
├── db/
│   ├── bootstrap.py                # DuckDB warehouse builder (taxi_trips, passengers)
│   ├── driver.py                   # read-only connection singleton
│   └── catalog.py                  # list_tables, describe_table, sample_rows
├── security/
│   ├── auth.py                     # TokenStore + Principal + scope check
│   ├── audit.py                    # AuditLog (JSONL) + AuditTimer context manager
│   ├── rate_limit.py               # sliding 1-hour token-bucket per principal
│   ├── sql_gate.py                 # sqlglot read-only enforcement + LIMIT injection
│   └── pii.py                      # column-level masking policy
├── server/
│   ├── app.py                      # FastMCP server registration
│   └── tools.py                    # 5 tool implementations + Pydantic response models
└── eval/
    ├── golden.py                   # 13 cases covering every control
    └── runner.py                   # in-process eval runner with rubric
```

## Production design choices

1. **Two-layer SQL safety.** Application-layer gate (sqlglot) gives clear errors and audit trail; engine-layer (`read_only=True`) is the belt to the application's braces.
2. **Auth as an interface, not an implementation.** `TokenStore.authenticate(bearer) -> Principal` matches what an OAuth verifier returns. Swapping in a JWT verifier is one file.
3. **PII as a column-tag policy.** Same model as Snowflake / Unity Catalog. Adding a new tagged column doesn't touch the tools.
4. **Audit is structured, not free-form.** JSONL means it's queryable in any log platform (Splunk, Datadog, Loki). Every event has `outcome ∈ {ok, denied, error}`.
5. **Bearer in tool args, not session state.** Keeps the demo runnable across both stdio and HTTP without writing transport-specific middleware. Production HTTP swaps this out for `Authorization` header parsing.
6. **NL→SQL routes through the same gate.** The LLM never gets to bypass the parser. A prompt-injection that produces `DROP TABLE` is caught at sqlglot, not by trusting the model.
7. **Rate limit per token.** Even a perfectly-authenticated agent can be rate-limited; useful for cost control and DoS protection.
8. **Telemetry off by default.** FastMCP can phone home for traces; we leave that opt-in.

## Inspiration (motivation only — no code copied)

- [`Snowflake-Labs/mcp`](https://github.com/Snowflake-Labs/mcp) — the canonical Snowflake reference
- [`bytebase/dbhub`](https://github.com/bytebase/dbhub) — multi-DB universal MCP
- [`PrefectHQ/fastmcp`](https://github.com/PrefectHQ/fastmcp) — the framework underneath
- [Render's "Enterprise-ready MCP in minutes with Descope" post](https://render.com/blog/enterprise-ready-mcp-in-minutes-with-descope-auth-on-render) — production OAuth pattern
- Real internal data platform tools at FAANG-tier companies

## Status

- [x] FastMCP 3.x server with 5 tools, stdio + streamable-HTTP transports
- [x] Token-based auth with per-scope authorization
- [x] sqlglot read-only SQL gate (DDL/DML/multi-statement rejection)
- [x] Automatic LIMIT injection
- [x] Column-level PII masking with `read:pii` scope override
- [x] Per-token sliding-window rate limit
- [x] JSONL audit log with principal_id on every event
- [x] Natural-language SQL via OpenAI structured output (gated like any other SELECT)
- [x] In-process eval (13 cases) and live HTTP smoke test
- [x] DuckDB warehouse bootstrap with synthetic NYC TLC + passengers schemas
- [ ] Snowflake adapter (drop-in for `db/driver.py`)
- [ ] OAuth 2.1 / OIDC integration (drop-in for `TokenStore`)
- [ ] Redis-backed rate limiter for multi-replica deployments
- [ ] Per-tool cost ceilings (max bytes scanned)
- [ ] OpenTelemetry tracing
