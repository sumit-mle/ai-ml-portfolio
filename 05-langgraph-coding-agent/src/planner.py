"""LLM-based planner.

For each Finding the scanner produced, the planner:
  1. Assigns a risk rating (low/medium/high)
  2. Writes a one-sentence rationale
  3. Returns a PlannedChange

Why an LLM here? The scanner produces many findings; the LLM contextualizes
them ("this %-format inside a logging call is low risk; that %-format inside
a SQL string is HIGH risk because it might be a SQL injection signal").

We use OpenAI structured output (Pydantic) so the planner output is always
valid — no regex parsing of LLM prose.
"""
from __future__ import annotations

import logging
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import get_settings, require_openai_key
from .models import Finding, PlannedChange, Risk

logger = logging.getLogger(__name__)


_SYSTEM = (
    "You are a senior Python engineer reviewing modernization proposals. "
    "For each finding, you decide:\n"
    "  - risk: 'low' | 'medium' | 'high' — how likely is this rewrite to "
    "    change behavior or break tests?\n"
    "  - risk_reason: ONE sentence explaining the rating.\n"
    "  - expected_diff_summary: ONE sentence summarizing what the rewrite does.\n\n"
    "Risk guidelines:\n"
    "  low    — pure syntax modernization with no semantic change "
    "           (e.g. List[int] -> list[int], Optional[X] -> X | None).\n"
    "  medium — local behavior could differ in edge cases "
    "           (e.g. f-string evaluation order vs %-format on lazy expressions).\n"
    "  high   — control-flow change or risk of side-effect change "
    "           (e.g. open() -> with-statement; raises move).\n\n"
    "Be conservative. If unsure, rate higher."
)


class _PlanItem(BaseModel):
    risk: Literal["low", "medium", "high"]
    risk_reason: str
    expected_diff_summary: str


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8))
def _plan_one(client: OpenAI, model: str, finding: Finding) -> _PlanItem:
    user = (
        f"Recipe: {finding.recipe}\n"
        f"File: {finding.file}\n"
        f"Lines: {finding.line}-{finding.end_line}\n"
        f"Scanner rationale: {finding.rationale}\n"
        f"Scanner risk hint: {finding.estimated_risk}\n"
        f"Snippet:\n```\n{finding.snippet}\n```"
    )
    completion = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        response_format=_PlanItem,
        temperature=0.0,
    )
    msg = completion.choices[0].message
    if msg.refusal or msg.parsed is None:
        return _PlanItem(
            risk=finding.estimated_risk,
            risk_reason=f"planner fallback (refusal: {msg.refusal})",
            expected_diff_summary=f"Apply {finding.recipe}",
        )
    return msg.parsed


def plan_changes(findings: list[Finding]) -> list[PlannedChange]:
    require_openai_key()
    s = get_settings()
    client = OpenAI(api_key=s.openai_api_key)

    out: list[PlannedChange] = []
    for f in findings:
        try:
            item = _plan_one(client, s.gen_model, f)
        except Exception as e:
            logger.warning("Planner failed for %s: %s", f.recipe, e)
            item = _PlanItem(
                risk=f.estimated_risk,
                risk_reason=f"planner error: {e}",
                expected_diff_summary=f"Apply {f.recipe}",
            )
        risk: Risk = item.risk  # type: ignore[assignment]
        plan = PlannedChange(
            finding=f,
            risk=risk,
            risk_reason=item.risk_reason,
            expected_diff_summary=item.expected_diff_summary,
            auto_approved=s.is_auto_approved(risk),
        )
        out.append(plan)
    logger.info(
        "Planned %d changes (auto-approved %d)",
        len(out),
        sum(1 for p in out if p.auto_approved),
    )
    return out
