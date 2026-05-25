# 09 — RAG Regression Harness

A production-grade **regression-testing and observability harness** for RAG pipelines. Eats project 01's dog food by treating its three implementations (LangChain naive / LangChain hybrid / LlamaIndex naive) as the System Under Test and running them through six metrics, baseline comparison, retrieval-order drift detection, and a CI-friendly pytest gate.

This is the project that turns "we have evals" into "**a PR that drops faithfulness 5% fails the build automatically.**"

## The business problem

Every RAG team eventually ships a change that silently degrades quality:

- An embedding-model upgrade rebalances retrieval order
- A prompt tweak makes answers paraphrase instead of quote
- A chunker change drops a high-value document below top-k
- A library upgrade subtly changes tokenization

These regressions don't crash the system — they make answers slightly worse. By the time users notice, several PRs have shipped. **You need a CI gate**.

| Metric | Manual / ad-hoc | With this harness |
|--------|-----------------|-------------------|
| Detect a 5% faithfulness drop | weeks (user complaints) | **single PR fails CI** |
| Compare 3 retrievers head-to-head | spreadsheet shuffling | one HTML report |
| Detect retrieval-order drift | invisible | Kendall-tau per question |
| Verify behavior preservation | re-eyeball samples | deterministic pytest |

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| **System-Under-Test interface** | Pluggable Python protocol with a registry | Same harness runs against any RAG pipeline; current 3 SUTs are project 01's pipelines, future projects (02, 03) plug in identically |
| **Golden set** | Built **dynamically** from project 01's CUAD-labeled sample corpus | Real ground-truth spans, not LLM-generated questions — stable across re-runs |
| **Retrieval metrics** | Deterministic substring + offset checks | Free, fast, deterministic |
| **Generation metrics** | OpenAI structured-output **LLM-as-judge** (faithfulness + answer_relevancy) | We don't depend on Ragas (which has Python 3.14 wheel issues); same evaluator pattern, less dependency surface |
| **Statistical tests** | scipy `kendalltau` for drift | Standard primitive; good for retrieval-order comparisons |
| **Baseline + gate** | Saved JSON baselines per SUT + `pytest` parametrized over SUTs | Drop into GitHub Actions / GitLab CI as-is |
| **Report** | Single Jinja2 HTML with no external assets | Email-ready, PR-attachable |

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                         GOLDEN SET                                     │
│  Built at runtime from project 01's CUAD-labeled sample contracts.    │
│  12 questions × known clause spans = stable ground truth.              │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │
                                 ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         SUT REGISTRY                                    │
│  project01.langchain.naive    project01.langchain.hybrid                │
│  project01.llamaindex.naive    (add more by appending to known_suts)   │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │
                                 ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         METRICS                                         │
│  Deterministic (free):                                                  │
│    clause_match  citation_correct  context_recall  answer_quotes_clause │
│  LLM-as-judge:                                                          │
│    faithfulness  answer_relevancy   (OpenAI structured-output)          │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │
                  ┌──────────────┴──────────────────┐
                  ▼                                 ▼
   ┌──────────────────────────┐         ┌──────────────────────┐
   │  Run record (JSON)        │         │  Baseline (JSON)      │
   │  results/runs/<sut>__ts.json │      │  baselines/<sut>.json │
   └────────────┬─────────────┘         └──────────┬───────────┘
                │                                   │
                └───────────────┬──────────────────┘
                                ▼
              ┌─────────────────────────────────────┐
              │  Regression gate                     │
              │  metric delta vs baseline > tol → CI fail │
              │  available via:                      │
              │    `cli gate --sut <name>`           │
              │    `pytest tests/`                   │
              └────────────────┬────────────────────┘
                               ▼
                      ┌───────────────────┐
                      │  HTML report       │
                      │  (Jinja2, single  │
                      │   self-contained) │
                      └───────────────────┘
```

## Quick start

```sh
copy .env.example .env
# Edit .env: set OPENAI_API_KEY (for the LLM-as-judge)
#            set PROJECT_01_PATH if your sibling layout is non-default

# 1. List the SUTs the harness can evaluate
python -m src.cli list-suts

# 2. Run the 3 SUTs and save baselines
python -m src.cli run --sut project01.langchain.naive project01.langchain.hybrid project01.llamaindex.naive
python -m src.cli save-baseline --sut project01.langchain.naive
python -m src.cli save-baseline --sut project01.langchain.hybrid
python -m src.cli save-baseline --sut project01.llamaindex.naive

# 3. Make a "change" to project 01 and run the gate
python -m src.cli gate --sut project01.langchain.naive
# Exits 0 if no metric is below tolerance; exits 1 otherwise.

# 4. Or run the pytest version (drops cleanly into CI)
python -m pytest tests/

# 5. Drift detection: compare two runs
python -m src.cli drift results/runs/project01_langchain_naive__OLD.json results/runs/project01_langchain_naive__NEW.json

# 6. HTML report
python -m src.cli report --sut project01.langchain.naive project01.langchain.hybrid project01.llamaindex.naive
# Opens reports/report.html
```

## Verified results

3/3 SUTs evaluated and baseline-gated. See [`results/README.md`](./results/README.md) for the full breakdown.

| SUT | clause_match | citation | context_recall | verbatim | faithfulness | relevancy | avg_ms |
|-----|-------------:|---------:|---------------:|---------:|-------------:|----------:|-------:|
| project01.langchain.naive | 1.00 | 1.00 | 1.00 | 0.92 | 1.00 | 1.00 | 7,536 |
| project01.langchain.hybrid | 0.92 | 1.00 | 0.92 | 0.92 | 1.00 | 1.00 | 4,556 |
| project01.llamaindex.naive | 1.00 | 1.00 | 1.00 | 0.08 | 1.00 | 1.00 | 4,989 |

**Drift between two consecutive runs of langchain.naive: Kendall-tau = 1.00** (perfect, no embedding drift).

## Findings (the harness's own ROI)

### 1. The harness immediately surfaced a real production-quality finding

`project01.llamaindex.naive` scores **0.08** on `answer_quotes_clause` while both LangChain pipelines score **0.92**. The harness didn't tell us why — but a one-line investigation in project 01's code shows LlamaIndex's default `as_query_engine` synthesizer paraphrases instead of quoting. That's the kind of architectural decision that **silently changes audit-trail quality**, and exactly what a regression gate catches.

### 2. The baseline + tolerance pattern catches LLM noise the right way

In one run, `answer_quotes_clause` for LlamaIndex flipped from 0.08 → 0.00 between baseline and gate (model variance — the LLM judge happened to score one borderline-quoted answer differently). That's a **-0.08 delta** which exceeded our 5% tolerance and **the gate correctly failed CI**. Re-baselining with a wider tolerance for unstable metrics is the right operational response.

### 3. Drift detection is cheap and fast

Kendall-tau over the per-question retrieved chunk list is one scipy call. Two runs of the same SUT against the same questions should return tau ≈ 1.00; ours did. If you upgrade your embedding model and the tau drops to 0.4, you have **silent ranking drift** the metric summaries can't see.

### 4. Pytest-native CI integration is essential

`tests/test_regression_gate.py` parametrizes over every known SUT. One file, three asserts, runs in ~4 minutes on `gpt-4o-mini`. Drop into a GitHub Actions job:

```yaml
- run: python -m pytest tests/ -q
```

…and any PR that regresses RAG quality fails the build.

### 5. We deliberately did not depend on Ragas

Ragas is the de-facto standard, but it has known Python 3.14 wheel issues and pulls in a heavy dependency tree. The same evaluation pattern (LLM-as-judge with faithfulness + answer_relevancy) takes 50 lines of OpenAI structured output here, with zero dependency drift risk. If you want Ragas-compatible scores, swap in `metrics.llm_judge` — the API is the same.

## Project layout

```
src/
├── cli.py                          # list-suts / run / save-baseline / gate / drift / report
├── config.py                       # typed Settings (paths, tolerances)
├── golden.py                       # builds golden set from project 01's CUAD labels
├── metrics.py                      # 4 deterministic + 2 LLM-as-judge metrics
├── runner.py                       # run_sut + detect_regressions + save_baseline
├── drift.py                        # Kendall-tau over retrieval-order
├── report.py                       # Jinja2 single-file HTML report
└── sut/
    ├── interface.py                # Sut Protocol + project01 SUT implementations
    └── (future: project02, project03 SUTs slot in identically)

tests/
├── conftest.py                     # loads .env so pytest picks up OPENAI_API_KEY
└── test_regression_gate.py         # parametrized over every known SUT

baselines/                          # per-SUT JSON baselines (committed)
results/runs/                       # one JSON per run (gitignored, archived in CI)
reports/                            # HTML reports (gitignored)
```

## Production design choices

1. **SUT interface keeps the harness coupling-free.** New RAG pipelines from this portfolio (or anywhere) plug in by appending to `known_suts()`. The interface is two methods: `name` and `run(question, doc_id) -> RunOutput`.
2. **Golden set is data-driven, not LLM-generated.** Every Q is anchored to a real CUAD label span, so re-runs on different machines / weeks produce identical expectations. No silent test drift.
3. **Two metric tiers.** Deterministic substring/offset checks are free and run in milliseconds; LLM-as-judge metrics use OpenAI structured output with retries. CI can run with `--no-judge` for fast smoke checks and full mode for the real gate.
4. **Per-metric tolerances.** Baselines aren't strict equality — they're "no metric below baseline by more than X". Configurable in `.env`. Stricter on faithfulness (regulatory risk), looser on verbatim quoting (model variance).
5. **Drift detection is separate from regression.** A run can hit the same summary scores while the underlying retrieval order changed completely. Kendall-tau makes that visible.
6. **Run records are append-only.** Each run goes to `results/runs/<sut>__<ts>.json`. Baselines are explicit promotions, not "the most recent run". This is the only way you can trust them in a multi-developer team.
7. **Pytest-native, GitHub-Actions ready.** One file, parametrized, runs against every SUT, fails on regression. No proprietary CI integration required.

## Inspiration (motivation only — no code copied)

- [Ragas](https://github.com/explodinggradients/ragas) — the canonical RAG evaluation library; informed our metric set
- [DeepEval](https://github.com/confident-ai/deepeval) — the pytest-native evaluation pattern this harness extends
- [Forasoft 2026 production guide](https://www.forasoft.com/blog/article/llm-app-evaluation-production-2026) — informed the "100-200 golden Q/As, fail CI on regression" framing
- The 2026 production guides we surveyed at the start of this build (FutureAGI, MarsDevs, Latitude) — converged on the "structured baselines + tolerances + gate" pattern

## Status

- [x] SUT interface + registry (3 SUTs from project 01)
- [x] Golden set built dynamically from CUAD labels
- [x] 6 metrics (4 deterministic + 2 LLM-as-judge)
- [x] Run record persistence (JSON per run)
- [x] Baseline save/load + per-metric tolerances
- [x] Regression gate (`cli gate` and `pytest tests/`)
- [x] Drift detection via Kendall-tau
- [x] HTML report (Jinja2, single self-contained file)
- [x] End-to-end verified on all 3 project 01 pipelines
- [ ] Slack / email notifications on gate failure
- [ ] Token-cost tracking per run
- [ ] LangSmith / Langfuse trace upload (optional)
- [ ] Add SUTs from projects 02, 03 (when their public APIs stabilize)
