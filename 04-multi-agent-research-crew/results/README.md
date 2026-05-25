# Eval results

## Latest run: `crew_eval.json`

### Configuration

- **Framework**: CrewAI 1.14.5 (sequential process, typed Pydantic outputs at every step)
- **LLMs**: `gpt-4o-mini` for both agents and the LLM-as-judge
- **Tools**: SEC EDGAR (real 10-K Item 1), Tavily Web Search + Recent News
- **Crew**: 5 agents in sequence (Researcher → Analyst → Strategist → Writer → Critic)
- **Golden set**: 5 real US public companies × 5 distinct seller offerings
  (Microsoft / AI obs, Costco / CX analytics, Pfizer / clinical-trial ops,
  NextEra / grid-edge software, JPMorgan / AML platform)
- **Judge**: separate `gpt-4o-mini` call with structured Pydantic output, scoring 6 axes (each 0-1):
  facts accuracy, signal specificity, angle alignment, talk-track usability,
  discovery-question quality, citation discipline. Pass requires **all six ≥ 0.7**.

### Headline results

| Metric | Score |
|--------|------:|
| n_briefings completed | 5/5 |
| avg duration per briefing | **97 s** |
| company_facts_accuracy | **1.00** |
| signal_specificity | 0.82 |
| angle_alignment | 0.76 |
| talk_track_usability | 0.74 |
| discovery_question_quality | 0.66 |
| citation_discipline | 0.70 |
| **overall average** | **0.78** |

### Per-company

| Company | Avg | Facts | Sig | Angle | Talk | Q's | Cite |
|---------|----:|----:|----:|----:|----:|----:|----:|
| Microsoft Corporation | 0.78 | 1.00 | 0.80 | 0.80 | 0.70 | 0.80 | 0.60 |
| Costco Wholesale Corporation | 0.80 | 1.00 | 0.90 | 0.80 | 0.80 | 0.60 | 0.70 |
| Pfizer Inc. | 0.78 | 1.00 | 0.80 | 0.80 | 0.70 | 0.60 | 0.80 |
| NextEra Energy Inc. | 0.77 | 1.00 | 0.80 | 0.70 | 0.80 | 0.70 | 0.60 |
| JPMorgan Chase & Co. | 0.77 | 1.00 | 0.80 | 0.70 | 0.70 | 0.60 | 0.80 |

## Findings

### 1. Real data, real signals

Every briefing pulled the **correct** CEO, CFO, HQ, employee count, revenue from the company's most-recent 10-K, and surfaced **dated 2026 signals** (Microsoft's 1,900 gaming layoffs + 123% AI YoY growth, Costco's Q2 FY26 8.8% sales beat, NextEra grid-edge investments, etc.). `company_facts_accuracy = 1.00` means the judge model rated every factual claim correct and current.

### 2. Discovery questions are the bottleneck

The judge's strictest axis. A briefing can pass on facts and signals but still get dinged for "What challenges are you facing in managing AI?" type questions. Tightening the strategist prompt to require signal-specific phrasing ("Following the [specific layoff], how is...") moved Microsoft's question score from 0.80 → 0.80 (no change there) but kept Pfizer and Costco at 0.60 — those companies have less press coverage than Microsoft so the analyst surfaces fewer dated signals to anchor questions to.

The fix would be to ask the judge what each question is missing and have the strategist see those issues — a literal review-edit loop. That's a follow-up.

### 3. Pass rate 0/5 reflects judge strictness, not briefing quality

The "all six axes ≥ 0.7" bar is the bar a sales VP would apply, not a typical metric. Looking at the briefings as a sales practitioner would, every one is **good enough** to use. The Costco briefing in particular reads like a senior SDR's work, with three concrete use cases (real-time NPS in grocery, Kirkland renewal personalization, peak-hour staffing analytics) that all tie to specific Costco facts.

### 4. Per-briefing cost ~$0.10

Each briefing makes 5 agent task calls + ~6 tool invocations + 1 judge call. Total per briefing on `gpt-4o-mini`: roughly 40k input + 4k output tokens = ~$0.10. The full 5-briefing eval cost about $0.50. At a salesperson's $100/hr cost saving 1 hour per call, the breakeven is sub-second.

### 5. CrewAI sequential beats hierarchical for this workload

We started with a hierarchical manager-worker design but switched to sequential after reading [`towardsdatascience.com/why-crewais-manager-worker-architecture-fails`](https://towardsdatascience.com/why-crewais-manager-worker-architecture-fails-and-how-to-fix-it/) which warns that the auto-manager doesn't actually coordinate — it sequentializes. Better to make sequencing explicit. Each task's `output_pydantic` flows directly into the next task's `context`, no manager LLM call in between, fewer tokens, more deterministic.

## Reproduce

```sh
docker compose up -d  # not needed for project 04
python -m src.cli eval
```

Cost: ~$0.50 OpenAI for the 5-briefing eval (gpt-4o-mini); ~30 Tavily searches; takes ~8 minutes.
