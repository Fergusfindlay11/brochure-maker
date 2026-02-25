"""SVG Renderer — multi-pass road rendering with canonical output.

Mandatory road pass order:
  Pass 1: minor casings
  Pass 2: minor fills
  Pass 3: major casings
  Pass 4: major fills
Then: parks, water, buildings, labels, icons, stations, building marker.

SVG output is byte-deterministic: canonical XML with stable attribute order
and stable float rounding.
"""

from __future__ import annotations

import hashlib
import math
import logging
from typing import Any

from brochure_maker.geometry import (
    MercatorProjection,
    rdp_simplify as _rdp_simplify,
    snap_to_grid as _snap,
    way_pixel_length as _way_pixel_length,
    way_midpoint as _way_midpoint,
    way_angle as _way_angle,
    points_to_path_d as _points_to_path_d,
    coords_to_polygon_points as _polygon_points_str,
    polygon_centroid as _polygon_centroid,
    point_in_polygon as _point_in_polygon,
    svg_escape as _esc,
)

from .basemap import BasemapFeature
from .feature_selection import SelectedFeatures, SelectedPOI
from .label_engine import (
    LabelCandidate,
    LabelPlacementResult,
    PlacedLabel,
    place_labels,
)
from .models import StyleTokens, LabelMetrics
from .text_metrics import measure_text

logger = logging.getLogger(__name__)

# Major road classes (get wider strokes, painted on top)
MAJOR_ROADS = {"trunk", "primary", "secondary", "tertiary"}

# POI icon SVG paths (12x12 viewBox)
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


# MercatorProjection, geometry helpers, and SVG path utilities
# are imported from brochure_maker.geometry (single source of truth).


# ──────────────────────────────────────────────
#  Road Pass Classification
# ──────────────────────────────────────────────

def _classify_road(highway: str) -> str:
    """Classify road as 'major' or 'minor'."""
    if highway in MAJOR_ROADS:
        return "major"
    return "minor"


def _process_road(
    road: BasemapFeature,
    proj: MercatorProjection,
) -> dict[str, Any] | None:
    """Project and simplify a road. Returns processed data or None."""
    raw_pts = [proj.project(c[0], c[1]) for c in road.coords]
    hw = road.properties.get("highway", "residential")

    rdp_tol = {
        "trunk": 1.0, "primary": 1.0, "secondary": 1.5,
        "tertiary": 2.0, "residential": 3.0, "unclassified": 3.0,
        "pedestrian": 3.5, "living_street": 3.5,
    }.get(hw, 3.0)

    simplified = _rdp_simplify(raw_pts, rdp_tol)
    snapped = [_snap(x, y) for x, y in simplified]
    if len(snapped) < 2:
        return None

    px_len = _way_pixel_length(snapped)
    if px_len < 30:
        return None

    return {
        "highway": hw,
        "class": _classify_road(hw),
        "name": road.properties.get("name", ""),
        "px_points": snapped,
        "px_len": px_len,
        "path_d": _points_to_path_d(snapped),
    }


# ──────────────────────────────────────────────
#  SVG Layer Builders
# ──────────────────────────────────────────────

def _render_road_passes(
    processed_roads: list[dict],
    tokens: StyleTokens,
) -> str:
    """Render roads in strict 4-pass order for clean intersections.

    Pass 1: minor casings
    Pass 2: minor fills
    Pass 3: major casings
    Pass 4: major fills
    """
    minor_casings = []
    minor_fills = []
    major_casings = []
    major_fills = []

    for road in processed_roads:
        d = road["path_d"]
        cls = road["class"]

        if cls == "minor":
            minor_casings.append(
                f'    <path d="{d}" stroke="{tokens.roads.minor_casing}" '
                f'stroke-width="{tokens.roads.minor_casing_width}" fill="none" '
                f'stroke-linecap="round" stroke-linejoin="round" '
                f'opacity="{tokens.roads.minor_casing_opacity}"/>'
            )
            minor_fills.append(
                f'    <path d="{d}" stroke="{tokens.roads.minor_fill}" '
                f'stroke-width="{tokens.roads.minor_fill_width}" fill="none" '
                f'stroke-linecap="round" stroke-linejoin="round" '
                f'opacity="{tokens.roads.minor_fill_opacity}"/>'
            )
        else:
            major_casings.append(
                f'    <path d="{d}" stroke="{tokens.roads.major_casing}" '
                f'stroke-width="{tokens.roads.major_casing_width}" fill="none" '
                f'stroke-linecap="round" stroke-linejoin="round" '
                f'opacity="{tokens.roads.major_casing_opacity}"/>'
            )
            major_fills.append(
                f'    <path d="{d}" stroke="{tokens.roads.major_fill}" '
                f'stroke-width="{tokens.roads.major_fill_width}" fill="none" '
                f'stroke-linecap="round" stroke-linejoin="round" '
                f'opacity="{tokens.roads.major_fill_opacity}"/>'
            )

    svg_parts = []
    if minor_casings:
        svg_parts.append(f'  <g class="roads-minor-casing">\n' + "\n".join(minor_casings) + "\n  </g>")
    if minor_fills:
        svg_parts.append(f'  <g class="roads-minor-fill">\n' + "\n".join(minor_fills) + "\n  </g>")
    if major_casings:
        svg_parts.append(f'  <g class="roads-major-casing">\n' + "\n".join(major_casings) + "\n  </g>")
    if major_fills:
        svg_parts.append(f'  <g class="roads-major-fill">\n' + "\n".join(major_fills) + "\n  </g>")

    return "\n".join(svg_parts)


def _render_buildings(
    buildings: list[BasemapFeature],
    proj: MercatorProjection,
    tokens: StyleTokens,
) -> str:
    svg = []
    fill = tokens.features.block_fill
    op = tokens.features.block_opacity

    for bld in buildings:
        pts = _polygon_points_str(bld.coords, proj)
        svg.append(
            f'    <polygon points="{pts}" fill="{fill}" '
            f'opacity="{op}" stroke="{fill}" stroke-width="0.3" '
            f'stroke-opacity="{min(op + 0.1, 1.0)}"/>'
        )

    if not svg:
        return ""
    return f'  <g class="buildings">\n' + "\n".join(svg) + "\n  </g>"


def _render_parks(
    parks: list[BasemapFeature],
    proj: MercatorProjection,
    tokens: StyleTokens,
) -> str:
    import random as _rng

    svg = []
    park_fill = tokens.features.park_fill
    park_op = tokens.features.park_opacity

    for park in parks:
        pts = _polygon_points_str(park.coords, proj)
        svg.append(
            f'  <polygon points="{pts}" fill="{park_fill}" '
            f'opacity="{park_op}"/>'
        )

        # Decorative tree dots
        px_pts = [proj.project(c[0], c[1]) for c in park.coords]
        if len(px_pts) >= 3:
            xs = [p[0] for p in px_pts]
            ys = [p[1] for p in px_pts]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            if (max_x - min_x) >= 25 and (max_y - min_y) >= 25:
                seed = int((min_x + max_x) * 100 + (min_y + max_y) * 37) & 0xFFFFFF
                rng = _rng.Random(seed)
                spacing = 18.0
                # Darken park fill for dots
                from .style_director import _darken
                dark_green = _darken(park_fill, 0.7)

                y = min_y + spacing / 2
                while y < max_y:
                    x = min_x + spacing / 2
                    while x < max_x:
                        jx = x + rng.uniform(-spacing * 0.3, spacing * 0.3)
                        jy = y + rng.uniform(-spacing * 0.3, spacing * 0.3)
                        if _point_in_polygon(jx, jy, px_pts):
                            r = rng.uniform(2.5, 4.5)
                            op = rng.uniform(0.18, 0.35)
                            svg.append(
                                f'    <circle cx="{jx:.1f}" cy="{jy:.1f}" r="{r:.1f}" '
                                f'fill="{dark_green}" opacity="{op:.2f}"/>'
                            )
                        x += spacing
                    y += spacing

    if not svg:
        return ""
    return f'  <g class="parks">\n' + "\n".join(svg) + "\n  </g>"


def _render_waterways(
    water: list[BasemapFeature],
    proj: MercatorProjection,
    tokens: StyleTokens,
) -> str:
    svg = []
    for ww in water:
        raw_pts = [proj.project(c[0], c[1]) for c in ww.coords]
        if len(raw_pts) < 2:
            continue
        d = _points_to_path_d(raw_pts)
        wtype = ww.properties.get("waterway", "")
        sw = "9" if wtype in ("canal", "river") else "6"
        svg.append(
            f'  <path d="{d}" stroke="{tokens.features.water_stroke}" stroke-width="{sw}" '
            f'fill="none" stroke-linecap="round" opacity="{tokens.features.water_opacity}"/>'
        )

    if not svg:
        return ""
    return f'  <g class="waterways">\n' + "\n".join(svg) + "\n  </g>"


def _render_neighbourhoods(
    neighbourhoods: list[dict],
    proj: MercatorProjection,
    tokens: StyleTokens,
    building_lat: float,
    building_lon: float,
) -> str:
    svg = []
    bx, by = proj.project(building_lat, building_lon)

    for nb in neighbourhoods:
        x, y = proj.project(nb["lat"], nb["lon"])
        name = nb.get("name", "")
        if not name:
            continue
        dist = math.sqrt((x - bx) ** 2 + (y - by) ** 2)
        if dist < 80:
            continue
        if x < -20 or x > proj.width_px + 20 or y < -20 or y > proj.height_px + 20:
            continue

        svg.append(
            f'  <text x="{x}" y="{y}" font-size="32" '
            f'fill="{tokens.labels.neighbourhood_colour}" opacity="{tokens.labels.neighbourhood_opacity}" '
            f'class="map-serif" font-weight="700" '
            f'text-anchor="middle" letter-spacing="8">'
            f'{_esc(name.upper())}</text>'
        )

    if not svg:
        return ""
    return f'  <g class="neighbourhood-labels">\n' + "\n".join(svg) + "\n  </g>"


def _render_poi_icon(x: float, y: float, poi_type: str, colour: str) -> str:
    path_d = POI_ICON_PATHS.get(poi_type, "M4 6h4M6 4v4")
    return (
        f'<g transform="translate({x - 7},{y - 7})">'
        f'<path d="{path_d}" fill="none" stroke="{colour}" '
        f'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" '
        f'opacity="0.95" transform="scale(1.15)"/></g>'
    )


def _render_labels_svg(
    placement: LabelPlacementResult,
    tokens: StyleTokens,
) -> str:
    """Render placed labels as SVG text elements.

    Uses bbox_rect halo mode: a rounded rect behind text instead of stroke,
    keeping text selectable in PDF.
    """
    svg = []
    use_bbox_halo = tokens.labels.label_halo_mode.value == "bbox_rect"

    for placed in placement.placed:
        c = placed.candidate
        x, y = placed.x, placed.y

        # Determine colour based on category
        if c.category == "street":
            colour = tokens.labels.street_colour
            halo = tokens.labels.street_halo
        elif c.category == "poi":
            colour = tokens.labels.poi_colour
            halo = tokens.labels.poi_halo
        elif c.category == "park":
            colour = tokens.labels.park_colour
            halo = tokens.text_stroke
        elif c.category == "water":
            colour = tokens.labels.water_colour
            halo = tokens.text_stroke
        else:
            colour = tokens.text_primary
            halo = tokens.text_stroke

        # Halo as bbox rect (selectable-safe)
        if use_bbox_halo and c.halo_width > 0:
            hw = c.halo_width
            bw = placed.bbox.padded_width + 4
            bh = placed.bbox.padded_height + 2
            svg.append(
                f'  <rect x="{x - hw}" y="{y - bh - hw}" '
                f'width="{bw + 2 * hw}" height="{bh + 2 * hw}" '
                f'rx="2" fill="{halo}" opacity="0.7"/>'
            )

        # Rotation
        transform = ""
        if c.rotation != 0:
            transform = f' transform="rotate({c.rotation},{x},{y})"'

        font_class = "map-serif" if "serif" in c.font_family.lower() or "playfair" in c.font_family.lower() else "map-sans"

        svg.append(
            f'  <text x="{x}" y="{y}" font-size="{c.font_size}" '
            f'fill="{colour}" '
            f'class="{font_class}" font-weight="{c.font_weight}" '
            f'letter-spacing="{c.letter_spacing}"'
            f'{transform}>'
            f'{_esc(c.text)}</text>'
        )

        # Leader line for pushed labels
        if placed.leader_line:
            lx1, ly1, lx2, ly2 = placed.leader_line
            svg.append(
                f'  <line x1="{lx1}" y1="{ly1}" x2="{lx2}" y2="{ly2}" '
                f'stroke="{colour}" stroke-width="0.5" opacity="0.4" '
                f'stroke-dasharray="2,2"/>'
            )

    if not svg:
        return ""
    return f'  <g class="labels">\n' + "\n".join(svg) + "\n  </g>"


def _render_poi_icons_svg(
    pois: list[SelectedPOI],
    proj: MercatorProjection,
    tokens: StyleTokens,
) -> str:
    svg = []
    for poi in pois:
        x, y = proj.project(poi.lat, poi.lon)
        svg.append(f'  {_render_poi_icon(x, y, poi.category, tokens.labels.poi_colour)}')

    if not svg:
        return ""
    return f'  <g class="poi-icons">\n' + "\n".join(svg) + "\n  </g>"


def _render_station_markers(
    stations: list[dict],
    proj: MercatorProjection,
    tokens: StyleTokens,
) -> str:
    svg = []
    edge_margin = 60
    min_x, max_x = edge_margin, proj.width_px - edge_margin
    min_y, max_y = edge_margin, proj.height_px - edge_margin
    roundel_colour = tokens.markers.station_roundel_colour

    for st in stations:
        lat = st.get("lat")
        lon = st.get("lon")
        if lat is None or lon is None:
            continue
        x, y = proj.project(lat, lon)
        name = st.get("display_name", st.get("name", ""))
        time_str = st.get("time", "")
        is_walking = "walk" in time_str.lower()
        in_view = (0 <= x <= proj.width_px and 0 <= y <= proj.height_px)

        if not in_view and not is_walking:
            continue

        if not in_view and is_walking:
            x = max(min_x, min(max_x, x))
            y = max(min_y, min(max_y, y))

        svg.append(f'  <g transform="translate({x},{y})">')

        if is_walking:
            anchor = "start" if x <= min_x + 10 else ("end" if x >= max_x - 10 else "middle")
            roundel_offset = -16 if anchor == "end" else (16 if anchor == "start" else 0)
            text_x = roundel_offset + (8 if anchor == "start" else (-8 if anchor == "end" else 0))

            svg.append(
                f'    <circle cx="{roundel_offset}" cy="0" r="6" '
                f'fill="white" stroke="{roundel_colour}" stroke-width="2"/>'
            )
            svg.append(
                f'    <rect x="{roundel_offset - 7.5}" y="-1.5" '
                f'width="15" height="3" rx="1" fill="{roundel_colour}"/>'
            )
            svg.append(
                f'    <text x="{text_x}" y="-14" font-size="20" '
                f'fill="{tokens.markers.building_label_colour}" '
                f'class="map-serif" font-weight="700" '
                f'text-anchor="{anchor}" letter-spacing="3">'
                f'{_esc(name.upper())}</text>'
            )
            if time_str:
                svg.append(
                    f'    <text x="{text_x}" y="20" font-size="9" '
                    f'fill="{tokens.text_secondary}" '
                    f'class="map-sans" font-weight="300" font-style="italic" '
                    f'text-anchor="{anchor}">{_esc(time_str)}</text>'
                )
        else:
            r = 10
            svg.append(
                f'    <circle r="{r}" fill="white" stroke="{roundel_colour}" stroke-width="3.0"/>'
            )
            bar_w = r * 2.5
            svg.append(
                f'    <rect x="{-bar_w / 2}" y="-2.5" width="{bar_w}" height="5" '
                f'rx="1.5" fill="{roundel_colour}"/>'
            )
            svg.append(
                f'    <text x="0" y="{-r - 8}" font-size="13" fill="{tokens.markers.building_label_colour}" '
                f'class="map-serif" font-weight="700" '
                f'text-anchor="middle" letter-spacing="2">'
                f'{_esc(name.upper())}</text>'
            )
            if time_str:
                svg.append(
                    f'    <text x="0" y="{-r - 22}" font-size="8" '
                    f'fill="{tokens.text_secondary}" '
                    f'class="map-sans" font-weight="300" '
                    f'text-anchor="middle">{_esc(time_str)}</text>'
                )

        svg.append('  </g>')

    if not svg:
        return ""
    return f'  <g class="stations">\n' + "\n".join(svg) + "\n  </g>"


def _render_building_marker(
    lat: float,
    lon: float,
    name: str,
    proj: MercatorProjection,
    tokens: StyleTokens,
) -> str:
    x, y = proj.project(lat, lon)
    primary = tokens.markers.building_fill
    primary_dark = tokens.markers.building_stroke
    label_colour = tokens.markers.building_label_colour
    font_size = tokens.markers.building_label_font_size
    label_text = name.upper()

    svg = [f'  <g transform="translate({x},{y})">']
    svg.append(
        f'    <polygon points="0,-24 16,0 0,24 -16,0" '
        f'fill="{label_colour}" stroke="{primary_dark}" stroke-width="2.5"/>'
    )
    svg.append(f'    <circle r="5" fill="{primary}"/>')
    svg.append(
        f'    <text x="0" y="-42" font-size="{font_size}" fill="{label_colour}" '
        f'class="map-serif" font-weight="700" '
        f'text-anchor="middle" letter-spacing="3">{_esc(label_text)}</text>'
    )
    svg.append('  </g>')
    return f'  <g class="building-marker">\n' + "\n".join(svg) + "\n  </g>"


# ──────────────────────────────────────────────
#  Main Render Function
# ──────────────────────────────────────────────

def render_svg(
    features: SelectedFeatures,
    center_lat: float,
    center_lon: float,
    width: int,
    height: int,
    radius_m: int,
    tokens: StyleTokens,
    building_name: str = "",
    stations: list[dict[str, Any]] | None = None,
    debug: bool = False,
) -> tuple[str, LabelMetrics]:
    """Render a complete SVG map with multi-pass road rendering.

    Returns (svg_string, label_metrics).
    """
    proj = MercatorProjection(center_lat, center_lon, width, height, radius_m)
    bx, by = proj.project(center_lat, center_lon)

    # Process roads
    processed_roads = []
    for road in features.roads:
        processed = _process_road(road, proj)
        if processed:
            processed_roads.append(processed)

    # Build label candidates
    label_candidates: list[LabelCandidate] = []
    building_reserved = [(bx - 50, by - 60, bx + 50, by + 60)]

    # Street labels
    best_by_name: dict[str, dict] = {}
    for road in processed_roads:
        name = road["name"]
        if not name or road["px_len"] < 120:
            continue
        key = name.upper()
        if key not in best_by_name or road["px_len"] > best_by_name[key]["px_len"]:
            best_by_name[key] = road

    sorted_street_labels = sorted(best_by_name.items(), key=lambda kv: -kv[1]["px_len"])
    max_street_labels = 4  # configurable via content.street_label_max
    for i, (name_upper, road) in enumerate(sorted_street_labels[:max_street_labels]):
        mx, my = _way_midpoint(road["px_points"])
        angle = _way_angle(road["px_points"])

        dist_to_bld = math.sqrt((mx - bx) ** 2 + (my - by) ** 2)
        if dist_to_bld < 55:
            continue

        label_candidates.append(LabelCandidate(
            text=name_upper,
            anchor_x=mx,
            anchor_y=my - 5,
            font_family=tokens.font_families.get("sans", "Jost"),
            font_weight=tokens.labels.street_font_weight,
            font_size=tokens.labels.street_font_size,
            letter_spacing=tokens.labels.street_letter_spacing,
            halo_width=3.0,
            priority=20 + i,
            category="street",
            rotation=angle,
        ))

    # POI labels
    poi_anchors: list[tuple[float, float]] = []
    for poi in features.pois:
        x, y = proj.project(poi.lat, poi.lon)

        if math.sqrt((x - bx) ** 2 + (y - by) ** 2) < 45:
            continue

        if any(math.sqrt((x - px) ** 2 + (y - py) ** 2) < 18 for px, py in poi_anchors):
            continue

        poi_anchors.append((x, y))
        label_candidates.append(LabelCandidate(
            text=poi.name,
            anchor_x=x,
            anchor_y=y,
            font_family=tokens.font_families.get("sans", "Jost"),
            font_weight=tokens.labels.poi_font_weight,
            font_size=tokens.labels.poi_font_size,
            halo_width=3.0,
            priority=poi.priority + 30,
            is_critical=poi.is_critical,
            category="poi",
        ))

    # Park labels
    for park in features.parks:
        name = park.properties.get("name", "")
        if not name:
            continue
        cx, cy = _polygon_centroid(park.coords, proj)
        label_candidates.append(LabelCandidate(
            text=name.upper(),
            anchor_x=cx,
            anchor_y=cy,
            font_family=tokens.font_families.get("sans", "Jost"),
            font_weight=600,
            font_size=9.0,
            letter_spacing=2.0,
            halo_width=2.0,
            priority=25,
            category="park",
        ))

    # Water labels
    for ww in features.water:
        name = ww.properties.get("name", "")
        if not name:
            continue
        raw_pts = [proj.project(c[0], c[1]) for c in ww.coords]
        if len(raw_pts) < 2:
            continue
        mx, my = _way_midpoint(raw_pts)
        angle = _way_angle(raw_pts)
        label_candidates.append(LabelCandidate(
            text=name,
            anchor_x=mx,
            anchor_y=my - 10,
            font_family=tokens.font_families.get("sans", "Jost"),
            font_weight=500,
            font_size=9.0,
            halo_width=0,
            priority=22,
            category="water",
            rotation=angle,
        ))

    # Run label engine
    placement = place_labels(
        label_candidates, float(width), float(height),
        reserved_rects=building_reserved,
    )

    # Assemble SVG layers
    layers = []

    # Font definitions
    serif_font = tokens.font_families.get("serif", "Playfair Display")
    sans_font = tokens.font_families.get("sans", "Jost")
    layers.append(
        '  <defs><style>\n'
        f'    .map-serif {{ font-family: "{serif_font}", "Georgia", serif; }}\n'
        f'    .map-sans  {{ font-family: "{sans_font}", "Segoe UI", sans-serif; }}\n'
        '  </style></defs>'
    )

    # Layer 1: Neighbourhood labels (behind everything)
    nb_svg = _render_neighbourhoods(
        features.neighbourhoods, proj, tokens,
        center_lat, center_lon,
    )
    if nb_svg:
        layers.append(nb_svg)

    # Layer 2: Buildings
    bld_svg = _render_buildings(features.buildings, proj, tokens)
    if bld_svg:
        layers.append(bld_svg)

    # Layer 3: Parks
    park_svg = _render_parks(features.parks, proj, tokens)
    if park_svg:
        layers.append(park_svg)

    # Layer 4: Waterways
    water_svg = _render_waterways(features.water, proj, tokens)
    if water_svg:
        layers.append(water_svg)

    # Layer 5: Roads (4-pass order)
    roads_svg = _render_road_passes(processed_roads, tokens)
    if roads_svg:
        layers.append(roads_svg)

    # Layer 6: Labels (all types via label engine)
    labels_svg = _render_labels_svg(placement, tokens)
    if labels_svg:
        layers.append(labels_svg)

    # Layer 7: POI icons
    poi_icons_svg = _render_poi_icons_svg(features.pois, proj, tokens)
    if poi_icons_svg:
        layers.append(poi_icons_svg)

    # Layer 8: Station markers
    if stations:
        station_svg = _render_station_markers(stations, proj, tokens)
        if station_svg:
            layers.append(station_svg)

    # Layer 9: Building marker (always on top)
    if building_name:
        bld_marker = _render_building_marker(
            center_lat, center_lon, building_name, proj, tokens,
        )
        layers.append(bld_marker)

    # Assemble canonical SVG
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="100%" height="100%" '
        f'viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet">\n'
        + "\n".join(layers)
        + "\n</svg>"
    )

    # Build label metrics
    metrics = LabelMetrics(
        placed_labels=placement.placed_count,
        pushed_labels=placement.pushed_count,
        dropped_labels=placement.dropped_count,
        dropped_reason_counts=placement.dropped_reason_counts,
        critical_label_drops=placement.critical_drops,
    )

    return svg, metrics


def compute_svg_hash(svg_content: str) -> str:
    """Compute deterministic hash of SVG content."""
    return hashlib.sha256(svg_content.encode("utf-8")).hexdigest()[:16]
