"""Test configuration.

Environment variables are set before any application module is imported, so the
test run uses an isolated database and never touches the network.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TMP_DB = Path(tempfile.gettempdir()) / "floodsafe_test.db"
os.environ["DATABASE_PATH"] = str(_TMP_DB)
os.environ["STARTUP_PREFETCH"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
# Every test runs offline. Tests that need upstream data inject it explicitly.
os.environ["FLOODSAFE_DISABLE_NETWORK"] = "1"

import pytest  # noqa: E402

from app.database.db import init_db, reset_db  # noqa: E402
from app.models.enums import Freshness  # noqa: E402
from app.services.feature_engineering import FeatureSet, FeatureValue  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    init_db()
    reset_db()
    yield
    try:
        _TMP_DB.unlink(missing_ok=True)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _clean_simulation():
    """Every test starts with the simulator switched off."""
    from app.database import repository

    repository.clear_simulation_state()
    yield
    repository.clear_simulation_state()


# --------------------------------------------------------------------------
# Feature-set builders
# --------------------------------------------------------------------------
#: A fixed, moderately vulnerable Himalayan terrain profile, so risk assertions
#: depend only on the weather/hydrology values a test varies.
BASE_TERRAIN = {
    "slope": 18.0,             # steep hillslope
    "terrain_relief": 500.0,   # 500 m of local relief
    "river_proximity": 250.0,  # 250 m from the channel
    "stream_density": 1.2,     # km per km2
}

CALM_WEATHER = {
    "rainfall_intensity": 0.0,
    "rainfall_3h": 0.0,
    "rainfall_24h": 0.5,
    "rainfall_forecast_24h": 1.0,
    "rainfall_trend": 0.0,
    "rainfall_anomaly": 20.0,
    "antecedent_precipitation_index": 3.0,
    "river_discharge_anomaly": 0.9,
}

SEVERE_WEATHER = {
    "rainfall_intensity": 95.0,
    "rainfall_3h": 115.0,
    "rainfall_24h": 260.0,
    "rainfall_forecast_24h": 210.0,
    "rainfall_trend": 16.0,
    "rainfall_anomaly": 99.5,
    "antecedent_precipitation_index": 140.0,
    "river_discharge_anomaly": 4.2,
}


def make_feature_set(
    values: dict[str, float],
    *,
    location_id: str = "test_location",
    freshness: str = Freshness.LIVE.value,
    simulated: bool = False,
) -> FeatureSet:
    """Build a FeatureSet directly from raw feature values."""
    fs = FeatureSet(location_id=location_id)
    units = {
        "rainfall_intensity": "mm/h", "rainfall_3h": "mm", "rainfall_24h": "mm",
        "rainfall_forecast_24h": "mm", "rainfall_trend": "mm/h per h",
        "rainfall_anomaly": "percentile", "antecedent_precipitation_index": "mm",
        "slope": "deg", "terrain_relief": "m", "river_proximity": "m",
        "stream_density": "km/km2", "river_discharge_anomaly": "ratio",
    }
    for key, value in values.items():
        fs.features[key] = FeatureValue(
            key=key, raw=float(value), unit=units.get(key, ""),
            source="test", source_key="test", freshness=freshness,
            available=True, simulated=simulated,
        )
    return fs


@pytest.fixture
def calm_features() -> FeatureSet:
    return make_feature_set({**BASE_TERRAIN, **CALM_WEATHER})


@pytest.fixture
def severe_features() -> FeatureSet:
    return make_feature_set({**BASE_TERRAIN, **SEVERE_WEATHER})


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
