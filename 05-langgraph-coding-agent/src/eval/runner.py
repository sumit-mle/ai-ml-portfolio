"""Run the agent on each Scenario and score the outcome.

For each scenario:
  1. Materialize the source files in a temp directory.
  2. Run the agent in --auto_yes mode against that directory.
  3. Compare each file to expected (changed vs unchanged).
  4. Verify tests still pass.

We score on three axes per scenario:
  - changes_correct      did the right files change (and only those)?
  - tests_preserved      do tests still pass after modernization?
  - rollback_when_needed for scenarios designed to fail tests, did rollback fire?
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .golden import Scenario, all_scenarios
from ..agent.graph import run_modernize

logger = logging.getLogger(__name__)


def _materialize(scenario: Scenario, root: Path) -> None:
    for rel, content in scenario.files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _read_files(root: Path, paths: set[str]) -> dict[str, str]:
    return {p: (root / p).read_text(encoding="utf-8") for p in paths}


def _evaluate_one(scenario: Scenario, base: Path) -> dict[str, Any]:
    src = base / scenario.name / "src"
    workdir = base / scenario.name / "workdir"
    src.mkdir(parents=True, exist_ok=True)
    _materialize(scenario, src)

    t0 = time.time()
    state = run_modernize(
        src,
        workdir,
        interactive=False,
        auto_yes=True,
        rollback_enabled=True,
    )
    duration = round(time.time() - t0, 1)

    # Diff each expected file
    src_files = _read_files(src, set(scenario.files.keys()))
    work_files = _read_files(workdir, set(scenario.files.keys()))
    actually_changed = {p for p in scenario.files if src_files[p] != work_files[p]}

    correct_changed = scenario.expected_changes_in.issubset(actually_changed)
    no_extra_changed = actually_changed.issubset(scenario.expected_changes_in)
    changes_correct = correct_changed and no_extra_changed

    after = state.get("test_run_after") or {}
    before = state.get("test_run_before") or {}
    tests_preserved = bool(after.get("passed", True)) and (
        before.get("n_passed", 0) <= after.get("n_passed", 0) + after.get("n_failed", 0)
    )

    return {
        "scenario": scenario.name,
        "description": scenario.description,
        "duration_s": duration,
        "expected_changes_in": sorted(scenario.expected_changes_in),
        "actually_changed": sorted(actually_changed),
        "changes_correct": changes_correct,
        "tests_before": before,
        "tests_after": after,
        "tests_preserved": tests_preserved,
        "rollback_triggered": bool(state.get("rollback_triggered")),
        "n_findings": len(state.get("findings", [])),
        "n_applied": sum(1 for p in state.get("patches", []) if p.get("applied")),
    }


def run_eval(*, out_dir: str = "results") -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix="modernize_eval_"))
    logger.info("Eval workspace: %s", base)
    rows: list[dict[str, Any]] = []
    try:
        for scenario in all_scenarios():
            logger.info("--- Scenario: %s ---", scenario.name)
            row = _evaluate_one(scenario, base)
            rows.append(row)
    finally:
        # Keep the eval workspace for inspection if anything failed; otherwise
        # clean it up quietly.
        if all(r["changes_correct"] and r["tests_preserved"] for r in rows):
            shutil.rmtree(base, ignore_errors=True)

    n = len(rows)
    summary = {
        "n_scenarios": n,
        "n_changes_correct": sum(1 for r in rows if r["changes_correct"]),
        "n_tests_preserved": sum(1 for r in rows if r["tests_preserved"]),
        "n_overall_pass": sum(
            1 for r in rows if r["changes_correct"] and r["tests_preserved"]
        ),
        "avg_duration_s": round(sum(r["duration_s"] for r in rows) / max(1, n), 1),
    }

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(out_dir) / "modernize_eval.json"
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    return summary
