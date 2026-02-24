"""Tests for the shared geometry module."""

from brochure_maker.geometry import (
    MercatorProjection,
    rdp_simplify,
    snap_to_grid,
    haversine_m,
    way_pixel_length,
    way_pixel_length_projected,
    way_midpoint,
    way_midpoint_projected,
    way_angle,
    way_angle_projected,
    points_to_path_d,
    coords_to_path_d,
    coords_to_polygon_points,
    polygon_centroid,
    point_in_polygon,
    svg_escape,
)


class TestMercatorProjection:
    def test_center_projects_to_center(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        x, y = proj.project(51.5, -0.12)
        assert abs(x - 470) < 2
        assert abs(y - 375) < 2

    def test_north_above_south(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        _, y_n = proj.project(51.502, -0.12)
        _, y_s = proj.project(51.498, -0.12)
        assert y_n < y_s

    def test_east_right_of_west(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        x_e, _ = proj.project(51.5, -0.118)
        x_w, _ = proj.project(51.5, -0.122)
        assert x_e > x_w

    def test_in_bounds(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        assert proj.in_bounds(51.5, -0.12)
        assert not proj.in_bounds(52.0, -0.12)


class TestRdpSimplify:
    def test_two_points_unchanged(self):
        pts = [(0, 0), (10, 10)]
        assert rdp_simplify(pts, 1.0) == pts

    def test_collinear_simplified(self):
        pts = [(0, 0), (5, 5), (10, 10)]
        result = rdp_simplify(pts, 1.0)
        assert len(result) == 2

    def test_bent_line_kept(self):
        pts = [(0, 0), (5, 10), (10, 0)]
        result = rdp_simplify(pts, 0.1)
        assert len(result) == 3


class TestSnapToGrid:
    def test_snap(self):
        x, y = snap_to_grid(1.0, 2.3, 1.5)
        assert x == round(1.0 / 1.5) * 1.5
        assert y == round(2.3 / 1.5) * 1.5

    def test_snap_deterministic(self):
        a = snap_to_grid(3.7, 8.1)
        b = snap_to_grid(3.7, 8.1)
        assert a == b


class TestHaversine:
    def test_same_point_zero(self):
        assert haversine_m(51.5, -0.12, 51.5, -0.12) == 0.0

    def test_known_distance(self):
        # ~111 km per degree of latitude
        d = haversine_m(51.0, 0.0, 52.0, 0.0)
        assert 110_000 < d < 112_000


class TestWayFunctions:
    def test_pixel_length(self):
        pts = [(0, 0), (3, 4)]
        assert abs(way_pixel_length(pts) - 5.0) < 0.01

    def test_pixel_length_projected(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        coords = [(51.5, -0.12), (51.501, -0.118)]
        length = way_pixel_length_projected(coords, proj)
        assert length > 0

    def test_midpoint(self):
        pts = [(0, 0), (5, 5), (10, 10)]
        assert way_midpoint(pts) == (5, 5)

    def test_midpoint_projected(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        coords = [(51.499, -0.122), (51.5, -0.12), (51.501, -0.118)]
        mx, my = way_midpoint_projected(coords, proj)
        assert abs(mx - 470) < 2
        assert abs(my - 375) < 2

    def test_angle_horizontal(self):
        pts = [(0, 0), (10, 0)]
        assert way_angle(pts) == 0.0

    def test_angle_vertical(self):
        pts = [(0, 0), (0, 10)]
        assert way_angle(pts) == 90.0

    def test_angle_projected(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        coords = [(51.5, -0.122), (51.5, -0.118)]
        angle = way_angle_projected(coords, proj)
        assert abs(angle) < 1  # roughly horizontal


class TestSVGPathHelpers:
    def test_points_to_path_d(self):
        pts = [(10, 20), (30, 40)]
        d = points_to_path_d(pts)
        assert d == "M10,20 L30,40"

    def test_coords_to_path_d(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        coords = [(51.5, -0.12)]
        d = coords_to_path_d(coords, proj)
        assert d.startswith("M")

    def test_coords_to_polygon_points(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        coords = [(51.5, -0.12), (51.501, -0.118), (51.499, -0.118)]
        result = coords_to_polygon_points(coords, proj)
        assert len(result.split(" ")) == 3


class TestPolygonHelpers:
    def test_centroid(self):
        proj = MercatorProjection(51.5, -0.12, 940, 750, 350)
        coords = [(51.499, -0.122), (51.501, -0.122), (51.501, -0.118), (51.499, -0.118)]
        cx, cy = polygon_centroid(coords, proj)
        # Should be near center
        assert abs(cx - 470) < 5
        assert abs(cy - 375) < 5

    def test_point_in_polygon_inside(self):
        polygon = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert point_in_polygon(5, 5, polygon)

    def test_point_in_polygon_outside(self):
        polygon = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert not point_in_polygon(15, 15, polygon)


class TestSvgEscape:
    def test_ampersand(self):
        assert svg_escape("A & B") == "A &amp; B"

    def test_quotes(self):
        assert svg_escape('"hello"') == "&quot;hello&quot;"

    def test_angle_brackets(self):
        assert svg_escape("<script>") == "&lt;script&gt;"
