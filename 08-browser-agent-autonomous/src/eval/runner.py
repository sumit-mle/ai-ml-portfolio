"""Run the deterministic Playwright extractor over each golden vendor and
score the bundles against expectations.

The autonomous agent path has its own `cli pull-evidence --autonomous`
command for ad-hoc demos. We deliberately don't put it in the eval because
LLM-driven runs are flaky and expensive — keeping CI deterministic.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .golden import VendorExpectation, all_expectations
from ..extractors.deterministic import pull_vendor_sync

logger = logging.getLogger(__name__)


def _check(bundle, expectation: VendorExpectation) -> tuple[bool, list[str]]:
    issues: list[str] = []
    by_kind = {item.kind: item for item in bundle.items}

    for kind in expectation.must_be_found:
        item = by_kind.get(kind)
        if item is None:
            issues.append(f"{kind}: not in bundle at all")
            continue
        if item.status != "found":
            issues.append(f"{kind}: expected found, got {item.status}")
            continue
        if item.bytes is None or item.bytes <= 0:
            issues.append(f"{kind}: empty file ({item.bytes} bytes)")
        if not item.sha256:
            issues.append(f"{kind}: missing sha256")

    for kind in expectation.expected_missing:
        item = by_kind.get(kind)
        if item is None or item.status != "missing":
            actual = item.status if item else "absent"
            issues.append(f"{kind}: expected missing, got {actual}")

    return len(issues) == 0, issues


def run_eval(*, out_dir: str = "results") -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    run_id = f"eval_{uuid4().hex[:8]}"
    for exp in all_expectations():
        t0 = time.time()
        bundle = pull_vendor_sync(exp.vendor_id, operator="eval", run_id=run_id)
        ok, issues = _check(bundle, exp)
        rows.append({
            "vendor_id": exp.vendor_id,
            "vendor_name": bundle.vendor_name,
            "duration_s": round(time.time() - t0, 1),
            "overall_status": bundle.overall_status,
            "n_required": bundle.n_required,
            "n_found": bundle.n_found,
            "n_missing": bundle.n_missing,
            "n_expired": bundle.n_expired,
            "items": [
                {
                    "kind": i.kind, "status": i.status,
                    "bytes": i.bytes, "sha256": (i.sha256 or "")[:16] + ("..." if i.sha256 else ""),
                    "valid_until": i.valid_until,
                }
                for i in bundle.items
            ],
            "passed": ok,
            "issues": issues,
        })

    n = len(rows)
    summary = {
        "n_vendors": n,
        "n_passed": sum(1 for r in rows if r["passed"]),
        "n_failed": sum(1 for r in rows if not r["passed"]),
        "avg_duration_s": round(sum(r["duration_s"] for r in rows) / max(1, n), 1),
        "run_id": run_id,
    }
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    Path(out_dir, "browser_eval.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2)
    )
    return summary
