# 03 — GraphRAG Knowledge Explorer (M&A Due Diligence)

Production-grade GraphRAG over real **SEC EDGAR** filings, backed by **Neo4j 5** with native vector search. Builds a typed knowledge graph of companies, executives, board members, subsidiaries, and ownership stakes from 10-K and DEF 14A filings, then answers multi-hop M&A due-diligence questions that flat vector RAG cannot.

Two retrievers compared on the same data:

- **Graph RAG**: Neo4j vector search → entity-link → multi-hop Cypher traversal → structured context → LLM
- **Vector RAG (baseline)**: Neo4j vector search → top-k chunks → LLM

## The business problem

In M&A due diligence, analysts trace relationships across hundreds of filings:

- "Which board members serve on both [acquirer] and [target]?"
- "What executives moved between competitors in the last 5 years?"
- "What subsidiaries does [company] disclose, and how do they connect to our portfolio?"
- "What's our supply-chain sanctions exposure two hops out?"

These are **multi-hop** questions: the answer requires connecting facts from 2–3 different filings. Vector RAG retrieves documents independently and hopes the LLM connects the dots. Graph RAG makes the connections explicit.

### Verified impact on real SEC data

Eval over a 6-company corpus (Apple, Microsoft, JPMorgan, Tesla, Berkshire, Alphabet — 12 filings, 612 relationships). Dynamic golden set built directly from the graph.

| Metric | Graph RAG | Vector RAG |
|--------|----------:|----------:|
| must_mention_hit | **0.80** | 0.30 |
| filing_recall | **0.70** | 0.45 |
| answered_rate | **1.00** | 0.60 |

Head-to-head: **5 graph wins, 0 vector wins, 5 ties**. Graph wins 1.00 vs 0.00 on board-overlap questions (the canonical multi-hop M&A query) and 1.00 vs 0.33 on subsidiary lookups.

## Stack

| Concern | Choice | Why |
|---------|--------|-----|
| Graph DB | **Neo4j 5.26 community + APOC** | Industry standard. Native vector index. Cypher is expressive enough for k-hop traversals. |
| Container | **Docker Compose** | Reproducible, one command to bring up the whole infra. |
| Vector index | **Native Neo4j vector index (cosine)** | Same store as the graph — no two-system synchronization. |
| Extraction | OpenAI `gpt-4o-mini` + Pydantic structured output | Validated JSON, no flaky regex. |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) | Cheap, plenty good for filing chunks. |
| Generation | OpenAI `gpt-4o-mini` via LangChain | Match project 01/02 for honest comparison. |
| Data source | **SEC EDGAR REST API** | Free, license-clear, every public US company. Rate-limited per SEC fair-access rules. |
| Resilience | tenacity retries on EDGAR / OpenAI / Neo4j | Network is a fact of life. |

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                         INGESTION PIPELINE                             │
│                                                                        │
│  cli ingest --cik 0000320193                                           │
│      │                                                                 │
│      ├─▶  SEC EDGAR submissions API  (list filings)                    │
│      ├─▶  fetch primary document      (cached on disk)                 │
│      ├─▶  HTML → text  +  section slicing (Item 1 / 1A / proxy)        │
│      ├─▶  LLM extraction (Pydantic ExtractionResult)                   │
│      │      • entities: Company, Person, Location                      │
│      │      • relations: HAS_SUBSIDIARY, EXECUTIVE_OF,                 │
│      │                   BOARD_MEMBER_OF, FORMER_EXECUTIVE_OF,         │
│      │                   OWNS_STAKE_IN, ACQUIRED, SUPPLIES, ...        │
│      ├─▶  sanitize  (drop role-strings, fix swapped directions,        │
│      │              canonicalize company name aliases)                 │
│      ├─▶  chunk + embed  (1200-char chunks, 1536-dim vectors)          │
│      └─▶  MERGE into Neo4j  (idempotent — re-run is safe)              │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        NEO4J KNOWLEDGE GRAPH                           │
│                                                                        │
│   (Company)─[FILED]→(Filing)←[PART_OF]─(Chunk{embedding})              │
│   (Person)─[EXECUTIVE_OF{role}]→(Company)                              │
│   (Person)─[BOARD_MEMBER_OF]→(Company)                                 │
│   (Person)─[FORMER_EXECUTIVE_OF{role}]→(Company)                       │
│   (Company)─[HAS_SUBSIDIARY|OWNS_STAKE_IN{pct}|ACQUIRED{year}]→(Company)│
│   (Company)─[SUPPLIES|PARTNER_WITH|ADVISED]→(Company)                  │
│   (Company)─[HEADQUARTERED_IN|SANCTIONED_BY]→(Location)                │
│                                                                        │
│   + native vector index on Chunk.embedding (cosine)                    │
└────────────────────────────────────────────────────────────────────────┘
                                   │
              ┌────────────────────┴────────────────────┐
              ▼                                         ▼
┌─────────────────────────────┐         ┌─────────────────────────────┐
│       GRAPH RAG             │         │        VECTOR RAG           │
│                             │         │                             │
│  query → embed → Neo4j      │         │  query → embed → Neo4j      │
│       vector top-k chunks   │         │       vector top-k chunks   │
│  → seed Companies           │         │  → context blocks           │
│  → name-link Q              │         │  → LLM                      │
│  → multi-hop Cypher         │         │                             │
│       (board overlaps,      │         └─────────────────────────────┘
│        subs, supply chains) │
│  → triples + chunk excerpts │
│  → LLM with citations       │
└─────────────────────────────┘
```

## Quick start

```sh
# 1. Bring up Neo4j + APOC
docker compose up -d
# Wait ~30s for the healthcheck to pass. Browser: http://localhost:7474

# 2. Configure credentials
copy .env.example .env
# Edit .env: set OPENAI_API_KEY and SEC_USER_AGENT (your name + email — SEC requires it)

# 3. Apply Neo4j schema (constraints + vector index)
python -m src.cli init

# 4. Ingest real SEC filings
python -m src.cli ingest --cik 0000320193 --forms "10-K,DEF 14A" --limit 1   # Apple
python -m src.cli ingest --cik 0000789019 --forms "10-K,DEF 14A" --limit 1   # Microsoft
python -m src.cli ingest --cik 0000019617 --forms "10-K,DEF 14A" --limit 1   # JPMorgan
python -m src.cli ingest --cik 0001318605 --forms "10-K,DEF 14A" --limit 1   # Tesla
python -m src.cli ingest --cik 0001067983 --forms "10-K,DEF 14A" --limit 1   # Berkshire
python -m src.cli ingest --cik 0001652044 --forms "10-K,DEF 14A" --limit 1   # Alphabet

python -m src.cli stats
# companies=119  persons=82  filings=12  chunks=414  relationships_total=612

# 5. Ask multi-hop questions
python -m src.cli ask --technique graph \
    --question "Which board members serve on both Apple Inc. and JPMorgan Chase & Co.?"

# 6. Run the side-by-side eval
python -m src.cli eval
```

The CLI is also `--verbose` for DEBUG logs and supports `reset --yes` to wipe the database.

## Cost note

A full ingest of 12 filings costs **~$0.20** in OpenAI usage (extraction + embeddings on `gpt-4o-mini` and `text-embedding-3-small`). Re-running ingest hits the on-disk extraction cache and costs $0. The eval costs another ~$0.05.

## SEC fair-access

Set `SEC_USER_AGENT` in `.env` per [SEC's policy](https://www.sec.gov/os/accessing-edgar-data). The fetcher self-throttles at ~8 req/s (under the 10 req/s ceiling) and retries with exponential backoff. The repo never hits SEC at request-time during a query — only during `ingest`.

## Schema design

Constraints (uniqueness + backing index in one):

```cypher
CREATE CONSTRAINT company_cik       FOR (c:Company)  REQUIRE c.cik IS UNIQUE;
CREATE CONSTRAINT company_name      FOR (c:Company)  REQUIRE c.name IS UNIQUE;
CREATE CONSTRAINT person_id         FOR (p:Person)   REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT filing_accession  FOR (f:Filing)   REQUIRE f.accession_no IS UNIQUE;
CREATE CONSTRAINT chunk_id          FOR (k:Chunk)    REQUIRE k.id IS UNIQUE;
CREATE CONSTRAINT location_name     FOR (l:Location) REQUIRE l.name IS UNIQUE;
```

Vector index on `Chunk.embedding` (1536-dim, cosine).

All writes use `MERGE` with `ON CREATE` / `ON MATCH` so re-ingestion is idempotent. The `filings` property on each relation is a list — re-ingesting the same filing keeps it in the list once; ingesting a NEW filing that asserts the same fact appends the new accession_no, building cross-filing evidence over time.

## Project layout

```
docker-compose.yml                  # Neo4j 5 + APOC, healthcheck, named volumes
src/
├── cli.py                          # init / reset / stats / ingest / ask / eval
├── shared/
│   ├── config.py                   # typed Settings from .env (lru_cached)
│   └── llm.py                      # OpenAI defaults + system prompts
├── db/
│   ├── driver.py                   # Neo4j driver singleton, retries
│   └── schema.py                   # constraints, vector index, stats, reset
├── ingest/
│   ├── sec_edgar.py                # EDGAR REST API + HTML→text + section slicing
│   ├── extract.py                  # LLM extraction with Pydantic structured output
│   ├── loader.py                   # idempotent MERGE writes + sanitization
│   └── pipeline.py                 # fetch + extract + load orchestration
├── retrieval/
│   ├── graph_rag.py                # vector search + multi-hop Cypher traversal
│   └── vector_rag.py               # baseline: pure vector RAG over the same chunks
└── eval/
    ├── golden.py                   # dynamic golden Q/A built from graph state
    ├── metrics.py                  # citation recall, must-mention, answered
    └── runner.py                   # both retrievers, head-to-head comparison
```

## Findings (see [`results/README.md`](./results/README.md) for full breakdown)

1. **Graph RAG wins 100% on board overlap**, 0% for vector RAG. The multi-board-membership query is the canonical multi-hop M&A question, and it's exactly where the graph structure pays for itself.
2. **Sanitization is essential.** A first-pass extraction over 6 proxy filings produced 35+ broken triples (role strings as entity names, source/target swapped, casing duplicates). The loader's sanitizer catches and fixes them before they reach the database.
3. **Company-name canonicalization across filings matters.** EDGAR's submissions API returns "MICROSOFT CORP" while the proxy text says "Microsoft Corporation" — without canonicalization those become two different graph nodes and downstream queries miss filings.
4. **Cached extractions make iteration cheap.** Once a filing's JSON is on disk, schema-fix iterations cost $0 (just Neo4j writes). This made the dev loop fast.

## Inspiration (motivation only — no code copied)

- [Microsoft GraphRAG](https://github.com/microsoft/graphrag) — community detection + summarization
- [LightRAG](https://github.com/HKUDS/LightRAG) — lightweight graph-based retrieval
- [neo4j/neo4j-graphrag-python](https://github.com/neo4j/neo4j-graphrag-python) — official Neo4j patterns
- Real SEC EDGAR research at PE / hedge-fund analysts

## Status

- [x] Docker Compose: Neo4j 5.26 community + APOC, healthcheck
- [x] Idempotent schema (constraints + vector index)
- [x] SEC EDGAR ingestion (10-K, DEF 14A) with on-disk caching
- [x] LLM extraction with Pydantic structured output
- [x] Sanitization: role-string filter, direction-flip for swapped relations, alias canonicalization
- [x] Graph RAG: vector search → entity-link → multi-hop Cypher
- [x] Vector RAG baseline over the same Neo4j chunk index
- [x] Dynamic golden Q/A built from graph state
- [x] Side-by-side eval with head-to-head comparison
- [x] End-to-end verified on 12 real SEC filings
- [ ] Community detection (Leiden via APOC) for graph summarization
- [ ] Streaming ingestion for large CIK lists
- [ ] Streamlit UI showing the subgraph for a query
- [ ] LangGraph agent on top (when to retrieve, when to answer)
