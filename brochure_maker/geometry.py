"""Shared geometry utilities for map rendering.

Single source of truth for projection, simplification, measurement,
and SVG path helpers used by both the legacy illustrated_map module
and the v1 map pipeline.
"""

from __future__ import annotations

import html as _html_module
import math


# ──────────────────────────────────────────────
#  Coordinate Projection
# ──────────────────────────────────────────────

class MercatorProjection:
    """Simple equirectangular lat/lon → pixel projection."""

    def __init__(
        self,
        center_lat: float,
        center_lon: float,
        width_px: int,
        height_px: int,
        radius_m: int = 350,
        padding_px: int = 40,
    ):
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.width_px = width_px
        self.height_px = height_px
        self.padding_px = padding_px

        usable_w = width_px - 2 * padding_px
        usable_h = height_px - 2 * padding_px

        lat_offset = radius_m / 111320.0
        lon_offset = radius_m / (111320.0 * math.cos(math.radians(center_lat)))

        self.south = center_lat - lat_offset
        self.north = center_lat + lat_offset
        self.west = center_lon - lon_offset
        self.east = center_lon + lon_offset

        self.x_scale = usable_w / (self.east - self.west) if self.east != self.west else 1
        self.y_scale = usable_h / (self.north - self.south) if self.north != self.south else 1

    def project(self, lat: float, lon: float) -> tuple[float, float]:
        x = self.padding_px + (lon - self.west) * self.x_scale
        y = self.padding_px + (self.north - lat) * self.y_scale
        return (round(x, 1), round(y, 1))

    def in_bounds(self, lat: float, lon: float, margin: float = 0.0005) -> bool:
        return (
            self.south - margin <= lat <= self.north + margin
            and self.west - margin <= lon <= self.east + margin
        )


# ──────────────────────────────────────────────
#  Polyline Simplification
# ──────────────────────────────────────────────

def rdp_simplify(
    points: list[tuple[float, float]], tolerance: float,
) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker polyline simplification in pixel space."""
    if len(points) <= 2:
        return points

    # Find the point farthest from the line between first and last
    start, end = points[0], points[-1]
    max_dist = 0.0
    max_idx = 0
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    line_len_sq = dx * dx + dy * dy

    for i in range(1, len(points) - 1):
        px, py = points[i]
        if line_len_sq == 0:
            d = math.sqrt((px - start[0]) ** 2 + (py - start[1]) ** 2)
        else:
            t = max(0, min(1, ((px - start[0]) * dx + (py - start[1]) * dy) / line_len_sq))
            proj_x = start[0] + t * dx
            proj_y = start[1] + t * dy
            d = math.sqrt((px - proj_x) ** 2 + (py - proj_y) ** 2)
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > tolerance:
        left = rdp_simplify(points[: max_idx + 1], tolerance)
        right = rdp_simplify(points[max_idx:], tolerance)
        return left[:-1] + right
    else:
        return [start, end]


# ──────────────────────────────────────────────
#  Coordinate Snapping
# ──────────────────────────────────────────────

def snap_to_grid(x: float, y: float, grid: float = 1.5) -> tuple[float, float]:
    """Snap coordinates to a grid for cleaner strokes."""
    return (round(x / grid) * grid, round(y / grid) * grid)


# ──────────────────────────────────────────────
#  Distance
# ──────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance in metres between two lat/lon points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ──────────────────────────────────────────────
#  Way Measurement (pre-projected pixel points)
# ──────────────────────────────────────────────

def way_pixel_length(pts: list[tuple[float, float]]) -> float:
    """Total pixel-length of a polyline from pre-projected points."""
    total = 0.0
    for i in range(len(pts) - 1):
        dx = pts[i + 1][0] - pts[i][0]
        dy = pts[i + 1][1] - pts[i][1]
        total += math.sqrt(dx * dx + dy * dy)
    return total


def way_pixel_length_projected(coords: list, proj: MercatorProjection) -> float:
    """Total pixel-length of a way, projecting coords on the fly."""
    pts = [proj.project(c[0], c[1]) for c in coords]
    return way_pixel_length(pts)


def way_midpoint(pts: list[tuple[float, float]]) -> tuple[float, float]:
    """Pixel midpoint from pre-projected points."""
    n = len(pts)
    mid = min(n // 2, n - 1)
    return pts[mid]


def way_midpoint_projected(coords: list, proj: MercatorProjection) -> tuple[float, float]:
    """Pixel midpoint of a way, projecting coords on the fly."""
    pts = [proj.project(c[0], c[1]) for c in coords]
    n = len(pts)
    mid = n // 2
    if mid >= n:
        mid = n - 1
    return pts[mid]


def way_angle(pts: list[tuple[float, float]]) -> float:
    """Rotation angle (degrees) at the midpoint of pre-projected points.

    Normalised to [-90, 90] so text is never upside-down.
    """
    n = len(pts)
    mid = min(n // 2, max(0, n - 2))
    x1, y1 = pts[mid]
    x2, y2 = pts[mid + 1] if mid + 1 < n else pts[mid]
    angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    if angle > 90:
        angle -= 180
    if angle < -90:
        angle += 180
    return round(angle, 1)


def way_angle_projected(coords: list, proj: MercatorProjection) -> float:
    """Rotation angle at the midpoint, projecting coords on the fly."""
    pts = [proj.project(c[0], c[1]) for c in coords]
    return way_angle(pts)


# ──────────────────────────────────────────────
#  SVG Path Helpers
# ──────────────────────────────────────────────

def points_to_path_d(pts: list[tuple[float, float]]) -> str:
    """Convert pre-projected pixel points to SVG path d attribute."""
    parts = []
    for i, (x, y) in enumerate(pts):
        cmd = "M" if i == 0 else "L"
        parts.append(f"{cmd}{x},{y}")
    return " ".join(parts)


def coords_to_path_d(coords: list, proj: MercatorProjection) -> str:
    """Convert a list of (lat, lon) coords to SVG path d attribute."""
    parts = []
    for i, (lat, lon) in enumerate(coords):
        x, y = proj.project(lat, lon)
        cmd = "M" if i == 0 else "L"
        parts.append(f"{cmd}{x},{y}")
    return " ".join(parts)


def coords_to_polygon_points(coords: list, proj: MercatorProjection) -> str:
    """Convert coords to SVG polygon points attribute."""
    pts = []
    for lat, lon in coords:
        x, y = proj.project(lat, lon)
        pts.append(f"{x},{y}")
    return " ".join(pts)


# ──────────────────────────────────────────────
#  Polygon Helpers
# ──────────────────────────────────────────────

def polygon_centroid(coords: list, proj: MercatorProjection) -> tuple[float, float]:
    """Compute centroid of a polygon in pixel space."""
    pts = [proj.project(c[0], c[1]) for c in coords]
    n = len(pts)
    if n == 0:
        return (0, 0)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    return (round(cx, 1), round(cy, 1))


def point_in_polygon(px: float, py: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test for pixel-space polygon."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ──────────────────────────────────────────────
#  Text Escaping
# ──────────────────────────────────────────────

def svg_escape(text: str) -> str:
    """Escape text for SVG XML."""
    return _html_module.escape(text, quote=True)
