# AI / ML / RAG / Agentic Portfolio

> Ten production-grade AI projects, each anchored to a real enterprise use case, built with the frameworks that actually ship in 2026, and measured against verified evals.

[![Python](https://img.shields.io/badge/python-3.13%20%7C%203.14-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/all_projects-evaluated-success)]()

> Built by **Sumit Kumar** — Machine Learning Engineer IV at Avalara · 7+ years building production AI across healthcare (Oracle Cerner), pharma analytics (ZS Associates), and global trade compliance (Avalara) · M.Sc. IIT Kharagpur

This is not a tutorial repo. Every project here:

- Solves a problem someone would pay to have solved (M&A diligence, pharma lit review, IT helpdesk, vendor compliance, marketing-mix analysis)
- Uses production-grade frameworks (LangGraph, LlamaIndex, CrewAI, FastMCP, Neo4j, OpenAI Realtime, Playwright, libcst)
- Ships with a real eval harness and verified numbers — no "trust me" results
- Includes the production patterns that separate a demo from an enterprise system: authentication, audit logs, rate limits, PII gates, regression detection, rollback on failure

**TL;DR for recruiters / hiring managers:** scroll to [the project table](#-projects). Each row links to a working repo with its own README, tests, and `results/`.

---

## 🎯 Projects

| # | Project | Headline result | Stack |
|---|---------|-----------------|-------|
| **01** | [Contract Review RAG](./01-contract-review-rag) | 8 retrieval techniques side-by-side on CUAD; LangChain hits 1.00 verbatim, LlamaIndex 0.08 (revealing default-synthesizer paraphrasing) | LangChain + LlamaIndex, FAISS, OpenAI |
| **02** | [Pharma Agentic RAG](./02-agentic-rag-research) | Reflection-loop agent over PubMed; **9/9 questions** with 1.00 citation recall and 1.00 honest-abstain on unanswerable | LangGraph + LlamaIndex, NCBI E-utilities |
| **03** | [GraphRAG over SEC EDGAR](./03-graphrag-knowledge-explorer) | Real Neo4j with 612 typed relations from 12 SEC proxy filings; **graph wins 1.00 vs vector 0.00** on board-overlap multi-hop | Neo4j 5.26 + APOC, Docker, sqlglot, OpenAI |
| **04** | [Sales Research Crew](./04-multi-agent-research-crew) | 5-agent CrewAI crew produces account briefings from real SEC + Tavily data; **1.00 facts accuracy**, 0.78 overall on 6-axis judge rubric | CrewAI 1.14, OpenAI structured output |
| **05** | [Legacy Modernization Agent](./05-langgraph-coding-agent) | LangGraph state machine modernizes Python codebases via libcst; **8/8 tests pass before AND after**, deterministic transformer + bounded repair loop | LangGraph, libcst, pytest |
| **06** | [Enterprise MCP Server](./06-mcp-tool-server-collection) | FastMCP server with auth, per-tool scopes, sqlglot SQL gate, PII masking, JSONL audit; **13/13 security tests pass**, both stdio + streamable-HTTP transports verified | FastMCP 3.3, DuckDB, sqlglot |
| **07** | [Voice Helpdesk Agent](./07-voice-agent-realtime) | OpenAI Realtime API speech-to-speech with browser UI, server-side identity gate, two-strike escalation, audit log; **5/5 scenarios pass** | OpenAI Realtime (`gpt-realtime`), FastAPI, SQLite |
| **08** | [Vendor Compliance Browser Agent](./08-browser-agent-autonomous) | Self-hosted vendor portal + deterministic Playwright extractor + browser-use autonomous path; **3/3 vendors** including MFA + missing-doc detection at 3.7s avg | Playwright, browser-use, FastAPI, reportlab |
| **09** | [RAG Regression Harness](./09-agent-evaluation-harness) | Eats project 01's dog food: 6 metrics, baseline + tolerance gating, Kendall-tau drift detection, **pytest 3/3 green** with the gate already catching real LLM-judge variance | OpenAI structured output, scipy, Jinja2, pytest |
| **10** | [Marketing Analytics Agent](./10-agentic-data-pipeline) | LangGraph NL→SQL with two-layer read-only enforcement and bounded repair loop on a marketing-mix warehouse; **5/5 BIRD-style execution-correctness** | LangGraph, DuckDB, sqlglot |

Total: **~7,500 lines of Python, 60+ tests, 10 working evals.** Every checkmark above is a number written into a `results/` JSON in the repo.

## 🧩 What's interesting about this portfolio (the unspoken)

1. **Every project ships with an eval that runs in CI.** Not "we tried it and it worked" — actual JSON files with metrics that anyone can rerun with `python -m src.cli eval`.
2. **Production controls show up early.** Project 06 has token auth + scope authorization + SQL parser gate + PII column-tag masking + audit log. Project 05 has HITL gates and automatic rollback when tests regress. Project 07 server-side enforces identity verification before privileged actions. These are the things every demo skips and every enterprise needs.
3. **Side-by-side framework comparisons.** Project 01 runs LangChain and LlamaIndex against the same questions and exposes a real architectural difference (LlamaIndex paraphrases by default, LangChain quotes verbatim — same OpenAI model). That's the kind of finding only honest evaluation surfaces.
4. **Real datasets, not toy ones.** CUAD legal contracts, real SEC EDGAR filings (Apple, Microsoft, JPMorgan, Tesla, Berkshire, Alphabet), live PubMed via NCBI E-utilities, NYC TLC taxi traffic, BIRD-style marketing-mix.
5. **Honest about limits.** Project 08's autonomous browser-use path got confused on simple HTML forms during testing — that's documented in the README, not hidden. Project 07 notes that OpenAI Realtime is not yet HIPAA-eligible. Project 04's pass rate is 0/5 on a strict 6-axis bar — explained, not glossed over.

## 🛠 Stack coverage

What this portfolio touches end-to-end:

- **RAG**: naive, hybrid (BM25 + dense + RRF), cross-encoder reranking, sentence-window, query rewriting, GraphRAG with multi-hop Cypher
- **Agent frameworks**: LangGraph (5 projects), CrewAI (1), browser-use (1), OpenAI Realtime (1)
- **Vector DBs / stores**: FAISS, LlamaIndex VectorStoreIndex, **Neo4j native vector index**
- **Graph**: NetworkX → Neo4j 5.26 + APOC + Cypher k-hop traversal
- **MCP**: FastMCP 3.3 with stdio + streamable-HTTP, OAuth-shaped auth interface
- **Voice**: OpenAI Realtime (`gpt-realtime`) over WebSocket bridge with browser AudioWorklet capture
- **Browser automation**: Playwright deterministic + browser-use autonomous
- **SQL safety**: sqlglot AST gate (DDL/DML rejection, multi-statement detection, LIMIT injection) — used in projects 06 and 10
- **Evaluation**: BIRD-style execution correctness, Kendall-tau retrieval drift, LLM-as-judge with Pydantic structured output, pytest CI gates
- **Observability**: structured JSONL audit logs in 4 projects (compatible with Splunk/Datadog/Loki)

## 📁 How each project is laid out

```
NN-project-name/
├── README.md            # business problem, architecture, verified results, design choices
├── requirements.txt
├── .env.example         # template — real secrets in .env (gitignored)
├── .gitignore
├── src/
│   ├── cli.py           # CLI entry: init / serve / ask / eval / show-* subcommands
│   └── ...              # project-specific modules
├── results/
│   ├── README.md        # eval breakdown + findings
│   └── *.json           # actual eval results, committed
└── (optional) tests/, docker-compose.yml, output/
```

Each project's `README.md` follows the same shape: business problem → stack → architecture diagram → quick start → verified results → design choices → status checklist.

## 🚀 Quick start

Pick any project and follow its `README.md`. All projects use the same shape:

```sh
cd NN-project-name
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit .env with your OPENAI_API_KEY (and any project-specific keys)
python -m src.cli eval
```

For the workspace as a whole, a single shared venv at `./.venv` works for projects 01–03, 05, 06, 09, 10. Projects 04, 07, 08 have their own dependency requirements detailed in each README.

## 🤝 Connect

- **GitHub**: [sumit-mle](https://github.com/sumit-mle)
- **Email**: sumit.kgpiit@gmail.com
- **Location**: Bengaluru, India
- Open an issue or DM if anything in here would be useful for your team — happy to walk through it.

## 📜 License

MIT — see [LICENSE](./LICENSE).
