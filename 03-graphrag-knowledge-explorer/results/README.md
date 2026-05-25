# Eval results

## Latest run: `graphrag_vs_vector.json`

### Configuration

- **Graph DB**: Neo4j 5.26 community (Docker Compose) + APOC + native vector index
- **Models**: `gpt-4o-mini` for chat + extraction, `text-embedding-3-small` for embeddings (1536-dim, cosine)
- **Corpus**: 6 companies × 2 forms (10-K + DEF 14A) = **12 real SEC filings**
  - Apple, Microsoft, JPMorgan Chase, Tesla, Berkshire Hathaway, Alphabet
- **Graph state at eval time**:
  - 119 companies, 82 persons, 12 filings, 414 chunks, **612 typed relationships**
- **Golden set**: built dynamically from the loaded graph (not hard-coded)
- **Retrievers**:
  - Graph RAG: vector top-5 chunks → seed Companies → 2-hop Cypher traversal → triples + excerpts → LLM
  - Vector RAG: vector top-5 chunks → LLM (baseline)

### Head-to-head summary

| Metric | Graph RAG | Vector RAG |
|--------|----------:|----------:|
| n_questions | 10 | 10 |
| must_mention_hit | **0.80** | 0.30 |
| filing_recall | **0.70** | 0.45 |
| answered_rate | **1.00** | 0.60 |

| Outcome | Count |
|---------|------:|
| Graph wins | 5 |
| Ties | 5 |
| Vector wins | 0 |
| Graph win rate | **50%** |

### By question pattern

| Pattern | n | Graph RAG | Vector RAG | Gap |
|---------|--:|---------:|-----------:|----:|
| board_overlap (multi-hop) | 2 | **1.00** | 0.00 | +1.00 |
| executive (single-hop) | 5 | 0.60 | 0.40 | +0.20 |
| subsidiary | 3 | **1.00** | 0.33 | +0.67 |

## Findings

### 1. Graph RAG dominates exactly where it should

Board-overlap and subsidiary questions are where the graph structure pays for itself. Vector RAG can find a single filing that mentions a fact, but it cannot connect the same person across two filings. The graph's typed `BOARD_MEMBER_OF` edges trivially answer "who serves on both X and Y" with a 2-hop Cypher pattern.

Concrete example: **"Which board members serve on both Apple Inc. and JPMorgan Chase & Co.?"**
- Graph RAG: surfaces **Alex Gorsky** (correct, supported by both proxies)
- Vector RAG: returns "Not found in the available filings."

### 2. Vector RAG holds its own on single-hop questions, where it should

When the question is "Who is the CEO of [company]?", the relevant fact is in one chunk of one filing. Vector RAG retrieves it, the LLM extracts it, done. Graph still wins (0.60 vs 0.40) but the gap is small and partly an artifact of name variants ("Jamie Dimon" vs "James Dimon") that hit our must_mention metric inconsistently.

### 3. Sanitization is essential

The first-pass extraction produced 35+ broken triples per Berkshire proxy alone:
- Role strings as Person entity names: `Person="CEO"`, `Person="Senior Vice President of Software Engineering"`
- Source/target swapped: `Tesla, Inc. -[EXECUTIVE_OF]-> Elon Musk` instead of `Elon Musk -[EXECUTIVE_OF]-> Tesla, Inc.`
- Possessive variants: `Apple's Senior Vice President of Software Engineering` as a Person node

The loader's `_sanitize_extraction` step catches these by:
- Rejecting names that match role tokens or role prefixes
- Detecting corporate suffixes (Inc., Corp., LLC, ...) so company names are never mistaken for persons
- Flipping wrong-direction person relations rather than dropping them

After sanitization the graph is clean and queryable.

### 4. Company-name canonicalization across filings matters

EDGAR's submissions API returns names like "MICROSOFT CORP" and "JPMORGAN CHASE & CO" while the proxy text uses "Microsoft Corporation" and "JPMorgan Chase & Co.". Without canonicalization those become two separate graph nodes and downstream queries that look for `(Company {name: 'Microsoft Corporation'})-[:FILED]->(...)` find nothing.

`_canonicalize_name` plus an alias map for the demo fixes this. A production system would learn aliases from the data (LLM-driven entity resolution).

### 5. Cached extractions make iteration cheap

Each filing's extraction JSON is written to `data/extracted/<accession>.json`. After the first ingest run (which costs OpenAI), every subsequent `reset && ingest` only writes to Neo4j — schema and sanitizer changes can be tested in seconds, not minutes.

### 6. Open issues this run surfaced

- **Subsidiary edges are sparse from proxy alone** — proxies don't list subsidiaries; the 10-K Item 1 Business section does. Adding 10-Ks to the corpus pushed subsidiary scores to 1.00.
- **Name variants confuse must_mention metric** — "James Dimon" vs "Jamie Dimon" both refer to the same JPMC CEO. The metric should fuzzy-match or accept either, not require an exact substring.

## Reproduce

```sh
docker compose up -d
python -m src.cli init

# Ingest 6 companies, 12 filings (~$0.20 OpenAI usage)
for cik in 0000320193 0000789019 0000019617 0001318605 0001067983 0001652044; do
  python -m src.cli ingest --cik $cik --forms "10-K,DEF 14A" --limit 1
done

python -m src.cli stats
python -m src.cli eval
```
