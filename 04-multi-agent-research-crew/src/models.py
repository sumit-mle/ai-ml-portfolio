"""Pydantic schemas for typed agent outputs.

Every CrewAI task in this crew uses `output_pydantic` so we never have to parse
free-form strings. The final briefing is a single AccountBriefing object that
serializes cleanly to JSON for downstream consumers (CRM, Slack, email).
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class ResearchRequest(BaseModel):
    """What the AE / SDR submits."""
    company_name: str
    company_domain: str | None = None
    seller_offering: str = Field(
        ...,
        description=(
            "What you sell. Used by the strategist to align the briefing to "
            "the buyer's pain points. e.g. 'AI observability platform for "
            "ML pipelines'."
        ),
    )
    meeting_context: str | None = Field(
        default=None,
        description="Discovery call, executive briefing, RFP response, etc.",
    )


# ---------------------------------------------------------------------------
# Per-agent outputs (the building blocks)
# ---------------------------------------------------------------------------


class CompanyProfile(BaseModel):
    """Researcher output: hard facts."""
    legal_name: str
    cik: str | None = None
    ticker: str | None = None
    headquarters: str | None = None
    industry: str | None = None
    employees: int | None = None
    revenue_usd: int | None = None
    fiscal_year_end: str | None = None

    ceo: str | None = None
    cfo: str | None = None
    other_executives: list[str] = Field(default_factory=list)
    board_members: list[str] = Field(default_factory=list)

    business_summary: str = ""
    main_products: list[str] = Field(default_factory=list)
    sources: list[str] = Field(
        default_factory=list,
        description="URLs / accession numbers backing each fact",
    )


class SignalKind(BaseModel):
    kind: Literal[
        "earnings_miss",
        "earnings_beat",
        "guidance_change",
        "leadership_change",
        "layoffs",
        "hiring_surge",
        "acquisition",
        "divestiture",
        "product_launch",
        "regulatory",
        "litigation",
        "other",
    ]
    description: str
    reported_at: str | None = None  # iso date or "approx Q3 2025"
    source_url: str | None = None
    relevance_to_seller: str = Field(
        default="",
        description="Why this matters for the seller's pitch (1-2 sentences).",
    )


class MarketSignals(BaseModel):
    """Analyst output: recent dynamics."""
    signals: list[SignalKind] = Field(default_factory=list)
    overall_temperature: Literal["expanding", "stable", "contracting", "in_crisis"] = "stable"
    one_line_summary: str = ""


class StrategicAngle(BaseModel):
    """Strategist output: how seller's offering fits the account."""
    pain_points: list[str] = Field(default_factory=list)
    why_now: list[str] = Field(default_factory=list, description="Triggers from the signals")
    use_cases: list[str] = Field(default_factory=list, description="2-3 specific use cases for THIS buyer")
    proof_points: list[str] = Field(
        default_factory=list,
        description="Evidence to bring (case studies, ROI numbers).",
    )
    objections: list[str] = Field(
        default_factory=list,
        description="Likely objections and how to handle them.",
    )
    discovery_questions: list[str] = Field(
        default_factory=list,
        description="3-5 sharp questions for the meeting.",
    )


class Critique(BaseModel):
    """Critic output: pre-delivery QA."""
    grounded: float = Field(..., ge=0.0, le=1.0,
                            description="Are claims supported by sources?")
    specific: float = Field(..., ge=0.0, le=1.0,
                            description="Are claims concrete or generic platitudes?")
    actionable: float = Field(..., ge=0.0, le=1.0,
                              description="Could an AE walk into the meeting and use this?")
    issues: list[str] = Field(default_factory=list)
    fixes_suggested: list[str] = Field(default_factory=list)
    overall_pass: bool


# ---------------------------------------------------------------------------
# Final assembled briefing
# ---------------------------------------------------------------------------


class AccountBriefing(BaseModel):
    """The final deliverable an AE/SDR can read in 2 minutes before a call."""
    request: ResearchRequest
    profile: CompanyProfile
    signals: MarketSignals
    angle: StrategicAngle
    executive_summary: str = Field(
        ...,
        description="3-5 sentence narrative the AE reads first.",
    )
    talk_track: list[str] = Field(
        default_factory=list,
        description="Bulleted opener + transition lines for the meeting.",
    )
    critique: Critique | None = None
    generated_at: str = Field(default_factory=lambda: date.today().isoformat())

    def to_markdown(self) -> str:
        def _bul(items: list[str]) -> str:
            return "\n".join(f"- {x}" for x in items) if items else "_(none)_"

        sigs = "\n".join(
            f"- **{s.kind}** — {s.description} _({s.reported_at or '—'})_\n"
            f"  - relevance: {s.relevance_to_seller or '—'}"
            for s in self.signals.signals
        ) or "_(no recent signals identified)_"

        critique_section = ""
        if self.critique:
            critique_section = (
                "\n## QA\n\n"
                "| grounded | specific | actionable | pass |\n"
                "|---:|---:|---:|:---:|\n"
                f"| {self.critique.grounded:.2f} | {self.critique.specific:.2f} "
                f"| {self.critique.actionable:.2f} "
                f"| {'PASS' if self.critique.overall_pass else 'FAIL'} |\n"
            )
            if self.critique.issues:
                critique_section += "\n**Issues flagged:**\n" + _bul(self.critique.issues) + "\n"
            if self.critique.fixes_suggested:
                critique_section += (
                    "\n**Fixes suggested:**\n" + _bul(self.critique.fixes_suggested) + "\n"
                )

        revenue = (
            f"${self.profile.revenue_usd:,}" if self.profile.revenue_usd else "—"
        )
        parts = [
            f"# Account Briefing — {self.profile.legal_name}",
            "",
            f"_Generated {self.generated_at} for: {self.request.seller_offering}_",
            "",
            "## Executive summary",
            "",
            self.executive_summary,
            "",
            "## Company profile",
            "",
            f"- **Legal name:** {self.profile.legal_name}",
            f"- **HQ:** {self.profile.headquarters or '—'}",
            f"- **Industry:** {self.profile.industry or '—'}",
            f"- **CEO / CFO:** {self.profile.ceo or '—'} / {self.profile.cfo or '—'}",
            f"- **Revenue:** {revenue}",
            f"- **Employees:** {self.profile.employees or '—'}",
            "",
            f"**Business:** {self.profile.business_summary or '—'}",
            "",
            "**Products:**",
            _bul(self.profile.main_products),
            "",
            f"## Recent signals — _{self.signals.overall_temperature}_",
            "",
            f"> {self.signals.one_line_summary}",
            "",
            sigs,
            "",
            "## Strategic angle",
            "",
            "**Likely pain points**",
            _bul(self.angle.pain_points),
            "",
            "**Why now**",
            _bul(self.angle.why_now),
            "",
            "**Use cases for this account**",
            _bul(self.angle.use_cases),
            "",
            "**Proof points to bring**",
            _bul(self.angle.proof_points),
            "",
            "**Anticipated objections**",
            _bul(self.angle.objections),
            "",
            "## Talk track",
            "",
            _bul(self.talk_track),
            "",
            "## Discovery questions",
            "",
            _bul(self.angle.discovery_questions),
            critique_section,
            "## Sources",
            "",
            _bul(self.profile.sources),
            "",
        ]
        return "\n".join(parts)
