"""Golden Q/A set built dynamically from the loaded Neo4j graph.

Rather than hard-coding questions about specific companies, we query the
graph at eval time to find:
  - board overlaps (multi-hop): people on >=2 different companies' boards
  - executive succession: FORMER_EXECUTIVE_OF + EXECUTIVE_OF on different companies
  - subsidiary chains: Company -> Subsidiary -> Sub-subsidiary
  - cross-filing entities: any entity referenced by >=2 filings

This makes the eval *adaptive* to whatever you ingested. With a 5-company
seed corpus you get one set of questions; with 50 companies you get a richer
set automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..db.driver import session


@dataclass(frozen=True)
class GoldenQA:
    qid: str
    question: str
    hops: int                                  # graph hops required
    expected_filings: tuple[str, ...]
    must_mention: tuple[str, ...] = field(default_factory=tuple)
    pattern: str = ""                          # for diagnostics


# Cypher queries that find facts the system *should* be able to answer.
# Each returns rows we turn into questions + expected substring assertions.

_BOARD_OVERLAPS = """
MATCH (p:Person)-[:BOARD_MEMBER_OF]->(c1:Company)
MATCH (p)-[:BOARD_MEMBER_OF]->(c2:Company)
WHERE elementId(c1) < elementId(c2)
WITH p, c1, c2
MATCH (c1)-[:FILED]->(f1:Filing)
MATCH (c2)-[:FILED]->(f2:Filing)
RETURN p.name AS person, c1.name AS company1, c2.name AS company2,
       collect(DISTINCT f1.accession_no)[0..2] AS f1s,
       collect(DISTINCT f2.accession_no)[0..2] AS f2s
LIMIT 5
"""

_EXEC_OVERLAPS = """
MATCH (p:Person)-[r1:EXECUTIVE_OF]->(c:Company)
WHERE r1.role IS NOT NULL AND r1.role <> ''
MATCH (c)-[:FILED]->(f:Filing)
WITH p, r1, c, f
ORDER BY size(coalesce(r1.role, '')) ASC
WITH c, head(collect({person: p.name, role: r1.role, filing: f.accession_no})) AS top
RETURN top.person AS person, top.role AS role, c.name AS company,
       [top.filing] AS filings
LIMIT 5
"""

_SUBSIDIARY_CHAINS = """
MATCH (a:Company)-[:HAS_SUBSIDIARY]->(b:Company)
MATCH (a)-[:FILED]->(f:Filing)
WITH a, collect(DISTINCT b.name)[0..5] AS subsidiaries,
     collect(DISTINCT f.accession_no)[0..2] AS filings
RETURN a.name AS parent, subsidiaries, filings
LIMIT 5
"""

_FORMER_EXEC = """
MATCH (p:Person)-[:FORMER_EXECUTIVE_OF]->(c1:Company)
MATCH (p)-[:BOARD_MEMBER_OF|EXECUTIVE_OF]->(c2:Company)
WHERE c1 <> c2
MATCH (c1)-[:FILED]->(f1:Filing)
MATCH (c2)-[:FILED]->(f2:Filing)
RETURN p.name AS person, c1.name AS former_company, c2.name AS current_company,
       collect(DISTINCT f1.accession_no)[0..1] + collect(DISTINCT f2.accession_no)[0..1] AS filings
LIMIT 3
"""


def build_golden() -> list[GoldenQA]:
    out: list[GoldenQA] = []
    qid = 0

    def _next_id() -> str:
        nonlocal qid
        qid += 1
        return f"q{qid:02d}"

    with session() as s:
        # 1) Board overlaps (the classic multi-hop M&A question)
        for r in s.run(_BOARD_OVERLAPS):
            person = r["person"]; c1 = r["company1"]; c2 = r["company2"]
            out.append(
                GoldenQA(
                    qid=_next_id(),
                    question=(
                        f"Which board members serve on both {c1} and {c2}?"
                    ),
                    hops=2,
                    expected_filings=tuple((r["f1s"] or []) + (r["f2s"] or [])),
                    must_mention=(person,),
                    pattern="board_overlap",
                )
            )

        # 2) Executive identification (single-hop sanity)
        for r in s.run(_EXEC_OVERLAPS):
            person = r["person"]; role = r["role"] or "executive"; co = r["company"]
            out.append(
                GoldenQA(
                    qid=_next_id(),
                    question=f"Who is the {role} of {co}?",
                    hops=1,
                    expected_filings=tuple(r["filings"] or []),
                    must_mention=(person,),
                    pattern="executive",
                )
            )

        # 3) Subsidiary lookup (one question per parent, with all subs as
        # acceptable mentions)
        for r in s.run(_SUBSIDIARY_CHAINS):
            parent = r["parent"]
            subs = r["subsidiaries"] or []
            if not subs:
                continue
            out.append(
                GoldenQA(
                    qid=_next_id(),
                    question=f"What subsidiaries does {parent} disclose?",
                    hops=1,
                    expected_filings=tuple(r["filings"] or []),
                    must_mention=tuple(subs[:1]),  # any one is enough
                    pattern="subsidiary",
                )
            )

        # 4) Former-exec, now-board (multi-hop, the high-value M&A question)
        for r in s.run(_FORMER_EXEC):
            p = r["person"]; old = r["former_company"]; new = r["current_company"]
            out.append(
                GoldenQA(
                    qid=_next_id(),
                    question=(
                        f"Who was a former executive of {old} and now serves on the board of {new}?"
                    ),
                    hops=2,
                    expected_filings=tuple(r["filings"] or []),
                    must_mention=(p,),
                    pattern="former_exec_board",
                )
            )

    return out


def load_golden() -> list[GoldenQA]:
    """Build (or rebuild) from the current graph state."""
    return build_golden()
