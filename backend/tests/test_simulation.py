"""Simulation engine tests."""
from __future__ import annotations

import pytest

from app.services import flood_risk_engine, simulation_service
from app.services.feature_engineering import apply_overrides
from tests.conftest import BASE_TERRAIN, CALM_WEATHER, make_feature_set

SCENARIOS = ["normal", "heavy_rain", "flash_flood_warning", "extreme_flash_flood"]


class TestScenarioDefinitions:
    def test_all_four_scenarios_load(self):
        ids = {s["id"] for s in simulation_service.list_scenarios()}
        assert set(SCENARIOS) <= ids

    def test_scenarios_are_labelled_as_simulation(self):
        for s in simulation_service.list_scenarios():
            assert s["data_kind"] == "SIMULATION"

    def test_scenario_overrides_reference_real_features(self):
        from app.services.feature_engineering import FEATURE_KEYS

        for s in simulation_service.list_scenarios():
            for key in s["overrides"]:
                assert key in FEATURE_KEYS, f"{s['id']} references unknown feature {key}"

    def test_scenarios_escalate_in_severity(self):
        scores = []
        for sid in SCENARIOS:
            scenario = simulation_service.get_scenario(sid)
            fs = make_feature_set({**BASE_TERRAIN, **CALM_WEATHER})
            apply_overrides(fs, scenario["overrides"])
            scores.append(flood_risk_engine.assess(fs)["risk_score"])
        assert scores == sorted(scores), scores

    @pytest.mark.parametrize("scenario_id", SCENARIOS)
    def test_scenario_lands_in_its_expected_band(self, scenario_id):
        scenario = simulation_service.get_scenario(scenario_id)
        fs = make_feature_set({**BASE_TERRAIN, **CALM_WEATHER})
        apply_overrides(fs, scenario["overrides"])
        result = flood_risk_engine.assess(fs)
        low, high = scenario["expected_score_range"]
        assert low <= result["risk_score"] <= high, (
            f"{scenario_id} scored {result['risk_score']}, expected {low}-{high}"
        )

    def test_extreme_scenario_is_extreme(self):
        scenario = simulation_service.get_scenario("extreme_flash_flood")
        fs = make_feature_set({**BASE_TERRAIN, **CALM_WEATHER})
        apply_overrides(fs, scenario["overrides"])
        assert flood_risk_engine.assess(fs)["risk_level"] == "EXTREME"


class TestOverrides:
    def test_overrides_are_marked_simulated(self):
        fs = make_feature_set({**BASE_TERRAIN, **CALM_WEATHER})
        apply_overrides(fs, {"rainfall_intensity": 80.0})
        fv = fs.features["rainfall_intensity"]
        assert fv.simulated is True
        assert fv.freshness == "SIMULATION"
        assert fv.raw == 80.0
        assert fs.any_simulated

    def test_untouched_features_stay_real(self):
        fs = make_feature_set({**BASE_TERRAIN, **CALM_WEATHER})
        apply_overrides(fs, {"rainfall_intensity": 80.0})
        assert fs.features["slope"].simulated is False
        assert fs.features["slope"].freshness == "LIVE"

    def test_assessment_reports_simulation_mode(self):
        fs = make_feature_set({**BASE_TERRAIN, **CALM_WEATHER})
        apply_overrides(fs, {"rainfall_intensity": 80.0})
        result = flood_risk_engine.assess(fs)
        assert result["mode"] == "SIMULATION"
        assert "rainfall_intensity" in result["feature_summary"]["simulated"]

    def test_sanitize_rejects_unknown_features(self):
        clean, rejected = simulation_service.sanitize_overrides(
            {"rainfall_intensity": 50, "not_a_feature": 1, "slope": "abc"}
        )
        assert clean == {"rainfall_intensity": 50.0}
        assert len(rejected) == 2

    def test_sanitize_clamps_out_of_range_values(self):
        clean, rejected = simulation_service.sanitize_overrides({"rainfall_intensity": 9999})
        assert clean["rainfall_intensity"] == 120.0
        assert any("clamped" in r for r in rejected)

    def test_every_control_maps_to_a_weighted_feature(self):
        for control in simulation_service.controls():
            assert control["model_weight"] is not None and control["model_weight"] > 0
            assert control["min"] < control["max"]


class TestSimulationState:
    def test_run_then_reset(self):
        state = simulation_service.run(scenario_id="extreme_flash_flood")
        assert state["active"] is True
        assert simulation_service.current_state()["active"] is True

        simulation_service.reset()
        assert simulation_service.current_state()["active"] is False
        assert simulation_service.current_state()["overrides"] == {}

    def test_unknown_scenario_raises(self):
        with pytest.raises(KeyError):
            simulation_service.run(scenario_id="apocalypse")

    def test_merge_preserves_earlier_overrides(self):
        simulation_service.run(overrides={"rainfall_intensity": 40})
        simulation_service.run(overrides={"slope": 30}, merge=True)
        overrides = simulation_service.current_state()["overrides"]
        assert overrides["rainfall_intensity"] == 40
        assert overrides["slope"] == 30

    def test_without_merge_overrides_are_replaced(self):
        simulation_service.run(overrides={"rainfall_intensity": 40})
        simulation_service.run(overrides={"slope": 30}, merge=False)
        assert "rainfall_intensity" not in simulation_service.current_state()["overrides"]

    def test_derived_readouts_are_flagged_as_scenario_attributes(self):
        readouts = simulation_service.derived_readouts({"river_discharge_anomaly": 4.2})
        assert readouts["is_scenario_attribute"] is True
        assert readouts["river_level_label"] == "Critical"
        assert readouts["water_rise_rate_cm_per_hr"] > 0


class TestSimulationApi:
    def test_run_and_reset_through_the_api(self, client):
        r = client.post("/api/simulation/run", json={"scenario_id": "extreme_flash_flood"})
        assert r.status_code == 200
        assert r.json()["active"] is True

        risk = client.get("/api/risk/loc_gaurikund").json()["risk"]
        assert risk["mode"] == "SIMULATION"
        assert risk["risk_level"] == "EXTREME"

        assert client.post("/api/simulation/reset").status_code == 200
        assert client.get("/api/risk/loc_gaurikund").json()["risk"]["mode"] == "LIVE"

    def test_simulation_changes_the_map(self, client):
        before = client.get("/api/risk/map").json()
        client.post("/api/simulation/run", json={"scenario_id": "extreme_flash_flood"})
        after = client.get("/api/risk/map").json()

        assert after["mode"] == "SIMULATION"
        # The whole grid must escalate sharply, not merely tick upward.
        assert after["summary"]["max"] >= before["summary"]["max"] + 30
        assert after["summary"]["min"] > before["summary"]["max"]

        severe = (
            after["summary"]["distribution"]["HIGH"]
            + after["summary"]["distribution"]["EXTREME"]
        )
        # Offline the grid loses the OpenStreetMap river-proximity feature, so
        # cells top out at HIGH rather than EXTREME. Either way every cell must
        # reach at least HIGH under a cloudburst scenario.
        assert severe == len(after["cells"]), after["summary"]["distribution"]
        client.post("/api/simulation/reset")

    def test_simulation_escalates_alerts(self, client):
        client.post("/api/simulation/run", json={"scenario_id": "extreme_flash_flood"})
        body = client.get("/api/alerts", params={"min_severity": "CRITICAL"}).json()
        assert body["count"] > 0
        alert = body["alerts"][0]
        assert alert["is_simulation"] is True
        assert "SIMULATED" in alert["simulation_notice"]
        client.post("/api/simulation/reset")

    def test_run_requires_a_payload(self, client):
        assert client.post("/api/simulation/run", json={}).status_code == 400

    def test_unknown_scenario_is_404(self, client):
        r = client.post("/api/simulation/run", json={"scenario_id": "apocalypse"})
        assert r.status_code == 404

    def test_manual_override_moves_the_score(self, client):
        baseline = client.get("/api/risk/loc_dehradun").json()["risk"]["risk_score"]
        client.post("/api/simulation/run", json={"overrides": {"rainfall_intensity": 100.0}})
        raised = client.get("/api/risk/loc_dehradun").json()["risk"]["risk_score"]
        assert raised > baseline
        client.post("/api/simulation/reset")

    def test_controls_endpoint(self, client):
        body = client.get("/api/simulation/controls").json()
        assert len(body["controls"]) == 9
        groups = {c["group"] for c in body["controls"]}
        assert groups == {"Rainfall", "Catchment", "Terrain"}
