"""Generate illustrated neighbourhood maps using OSM Overpass data + SVG rendering."""

import asyncio
import hashlib
import json
import math
import html as html_module
import logging
from pathlib import Path
from typing import Optional

import httpx

from brochure_maker.geometry import (
    MercatorProjection,
    rdp_simplify as _rdp_simplify,
    snap_to_grid as _snap_to_grid,
    haversine_m as _haversine_m,
    way_pixel_length as _way_pixel_length_fast,
    way_pixel_length_projected as _way_pixel_length,
    way_midpoint as _way_midpoint_fast,
    way_midpoint_projected as _way_midpoint,
    way_angle as _way_angle_fast,
    way_angle_projected as _way_angle_at_midpoint,
    points_to_path_d as _points_to_path_d,
    coords_to_path_d as _coords_to_path_d,
    coords_to_polygon_points as _coords_to_polygon_points,
    polygon_centroid as _polygon_centroid,
    point_in_polygon as _point_in_polygon,
    svg_escape as _esc,
)

logger = logging.getLogger(__name__)

# Simple file-based cache for Overpass responses
_CACHE_DIR = Path(__file__).resolve().parent.parent / "overpass_cache"

OVERPASS_URLS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
USER_AGENT = "BrochureMakerApp/1.0"


# ──────────────────────────────────────────────
#  Overpass API Data Fetching
# ──────────────────────────────────────────────

class OverpassError(Exception):
    """Raised when the Overpass API is unreachable or returns an error."""


async def fetch_overpass_data(
    lat: float,
    lon: float,
    radius_m: int = 350,
    timeout: int = 25,
) -> dict:
    """Fetch streets, parks, waterways, POIs from Overpass API.

    Tries multiple Overpass mirrors for reliability.
    Caches results to disk so repeated requests for the same area are instant.
    """
    # Check cache first
    CACHE_VERSION = 4  # Bump when query/schema changes
    cache_key = f"v{CACHE_VERSION}_{lat:.5f}_{lon:.5f}_{radius_m}"
    cache_hash = hashlib.md5(cache_key.encode()).hexdigest()[:12]
    _CACHE_DIR.mkdir(exist_ok=True)
    cache_file = _CACHE_DIR / f"{cache_hash}.json"
    if cache_file.exists():
        logger.info("Using cached Overpass data (%s)", cache_file.name)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    lat_offset = radius_m / 111320.0
    lon_offset = radius_m / (111320.0 * math.cos(math.radians(lat)))
    south = round(lat - lat_offset, 6)
    north = round(lat + lat_offset, 6)
    west = round(lon - lon_offset, 6)
    east = round(lon + lon_offset, 6)
    bbox = f"{south},{west},{north},{east}"

    query = f"""[out:json][timeout:{timeout}];(
way["highway"~"primary|secondary|tertiary|residential"]({bbox});
way["building"]({bbox});
way["leisure"="park"]({bbox});
way["landuse"~"grass|meadow"]({bbox});
way["waterway"]({bbox});
node["amenity"~"restaurant|cafe|bar|pub|pharmacy|cinema|bank"]["name"]({bbox});
node["shop"~"supermarket|convenience"]["name"]({bbox});
node["leisure"~"fitness_centre|sports_centre"]["name"]({bbox});
node["tourism"~"hotel|museum|gallery"]["name"]({bbox});
node["railway"~"station|halt"]({bbox});
node["public_transport"="station"]({bbox});
node["place"~"neighbourhood|suburb"]({bbox});
);out geom qt;"""

    last_error = None
    async with httpx.AsyncClient(timeout=timeout + 20) as client:
        for url in OVERPASS_URLS:
            try:
                logger.info("Trying Overpass mirror: %s", url)
                resp = await client.post(
                    url,
                    data={"data": query},
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                data = resp.json()
                # Cache for future requests
                try:
                    cache_file.write_text(json.dumps(data), encoding="utf-8")
                    logger.info("Cached Overpass data to %s", cache_file.name)
                except Exception:
                    pass  # Caching is best-effort
                return data
            except httpx.TimeoutException:
                last_error = OverpassError(f"Overpass timed out ({url})")
                logger.warning("Overpass timed out at %s, trying next", url)
            except httpx.HTTPStatusError as e:
                last_error = OverpassError(
                    f"Overpass API error: {e.response.status_code} ({url})"
                )
                logger.warning("Overpass returned %s at %s, trying next", e.response.status_code, url)
            except Exception as e:
                last_error = OverpassError(f"Overpass fetch failed: {e} ({url})")
                logger.warning("Overpass failed at %s: %s", url, e)

    raise last_error or OverpassError("All Overpass mirrors failed")


def _parse_overpass_elements(data: dict) -> dict:
    """Parse raw Overpass JSON into structured feature lists.

    Uses `out geom` format where ways have inline geometry arrays
    instead of requiring separate node lookups.
    """
    elements = data.get("elements", [])

    streets = []
    buildings = []
    parks = []
    waterways = []
    pois = []
    rail_stations = []
    neighbourhoods = []

    for el in elements:
        tags = el.get("tags", {})

        if el["type"] == "node":
            # Match POIs from amenity, shop, leisure, tourism tags
            poi_type = None
            for tag_key in ("amenity", "shop", "leisure", "tourism"):
                val = tags.get(tag_key)
                if val in (
                    "restaurant", "cafe", "bar", "pub", "pharmacy", "cinema", "bank",
                    "supermarket", "convenience",
                    "fitness_centre", "sports_centre",
                    "hotel", "museum", "gallery",
                ):
                    poi_type = val
                    break
            if poi_type:
                pois.append({
                    "name": tags.get("name", ""),
                    "type": poi_type,
                    "lat": el["lat"], "lon": el["lon"],
                })
            elif (
                tags.get("railway") in ("station", "halt")
                or tags.get("public_transport") == "station"
                or tags.get("station")
            ):
                rail_stations.append({
                    "name": tags.get("name", ""),
                    "lat": el["lat"], "lon": el["lon"],
                })
            elif tags.get("place") in ("neighbourhood", "suburb"):
                name = tags.get("name", "")
                if name:
                    neighbourhoods.append({
                        "name": name,
                        "lat": el["lat"], "lon": el["lon"],
                    })

        elif el["type"] == "way":
            # With `out geom`, geometry is inline on the way element
            geom = el.get("geometry", [])
            coords = [(pt["lat"], pt["lon"]) for pt in geom if "lat" in pt]
            if len(coords) < 2:
                continue

            if tags.get("highway"):
                streets.append({
                    "name": tags.get("name", ""),
                    "highway": tags["highway"],
                    "coords": coords,
                })
            elif tags.get("building"):
                buildings.append({
                    "coords": coords,
                })
            elif tags.get("leisure") == "park" or tags.get("landuse") in ("grass", "meadow"):
                parks.append({
                    "name": tags.get("name", ""),
                    "coords": coords,
                })
            elif tags.get("waterway"):
                waterways.append({
                    "name": tags.get("name", ""),
                    "waterway": tags["waterway"],
                    "coords": coords,
                })

    # Filter out nameless POIs
    pois = [p for p in pois if p.get("name")]

    return {
        "streets": streets,
        "buildings": buildings,
        "parks": parks,
        "waterways": waterways,
        "pois": pois,
        "rail_stations": rail_stations,
        "neighbourhoods": neighbourhoods,
    }


# MercatorProjection is imported from brochure_maker.geometry


# ──────────────────────────────────────────────
#  Colour Helpers
# ──────────────────────────────────────────────

def _hex_to_rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _luminance(hex_colour: str) -> float:
    r, g, b = _hex_to_rgb(hex_colour)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _choose_street_colour(primary: str) -> tuple[str, str]:
    """Return (stroke_colour, label_colour) based on background luminance."""
    if _luminance(primary) > 0.5:
        return ("rgba(0,0,0,0.20)", "rgba(0,0,0,0.40)")
    else:
        return ("rgba(255,255,255,0.30)", "rgba(255,255,255,0.50)")


def _darken_hex(hex_colour: str, factor: float = 0.75) -> str:
    r, g, b = _hex_to_rgb(hex_colour)
    r, g, b = int(r * factor), int(g * factor), int(b * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _lighten_hex(hex_colour: str, factor: float = 0.3) -> str:
    r, g, b = _hex_to_rgb(hex_colour)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _tint_hex(base_hex: str, accent_hex: str, ratio: float = 0.4) -> str:
    """Blend base colour toward accent colour by ratio (0.0 = pure base, 1.0 = pure accent)."""
    br, bg, bb = _hex_to_rgb(base_hex)
    ar, ag, ab = _hex_to_rgb(accent_hex)
    r = max(0, min(255, int(br + (ar - br) * ratio)))
    g = max(0, min(255, int(bg + (ag - bg) * ratio)))
    b = max(0, min(255, int(bb + (ab - bb) * ratio)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _complement_hex(hex_colour: str) -> str:
    """Return the hue-complement of a colour (rotate hue 180 degrees)."""
    import colorsys
    r, g, b = _hex_to_rgb(hex_colour)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h = (h + 0.5) % 1.0
    s = max(0.3, min(0.7, s))  # moderate saturation for subtlety
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"


def _rgba(hex_colour: str, opacity: float) -> str:
    """Convert #RRGGBB + opacity to rgba() string."""
    r, g, b = _hex_to_rgb(hex_colour)
    return f"rgba({r},{g},{b},{opacity:.2f})"


# ──────────────────────────────────────────────
#  Map Palette — derived from brochure background
# ──────────────────────────────────────────────

from typing import NamedTuple

class MapPalette(NamedTuple):
    """All derived colours for the illustrated map SVG."""
    base_hex: str
    # Roads
    road_stroke: str
    road_label: str
    # City blocks
    block_fill: str
    block_opacity_min: float
    block_opacity_max: float
    # Parks
    park_fill: str
    park_opacity: float
    park_label: str
    # Waterways
    water_stroke: str
    water_opacity: float
    water_label: str
    # Text (contrast-aware)
    text_primary: str
    text_secondary: str
    text_stroke: str
    # Streets
    street_stroke: str
    street_label: str
    # POIs
    poi_dot: str
    poi_label: str
    # Building marker
    building_primary: str
    building_dark: str
    building_label: str
    # Mode
    is_light_base: bool


ACCENT_GREEN = "#2d7a3a"


def derive_map_palette(
    base_hex: str,
    primary_hex: str = "",
) -> MapPalette:
    """Derive a full SVG colour palette from the brochure background colour.

    Args:
        base_hex: The brochure slide background colour (#RRGGBB).
        primary_hex: The original brand primary colour (for building marker).
    """
    # Fallback for invalid input
    try:
        _hex_to_rgb(base_hex)
    except (ValueError, IndexError):
        base_hex = "#B8714E"

    if not primary_hex:
        primary_hex = base_hex

    lum = _luminance(base_hex)
    is_light = lum > 0.55

    # Roads — darker underlay for clear cased stroke definition
    road_stroke = _darken_hex(base_hex, factor=0.70)
    road_label = _darken_hex(base_hex, factor=0.90)

    # Parks
    park_fill = _tint_hex(base_hex, ACCENT_GREEN, 0.40)
    park_opacity = 0.45 if is_light else 0.55

    # Waterways
    water_stroke = _complement_hex(base_hex)
    water_opacity = 0.70

    # City blocks / building footprints — subtle, slightly darker than background
    block_fill = _darken_hex(base_hex, factor=0.92)

    if is_light:
        # Dark text for light backgrounds
        text_primary = _darken_hex(base_hex, factor=0.15)
        text_secondary = _darken_hex(base_hex, factor=0.35)
        # Halo uses the base colour itself — blends with background perfectly
        r, g, b = _hex_to_rgb(base_hex)
        text_stroke = f"rgba({r},{g},{b},0.85)"
        # Road overlay is lighter (closer to background) for soft illustrated look
        street_stroke = _lighten_hex(base_hex, factor=0.18)
        street_label = _darken_hex(base_hex, factor=0.30)
        poi_dot = _darken_hex(base_hex, factor=0.25)
        poi_label = _darken_hex(base_hex, factor=0.12)
        park_label = _darken_hex(park_fill, factor=0.25)
        water_label = _darken_hex(water_stroke, factor=0.40)
    else:
        # Light text for dark backgrounds
        text_primary = _lighten_hex(base_hex, factor=0.85)
        text_secondary = _lighten_hex(base_hex, factor=0.55)
        r, g, b = _hex_to_rgb(base_hex)
        text_stroke = f"rgba({r},{g},{b},0.85)"
        street_stroke = _lighten_hex(base_hex, factor=0.25)
        street_label = _lighten_hex(base_hex, factor=0.55)
        poi_dot = _lighten_hex(base_hex, factor=0.65)
        poi_label = _lighten_hex(base_hex, factor=0.80)
        park_label = _lighten_hex(park_fill, factor=0.65)
        water_label = _lighten_hex(water_stroke, factor=0.50)

    # Building marker uses original brand colour
    building_primary = primary_hex
    building_dark = _darken_hex(primary_hex, factor=0.80)
    building_label = text_primary

    return MapPalette(
        base_hex=base_hex,
        road_stroke=road_stroke,
        road_label=road_label,
        block_fill=block_fill,
        block_opacity_min=0.10,
        block_opacity_max=0.20,
        park_fill=park_fill,
        park_opacity=park_opacity,
        park_label=park_label,
        water_stroke=water_stroke,
        water_opacity=water_opacity,
        water_label=water_label,
        text_primary=text_primary,
        text_secondary=text_secondary,
        text_stroke=text_stroke,
        street_stroke=street_stroke,
        street_label=street_label,
        poi_dot=poi_dot,
        poi_label=poi_label,
        building_primary=building_primary,
        building_dark=building_dark,
        building_label=building_label,
        is_light_base=is_light,
    )


# ──────────────────────────────────────────────
#  Geometry helpers are imported from brochure_maker.geometry
#  (_rdp_simplify, _snap_to_grid, _haversine_m, etc.)
# ──────────────────────────────────────────────


def _filter_roads(
    streets: list,
    proj: MercatorProjection,
    center_lat: float,
    center_lon: float,
    max_residential: int = 80,
) -> list:
    """Filter roads: keep major roads, limit residential by distance and count.

    Returns a filtered list of street dicts, also dropping tiny fragments
    (< 30px rendered length).
    """
    ALWAYS_KEEP = {"trunk", "primary", "secondary", "tertiary"}
    filtered = []
    residential_ways: list[tuple[float, dict]] = []

    for st in streets:
        hw = st.get("highway", "residential")
        # Compute pixel length for fragment filtering
        px_len = _way_pixel_length_fast(st["_px_points"])
        if px_len < 60:
            continue  # drop short stubs

        if hw in ALWAYS_KEEP:
            st["_px_len"] = px_len
            filtered.append(st)
        elif hw in ("residential", "unclassified", "pedestrian", "living_street"):
            # Distance from center (using first coord of way)
            c0 = st["coords"][0]
            dist = _haversine_m(center_lat, center_lon, c0[0], c0[1])
            if dist <= 250:
                st["_px_len"] = px_len
                residential_ways.append((dist, st))

    # Cap residential roads
    # Score = (px_length * 0.8) - (distance * 0.2); higher is better
    residential_ways.sort(key=lambda t: -(t[1].get("_px_len", 0) * 0.8 - t[0] * 0.2))
    for _, st in residential_ways[:max_residential]:
        filtered.append(st)

    return filtered


# ──────────────────────────────────────────────
#  SVG rendering helpers are imported from brochure_maker.geometry
#  (_way_pixel_length, _way_midpoint, _way_angle, _points_to_path_d,
#   _coords_to_path_d, _coords_to_polygon_points, _polygon_centroid, _esc)
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
#  Inline SVG POI Icons (consistent across platforms)
# ──────────────────────────────────────────────

# Each icon is an SVG path at 12x12 viewBox, rendered at POI location.
# Keys map to OSM amenity/shop/leisure/tourism values.
POI_ICON_PATHS: dict[str, str] = {
    "cafe":           "M2 3h7v5a3 3 0 01-3 3H5a3 3 0 01-3-3V3zm7 1h2a1.5 1.5 0 010 3H9",
    "restaurant":     "M3 1v4a2 2 0 002 2v4M9 1v10M9 1a2 2 0 012 2v1a2 2 0 01-2 2",
    "bar":            "M3 2l3 4 3-4M6 6v5M4 11h4",
    "pub":            "M3 2h6v4a3 3 0 01-6 0V2zM6 6v4M4 10h4",
    "supermarket":    "M1 1h2l1 7h6l1-5H4M5 10a1 1 0 100 2 1 1 0 000-2M9 10a1 1 0 100 2 1 1 0 000-2",
    "convenience":    "M2 4h8v6H2zM4 4V2h4v2M6 7v1",
    "fitness_centre": "M1 6h2v-2h1v4h-1v-2M10 6h-2v-2h-1v4h1v-2M4 5h4v2H4z",
    "sports_centre":  "M1 6h2v-2h1v4h-1v-2M10 6h-2v-2h-1v4h1v-2M4 5h4v2H4z",
    "hotel":          "M2 9V5a1 1 0 011-1h1v3h4V4h1a1 1 0 011 1v4M1 9h10M5 7a1.5 1.5 0 100-3 1.5 1.5 0 000 3",
    "museum":         "M6 1L1 4h10L6 1zM2 5v5M6 5v5M10 5v5M1 10h10",
    "gallery":        "M2 2h8v7H2zM4 6l2-2 2 2M5 5a1 1 0 100-2 1 1 0 000 2",
    "pharmacy":       "M4 6h4M6 4v4",
    "cinema":         "M2 3h8v6H2zM4 1v2M8 1v2",
    "bank":           "M6 1L1 4h10L6 1zM3 5v4M6 5v4M9 5v4M1 9h10M1 10h10",
}

# Per-category caps (from user spec)
POI_CATEGORY_CAPS: dict[str, int] = {
    "cafe": 3, "restaurant": 4, "bar": 3, "pub": 2,
    "fitness_centre": 2, "sports_centre": 2,
    "hotel": 2, "museum": 2, "gallery": 2,
    "supermarket": 3, "convenience": 2,
    "pharmacy": 1, "cinema": 2, "bank": 2,
}


def _render_poi_icon(x: float, y: float, poi_type: str, palette: MapPalette) -> str:
    """Render a small inline SVG icon for a POI at (x, y)."""
    path_d = POI_ICON_PATHS.get(poi_type, "M4 6h4M6 4v4")  # fallback: plus sign
    fill = "none"
    stroke = palette.poi_dot
    return (
        f'<g transform="translate({x - 7},{y - 7})">'
        f'<path d="{path_d}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" '
        f'opacity="0.95" transform="scale(1.15)"/></g>'
    )


# ──────────────────────────────────────────────
#  Collision Detection
# ──────────────────────────────────────────────

def _rect_overlaps(new_rect: tuple, occupied: list) -> bool:
    """Check if new_rect overlaps any rect in occupied (no append)."""
    for rect in occupied:
        if not (new_rect[2] < rect[0] or new_rect[0] > rect[2] or
                new_rect[3] < rect[1] or new_rect[1] > rect[3]):
            return True
    return False


def _label_overlaps(
    x: float, y: float, text: str, occupied: list, font_size: float = 8.0,
) -> bool:
    """Check if a label bounding box overlaps any existing rect.

    Appends to `occupied` if no overlap.
    """
    width = len(text) * font_size * 0.55
    h = font_size * 1.2
    new_rect = (x, y - h, x + width, y)
    if _rect_overlaps(new_rect, occupied):
        return True
    occupied.append(new_rect)
    return False


def _try_poi_placement(
    x: float, y: float, text: str, occupied: list,
    font_size: float = 8.5, icon_gap: float = 16.0,
) -> tuple[float, float] | None:
    """Try multiple offset positions for a POI label.

    Returns (label_x, label_y) or None if all placements collide.
    """
    width = len(text) * font_size * 0.55 + icon_gap
    h = font_size * 1.2
    # Offsets: E, NE, SE, W, NW, SW
    offsets = [
        (icon_gap, 0),
        (icon_gap, -h - 2),
        (icon_gap, h + 2),
        (-width - 4, 0),
        (-width - 4, -h - 2),
        (-width - 4, h + 2),
    ]
    for dx, dy in offsets:
        lx, ly = x + dx, y + dy
        new_rect = (lx, ly - h, lx + width, ly)
        if not _rect_overlaps(new_rect, occupied):
            occupied.append(new_rect)
            return (lx, ly)
    return None


# ──────────────────────────────────────────────
#  SVG Layer Renderers
# ──────────────────────────────────────────────

def _render_buildings(
    buildings: list, proj: MercatorProjection, palette: MapPalette,
) -> str:
    """Render building footprints as subtle filled polygons.

    Creates the 'city fabric' texture that professional illustrated maps have.
    Buildings are drawn as a single group with low opacity, giving depth
    without competing with roads/POIs.
    """
    svg = []
    fill = palette.block_fill
    op_min = palette.block_opacity_min
    op_max = palette.block_opacity_max
    # Use a mid-range opacity for all buildings (simpler, cleaner)
    op = round((op_min + op_max) / 2, 2)

    for bld in buildings:
        pts = _coords_to_polygon_points(bld["coords"], proj)
        svg.append(
            f'    <polygon points="{pts}" fill="{fill}" '
            f'opacity="{op}" stroke="{fill}" stroke-width="0.3" '
            f'stroke-opacity="{min(op + 0.1, 1.0)}"/>'
        )

    return "\n".join(svg)


def _render_neighbourhood_labels(
    neighbourhoods: list,
    proj: MercatorProjection,
    palette: MapPalette,
    building_lat: float,
    building_lon: float,
) -> str:
    """Render neighbourhood/suburb names as large, semi-transparent background text.

    These go BEHIND roads and POIs — they're part of the map fabric, like area
    labels on professional illustrated maps. Big serif, low opacity, all caps.
    """
    svg = []
    bx, by = proj.project(building_lat, building_lon)

    for nb in neighbourhoods:
        x, y = proj.project(nb["lat"], nb["lon"])
        name = nb.get("name", "")
        if not name:
            continue

        # Skip if too close to building marker
        dist = math.sqrt((x - bx) ** 2 + (y - by) ** 2)
        if dist < 80:
            continue

        # Skip if outside visible area (with margin)
        if x < -20 or x > proj.width_px + 20 or y < -20 or y > proj.height_px + 20:
            continue

        svg.append(
            f'  <text x="{x}" y="{y}" font-size="32" '
            f'fill="{palette.text_primary}" opacity="0.10" '
            f'class="map-serif" font-weight="700" '
            f'text-anchor="middle" letter-spacing="8">'
            f'{_esc(name.upper())}</text>'
        )

    return "\n".join(svg)


# _point_in_polygon is imported from brochure_maker.geometry


def _scatter_tree_dots(
    coords: list, proj: MercatorProjection, palette: MapPalette,
    spacing: float = 18.0,
) -> list[str]:
    """Generate scattered decorative tree dots inside a park polygon.

    Uses a grid + jitter approach: lay a grid across the bounding box,
    then only place dots that fall inside the polygon. Each dot is a
    small filled circle with slight size/opacity variation for organic look.
    """
    import random as _rng

    pts = [proj.project(c[0], c[1]) for c in coords]
    if len(pts) < 3:
        return []

    # Bounding box
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    # Skip tiny parks
    if (max_x - min_x) < 25 or (max_y - min_y) < 25:
        return []

    # Deterministic seed based on polygon centroid (reproducible)
    seed = int((min_x + max_x) * 100 + (min_y + max_y) * 37) & 0xFFFFFF
    rng = _rng.Random(seed)

    dark_green = _darken_hex(palette.park_fill, 0.7)
    dots = []
    y = min_y + spacing / 2
    while y < max_y:
        x = min_x + spacing / 2
        while x < max_x:
            # Jitter
            jx = x + rng.uniform(-spacing * 0.3, spacing * 0.3)
            jy = y + rng.uniform(-spacing * 0.3, spacing * 0.3)
            if _point_in_polygon(jx, jy, pts):
                r = rng.uniform(2.5, 4.5)
                op = rng.uniform(0.18, 0.35)
                dots.append(
                    f'    <circle cx="{jx:.1f}" cy="{jy:.1f}" r="{r:.1f}" '
                    f'fill="{dark_green}" opacity="{op:.2f}"/>'
                )
            x += spacing
        y += spacing

    return dots


def _render_parks(parks: list, proj: MercatorProjection, palette: MapPalette) -> str:
    """Render parks as filled polygons with scattered tree dots for texture."""
    svg = []
    for park in parks:
        pts = _coords_to_polygon_points(park["coords"], proj)
        svg.append(
            f'  <polygon points="{pts}" fill="{palette.park_fill}" '
            f'opacity="{palette.park_opacity}"/>'
        )
        # Decorative tree dots
        tree_dots = _scatter_tree_dots(park["coords"], proj, palette)
        svg.extend(tree_dots)

        name = park.get("name", "")
        if name:
            cx, cy = _polygon_centroid(park["coords"], proj)
            svg.append(
                f'  <text x="{cx}" y="{cy}" font-size="9" '
                f'fill="{palette.park_label}" opacity="0.95" '
                f'stroke="{palette.park_fill}" stroke-width="2" paint-order="stroke" '
                f'class="map-sans" font-weight="600" '
                f'text-anchor="middle" letter-spacing="2">'
                f'{_esc(name.upper())}</text>'
            )
    return "\n".join(svg)


def _render_waterways(waterways: list, proj: MercatorProjection, palette: MapPalette) -> str:
    """Render waterways with bold, clean strokes (width 10-16)."""
    svg = []
    for ww in waterways:
        d = _coords_to_path_d(ww["coords"], proj)
        wtype = ww.get("waterway", "")
        sw = "9" if wtype in ("canal", "river") else "6"
        svg.append(
            f'  <path d="{d}" stroke="{palette.water_stroke}" stroke-width="{sw}" '
            f'fill="none" stroke-linecap="round" opacity="{palette.water_opacity}"/>'
        )
        name = ww.get("name", "")
        if name:
            mx, my = _way_midpoint(ww["coords"], proj)
            angle = _way_angle_at_midpoint(ww["coords"], proj)
            svg.append(
                f'  <text x="{mx}" y="{my - 10}" font-size="9" '
                f'fill="{palette.water_label}" opacity="0.95" '
                f'class="map-sans" font-style="italic" font-weight="500" '
                f'text-anchor="middle" '
                f'transform="rotate({angle},{mx},{my - 10})">'
                f'{_esc(name)}</text>'
            )
    return "\n".join(svg)


def _render_streets_cased(
    streets: list, proj: MercatorProjection, palette: MapPalette,
) -> str:
    """Render roads with cased strokes (dark underlay + light overlay).

    Each road is drawn twice to create the poster/illustrated look.
    Roads are pre-projected, simplified (RDP), and grid-snapped.
    """
    # Cased stroke widths: (underlay, overlay) per highway class
    CASED_WIDTHS = {
        "trunk":          (14, 10),
        "primary":        (14, 10),
        "secondary":      (11, 8),
        "tertiary":       (8, 5.5),
        "residential":    (5.5, 3.5),
        "unclassified":   (5.5, 3.5),
        "pedestrian":     (4, 2.5),
        "living_street":  (4, 2.5),
    }
    UNDERLAY_OPACITY = {
        "trunk": 0.80, "primary": 0.80, "secondary": 0.70,
        "tertiary": 0.60, "residential": 0.50, "unclassified": 0.50,
        "pedestrian": 0.40, "living_street": 0.40,
    }
    OVERLAY_OPACITY = {
        "trunk": 0.50, "primary": 0.50, "secondary": 0.42,
        "tertiary": 0.35, "residential": 0.28, "unclassified": 0.28,
        "pedestrian": 0.20, "living_street": 0.20,
    }
    # RDP tolerance per class (higher = more simplified)
    RDP_TOLERANCE = {
        "trunk": 1.0, "primary": 1.0, "secondary": 1.5,
        "tertiary": 2.0, "residential": 3.0, "unclassified": 3.0,
        "pedestrian": 3.5, "living_street": 3.5,
    }

    underlay_svg = []
    overlay_svg = []

    for st in streets:
        hw = st.get("highway", "residential")
        under_w, over_w = CASED_WIDTHS.get(hw, (6, 4))
        under_op = UNDERLAY_OPACITY.get(hw, 0.45)
        over_op = OVERLAY_OPACITY.get(hw, 0.35)
        tol = RDP_TOLERANCE.get(hw, 3.0)

        # Project, simplify, snap
        raw_pts = [proj.project(c[0], c[1]) for c in st["coords"]]
        simplified = _rdp_simplify(raw_pts, tol)
        snapped = [_snap_to_grid(x, y) for x, y in simplified]
        if len(snapped) < 2:
            continue

        d = _points_to_path_d(snapped)

        # Store processed points for label use
        st["_px_points"] = snapped

        # Underlay (dark, wider)
        underlay_svg.append(
            f'    <path d="{d}" stroke="{palette.road_stroke}" '
            f'stroke-width="{under_w}" fill="none" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'opacity="{under_op}"/>'
        )
        # Overlay (white, narrower) — classic illustrated map look: white roads on colour
        overlay_svg.append(
            f'    <path d="{d}" stroke="white" '
            f'stroke-width="{over_w}" fill="none" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'opacity="{over_op}"/>'
        )

    # Draw all underlays first, then overlays (so overlays sit on top)
    svg = underlay_svg + overlay_svg
    return "\n".join(svg)


def _render_street_labels(
    streets: list, proj: MercatorProjection, palette: MapPalette,
    building_x: float = 0, building_y: float = 0,
    max_labels: int = 4,
    min_px_length: float = 120,
) -> tuple[str, list]:
    """Render street labels — capped to max_labels, min rendered length 120px.

    Returns (svg_string, occupied_rects) so POI labels can reuse the
    occupied list to avoid overlapping street names.
    """
    occupied: list = []

    # Deduplicate: pick longest segment per street name
    best_by_name: dict[str, dict] = {}
    for st in streets:
        name = st.get("name", "")
        if not name:
            continue
        # Use pre-processed points if available
        pts = st.get("_px_points")
        if pts:
            px_len = _way_pixel_length_fast(pts)
        else:
            px_len = _way_pixel_length(st["coords"], proj)
        if px_len < min_px_length:
            continue
        key = name.upper()
        if key not in best_by_name or px_len > best_by_name[key]["_px_len"]:
            best_by_name[key] = {**st, "_px_len": px_len}

    # Sort by pixel length (longest first) and cap
    sorted_labels = sorted(best_by_name.items(), key=lambda kv: -kv[1]["_px_len"])

    svg = []
    label_count = 0
    for name_upper, st in sorted_labels:
        if label_count >= max_labels:
            break

        pts = st.get("_px_points")
        if pts:
            mx, my = _way_midpoint_fast(pts)
            angle = _way_angle_fast(pts)
        else:
            mx, my = _way_midpoint(st["coords"], proj)
            angle = _way_angle_at_midpoint(st["coords"], proj)

        # Skip labels too close to building marker (within 40px)
        dx = mx - building_x
        dy = my - building_y
        if math.sqrt(dx * dx + dy * dy) < 55:
            continue

        # Skip overlapping labels
        if _label_overlaps(mx, my - 4, name_upper, occupied, font_size=9.5):
            continue

        svg.append(
            f'  <text x="{mx}" y="{my - 5}" font-size="10" '
            f'fill="{palette.street_label}" opacity="0.95" '
            f'stroke="{palette.text_stroke}" stroke-width="3" paint-order="stroke" '
            f'class="map-sans" font-weight="600" '
            f'text-anchor="middle" letter-spacing="2.2" '
            f'transform="rotate({angle},{mx},{my - 5})">'
            f'{_esc(name_upper)}</text>'
        )
        label_count += 1
    return "\n".join(svg), occupied


def _render_pois(
    pois: list,
    proj: MercatorProjection,
    building_lat: float,
    building_lon: float,
    palette: MapPalette,
    occupied: list | None = None,
    max_count: int = 15,
    debug: bool = False,
) -> str:
    """Render POIs with inline SVG icons + multi-offset collision detection.

    Per-category caps prevent any single type dominating.
    Labels try 6 offsets (E, NE, SE, W, NW, SW) before being skipped.
    """
    if occupied is None:
        occupied = []

    # Apply per-category caps
    type_counts: dict[str, int] = {}
    capped: list[dict] = []
    def dist(p):
        dlat = p["lat"] - building_lat
        dlon = (p["lon"] - building_lon) * math.cos(math.radians(building_lat))
        return dlat * dlat + dlon * dlon

    for poi in sorted(pois, key=dist):
        ptype = poi.get("type", "other")
        cap = POI_CATEGORY_CAPS.get(ptype, 3)
        count = type_counts.get(ptype, 0)
        if count >= cap:
            continue
        type_counts[ptype] = count + 1
        capped.append(poi)
        if len(capped) >= max_count:
            break

    svg = []
    debug_svg = []
    placed = 0
    skipped = 0

    bx, by = proj.project(building_lat, building_lon)
    poi_anchors: list[tuple[float, float]] = []

    for poi in capped:
        x, y = proj.project(poi["lat"], poi["lon"])

        # Building clear zone — skip icon AND label within 45px
        if math.sqrt((x - bx) ** 2 + (y - by) ** 2) < 45:
            skipped += 1
            continue

        # Minimum distance between POI anchors (18px)
        if any(math.sqrt((x - px) ** 2 + (y - py) ** 2) < 18
               for px, py in poi_anchors):
            skipped += 1
            continue

        name = poi.get("name", "")
        ptype = poi.get("type", "")

        # Try label placement with 6 offsets
        placement = _try_poi_placement(x, y, name, occupied, font_size=11, icon_gap=16)

        # Draw icon dot (passed distance checks)
        svg.append(f'  {_render_poi_icon(x, y, ptype, palette)}')

        if placement:
            lx, ly = placement
            svg.append(
                f'  <text x="{lx}" y="{ly}" font-size="11" '
                f'fill="{palette.poi_label}" opacity="1.0" '
                f'stroke="{palette.text_stroke}" stroke-width="3" '
                f'paint-order="stroke" '
                f'class="map-sans" font-weight="500">'
                f'{_esc(name)}</text>'
            )
            placed += 1
            if debug:
                w = len(name) * 11 * 0.55 + 16
                h = 11 * 1.2
                debug_svg.append(
                    f'  <rect x="{lx}" y="{ly - h}" width="{w}" height="{h}" '
                    f'fill="none" stroke="red" stroke-width="0.5" opacity="0.5"/>'
                )
        else:
            skipped += 1
            if debug:
                debug_svg.append(
                    f'  <circle cx="{x}" cy="{y}" r="8" '
                    f'fill="none" stroke="orange" stroke-width="0.5" opacity="0.5"/>'
                )

        poi_anchors.append((x, y))

    logger.info("POIs: %d placed, %d skipped (collision)", placed, skipped)

    if debug:
        svg.extend(debug_svg)
    return "\n".join(svg)


def _edge_text_anchor(x: float, width: int, margin: int) -> str:
    """Determine text-anchor based on which edge the station is clamped to."""
    if x <= margin + 10:
        return "start"
    elif x >= width - margin - 10:
        return "end"
    return "middle"


def _render_station_markers(
    stations: list[dict],
    proj: MercatorProjection,
    colour_scheme: dict,
    palette: MapPalette,
) -> str:
    """Render station markers — large serif text for walking stations,
    TfL roundels for transit stations."""
    svg = []

    # Edge margins for clamping
    edge_margin = 60
    min_x, max_x = edge_margin, proj.width_px - edge_margin
    min_y, max_y = edge_margin, proj.height_px - edge_margin

    for st in stations:
        x, y = proj.project(st["lat"], st["lon"])
        name = st.get("display_name", st.get("name", ""))
        time_str = st.get("time", "")
        is_walking = "walk" in time_str.lower()

        # Check if station is within the visible area
        in_view = (0 <= x <= proj.width_px and 0 <= y <= proj.height_px)

        if not in_view and not is_walking:
            continue

        # For walking stations off-screen, clamp to edge
        clamped = False
        if not in_view and is_walking:
            clamped = True
            x = max(min_x, min(max_x, x))
            y = max(min_y, min(max_y, y))

        svg.append(f'  <g transform="translate({x},{y})">')

        if is_walking:
            # --- WALKING STATIONS: large serif text at edges ---
            anchor = _edge_text_anchor(x, proj.width_px, edge_margin)
            name_upper = name.upper()

            # Small roundel next to text (TfL branding — stays red/white)
            roundel_offset = -16 if anchor == "end" else (16 if anchor == "start" else 0)
            text_x = roundel_offset + (8 if anchor == "start" else (-8 if anchor == "end" else 0))

            # Roundel
            svg.append(
                f'    <circle cx="{roundel_offset}" cy="0" r="6" '
                f'fill="white" stroke="#CC3333" stroke-width="2"/>'
            )
            svg.append(
                f'    <rect x="{roundel_offset - 7.5}" y="-1.5" '
                f'width="15" height="3" rx="1" fill="#CC3333"/>'
            )

            # Station name — large Playfair Display serif
            svg.append(
                f'    <text x="{text_x}" y="-14" font-size="20" '
                f'fill="{palette.text_primary}" '
                f'stroke="{palette.text_stroke}" stroke-width="3" paint-order="stroke" '
                f'class="map-serif" font-weight="700" '
                f'text-anchor="{anchor}" letter-spacing="3">'
                f'{_esc(name_upper)}</text>'
            )

            # Time text below
            if time_str:
                svg.append(
                    f'    <text x="{text_x}" y="20" font-size="9" '
                    f'fill="{palette.text_secondary}" '
                    f'class="map-sans" font-weight="300" font-style="italic" '
                    f'text-anchor="{anchor}">{_esc(time_str)}</text>'
                )
        else:
            # --- TRANSIT STATIONS: TfL roundel with serif label ---
            # Roundels stay red/white (TfL brand identity)
            r = 10
            svg.append(
                f'    <circle r="{r}" fill="white" stroke="#CC3333" stroke-width="3.0"/>'
            )
            bar_w = r * 2.5
            svg.append(
                f'    <rect x="{-bar_w / 2}" y="-2.5" width="{bar_w}" height="5" '
                f'rx="1.5" fill="#CC3333"/>'
            )
            # Station name — Playfair Display serif
            svg.append(
                f'    <text x="0" y="{-r - 8}" font-size="13" fill="{palette.text_primary}" '
                f'stroke="{palette.text_stroke}" stroke-width="2.5" paint-order="stroke" '
                f'class="map-serif" font-weight="700" '
                f'text-anchor="middle" letter-spacing="2">'
                f'{_esc(name.upper())}</text>'
            )
            if time_str:
                svg.append(
                    f'    <text x="0" y="{-r - 22}" font-size="8" '
                    f'fill="{palette.text_secondary}" '
                    f'class="map-sans" font-weight="300" '
                    f'text-anchor="middle">{_esc(time_str)}</text>'
                )

        svg.append('  </g>')
    return "\n".join(svg)


def _render_building_marker(
    lat: float,
    lon: float,
    name: str,
    proj: MercatorProjection,
    colour_scheme: dict,
    palette: MapPalette,
) -> str:
    """Render the subject building as a diamond marker with large serif label."""
    x, y = proj.project(lat, lon)
    primary = colour_scheme.get("primary", palette.building_primary)
    primary_dark = colour_scheme.get("primary_dark", palette.building_dark)
    label_text = name.upper()

    svg = [f'  <g transform="translate({x},{y})">']
    # Diamond shape — hero size
    svg.append(
        f'    <polygon points="0,-24 16,0 0,24 -16,0" '
        f'fill="{palette.building_label}" stroke="{primary_dark}" stroke-width="2.5"/>'
    )
    # Inner dot — keeps brand primary colour
    svg.append(f'    <circle r="5" fill="{primary}"/>')
    # Label text — large Playfair Display serif with thick halo
    svg.append(
        f'    <text x="0" y="-42" font-size="28" fill="{palette.building_label}" '
        f'stroke="{palette.text_stroke}" stroke-width="5" paint-order="stroke" '
        f'class="map-serif" font-weight="700" '
        f'text-anchor="middle" letter-spacing="3">{_esc(label_text)}</text>'
    )
    svg.append('  </g>')
    return "\n".join(svg)


# ──────────────────────────────────────────────
#  SVG Assembly
# ──────────────────────────────────────────────

def build_illustrated_svg(
    features: dict,
    proj: MercatorProjection,
    building_coords: tuple[float, float],
    building_name: str,
    station_data: list[dict],
    colour_scheme: dict,
    palette: MapPalette,
    width: int = 940,
    height: int = 750,
    debug: bool = False,
) -> str:
    """Build the complete SVG string for the illustrated map.

    The SVG has a transparent background (no fill rect) so the parent
    element's CSS background colour shows through — enabling dynamic
    colour adaptation when the user changes the brochure colour scheme.
    All element colours are derived from the palette (based on brochure
    background colour) so the map integrates with any colour scheme.
    """
    layers = []

    # Font class definitions (fonts loaded in brochure <head>)
    layers.append(
        '  <defs><style>\n'
        '    .map-serif { font-family: "Playfair Display", "Georgia", serif; }\n'
        '    .map-sans  { font-family: "Jost", "Segoe UI", sans-serif; }\n'
        '  </style></defs>'
    )

    # Layer 1: Neighbourhood labels (large, faint background text — behind everything)
    nb_svg = _render_neighbourhood_labels(
        features.get("neighbourhoods", []), proj, palette,
        building_coords[0], building_coords[1],
    )
    if nb_svg:
        layers.append(f'  <g class="neighbourhood-labels">\n{nb_svg}\n  </g>')

    # Layer 2: Building footprints (city fabric texture)
    buildings_svg = _render_buildings(features.get("buildings", []), proj, palette)
    if buildings_svg:
        layers.append(f'  <g class="buildings">\n{buildings_svg}\n  </g>')

    # Layer 3: Parks (with decorative tree dots)
    parks_svg = _render_parks(features["parks"], proj, palette)
    if parks_svg:
        layers.append(f'  <g class="parks">\n{parks_svg}\n  </g>')

    # Layer 4: Waterways
    water_svg = _render_waterways(features["waterways"], proj, palette)
    if water_svg:
        layers.append(f'  <g class="waterways">\n{water_svg}\n  </g>')

    # Layer 5: Streets (cased strokes)
    streets_svg = _render_streets_cased(features["streets"], proj, palette)
    if streets_svg:
        layers.append(f'  <g class="streets">\n{streets_svg}\n  </g>')

    # Layer 6: Street labels (with collision detection)
    bx, by = proj.project(building_coords[0], building_coords[1])
    street_labels_svg, occupied_rects = _render_street_labels(
        features["streets"], proj, palette,
        building_x=bx, building_y=by,
        max_labels=4,
    )
    if street_labels_svg:
        layers.append(f'  <g class="street-labels">\n{street_labels_svg}\n  </g>')

    # Layer 7: POI icons + labels (reuse occupied rects from street labels)
    poi_svg = _render_pois(
        features["pois"], proj,
        building_coords[0], building_coords[1], palette,
        occupied=occupied_rects,
        debug=debug,
    )
    if poi_svg:
        layers.append(f'  <g class="pois">\n{poi_svg}\n  </g>')

    # Layer 8: Station markers
    station_svg = _render_station_markers(station_data, proj, colour_scheme, palette)
    if station_svg:
        layers.append(f'  <g class="stations">\n{station_svg}\n  </g>')

    # Layer 9: Building marker (always on top)
    bld_svg = _render_building_marker(
        building_coords[0], building_coords[1],
        building_name, proj, colour_scheme, palette,
    )
    layers.append(f'  <g class="building-marker">\n{bld_svg}\n  </g>')

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="100%" height="100%" '
        f'viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet">\n'
        + "\n".join(layers)
        + "\n</svg>"
    )
    return svg


# ──────────────────────────────────────────────
#  HTML Wrapper
# ──────────────────────────────────────────────

def wrap_svg_in_html(
    svg_content: str,
    width: int,
    height: int,
    bg_colour: str,
) -> str:
    """Wrap SVG in minimal HTML page for Playwright rendering."""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Jost:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ width: {width}px; height: {height}px; overflow: hidden; background: {bg_colour}; font-family: Jost, sans-serif; }}
</style>
</head>
<body>
{svg_content}
<script>
  // Signal ready after brief delay for font + render
  document.title = 'MAP_READY';
</script>
</body>
</html>"""


# ──────────────────────────────────────────────
#  Station Matching
# ──────────────────────────────────────────────

def _normalize_station_name(name: str) -> str:
    """Normalize a station name for fuzzy matching."""
    s = name.upper().strip()
    for remove in ("STATION", "UNDERGROUND", "OVERGROUND", "RAIL", "DLR"):
        s = s.replace(remove, "")
    s = s.replace(".", "").replace("'", "").replace("\u2019", "")
    s = s.replace("&", "AND").replace("-", " ").replace("ST ", "ST. ")
    return " ".join(s.split())


async def match_stations_to_overpass(
    analysis_stations: list[dict],
    overpass_stations: list[dict],
    proj: MercatorProjection,
    location: str = "",
) -> list[dict]:
    """Match analysis.json stations to Overpass rail_station nodes.

    Falls back to Nominatim geocoding for walking-distance stations
    that aren't found in Overpass data.

    Returns a list of station dicts with lat, lon, name, time, display_name.
    Only includes stations that fall within the map bounds.
    """
    # Build lookup from normalized overpass names
    osm_lookup = {}
    for rs in overpass_stations:
        key = _normalize_station_name(rs.get("name", ""))
        if key:
            osm_lookup[key] = rs

    logger.debug("OSM station lookup keys: %s", list(osm_lookup.keys()))

    results = []
    unmatched_walking = []

    for st in analysis_stations:
        name = st.get("name", "")
        time_str = st.get("time", "")
        norm = _normalize_station_name(name)
        is_walking = "walk" in time_str.lower()

        # Try exact match
        osm = osm_lookup.get(norm)
        if osm:
            lat, lon = osm["lat"], osm["lon"]
            logger.debug("Station EXACT match: '%s' -> (%s, %s)", norm, lat, lon)
        else:
            # Try partial match
            matched = None
            for key, val in osm_lookup.items():
                if norm in key or key in norm:
                    matched = val
                    break
            if matched:
                lat, lon = matched["lat"], matched["lon"]
                logger.debug("Station PARTIAL match: '%s' via '%s'", norm, key)
            else:
                logger.debug("Station NO match: '%s' (walking=%s)", norm, is_walking)
                # Walking stations are important — geocode them as fallback
                if is_walking:
                    unmatched_walking.append(st)
                continue

        # Walking stations: always include (skip bounds check)
        if is_walking:
            results.append({
                "name": name,
                "display_name": name.title().replace("'S", "'s"),
                "time": time_str,
                "lat": lat,
                "lon": lon,
                "lines": st.get("lines", []),
            })
            continue

        # Only include non-walking if within map bounds (with margin)
        if not proj.in_bounds(lat, lon, margin=0.003):
            logger.debug("Station out of bounds: '%s'", norm)
            continue

        results.append({
            "name": name,
            "display_name": name.title().replace("'S", "'s"),
            "time": time_str,
            "lat": lat,
            "lon": lon,
            "lines": st.get("lines", []),
        })

    # Fallback: geocode unmatched walking stations via Nominatim
    # Walking stations are always included (they're the most important ones)
    if unmatched_walking:
        logger.info("Geocoding %d unmatched walking stations", len(unmatched_walking))
        from brochure_maker.map_generator import geocode_stations
        geocoded = await geocode_stations(unmatched_walking, location)
        for gc in geocoded:
            logger.debug("Geocoded '%s' -> (%s, %s)", gc["name"], gc["lat"], gc["lon"])
            results.append({
                "name": gc["name"],
                "display_name": gc["name"].title().replace("'S", "'s"),
                "time": gc.get("time", ""),
                "lat": gc["lat"],
                "lon": gc["lon"],
                "lines": [],
            })

    logger.info("Station matching: %d stations matched", len(results))
    return results


# ──────────────────────────────────────────────
#  Main Entry Point
# ──────────────────────────────────────────────

async def generate_illustrated_map(
    building_coords: tuple[float, float],
    building_name: str,
    location: str,
    stations: list[dict],
    colour_scheme: dict,
    width: int = 940,
    height: int = 750,
    radius_m: int = 350,
    base_hex: str = "",
    debug: bool = False,
) -> str:
    """Full pipeline: fetch data, project, build SVG, return raw SVG string.

    Args:
        base_hex: Brochure background colour. All map element colours are
            derived from this so the map integrates with the chosen scheme.
            Falls back to colour_scheme["primary"] if empty.

    Returns:
        SVG markup string (no HTML wrapper) with transparent background.
        The SVG is designed for inline DOM injection where the parent
        element provides the background colour via CSS.
    """
    lat, lon = building_coords

    # Derive map palette from brochure background colour
    effective_base = base_hex or colour_scheme.get("primary", "#B8714E")
    palette = derive_map_palette(
        base_hex=effective_base,
        primary_hex=colour_scheme.get("primary", ""),
    )

    # Auto-radius: start at requested radius, widen if too few POIs
    features = None
    for attempt_radius in [radius_m, radius_m + 100, radius_m + 200]:
        raw_data = await fetch_overpass_data(lat, lon, radius_m=attempt_radius)
        features = _parse_overpass_elements(raw_data)
        if len(features["pois"]) >= 8 or attempt_radius >= radius_m + 200:
            radius_m = attempt_radius
            break
        logger.info("Only %d POIs at %dm, widening", len(features["pois"]), attempt_radius)

    # Set up projection
    proj = MercatorProjection(
        center_lat=lat,
        center_lon=lon,
        width_px=width,
        height_px=height,
        radius_m=radius_m,
        padding_px=40,
    )

    # Pre-project street coordinates for filtering
    for st in features["streets"]:
        st["_px_points"] = [proj.project(c[0], c[1]) for c in st["coords"]]

    # Filter roads: keep major, cap residential, drop tiny fragments
    features["streets"] = _filter_roads(
        features["streets"], proj, lat, lon,
        max_residential=70,
    )

    # Match analysis stations to Overpass station locations
    matched_stations = await match_stations_to_overpass(
        stations, features["rail_stations"], proj, location=location,
    )

    # Build SVG
    svg = build_illustrated_svg(
        features=features,
        proj=proj,
        building_coords=building_coords,
        building_name=building_name,
        station_data=matched_stations,
        colour_scheme=colour_scheme,
        palette=palette,
        width=width,
        height=height,
        debug=debug,
    )

    return svg
