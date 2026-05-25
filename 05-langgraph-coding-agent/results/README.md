# Eval results

## Latest run: `modernize_eval.json`

### Configuration

- **Framework**: LangGraph 0.2 state machine
- **Code transformer**: libcst 1.8 (preserves comments + formatting)
- **Validator**: pytest 9 in subprocess
- **LLM (planner only)**: `gpt-4o-mini` with Pydantic structured output

### Headline results across 3 scenarios

| Metric | Score |
|--------|------:|
| n_scenarios | 3 |
| n_changes_correct | **3 / 3** |
| n_tests_preserved | **3 / 3** |
| n_overall_pass | **3 / 3** |
| avg duration / scenario | **7.7 s** |

### Scenarios

| Name | Files | What it tests | Result |
|------|------:|---------------|:------:|
| basic_modernization | 2 | Every safe recipe applies cleanly; tests still pass | PASS |
| format_spec_bailout | 2 | Agent does NOT touch `{:.2f}` / `%.1f%%` strings | PASS |
| missing_tests | 1 | Agent runs without tests (no false-fail) | PASS |

## Live demo results

Real run on `samples/legacy_project/billing.py` (a 50-line legacy billing module with 17 detected modernization candidates):

| Metric | Value |
|--------|------:|
| Findings | 17 |
| Plans | 17 (all auto-approved low-risk) |
| Applied | 3 (one per file/recipe) |
| Tests before | 8/8 PASS |
| Tests after | 8/8 PASS |
| Rollback triggered | no |
| Duration | 39 s |

### What got rewritten

```diff
-from typing import Dict, List, Optional, Tuple
+from typing import Dict, List, Optional, Tuple   # imports left intact (separate cleanup pass)

-def format_invoice_line(item: Dict[str, str], qty: int, price: float) -> str:
+def format_invoice_line(item: dict[str, str], qty: int, price: float) -> str:

-def aggregate(items: List[Dict[str, float]]) -> Dict[str, float]:
-    out: Dict[str, float] = {}
+def aggregate(items: list[dict[str, float]]) -> dict[str, float]:
+    out: dict[str, float] = {}

-def find_first_overdue(invoices: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
+def find_first_overdue(invoices: list[dict[str, str]]) -> dict[str, str] | None:

-def split_address(addr: str) -> Tuple[str, str, str]:
+def split_address(addr: str) -> tuple[str, str, str]:

-def render_receipt(customer: str, lines: List[Tuple[str, int, float]]) -> str:
-    out = ["Receipt for {}".format(customer)]
+def render_receipt(customer: str, lines: list[tuple[str, int, float]]) -> str:
+    out = [f"Receipt for {customer}"]
```

### What was correctly LEFT ALONE

These are the ones that demonstrate the agent's conservatism:

```python
"%s x %d = $%.2f" % (item["name"], qty, qty * price)   # has %.2f spec → bail
"  %-20s %3d @ %.2f" % (name, qty, price)              # width specs → bail
"Customer {} owes ${:.2f}".format(name, total)         # has :.2f spec → bail
"Total: ${:.2f}".format(total)                         # has :.2f spec → bail
```

The recipe's parser specifically refuses to rewrite format strings whose spec/conversion fields would change behavior. Better to defer to humans than to risk breaking financial output.

## Findings

### 1. The deterministic transformer is the right primitive

The LLM ranks risk and writes rationale. It does not author code. All actual rewrites go through libcst transformers we wrote and unit-tested. This is the difference between a demo and a production agent — a production system never has the LLM emit raw Python that goes straight to disk.

### 2. The `_Bail` pattern protects high-stakes strings

Inside the f-string transformer we explicitly bail out on:
- format specs (`{:.2f}`, `%-20s`, `%.3f`)
- conversions (`{!r}`, `{!s}`)
- arg-count mismatches

A failed bail is a no-op (recipe returns 0 changes). Combined with the test-validator gate, we get conservative-by-default behavior.

### 3. Working-copy + rollback is essential

The agent never edits the source tree. It mirrors into a workdir, applies patches there, runs tests there, and rolls the workdir back if tests regress. The original is untouched until a human commits the diff.

### 4. Per-file/recipe deduplication keeps the audit clean

The scanner emits one Finding per AST node, so a file with 5 `Dict[...]` types produces 5 findings of recipe `typing_to_pep585`. The transformer rewrites all of them in one pass, so we apply the recipe once per (file, recipe) pair. The audit shows the dedups explicitly.

## Reproduce

```sh
python -m src.cli eval                           # 3 scenarios, ~25 s total, ~$0.01 OpenAI
python -m src.cli scan --project samples/legacy_project   # no LLM cost
python -m src.cli plan --project samples/legacy_project   # ~$0.005 OpenAI for 17 findings
python -m src.cli modernize --project samples/legacy_project --workdir samples/_workdir/legacy_project --auto_yes
```
