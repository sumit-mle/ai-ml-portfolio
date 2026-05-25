"""Pydantic schemas — the contract between LangGraph nodes.

A Finding is something the scanner detected. A Plan attaches a transformation
recipe and a risk rating. A PatchResult records what was actually applied.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Risk = Literal["low", "medium", "high"]


# Modernization recipes the agent supports today. Each maps to a libcst
# transformer in src.transforms.recipes.
#
# Adding a new recipe = (1) add to this enum, (2) implement the transformer,
# (3) add a scanner pattern in src.scanner.
RecipeId = Literal[
    "format_to_fstring",         # %-format / .format() -> f-string
    "typing_to_pep585",          # List[int]/Dict[k,v] -> list[int]/dict[k,v]
    "typing_optional_to_pep604",  # Optional[X] -> X | None
    "open_to_with_open",         # raw open() w/o `with` -> with-statement (high risk: control flow)
]


class Finding(BaseModel):
    """One spot in the source the scanner thinks could be modernized."""
    file: str
    line: int                              # 1-indexed
    end_line: int                          # 1-indexed, inclusive
    recipe: RecipeId
    snippet: str = ""                      # short excerpt for human review
    rationale: str = ""                    # why this matches
    estimated_risk: Risk = "low"           # default; the planner can override


class PlannedChange(BaseModel):
    """LLM-rated, recipe-bound change ready to apply."""
    finding: Finding
    risk: Risk
    risk_reason: str = ""
    expected_diff_summary: str = Field(
        default="",
        description="One sentence describing what changes, e.g. "
                    "'Convert 3 %-format strings in module to f-strings'.",
    )
    auto_approved: bool = False
    human_approved: bool | None = None     # None = not yet asked; bool once gated


class PatchResult(BaseModel):
    """Outcome of applying one PlannedChange to the source tree."""
    plan: PlannedChange
    applied: bool
    files_changed: list[str] = Field(default_factory=list)
    error: str | None = None
    diff: str = ""                          # unified diff text for the audit log


class TestRun(BaseModel):
    passed: bool
    n_passed: int = 0
    n_failed: int = 0
    n_skipped: int = 0
    duration_s: float = 0.0
    output_tail: str = ""                   # last ~80 lines if failed


class AuditReport(BaseModel):
    """Final per-run summary that ships with the patched code."""
    project_path: str
    n_findings: int
    n_planned: int
    n_auto_approved: int
    n_human_approved: int
    n_human_rejected: int
    n_applied: int
    n_failed_to_apply: int
    test_run_before: TestRun | None = None
    test_run_after: TestRun | None = None
    rollback_triggered: bool = False
    duration_s: float = 0.0
    by_recipe: dict[str, int] = Field(default_factory=dict)
    patches: list[PatchResult] = Field(default_factory=list)

    def to_markdown(self) -> str:
        def yn(b: bool) -> str:
            return "PASS" if b else "FAIL"

        before = self.test_run_before
        after = self.test_run_after

        parts: list[str] = [
            f"# Modernization audit — `{self.project_path}`",
            "",
            "## Summary",
            "",
            f"- **Findings:** {self.n_findings}",
            f"- **Planned:** {self.n_planned} "
            f"(auto-approved {self.n_auto_approved}, human-approved {self.n_human_approved}, "
            f"rejected {self.n_human_rejected})",
            f"- **Applied:** {self.n_applied} of {self.n_planned} "
            f"(failed: {self.n_failed_to_apply})",
            f"- **Rollback triggered:** {'yes' if self.rollback_triggered else 'no'}",
            f"- **Duration:** {self.duration_s:.1f} s",
            "",
            "## Tests",
            "",
            "| run    | passed | failed | skipped | result |",
            "|--------|-------:|-------:|--------:|--------|",
            (
                f"| before | {before.n_passed} | {before.n_failed} | {before.n_skipped} | {yn(before.passed)} |"
                if before else "| before | n/a | n/a | n/a | n/a |"
            ),
            (
                f"| after  | {after.n_passed} | {after.n_failed} | {after.n_skipped} | {yn(after.passed)} |"
                if after else "| after  | n/a | n/a | n/a | n/a |"
            ),
            "",
            "## By recipe",
            "",
        ]
        if self.by_recipe:
            parts.append("| recipe | applied |")
            parts.append("|--------|--------:|")
            for k, v in sorted(self.by_recipe.items()):
                parts.append(f"| `{k}` | {v} |")
        else:
            parts.append("_(none)_")

        parts.append("\n## Patches\n")
        if self.patches:
            for p in self.patches:
                status = "applied" if p.applied else f"skipped ({p.error or 'no change'})"
                parts.append(
                    f"- `{p.plan.finding.recipe}` "
                    f"@ {p.plan.finding.file}:{p.plan.finding.line} "
                    f"({p.plan.risk}) — {status}"
                )
        else:
            parts.append("_(no patches)_")

        return "\n".join(parts) + "\n"
