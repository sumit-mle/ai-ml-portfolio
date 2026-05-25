# 10 — Marketing-Mix Analytics Agent (LangGraph + DuckDB)

A natural-language analytics agent for a CMO who keeps asking **"why did EMEA revenue drop last week?"** Built as a LangGraph state machine with schema retrieval, read-only SQL gating, and a self-correcting repair loop. **5/5 questions pass execution-correctness eval.**

## The business problem

Marketing-mix analysis is the analytics question every CFO and CMO asks weekly:

- Why did revenue dip in region X?
- Which channel paid back the budget cut?
- How does our spend in EMEA compare to AMER over the last 14 days?
- What products drove last week's revenue?

A skilled analyst takes 30 minutes to a few hours per question, depending on how complex the data model is. A LangGraph agent over the same warehouse turns that into **~5 seconds per question** with citation-quality SQL the analyst can review.

| Metric | Manual analyst | With this agent |
|--------|---------------:|----------------:|
| Time per CMO question | 30 min - 4 hours | **~5 seconds** |
| SQL audit trail | Variable | Every run: validated SQL + result + trace |
| Self-correction on failed query | Manual rewrite | Bounded repair loop (≤3 attempts) |
| Read-only safety guarantee | "Trust me" | Engine-level `read_only=True` + sqlglot policy gate |

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| **Orchestration** | LangGraph | Conditional edges (validate → execute or repair, execute → answer or repair) are native; the deterministic repair-loop budget is the production-grade pattern |
| **Database** | DuckDB | Real SQL semantics that map 1:1 to Snowflake/BigQuery/Postgres; opens `read_only=True` for engine-level safety; zero infra |
| **SQL gate** | sqlglot | Parses the AST, rejects DDL/DML/multi-statement, injects LIMIT |
| **LLM** | OpenAI `gpt-4o-mini` + Pydantic structured output | Cheap; structured output means zero string parsing |
| **Schema retrieval** | Keyword scoring over a hand-curated catalog | Tiny demo schema (6 tables); for 100s-of-tables swap in embedding similarity, the interface doesn't change |

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│ INPUT                                                                 │
│   "Why did EMEA revenue drop last week?"                              │
└──────────────────────────────┬────────────────────────────────────────┘
                               ▼
   ┌────────┐    ┌────────┐    ┌──────────┐    ┌─────────┐    ┌────────┐
   │  PLAN  │──▶ │ DRAFT  │──▶ │ VALIDATE │──▶ │ EXECUTE │──▶ │ ANSWER │
   │ schema │    │  SQL   │    │ sqlglot  │    │ DuckDB  │    │ NL out │
   │ retrieve│    │ (LLM)  │    │ read-only│    │  RO     │    │ (LLM)  │
   └────────┘    └────────┘    └────┬─────┘    └────┬────┘    └────────┘
                                    │  fail         │  fail
                                    ▼               ▼
                                 ┌─────────────────────┐
                                 │       REPAIR        │
                                 │  (LLM, bounded by   │
                                 │  MAX_REPAIR_ATTEMPTS)│
                                 └─────────────────────┘
                                            ▲           │
                                            │  loop     ▼
                                            └─── back to VALIDATE
                                            │
                                            ▼ budget exhausted
                                       ┌──────────┐
                                       │  GIVE_UP │
                                       └──────────┘
```

Every node appends a typed event to `state["trace"]`. After the run you can `cli show-trace <path>` and see the entire decision sequence.

## Schema

The synthetic warehouse models a B2B SaaS marketing-mix problem:

| Table | Purpose |
|-------|---------|
| `regions` | AMER / EMEA / APAC / LATAM |
| `products` | 4 products: Starter / Pro / Enterprise SaaS, Onboarding Service |
| `customers` | 5,000 customers w/ region, signup_date, plan |
| `campaigns` | 32 campaigns across 4 regions × 4 channels (search / social / display / video) |
| `ad_spend` | 1,800+ daily spend rows per campaign |
| `revenue_daily` | 1,400+ daily revenue rows per region/product |

The bootstrap **deliberately injects a 32% revenue dip in EMEA over the last 7 days**, AND drops EMEA campaign budget by 60% over the preceding 14 days. The agent's job: find the dip, attribute it to the cut, and explain in a sentence.

## Quick start

```sh
copy .env.example .env
# Edit .env: set OPENAI_API_KEY

# 1. Build the synthetic warehouse (1 second, $0)
python -m src.cli init-db

# 2. View the table catalog
python -m src.cli schema

# 3. Ask a question
python -m src.cli ask --question "Why did EMEA revenue drop last week?"

# 4. Same with the trace saved to disk
python -m src.cli ask --question "Why did EMEA revenue drop last week?" --save-trace --show-trace

# 5. Run the golden eval (5 questions, ~30s, ~$0.02)
python -m src.cli eval

# 6. Replay a saved trace
python -m src.cli show-trace output/traces/trace_<id>.json
```

## Verified results

5/5 golden questions pass execution-correctness eval. See [`results/README.md`](./results/README.md) for the full breakdown.

| QID | Question | Result |
|-----|----------|--------|
| q1 | Total gross revenue across all regions in the last 7 days? | PASS |
| q2 | EMEA revenue: last 7 days vs prior 7 days, % change? | PASS |
| q3 | Highest-spend marketing channel in EMEA over the last 14 days? | PASS (1 repair) |
| q4 | Gross revenue per region on 2026-05-24? | PASS |
| q5 | EMEA customers on the 'pro' plan? | PASS |

The headline CMO question — **"Why did EMEA revenue drop last week?"** — produces real quantified narrative analysis end-to-end (see results/README.md for the actual answer).

## Project layout

```
src/
├── cli.py                          # init-db / schema / ask / eval / show-trace
├── config.py                       # typed Settings from .env
├── db/
│   ├── bootstrap.py                # synthetic ETL (regions/products/customers/campaigns/ad_spend/revenue)
│   ├── driver.py                   # read-only DuckDB singleton
│   └── schema.py                   # table catalog + retrieval
├── safety/
│   └── sql_gate.py                 # sqlglot read-only policy (same pattern as project 06)
├── agent/
│   ├── graph.py                    # LangGraph wiring (plan → draft → validate → execute → answer)
│   ├── nodes.py                    # node implementations + LLM calls
│   ├── prompts.py                  # drafter / repair / answer system prompts
│   └── state.py                    # typed AgentState
└── eval/
    ├── golden.py                   # 5 marketing-mix questions + reference SQL
    └── runner.py                   # BIRD-style execution-correctness scoring
```

## Production design choices

1. **Two layers of read-only enforcement.** Engine-level `read_only=True` is the floor; sqlglot's policy gate is the application-level enforcer that gives clear, fast-fail errors the repair node can react to.
2. **The repair loop is bounded.** `MAX_REPAIR_ATTEMPTS=3` in `.env`. The graph routes to `give_up` after the budget is exhausted rather than spinning forever — the only correct production posture.
3. **Schema retrieval, not full schema dumps.** Even for 6 tables we score per question and pick the relevant ones. Larger warehouses (100+ tables) need this; we keep the API the same so swapping in vector retrieval later is a one-file change.
4. **Pydantic structured output for SQL.** The drafter and repair nodes both use OpenAI's `beta.chat.completions.parse` with a `_SqlOut` schema. No regex, no string surgery on LLM prose.
5. **Date semantics in the prompt.** The first eval run scored 3/5 because of inclusive/exclusive boundary ambiguity; adding explicit examples ("'last 7 days' means `date >= 'YYYY-MM-DD'`...") jumped it to 5/5. This is exactly the kind of finding execution-correctness eval makes visible.
6. **Every node trace is structured JSON.** No print-debugging in the agent — every transition appends a typed event with a timestamp. The `show-trace` CLI is the audit-replay tool a real ops team would build.
7. **BIRD-style evaluation.** Each golden question has a reference SQL; we run both and compare result rows numerically with tolerance. This is the canonical text-to-SQL eval pattern from BIRD/Spider, adapted for an agentic loop.

## Inspiration (motivation only — no code copied)

- [LangGraph SQL agent docs](https://docs.langchain.com/oss/python/langgraph/sql-agent) — the canonical reference shape
- [`mlnotes.substack` "Building a Production-Ready SQL Agent"](https://mlnotes.substack.com/p/building-a-production-ready-sql-agent) — informed the route → schema → draft → validate → execute → repair → answer pipeline
- [BIRD benchmark](https://bird-bench.github.io/) — the execution-correctness eval pattern
- [MotherDuck's BIRD-Bench post](https://motherduck.com/blog/bird-bench-and-data-models/) — informed the schema-as-semantic-layer thinking

## Status

- [x] LangGraph state machine: plan → draft → validate → execute → answer (with repair loop)
- [x] DuckDB warehouse with deterministic synthetic ETL (5,000 customers, 32 campaigns, ~1,800 spend rows, ~1,400 revenue rows)
- [x] Read-only SQL gate (sqlglot, two-layer enforcement)
- [x] Pydantic-structured LLM SQL output (drafter + repair)
- [x] Bounded repair loop (`MAX_REPAIR_ATTEMPTS=3`)
- [x] BIRD-style execution-correctness eval (5/5 pass)
- [x] Per-node typed trace events with `cli show-trace` replay
- [x] Natural-language answer synthesis from result rows
- [ ] LangSmith / Langfuse trace upload
- [ ] Streamlit UI showing the trace step-by-step
- [ ] Larger schema with embedding-based retrieval for 100+ tables
- [ ] Memory layer for follow-up questions ("...and what about APAC?")
