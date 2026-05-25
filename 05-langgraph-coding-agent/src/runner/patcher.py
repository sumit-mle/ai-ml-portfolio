"""Apply a PlannedChange to disk and produce a unified diff.

The patcher operates on a working directory copy of the project so the
original is untouched until commit. This is critical for HITL: the human
sees the proposed diff and either approves (commit) or rejects (drop the
working copy).
"""
from __future__ import annotations

import difflib
import logging
import shutil
from pathlib import Path

from ..models import PatchResult, PlannedChange
from ..transforms.recipes import apply_recipe

logger = logging.getLogger(__name__)


def make_workdir(source_root: Path, workdir: Path) -> Path:
    """Mirror source_root into workdir. Overwrites existing workdir."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, workdir, dirs_exist_ok=True)
    return workdir


def apply_plan(plan: PlannedChange, workdir: Path) -> PatchResult:
    finding = plan.finding
    target = workdir / finding.file
    if not target.exists():
        return PatchResult(plan=plan, applied=False, error=f"file not found: {target}")
    try:
        original = target.read_text(encoding="utf-8")
    except Exception as e:
        return PatchResult(plan=plan, applied=False, error=f"read failed: {e}")

    new_source, n_changes = apply_recipe(original, finding.recipe)
    if n_changes == 0 or new_source == original:
        return PatchResult(plan=plan, applied=False, error="recipe made no changes")

    try:
        target.write_text(new_source, encoding="utf-8")
    except Exception as e:
        return PatchResult(plan=plan, applied=False, error=f"write failed: {e}")

    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile=f"a/{finding.file}",
            tofile=f"b/{finding.file}",
        )
    )
    return PatchResult(
        plan=plan,
        applied=True,
        files_changed=[finding.file],
        diff=diff,
    )
