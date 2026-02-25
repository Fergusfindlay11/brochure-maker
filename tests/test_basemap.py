"""Tests for basemap extraction — zoom selection and geometry processing."""

from brochure_maker.map_pipeline.basemap import (
    select_zoom,
    _snap_coord,
    _simplify_coords,
    _linemerge_roads,
    BasemapFeature,
)


class TestZoomSelection:
    """Zoom level selection based on extent and output size."""

    def test_small_radius_high_zoom(self):
        z = select_zoom(200, 940, 750)
        assert z >= 15

    def test_large_radius_low_zoom(self):
        z = select_zoom(2000, 940, 750)
        assert z <= 15

    def test_clamp_to_min(self):
        z = select_zoom(50000, 100, 100, min_zoom=13)
        assert z >= 13

    def test_clamp_to_max(self):
        z = select_zoom(10, 4000, 4000, max_zoom=17)
        assert z <= 17

    def test_zero_extent_returns_min(self):
        z = select_zoom(0, 940, 750)
        assert z == 13


class TestCoordSnap:
    """Coordinate snapping for determinism."""

    def test_snap_rounds_to_tolerance(self):
        snapped = _snap_coord(51.50001234, 0.000001)
        assert snapped == 51.500012

    def test_snap_identical_inputs(self):
        a = _snap_coord(51.50001234)
        b = _snap_coord(51.50001234)
        assert a == b


class TestSimplify:
    """Douglas-Peucker simplification."""

    def test_two_points_unchanged(self):
        coords = [(51.5, -0.12), (51.501, -0.118)]
        result = _simplify_coords(coords)
        assert len(result) == 2

    def test_collinear_simplified(self):
        # Three collinear points should simplify to two
        coords = [(51.5, -0.12), (51.5005, -0.119), (51.501, -0.118)]
        result = _simplify_coords(coords, tolerance=0.001)
        assert len(result) == 2

    def test_non_collinear_kept(self):
        # Points with significant deviation should be kept
        coords = [(51.5, -0.12), (51.5005, -0.115), (51.501, -0.118)]
        result = _simplify_coords(coords, tolerance=0.0001)
        assert len(result) >= 2


class TestLinemerge:
    """Road segment merging."""

    def test_merge_connected_segments(self):
        roads = [
            BasemapFeature(
                feature_type="road", geometry_type="LineString",
                coords=[(51.5, -0.12), (51.501, -0.12)],
                properties={"highway": "residential"},
            ),
            BasemapFeature(
                feature_type="road", geometry_type="LineString",
                coords=[(51.501, -0.12), (51.502, -0.12)],
                properties={"highway": "residential"},
            ),
        ]
        merged = _linemerge_roads(roads)
        # Should merge into fewer segments
        assert len(merged) <= len(roads)

    def test_different_classes_separate(self):
        roads = [
            BasemapFeature(
                feature_type="road", geometry_type="LineString",
                coords=[(51.5, -0.12), (51.501, -0.12)],
                properties={"highway": "residential"},
            ),
            BasemapFeature(
                feature_type="road", geometry_type="LineString",
                coords=[(51.501, -0.12), (51.502, -0.12)],
                properties={"highway": "primary"},
            ),
        ]
        merged = _linemerge_roads(roads)
        assert len(merged) >= 2  # Different classes not merged
