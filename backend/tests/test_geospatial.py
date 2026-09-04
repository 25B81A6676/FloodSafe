"""Geometry, terrain derivation and validation tests.

These cover the computations the platform performs itself rather than reads
from an API: point-to-polyline distance, DEM slope, and input validation.
"""
from __future__ import annotations

import math

import pytest

from app.services.elevation_service import _offsets, _slope_and_relief
from app.services.osm_service import (
    _clip_length_within_radius,
    _point_segment_distance_m,
    _polyline_distance_m,
    _polyline_length_m,
)
from app.services.region_service import get_grid_cells, haversine_m
from app.services.validation import (
    ValidationReport,
    check_monotonic_accumulation,
    clean_number,
    dedupe_series,
    valid_coordinates,
)


class TestDistance:
    def test_haversine_against_a_known_separation(self):
        # One degree of latitude is close to 111.2 km anywhere on the globe.
        d = haversine_m(30.0, 79.0, 31.0, 79.0)
        assert 110_500 < d < 111_600

    def test_point_on_segment_is_zero(self):
        d = _point_segment_distance_m(30.5, 79.0, 30.0, 79.0, 31.0, 79.0)
        assert d < 1.0

    def test_perpendicular_distance_to_a_meridian(self):
        """A point 0.01 deg east of a north-south line, at latitude 30."""
        expected = 0.01 * 111_320 * math.cos(math.radians(30.0))
        d = _point_segment_distance_m(30.0, 79.01, 29.9, 79.0, 30.1, 79.0)
        assert abs(d - expected) < expected * 0.02

    def test_distance_clamps_to_segment_ends(self):
        """Beyond the end of a segment, distance is measured to the endpoint."""
        d = _point_segment_distance_m(32.0, 79.0, 30.0, 79.0, 30.5, 79.0)
        expected = haversine_m(32.0, 79.0, 30.5, 79.0)
        assert abs(d - expected) < expected * 0.02

    def test_polyline_distance_takes_the_nearest_segment(self):
        line = [(30.0, 79.0), (30.0, 79.5), (30.5, 79.5)]
        assert _polyline_distance_m(30.0, 79.25, line) < 50
        assert _polyline_distance_m(30.25, 79.5, line) < 50

    def test_polyline_distance_never_exceeds_vertex_distance(self):
        """A segment-aware distance must be <= the nearest-vertex distance."""
        line = [(30.0, 79.0), (30.4, 79.0)]
        point = (30.2, 79.02)
        seg = _polyline_distance_m(point[0], point[1], line)
        vertex = min(haversine_m(point[0], point[1], a, b) for a, b in line)
        assert seg <= vertex + 1e-6

    def test_polyline_length(self):
        length = _polyline_length_m([(30.0, 79.0), (30.1, 79.0), (30.2, 79.0)])
        assert abs(length - 0.2 * 111_320) < 500

    def test_clipped_length_is_bounded_by_total_length(self):
        line = [(30.0, 79.0), (30.5, 79.0)]
        total = _polyline_length_m(line)
        clipped = _clip_length_within_radius(30.0, 79.0, line, 3500.0)
        assert 0 <= clipped <= total

    def test_empty_polyline_is_infinitely_far(self):
        assert _polyline_distance_m(30.0, 79.0, []) == float("inf")


class TestTerrainDerivation:
    def test_stencil_has_nine_points_centred_correctly(self):
        pts = _offsets(30.0, 79.0, 500.0)
        assert len(pts) == 9
        assert pts[4] == (30.0, 79.0)
        # North row is north of the south row.
        assert pts[0][0] > pts[8][0]

    def test_stencil_spacing_matches_the_requested_distance(self):
        pts = _offsets(30.0, 79.0, 500.0)
        north_south = haversine_m(pts[1][0], pts[1][1], pts[7][0], pts[7][1])
        east_west = haversine_m(pts[3][0], pts[3][1], pts[5][0], pts[5][1])
        assert abs(north_south - 1000) < 25
        assert abs(east_west - 1000) < 25

    def test_flat_terrain_has_zero_slope(self):
        slope, aspect, relief = _slope_and_relief([100.0] * 9, 30.0)
        assert slope == 0.0
        assert relief == 0.0

    def test_known_gradient_gives_the_expected_angle(self):
        """North row 1500 m, south row 500 m, 1000 m apart: a 1:1 gradient, so 45 degrees."""
        elevs = [
            1500.0, 1500.0, 1500.0,
            1000.0, 1000.0, 1000.0,
            500.0, 500.0, 500.0,
        ]
        slope, aspect, relief = _slope_and_relief(elevs, 30.0)
        assert abs(slope - math.degrees(math.atan(1000.0 / 1000.0))) < 0.1
        assert relief == 1000.0

    def test_slope_is_always_within_zero_and_ninety(self):
        for elevs in ([0.0] * 9, [8000.0, 0.0] * 4 + [4000.0], list(range(9))):
            slope, _, _ = _slope_and_relief([float(e) for e in elevs], 30.0)
            assert 0.0 <= slope <= 90.0

    def test_missing_samples_fall_back_to_the_centre(self):
        slope, aspect, relief = _slope_and_relief(
            [None, None, None, None, 1200.0, None, None, None, None], 30.0
        )
        assert slope == 0.0
        assert relief == 0.0

    def test_all_samples_missing_returns_none(self):
        assert _slope_and_relief([None] * 9, 30.0) == (None, None, None)


class TestGrid:
    def test_grid_tiles_the_bbox_without_gaps_or_overlap(self):
        cells = get_grid_cells("uttarakhand")
        assert len(cells) == 42
        lats = sorted({round(c.min_lat, 5) for c in cells})
        lons = sorted({round(c.min_lon, 5) for c in cells})
        assert len(lats) == 6 and len(lons) == 7
        for c in cells:
            assert c.min_lat < c.center_lat < c.max_lat
            assert c.min_lon < c.center_lon < c.max_lon

    def test_cell_ids_are_unique(self):
        cells = get_grid_cells("uttarakhand")
        assert len({c.id for c in cells}) == len(cells)


class TestValidation:
    @pytest.mark.parametrize(
        "lat,lon,ok",
        [
            (30.0, 79.0, True), (-90, -180, True), (90, 180, True),
            (91, 79, False), (30, 181, False), (None, 79, False),
            ("abc", 79, False), (float("nan"), 79, False),
        ],
    )
    def test_coordinate_validation(self, lat, lon, ok):
        assert valid_coordinates(lat, lon) is ok

    def test_impossible_rainfall_is_clamped_and_reported(self):
        report = ValidationReport()
        value = clean_number(99_999, "precipitation_mm_h", report)
        assert value == 400.0
        assert report.warnings and "outside plausible range" in report.warnings[0]

    def test_negative_rainfall_is_clamped_to_zero(self):
        report = ValidationReport()
        assert clean_number(-5, "rain_24h", report) == 0.0

    def test_non_numeric_is_an_error(self):
        report = ValidationReport()
        assert clean_number("wet", "rain_24h", report, default=0.0) == 0.0
        assert not report.ok
        assert "rain_24h" in report.dropped_fields

    def test_missing_value_uses_the_default(self):
        report = ValidationReport()
        assert clean_number(None, "rain_24h", report, default=0.0) == 0.0
        assert report.repaired

    def test_nan_and_infinity_are_rejected(self):
        report = ValidationReport()
        assert clean_number(float("nan"), "rain_24h", report) is None
        assert clean_number(float("inf"), "rain_24h", report) is None
        assert len(report.errors) == 2

    def test_non_monotonic_accumulation_is_flagged(self):
        report = ValidationReport()
        check_monotonic_accumulation(
            {"rain_1h": 50.0, "rain_3h": 10.0, "rain_24h": 100.0}, report
        )
        assert any("not monotonic" in w for w in report.warnings)

    def test_valid_accumulation_passes_cleanly(self):
        report = ValidationReport()
        check_monotonic_accumulation(
            {"rain_1h": 5.0, "rain_3h": 12.0, "rain_24h": 40.0}, report
        )
        assert not report.warnings

    def test_duplicate_timestamps_are_removed(self):
        points = [
            {"time": "2026-09-03T10:00", "precipitation": 1},
            {"time": "2026-09-03T10:00", "precipitation": 2},
            {"time": "2026-09-03T11:00", "precipitation": 3},
        ]
        out = dedupe_series(points)
        assert len(out) == 2
        assert out[0]["precipitation"] == 2  # last occurrence wins
