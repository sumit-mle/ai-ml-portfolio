# 02 — Agentic RAG Research Assistant (Pharma Medical Affairs)

A research agent for **pharma medical-affairs literature review**. A medical-affairs scientist receives an inquiry from a healthcare professional ("what does the latest evidence say about [drug] for [indication / adverse event]?"), and the agent:

1. **Plans** a focused PubMed-style search query
2. **Retrieves** relevant abstracts (built-in synthetic sample, or live PubMed via NCBI E-utilities)
3. **Generates** a grounded answer with PMID citations
4. **Reflects** on the answer (LLM-as-judge scores grounded / cited / complete)
5. **Loops back** to retrieve more evidence if the answer is incomplete — capped at a configurable iteration budget

The reflection loop is the differentiator. In medical affairs, hallucinations are a regulatory risk, not just a UX bug, so the agent self-checks before finalizing.

## The business problem

Medical-affairs scientists at pharma companies field inquiries from HCPs (oncologists, cardiologists, primary-care physicians) asking for the current evidence on safety, efficacy, dosing, drug-drug interactions. A single inquiry can take **2–4 hours of manual literature review**: search PubMed, screen titles, read abstracts, draft a response, fact-check citations.

This project compresses the first pass to **~2 minutes**: the agent assembles a draft response with verifiable citations the scientist reviews and edits, instead of starting from a blank page.

### Impact metrics this project tracks

| Metric | Manual baseline | Target with agent |
|--------|----------------|-------------------|
| Time per inquiry | 2–4 hours | 2–5 minutes draft + 15–30 min review |
| Citation auditability | Variable | 100% of claims carry [PMID] tags |
| Coverage of evidence | Search-skill dependent | Reflection loop probes for gaps |
| Honest abstention | Pressure to answer | Explicit "literature does not address" path |

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| State machine | **LangGraph** | Reflection loop is a state machine; conditional edges fit naturally |
| Retrieval | **LlamaIndex** | Cleaner node abstractions; easy to swap in sentence-window / auto-merging later |
| LLM | OpenAI `gpt-4o-mini` | Cheap, plenty good for medical-affairs drafting |
| Embeddings | OpenAI `text-embedding-3-small` | Match project 01 |
| Corpus | Built-in synthetic sample + live **PubMed** (NCBI E-utilities) | License-clear, free |
| Eval | Inline metrics (citation precision/recall, must-mention, honest-abstain) | No ragas dependency |

## Dataset

- **Built-in sample**: 8 synthetic abstracts with fake 9-digit PMIDs (prefixed `99...`) and explicit `[SYNTHETIC]` labels in titles. Lets the CLI run in seconds offline with zero risk of leaking misleading clinical content.
- **Live mode** (`--full --query "..."`): hits [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/) `esearch` + `efetch` for real PubMed abstracts. Free; no API key required (3 req/s); set `NCBI_API_KEY` for 10 req/s.

## Architecture

```
                     ┌───────────────┐
   question ────────▶│     plan      │  rewrite into PubMed-style query
                     └───────┬───────┘
                             ▼
                     ┌───────────────┐
                     │   retrieve    │  LlamaIndex top-k over abstracts
                     └───────┬───────┘
                             ▼
                     ┌───────────────┐
                     │   generate    │  grounded draft, [PMID] citations
                     └───────┬───────┘
                             ▼
                     ┌───────────────┐
                     │    reflect    │  LLM judge: grounded / cited / complete
                     └───────┬───────┘
                             │
              passing? ──────┼─────── needs more evidence?
                  │                       │
                  ▼                       ▼
              finalize                ┌───────┐
                                      │replan │  follow-up query
                                      └───┬───┘
                                          │
                                          └──▶ retrieve (loops, budget capped)
```

## Quick start

```sh
# Built-in sample — runs in seconds, no network
python -m src.cli ask --question "What does the evidence show about cardiovascular outcomes with SGLT2 inhibitors in type 2 diabetes?"

# Live PubMed
python -m src.cli ask --full --query "semaglutide weight loss randomized trial" --question "What weight reduction does semaglutide achieve in adults with obesity?"

# Cache a PubMed search to disk (sanity check)
python -m src.cli ingest --query "apixaban elderly atrial fibrillation" --retmax 20

# Run the golden Q/A set on the synthetic sample
python -m src.cli eval
```

## Eval

The golden set (`src/eval/golden.py`) has 9 questions exercising:
- 4 single-hop (one relevant abstract)
- 2 multi-hop (require fusion of two abstracts)
- 1 single-hop with non-trivial keyword match
- 1 single-hop on a less-discussed drug class
- 1 deliberately unanswerable (probes honest abstention)

**Verified results on built-in sample (`gpt-4o-mini`, top_k=5, max_iterations=2):**

| Metric | Score | Notes |
|--------|------:|-------|
| context_recall | 1.00 | every gold PMID surfaced in retrieval |
| citation_recall | 1.00 | every gold PMID cited in the final answer |
| must_mention_hit | 1.00 | answers contain expected key facts |
| honest_abstain_rate | 1.00 | unanswerable question correctly abstains |
| context_precision | 0.33 | top-5 over 8 docs is wide; reranking is the next obvious upgrade |
| avg_iterations | 1.11 | reflection loop only fires when needed |

See [`results/README.md`](./results/README.md) for the per-question breakdown and findings.

## Project layout

```
src/
├── cli.py                          # ask / ingest / eval
├── shared/
│   ├── corpus.py                   # built-in sample + PubMed E-utilities loader
│   ├── retriever.py                # LlamaIndex VectorStoreIndex wrapper
│   └── llm.py                      # OpenAI defaults + system prompts
├── agent/
│   └── graph.py                    # LangGraph state machine (plan/retrieve/generate/reflect/replan)
└── eval/
    ├── golden.py                   # 9-question golden set
    ├── metrics.py                  # citation recall, must-mention, abstain
    └── runner.py                   # runs the agent over the golden set
```

## Inspiration (motivation only — no code copied)

- [`langchain-ai/langgraph`](https://github.com/langchain-ai/langgraph) reflection examples
- [`run-llama/llama_index`](https://github.com/run-llama/llama_index) retrieval primitives
- Self-RAG / CRAG / Reflection-RAG literature
- The 2024 NEJM / JAMA discussions on LLMs in medical-information services

## Status

- [x] LangGraph state machine: plan / retrieve / generate / reflect / replan / finalize
- [x] LlamaIndex VectorStoreIndex retriever
- [x] PubMed E-utilities live loader
- [x] Golden Q/A set with single-hop, multi-hop, and unanswerable cases
- [x] Inline eval metrics (citation recall, must-mention, honest abstain)
- [x] End-to-end verified against OpenAI on sample and live PubMed
- [ ] Cross-encoder reranking (lift `context_precision` from 0.33)
- [ ] LangSmith tracing wiring
- [ ] LangGraph checkpointing for resumable long-running runs
- [ ] Streamlit UI showing the trace step by step
