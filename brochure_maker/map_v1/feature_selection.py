"""Feature clipping and selection rules for map-v1."""

from __future__ import annotations

import math
from typing import Iterable

from .models import AreaFeature, PoiFeature, RoadFeature, StyleTokens
from .utils import haversine_m


def _rdp(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points

    start = points[0]
    end = points[-1]

    max_dist = -1.0
    index = 0
    for i in range(1, len(points) - 1):
        dist = _point_line_distance(points[i], start, end)
        if dist > max_dist:
            max_dist = dist
            index = i

    if max_dist > epsilon:
        left = _rdp(points[: index + 1], epsilon)
        right = _rdp(points[index:], epsilon)
        return left[:-1] + right

    return [start, end]


def _point_line_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    x0, y0 = point
    x1, y1 = start
    x2, y2 = end

    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x0 - x1, y0 - y1)

    t = ((x0 - x1) * dx + (y0 - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    px = x1 + t * dx
    py = y1 + t * dy
    return math.hypot(x0 - px, y0 - py)


def simplify_roads(roads: Iterable[RoadFeature], epsilon_deg: float = 0.00008) -> list[RoadFeature]:
    out: list[RoadFeature] = []
    for road in roads:
        if len(road.coords) < 2:
            continue
        simplified = _rdp(road.coords, epsilon_deg)
        if len(simplified) < 2:
            continue
        out.append(
            RoadFeature(
                id=road.id,
                road_class=road.road_class,
                name=road.name,
                coords=simplified,
            )
        )
    return out


def filter_roads_for_extent(
    roads: Iterable[RoadFeature],
    center_lat: float,
    center_lon: float,
    max_minor: int = 120,
) -> list[RoadFeature]:
    major_classes = {"motorway", "trunk", "primary", "secondary", "tertiary", "major"}
    major: list[RoadFeature] = []
    minor: list[tuple[float, RoadFeature]] = []

    for road in roads:
        kind = (road.road_class or "minor").lower()
        if kind in major_classes:
            major.append(road)
            continue

        if not road.coords:
            continue
        lat, lon = road.coords[0]
        dist = haversine_m(center_lat, center_lon, lat, lon)
        minor.append((dist, road))

    minor.sort(key=lambda item: (item[0], item[1].name.lower(), item[1].id))
    selected_minor = [item[1] for item in minor[:max_minor]]
    return major + selected_minor


def select_pois(
    pois: Iterable[PoiFeature],
    center_lat: float,
    center_lon: float,
    style_tokens: StyleTokens,
) -> list[PoiFeature]:
    rules = style_tokens.rules
    per_category = {k.lower(): int(v) for k, v in rules.max_pois_per_category.items()}
    total_cap = rules.max_pois_total

    scored: list[tuple[float, PoiFeature]] = []
    for poi in pois:
        dist = haversine_m(center_lat, center_lon, poi.lat, poi.lon)
        # Higher score is better: favor high-priority poi, then near distance.
        score = poi.priority * 1000.0 - dist
        scored.append((score, poi))

    scored.sort(key=lambda item: (-item[0], item[1].category, item[1].name.lower(), item[1].id))

    selected: list[PoiFeature] = []
    counts: dict[str, int] = {}

    for _, poi in scored:
        cat = poi.category.lower()
        current = counts.get(cat, 0)
        cap = per_category.get(cat, 4)
        if current >= cap:
            continue

        counts[cat] = current + 1
        selected.append(poi)
        if len(selected) >= total_cap:
            break

    return selected


def clip_area_features(areas: Iterable[AreaFeature]) -> list[AreaFeature]:
    # For now we rely on data-layer bbox filtering and keep geometry as-is.
    # This keeps deterministic behavior and avoids heavy polygon clipping deps.
    out: list[AreaFeature] = []
    for area in areas:
        if not area.rings:
            continue
        if not area.rings[0]:
            continue
        out.append(area)
    return out
