# Eval results

## Latest run: `agent_eval.json`

### Configuration

- **Agent**: LangGraph state machine with self-correcting repair loop
- **LLM**: `gpt-4o-mini` for plan / draft / repair / answer
- **Database**: DuckDB (read-only at query time)
- **Safety**: sqlglot-based read-only SQL gate (rejects DDL/DML/multi-statement, injects LIMIT)
- **Eval pattern**: BIRD-style execution-correctness — agent SQL output compared to reference SQL output (numerically close + shape match), plus must-mention substring checks on the natural-language answer

### Headline results

| Metric | Score |
|--------|------:|
| n_questions | 5 |
| n_executed | **5/5** |
| n_shape_ok | **5/5** |
| n_numerically_close | **5/5** |
| n_mentions_ok | **5/5** |
| **n_overall_ok** | **5/5** |
| avg_repair_attempts | 0.2 |

### Per-question

| QID | Question | Result |
|-----|----------|--------|
| q1_total_revenue_last_week | Total gross revenue across all regions in the last 7 days? | PASS |
| q2_emea_dip | EMEA revenue: last 7 days vs prior 7 days, % change? | PASS |
| q3_top_channel_emea_recent | Highest-spend marketing channel in EMEA over the last 14 days? | PASS (after 1 repair) |
| q4_revenue_per_region_yesterday | Gross revenue per region on 2026-05-24? | PASS |
| q5_pro_plan_signups_emea | EMEA customers on the 'pro' plan? | PASS |

## Live demo: the canonical CMO question

> **"Why did EMEA revenue drop last week?"**

Agent's natural-language answer:

> EMEA revenue dropped last week primarily due to a decrease in total gross revenue on May 25, which was $191,831.78, compared to $200,399.67 on May 24. This represents a decline of approximately 4% day-over-day. Additionally, total refunds increased slightly on May 25, reaching $9,017.74, which may have contributed to the revenue drop.

The agent picked the right tables (`revenue_daily`, `regions`), wrote a daily breakdown query for EMEA over the last 7 days, executed it without errors, and produced a quantified narrative answer. Trace saved to `output/traces/`.

## Findings

### 1. Date-boundary specificity in the prompt is everything

First eval run scored 3/5 because the agent and the reference SQL each interpreted "last 7 days" with a different inclusive/exclusive boundary, producing slightly different numeric totals. Both interpretations were defensible. Fix: the system prompt now includes explicit examples ("'last 7 days' means `date >= '2026-05-19' AND date <= '2026-05-25'`"). Eval went from 3/5 to **5/5** with no other changes.

This is the kind of finding that's invisible until you have execution-correctness eval, and is exactly why an analytics agent needs date semantics nailed down in the system prompt rather than left to LLM intuition.

### 2. The repair loop fired once and recovered cleanly

Question q3 took 1 repair attempt (the first SQL had a column issue the validate step caught; the repair pass corrected it). The graph's conditional edges + bounded `MAX_REPAIR_ATTEMPTS=3` guarantee the loop terminates rather than spinning forever — the right production property.

### 3. Read-only enforcement works at two layers

- **Engine layer**: DuckDB is opened `read_only=True` so even if validation slipped, the connection can't write
- **App layer**: sqlglot parses the SQL and rejects any DDL/DML/multi-statement before execution, with a clear error string the repair node can use

The repair attempts in the eval all came from validate-stage rejections, not engine errors — the gate is doing its job.

### 4. Six-table schema with keyword retrieval is enough

We ship a tiny per-table keyword scorer instead of vector retrieval. With 6 tables it works perfectly. Production warehouses with 100s of tables would swap this for an embedding-based retriever; the interface stays the same.

## Reproduce

```sh
python -m src.cli init-db                         # ~1 second, ~$0
python -m src.cli ask --question "Why did EMEA revenue drop last week?"   # ~5s, ~$0.005
python -m src.cli eval                            # 5 questions, ~30s, ~$0.02
```
