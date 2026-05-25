# Eval results

## Latest run: `mcp_eval.json`

### Configuration

- **MCP server**: FastMCP 3.3.1
- **Database**: DuckDB 1.5 (read-only, 50 k taxi trips + 5 k passengers, synthetic)
- **SQL parser**: sqlglot 30 (read-only enforcement, multi-statement detection, LIMIT injection)
- **LLM (NL->SQL only)**: `gpt-4o-mini` with OpenAI structured output (Pydantic)

### Headline results

| Metric | Score |
|--------|------:|
| n_cases | 13 |
| n_passed | **13** |
| n_failed | 0 |

### What's tested

| # | Case | What it proves |
|---|------|----------------|
| 1 | `auth_missing_token` | Missing bearer → DENIED + audited |
| 2 | `auth_invalid_token` | Unknown bearer → DENIED + audited |
| 3 | `authz_missing_query_nl_scope` | Valid token but no `query:nl` → DENIED |
| 4 | `sql_gate_drop` | `DROP TABLE` rejected with explicit reason |
| 5 | `sql_gate_insert` | `INSERT` rejected |
| 6 | `sql_gate_multi_statement` | `;` chaining rejected (no SQL injection) |
| 7 | `row_limit_injected` | `SELECT *` gets a LIMIT injected automatically |
| 8 | `select_count_no_limit_needed` | Aggregates pass through unchanged |
| 9 | `select_join_aggregate` | Real GROUP BY + aggregate runs in DuckDB |
| 10 | `pii_masked_for_public_principal` | Email / name / phone all masked when caller has only `read:public` |
| 11 | `pii_unmasked_for_privileged_principal` | Same query as #10 returns real data when caller adds `read:pii` |
| 12 | `describe_table_marks_pii` | `is_pii` flag in column descriptions |
| 13 | `nl_query_happy_path` | LLM-generated SQL passes the same gate, executes, returns rows |

### Audit log (post-eval)

Every event was correctly classified:

```
DENIED   list_tables                     by <none>              missing bearer token
DENIED   list_tables                     by <none>              invalid token
DENIED   natural_language_query          by tok_analyst_public  missing required scope(s): query:nl
DENIED   run_select_sql                  by tok_analyst_public  sql policy: statement type Drop is not allowed
DENIED   run_select_sql                  by tok_analyst_public  sql policy: statement type Insert is not allowed
DENIED   run_select_sql                  by tok_analyst_public  sql policy: multiple statements not allowed
OK       run_select_sql                  by tok_analyst_public
OK       run_select_sql                  by tok_analyst_public
OK       run_select_sql                  by tok_analyst_public
OK       sample_rows                     by tok_analyst_public
OK       sample_rows                     by tok_analyst_pii
OK       describe_table                  by tok_analyst_public
OK       natural_language_query          by tok_data_scientist
```

This is the trace shape SOC 2 / SOX auditors look for: principal_id on every record, structured outcome, no secret values logged.

### HTTP transport smoke test

Started the server with `python -m src.cli serve --transport http` and called it with the FastMCP client:

```
Tools advertised by http://127.0.0.1:7878/mcp:
  - list_tables
  - describe_table
  - sample_rows
  - run_select_sql
  - natural_language_query

list_tables result:
  tables=[
    {table_name: 'passengers',  n_columns: 5},
    {table_name: 'taxi_trips',  n_columns: 14},
  ]
```

Streamable-HTTP (MCP protocol v2025-11-25), session negotiation, bearer-passed-as-arg, structured Pydantic response — all working.

## Findings

### 1. The SQL gate is the most important enforcement point

Even though DuckDB is opened `read_only=True` (engine-level enforcer), the application-layer parser is what gives **clear, auditable, fast-fail rejections**. The engine would also refuse `DROP TABLE`, but with a generic permission error. The gate refuses with `"statement type Drop is not allowed"` and logs it with the principal who tried — that's what the auditor needs.

### 2. Multi-statement detection blocks classic SQL injection

A simple `;` between statements is the oldest LLM-prompt-injection vector. Rejected at parse time, audited, denied. No engine round-trip.

### 3. PII masking is a per-tool decision

Two principals run the *exact same SELECT* and get different bytes back, transparently. The masking is column-level (matches Snowflake / Unity Catalog tag semantics), so adding a new tagged column is a one-line change in `policy.py`.

### 4. NL query reuses the same gate

The natural-language tool has the LLM emit SQL but routes it through the **same** `enforce_read_only` parser before execution. So any prompt-injection that tries to trick the LLM into emitting `DROP` is caught at the gate, not by the LLM.

### 5. Per-token rate limits are local but the interface is right

The in-memory rate limiter survives a single process. Production replicas would back this with Redis, but the call-sites (`rl.check_and_record(token_id, limit)`) don't change.

## Reproduce

```sh
python -m src.cli init-db
python -m src.cli init-tokens
python -m src.cli eval

# HTTP smoke
python -m src.cli serve --transport http   # in another terminal
python -m src.cli smoke-http --bearer <paste from auth/tokens.json>
```

Cost: ~$0.005 OpenAI for the one NL-query case in the eval (pure SQL roundtrip otherwise).
