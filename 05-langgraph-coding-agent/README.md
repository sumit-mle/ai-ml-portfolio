# 05 — Legacy Modernization Agent (LangGraph + libcst)

A LangGraph state machine that scans a Python codebase for outdated idioms, ranks each finding for risk with an LLM, applies safe rewrites in a working copy via libcst transformers, validates with `pytest`, and produces an audit report. **Never edits source until tests pass.**

## The business problem

Banks and insurers spend billions modernizing legacy code. The dollar-volume case is COBOL→Java, but every Fortune 500 has a similar story for **Python 3.6/3.7 → 3.13** modernization: deprecated typing imports, `.format()` strings, untyped APIs, file-handle leaks. Today this is one engineer at a time, manually, with no audit trail.

This agent compresses the loop:

| Step | Manual | With agent |
|------|--------|------------|
| Scan a 50k LoC project for candidates | 2 days | **2 minutes** |
| Risk-rate each candidate | several hours of judgment | LLM with code context |
| Apply the rewrite | one file at a time | deterministic libcst transform |
| Verify behavior preserved | run tests, eyeball | `pytest` in subprocess |
| Audit trail for code review | engineer writes it | structured JSON + Markdown |

The key safety property: **the LLM never authors code**. It rates risk and writes rationale. Every actual rewrite is a libcst transformer we wrote and unit-tested.

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| State machine | **LangGraph 0.2** | Conditional edges (test pass → report; test fail → rollback → report) are native. |
| Static analysis | **`ast` (stdlib)** | Standard, fast, perfect for scanning patterns. |
| Source rewrites | **libcst 1.8** | Preserves comments + formatting. Diffs are reviewable. |
| Behavior validation | **pytest in subprocess** | Real test runner, no in-process risk; 120s timeout. |
| LLM | OpenAI `gpt-4o-mini` (planner only) | Risk rating + rationale; never authors code. |
| Output schemas | Pydantic v2 | Audit reports, plans, patches — all typed. |
| HITL | inline `input()` prompt + `auto_yes` flag | CLI-friendly; no separate UI required for the demo. |

## Architecture

```
                ┌──────────────────────────────────────────┐
                │  source project (read-only after init)   │
                └─────────────────────┬────────────────────┘
                                      │ mirror to workdir
                                      ▼
   ┌──────────────────────────────────────────────────────────────┐
   │                  LangGraph state machine                      │
   │                                                               │
   │   scan ──▶ test_before ──▶ plan ──▶ gate ──▶ apply ──▶ test_after
   │                                       │                      │
   │                                  HITL gate          ┌────────┴────────┐
   │                                  + risk-based       │ pass        fail │
   │                                  auto-approve       ▼                  ▼
   │                                                    report        rollback
   │                                                                       │
   │                                                                       ▼
   │                                                                    report
   └──────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
              audit_<project>.json + audit_<project>.md
```

### Nodes

| Node | Tool | What it produces |
|------|------|------------------|
| `scan` | `ast` walker | `list[Finding]` |
| `test_before` | `pytest` subprocess | `TestRun` (baseline) |
| `plan` | OpenAI structured output | `list[PlannedChange]` with risk + reason |
| `gate` | risk threshold + HITL prompt | `list[PlannedChange]` (approval status set) |
| `apply` | libcst transformers | `list[PatchResult]` (with diffs) |
| `test_after` | `pytest` subprocess on workdir | `TestRun` |
| `rollback` | shutil — restore workdir from source | only if tests regress + rollback enabled |
| `report` | Pydantic → JSON + Markdown | `AuditReport` written to `output/` |

### Recipes (deterministic libcst transformers)

| ID | What | Risk |
|----|------|:---:|
| `format_to_fstring` | `"%s" % x` and `"{}".format(x)` → `f"{x}"` (refuses if spec/conversion present) | low |
| `typing_to_pep585` | `List[X]` → `list[X]`, `Dict`, `Tuple`, `Set`, `FrozenSet`, `Type` | low |
| `typing_optional_to_pep604` | `Optional[X]` → `X \| None` | low |
| `open_to_with_open` | `f = open(...); ...; f.close()` → `with open(...) as f: ...` | high |

Adding a recipe = (1) add to enum in `models.py`, (2) implement scanner pattern in `scanner.py`, (3) implement transformer in `transforms/recipes.py`.

## Quick start

```sh
copy .env.example .env
# Edit .env: set OPENAI_API_KEY (only the planner needs it)

# 1. Scanner only — no LLM cost
python -m src.cli scan --project samples/legacy_project

# 2. Scanner + planner — ~$0.005, no writes
python -m src.cli plan --project samples/legacy_project

# 3. Full pipeline — auto-approve everything safe
python -m src.cli modernize \
    --project samples/legacy_project \
    --workdir samples/_workdir/legacy_project \
    --auto_yes

# 4. Interactive HITL mode
python -m src.cli modernize \
    --project samples/legacy_project \
    --workdir samples/_workdir/legacy_project \
    --interactive

# 5. Run the eval scenarios
python -m src.cli eval

# 6. Print a saved audit
python -m src.cli show output/audit_legacy_project.json
```

## Verified results

### On the bundled sample (`samples/legacy_project/billing.py`)

| Metric | Value |
|--------|------:|
| Findings | 17 |
| Plans | 17 (all auto-approved low-risk) |
| Applied | 3 (deduped per file/recipe) |
| Tests before | **8/8 PASS** |
| Tests after | **8/8 PASS** |
| Rollback triggered | no |
| Duration | 39 s |

The transformer correctly refused to rewrite 4 format strings with `{:.2f}` / `%.2f` specs — exactly the conservatism a production system needs. See [`results/README.md`](./results/README.md) for the diff.

### Eval scenarios (3/3 pass)

| Scenario | What it tests | Result |
|----------|--------------|:------:|
| basic_modernization | Every safe recipe applies | PASS |
| format_spec_bailout | Refuse format-spec strings | PASS |
| missing_tests | Run cleanly without tests | PASS |

## Project layout

```
src/
├── cli.py                          # scan / plan / modernize / show / eval
├── config.py                       # Settings + risk-threshold logic
├── models.py                       # Finding / PlannedChange / PatchResult / AuditReport (Pydantic)
├── scanner.py                      # ast-based pattern detection
├── planner.py                      # OpenAI structured-output risk rater
├── transforms/
│   └── recipes.py                  # libcst transformers (one per RecipeId)
├── runner/
│   ├── patcher.py                  # workdir mirroring + apply + diff
│   └── tests.py                    # pytest subprocess + summary parsing
├── agent/
│   └── graph.py                    # LangGraph state machine
└── eval/
    ├── golden.py                   # 3 self-contained scenarios
    └── runner.py                   # eval harness + scoring

samples/
└── legacy_project/                 # 50-LoC billing module + tests for the demo
```

## Production design choices

1. **LLM never authors code.** Risk + rationale only. All rewrites are deterministic libcst transformers we own.
2. **Working copy + rollback.** Original source is read-only. The agent mirrors into a workdir, applies patches, runs tests there. Original stays clean until human commits.
3. **`_Bail` pattern in transformers.** Each recipe explicitly bails on cases it can't safely handle (format specs, multi-arg mismatches, etc.). A bail is a no-op, never an incorrect rewrite.
4. **HITL gate based on risk threshold.** Configurable via `AUTO_APPROVE_BELOW`. Low-risk auto-applies; medium / high require explicit approval (or `--auto_yes` for demo).
5. **Behavior validation via real `pytest`.** Subprocess with timeout. If the project had no tests we record `passed=True` with a "(no tests)" tail — the audit makes this visible.
6. **Per-file/recipe deduplication.** A file with 5 `Dict[...]` types produces 5 Findings but the transformer rewrites all of them in one pass. We apply each (file, recipe) once and explicitly mark the rest "already applied to this file" in the audit.
7. **Conditional rollback.** Only triggers when tests USED TO pass and now fail — prevents masking real test failures introduced earlier.

## Inspiration (motivation only — no code copied)

- [`Instagram/LibCST`](https://github.com/Instagram/LibCST) — the source-transformation library this builds on
- [`asottile/pyupgrade`](https://github.com/asottile/pyupgrade) — the canonical Python modernizer; we borrow some recipe ideas
- LangChain blog posts on stateful agents with HITL gates
- Real legacy-modernization workflows at banks and insurers

## Status

- [x] LangGraph state machine: scan → test_before → plan → gate → apply → test_after → (rollback) → report
- [x] AST-based scanner with 4 recipe patterns
- [x] LLM planner with Pydantic structured output (risk + rationale)
- [x] libcst transformers with `_Bail` pattern for unsafe cases
- [x] pytest subprocess validator with summary parsing
- [x] Working-copy + rollback safety model
- [x] Audit report (JSON + Markdown)
- [x] HITL gate (interactive `input()` + `--auto_yes`)
- [x] Eval scenarios with synthetic test cases
- [x] End-to-end verified on real legacy code: 8/8 tests pass before AND after
- [ ] More recipes: `print` statement (PY2), `xrange`, `dict.iteritems`, `super()` shorthand
- [ ] LangGraph checkpointing for resumable runs
- [ ] Tree-sitter for cross-language support (the original handoff hint)
- [ ] Streamlit UI showing the diff per patch
