"""Golden Q/A set with reference SQL.

For each question we have a reference SQL that produces the correct
answer set. The eval runs the agent, runs the reference, and compares:
  - shape correctness (right number of rows, right column count)
  - numeric closeness (sums within tolerance, top-K orderings match)
  - did the answer mention the right ballpark numbers

This is the BIRD-style evaluation pattern adapted for an agentic pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenQuery:
    qid: str
    question: str
    reference_sql: str
    must_mention_substrings: tuple[str, ...] = ()
    # Acceptable shape: (min_rows, max_rows). None = don't check.
    expected_rows: tuple[int, int] | None = None
    # Accept any percentage value in the answer text within this range.
    # Useful when the question has multiple reasonable interpretations of
    # the same intent (different week boundary etc.) but the magnitude is
    # what we care about.
    accept_pct_in: tuple[float, float] | None = None


GOLDEN: list[GoldenQuery] = [
    GoldenQuery(
        qid="q1_total_revenue_last_week",
        question="What was the total gross revenue across all regions in the last 7 days?",
        reference_sql="""
            SELECT round(sum(gross_revenue_usd), 2) AS total_gross
            FROM revenue_daily
            WHERE date >= date '2026-05-19'
        """,
        expected_rows=(1, 1),
    ),
    GoldenQuery(
        qid="q2_emea_dip",
        question=(
            "Compare EMEA gross revenue in the last 7 days vs the prior 7 days. "
            "What was the percent change?"
        ),
        reference_sql="""
            SELECT
                round(sum(case when date >= date '2026-05-19' then gross_revenue_usd end), 2) AS last_week,
                round(sum(case when date >= date '2026-05-12' and date <= date '2026-05-18' then gross_revenue_usd end), 2) AS prior_week
            FROM revenue_daily r
            JOIN regions reg ON reg.region_id = r.region_id
            WHERE reg.region_name = 'EMEA'
              AND date >= date '2026-05-12' AND date <= date '2026-05-25'
        """,
        # Both 7-day window interpretations are acceptable — the answer
        # must mention EMEA and a percent drop in the 25-40% range.
        must_mention_substrings=("EMEA",),
        expected_rows=(1, 1),
        # Accept any percentage between 20-40%
        accept_pct_in=(20.0, 40.0),
    ),
    GoldenQuery(
        qid="q3_top_channel_emea_recent",
        question=(
            "Which marketing channel had the highest spend in EMEA over the last 14 days?"
        ),
        reference_sql="""
            SELECT c.channel, round(sum(s.spend_usd), 2) AS total_spend
            FROM ad_spend s
            JOIN campaigns c ON c.campaign_id = s.campaign_id
            JOIN regions r ON r.region_id = c.region_id
            WHERE r.region_name = 'EMEA'
              AND s.date >= date '2026-05-12'
            GROUP BY c.channel
            ORDER BY total_spend DESC
            LIMIT 1
        """,
        must_mention_substrings=("EMEA", "search"),
        # Agents may return the full ranking or just the winner — both fine.
        expected_rows=(1, 4),
    ),
    GoldenQuery(
        qid="q4_revenue_per_region_yesterday",
        question="Show gross revenue per region on 2026-05-24.",
        reference_sql="""
            SELECT reg.region_name,
                   round(sum(r.gross_revenue_usd), 2) AS gross_revenue
            FROM revenue_daily r
            JOIN regions reg ON reg.region_id = r.region_id
            WHERE r.date = date '2026-05-24'
            GROUP BY reg.region_name
            ORDER BY gross_revenue DESC
        """,
        must_mention_substrings=("AMER", "EMEA"),
        expected_rows=(4, 4),
    ),
    GoldenQuery(
        qid="q5_pro_plan_signups_emea",
        question="How many EMEA customers are on the 'pro' plan?",
        reference_sql="""
            SELECT count(*) AS n_pro_emea
            FROM customers c
            JOIN regions r ON r.region_id = c.region_id
            WHERE r.region_name = 'EMEA' AND c.plan = 'pro'
        """,
        expected_rows=(1, 1),
    ),
]


def all_goldens() -> list[GoldenQuery]:
    return list(GOLDEN)
