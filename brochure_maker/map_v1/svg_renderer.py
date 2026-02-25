"""SVG renderer for map-v1 output."""

from __future__ import annotations

import html
from typing import Any

from .models import StyleTokens
from .text_metrics import TextMeasureInput, measure_text, resolve_label_font, rounded_halo_rect
from .utils import project_web_mercator

_MAJOR_ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary", "major"}


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _line_path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    out = [f"M{points[0][0]:.2f},{points[0][1]:.2f}"]
    for x, y in points[1:]:
        out.append(f"L{x:.2f},{y:.2f}")
    return " ".join(out)


def _poly_points(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def _project_line(
    latlon_coords: list[tuple[float, float]],
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
) -> list[tuple[float, float]]:
    projected: list[tuple[float, float]] = []
    for lat, lon in latlon_coords:
        projected.append(project_web_mercator(lon, lat, bbox, width, height))
    return projected


def _project_ring(
    latlon_ring: list[tuple[float, float]],
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
) -> list[tuple[float, float]]:
    projected: list[tuple[float, float]] = []
    for lat, lon in latlon_ring:
        projected.append(project_web_mercator(lon, lat, bbox, width, height))
    return projected


def _road_widths(road_class: str) -> tuple[float, float]:
    cls = (road_class or "minor").lower()
    if cls in _MAJOR_ROAD_CLASSES:
        return (8.0, 5.0)
    return (5.2, 3.2)


def render_svg(
    *,
    width: int,
    height: int,
    bbox: tuple[float, float, float, float],
    style_tokens: StyleTokens,
    roads: list[dict[str, Any]],
    waterways: list[dict[str, Any]],
    parks: list[dict[str, Any]],
    buildings: list[dict[str, Any]],
    pois: list[dict[str, Any]],
    stations: list[dict[str, Any]],
    subject: dict[str, Any],
    labels: list[dict[str, Any]],
) -> str:
    pal = style_tokens.palette
    typo = style_tokens.typography

    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">',
        "<defs>",
        "<style>",
        f'.map-sans {{ font-family: "{_esc(typo.font_family_sans)}", sans-serif; }}',
        f'.map-serif {{ font-family: "{_esc(typo.font_family_serif)}", serif; }}',
        "</style>",
        "</defs>",
    ]

    # Areas first.
    if parks:
        svg.append('<g class="parks">')
        for park in parks:
            rings = park.get("rings", [])
            if not rings:
                continue
            outer = _project_ring(rings[0], bbox, width, height)
            svg.append(
                f'<polygon points="{_poly_points(outer)}" fill="{pal.park_fill}" fill-opacity="0.55" stroke="none" />'
            )
        svg.append("</g>")

    if waterways:
        svg.append('<g class="waterways">')
        for water in waterways:
            points = _project_line(water.get("coords", []), bbox, width, height)
            if len(points) < 2:
                continue
            sw = 6.0 if (water.get("road_class") or "") in {"river", "canal", "major"} else 4.0
            svg.append(
                f'<path d="{_line_path(points)}" fill="none" stroke="{pal.water_fill}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round" />'
            )
        svg.append("</g>")

    if buildings:
        svg.append('<g class="buildings">')
        for b in buildings:
            rings = b.get("rings", [])
            if not rings:
                continue
            outer = _project_ring(rings[0], bbox, width, height)
            svg.append(
                f'<polygon points="{_poly_points(outer)}" fill="{pal.building_fill}" fill-opacity="0.18" stroke="{pal.building_stroke}" stroke-opacity="0.22" stroke-width="0.6" />'
            )
        svg.append("</g>")

    # Roads in mandatory multi-pass ordering.
    minor = []
    major = []
    for road in roads:
        cls = (road.get("road_class") or "minor").lower()
        (major if cls in _MAJOR_ROAD_CLASSES else minor).append(road)

    def _draw_roads(layer_name: str, data: list[dict[str, Any]], casing: bool, major_roads: bool) -> None:
        svg.append(f'<g class="{layer_name}">')
        for road in data:
            pts = _project_line(road.get("coords", []), bbox, width, height)
            if len(pts) < 2:
                continue
            casing_w, fill_w = _road_widths(road.get("road_class") or "minor")
            stroke_w = casing_w if casing else fill_w
            if major_roads:
                stroke = pal.road_major_casing if casing else pal.road_major_fill
                opacity = 0.95 if casing else 0.92
            else:
                stroke = pal.road_minor_casing if casing else pal.road_minor_fill
                opacity = 0.82 if casing else 0.90
            svg.append(
                f'<path d="{_line_path(pts)}" fill="none" stroke="{stroke}" stroke-width="{stroke_w}" stroke-opacity="{opacity:.2f}" stroke-linecap="round" stroke-linejoin="round" />'
            )
        svg.append("</g>")

    # Pass 1: all minor casings
    _draw_roads("roads-minor-casing", minor, casing=True, major_roads=False)
    # Pass 2: all minor fills
    _draw_roads("roads-minor-fill", minor, casing=False, major_roads=False)
    # Pass 3: all major casings
    _draw_roads("roads-major-casing", major, casing=True, major_roads=True)
    # Pass 4: all major fills
    _draw_roads("roads-major-fill", major, casing=False, major_roads=True)

    if pois:
        svg.append('<g class="poi-icons">')
        for poi in pois:
            x, y = project_web_mercator(poi["lon"], poi["lat"], bbox, width, height)
            svg.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4.2" fill="{pal.poi_icon_fill}" stroke="{pal.poi_icon_stroke}" stroke-width="1.2" />'
            )
        svg.append("</g>")

    if stations:
        svg.append('<g class="station-icons">')
        for st in stations:
            if st.get("lat") is None or st.get("lon") is None:
                continue
            x, y = project_web_mercator(st["lon"], st["lat"], bbox, width, height)
            svg.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5.6" fill="{pal.station_fill}" stroke="{pal.station_stroke}" stroke-width="1.4" />'
            )
        svg.append("</g>")

    # Subject marker stays above all transport/poi symbols.
    sx, sy = project_web_mercator(subject["lon"], subject["lat"], bbox, width, height)
    svg.append('<g class="subject-marker">')
    svg.append(
        f'<polygon points="{sx:.2f},{sy - 14:.2f} {sx + 10:.2f},{sy:.2f} {sx:.2f},{sy + 14:.2f} {sx - 10:.2f},{sy:.2f}" fill="{pal.building_fill}" stroke="{pal.building_stroke}" stroke-width="1.8" />'
    )
    subject_name = str(subject.get("name", "")).strip()
    if subject_name:
        subj_y = sy - 20.0
        family, weight = resolve_label_font("building", typo.font_family_sans, typo.font_family_serif)
        subject_metrics = measure_text(
            TextMeasureInput(
                font_family=family,
                weight=weight,
                size_pt=14.0,
                tracking=1.0,
                text=subject_name,
            ),
            halo_width_pt=typo.halo_width_pt if style_tokens.rules.label_halo_mode == "bbox_rect" else 0.0,
        )
        if style_tokens.rules.label_halo_mode == "bbox_rect":
            hx, hy, hw, hh, hr = rounded_halo_rect(
                sx - (subject_metrics.width_px * 0.5),
                subj_y,
                subject_metrics,
                radius_px=max(2.0, subject_metrics.halo_pad_px),
            )
            svg.append(
                f'<rect x="{hx:.2f}" y="{hy:.2f}" width="{hw:.2f}" height="{hh:.2f}" rx="{hr:.2f}" ry="{hr:.2f}" fill="{pal.label_halo}" fill-opacity="0.95" />'
            )
        svg.append(
            f'<text x="{sx:.2f}" y="{subj_y:.2f}" class="map-serif" font-size="14.00" font-weight="{_esc(weight)}" fill="{pal.label_text}" text-anchor="middle" letter-spacing="1.00">{_esc(subject_name)}</text>'
        )
    svg.append("</g>")

    if labels:
        svg.append('<g class="labels">')
        for label in labels:
            if label.get("leader_to_x") is not None and label.get("leader_to_y") is not None:
                svg.append(
                    f'<line x1="{label["anchor_x"]:.2f}" y1="{label["anchor_y"]:.2f}" x2="{label["x"]:.2f}" y2="{label["y"] - 3.0:.2f}" stroke="{pal.label_text}" stroke-opacity="0.35" stroke-width="0.8" />'
                )
            size = float(label.get("font_size") or typo.label_size_pt)
            klass = str(label.get("label_class") or "").lower()
            family_name, weight = resolve_label_font(klass, typo.font_family_sans, typo.font_family_serif)
            family_class = "map-serif" if family_name == typo.font_family_serif else "map-sans"
            measured = measure_text(
                TextMeasureInput(
                    font_family=family_name,
                    weight=weight,
                    size_pt=size,
                    tracking=typo.tracking,
                    text=str(label["text"]),
                ),
                halo_width_pt=typo.halo_width_pt if style_tokens.rules.label_halo_mode == "bbox_rect" else 0.0,
            )
            if style_tokens.rules.label_halo_mode == "bbox_rect":
                hx, hy, hw, hh, hr = rounded_halo_rect(
                    float(label["x"]),
                    float(label["y"]),
                    measured,
                    radius_px=max(1.0, measured.halo_pad_px),
                )
                svg.append(
                    f'<rect x="{hx:.2f}" y="{hy:.2f}" width="{hw:.2f}" height="{hh:.2f}" rx="{hr:.2f}" ry="{hr:.2f}" fill="{pal.label_halo}" fill-opacity="0.92" />'
                )
            svg.append(
                f'<text x="{label["x"]:.2f}" y="{label["y"]:.2f}" class="{family_class}" font-size="{size:.2f}" font-weight="{_esc(weight)}" fill="{pal.label_text}" letter-spacing="{typo.tracking:.2f}">{_esc(label["text"])}</text>'
            )
        svg.append("</g>")

    svg.append("</svg>")
    return "\n".join(svg)
