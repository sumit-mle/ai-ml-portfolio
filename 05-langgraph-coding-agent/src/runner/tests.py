"""Run the project's pytest suite and classify the result.

We invoke pytest as a subprocess (not via pytest.main) so a crashing user
test can't take down the agent process. Tests that don't exist are fine —
we record `n_passed=0, passed=True` and the audit report shows "no tests".
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from pathlib import Path

from ..models import TestRun

logger = logging.getLogger(__name__)


_SUMMARY_RE = re.compile(
    r"(?:(?P<failed>\d+)\s+failed,?\s*)?"
    r"(?:(?P<passed>\d+)\s+passed,?\s*)?"
    r"(?:(?P<skipped>\d+)\s+skipped,?\s*)?",
)


def has_pytest_tests(project_root: Path) -> bool:
    """Heuristic: project has tests if it has test_*.py or */tests/*.py."""
    if any(project_root.rglob("test_*.py")):
        return True
    if any(project_root.rglob("*_test.py")):
        return True
    return False


def run_pytest(project_root: Path, *, timeout_s: int = 120) -> TestRun:
    if not has_pytest_tests(project_root):
        logger.info("No pytest tests found in %s", project_root)
        return TestRun(passed=True, n_passed=0, output_tail="(no tests)")

    t0 = time.time()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short", "--no-header", str(project_root)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return TestRun(
            passed=False,
            duration_s=round(time.time() - t0, 1),
            output_tail=f"(timed out after {timeout_s}s)",
        )

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    duration = round(time.time() - t0, 1)
    n_passed = n_failed = n_skipped = 0

    # pytest's summary line e.g. "5 passed, 1 skipped in 0.02s"
    summary_lines = [ln for ln in output.splitlines() if "passed" in ln or "failed" in ln or "error" in ln]
    if summary_lines:
        for ln in reversed(summary_lines):
            m = _SUMMARY_RE.search(ln)
            if m:
                n_passed = int(m.group("passed") or 0)
                n_failed = int(m.group("failed") or 0)
                n_skipped = int(m.group("skipped") or 0)
                break

    passed = result.returncode == 0
    tail_lines = output.splitlines()[-80:]
    return TestRun(
        passed=passed,
        n_passed=n_passed,
        n_failed=n_failed,
        n_skipped=n_skipped,
        duration_s=duration,
        output_tail="\n".join(tail_lines),
    )
