"""Resilience: the LIVE -> CACHE -> DEMO fallback chain.

The whole test module runs with outbound networking disabled, so these
exercise the real failure path rather than a mocked one.
"""
from __future__ import annotations

import pytest

from app.services import data_cache, weather_service
from app.services.http_client import UpstreamError, fetch_json


class TestUpstreamFailure:
    async def test_fetch_json_raises_upstream_error_not_a_crash(self):
        with pytest.raises(UpstreamError):
            await fetch_json("test-source", "https://example.invalid/data")

    async def test_weather_falls_back_to_demo_when_nothing_is_cached(self):
        data_cache.clear()
        weather = await weather_service.get_weather("fallback_test", 30.5, 79.5)
        assert weather["freshness"] == "DEMO"
        assert weather["current"]["precipitation_mm_h"] is not None
        assert weather["series"]["past"], "demo data must still populate charts"

    async def test_demo_data_is_explicitly_labelled(self):
        data_cache.clear()
        weather = await weather_service.get_weather("fallback_test", 30.5, 79.5)
        joined = " ".join(weather["notes"]).upper()
        assert "SYNTHETIC" in joined or "DEMO" in joined
        assert weather["source"] != weather_service.SOURCE_LABEL

    async def test_demo_data_is_deterministic(self):
        data_cache.clear()
        a = await weather_service.get_weather("stable_id", 30.5, 79.5)
        b = await weather_service.get_weather("stable_id", 30.5, 79.5)
        assert a["current"]["precipitation_mm_h"] == b["current"]["precipitation_mm_h"]


class TestStaleCache:
    async def test_expired_cache_is_preferred_over_demo(self):
        """A real observation from an hour ago beats invented numbers."""
        data_cache.clear()
        key = data_cache.make_key(weather_service.SOURCE_KEY, lat=30.9, lon=79.9)
        payload = {
            "timezone": "Asia/Kolkata",
            "elevation": 2100.0,
            "current": {
                "time": "2026-09-03T12:00", "precipitation": 42.0, "rain": 42.0,
                "temperature_2m": 17.5, "relative_humidity_2m": 95,
                "wind_speed_10m": 8.0, "surface_pressure": 790.0,
            },
            "hourly": {
                "time": [f"2026-09-03T{h:02d}:00" for h in range(24)],
                "precipitation": [3.0] * 24,
                "precipitation_probability": [80] * 24,
                "temperature_2m": [17.0] * 24,
            },
        }
        # Insert with a TTL that has already elapsed.
        data_cache.put(key, payload, source=weather_service.SOURCE_KEY, ttl_seconds=-60)

        weather = await weather_service.get_weather("stale_test", 30.9, 79.9)
        assert weather["freshness"] == "STALE_CACHE"
        assert weather["current"]["precipitation_mm_h"] == 42.0
        assert weather["source"] == weather_service.SOURCE_LABEL
        assert any("unavailable" in n.lower() for n in weather["notes"])

    async def test_fresh_cache_is_served_as_cached(self):
        data_cache.clear()
        key = data_cache.make_key(weather_service.SOURCE_KEY, lat=31.1, lon=79.1)
        data_cache.put(
            key,
            {
                "current": {"time": "2026-09-03T12:00", "precipitation": 5.0},
                "hourly": {"time": ["2026-09-03T12:00"], "precipitation": [5.0]},
            },
            source=weather_service.SOURCE_KEY, ttl_seconds=600,
        )
        weather = await weather_service.get_weather("cache_test", 31.1, 79.1)
        assert weather["freshness"] == "CACHED"
        assert weather["current"]["precipitation_mm_h"] == 5.0


class TestCacheMechanics:
    def test_roundtrip(self):
        data_cache.clear()
        data_cache.put("k1", {"v": 1}, source="unit-test", ttl_seconds=60)
        got = data_cache.get("k1")
        assert got is not None and got.payload == {"v": 1}
        assert not got.expired

    def test_expired_entries_are_hidden_unless_requested(self):
        data_cache.clear()
        data_cache.put("k2", {"v": 2}, source="unit-test", ttl_seconds=-1)
        assert data_cache.get("k2") is None
        stale = data_cache.get("k2", allow_expired=True)
        assert stale is not None and stale.expired and stale.payload == {"v": 2}

    def test_keys_round_coordinates_so_near_requests_share_an_entry(self):
        a = data_cache.make_key("src", lat=30.123456, lon=79.123456)
        b = data_cache.make_key("src", lat=30.123457, lon=79.123458)
        assert a == b

    def test_keys_separate_genuinely_different_points(self):
        a = data_cache.make_key("src", lat=30.10, lon=79.10)
        b = data_cache.make_key("src", lat=30.90, lon=79.90)
        assert a != b

    def test_stats_report_usage(self):
        data_cache.clear()
        data_cache.put("k3", {"v": 3}, source="unit-test", ttl_seconds=60)
        data_cache.get("k3")
        stats = data_cache.stats()
        assert stats["entries"] >= 1
        assert stats["total_hits"] >= 1


class TestApplicationSurvivesOutage:
    """The single most important resilience property: nothing 500s."""

    def test_no_endpoint_returns_a_server_error_while_offline(self, client):
        paths = [
            "/api/health", "/api/system/sources", "/api/system/cache",
            "/api/regions", "/api/regions/uttarakhand", "/api/locations",
            "/api/locations/loc_gaurikund", "/api/weather/loc_gaurikund",
            "/api/climatology/loc_gaurikund", "/api/terrain/loc_gaurikund",
            "/api/terrain", "/api/hydrology/loc_gaurikund", "/api/gis/layers",
            "/api/gis/waterways", "/api/gis/infrastructure", "/api/risk/loc_gaurikund",
            "/api/risk/map", "/api/risk/model", "/api/monitoring/loc_gaurikund",
            "/api/dashboard/summary", "/api/dashboard/authority",
            "/api/dashboard/diagnostics", "/api/alerts", "/api/alerts/loc_gaurikund",
            "/api/simulation/scenarios", "/api/simulation/controls",
            "/api/simulation/state", "/api/historical/flood-events",
        ]
        failures = []
        for path in paths:
            r = client.get(path)
            if r.status_code >= 500:
                failures.append((path, r.status_code, r.text[:160]))
        assert not failures, failures

    def test_degraded_state_is_visible_to_the_user(self, client):
        body = client.get("/api/monitoring/loc_gaurikund", params={"timeline": False}).json()
        freshness = body["data_freshness"]
        assert freshness["overall"] in {"DEGRADED", "CACHED", "LIVE"}
        assert freshness["degraded"] >= 1, "an outage must be reported, not hidden"

    def test_risk_is_still_produced_during_an_outage(self, client):
        risk = client.get("/api/risk/loc_gaurikund").json()["risk"]
        assert 0 <= risk["risk_score"] <= 100
        assert risk["confidence"] in {"LOW", "MEDIUM", "HIGH"}
