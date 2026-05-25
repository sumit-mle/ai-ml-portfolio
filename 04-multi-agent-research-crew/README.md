# 04 — B2B Sales Account Research Crew (CrewAI)

Production-grade **multi-agent crew** that takes one input — _"I'm meeting Microsoft about AI observability tomorrow"_ — and returns a typed, sourced **Account Briefing** an AE/SDR can read in 2 minutes before the call.

Five specialized agents collaborate via CrewAI's sequential process, with **Pydantic-typed outputs at every handoff** so each step's data is validated before the next agent reads it.

## The business problem

Today, an account executive doing real prep before a discovery call spends 1-2 hours per meeting:

- 30 min: read the company's most-recent 10-K, earnings call, news
- 20 min: hunt for executive moves, layoffs, acquisitions, product launches
- 20 min: align the seller's value prop to whatever they just learned
- 30 min: write up notes + talk track + discovery questions

Across a 30-meeting/quarter quota that's 30-60 hours of prep. The reality: most reps skip most of this and walk into meetings cold. The result is generic pitches that don't land.

This crew compresses the prep to **~90 seconds of agent time** at **~$0.10/briefing**.

### Verified results on real public companies

5 briefings × 6 quality axes scored by an independent LLM judge:

| Metric | Score |
|--------|------:|
| company_facts_accuracy | **1.00** |
| signal_specificity | 0.82 |
| angle_alignment | 0.76 |
| talk_track_usability | 0.74 |
| discovery_question_quality | 0.66 |
| citation_discipline | 0.70 |
| **overall average** | **0.78** |

Companies tested: Microsoft, Costco, Pfizer, NextEra Energy, JPMorgan Chase. See [`results/README.md`](./results/README.md) for the full breakdown.

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| Agent framework | **CrewAI 1.14** | Role-based agents are the right primitive for sales research; sequential process is more deterministic than the auto-manager (per the [tds analysis](https://towardsdatascience.com/why-crewais-manager-worker-architecture-fails-and-how-to-fix-it/)) |
| LLM | OpenAI `gpt-4o-mini` | Cheap, plenty good for drafting and tool-use; matches the rest of the portfolio |
| Web search | **Tavily** (`tavily-python`) | Built for LLM agents, returns clean text + answer summary, free tier covers 1000 calls/mo |
| SEC filings | EDGAR REST API (reused from project 03) | License-clear, every US public company |
| Output schema | Pydantic v2 (`BaseModel`) | Typed handoffs between agents, no string-parsing |
| Structured judge | OpenAI Beta `parse` API with Pydantic | Reliable JSON for the eval rubric |
| Resilience | tenacity retries on EDGAR / Tavily | Network is a fact of life |
| Production guardrails | per-agent `max_iter=12`, `max_rpm=30`, `max_execution_time=180s` | Per Mark AI's [CrewAI production guide](https://markaicode.com/architecture/crewai-agent-architecture/), without these agents can run unbounded |

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  REQUEST: ResearchRequest(company_name, seller_offering, context)    │
└────────────────────────────┬─────────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────┐
│  RESEARCHER  (SECEdgarTool, TavilyWebSearchTool)│  →  CompanyProfile
│  • Fetch most-recent 10-K Item 1                │     (CEO, revenue,
│  • Confirm CEO/CFO/HQ/products on the web      │      products, sources)
│  • Cite every factual claim                    │
└────────────────────────────┬────────────────────┘
                             ▼
┌─────────────────────────────────────────────────┐
│  ANALYST    (TavilyNewsTool)                    │  →  MarketSignals
│  • Pull last 90-180 days of news                │     (3-7 dated
│  • Classify each signal (earnings, layoffs,…)   │      signals + temp:
│  • Tag relevance to seller's offering           │      expanding/stable/
│  • Judge company temperature                    │      contracting)
└────────────────────────────┬────────────────────┘
                             ▼
┌─────────────────────────────────────────────────┐
│  STRATEGIST  (no tools — pure reasoning)        │  →  StrategicAngle
│  • Map signals → why-now triggers               │     (pain points,
│  • Tie pain points to facts from research       │      use cases,
│  • Generate signal-specific discovery questions │      objections,
│  • Anticipate objections                        │      questions)
└────────────────────────────┬────────────────────┘
                             ▼
┌─────────────────────────────────────────────────┐
│  WRITER                                         │  →  AccountBriefing
│  • 3-5 sentence executive summary               │     (final object,
│  • 4-6 line talk track                          │      profile +
│  • Stitch profile + signals + angle             │      signals + angle
│  • Enforce citation discipline                  │      + summary +
└────────────────────────────┬────────────────────┘     talk track)
                             ▼
┌─────────────────────────────────────────────────┐
│  CRITIC                                         │  →  Critique
│  • Score grounded / specific / actionable       │     (3 sub-scores +
│  • List issues + suggested fixes                │      issues +
│  • Pass = all three ≥ 0.7                      │      overall_pass)
└────────────────────────────┬────────────────────┘
                             ▼
                  AccountBriefing → JSON + Markdown
                             │
                             ▼
                  External LLM-as-judge rubric (6 axes)
```

Every arrow above is a Pydantic-typed object handoff. There is no free-form text passed between agents.

## Quick start

```sh
# 1. Create a Python 3.13 venv (CrewAI doesn't support 3.14 yet)
uv venv .venv --python 3.13
uv pip install --python .venv\Scripts\python.exe -r requirements.txt

# 2. Configure
copy .env.example .env
# Edit .env: set OPENAI_API_KEY, TAVILY_API_KEY, SEC_USER_AGENT (your name + email)

# 3. Generate one briefing
python -m src.cli research \
    --company "Microsoft Corporation" \
    --domain microsoft.com \
    --offering "AI observability platform for production LLM apps" \
    --context "Discovery call with VP of AI Platform"

# 4. Run the full eval (5 briefings, ~$0.50, ~8 min)
python -m src.cli eval

# 5. Print a saved briefing
python -m src.cli show output/Microsoft_Corporation.json
```

The CLI is `--verbose` aware for DEBUG logs. Every briefing produces both a JSON (machine-readable) and Markdown (human-readable) file in `output/`.

## Project layout

```
src/
├── cli.py                     # research / eval / show
├── config.py                  # typed Settings from .env
├── models.py                  # Pydantic schemas (the type system the agents share)
├── tools/
│   ├── sec_edgar.py           # CrewAI BaseTool: 10-K Item 1 lookup
│   └── tavily_search.py       # CrewAI BaseTools: web search + recent news
├── agents/
│   └── factory.py             # 6 agents with production guardrails
├── crew.py                    # Sequential pipeline + Pydantic handoffs
└── eval/
    ├── golden.py              # 5 real companies × 5 seller offerings
    ├── rubric.py              # External LLM-as-judge (6-axis Pydantic)
    └── runner.py              # Batch eval + JSON results writer
```

## Production design choices

1. **Sequential over hierarchical.** CrewAI's auto-manager has known coordination issues. We make the pipeline explicit so every handoff is a typed object the next agent receives directly.
2. **Pydantic everywhere.** Each Task uses `output_pydantic=...` so we never have to parse free-form prose. Failed parses surface immediately.
3. **Per-agent caps.** `max_iter=12`, `max_rpm=30`, `max_execution_time=180s` on every worker. Without these CrewAI agents can run unbounded.
4. **Workers don't delegate.** `allow_delegation=False` on the workers — delegation is a top-1% feature and degrades determinism. Sequential context handoff is enough.
5. **Tools wrap tested code.** SEC EDGAR fetcher reuses the production-tested module from project 03. Tavily wrappers use Pydantic args validation, not free-form strings.
6. **External judge separate from in-crew critic.** The in-crew critic gives the writer a chance to revise. The external rubric scores the final shipped briefing. Different prompts, different temperatures (0.0 for the judge).
7. **Telemetry off by default.** `OTEL_SDK_DISABLED=true` and `CREWAI_TRACING_ENABLED=false` in `.env` — opt in if you want CrewAI's hosted traces.

## Inspiration (motivation only — no code copied)

- [`crewAIInc/crewAI-examples`](https://github.com/crewAIInc/crewAI-examples) (read-only archive)
- [`hollaugo/crewai-sales-report-generator`](https://github.com/hollaugo/crewai-sales-report-generator)
- Mark AI's [CrewAI production architecture series](https://markaicode.com/architecture/crewai-agent-architecture/)
- The [`towardsdatascience.com` analysis of hierarchical-manager pitfalls](https://towardsdatascience.com/why-crewais-manager-worker-architecture-fails-and-how-to-fix-it/) — informed the decision to use sequential
- Real B2B sales workflows at SaaS companies

## Status

- [x] Six-agent sequential crew with explicit Pydantic handoffs
- [x] SEC EDGAR + Tavily Web + Tavily News tools (typed args)
- [x] Production guardrails: max_iter, max_rpm, max_execution_time
- [x] AccountBriefing → JSON + Markdown
- [x] In-crew QA critic (grounded / specific / actionable)
- [x] External LLM-as-judge rubric over 6 axes
- [x] 5-company golden set across 5 industries
- [x] End-to-end verified: 5/5 briefings completed, avg score 0.78, 1.00 facts accuracy, 97 s/briefing
- [ ] Async kickoff for batch processing
- [ ] Slack / email integration for delivery
- [ ] HubSpot / Salesforce CRM lookup tool
- [ ] Streamlit demo UI showing the pipeline live
- [ ] Re-run loop when critic fails (revise → re-critique)
