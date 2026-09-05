"""Application settings.

All configuration is environment-overridable but has working defaults, so the
prototype runs with zero setup. No external service used by this project
requires an API key.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config/settings.py -> parents[3] == project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FloodSafe"
    app_tagline: str = "Flash Flood Monitoring & Prediction for Hilly Regions"
    version: str = "1.0.0"
    environment: str = "development"
    log_level: str = "INFO"

    # --- paths -----------------------------------------------------------
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    regions_dir: Path = PROJECT_ROOT / "data" / "regions"
    india_dir: Path = PROJECT_ROOT / "data" / "india"
    scenarios_dir: Path = PROJECT_ROOT / "data" / "scenarios"
    config_dir: Path = PROJECT_ROOT / "data" / "config"
    cache_dir: Path = PROJECT_ROOT / "data" / "cache"
    database_path: Path = PROJECT_ROOT / "data" / "cache" / "floodsafe.db"

    # --- region ----------------------------------------------------------
    default_region_id: str = "uttarakhand"

    # --- geographic scope ------------------------------------------------
    # Risk-grid resolution per scope. Coarser at national scope because every
    # cell costs real API calls against four upstream services; finer as the
    # user drills in and the area shrinks. Rows*cols must stay under
    # max_batch_points or the weather fetch splits into extra requests.
    grid_rows_national: int = 8
    grid_cols_national: int = 8
    grid_rows_state: int = 6
    grid_cols_state: int = 7
    grid_rows_district: int = 5
    grid_cols_district: int = 5
    # Largest OSM query box, in degrees. A whole-state Overpass query for every
    # hospital, school, bridge and settlement is slow and unfair to a shared
    # community endpoint, so wide scopes query a window and say that they did.
    gis_max_bbox_degrees: float = 2.0
    # Settlements resolved per district for the location selector.
    district_settlement_limit: int = 250
    # Max stream polylines whose GEOMETRY is sent to the browser. The true
    # total is always reported alongside, and the map says when it differs -
    # a displayed count must never be an artefact of the transport budget.
    # Set above any curated region's network so the pilot regions send all.
    map_stream_limit: int = 2000

    # --- search radii ----------------------------------------------------
    # GloFAS cell size is ~0.05 deg, so this offset reaches the adjacent cell.
    # The stencil exists because a settlement's own cell is often a hillslope
    # rather than the channel: at Rishikesh the centre cell reports ~0.9 m3/s
    # while the neighbouring cell holding the Ganga reports three orders of
    # magnitude more. Widening this searches further for the channel.
    glofas_stencil_offset_deg: float = 0.05
    # River-proximity and drainage-density search radius for a monitoring point.
    osm_search_radius_m: float = 3500.0
    # The same, for a risk-grid cell, which covers more ground than a point.
    osm_grid_search_radius_m: float = 4000.0

    # --- external endpoints (all free, no API key) -----------------------
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_flood_url: str = "https://flood-api.open-meteo.com/v1/flood"
    open_meteo_elevation_url: str = "https://api.open-meteo.com/v1/elevation"
    open_elevation_url: str = "https://api.open-elevation.com/api/v1/lookup"
    open_topo_data_url: str = "https://api.opentopodata.org/v1/srtm90m"
    overpass_urls: str = (
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter"
    )

    # --- http behaviour --------------------------------------------------
    http_timeout_seconds: float = 25.0
    http_overpass_timeout_seconds: float = 90.0
    http_max_retries: int = 2
    http_backoff_seconds: float = 1.2
    user_agent: str = "FloodSafe-SIH26192-Prototype/1.0 (educational prototype)"

    # --- cache TTLs (seconds) -------------------------------------------
    cache_ttl_weather: int = 15 * 60
    cache_ttl_elevation: int = 180 * 24 * 3600   # terrain is effectively static
    cache_ttl_hydrology: int = 3 * 3600          # GloFAS updates daily
    cache_ttl_osm: int = 7 * 24 * 3600           # OSM geometry changes slowly
    cache_ttl_climatology: int = 30 * 24 * 3600  # ERA5 history is immutable

    # --- fetch budgets ---------------------------------------------------
    max_batch_points: int = 90          # Open-Meteo multi-point request cap we self-impose
    climatology_years: int = 5          # years of ERA5 history per location
    startup_prefetch: bool = True       # warm the cache in the background at boot

    # --- CORS ------------------------------------------------------------
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def overpass_url_list(self) -> list[str]:
        return [o.strip() for o in self.overpass_urls.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.cache_dir.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()

# Allow disabling all outbound network calls (used by the offline/fallback tests).
def network_disabled() -> bool:
    return os.getenv("FLOODSAFE_DISABLE_NETWORK", "").lower() in {"1", "true", "yes"}
