"""SQLite persistence layer.

Deliberately small: a connection helper, the schema, and a couple of generic
helpers. Repositories in ``repository.py`` hold the actual queries.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config.logging_config import EV_DB, get_logger
from app.config.settings import settings

log = get_logger(__name__)

_write_lock = threading.Lock()

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS api_cache (
    cache_key    TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    payload      TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    hit_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_cache_source ON api_cache(source);

CREATE TABLE IF NOT EXISTS monitoring_locations (
    id           TEXT PRIMARY KEY,
    region_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    district     TEXT,
    latitude     REAL NOT NULL,
    longitude    REAL NOT NULL,
    elevation_m  REAL,
    nearest_river TEXT,
    settlement_type TEXT,
    exposure     TEXT,
    updated_at   TEXT NOT NULL,
    country      TEXT,
    state_id     TEXT,
    district_id  TEXT,
    origin       TEXT
);
CREATE INDEX IF NOT EXISTS idx_locations_region ON monitoring_locations(region_id);

CREATE TABLE IF NOT EXISTS weather_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id   TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    source        TEXT NOT NULL,
    freshness     TEXT NOT NULL,
    precipitation_mm_h REAL,
    rain_1h       REAL,
    rain_3h       REAL,
    rain_6h       REAL,
    rain_24h      REAL,
    forecast_24h  REAL,
    temperature_c REAL,
    humidity_pct  REAL,
    wind_kmh      REAL,
    pressure_hpa  REAL,
    payload       TEXT
);
CREATE INDEX IF NOT EXISTS idx_weather_loc_time
    ON weather_observations(location_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS terrain_observations (
    location_id   TEXT PRIMARY KEY,
    observed_at   TEXT NOT NULL,
    source        TEXT NOT NULL,
    elevation_m   REAL,
    slope_deg     REAL,
    relief_m      REAL,
    payload       TEXT
);

CREATE TABLE IF NOT EXISTS hydrology_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id   TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    source        TEXT NOT NULL,
    freshness     TEXT NOT NULL,
    discharge_m3s REAL,
    discharge_mean_m3s REAL,
    discharge_ratio REAL,
    river_distance_m REAL,
    stream_density REAL,
    payload       TEXT
);
CREATE INDEX IF NOT EXISTS idx_hydro_loc_time
    ON hydrology_observations(location_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS risk_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id   TEXT NOT NULL,
    computed_at   TEXT NOT NULL,
    risk_score    REAL NOT NULL,
    risk_level    TEXT NOT NULL,
    confidence    TEXT NOT NULL,
    mode          TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    payload       TEXT
);
CREATE INDEX IF NOT EXISTS idx_risk_loc_time
    ON risk_observations(location_id, computed_at DESC);

CREATE TABLE IF NOT EXISTS simulation_scenarios (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    severity      TEXT,
    payload       TEXT NOT NULL,
    loaded_at     TEXT NOT NULL
);

-- Registered phones for flood early-warning push notifications.
-- New tables, so CREATE TABLE IF NOT EXISTS upgrades an existing database on
-- its own; no ALTER is needed and no existing row is touched.
CREATE TABLE IF NOT EXISTS devices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fcm_token     TEXT NOT NULL UNIQUE,
    label         TEXT,
    location_id   TEXT,
    location_name TEXT,
    district      TEXT,
    state_id      TEXT,
    state_name    TEXT,
    latitude      REAL,
    longitude     REAL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_devices_location ON devices(location_id, active);
CREATE INDEX IF NOT EXISTS idx_devices_district ON devices(state_id, district, active);

-- One row per dispatch ATTEMPT. Doubles as the cooldown ledger: a repeat alert
-- for the same location and severity inside the cooldown window is suppressed
-- by looking here, so a dashboard refresh cannot re-notify anyone.
CREATE TABLE IF NOT EXISTS alert_dispatches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id   TEXT NOT NULL,
    location_name TEXT,
    risk_level    TEXT NOT NULL,
    risk_score    REAL,
    previous_level TEXT,
    mode          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    targeted      INTEGER NOT NULL DEFAULT 0,
    accepted      INTEGER NOT NULL DEFAULT 0,
    rejected      INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    detail        TEXT,
    sent_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dispatch_cooldown
    ON alert_dispatches(location_id, risk_level, sent_at DESC);

CREATE TABLE IF NOT EXISTS simulation_state (
    session_id    TEXT PRIMARY KEY,
    scenario_id   TEXT,
    overrides     TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or settings.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Read/write connection. Writes are serialised by a process-level lock."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def write_conn() -> Iterator[sqlite3.Connection]:
    with _write_lock:
        with get_conn() as conn:
            yield conn


# Additive column migrations for databases created before pan-India support.
# Purely additive: no existing column is dropped, renamed or retyped, so an
# older database keeps every row it had. `CREATE TABLE IF NOT EXISTS` alone
# cannot do this - it is a no-op once the table exists.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("monitoring_locations", "country", "TEXT"),
    ("monitoring_locations", "state_id", "TEXT"),
    ("monitoring_locations", "district_id", "TEXT"),
    ("monitoring_locations", "origin", "TEXT"),
)


# Indexes over migrated columns. These CANNOT live in SCHEMA: on a database that
# already exists, `CREATE TABLE IF NOT EXISTS` is a no-op, so the column is still
# missing when the index statement runs and the whole script fails with
# "no such column: state_id". They run after _migrate has added the columns.
POST_MIGRATION_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_locations_state ON monitoring_locations(state_id);
CREATE INDEX IF NOT EXISTS idx_locations_district ON monitoring_locations(district_id);
"""


def _migrate(conn: sqlite3.Connection) -> list[str]:
    applied: list[str] = []
    for table, column, decl in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table not created yet; SCHEMA already has the column
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            applied.append(f"{table}.{column}")
    return applied


def init_db() -> None:
    with write_conn() as conn:
        conn.executescript(SCHEMA)
        applied = _migrate(conn)
        conn.executescript(POST_MIGRATION_INDEXES)
    if applied:
        log.info("%s applied %d migration(s): %s", EV_DB, len(applied), ", ".join(applied))
    log.info("%s schema ready at %s", EV_DB, settings.database_path)


def reset_db() -> None:
    """Drop and recreate everything. Used by tests only."""
    with write_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for r in rows:
            if r["name"] != "sqlite_sequence":
                conn.execute(f'DROP TABLE IF EXISTS "{r["name"]}"')
        conn.executescript(SCHEMA)
        conn.executescript(POST_MIGRATION_INDEXES)


def jdump(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))


def jload(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
