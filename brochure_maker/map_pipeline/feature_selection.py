"""Feature Selection — clips, simplifies, and applies POI quotas.

Takes raw basemap features + POI data and prepares them for rendering:
- Clips/simplifies roads/water/parks/buildings by extent.
- Applies POI quotas and priority sampling.
- Returns a deterministic, render-ready feature set.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Any

from .basemap import BasemapFeature
from .data_service import POIRecord, CATEGORY_PRIORITY

logger = logging.getLogger(__name__)

# Per-category caps
POI_CATEGORY_CAPS: dict[str, int] = {
    "cafe": 3,
    "restaurant": 4,
    "bar": 3,
    "pub": 2,
    "fitness_centre": 2,
    "sports_centre": 2,
    "hotel": 2,
    "museum": 2,
    "gallery": 2,
    "supermarket": 3,
    "convenience": 2,
    "pharmacy": 1,
    "cinema": 2,
    "bank": 2,
}


@dataclass
class SelectedPOI:
    """A POI ready for rendering with pixel coordinates."""

    name: str
    category: str
    lat: float
    lon: float
    priority: int = 99
    is_critical: bool = False


@dataclass
class SelectedFeatures:
    """All features ready for rendering."""

    roads: list[BasemapFeature] = field(default_factory=list)
    parks: list[BasemapFeature] = field(default_factory=list)
    water: list[BasemapFeature] = field(default_factory=list)
    buildings: list[BasemapFeature] = field(default_factory=list)
    pois: list[SelectedPOI] = field(default_factory=list)
    rail_stations: list[dict[str, Any]] = field(default_factory=list)
    neighbourhoods: list[dict[str, Any]] = field(default_factory=list)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two lat/lon points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _filter_roads(
    roads: list[BasemapFeature],
    center_lat: float,
    center_lon: float,
    max_residential: int = 80,
) -> list[BasemapFeature]:
    """Keep major roads, limit residential by distance and count."""
    ALWAYS_KEEP = {"trunk", "primary", "secondary", "tertiary"}
    filtered = []
    residential: list[tuple[float, BasemapFeature]] = []

    for road in roads:
        hw = road.properties.get("highway", "residential")
        if len(road.coords) < 2:
            continue

        if hw in ALWAYS_KEEP:
            filtered.append(road)
        elif hw in ("residential", "unclassified", "pedestrian", "living_street"):
            c0 = road.coords[0]
            dist = _haversine_m(center_lat, center_lon, c0[0], c0[1])
            if dist <= 300:
                residential.append((dist, road))

    # Sort residential by distance (closest first)
    residential.sort(key=lambda t: t[0])
    for _, road in residential[:max_residential]:
        filtered.append(road)

    return filtered


def _apply_poi_quotas(
    pois: list[POIRecord],
    center_lat: float,
    center_lon: float,
    max_total: int = 15,
) -> list[SelectedPOI]:
    """Apply per-category caps and distance-based priority.

    Returns POIs sorted by priority then distance for determinism.
    """
    # Sort by (category_priority, distance) for deterministic selection
    def _sort_key(p: POIRecord) -> tuple[int, float]:
        priority = CATEGORY_PRIORITY.get(p.category, 99)
        dist = _haversine_m(center_lat, center_lon, p.lat, p.lon)
        return (priority, dist)

    sorted_pois = sorted(pois, key=_sort_key)

    type_counts: dict[str, int] = {}
    selected: list[SelectedPOI] = []

    for poi in sorted_pois:
        cap = POI_CATEGORY_CAPS.get(poi.category, 3)
        count = type_counts.get(poi.category, 0)
        if count >= cap:
            continue
        type_counts[poi.category] = count + 1

        priority = CATEGORY_PRIORITY.get(poi.category, 99)
        selected.append(SelectedPOI(
            name=poi.name,
            category=poi.category,
            lat=poi.lat,
            lon=poi.lon,
            priority=priority,
            is_critical=(priority <= 2),
        ))

        if len(selected) >= max_total:
            break

    return selected


def select_features(
    roads: list[BasemapFeature],
    parks: list[BasemapFeature],
    water: list[BasemapFeature],
    buildings: list[BasemapFeature],
    pois: list[POIRecord],
    center_lat: float,
    center_lon: float,
    radius_m: int = 350,
    max_pois: int = 15,
    overpass_features: dict[str, Any] | None = None,
) -> SelectedFeatures:
    """Select and prepare features for rendering.

    Combines basemap features with POI data, applies quotas and filtering.
    """
    filtered_roads = _filter_roads(roads, center_lat, center_lon)
    selected_pois = _apply_poi_quotas(pois, center_lat, center_lon, max_pois)

    # Extract rail stations and neighbourhoods from overpass features
    rail_stations = []
    neighbourhoods = []
    if overpass_features:
        rail_stations = overpass_features.get("rail_stations", [])
        neighbourhoods = overpass_features.get("neighbourhoods", [])

    return SelectedFeatures(
        roads=filtered_roads,
        parks=parks,
        water=water,
        buildings=buildings,
        pois=selected_pois,
        rail_stations=rail_stations,
        neighbourhoods=neighbourhoods,
    )
