"""Persistence for observations, risk history and simulation state."""
from __future__ import annotations

from typing import Any

from app.config.logging_config import get_logger
from app.database.db import get_conn, jdump, jload, write_conn
from app.services.data_cache import iso, utcnow

log = get_logger(__name__)


# --------------------------------------------------------------------------
# Observations
# --------------------------------------------------------------------------
def save_weather(location_id: str, weather: dict[str, Any]) -> None:
    cur = weather.get("current") or {}
    acc = weather.get("accumulation") or {}
    fc = weather.get("forecast") or {}
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO weather_observations
               (location_id, observed_at, source, freshness, precipitation_mm_h,
                rain_1h, rain_3h, rain_6h, rain_24h, forecast_24h,
                temperature_c, humidity_pct, wind_kmh, pressure_hpa, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                location_id, weather.get("observed_at") or iso(utcnow()),
                weather.get("source"), weather.get("freshness"),
                cur.get("precipitation_mm_h"), acc.get("rain_1h"), acc.get("rain_3h"),
                acc.get("rain_6h"), acc.get("rain_24h"), fc.get("rain_24h"),
                cur.get("temperature_c"), cur.get("humidity_pct"),
                cur.get("wind_kmh"), cur.get("pressure_hpa"),
                jdump({"trend": weather.get("trend")}),
            ),
        )


def save_terrain(location_id: str, terrain: dict[str, Any]) -> None:
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO terrain_observations
               (location_id, observed_at, source, elevation_m, slope_deg, relief_m, payload)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(location_id) DO UPDATE SET
                 observed_at=excluded.observed_at, source=excluded.source,
                 elevation_m=excluded.elevation_m, slope_deg=excluded.slope_deg,
                 relief_m=excluded.relief_m, payload=excluded.payload""",
            (
                location_id, terrain.get("observed_at") or iso(utcnow()),
                terrain.get("source"), terrain.get("elevation_m"),
                terrain.get("slope_deg"), terrain.get("relief_m"),
                jdump({"aspect": terrain.get("aspect_deg"), "class": terrain.get("terrain_class")}),
            ),
        )


def save_hydrology(location_id: str, hydro: dict[str, Any], river: dict[str, Any]) -> None:
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO hydrology_observations
               (location_id, observed_at, source, freshness, discharge_m3s,
                discharge_mean_m3s, discharge_ratio, river_distance_m, stream_density, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                location_id, hydro.get("observed_at") or iso(utcnow()),
                hydro.get("source"), hydro.get("freshness"),
                hydro.get("discharge_m3s"), hydro.get("mean_30d_m3s"),
                hydro.get("discharge_ratio"), river.get("river_distance_m"),
                river.get("stream_density_km_per_km2"),
                jdump({"status": hydro.get("river_status"),
                       "nearest": river.get("nearest_waterway_name")}),
            ),
        )


def save_risk(location_id: str, risk: dict[str, Any]) -> None:
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO risk_observations
               (location_id, computed_at, risk_score, risk_level, confidence, mode, model_id, payload)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                location_id, risk.get("timestamp") or iso(utcnow()),
                risk.get("risk_score_precise", risk.get("risk_score", 0)),
                risk.get("risk_level"), risk.get("confidence"),
                risk.get("mode", "LIVE"), (risk.get("model") or {}).get("id", "unknown"),
                jdump({"contributors": [
                    {"key": c["key"], "impact": c["impact"], "share_pct": c["share_pct"]}
                    for c in (risk.get("contributors") or [])[:6]
                ]}),
            ),
        )


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------
def latest_risk(location_id: str, *, mode: str = "LIVE") -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT risk_score, risk_level, confidence, computed_at, mode
               FROM risk_observations WHERE location_id=? AND mode=?
               ORDER BY computed_at DESC, id DESC LIMIT 1""",
            (location_id, mode),
        ).fetchone()
    return dict(row) if row else None


def risk_history(location_id: str, limit: int = 48, *, mode: str | None = "LIVE") -> list[dict[str, Any]]:
    sql = """SELECT risk_score, risk_level, confidence, computed_at, mode
             FROM risk_observations WHERE location_id=?"""
    params: list[Any] = [location_id]
    if mode:
        sql += " AND mode=?"
        params.append(mode)
    sql += " ORDER BY computed_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in reversed(rows)]


def weather_history(location_id: str, limit: int = 48) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT observed_at, precipitation_mm_h, rain_24h, freshness
               FROM weather_observations WHERE location_id=?
               ORDER BY observed_at DESC, id DESC LIMIT ?""",
            (location_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def counts() -> dict[str, int]:
    with get_conn() as conn:
        out = {}
        for table in ("weather_observations", "terrain_observations",
                      "hydrology_observations", "risk_observations",
                      "monitoring_locations", "api_cache"):
            out[table] = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    return out


# --------------------------------------------------------------------------
# Simulation state
# --------------------------------------------------------------------------
DEFAULT_SESSION = "default"


def save_simulation_state(
    overrides: dict[str, Any], *, scenario_id: str | None, active: bool,
    session_id: str = DEFAULT_SESSION,
) -> None:
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO simulation_state (session_id, scenario_id, overrides, active, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 scenario_id=excluded.scenario_id, overrides=excluded.overrides,
                 active=excluded.active, updated_at=excluded.updated_at""",
            (session_id, scenario_id, jdump(overrides), 1 if active else 0, iso(utcnow())),
        )


def load_simulation_state(session_id: str = DEFAULT_SESSION) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT scenario_id, overrides, active, updated_at FROM simulation_state WHERE session_id=?",
            (session_id,),
        ).fetchone()
    if not row:
        return {"active": False, "scenario_id": None, "overrides": {}, "updated_at": None}
    return {
        "active": bool(row["active"]),
        "scenario_id": row["scenario_id"],
        "overrides": jload(row["overrides"]) or {},
        "updated_at": row["updated_at"],
    }


def clear_simulation_state(session_id: str = DEFAULT_SESSION) -> None:
    with write_conn() as conn:
        conn.execute("DELETE FROM simulation_state WHERE session_id=?", (session_id,))


def upsert_scenario(scenario: dict[str, Any]) -> None:
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO simulation_scenarios (id, name, severity, payload, loaded_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, severity=excluded.severity,
                 payload=excluded.payload, loaded_at=excluded.loaded_at""",
            (
                scenario["id"], scenario.get("name", scenario["id"]),
                scenario.get("severity"), jdump(scenario), iso(utcnow()),
            ),
        )
