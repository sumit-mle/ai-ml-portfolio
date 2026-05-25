# Results

Run on the built-in 3-contract sample (12 golden Q/A from CUAD-style labels).
Models: `gpt-4o-mini` for generation, `text-embedding-3-small` for retrieval,
`cross-encoder/ms-marco-MiniLM-L-6-v2` for reranking.

| Backend | Technique | Clause match | Citation correct | Verbatim quote |
|---------|-----------|-------------:|-----------------:|---------------:|
| LangChain  | naive   | 1.00 | 1.00 | 1.00 |
| LangChain  | hybrid  | 0.92 | 1.00 | 0.92 |
| LlamaIndex | naive   | 1.00 | 1.00 | 0.00 |

## Reading the numbers

- **Clause match** — did any retrieved chunk overlap the labeled clause span?
- **Citation correct** — did retrieval return at least one chunk from the right contract?
- **Verbatim quote** — did the generated answer include a substring of the labeled clause? Cheap proxy for faithfulness.

## Findings on the sample (and why this exercise is useful)

1. **LlamaIndex paraphrases by default.** Retrieval is perfect, but the default response synthesizer rewrites the clause instead of quoting it. A reviewer reading the answer can't verify the wording without going back to the source. Our LangChain pipeline uses an explicit "quote the clause and cite as [doc_id::section]" prompt and hits 100% verbatim.
2. **Hybrid retrieval lost a point on this sample.** With only 6 short contracts, BM25 noise can outvote the dense signal on conceptual queries ("non-compete provision"). Hybrid earns its keep on larger corpora and on rare-keyword queries; the next step is to re-run on the full CUAD set and watch the gap close.
3. **Both backends agree on which clauses exist.** That's the signal you actually want for an M&A first pass: independent retrievers converging on the same answer.

## How to reproduce

```sh
python -m src.cli eval --backend langchain  --technique naive
python -m src.cli eval --backend langchain  --technique hybrid
python -m src.cli eval --backend llamaindex --technique naive
```

Per-question JSON traces land in `results/<backend>__<technique>.json` next to this file.
