"""Run the crew over the golden set and write results."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .golden import load_golden
from .rubric import RubricScore, score_briefing
from ..crew import run_briefing, write_outputs

logger = logging.getLogger(__name__)


def run_eval(*, out_dir: str = "results") -> dict[str, Any]:
    goldens = load_golden()
    rows: list[dict[str, Any]] = []

    for i, req in enumerate(goldens, 1):
        logger.info("[%d/%d] Briefing for %s ...", i, len(goldens), req.company_name)
        t0 = time.time()
        try:
            briefing = run_briefing(req)
        except Exception as e:
            logger.exception("Briefing failed for %s", req.company_name)
            rows.append({
                "company": req.company_name,
                "error": str(e),
                "duration_s": round(time.time() - t0, 1),
            })
            continue

        write_outputs(briefing)
        score = score_briefing(briefing)
        rows.append({
            "company": req.company_name,
            "duration_s": round(time.time() - t0, 1),
            "company_facts_accuracy": score.company_facts_accuracy,
            "signal_specificity": score.signal_specificity,
            "angle_alignment": score.angle_alignment,
            "talk_track_usability": score.talk_track_usability,
            "discovery_question_quality": score.discovery_question_quality,
            "citation_discipline": score.citation_discipline,
            "average": round(score.average, 2),
            "overall_pass": score.overall_pass,
            "internal_critique_pass": (
                briefing.critique.overall_pass if briefing.critique else None
            ),
            "rationale": score.rationale[:500],
        })

    def _avg(field: str) -> float:
        vals = [r[field] for r in rows if field in r]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    summary = {
        "n_briefings": len(rows),
        "n_completed": sum(1 for r in rows if "error" not in r),
        "n_passed_judge": sum(1 for r in rows if r.get("overall_pass")),
        "avg_company_facts_accuracy": _avg("company_facts_accuracy"),
        "avg_signal_specificity": _avg("signal_specificity"),
        "avg_angle_alignment": _avg("angle_alignment"),
        "avg_talk_track_usability": _avg("talk_track_usability"),
        "avg_discovery_question_quality": _avg("discovery_question_quality"),
        "avg_citation_discipline": _avg("citation_discipline"),
        "overall_average": _avg("average"),
        "avg_duration_s": _avg("duration_s"),
    }

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(out_dir) / "crew_eval.json"
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    return summary
