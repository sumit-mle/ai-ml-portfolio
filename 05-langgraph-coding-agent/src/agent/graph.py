"""LangGraph state machine for the modernization agent.

Nodes:
    scan        -> walk the project, return Findings
    plan        -> LLM rates each Finding (PlannedChange + risk + reason)
    gate        -> auto-approve below the configured risk threshold;
                   collect human approvals for the rest (interactive or
                   non-interactive)
    apply       -> for each approved plan, run the libcst transformer in a
                   working copy of the project
    test        -> run pytest on the working copy
    rollback    -> if tests fail and rollback is enabled, restore originals
    report      -> write AuditReport JSON + Markdown

Edges:
    scan -> plan -> gate -> apply -> test -> [pass: report] | [fail: rollback -> report]

LangGraph state is a TypedDict; we wrap heavier objects (Findings, plans,
patches) as JSON-serializable dicts so checkpointing works cleanly.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, StateGraph

from ..config import get_settings
from ..models import (
    AuditReport,
    Finding,
    PatchResult,
    PlannedChange,
    TestRun,
)
from ..planner import plan_changes
from ..runner.patcher import apply_plan, make_workdir
from ..runner.tests import run_pytest
from ..scanner import scan_repo

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    project_root: str        # source-of-truth path (read-only)
    workdir: str             # working copy path
    rollback_enabled: bool
    interactive: bool        # ask the human at the gate?
    auto_yes: bool           # if non-interactive, approve everything?

    findings: list[dict[str, Any]]
    plans: list[dict[str, Any]]
    patches: list[dict[str, Any]]
    test_run_before: dict[str, Any] | None
    test_run_after: dict[str, Any] | None
    rollback_triggered: bool
    started_at: float


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------


def _node_scan(state: AgentState) -> AgentState:
    root = Path(state["project_root"])
    findings = scan_repo(root)
    logger.info("scan -> %d findings", len(findings))
    return {"findings": [f.model_dump() for f in findings]}


def _node_test_before(state: AgentState) -> AgentState:
    root = Path(state["project_root"])
    res = run_pytest(root)
    logger.info(
        "test_before -> %s (passed=%d failed=%d)",
        "PASS" if res.passed else "FAIL", res.n_passed, res.n_failed,
    )
    return {"test_run_before": res.model_dump()}


def _node_plan(state: AgentState) -> AgentState:
    findings = [Finding.model_validate(d) for d in state.get("findings", [])]
    if not findings:
        logger.info("plan -> 0 (no findings)")
        return {"plans": []}
    plans = plan_changes(findings)
    return {"plans": [p.model_dump() for p in plans]}


def _node_gate(state: AgentState) -> AgentState:
    plans = [PlannedChange.model_validate(d) for d in state.get("plans", [])]
    interactive = state.get("interactive", False)
    auto_yes = state.get("auto_yes", False)

    decided: list[PlannedChange] = []
    for p in plans:
        if p.auto_approved:
            decided.append(p)
            continue
        # Non-auto: requires human signal
        if interactive:
            ok = _ask_human(p)
        else:
            ok = auto_yes
        p.human_approved = ok
        if ok:
            decided.append(p)
        else:
            # Keep the plan in state so the audit reflects the rejection
            decided.append(p)
    n_auto = sum(1 for p in decided if p.auto_approved)
    n_human_yes = sum(1 for p in decided if p.human_approved is True)
    n_human_no = sum(1 for p in decided if p.human_approved is False)
    logger.info(
        "gate -> %d auto-approved, %d human-approved, %d rejected",
        n_auto, n_human_yes, n_human_no,
    )
    return {"plans": [p.model_dump() for p in decided]}


def _ask_human(p: PlannedChange) -> bool:
    print(
        f"\n[HITL] {p.finding.recipe} @ {p.finding.file}:{p.finding.line}\n"
        f"  Risk: {p.risk}  ({p.risk_reason})\n"
        f"  Expected: {p.expected_diff_summary}\n"
        f"  Snippet:\n    {p.finding.snippet[:200]}\n"
    )
    try:
        ans = input("Apply? [y/N] ").strip().lower()
    except EOFError:
        ans = "n"
    return ans in ("y", "yes")


def _node_apply(state: AgentState) -> AgentState:
    src = Path(state["project_root"])
    workdir = Path(state["workdir"])
    make_workdir(src, workdir)

    plans = [PlannedChange.model_validate(d) for d in state.get("plans", [])]
    approved: list[PlannedChange] = [
        p for p in plans
        if p.auto_approved or p.human_approved is True
    ]

    patches: list[PatchResult] = []
    by_file_done: set[tuple[str, str]] = set()
    for plan in approved:
        # libcst transformers operate at the FILE level; running the same
        # recipe twice on the same file is redundant. Dedup by (file, recipe).
        key = (plan.finding.file, plan.finding.recipe)
        if key in by_file_done:
            patches.append(PatchResult(plan=plan, applied=False, error="already applied to this file"))
            continue
        result = apply_plan(plan, workdir)
        patches.append(result)
        if result.applied:
            by_file_done.add(key)

    logger.info(
        "apply -> %d applied, %d skipped",
        sum(1 for p in patches if p.applied),
        sum(1 for p in patches if not p.applied),
    )
    return {"patches": [p.model_dump() for p in patches]}


def _node_test_after(state: AgentState) -> AgentState:
    workdir = Path(state["workdir"])
    res = run_pytest(workdir)
    logger.info(
        "test_after -> %s (passed=%d failed=%d)",
        "PASS" if res.passed else "FAIL", res.n_passed, res.n_failed,
    )
    return {"test_run_after": res.model_dump()}


def _route_after_tests(state: AgentState) -> Literal["rollback", "report"]:
    after = state.get("test_run_after") or {}
    before = state.get("test_run_before") or {}
    rollback = state.get("rollback_enabled", True)
    after_passed = bool(after.get("passed"))
    before_passed = bool(before.get("passed", True))
    # Only rollback if tests USED TO pass but now fail (regressions caused
    # by us), and rollback is enabled.
    if rollback and before_passed and not after_passed:
        return "rollback"
    return "report"


def _node_rollback(state: AgentState) -> AgentState:
    src = Path(state["project_root"])
    workdir = Path(state["workdir"])
    logger.warning("rollback: restoring workdir from project_root")
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, workdir, dirs_exist_ok=True)
    return {"rollback_triggered": True}


def _node_report(state: AgentState) -> AgentState:
    plans = [PlannedChange.model_validate(d) for d in state.get("plans", [])]
    patches = [PatchResult.model_validate(d) for d in state.get("patches", [])]
    findings = state.get("findings", [])
    by_recipe: dict[str, int] = {}
    for p in patches:
        if p.applied:
            r = p.plan.finding.recipe
            by_recipe[r] = by_recipe.get(r, 0) + 1

    n_auto = sum(1 for p in plans if p.auto_approved)
    n_yes = sum(1 for p in plans if p.human_approved is True)
    n_no = sum(1 for p in plans if p.human_approved is False)
    n_applied = sum(1 for p in patches if p.applied)
    # "failed_to_apply" should mean: tried and got an actual error, not
    # "deduplicated because we already applied this recipe to this file".
    n_failed = sum(
        1 for p in patches
        if not p.applied and p.error not in (None, "already applied to this file")
    )

    test_before = (
        TestRun.model_validate(state["test_run_before"])
        if state.get("test_run_before") else None
    )
    test_after = (
        TestRun.model_validate(state["test_run_after"])
        if state.get("test_run_after") else None
    )
    started_at = state.get("started_at", time.time())
    report = AuditReport(
        project_path=state["project_root"],
        n_findings=len(findings),
        n_planned=len(plans),
        n_auto_approved=n_auto,
        n_human_approved=n_yes,
        n_human_rejected=n_no,
        n_applied=n_applied,
        n_failed_to_apply=n_failed,
        test_run_before=test_before,
        test_run_after=test_after,
        rollback_triggered=bool(state.get("rollback_triggered")),
        duration_s=round(time.time() - started_at, 1),
        by_recipe=by_recipe,
        patches=patches,
    )
    s = get_settings()
    out_dir = Path(s.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in Path(state["project_root"]).name)[:60]
    json_path = out_dir / f"audit_{safe}.json"
    md_path = out_dir / f"audit_{safe}.md"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    logger.info("report -> %s and %s", json_path, md_path)
    return {}


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("scan", _node_scan)
    g.add_node("test_before", _node_test_before)
    g.add_node("plan", _node_plan)
    g.add_node("gate", _node_gate)
    g.add_node("apply", _node_apply)
    g.add_node("test_after", _node_test_after)
    g.add_node("rollback", _node_rollback)
    g.add_node("report", _node_report)

    g.set_entry_point("scan")
    g.add_edge("scan", "test_before")
    g.add_edge("test_before", "plan")
    g.add_edge("plan", "gate")
    g.add_edge("gate", "apply")
    g.add_edge("apply", "test_after")
    g.add_conditional_edges(
        "test_after",
        _route_after_tests,
        {"rollback": "rollback", "report": "report"},
    )
    g.add_edge("rollback", "report")
    g.add_edge("report", END)
    return g.compile()


def run_modernize(
    project_root: Path,
    workdir: Path,
    *,
    interactive: bool = False,
    auto_yes: bool = False,
    rollback_enabled: bool = True,
) -> AgentState:
    graph = build_graph()
    initial: AgentState = {
        "project_root": str(project_root),
        "workdir": str(workdir),
        "rollback_enabled": rollback_enabled,
        "interactive": interactive,
        "auto_yes": auto_yes,
        "started_at": time.time(),
    }
    return graph.invoke(initial)
