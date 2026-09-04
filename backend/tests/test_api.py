"""API contract tests.

These run with the network disabled, so they also prove the whole API stays
functional when every upstream source is unreachable.
"""
from __future__ import annotations

import pytest


class TestSystem:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["network_enabled"] is False
        assert body["default_region"] == "uttarakhand"

    def test_root_carries_the_disclaimer(self, client):
        body = client.get("/").json()
        assert "prototype" in body["disclaimer"].lower()
        assert "SIH26192" in body["problem_statement"]

    def test_sources_endpoint_lists_every_provider(self, client):
        body = client.get("/api/system/sources").json()
        names = {s["name"] for s in body["sources"]}
        assert {"open-meteo-forecast", "open-meteo-flood", "open-meteo-elevation",
                "open-meteo-archive", "overpass"} <= names
        for s in body["sources"]:
            assert s["status"] in {"OK", "DEGRADED", "DOWN", "UNKNOWN"}
            assert s["label"]

    def test_openapi_schema_builds(self, client):
        assert client.get("/openapi.json").status_code == 200


class TestConfiguration:
    def test_regions(self, client):
        body = client.get("/api/regions").json()
        assert body["count"] >= 2
        ids = {r["id"] for r in body["regions"]}
        assert "uttarakhand" in ids and "himachal_pradesh" in ids
        assert any(r["is_default"] for r in body["regions"])

    def test_region_detail_includes_grid(self, client):
        body = client.get("/api/regions/uttarakhand").json()
        assert body["location_count"] == 26
        assert len(body["grid_cells"]) == 42
        cell = body["grid_cells"][0]
        assert {"id", "row", "col", "center", "bounds"} <= set(cell)

    def test_unknown_region_is_404(self, client):
        assert client.get("/api/regions/atlantis").status_code == 404

    def test_locations_have_valid_coordinates(self, client):
        body = client.get("/api/locations").json()
        assert body["count"] == 26
        for loc in body["locations"]:
            assert -90 <= loc["latitude"] <= 90
            assert -180 <= loc["longitude"] <= 180
            assert loc["id"] and loc["name"]

    def test_second_region_is_selectable(self, client):
        body = client.get("/api/locations", params={"region_id": "himachal_pradesh"}).json()
        assert body["region_id"] == "himachal_pradesh"
        assert body["count"] == 12

    def test_unknown_location_is_404(self, client):
        assert client.get("/api/locations/loc_nowhere").status_code == 404


class TestDataEndpoints:
    """With the network down every source must degrade, not fail."""

    def test_weather_survives_total_api_failure(self, client):
        r = client.get("/api/weather/loc_rudraprayag")
        assert r.status_code == 200
        w = r.json()["weather"]
        assert w["freshness"] in {"DEMO", "STALE_CACHE", "CACHED"}
        if w["freshness"] == "DEMO":
            assert any("SYNTHETIC" in n.upper() or "DEMO" in n.upper() for n in w["notes"])
        assert w["current"]["precipitation_mm_h"] is not None

    def test_terrain_falls_back_to_reference_elevation(self, client):
        t = client.get("/api/terrain/loc_joshimath").json()["terrain"]
        assert t["freshness"] in {"DEMO", "STALE_CACHE", "CACHED"}
        assert t["elevation_m"] is not None

    def test_hydrology_endpoint_responds(self, client):
        body = client.get("/api/hydrology/loc_rishikesh").json()
        assert "discharge" in body and "river_network" in body

    def test_risk_endpoint_schema(self, client):
        body = client.get("/api/risk/loc_gaurikund").json()
        risk = body["risk"]
        for key in ("risk_score", "risk_level", "confidence", "timestamp",
                    "contributors", "model", "feature_summary", "disclaimer"):
            assert key in risk, key
        assert 0 <= risk["risk_score"] <= 100
        assert risk["risk_level"] in {"SAFE", "LOW", "MODERATE", "HIGH", "EXTREME"}
        assert risk["confidence"] in {"HIGH", "MEDIUM", "LOW"}
        assert isinstance(risk["contributors"], list)

    def test_risk_endpoint_404_for_unknown_location(self, client):
        assert client.get("/api/risk/loc_nowhere").status_code == 404

    def test_risk_map_returns_a_complete_grid(self, client):
        body = client.get("/api/risk/map").json()
        assert len(body["cells"]) == 42
        assert body["grid"]["rows"] == 6 and body["grid"]["cols"] == 7
        for cell in body["cells"]:
            assert 0 <= cell["risk_score"] <= 100
            assert cell["risk_level"] in {"SAFE", "LOW", "MODERATE", "HIGH", "EXTREME"}
            b = cell["bounds"]
            assert b["min_lat"] < b["max_lat"] and b["min_lon"] < b["max_lon"]
        assert sum(body["summary"]["distribution"].values()) == 42

    def test_risk_model_config_is_fully_exposed(self, client):
        body = client.get("/api/risk/model").json()
        assert body["feature_count"] == 12
        assert abs(body["weight_sum"] - 1.0) < 1e-6
        for f in body["features"]:
            assert f["curve"] and f["rationale"] and f["weight"] is not None
        assert len(body["available_models"]) >= 2
        assert body["pipeline"]

    def test_monitoring_returns_the_whole_pipeline(self, client):
        body = client.get("/api/monitoring/loc_gaurikund", params={"timeline": False}).json()
        for key in ("location", "risk", "alert", "weather", "terrain", "hydrology",
                    "river_context", "features", "data_freshness", "mode"):
            assert key in body, key
        fresh = body["data_freshness"]
        assert fresh["total"] == 6
        assert {s["label"] for s in fresh["sources"]} == {
            "Weather", "Terrain", "River discharge", "River network",
            "Rainfall climatology", "Antecedent rainfall",
        }

    def test_dashboard_summary(self, client):
        body = client.get("/api/dashboard/summary").json()
        assert body["totals"]["monitoring_locations"] == 26
        assert sum(body["totals"]["distribution"].values()) == 26
        assert len(body["locations"]) == 26
        scores = [l["risk_score"] for l in body["locations"]]
        assert scores == sorted(scores, reverse=True), "locations must be ranked by risk"
        assert body["attribution"]

    def test_authority_view_includes_infrastructure(self, client):
        body = client.get("/api/dashboard/authority").json()
        assert "infrastructure" in body
        assert "exposure" in body

    def test_alerts_endpoint(self, client):
        body = client.get("/api/alerts", params={"min_severity": "INFO"}).json()
        assert body["count"] >= 1
        for a in body["alerts"]:
            assert a["severity"] in {"INFO", "ADVISORY", "WARNING", "CRITICAL"}
            assert a["headline"] and a["message"] and a["actions"]
        assert "does not issue official" in body["advisory_footer"]

    @pytest.mark.parametrize("severity", ["INFO", "ADVISORY", "WARNING", "CRITICAL"])
    def test_alert_severity_filter(self, client, severity):
        body = client.get("/api/alerts", params={"min_severity": severity}).json()
        order = {"INFO": 0, "ADVISORY": 1, "WARNING": 2, "CRITICAL": 3}
        for a in body["alerts"]:
            assert order[a["severity"]] >= order[severity]

    def test_flood_events_are_not_fabricated(self, client):
        body = client.get("/api/historical/flood-events").json()
        assert body["available"] is False
        assert body["events"] == []
        assert any("not fabricated" in n.lower() for n in body["notes"])
