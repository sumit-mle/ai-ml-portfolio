"""LLM-as-judge rubric for AccountBriefing quality.

Distinct from the in-crew critic (which is part of the pipeline). This rubric
runs OUTSIDE the crew using a separate model and prompt, scoring each briefing
on 6 axes that a sales leader actually cares about.

Returns RubricScore via OpenAI structured output — no flaky JSON parsing.
"""
from __future__ import annotations

import logging

from openai import OpenAI
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..models import AccountBriefing

logger = logging.getLogger(__name__)


class RubricScore(BaseModel):
    company_facts_accuracy: float = Field(
        ..., ge=0.0, le=1.0,
        description="CEO, HQ, industry, products correct and current?",
    )
    signal_specificity: float = Field(
        ..., ge=0.0, le=1.0,
        description="Recent signals are concrete and dated, not generic.",
    )
    angle_alignment: float = Field(
        ..., ge=0.0, le=1.0,
        description="Pain points and use cases tie to seller's offering AND specific company facts.",
    )
    talk_track_usability: float = Field(
        ..., ge=0.0, le=1.0,
        description="Could an AE walk in and use the talk track verbatim?",
    )
    discovery_question_quality: float = Field(
        ..., ge=0.0, le=1.0,
        description="Questions are sharp, account-specific, and trigger real buyer reflection.",
    )
    citation_discipline: float = Field(
        ..., ge=0.0, le=1.0,
        description="Every factual claim has a source URL or accession number.",
    )
    rationale: str = Field(default="", description="One paragraph explaining the scoring.")
    overall_pass: bool = Field(
        default=False,
        description="Overall pass requires all 6 axes >= 0.7",
    )

    @property
    def average(self) -> float:
        return (
            self.company_facts_accuracy
            + self.signal_specificity
            + self.angle_alignment
            + self.talk_track_usability
            + self.discovery_question_quality
            + self.citation_discipline
        ) / 6


_JUDGE_SYSTEM = (
    "You are a strict VP of Sales reviewing pre-meeting briefings written by "
    "a junior SDR. You score on the 6 axes provided, each 0.0 to 1.0. You "
    "are critical: 0.7 means 'I would let an AE walk in with this'; 0.5 "
    "means 'this needs a rewrite'; 0.3 means 'send the SDR for retraining'. "
    "Generic platitudes drop the score sharply. Tying claims to specific "
    "facts or dated signals raises it. Return JSON only."
)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8))
def score_briefing(briefing: AccountBriefing) -> RubricScore:
    s = get_settings()
    client = OpenAI(api_key=s.openai_api_key)

    user = (
        f"SELLER OFFERING: {briefing.request.seller_offering}\n\n"
        f"BRIEFING (markdown):\n\n{briefing.to_markdown()}\n\n"
        "Score each axis 0.0 to 1.0 with a one-paragraph rationale. "
        "Set overall_pass=true only if ALL six axes >= 0.7."
    )
    completion = client.beta.chat.completions.parse(
        model=s.judge_model,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": user},
        ],
        response_format=RubricScore,
        temperature=0.0,
    )
    msg = completion.choices[0].message
    if msg.refusal:
        logger.warning("Judge refused: %s", msg.refusal)
        return RubricScore(
            company_facts_accuracy=0.0,
            signal_specificity=0.0,
            angle_alignment=0.0,
            talk_track_usability=0.0,
            discovery_question_quality=0.0,
            citation_discipline=0.0,
            rationale=f"refused: {msg.refusal}",
            overall_pass=False,
        )
    score = msg.parsed
    score.overall_pass = (
        score.company_facts_accuracy >= 0.7
        and score.signal_specificity >= 0.7
        and score.angle_alignment >= 0.7
        and score.talk_track_usability >= 0.7
        and score.discovery_question_quality >= 0.7
        and score.citation_discipline >= 0.7
    )
    return score
