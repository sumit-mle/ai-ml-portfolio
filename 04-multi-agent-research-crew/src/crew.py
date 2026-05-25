"""Crew assembly + sequential task pipeline.

After researching CrewAI's hierarchical-vs-sequential trade-offs (see Mark
AI's production guide and the towardsdatascience.com analysis of hierarchical
manager failures) we use a deterministic sequential pipeline with explicit
task-to-task context handoffs.

Pipeline:
    research_task -> signals_task -> strategy_task -> write_task -> critique_task
                                                            ↑
                                                      revise_task (optional, if
                                                      critique fails)

Every task uses `output_pydantic` so the next task receives a typed object,
not free-form prose. This is the most reliable pattern for CrewAI in
production.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from crewai import Crew, Process, Task

from .agents.factory import (
    make_analyst,
    make_critic,
    make_researcher,
    make_strategist,
    make_writer,
)
from .config import get_settings, require_openai_key
from .models import (
    AccountBriefing,
    CompanyProfile,
    Critique,
    MarketSignals,
    ResearchRequest,
    StrategicAngle,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task descriptions. Heavy lifting goes here — agents follow the description.
# Each task references the previous task's typed output via {context_key}.
# ---------------------------------------------------------------------------


def _make_tasks(req: ResearchRequest) -> list[Task]:
    researcher = make_researcher()
    analyst = make_analyst()
    strategist = make_strategist()
    writer = make_writer()
    critic = make_critic()

    research_task = Task(
        description=(
            f"Build a Company Profile for **{req.company_name}**"
            + (f" (domain: {req.company_domain})" if req.company_domain else "")
            + ".\n\n"
            "Steps you MUST follow:\n"
            "1. Use the SEC EDGAR 10-K Lookup tool to fetch the company's most "
            "recent 10-K Item 1 Business section. If the company is not a US "
            "public filer, the tool will say so — fall back to web search.\n"
            "2. Use Tavily Web Search to confirm CEO, CFO, headquarters, "
            "industry, employee count, and most recent annual revenue.\n"
            "3. Compile the results into a CompanyProfile. Every factual "
            "claim must have at least one URL or SEC accession in `sources`.\n\n"
            "Be concrete. 'A leading provider of cloud software' is not a "
            "business summary — say what they actually sell."
        ),
        agent=researcher,
        expected_output=(
            "A CompanyProfile JSON object with hard facts and source URLs / "
            "accession numbers."
        ),
        output_pydantic=CompanyProfile,
    )

    signals_task = Task(
        description=(
            f"Identify recent (last 90-180 days) market signals for "
            f"**{req.company_name}**. Use Tavily Recent News to pull "
            "earnings, leadership changes, layoffs, acquisitions, product "
            "launches, regulatory matters, or major partnerships.\n\n"
            "For each signal:\n"
            " - classify it (use the SignalKind enum)\n"
            " - cite a source URL\n"
            " - say WHY it matters for a seller of: "
            f"'{req.seller_offering}'\n\n"
            "Also classify the company's overall temperature "
            "(expanding/stable/contracting/in_crisis) in one short sentence. "
            "Skip generic news; focus on triggers a CFO would care about."
        ),
        agent=analyst,
        expected_output="A MarketSignals JSON with 3-7 specific recent signals.",
        output_pydantic=MarketSignals,
        context=[research_task],
    )

    strategy_task = Task(
        description=(
            f"Given the CompanyProfile (research) and MarketSignals "
            f"(analysis), build a StrategicAngle that aligns the seller's "
            f"offering ('{req.seller_offering}') to THIS specific buyer.\n\n"
            "Required fields:\n"
            " - 2-4 specific pain points (tied to facts from research)\n"
            " - 2-4 'why now' triggers (tied to specific signals)\n"
            " - 2-3 use cases — each use case must reference a real product "
            "they have, a team they run, or a workflow they own\n"
            " - 2-3 proof points the seller can bring (case studies, ROI)\n"
            " - 2-3 anticipated objections and how to handle them\n"
            " - 3-5 sharp discovery questions for the meeting\n\n"
            "DISCOVERY QUESTIONS RULES (these get scored hard):\n"
            " - NEVER ask 'what challenges are you facing' or 'tell me about your business'\n"
            " - Each question MUST reference either (a) a specific recent signal "
            "(e.g. 'Following the Q3 layoffs in your X division, how is...') "
            "or (b) a specific named product/team from the company profile\n"
            " - Questions should make the buyer think 'how does this person know "
            "that about us?' — not 'I get this question every week'\n\n"
            "If you write a generic line like 'improve efficiency' or "
            "'reduce costs', you have failed. Tie every point to evidence."
        ),
        agent=strategist,
        expected_output="A StrategicAngle JSON with all five lists populated.",
        output_pydantic=StrategicAngle,
        context=[research_task, signals_task],
    )

    write_task = Task(
        description=(
            "Assemble the final AccountBriefing using the inputs from "
            "research, signals, and strategy tasks.\n\n"
            "Your job:\n"
            " 1. Write a 3-5 sentence executive_summary that names the "
            "company, the most important recent signal, and the top angle.\n"
            " 2. Write a talk_track of 4-6 bulleted lines an AE can read "
            "verbatim in the first 90 seconds of the meeting.\n"
            " 3. Pass through the profile, signals, and angle objects "
            "unchanged in their respective fields.\n"
            " 4. CITATION DISCIPLINE: ensure profile.sources contains AT LEAST 4 "
            "URLs or SEC accession numbers. Each major claim "
            "(CEO, revenue, employees, recent earnings, layoffs, products) MUST "
            "have a corresponding URL in sources. If the research returned a "
            "URL, copy it through verbatim.\n"
            " 5. Set request.company_name, request.company_domain, and "
            f"request.seller_offering to:\n"
            f"    company_name: {req.company_name!r}\n"
            f"    company_domain: {req.company_domain!r}\n"
            f"    seller_offering: {req.seller_offering!r}\n"
            f"    meeting_context: {req.meeting_context!r}\n"
            " 6. Leave critique=null. The QA agent fills it next."
        ),
        agent=writer,
        expected_output="A complete AccountBriefing JSON.",
        output_pydantic=AccountBriefing,
        context=[research_task, signals_task, strategy_task],
    )

    critique_task = Task(
        description=(
            "Score the AccountBriefing on three rubrics, each 0.0 to 1.0:\n\n"
            "**grounded** — every factual claim has a source in "
            "profile.sources or signals.signals[*].source_url. If a key "
            "executive name has no source, score below 0.7.\n\n"
            "**specific** — does the briefing mention this company's actual "
            "products, recent quarter results, named executives? Generic "
            "platitudes (e.g. 'they value efficiency') drag the score down.\n\n"
            "**actionable** — could an AE walk into the meeting and use the "
            "talk track and discovery questions verbatim? If the questions "
            "are 'tell me about your business' generic, score below 0.7.\n\n"
            "List the specific issues you found in `issues` and concrete "
            "fixes in `fixes_suggested`. Set overall_pass = (all three "
            ">= 0.7)."
        ),
        agent=critic,
        expected_output="A Critique JSON.",
        output_pydantic=Critique,
        context=[write_task],
    )

    return [research_task, signals_task, strategy_task, write_task, critique_task]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_briefing(req: ResearchRequest) -> AccountBriefing:
    require_openai_key()

    tasks = _make_tasks(req)
    crew = Crew(
        agents=[t.agent for t in tasks],
        tasks=tasks,
        process=Process.sequential,
        verbose=False,
        memory=False,  # Keep runs isolated; memory adds cost without
                      # clear benefit for a stateless briefing job.
    )

    logger.info("Crew kickoff for %s", req.company_name)
    result = crew.kickoff(inputs={
        "company_name": req.company_name,
        "company_domain": req.company_domain or "",
        "seller_offering": req.seller_offering,
        "meeting_context": req.meeting_context or "",
    })

    # The final task (critique) returns a Critique. The penultimate (write)
    # returned the AccountBriefing without critique. Stitch them.
    write_task = tasks[3]
    critique_task = tasks[4]

    briefing = _coerce_pydantic(write_task.output, AccountBriefing)
    critique = _coerce_pydantic(critique_task.output, Critique)

    if briefing is None:
        logger.error("Crew returned no parseable AccountBriefing")
        raise RuntimeError(
            "Crew did not return a valid AccountBriefing. Check task outputs."
        )

    briefing.critique = critique
    # Belt-and-braces: ensure the request fields are correct in case the
    # writer dropped them; force generated_at to today (writers sometimes
    # echo a placeholder date).
    from datetime import date as _date
    briefing.request = req
    briefing.generated_at = _date.today().isoformat()
    return briefing


def _coerce_pydantic(task_output, model_cls):
    """CrewAI 1.x exposes the parsed Pydantic on `task_output.pydantic`.

    Older versions sometimes returned a string in `.raw`. We try both.
    """
    if task_output is None:
        return None
    pyd = getattr(task_output, "pydantic", None)
    if isinstance(pyd, model_cls):
        return pyd
    raw = getattr(task_output, "raw", None) or str(task_output)
    try:
        return model_cls.model_validate_json(raw)
    except Exception:
        try:
            data = json.loads(raw)
            return model_cls.model_validate(data)
        except Exception as e:
            logger.warning("Could not parse %s from output: %s", model_cls.__name__, e)
            return None


def write_outputs(briefing: AccountBriefing, *, slug: str | None = None) -> dict[str, Path]:
    """Persist the briefing as both JSON and Markdown."""
    s = get_settings()
    out_dir = Path(s.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = slug or "".join(c if c.isalnum() else "_" for c in briefing.profile.legal_name)[:60]
    json_path = out_dir / f"{slug}.json"
    md_path = out_dir / f"{slug}.md"

    json_path.write_text(briefing.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(briefing.to_markdown(), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}
