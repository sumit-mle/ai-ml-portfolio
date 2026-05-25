"""Catalog of tables + their LLM-facing descriptions.

The agent retrieves the relevant tables for a question via embedding-free
keyword scoring (the schema is small enough — 6 tables — that fancy
retrieval would be overkill). For larger warehouses, swap in a vector
store keyed on the descriptions.
"""
from __future__ import annotations

from dataclasses import dataclass

from .driver import cursor


@dataclass(frozen=True)
class TableInfo:
    name: str
    description: str
    keywords: tuple[str, ...]
    columns_hint: str = ""


CATALOG: dict[str, TableInfo] = {
    "regions": TableInfo(
        name="regions",
        description="Geographic regions our company operates in.",
        keywords=("region", "geography", "country", "amer", "emea", "apac", "latam"),
        columns_hint="region_id, region_name (AMER|EMEA|APAC|LATAM), country_count",
    ),
    "products": TableInfo(
        name="products",
        description="Catalog of SaaS plans and one-time services we sell.",
        keywords=("product", "plan", "saas", "service", "price", "category"),
        columns_hint="product_id, product_name, category (saas|services), list_price_usd",
    ),
    "customers": TableInfo(
        name="customers",
        description="One row per customer with signup date, plan tier, and home region.",
        keywords=("customer", "user", "signup", "plan", "tier", "free", "starter", "pro", "enterprise"),
        columns_hint="customer_id, region_id, signup_date, plan (free|starter|pro|enterprise)",
    ),
    "campaigns": TableInfo(
        name="campaigns",
        description="Marketing campaigns by region and channel with a date window and total budget.",
        keywords=("campaign", "marketing", "channel", "search", "social", "display", "video", "budget"),
        columns_hint=(
            "campaign_id, region_id, channel (search|social|display|video), "
            "started_at, ended_at, budget_usd"
        ),
    ),
    "ad_spend": TableInfo(
        name="ad_spend",
        description="Daily spend, impressions, and clicks per campaign.",
        keywords=("spend", "ad", "impressions", "clicks", "ctr", "cost", "daily"),
        columns_hint="date, campaign_id, spend_usd, impressions, clicks",
    ),
    "revenue_daily": TableInfo(
        name="revenue_daily",
        description="Daily revenue by region and product including refunds and customer counts.",
        keywords=(
            "revenue", "sales", "income", "refund", "gross", "net", "daily", "dip", "drop",
            "customer_count",
        ),
        columns_hint=(
            "date, region_id, product_id, customer_count, gross_revenue_usd, refunds_usd"
        ),
    ),
}


def render_full_schema() -> str:
    """Markdown-ish dump of every table for inclusion in prompts."""
    lines: list[str] = []
    for tbl in CATALOG.values():
        lines.append(f"- **{tbl.name}** — {tbl.description}")
        if tbl.columns_hint:
            lines.append(f"  Columns: {tbl.columns_hint}")
    return "\n".join(lines)


def render_partial_schema(table_names: list[str]) -> str:
    """Schema dump for a subset of tables (after retrieval)."""
    lines: list[str] = []
    for name in table_names:
        tbl = CATALOG.get(name)
        if tbl is None:
            continue
        lines.append(f"- **{tbl.name}** — {tbl.description}")
        if tbl.columns_hint:
            lines.append(f"  Columns: {tbl.columns_hint}")
    return "\n".join(lines) if lines else render_full_schema()


def select_tables(question: str, *, top_k: int = 4) -> list[str]:
    """Score each table by keyword overlap with the question. Cheap and works
    well for a small schema; replace with embedding similarity for large ones.
    """
    q = question.lower()
    scored: list[tuple[float, str]] = []
    for name, tbl in CATALOG.items():
        score = sum(1.0 for k in tbl.keywords if k in q)
        # Always boost a default for tables every analytic question hits
        if name in ("revenue_daily", "regions"):
            score += 0.5
        scored.append((score, name))
    scored.sort(reverse=True)
    return [name for s, name in scored if s > 0][:top_k] or list(CATALOG.keys())[:top_k]


def sample_rows(table: str, n: int = 3) -> list[dict]:
    """Return a few rows for the prompt context."""
    with cursor() as conn:
        rel = conn.execute(f'SELECT * FROM "{table}" USING SAMPLE {n} ROWS')
        cols = [d[0] for d in rel.description]
        rows = rel.fetchall()
    return [dict(zip(cols, r)) for r in rows]
