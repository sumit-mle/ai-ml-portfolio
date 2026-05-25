"""CI-friendly pytest gate.

Runs each known SUT, compares the result to its saved baseline, and
asserts no metric is below tolerance. Drop this in your GitHub Actions
job and any PR that regresses RAG quality fails the build.

Skip cleanly if the baseline doesn't exist yet (first-time setup).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `src.*` resolvable when pytest runs from the project root.
_HARNESS_ROOT = Path(__file__).resolve().parent.parent
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

from src.runner import detect_regressions, load_baseline, run_sut  # noqa: E402
from src.sut.interface import known_suts  # noqa: E402


@pytest.mark.parametrize("sut_name", sorted(known_suts()))
def test_no_regression(sut_name: str) -> None:
    base = load_baseline(sut_name)
    if base is None:
        pytest.skip(f"No baseline for {sut_name}; run `harness save-baseline` first")

    current = run_sut(sut_name, use_judge=True)
    issues = detect_regressions(current, base)

    if issues:
        msg = f"\n{len(issues)} regression(s) for {sut_name}:\n"
        for i in issues:
            msg += (
                f"  {i['metric']}: baseline={i['baseline']:.2f} "
                f"current={i['current']:.2f} delta={i['delta']:+.2f} "
                f"(tolerance {i['tolerance']})\n"
            )
        pytest.fail(msg)
