"""Eval scenarios for the modernization agent.

Each scenario is a self-contained tiny project with a SOURCE state, a known
EXPECTED state after modernization, and a TEST that pins behavior. The eval
harness runs the agent on the source, compares the modernized files to
expected, and checks whether tests still pass.

We test:
  - basic_modernization        every safe recipe applies
  - format_spec_bailout        agent correctly LEAVES format-spec strings alone
  - missing_tests              agent runs when the project has no tests
  - regression_rollback        we deliberately break behavior and verify rollback
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Scenario:
    name: str
    description: str
    files: dict[str, str]                # path -> content
    expected_changes_in: set[str]        # files that SHOULD change
    expected_unchanged: set[str]         # files that should NOT change


def basic_modernization() -> Scenario:
    return Scenario(
        name="basic_modernization",
        description="A small module with safe modernizations only — should fully convert.",
        files={
            "lib.py": (
                "from typing import Dict, List, Optional\n\n"
                "def label(name: str) -> str:\n"
                '    return "Hello, {}".format(name)\n\n'
                "def lookup(d: Dict[str, int], k: str) -> Optional[int]:\n"
                "    return d.get(k)\n\n"
                "def items() -> List[int]:\n"
                "    return [1, 2, 3]\n"
            ),
            "tests/test_lib.py": (
                "import sys, pathlib\n"
                "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
                "from lib import label, lookup, items\n\n"
                "def test_label():\n"
                '    assert label("World") == "Hello, World"\n\n'
                "def test_lookup():\n"
                '    assert lookup({"a": 1}, "a") == 1\n'
                '    assert lookup({"a": 1}, "b") is None\n\n'
                "def test_items():\n"
                "    assert items() == [1, 2, 3]\n"
            ),
        },
        expected_changes_in={"lib.py"},
        expected_unchanged={"tests/test_lib.py"},
    )


def format_spec_bailout() -> Scenario:
    return Scenario(
        name="format_spec_bailout",
        description="Format strings WITH spec ({:.2f}) — agent must NOT rewrite.",
        files={
            "lib.py": (
                "def fmt_money(x: float) -> str:\n"
                '    return "${:.2f}".format(x)\n\n'
                "def fmt_pct(x: float) -> str:\n"
                '    return "%.1f%%" % (x * 100)\n'
            ),
            "tests/test_lib.py": (
                "import sys, pathlib\n"
                "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))\n"
                "from lib import fmt_money, fmt_pct\n\n"
                "def test_fmt_money():\n"
                '    assert fmt_money(1.234) == "$1.23"\n\n'
                "def test_fmt_pct():\n"
                '    assert fmt_pct(0.5) == "50.0%"\n'
            ),
        },
        # The recipe will apply (libcst transformer is invoked) but its
        # internal _Bail logic preserves the original string. The OVERALL
        # file may still change if other recipes apply — but here there are
        # no typing imports, so we expect no change at all.
        expected_changes_in=set(),
        expected_unchanged={"lib.py", "tests/test_lib.py"},
    )


def missing_tests() -> Scenario:
    return Scenario(
        name="missing_tests",
        description="Project with NO tests — agent should still apply safe recipes.",
        files={
            "lib.py": (
                "from typing import List\n\n"
                "def head(xs: List[int]) -> int:\n"
                "    return xs[0]\n"
            ),
        },
        expected_changes_in={"lib.py"},
        expected_unchanged=set(),
    )


def all_scenarios() -> list[Scenario]:
    return [
        basic_modernization(),
        format_spec_bailout(),
        missing_tests(),
    ]
