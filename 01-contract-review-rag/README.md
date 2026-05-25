# 01 — Contract Review Co-Pilot (RAG)

An AI co-pilot for **M&A contract review**. Given a commercial contract and a checklist question (change-of-control, anti-assignment, exclusivity, MFN, non-compete, indemnification cap, etc.), the system retrieves the relevant clause and produces an answer with citations a lawyer can verify.

Two parallel implementations — **LangChain** and **LlamaIndex** — share the same corpus, the same eval harness, and the same questions, so you can compare both stacks on the same problem.

## The business problem

In M&A due diligence, a buyer's law firm reviews every material contract the target company has signed. A mid-market deal can have 200–2,000 contracts, each checked for the same ~40 issues. At ~30–60 minutes of associate time per contract, this is $100K–$200K of manual review per deal.

This project automates the first pass: surface the relevant clause, label it, cite it, and let the associate verify in seconds instead of hunting through a PDF.

### Impact metrics this project tracks

| Metric | Manual baseline | Target with co-pilot |
|--------|----------------|----------------------|
| Time per contract | 30–60 min | 3–5 min (verify only) |
| Coverage | Senior cherry-picks | 100% of contracts get the same checklist |
| Citation auditability | Inconsistent | Every flag links to clause + source span |
| Issue recall on key clauses | ~85% (human) | Tracked vs CUAD ground truth |

## Dataset

[**CUAD v1**](https://huggingface.co/datasets/theatticusproject/cuad) — the Contract Understanding Atticus Dataset. 510 commercial contracts, 13,000+ lawyer-annotated clause spans across 41 categories (license grants, change of control, MFN, exclusivity, etc.). CC-BY-4.0.

CUAD's annotations are our **ground truth**: when the system says "this contract has a change-of-control clause", we score it against what real lawyers labeled.

The repo ships with a tiny built-in 3-contract sample so it runs in seconds before you download the full dataset.

## Techniques compared

| # | Technique | LangChain | LlamaIndex | Why it matters for legal |
|---|-----------|-----------|------------|--------------------------|
| 1 | Naive (top-k cosine) | ✅ | ✅ | Baseline |
| 2 | Hybrid (BM25 + dense, RRF) | ✅ | ✅ | "MFN", "change of control" need keyword match |
| 3 | Cross-encoder reranking | ✅ | ✅ | Lawyers need precision, not "close enough" |
| 4 | Multi-query / query rewriting | ✅ | — | Concept queries don't match contract phrasing |
| 5 | HyDE | ✅ | — | "Is there exclusivity here?" |
| 6 | Sentence-window | — | ✅ | Show full clause for citation |
| 7 | Auto-merging / parent-document | — | ✅ | Retrieve obligation, return whole section |
| 8 | Hybrid fusion (QueryFusion) | — | ✅ | LlamaIndex-native fusion patterns |

LangChain handles the LCEL / multi-query / HyDE patterns naturally. LlamaIndex shines at hierarchical chunking and sentence-window retrieval. Picking one framework per technique reflects how production teams actually mix them.

## Architecture

```
question + contract ──▶ retriever ──▶ generator ──▶ answer + clause citation
                       (LC or LI)     (gpt-4o-mini)
                            │
                            └─ checked against CUAD labels in eval
```

## Quick start

```sh
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit .env: set OPENAI_API_KEY

# Run with the built-in 3-contract sample (no download, ~5s)
python -m src.cli ask --backend langchain --technique naive --question "Is there a change-of-control clause?"

# Same query through LlamaIndex
python -m src.cli ask --backend llamaindex --technique naive --question "Is there a change-of-control clause?"

# Hybrid retrieval (recommended for legal queries)
python -m src.cli ask --backend langchain --technique hybrid --question "What is the indemnification cap?"

# Download the full CUAD and re-index
python -m src.cli ingest --full
```

## Eval

```sh
python -m src.cli eval --backend langchain --technique naive
python -m src.cli eval --backend langchain --technique hybrid
python -m src.cli eval --backend llamaindex --technique naive
```

Eval metrics:
- **Clause-match accuracy** — does the retrieved span overlap the CUAD-labeled span?
- **Answer faithfulness** — substring check that the answer quotes the retrieved span
- **Citation correctness** — does the cited contract / clause id match the answer?

Results land in `results/<backend>__<technique>.json` and aggregate into `results/README.md`.

## Project layout

```
src/
├── cli.py                           # ask / ingest / eval
├── shared/
│   ├── corpus.py                    # CUAD loader (sample + full) + Document model
│   ├── chunking.py                  # clause-aware splitter
│   ├── llm.py                       # OpenAI client (gpt-4o-mini default)
│   └── embeddings.py                # OpenAI text-embedding-3-small (with fastembed fallback)
├── langchain_impl/
│   ├── naive.py                     # LCEL: chunk → FAISS → retriever → prompt → LLM
│   ├── hybrid.py                    # EnsembleRetriever (BM25 + dense, RRF)
│   ├── rerank.py                    # ContextualCompressionRetriever + CrossEncoder
│   ├── multi_query.py               # MultiQueryRetriever (TODO)
│   └── hyde.py                      # HypotheticalDocumentEmbedder (TODO)
├── llamaindex_impl/
│   ├── naive.py                     # VectorStoreIndex.as_query_engine
│   ├── hybrid_fusion.py             # QueryFusionRetriever (TODO)
│   ├── sentence_window.py           # SentenceWindowNodeParser (TODO)
│   ├── auto_merging.py              # HierarchicalNodeParser + AutoMerging (TODO)
│   └── rerank.py                    # SentenceTransformerRerank (TODO)
└── eval/
    ├── golden.py                    # builds Q/A from CUAD labels
    ├── metrics.py                   # clause-match, faithfulness, citation
    └── runner.py                    # runs a technique over the golden set
```

## Inspiration (motivation only — no code copied)

- [`langchain-ai/rag-from-scratch`](https://github.com/langchain-ai/rag-from-scratch) — Lance Martin's progression of RAG techniques
- [`run-llama/llama_index`](https://github.com/run-llama/llama_index) examples on contract Q&A
- [`TheAtticusProject/cuad`](https://github.com/TheAtticusProject/cuad) — the dataset and original paper
- Public product demos from Harvey, Spellbook, Ironclad — what "production legal AI" looks like in 2026

## Status

- [x] Corpus loader with built-in sample + full CUAD downloader
- [x] Clause-aware chunker
- [x] LangChain naive RAG (LCEL)
- [x] LlamaIndex naive RAG
- [x] LangChain hybrid retrieval (BM25 + dense, RRF)
- [x] LangChain cross-encoder reranking
- [x] CUAD-grounded eval harness
- [ ] LangChain multi-query
- [ ] LangChain HyDE
- [ ] LlamaIndex sentence-window
- [ ] LlamaIndex auto-merging
- [ ] LlamaIndex hybrid-fusion
- [ ] LlamaIndex reranking
- [ ] Streamlit demo UI
- [ ] Results table in `results/README.md`
