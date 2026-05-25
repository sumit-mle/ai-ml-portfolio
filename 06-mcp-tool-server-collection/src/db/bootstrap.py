"""Create the demo warehouse.

Two tables:
  - `taxi_trips`     — synthetic NYC TLC-style trip records (~50k rows)
  - `passengers`     — synthetic PII table (id, full_name, email, phone, signup_at)

The SQL is intentionally close to a real lakehouse: types match what you'd
see in Snowflake/BigQuery, and the row count is large enough that aggregates
take a measurable but bounded time (good for rate-limit testing).

Run via: `python -m src.cli init-db`
"""
from __future__ import annotations

import logging
import random
from pathlib import Path

import duckdb

from ..config import get_settings

logger = logging.getLogger(__name__)


_TAXI_DDL = """
CREATE TABLE taxi_trips AS
WITH params AS (
    SELECT
        50000      AS n_trips,
        '2024-01-01'::TIMESTAMP AS t0,
        '2024-12-31'::TIMESTAMP AS t1
)
SELECT
    row_number() OVER ()                                              AS trip_id,
    'V' || (1 + (random() * 4)::INT)::VARCHAR                          AS vendor_id,
    t0 + INTERVAL ((random() * EPOCH(t1 - t0))::BIGINT) SECOND        AS pickup_at,
    NULL::TIMESTAMP                                                    AS dropoff_at,
    (1 + (random() * 6)::INT)::INT                                     AS passenger_count,
    round((0.3 + random() * 18)::DOUBLE, 2)                            AS trip_distance_miles,
    list_element(['credit_card','cash','no_charge','dispute'],
                 1 + (random() * 4)::INT)::VARCHAR                     AS payment_type,
    (1 + (random() * 264)::INT)::INT                                   AS pickup_location_id,
    (1 + (random() * 264)::INT)::INT                                   AS dropoff_location_id,
    round((2.5 + random() * 80)::DOUBLE, 2)                            AS fare_amount,
    round((random() * 6)::DOUBLE, 2)                                   AS tip_amount,
    round((random() * 12)::DOUBLE, 2)                                  AS tolls_amount,
    round((random() * 1)::DOUBLE, 2)                                   AS surcharge,
    NULL::DOUBLE                                                       AS total_amount
FROM params, range((SELECT n_trips FROM params)) AS r(i);

UPDATE taxi_trips
SET dropoff_at = pickup_at + INTERVAL ((trip_distance_miles * 200 + random() * 600)::BIGINT) SECOND;

UPDATE taxi_trips
SET total_amount = round(fare_amount + tip_amount + tolls_amount + surcharge, 2);
"""

_PASSENGERS_DDL = """
CREATE TABLE passengers AS
WITH params AS (SELECT 5000 AS n)
SELECT
    row_number() OVER ()                                              AS id,
    list_element(['Alex','Jordan','Sam','Casey','Taylor','Morgan',
                  'Riley','Avery','Quinn','Drew'],
                 1 + (random() * 10)::INT)::VARCHAR
        || ' '
        || list_element(['Smith','Lee','Patel','Garcia','Chen',
                         'Johnson','Brown','Davis','Miller','Wilson'],
                        1 + (random() * 10)::INT)::VARCHAR             AS full_name,
    'user' || ((1 + random() * 99999)::INT)::VARCHAR
        || '@example.com'                                              AS email,
    '+1-555-' || ((100 + random() * 899)::INT)::VARCHAR
        || '-' || ((1000 + random() * 8999)::INT)::VARCHAR             AS phone,
    ('2023-01-01'::TIMESTAMP
        + INTERVAL ((random() * EPOCH('2025-01-01'::TIMESTAMP - '2023-01-01'::TIMESTAMP))::BIGINT) SECOND
    )                                                                  AS signup_at
FROM params, range((SELECT n FROM params)) AS r(i);
"""


def init_warehouse(*, force: bool = False) -> dict:
    s = get_settings()
    s.duckdb_path.parent.mkdir(parents=True, exist_ok=True)

    # Drop any read-only cache by ensuring no live connections; for the
    # bootstrap we open a writable connection.
    if force and s.duckdb_path.exists():
        s.duckdb_path.unlink()

    with duckdb.connect(str(s.duckdb_path)) as conn:
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        if "_bootstrap" in existing:
            conn.execute("DROP TABLE _bootstrap")
            existing.discard("_bootstrap")

        if "taxi_trips" not in existing:
            logger.info("Creating taxi_trips ...")
            for stmt in [s for s in _TAXI_DDL.split(";") if s.strip()]:
                conn.execute(stmt)
        if "passengers" not in existing:
            logger.info("Creating passengers ...")
            for stmt in [s for s in _PASSENGERS_DDL.split(";") if s.strip()]:
                conn.execute(stmt)

        n_trips = conn.execute("SELECT count(*) FROM taxi_trips").fetchone()[0]
        n_passengers = conn.execute("SELECT count(*) FROM passengers").fetchone()[0]

    logger.info("Warehouse ready: %d trips, %d passengers", n_trips, n_passengers)
    return {"taxi_trips": n_trips, "passengers": n_passengers, "path": str(s.duckdb_path)}
