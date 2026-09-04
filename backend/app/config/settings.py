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
    scenarios_dir: Path = PROJECT_ROOT / "data" / "scenarios"
    config_dir: Path = PROJECT_ROOT / "data" / "config"
    cache_dir: Path = PROJECT_ROOT / "data" / "cache"
    database_path: Path = PROJECT_ROOT / "data" / "cache" / "floodsafe.db"

    # --- region ----------------------------------------------------------
    default_region_id: str = "uttarakhand"

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
