"""Marketing-mix DuckDB warehouse.

Six-table schema designed to support the canonical CMO question:
"why did revenue dip in EMEA last week?"

Tables:
  regions       (region_id, region_name, country_count)
  products      (product_id, product_name, category, list_price_usd)
  customers     (customer_id, region_id, signup_date, plan)
  campaigns     (campaign_id, region_id, channel, started_at, ended_at, budget_usd)
  ad_spend      (date, campaign_id, spend_usd, impressions, clicks)
  revenue_daily (date, region_id, product_id, customer_count, gross_revenue_usd, refunds_usd)

The ETL deliberately injects a -32% revenue dip in EMEA for the last 7
days of the data range, AND drops campaign budget there by 60% in the
preceding 14 days. The agent's job: find both, attribute the dip to the
budget cut.
"""
from __future__ import annotations

import logging
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

from ..config import get_settings

logger = logging.getLogger(__name__)


_DDL = [
    """
    CREATE TABLE regions (
        region_id     INTEGER PRIMARY KEY,
        region_name   VARCHAR NOT NULL,
        country_count INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE products (
        product_id    INTEGER PRIMARY KEY,
        product_name  VARCHAR NOT NULL,
        category      VARCHAR NOT NULL,
        list_price_usd DOUBLE NOT NULL
    )
    """,
    """
    CREATE TABLE customers (
        customer_id   INTEGER PRIMARY KEY,
        region_id     INTEGER NOT NULL REFERENCES regions(region_id),
        signup_date   DATE NOT NULL,
        plan          VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE campaigns (
        campaign_id   INTEGER PRIMARY KEY,
        region_id     INTEGER NOT NULL REFERENCES regions(region_id),
        channel       VARCHAR NOT NULL,
        started_at    DATE NOT NULL,
        ended_at      DATE NOT NULL,
        budget_usd    DOUBLE NOT NULL
    )
    """,
    """
    CREATE TABLE ad_spend (
        date          DATE NOT NULL,
        campaign_id   INTEGER NOT NULL REFERENCES campaigns(campaign_id),
        spend_usd     DOUBLE NOT NULL,
        impressions   BIGINT NOT NULL,
        clicks        BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE revenue_daily (
        date              DATE NOT NULL,
        region_id         INTEGER NOT NULL REFERENCES regions(region_id),
        product_id        INTEGER NOT NULL REFERENCES products(product_id),
        customer_count    INTEGER NOT NULL,
        gross_revenue_usd DOUBLE NOT NULL,
        refunds_usd       DOUBLE NOT NULL
    )
    """,
]


_REGIONS = [
    (1, "AMER", 3),
    (2, "EMEA", 33),
    (3, "APAC", 14),
    (4, "LATAM", 12),
]

_PRODUCTS = [
    (1, "Starter SaaS", "saas", 49.0),
    (2, "Pro SaaS",     "saas", 199.0),
    (3, "Enterprise SaaS", "saas", 1499.0),
    (4, "Onboarding Service", "services", 2500.0),
]

_CHANNELS = ["search", "social", "display", "video"]


def init_db(*, force: bool = False) -> dict:
    s = get_settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    if force and s.db_path.exists():
        s.db_path.unlink()
    if s.db_path.exists():
        logger.info("Warehouse already exists at %s — pass --force to recreate.", s.db_path)
        with duckdb.connect(str(s.db_path), read_only=True) as conn:
            return _stats(conn)

    rng = random.Random(20260525)
    today = date(2026, 5, 25)
    horizon_days = 90
    start = today - timedelta(days=horizon_days)

    with duckdb.connect(str(s.db_path)) as conn:
        for stmt in _DDL:
            conn.execute(stmt)

        # Seed regions, products
        conn.executemany("INSERT INTO regions VALUES (?, ?, ?)", _REGIONS)
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", _PRODUCTS)

        # Customers: 5000, distributed across regions
        cust_rows: list[tuple] = []
        for i in range(1, 5001):
            region_id = rng.choices([1, 2, 3, 4], weights=[40, 30, 20, 10])[0]
            signup = start + timedelta(days=rng.randint(0, horizon_days))
            plan = rng.choices(["free", "starter", "pro", "enterprise"],
                               weights=[40, 30, 25, 5])[0]
            cust_rows.append((i, region_id, signup, plan))
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", cust_rows)

        # Campaigns: 4 channels x 4 regions = ~16 campaigns/quarter, with
        # an EMEA budget cut for the last two weeks.
        camp_rows: list[tuple] = []
        cid = 1
        for region_id, _, _ in _REGIONS:
            for ch in _CHANNELS:
                # Two campaigns per region/channel — one continuous, one
                # mid-period to give the data some temporal structure.
                camp_rows.append((cid, region_id, ch, start, today,
                                  rng.uniform(8000, 25000)))
                cid += 1
                # Second campaign — EMEA mid-period campaign gets a 60% smaller
                # budget for the LAST 14 days, simulating a real-world cut.
                budget = rng.uniform(8000, 25000)
                if region_id == 2:
                    budget *= 0.4   # the budget cut
                camp_rows.append((cid, region_id, ch,
                                  today - timedelta(days=21), today, budget))
                cid += 1
        conn.executemany("INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?)", camp_rows)

        # Ad spend per day per campaign
        ad_rows: list[tuple] = []
        # We'll re-fetch to know the windows
        camp_lookup = {row[0]: row for row in camp_rows}
        for campaign_id, (_, region_id, ch, st, en, budget) in (
            (k, v) for k, v in camp_lookup.items()
        ):
            days = (en - st).days + 1
            daily_budget = budget / days
            for d_offset in range(days):
                d = st + timedelta(days=d_offset)
                spend = daily_budget * rng.uniform(0.85, 1.15)
                impressions = int(spend * rng.uniform(80, 140))
                clicks = int(impressions * rng.uniform(0.005, 0.025))
                ad_rows.append((d, campaign_id, round(spend, 2), impressions, clicks))
        conn.executemany("INSERT INTO ad_spend VALUES (?, ?, ?, ?, ?)", ad_rows)

        # Daily revenue per region/product. Inject the EMEA dip in the last
        # 7 days.
        rev_rows: list[tuple] = []
        for d_offset in range(horizon_days + 1):
            d = start + timedelta(days=d_offset)
            for region_id, _, _ in _REGIONS:
                for product_id, _, _, list_price in _PRODUCTS:
                    base_customers = {1: 110, 2: 80, 3: 55, 4: 25}[region_id]
                    cust_count = int(base_customers * rng.uniform(0.85, 1.15))
                    revenue = cust_count * list_price * rng.uniform(0.7, 1.0)
                    refunds = revenue * rng.uniform(0.005, 0.04)

                    # The intentional EMEA dip in the last 7 days
                    if region_id == 2 and (today - d).days <= 7:
                        cust_count = int(cust_count * 0.68)
                        revenue *= 0.68
                        refunds *= 1.4

                    rev_rows.append(
                        (d, region_id, product_id, cust_count,
                         round(revenue, 2), round(refunds, 2))
                    )
        conn.executemany("INSERT INTO revenue_daily VALUES (?, ?, ?, ?, ?, ?)", rev_rows)

        info = _stats(conn)

    logger.info(
        "Warehouse seeded: %d regions, %d products, %d customers, "
        "%d campaigns, %d ad_spend rows, %d revenue rows",
        info["regions"], info["products"], info["customers"],
        info["campaigns"], info["ad_spend"], info["revenue_daily"],
    )
    return info


def _stats(conn: duckdb.DuckDBPyConnection) -> dict:
    out = {}
    for tbl in ("regions", "products", "customers", "campaigns", "ad_spend", "revenue_daily"):
        out[tbl] = conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
    out["path"] = str(get_settings().db_path)
    return out
