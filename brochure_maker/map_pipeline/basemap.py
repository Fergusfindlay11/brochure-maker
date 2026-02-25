"""PMTiles Basemap Extraction.

Defines deterministic basemap feature extraction from PMTiles archives:
- Zoom selection based on extent size and output size.
- Clip to extent, linemerge per road class, snap/round coordinates.
- Falls back to Overpass API when PMTiles are not available.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import PMTilesReadError

logger = logging.getLogger(__name__)


@dataclass
class BasemapFeature:
    """A single basemap feature (road, park, water, building)."""

    feature_type: str  # "road", "park", "water", "building"
    geometry_type: str  # "LineString", "Polygon"
    coords: list[tuple[float, float]]  # [(lat, lon), ...]
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class BasemapExtraction:
    """Result of basemap feature extraction for a given extent."""

    roads: list[BasemapFeature] = field(default_factory=list)
    parks: list[BasemapFeature] = field(default_factory=list)
    water: list[BasemapFeature] = field(default_factory=list)
    buildings: list[BasemapFeature] = field(default_factory=list)
    zoom_level: int = 15
    tile_count: int = 0
    source: str = "overpass"  # "pmtiles" or "overpass"


def select_zoom(
    radius_m: float,
    width_px: int,
    height_px: int,
    min_zoom: int = 13,
    max_zoom: int = 17,
) -> int:
    """Select tile zoom level based on extent size and output pixel size.

    Formula: z = clamp(log2(output_px * 156543 / (extent_m * 2)), min, max)
    where 156543 m/px is the equatorial resolution at z=0.
    """
    extent_m = radius_m * 2
    output_px = max(width_px, height_px)

    if extent_m <= 0 or output_px <= 0:
        return min_zoom

    # metres per pixel at zoom z: 156543 * cos(lat) / 2^z
    # We want: mpp_target = extent_m / output_px
    # z = log2(156543 / mpp_target) ≈ log2(156543 * output_px / extent_m)
    mpp_target = extent_m / output_px
    if mpp_target <= 0:
        return max_zoom

    z = math.log2(156543.0 / mpp_target)
    return max(min_zoom, min(max_zoom, round(z)))


def _snap_coord(val: float, tolerance: float = 0.000001) -> float:
    """Snap coordinate to a fixed tolerance for determinism."""
    return round(val / tolerance) * tolerance


def _simplify_coords(
    coords: list[tuple[float, float]],
    tolerance: float = 0.00001,
) -> list[tuple[float, float]]:
    """Douglas-Peucker simplification on lat/lon coordinates."""
    if len(coords) <= 2:
        return coords

    # Find point farthest from line between first and last
    start, end = coords[0], coords[-1]
    max_dist = 0.0
    max_idx = 0
    dx = end[1] - start[1]
    dy = end[0] - start[0]
    line_len_sq = dx * dx + dy * dy

    for i in range(1, len(coords) - 1):
        lat, lon = coords[i]
        if line_len_sq == 0:
            d = math.sqrt((lat - start[0]) ** 2 + (lon - start[1]) ** 2)
        else:
            t = max(0, min(1, ((lat - start[0]) * dy + (lon - start[1]) * dx) / line_len_sq))
            proj_lat = start[0] + t * dy
            proj_lon = start[1] + t * dx
            d = math.sqrt((lat - proj_lat) ** 2 + (lon - proj_lon) ** 2)
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > tolerance:
        left = _simplify_coords(coords[:max_idx + 1], tolerance)
        right = _simplify_coords(coords[max_idx:], tolerance)
        return left[:-1] + right
    return [start, end]


def _clip_coords_to_bbox(
    coords: list[tuple[float, float]],
    south: float,
    west: float,
    north: float,
    east: float,
) -> list[tuple[float, float]]:
    """Clip coordinate list to bounding box (simple inclusion filter)."""
    margin = 0.0005  # small margin to avoid edge artifacts
    return [
        (lat, lon) for lat, lon in coords
        if south - margin <= lat <= north + margin
        and west - margin <= lon <= east + margin
    ]


def _linemerge_roads(
    roads: list[BasemapFeature],
) -> list[BasemapFeature]:
    """Merge road segments of the same class that share endpoints.

    Groups by road class + bridge/tunnel flags, then merges contiguous segments.
    """
    from collections import defaultdict

    groups: dict[str, list[BasemapFeature]] = defaultdict(list)
    for road in roads:
        key = (
            road.properties.get("highway", ""),
            road.properties.get("bridge", ""),
            road.properties.get("tunnel", ""),
        )
        groups[str(key)].append(road)

    merged = []
    for key, group in groups.items():
        # Simple merge: concatenate segments that share endpoints
        segments = [list(r.coords) for r in group if len(r.coords) >= 2]
        if not segments:
            continue

        # Greedy chaining
        chains: list[list[tuple[float, float]]] = [segments[0]]
        used = {0}

        changed = True
        while changed:
            changed = False
            for i, seg in enumerate(segments):
                if i in used:
                    continue
                for chain in chains:
                    if chain[-1] == seg[0]:
                        chain.extend(seg[1:])
                        used.add(i)
                        changed = True
                        break
                    elif chain[0] == seg[-1]:
                        chain[:0] = seg[:-1]
                        used.add(i)
                        changed = True
                        break

        # Add remaining unmerged segments as separate chains
        for i, seg in enumerate(segments):
            if i not in used:
                chains.append(seg)

        props = group[0].properties if group else {}
        for chain in chains:
            merged.append(BasemapFeature(
                feature_type="road",
                geometry_type="LineString",
                coords=chain,
                properties=dict(props),
            ))

    return merged


# ──────────────────────────────────────────────
#  Overpass Fallback (when PMTiles not available)
# ──────────────────────────────────────────────

async def extract_from_overpass(
    center_lat: float,
    center_lon: float,
    radius_m: int = 350,
) -> BasemapExtraction:
    """Extract basemap features from Overpass API (fallback path).

    Re-uses the existing illustrated_map Overpass fetcher but normalises
    the output to BasemapFeature format.
    """
    from brochure_maker.illustrated_map import (
        fetch_overpass_data,
        _parse_overpass_elements,
    )

    raw = await fetch_overpass_data(center_lat, center_lon, radius_m)
    features = _parse_overpass_elements(raw)

    roads = []
    for st in features.get("streets", []):
        roads.append(BasemapFeature(
            feature_type="road",
            geometry_type="LineString",
            coords=st["coords"],
            properties={
                "name": st.get("name", ""),
                "highway": st.get("highway", "residential"),
            },
        ))

    parks = []
    for p in features.get("parks", []):
        parks.append(BasemapFeature(
            feature_type="park",
            geometry_type="Polygon",
            coords=p["coords"],
            properties={"name": p.get("name", "")},
        ))

    water = []
    for w in features.get("waterways", []):
        water.append(BasemapFeature(
            feature_type="water",
            geometry_type="LineString",
            coords=w["coords"],
            properties={
                "name": w.get("name", ""),
                "waterway": w.get("waterway", ""),
            },
        ))

    buildings = []
    for b in features.get("buildings", []):
        buildings.append(BasemapFeature(
            feature_type="building",
            geometry_type="Polygon",
            coords=b["coords"],
            properties={},
        ))

    # Apply post-processing: clip, merge, snap
    lat_offset = radius_m / 111320.0
    lon_offset = radius_m / (111320.0 * math.cos(math.radians(center_lat)))
    south = center_lat - lat_offset
    north = center_lat + lat_offset
    west = center_lon - lon_offset
    east = center_lon + lon_offset

    # Snap coordinates
    for feat_list in (roads, parks, water, buildings):
        for feat in feat_list:
            feat.coords = [
                (_snap_coord(lat), _snap_coord(lon))
                for lat, lon in feat.coords
            ]

    # Linemerge roads
    roads = _linemerge_roads(roads)

    # Simplify
    for feat in roads:
        feat.coords = _simplify_coords(feat.coords)
    for feat in water:
        feat.coords = _simplify_coords(feat.coords)

    return BasemapExtraction(
        roads=roads,
        parks=parks,
        water=water,
        buildings=buildings,
        zoom_level=select_zoom(radius_m, 940, 750),
        source="overpass",
    )


async def extract_basemap(
    center_lat: float,
    center_lon: float,
    radius_m: int = 350,
    width_px: int = 940,
    height_px: int = 750,
) -> BasemapExtraction:
    """Extract basemap features for the given extent.

    Uses PMTiles if available, otherwise falls back to Overpass API.
    """
    # For v1, use Overpass fallback. PMTiles support is a future enhancement
    # when a local PMTiles archive is configured.
    return await extract_from_overpass(center_lat, center_lon, radius_m)
