"""Risk engine behaviour."""
from __future__ import annotations

import pytest

from app.models.enums import RiskLevel
from app.services import flood_risk_engine, risk_config
from tests.conftest import BASE_TERRAIN, CALM_WEATHER, SEVERE_WEATHER, make_feature_set


class TestScoring:
    def test_calm_conditions_give_low_risk(self, calm_features):
        result = flood_risk_engine.assess(calm_features)
        assert result["risk_score"] <= 40, result["risk_score"]
        assert result["risk_level"] in {RiskLevel.SAFE.value, RiskLevel.LOW.value}

    def test_severe_conditions_give_extreme_risk(self, severe_features):
        result = flood_risk_engine.assess(severe_features)
        assert result["risk_score"] >= 81, result["risk_score"]
        assert result["risk_level"] == RiskLevel.EXTREME.value

    def test_heavy_rain_beats_calm_on_identical_terrain(self, calm_features, severe_features):
        calm = flood_risk_engine.assess(calm_features)["risk_score"]
        severe = flood_risk_engine.assess(severe_features)["risk_score"]
        assert severe > calm + 30

    def test_vulnerable_terrain_scores_higher_than_benign(self):
        """Same weather, different terrain: steep and near a channel must score higher."""
        weather = {
            "rainfall_intensity": 15.0, "rainfall_3h": 35.0, "rainfall_24h": 90.0,
            "rainfall_forecast_24h": 60.0, "rainfall_trend": 2.0,
            "rainfall_anomaly": 85.0, "antecedent_precipitation_index": 60.0,
            "river_discharge_anomaly": 1.6,
        }
        vulnerable = make_feature_set({
            **weather, "slope": 32.0, "terrain_relief": 900.0,
            "river_proximity": 60.0, "stream_density": 3.0,
        })
        benign = make_feature_set({
            **weather, "slope": 2.0, "terrain_relief": 40.0,
            "river_proximity": 4200.0, "stream_density": 0.2,
        })
        assert (
            flood_risk_engine.assess(vulnerable)["risk_score"]
            > flood_risk_engine.assess(benign)["risk_score"]
        )

    def test_score_is_monotonic_in_rainfall_intensity(self):
        scores = []
        for intensity in (0, 5, 15, 30, 60, 100):
            fs = make_feature_set({**BASE_TERRAIN, **CALM_WEATHER, "rainfall_intensity": intensity})
            scores.append(flood_risk_engine.assess(fs)["risk_score"])
        assert scores == sorted(scores), scores
        assert scores[-1] > scores[0]

    def test_score_bounds_are_respected(self):
        maxed = make_feature_set({
            "rainfall_intensity": 500, "rainfall_3h": 500, "rainfall_24h": 900,
            "rainfall_forecast_24h": 900, "rainfall_trend": 100,
            "rainfall_anomaly": 100, "antecedent_precipitation_index": 500,
            "slope": 89, "terrain_relief": 5000, "river_proximity": 0,
            "stream_density": 40, "river_discharge_anomaly": 50,
        })
        result = flood_risk_engine.assess(maxed)
        assert 0 <= result["risk_score"] <= 100
        assert result["risk_level"] == RiskLevel.EXTREME.value

        floored = make_feature_set({
            "rainfall_intensity": 0, "rainfall_3h": 0, "rainfall_24h": 0,
            "rainfall_forecast_24h": 0, "rainfall_trend": -10,
            "rainfall_anomaly": 0, "antecedent_precipitation_index": 0,
            "slope": 0, "terrain_relief": 0, "river_proximity": 50000,
            "stream_density": 0, "river_discharge_anomaly": 0,
        })
        assert flood_risk_engine.assess(floored)["risk_score"] >= 0


class TestClassification:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (0, "SAFE"), (20, "SAFE"), (20.4, "SAFE"),
            (20.6, "LOW"), (21, "LOW"), (40, "LOW"),
            (40.6, "MODERATE"), (41, "MODERATE"), (60, "MODERATE"),
            (61, "HIGH"), (80, "HIGH"),
            (81, "EXTREME"), (100, "EXTREME"), (140, "EXTREME"),
        ],
    )
    def test_boundaries(self, score, expected):
        """Fractional scores must not fall between configured classes."""
        assert risk_config.classify(score).value == expected

    def test_displayed_score_always_matches_its_class(self):
        for raw in [i * 0.37 for i in range(280)]:
            level = risk_config.classify(raw)
            meta = risk_config.risk_class_meta(level)
            assert int(meta["min"]) <= round(min(raw, 100)) <= int(meta["max"])


class TestExplainability:
    def test_contributors_are_ranked_and_complete(self, severe_features):
        result = flood_risk_engine.assess(severe_features)
        contributors = result["contributors"]
        assert len(contributors) == 12
        shares = [c["contribution"] for c in contributors]
        assert shares == sorted(shares, reverse=True)
        assert abs(sum(c["share_pct"] for c in contributors) - 100.0) < 0.5

    def test_every_contributor_carries_provenance(self, severe_features):
        for c in flood_risk_engine.assess(severe_features)["contributors"]:
            assert c["factor"] and c["source"] and c["freshness"]
            assert c["display_value"]
            assert 0.0 <= c["normalized"] <= 1.0

    def test_rainfall_dominates_a_cloudburst(self, severe_features):
        result = flood_risk_engine.assess(severe_features)
        assert result["contributors"][0]["key"] == "rainfall_intensity"
        assert result["contributors"][0]["impact"] == "HIGH"

    def test_primary_factors_exclude_low_impact(self, severe_features):
        result = flood_risk_engine.assess(severe_features)
        assert all(f["impact"] != "LOW" for f in result["primary_factors"])


class TestMissingData:
    def test_missing_features_are_renormalised_not_zeroed(self):
        """A failed source must not silently reduce the risk score."""
        full = make_feature_set({**BASE_TERRAIN, **SEVERE_WEATHER})
        partial_values = {**BASE_TERRAIN, **SEVERE_WEATHER}
        partial_values.pop("river_discharge_anomaly")
        partial_values.pop("rainfall_anomaly")
        partial = make_feature_set(partial_values)

        full_score = flood_risk_engine.assess(full)["risk_score"]
        partial_result = flood_risk_engine.assess(partial)

        assert partial_result["model"]["weight_coverage"] < 1.0
        # Renormalisation keeps the score in the same band rather than collapsing it.
        assert partial_result["risk_score"] > full_score - 12
        assert "rainfall_anomaly" in partial_result["feature_summary"]["missing"]

    def test_confidence_degrades_with_missing_data(self):
        full = flood_risk_engine.assess(make_feature_set({**BASE_TERRAIN, **SEVERE_WEATHER}))
        sparse = flood_risk_engine.assess(make_feature_set({
            "rainfall_intensity": 95.0, "slope": 18.0,
        }))
        order = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
        assert order[sparse["confidence"]] < order[full["confidence"]]

    def test_confidence_degrades_with_demo_data(self):
        live = flood_risk_engine.assess(
            make_feature_set({**BASE_TERRAIN, **CALM_WEATHER}, freshness="LIVE")
        )
        demo = flood_risk_engine.assess(
            make_feature_set({**BASE_TERRAIN, **CALM_WEATHER}, freshness="DEMO")
        )
        assert demo["data_quality_score"] < live["data_quality_score"]
        assert demo["confidence"] == "LOW"

    def test_no_features_available_does_not_crash(self):
        result = flood_risk_engine.assess(make_feature_set({}))
        assert result["risk_score"] == 0
        assert result["model"]["weight_coverage"] == 0.0


class TestConfiguration:
    def test_weights_sum_to_one(self):
        cfg = risk_config.get_config()
        total = sum(f["weight"] for f in cfg["features"].values())
        assert abs(total - 1.0) < 1e-9, total

    def test_every_feature_has_a_monotonic_curve(self):
        for key, f in risk_config.get_config()["features"].items():
            curve = f["curve"]
            xs = [p[0] for p in curve]
            ys = [p[1] for p in curve]
            assert xs == sorted(xs), f"{key}: breakpoints out of order"
            assert all(0.0 <= y <= 1.0 for y in ys), f"{key}: output outside 0-1"
            if f["direction"] == "increasing":
                assert ys == sorted(ys), f"{key}: not monotonically increasing"
            else:
                assert ys == sorted(ys, reverse=True), f"{key}: not monotonically decreasing"

    def test_risk_classes_cover_every_integer_score(self):
        classes = risk_config.get_config()["risk_classes"]
        covered = set()
        for c in classes:
            covered.update(range(int(c["min"]), int(c["max"]) + 1))
        assert covered == set(range(0, 101))

    def test_normalisation_clamps_outside_the_curve(self):
        assert risk_config.normalize("rainfall_intensity", -50) == 0.0
        assert risk_config.normalize("rainfall_intensity", 10_000) == 1.0
        assert risk_config.normalize("river_proximity", 0) == 1.0
        assert risk_config.normalize("river_proximity", 10_000) < 0.05

    def test_river_proximity_decreases_with_distance(self):
        near = risk_config.normalize("river_proximity", 50)
        far = risk_config.normalize("river_proximity", 3000)
        assert near > far


class TestRegionalProfiles:
    """Optional per-region normalisation, shipped with no overrides."""

    def test_no_profiles_ship_by_default(self):
        """Inventing region-specific thresholds would be a fabrication."""
        from app.services import risk_config

        assert risk_config._profiles() == {}  # noqa: SLF001 - asserting shipped state
        assert risk_config.profile_for("kerala") is None
        assert risk_config.profile_for("kerala__wayanad") is None

    def test_normalisation_is_unchanged_without_a_profile(self):
        from app.services import risk_config

        assert risk_config.normalize("rainfall_24h", 64.4) == risk_config.normalize(
            "rainfall_24h", 64.4, None
        )

    def test_a_profile_can_override_a_curve_without_touching_scoring_code(self):
        from app.services import risk_config

        default = risk_config.normalize("rainfall_24h", 64.4)
        profile = {"features": {"rainfall_24h": {"curve": [[0, 0.0], [64.4, 1.0]]}}}
        overridden = risk_config.normalize("rainfall_24h", 64.4, profile)
        assert default == pytest.approx(0.45)
        assert overridden == pytest.approx(1.0)

    def test_a_profile_override_merges_rather_than_replaces(self):
        """A profile restates only what differs; the rest comes from the global config."""
        from app.services import risk_config

        profile = {"features": {"rainfall_24h": {"weight": 0.5}}}
        merged = risk_config.feature_config("rainfall_24h", profile)
        assert merged["weight"] == 0.5
        assert merged["curve"] == risk_config.feature_config("rainfall_24h")["curve"]
        assert merged["unit"] == "mm"

    def test_district_profile_falls_back_to_its_state(self, monkeypatch):
        from app.services import risk_config

        monkeypatch.setattr(risk_config, "_profiles", lambda: {"kerala": {"label": "test"}})
        assert risk_config.profile_for("kerala__wayanad")["label"] == "test"
        assert risk_config.profile_for("assam__cachar") is None
