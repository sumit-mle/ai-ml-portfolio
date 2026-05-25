"""Agent factory.

We build all six agents from one place so production guardrails (max_iter,
max_rpm, max_execution_time) are applied uniformly. This is the pattern
recommended by Mark AI's CrewAI production guide — without these caps,
agents can run unbounded and rack up cost.

Agents:
  - researcher      gathers hard facts from SEC EDGAR + web
  - analyst         scans recent news for triggers / signals
  - strategist      maps signals + facts to seller's offering
  - writer          assembles the final briefing object
  - critic          QA pass: grounded / specific / actionable
  - manager         the orchestrator (we define it explicitly because
                    CrewAI's auto-manager has known issues —
                    https://towardsdatascience.com/why-crewais-manager-worker-architecture-fails)
"""
from __future__ import annotations

from crewai import Agent, LLM

from ..config import get_settings
from ..tools.sec_edgar import SECEdgarTool
from ..tools.tavily_search import TavilyNewsTool, TavilyWebSearchTool


def _llm() -> LLM:
    s = get_settings()
    # CrewAI 1.x uses LiteLLM under the hood; "openai/gpt-4o-mini" is the
    # explicit provider/model form that avoids ambiguity.
    return LLM(
        model=f"openai/{s.gen_model}",
        api_key=s.openai_api_key,
        temperature=0.2,
        max_tokens=2000,
    )


def _common_kwargs() -> dict:
    s = get_settings()
    return {
        "llm": _llm(),
        "max_iter": s.agent_max_iter,
        "max_rpm": s.agent_max_rpm,
        "max_execution_time": s.task_timeout_seconds,
        "verbose": False,
        # Workers don't delegate further (delegation lives at the manager).
        "allow_delegation": False,
    }


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def make_researcher() -> Agent:
    return Agent(
        role="Senior Account Researcher",
        goal=(
            "Build an accurate Company Profile from SEC filings and the public "
            "web. Always include sources (URLs or SEC accession numbers). "
            "Distinguish facts from inference."
        ),
        backstory=(
            "You spent a decade as an equity analyst at a Tier 1 bank, where "
            "missing a single executive change meant losing a pitch. You now "
            "build briefings for B2B sellers and you trust SEC primary "
            "sources first, reputable financial press second, and never "
            "trust an unsourced claim."
        ),
        tools=[SECEdgarTool(), TavilyWebSearchTool()],
        **_common_kwargs(),
    )


def make_analyst() -> Agent:
    return Agent(
        role="Market Signals Analyst",
        goal=(
            "Surface 3-7 recent, account-specific signals (earnings, "
            "leadership changes, layoffs, acquisitions, product launches, "
            "regulatory issues) and judge whether the company is expanding, "
            "stable, contracting, or in crisis."
        ),
        backstory=(
            "You read 200+ company 8-Ks and earnings transcripts a quarter. "
            "You've learned that the *recent* signal is what changes a pitch "
            "from generic to surgical. You ignore PR fluff and focus on "
            "things buyers tell their CFO about."
        ),
        tools=[TavilyNewsTool()],
        **_common_kwargs(),
    )


def make_strategist() -> Agent:
    return Agent(
        role="Sales Strategist",
        goal=(
            "Translate the company profile and recent signals into a buyer-"
            "specific narrative for the seller's offering: pain points, why-"
            "now triggers, 2-3 use cases, proof points, and likely objections."
        ),
        backstory=(
            "You ran enterprise sales at three SaaS companies and now "
            "consult on go-to-market. You know the pitch is not about your "
            "features — it's about the buyer's quarter. You tie every point "
            "to a specific signal or fact in the research, never to "
            "platitudes."
        ),
        tools=[],  # Pure reasoning agent
        **_common_kwargs(),
    )


def make_writer() -> Agent:
    return Agent(
        role="Briefing Writer",
        goal=(
            "Assemble the final AccountBriefing object: a tight executive "
            "summary, a talk track an AE can actually use, and 3-5 sharp "
            "discovery questions. Quote sources verbatim from the prior "
            "research where possible."
        ),
        backstory=(
            "You used to write equity research notes for portfolio managers "
            "who read 30 of them a day. They'd give you 90 seconds. You "
            "learned to put the lede first and skip the warm-up."
        ),
        tools=[],
        **_common_kwargs(),
    )


def make_critic() -> Agent:
    return Agent(
        role="QA Critic",
        goal=(
            "Score the assembled briefing on 'grounded' (claims have "
            "sources), 'specific' (concrete vs platitude), and 'actionable' "
            "(usable in a real meeting). Flag specific issues and suggest "
            "fixes. Pass only if all three scores >= 0.7."
        ),
        backstory=(
            "You ran sales enablement at a public SaaS company. Generic "
            "briefings get reps generic meetings. You'd rather kill a "
            "briefing than ship one that wastes 30 minutes of a CRO's time."
        ),
        tools=[],
        **_common_kwargs(),
    )


# ---------------------------------------------------------------------------
# Manager (explicit — see module docstring on why)
# ---------------------------------------------------------------------------


def make_manager() -> Agent:
    s = get_settings()
    # Manager gets a bigger budget because it coordinates 5 workers.
    return Agent(
        role="Research Crew Manager",
        goal=(
            "Coordinate the research, analysis, strategy, writing, and QA "
            "agents to produce a single high-quality AccountBriefing for the "
            "seller. Delegate clearly, integrate outputs, and ship the final "
            "briefing in JSON form that matches the AccountBriefing schema."
        ),
        backstory=(
            "You ran sales operations at a $1B SaaS company. You orchestrate "
            "specialists without doing their jobs for them. You know the "
            "fastest path from request to a polished briefing is to give "
            "each specialist one clear sub-task and integrate the results."
        ),
        # Manager needs to delegate
        allow_delegation=True,
        llm=_llm(),
        max_iter=s.agent_max_iter,
        max_rpm=s.agent_max_rpm,
        max_execution_time=s.task_timeout_seconds * 2,
        verbose=False,
    )
