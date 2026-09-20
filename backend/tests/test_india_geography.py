"""Pan-India geography, scope resolution and risk-engine consistency.

The whole point of these tests is to prove the system is genuinely
configuration-driven rather than quietly dependent on the two Himalayan pilot
regions. Every assertion that could pass for Uttarakhand alone is parametrised
across twelve states spanning Himalaya, coast, plateau, desert, delta and
cold desert.

The suite runs with the network disabled (see conftest), so anything that would
need a live upstream call must degrade honestly rather than invent a value -
which is itself one of the things asserted here.
"""
from __future__ import annotations

import pytest

from app.models.enums import RunMode
from app.services import flood_risk_engine, india_service, region_service

pytestmark = pytest.mark.skipif(
    not india_service.available(),
    reason="India dataset not generated - run scripts/build_india_geo.py",
)

# Deliberately spans every terrain and monsoon regime in the country, and both
# curated pilot regions, so a Himalaya-only assumption cannot survive.
STATES = [
    "uttarakhand",        # curated pilot - Himalaya
    "himachal_pradesh",   # curated pilot - Himalaya
    "kerala",             # Western Ghats / coastal
    "assam",              # Brahmaputra floodplain
    "maharashtra",        # Deccan plateau
    "telangana",          # Deccan plateau
    "rajasthan",          # arid
    "odisha",             # east coast / delta
    "west_bengal",        # Ganga delta
    "tamil_nadu",         # south coast
    "jammu_and_kashmir",  # Himalaya
    "ladakh",             # cold desert
]

CURATED = {"uttarakhand", "himachal_pradesh"}


class TestDataset:
    def test_india_has_36_states_and_union_territories(self):
        """28 states + 8 union territories."""
        assert len(india_service.list_states()) == 36

    def test_every_state_has_a_real_bbox_and_centre(self):
        for state in india_service.list_states():
            bbox = state["bbox"]
            assert bbox["min_lat"] < bbox["max_lat"]
            assert bbox["min_lon"] < bbox["max_lon"]
            # Inside India's real extent, generously bounded.
            assert 6.0 <= state["center"]["latitude"] <= 37.5
            assert 68.0 <= state["center"]["longitude"] <= 97.5

    def test_district_count_is_national_scale(self):
        total = sum(s["district_count"] for s in india_service.list_states())
        assert 600 < total < 900, f"expected roughly 780 districts, got {total}"

    def test_dataset_declares_its_provenance(self):
        info = india_service.dataset_info()
        assert "OpenStreetMap" in (info["source"] or "")
        assert "ODbL" in (info["attribution"] or "")
        assert info["generated_at"]


@pytest.mark.parametrize("state_id", STATES)
class TestEveryState:
    def test_state_resolves(self, state_id):
        assert india_service.get_state(state_id)["id"] == state_id

    def test_state_has_districts(self, state_id):
        districts = india_service.list_districts(state_id)
        assert districts, f"{state_id} returned no districts"
        assert all(d["state_id"] == state_id for d in districts)

    def test_state_scope_resolves_to_a_usable_region(self, state_id):
        region = region_service.get_region(state_id)
        assert region["id"] == state_id
        for key in ("bbox", "center", "risk_grid", "monitoring_locations"):
            assert key in region, f"{state_id} region missing {key}"

    def test_state_grid_cells_are_distinct_and_inside_the_state(self, state_id):
        cells = region_service.get_grid_cells(state_id)
        assert cells
        bbox = region_service.get_region(state_id)["bbox"]
        centres = {(c.center_lat, c.center_lon) for c in cells}
        assert len(centres) == len(cells), "grid produced duplicate cell centres"
        for cell in cells:
            assert bbox["min_lat"] <= cell.center_lat <= bbox["max_lat"]
            assert bbox["min_lon"] <= cell.center_lon <= bbox["max_lon"]

    def test_first_district_scope_resolves(self, state_id):
        district = india_service.list_districts(state_id)[0]
        region = region_service.get_region(district["id"])
        assert region["scope"] == "district"
        assert region["state_id"] == state_id

    def test_district_centroid_location_resolves_by_id(self, state_id):
        district = india_service.list_districts(state_id)[0]
        location = region_service.get_location(f"loc_{district['id']}")
        assert location.latitude == district["center"]["latitude"]
        assert location.settlement_type == "district_centroid"


class TestCuratedRegionsWin:
    """The pilot regions must not be replaced by synthesised versions."""

    @pytest.mark.parametrize("region_id", sorted(CURATED))
    def test_curated_region_is_not_synthesized(self, region_id):
        region = region_service.get_region(region_id)
        assert not region.get("synthesized"), f"{region_id} was overwritten by synthesis"

    @pytest.mark.parametrize("region_id", sorted(CURATED))
    def test_curated_region_keeps_its_hand_checked_data(self, region_id):
        region = region_service.get_region(region_id)
        assert region.get("rivers"), "curated river list was lost"
        assert region.get("monitoring_locations"), "curated locations were lost"

    def test_uttarakhand_keeps_its_named_pilot_locations(self):
        names = {loc.name for loc in region_service.get_locations("uttarakhand")}
        assert {"Joshimath", "Rudraprayag", "Gaurikund"} <= names


class TestNationalScope:
    def test_national_region_resolves(self):
        region = region_service.get_region("india")
        assert region["scope"] == "national"
        assert region["name"] == "India"

    def test_national_locations_are_the_states(self):
        locations = region_service.get_locations("india")
        assert len(locations) == 36
        assert all(
            loc.settlement_type in {"state_admin_centre", "state_centroid"}
            for loc in locations
        )

    def test_national_grid_is_masked_to_land(self):
        """A uniform grid over India's bbox would sit partly in the ocean."""
        cells = region_service.get_grid_cells("india")
        assert cells, "national grid produced no cells"
        rows = india_service._grid_for("national")  # noqa: SLF001 - config check
        assert len(cells) < rows[0] * rows[1], "no cells were masked out"
        for cell in cells:
            assert india_service.in_india(cell.center_lat, cell.center_lon)

    def test_state_centroid_location_resolves_by_id(self):
        location = region_service.get_location("loc_state_kerala")
        assert location.name == "Kerala"
        assert location.settlement_type in {"state_admin_centre", "state_centroid"}


class TestScopeGridResolution:
    """Coarse nationally, finer as the user drills in."""

    def test_district_grid_is_finer_than_the_state_that_contains_it(self):
        district = india_service.list_districts("kerala")[0]
        state_bbox = region_service.get_region("kerala")["bbox"]
        district_bbox = region_service.get_region(district["id"])["bbox"]

        def cell_span(bbox, rows, cols):
            return ((bbox["max_lat"] - bbox["min_lat"]) / rows) * (
                (bbox["max_lon"] - bbox["min_lon"]) / cols
            )

        state_cell = cell_span(state_bbox, *india_service._grid_for("state"))  # noqa: SLF001
        district_cell = cell_span(district_bbox, *india_service._grid_for("district"))  # noqa: SLF001
        assert district_cell < state_cell

    def test_grid_never_exceeds_the_batch_budget(self):
        """Every cell costs upstream API calls, so the grid must stay batchable."""
        from app.config.settings import settings

        for scope in ("national", "state", "district"):
            rows, cols = india_service._grid_for(scope)  # noqa: SLF001
            assert rows * cols <= settings.max_batch_points, f"{scope} grid too large"


class TestOverpassBudget:
    def test_wide_scopes_clamp_their_gis_query_box(self):
        """Rajasthan spans ~9 degrees; an unclamped OSM query would be abusive."""
        region = region_service.get_region("rajasthan")
        box = region["gis_query_bbox"]
        span = max(box["max_lat"] - box["min_lat"], box["max_lon"] - box["min_lon"])
        from app.config.settings import settings

        assert span <= settings.gis_max_bbox_degrees + 1e-6

    def test_small_district_box_is_left_alone(self):
        district = india_service.list_districts("kerala")[0]
        region = region_service.get_region(district["id"])
        assert region["gis_query_bbox"] == region["bbox"]


class TestUnknownScopes:
    def test_unknown_state_raises(self):
        with pytest.raises(region_service.RegionNotFound):
            region_service.get_region("atlantis")

    def test_unknown_district_raises(self):
        with pytest.raises(region_service.RegionNotFound):
            region_service.get_region("kerala__atlantis")

    def test_unknown_location_raises(self):
        with pytest.raises(region_service.LocationNotFound):
            region_service.get_location("loc_state_atlantis")


class TestRiskEngineIsIdenticalEverywhere:
    """One engine, one config - no per-state scoring path."""

    @staticmethod
    def _score(location_id: str) -> dict:
        from tests.conftest import BASE_TERRAIN, SEVERE_WEATHER, make_feature_set

        fs = make_feature_set({**BASE_TERRAIN, **SEVERE_WEATHER}, location_id=location_id)
        return flood_risk_engine.assess(fs, mode=RunMode.LIVE)

    def test_identical_inputs_score_identically_in_every_state(self):
        """The same 12 feature values must produce the same score nationwide.

        If any state had its own formula or thresholds, this would diverge.
        """
        scores = {sid: self._score(f"probe_{sid}")["risk_score"] for sid in STATES}
        assert len(set(scores.values())) == 1, f"engine diverged by state: {scores}"

    def test_the_same_model_id_is_reported_everywhere(self):
        model_ids = {self._score(f"probe_{sid}")["model"]["id"] for sid in STATES}
        assert len(model_ids) == 1

    def test_missing_features_renormalise_rather_than_count_as_zero(self):
        """A dropped source must not make conditions look safer."""
        from tests.conftest import BASE_TERRAIN, SEVERE_WEATHER, make_feature_set

        full = make_feature_set({**BASE_TERRAIN, **SEVERE_WEATHER})
        partial_values = {**BASE_TERRAIN, **SEVERE_WEATHER}
        partial_values.pop("river_discharge_anomaly")
        partial_values.pop("rainfall_anomaly")
        partial = make_feature_set(partial_values)

        full_risk = flood_risk_engine.assess(full, mode=RunMode.LIVE)
        partial_risk = flood_risk_engine.assess(partial, mode=RunMode.LIVE)

        assert partial_risk["feature_summary"]["available"] < full_risk["feature_summary"]["available"]
        # Renormalised, so a severe day stays severe with two sources missing.
        assert partial_risk["risk_score"] > 50
        assert partial_risk["confidence"] in {"LOW", "MEDIUM", "HIGH"}


class TestNoFabricatedCoverage:
    """With the network off, missing data must be reported, never invented."""

    def test_offline_district_locations_fall_back_to_the_real_centroid(self, client):
        district = india_service.list_districts("telangana")[0]

        # Drive the fallback itself rather than relying on this district being
        # absent from the bundled snapshot. It used to be; once the snapshot
        # grew to cover every state the request was answered from real seeded
        # OSM data instead, and the fallback stopped being exercised at all.
        from app.database.db import write_conn

        with write_conn() as conn:
            removed = conn.execute(
                "DELETE FROM api_cache WHERE source = 'overpass'"
            ).rowcount
        try:
            response = client.get("/api/locations", params={"region_id": district["id"]})
            assert response.status_code == 200
            body = response.json()
            assert body["count"] >= 1
            # Offline with nothing cached, the centroid is used - and said so.
            assert body["locations"][0]["settlement_type"] == "district_centroid"
            assert body["freshness"] != "LIVE"
            assert any("centre" in n or "unavailable" in n for n in body["notes"])
        finally:
            if removed:  # restore the snapshot for whatever runs next
                from app.services import seed_cache

                seed_cache.load_seed()

    def test_seeded_district_coverage_is_never_reported_as_live(self, client):
        """The bundled snapshot is real data, but it is not fresh data."""
        district = india_service.list_districts("telangana")[0]
        body = client.get("/api/locations", params={"region_id": district["id"]}).json()
        assert body["freshness"] != "LIVE"

    def test_a_synthesized_region_never_claims_curated_river_data(self):
        region = region_service.get_region("rajasthan")
        assert not region.get("rivers")
        assert not region.get("confluences")


class TestGeographyApi:
    def test_geography_root_describes_the_hierarchy(self, client):
        body = client.get("/api/geography").json()
        assert body["country"]["id"] == "india"
        assert body["levels"] == ["country", "state", "district", "location"]
        assert body["state_count"] == 36
        assert "OpenStreetMap" in body["source"]

    def test_states_endpoint_lists_every_state(self, client):
        body = client.get("/api/geography/states").json()
        assert body["count"] == 36
        assert len(body["states"]) == 36
        assert "ODbL" in body["attribution"]

    @pytest.mark.parametrize("state_id", STATES)
    def test_districts_endpoint_works_for_every_tested_state(self, client, state_id):
        response = client.get(f"/api/geography/states/{state_id}/districts")
        assert response.status_code == 200
        body = response.json()
        assert body["state_id"] == state_id
        assert body["count"] > 0
        assert all(d["state_id"] == state_id for d in body["districts"])

    def test_unknown_state_returns_404(self, client):
        assert client.get("/api/geography/states/atlantis/districts").status_code == 404

    def test_locations_endpoint_serves_every_scope(self, client):
        national = client.get("/api/locations", params={"region_id": "india"}).json()
        assert national["scope"] == "national"
        assert national["count"] == 36

        state = client.get("/api/locations", params={"region_id": "kerala"}).json()
        assert state["scope"] == "state"
        assert state["count"] == len(india_service.list_districts("kerala"))

    def test_locations_endpoint_stays_backward_compatible(self, client):
        """The curated pilot region must answer exactly as it always did."""
        body = client.get("/api/locations", params={"region_id": "uttarakhand"}).json()
        assert body["region_id"] == "uttarakhand"
        assert body["count"] == 26
        assert {"id", "name", "district", "latitude", "longitude"} <= set(body["locations"][0])

    def test_unknown_region_returns_404(self, client):
        assert client.get("/api/locations", params={"region_id": "atlantis"}).status_code == 404


class TestScopedSummaries:
    """One aggregation path; the scope decides what a ranked row means."""

    def test_national_summary_ranks_states(self, client):
        body = client.get("/api/dashboard/summary", params={"region_id": "india"}).json()
        assert body["row_kind"] == "state"
        assert body["region"]["scope"] == "national"
        assert body["totals"]["monitoring_locations"] == 36

    def test_state_summary_ranks_districts(self, client):
        body = client.get("/api/dashboard/summary", params={"region_id": "kerala"}).json()
        assert body["row_kind"] == "district"
        assert body["region"]["scope"] == "state"
        assert body["totals"]["monitoring_locations"] == len(
            india_service.list_districts("kerala")
        )

    def test_curated_region_summary_still_ranks_locations(self, client):
        body = client.get("/api/dashboard/summary", params={"region_id": "uttarakhand"}).json()
        assert body["row_kind"] == "location"
        assert body["totals"]["monitoring_locations"] == 26

    def test_every_summary_reports_a_risk_distribution(self, client):
        for region_id in ("india", "kerala", "assam", "uttarakhand"):
            body = client.get(
                "/api/dashboard/summary", params={"region_id": region_id}
            ).json()
            distribution = body["totals"]["distribution"]
            assert sum(distribution.values()) == body["totals"]["monitoring_locations"]


class TestSimulationAnywhere:
    """The simulator must work at any Indian location, not just the pilot."""

    def test_simulation_changes_risk_at_a_non_pilot_location(self, client):
        district = india_service.list_districts("kerala")[0]
        location_id = f"loc_{district['id']}"

        before = client.get(f"/api/monitoring/{location_id}").json()
        assert before["risk"]["mode"] == "LIVE"

        run = client.post(
            "/api/simulation/run",
            json={"scenario_id": None, "overrides": {"rainfall_intensity": 95.0,
                                                     "rainfall_3h": 115.0,
                                                     "antecedent_precipitation_index": 150.0},
                  "location_id": location_id},
        )
        assert run.status_code == 200
        assert run.json()["active"] is True

        during = client.get(f"/api/monitoring/{location_id}").json()
        assert during["risk"]["mode"] == "SIMULATION"
        assert during["risk"]["risk_score"] > before["risk"]["risk_score"]

        reset = client.post("/api/simulation/reset", params={"location_id": location_id})
        assert reset.status_code == 200
        assert reset.json()["active"] is False

        after = client.get(f"/api/monitoring/{location_id}").json()
        assert after["risk"]["mode"] == "LIVE"
        assert after["risk"]["risk_score"] == before["risk"]["risk_score"]


class TestDatabasePersistence:
    def test_new_geographic_columns_exist(self):
        from app.database.db import get_conn

        with get_conn() as conn:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(monitoring_locations)")}
        assert {"country", "state_id", "district_id", "origin"} <= columns

    def test_resolved_locations_persist_and_resolve_after_a_cache_clear(self):
        """Settlements found via OSM must still resolve on a later request."""
        rows = [{
            "id": "loc_osm_test_1", "name": "Test Settlement",
            "latitude": 10.5, "longitude": 76.2, "settlement_type": "town",
            "district": "Palakkad", "district_id": "kerala__palakkad", "state_id": "kerala",
        }]
        written = region_service.remember_locations(
            rows, region_id="kerala__palakkad", origin="osm"
        )
        assert written == 1

        found = region_service.get_location("loc_osm_test_1")
        assert found.name == "Test Settlement"
        assert found.latitude == 10.5
        assert found.district == "Palakkad"


class TestRepresentativePoints:
    """A state must be assessed from a point that is actually inside it."""

    def test_most_states_use_a_real_administrative_centre(self):
        sources = [
            india_service.representative_point(s)["source"]
            for s in india_service.list_states()
        ]
        real = sources.count("osm_admin_centre")
        assert real >= 28, f"only {real}/36 states have a real admin centre"

    def test_fragmented_union_territory_is_not_assessed_from_its_bbox_centre(self):
        """Puducherry's four enclaves are spread across South India.

        Its bounding-box centre falls in inland Andhra Pradesh, hundreds of
        kilometres from any part of the territory, so ranking it from that point
        would attribute another state's weather to Puducherry.
        """
        state = india_service.get_state("puducherry")
        point = india_service.representative_point(state)
        assert point["source"] == "osm_admin_centre"
        # The bbox centre is the wrong answer; the real one must differ from it.
        assert abs(point["latitude"] - state["center"]["latitude"]) > 0.5

    def test_national_locations_use_the_representative_point(self):
        locations = {loc.id: loc for loc in region_service.get_locations("india")}
        for state in india_service.list_states():
            point = india_service.representative_point(state)
            location = locations[f"loc_state_{state['id']}"]
            assert location.latitude == pytest.approx(float(point["latitude"]))
            assert location.longitude == pytest.approx(float(point["longitude"]))
