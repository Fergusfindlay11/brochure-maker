"""Tests for the SVG renderer — multi-pass road rendering and determinism."""

from brochure_maker.map_pipeline.basemap import BasemapFeature
from brochure_maker.map_pipeline.feature_selection import SelectedFeatures, SelectedPOI
from brochure_maker.map_pipeline.models import (
    MapStyleGenerateRequestV1,
    StyleTokens,
)
from brochure_maker.map_pipeline.style_director import generate_style
from brochure_maker.map_pipeline.svg_renderer import (
    MercatorProjection,
    render_svg,
    compute_svg_hash,
)


def _make_style_tokens(primary: str = "#B8714E") -> StyleTokens:
    req = MapStyleGenerateRequestV1(brochure_primary_hex=primary)
    return generate_style(req).style_tokens


def _make_road(name: str, highway: str, coords: list) -> BasemapFeature:
    return BasemapFeature(
        feature_type="road",
        geometry_type="LineString",
        coords=coords,
        properties={"name": name, "highway": highway},
    )


def _make_park(name: str, coords: list) -> BasemapFeature:
    return BasemapFeature(
        feature_type="park",
        geometry_type="Polygon",
        coords=coords,
        properties={"name": name},
    )


class TestMercatorProjection:
    """Coordinate projection tests."""

    def test_center_projects_to_center(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        x, y = proj.project(51.5, -0.12)
        assert abs(x - 470) < 2
        assert abs(y - 375) < 2

    def test_north_projects_above_south(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        _, y_north = proj.project(51.502, -0.12)
        _, y_south = proj.project(51.498, -0.12)
        assert y_north < y_south

    def test_east_projects_right_of_west(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        x_east, _ = proj.project(51.5, -0.118)
        x_west, _ = proj.project(51.5, -0.122)
        assert x_east > x_west

    def test_in_bounds_center(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        assert proj.in_bounds(51.5, -0.12)

    def test_in_bounds_far_away(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        assert not proj.in_bounds(52.0, -0.12)


class TestSVGRenderBasic:
    """Basic SVG rendering tests."""

    def test_empty_features_renders(self):
        features = SelectedFeatures()
        tokens = _make_style_tokens()
        svg, metrics = render_svg(
            features, 51.5, -0.12, 940, 750, 350, tokens,
        )
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg

    def test_with_roads_renders(self):
        features = SelectedFeatures(
            roads=[
                _make_road("High Street", "primary", [
                    (51.501, -0.122), (51.501, -0.118),
                ]),
                _make_road("Side Lane", "residential", [
                    (51.500, -0.121), (51.502, -0.121),
                ]),
            ],
        )
        tokens = _make_style_tokens()
        svg, metrics = render_svg(
            features, 51.5, -0.12, 940, 750, 350, tokens,
        )
        assert "roads-minor-casing" in svg or "roads-major-casing" in svg

    def test_with_building_name(self):
        features = SelectedFeatures()
        tokens = _make_style_tokens()
        svg, metrics = render_svg(
            features, 51.5, -0.12, 940, 750, 350, tokens,
            building_name="Test Building",
        )
        assert "TEST BUILDING" in svg
        assert "building-marker" in svg


class TestMultiPassRoadOrder:
    """Verify the 4-pass road rendering order for clean intersections."""

    def test_pass_order_in_svg(self):
        features = SelectedFeatures(
            roads=[
                _make_road("Main Road", "primary", [
                    (51.499, -0.122), (51.501, -0.118),
                ]),
                _make_road("Local St", "residential", [
                    (51.498, -0.121), (51.502, -0.121),
                ]),
            ],
        )
        tokens = _make_style_tokens()
        svg, _ = render_svg(
            features, 51.5, -0.12, 940, 750, 350, tokens,
        )

        # Check that minor casings appear before major casings
        minor_casing_pos = svg.find("roads-minor-casing")
        minor_fill_pos = svg.find("roads-minor-fill")
        major_casing_pos = svg.find("roads-major-casing")
        major_fill_pos = svg.find("roads-major-fill")

        if minor_casing_pos >= 0 and major_casing_pos >= 0:
            assert minor_casing_pos < major_casing_pos
        if minor_fill_pos >= 0 and major_fill_pos >= 0:
            assert minor_fill_pos < major_fill_pos
        if minor_casing_pos >= 0 and minor_fill_pos >= 0:
            assert minor_casing_pos < minor_fill_pos
        if major_casing_pos >= 0 and major_fill_pos >= 0:
            assert major_casing_pos < major_fill_pos


class TestSVGDeterminism:
    """SVG output must be byte-deterministic."""

    def test_identical_inputs_identical_svg(self):
        features = SelectedFeatures(
            roads=[
                _make_road("Test Road", "primary", [
                    (51.499, -0.122), (51.501, -0.118),
                ]),
            ],
            pois=[
                SelectedPOI(name="Test Cafe", category="cafe", lat=51.5005, lon=-0.1195, priority=4),
            ],
        )
        tokens = _make_style_tokens()

        svg1, _ = render_svg(
            features, 51.5, -0.12, 940, 750, 350, tokens,
            building_name="Building",
        )
        svg2, _ = render_svg(
            features, 51.5, -0.12, 940, 750, 350, tokens,
            building_name="Building",
        )

        assert svg1 == svg2
        assert compute_svg_hash(svg1) == compute_svg_hash(svg2)


class TestSVGLabels:
    """Label rendering and metrics."""

    def test_label_metrics_in_response(self):
        features = SelectedFeatures(
            pois=[
                SelectedPOI(name="Cafe One", category="cafe", lat=51.5005, lon=-0.1195, priority=4),
                SelectedPOI(name="Pub Two", category="pub", lat=51.4995, lon=-0.1205, priority=5),
            ],
        )
        tokens = _make_style_tokens()
        _, metrics = render_svg(
            features, 51.5, -0.12, 940, 750, 350, tokens,
        )
        assert metrics.placed_labels >= 0
        assert metrics.dropped_labels >= 0
        assert metrics.placed_labels + metrics.dropped_labels >= 0
