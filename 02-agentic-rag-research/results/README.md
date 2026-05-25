# Eval results

Each JSON file in this directory is a full eval run: top-level `summary` block plus per-question `rows`.

## Latest run: `agentic_rag_v2.json`

Configuration:
- Model: `gpt-4o-mini` (chat) + `text-embedding-3-small` (embeddings)
- Corpus: 8 synthetic abstracts (built-in sample)
- Agent: LangGraph plan → retrieve → generate → reflect (max 2 iterations)
- top_k: 5

| Metric | Score |
|--------|------:|
| context_precision | 0.33 |
| context_recall | 1.00 |
| citation_recall | 1.00 |
| must_mention_hit | 1.00 |
| honest_abstain_rate | 1.00 |
| avg_iterations | 1.11 |
| n_questions | 9 |

### Per-question summary

| QID | Type | Iters | Cite recall | Must-mention | Note |
|-----|------|------:|------------:|-------------:|------|
| q1 | single-hop (SGLT2 + CV) | 1 | 1.00 | 1.00 | clean |
| q2 | single-hop (empagliflozin AKI) | 1 | 1.00 | 1.00 | clean |
| q3 | single-hop (GLP-1 pancreatitis) | 1 | 1.00 | 1.00 | clean after prompt fix |
| q4 | single-hop (semaglutide obesity) | 1 | 1.00 | 1.00 | clean |
| q5 | multi-hop (GLP-1: pancreatitis + obesity) | 1 | 1.00 | 1.00 | both PMIDs cited |
| q6 | multi-hop (apixaban: dosing + DOAC vs warfarin) | 1 | 1.00 | 1.00 | both PMIDs cited |
| q7 | single-hop (statin myopathy) | 1 | 1.00 | 1.00 | clean |
| q8 | single-hop (PCSK9 after statins) | 1 | 1.00 | 1.00 | clean after prompt fix |
| q9 | unanswerable (metformin) | 2 | n/a | 1.00 | reflection loop fired; honest abstain |

## Findings

### 1. The generator's "no clinical advice" guard was over-firing

First-pass v1 results showed a curious failure: q3 (GLP-1 / pancreatitis) and q8 (PCSK9 indications) returned "The retrieved literature does not address this question" *even though* the gold-relevant abstract was retrieved with high similarity. Both are phrased like clinical-recommendation questions ("are X associated with Y?", "when should X be considered?"), and the model was treating them as requests for clinical advice rather than evidence summarization.

**Fix:** tightened the generator system prompt to explicitly distinguish "reporting evidence" from "giving clinical advice", and constrained the abstain path to "NONE of the retrieved abstracts is on-topic". After the fix, citation_recall rose from 0.78 → 1.00 and must_mention_hit from 0.78 → 1.00, with no regression on the unanswerable case.

This is the kind of finding the explicit reflection loop makes visible: the trace shows the reflector flagging `grounded=0.0, cited=0.0` and trying a follow-up retrieval, but the generator stayed stuck in refusal mode. Without the trace this would have been much harder to diagnose.

### 2. Reflection loop fires only when needed

`avg_iterations = 1.11` across 9 questions: 8 questions finalized after one pass, 1 (the unanswerable q9) used both iterations. The reflector successfully distinguishes "good enough" from "needs more evidence" without burning the budget.

### 3. context_precision = 0.33 is the next obvious target

With top_k=5 and only 8 abstracts, every query retrieves 5 docs of which 1–2 are typically relevant. Precision is bounded mechanically. Adding cross-encoder reranking (the "rerank" technique from project 01) is the natural next upgrade; honest comparison would need a larger corpus to give precision room to move.

### 4. The unanswerable case is the most informative

q9 ("metformin monotherapy vs placebo for HbA1c reduction") has zero relevant abstracts in the synthetic corpus. The agent retrieves the closest-by-cosine docs (mostly SGLT2 and GLP-1), the generator follows the prompt and abstains, the reflector confirms, and the second iteration also abstains. This is exactly the desired behavior in a regulated setting — refusing to fabricate evidence is more valuable than producing a confident-sounding answer.

## Reproduce

```sh
python -m src.cli eval --label agentic_rag_v2
```

Cost: ~$0.01–0.02 of OpenAI usage per full eval pass (9 questions × ~2 LLM calls each on gpt-4o-mini).
