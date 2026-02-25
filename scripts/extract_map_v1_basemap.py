#!/usr/bin/env python3
"""Deterministic basemap extraction + stitching utility for map-v1.

This script supports two input paths:
1) pre-extracted GeoJSON (`--input-geojson`)
2) PMTiles via GDAL/OGR (`--pmtiles`) when `ogr2ogr` is available.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from brochure_maker.map_v1.errors import PMTilesReadError
from brochure_maker.map_v1.utils import bbox_from_center


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def select_zoom(lat: float, radius_m: float, width_px: int, height_px: int) -> int:
    target_mpp = (2.0 * radius_m) / max(1, min(width_px, height_px))
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    raw = round(math.log2((156543.03392804097 * cos_lat) / max(1e-6, target_mpp)))
    return _clamp(int(raw), 13, 16)


def _iter_coords(geometry: dict):
    gtype = (geometry.get("type") or "").lower()
    coords = geometry.get("coordinates")
    if not coords:
        return
    if gtype == "point":
        if len(coords) >= 2:
            yield (float(coords[0]), float(coords[1]))
    elif gtype in {"linestring", "multipoint"}:
        for pt in coords:
            if len(pt) >= 2:
                yield (float(pt[0]), float(pt[1]))
    elif gtype in {"polygon", "multilinestring"}:
        for ring in coords:
            for pt in ring:
                if len(pt) >= 2:
                    yield (float(pt[0]), float(pt[1]))
    elif gtype == "multipolygon":
        for poly in coords:
            for ring in poly:
                for pt in ring:
                    if len(pt) >= 2:
                        yield (float(pt[0]), float(pt[1]))


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    pts = list(_iter_coords(geometry))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _round_coord(value: float, tol: float) -> float:
    if tol <= 0:
        return value
    return round(round(value / tol) * tol, 10)


def _snap_line(coords: list[list[float]], tol: float) -> list[list[float]]:
    out = []
    for pt in coords:
        if len(pt) < 2:
            continue
        out.append([_round_coord(float(pt[0]), tol), _round_coord(float(pt[1]), tol)])
    return out


def _rdp(points: list[list[float]], epsilon: float) -> list[list[float]]:
    if len(points) < 3:
        return points
    x1, y1 = points[0]
    x2, y2 = points[-1]

    max_dist = -1.0
    max_idx = 0
    for i in range(1, len(points) - 1):
        x0, y0 = points[i]
        if x1 == x2 and y1 == y2:
            dist = math.hypot(x0 - x1, y0 - y1)
        else:
            t = ((x0 - x1) * (x2 - x1) + (y0 - y1) * (y2 - y1)) / ((x2 - x1) ** 2 + (y2 - y1) ** 2)
            t = max(0.0, min(1.0, t))
            px = x1 + t * (x2 - x1)
            py = y1 + t * (y2 - y1)
            dist = math.hypot(x0 - px, y0 - py)
        if dist > max_dist:
            max_dist = dist
            max_idx = i

    if max_dist <= epsilon:
        return [points[0], points[-1]]

    left = _rdp(points[: max_idx + 1], epsilon)
    right = _rdp(points[max_idx:], epsilon)
    return left[:-1] + right


def _coord_dist(a: list[float], b: list[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _merge_lines(lines: list[list[list[float]]], tol: float) -> list[list[list[float]]]:
    pending = [line for line in lines if len(line) >= 2]
    merged: list[list[list[float]]] = []
    while pending:
        current = pending.pop(0)
        changed = True
        while changed:
            changed = False
            for i, other in enumerate(pending):
                if _coord_dist(current[-1], other[0]) <= tol:
                    current = current + other[1:]
                elif _coord_dist(current[-1], other[-1]) <= tol:
                    current = current + list(reversed(other[:-1]))
                elif _coord_dist(current[0], other[-1]) <= tol:
                    current = other[:-1] + current
                elif _coord_dist(current[0], other[0]) <= tol:
                    current = list(reversed(other[1:])) + current
                else:
                    continue
                pending.pop(i)
                changed = True
                break
        merged.append(current)
    return merged


def _load_geojson_from_pmtiles(pmtiles: str) -> dict:
    if not shutil.which("ogr2ogr"):
        raise PMTilesReadError("ogr2ogr is required for PMTiles extraction")

    with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        src = pmtiles if pmtiles.startswith("PMTiles:") else f"PMTiles:{pmtiles}"
        proc = subprocess.run(
            ["ogr2ogr", "-f", "GeoJSON", str(tmp_path), src],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise PMTilesReadError(
                "Failed extracting PMTiles with ogr2ogr",
                hint=(proc.stderr or "").strip() or "Verify PMTiles path and GDAL PMTiles support.",
            )
        return json.loads(tmp_path.read_text(encoding="utf-8"))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def process_features(
    features: list[dict],
    *,
    extent_bbox: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
) -> list[dict]:
    min_lon, min_lat, max_lon, max_lat = extent_bbox
    lon_per_px = (max_lon - min_lon) / max(1, width_px)
    lat_per_px = (max_lat - min_lat) / max(1, height_px)
    snap_tol = max(lon_per_px, lat_per_px) * 0.1
    simplify_tol = max(lon_per_px, lat_per_px) * (0.15 * 96.0 / 25.4)

    filtered: list[dict] = []
    for feature in features:
        geom = feature.get("geometry") or {}
        gtype = (geom.get("type") or "").lower()
        if gtype not in {"linestring", "polygon", "point"}:
            continue
        try:
            if not _bbox_intersects(_bbox(geom), extent_bbox):
                continue
        except Exception:
            continue
        filtered.append(feature)

    grouped_lines: dict[tuple[str, str, str, str], list[list[list[float]]]] = {}
    passthrough: list[dict] = []

    for feature in filtered:
        geom = feature.get("geometry") or {}
        props = feature.get("properties", {}) or {}
        if (geom.get("type") or "").lower() != "linestring":
            passthrough.append(feature)
            continue
        key = (
            str(props.get("class") or props.get("highway") or "").lower(),
            str(props.get("bridge") or "0"),
            str(props.get("tunnel") or "0"),
            str(props.get("name") or ""),
        )
        grouped_lines.setdefault(key, []).append(_snap_line(geom.get("coordinates", []), snap_tol))

    stitched: list[dict] = []
    for key, lines in grouped_lines.items():
        merged = _merge_lines(lines, snap_tol * 1.5)
        for idx, line in enumerate(merged):
            simple = _rdp(line, simplify_tol)
            if len(simple) < 2:
                continue
            cls, bridge, tunnel, name = key
            props = {
                "id": f"line_{cls}_{bridge}_{tunnel}_{idx}",
                "layer": "water" if cls in {"river", "canal", "stream"} else "road",
                "class": cls or "minor",
                "name": name,
                "bridge": bridge,
                "tunnel": tunnel,
            }
            stitched.append(
                {
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "LineString", "coordinates": simple},
                }
            )

    for feature in passthrough:
        geom = feature.get("geometry") or {}
        props = feature.get("properties", {}) or {}
        gtype = (geom.get("type") or "").lower()
        if gtype == "point":
            coords = geom.get("coordinates", [])
            if len(coords) >= 2:
                geom["coordinates"] = [_round_coord(coords[0], snap_tol), _round_coord(coords[1], snap_tol)]
        elif gtype == "polygon":
            rings = []
            for ring in geom.get("coordinates", []):
                snapped = _snap_line(ring, snap_tol)
                simple = _rdp(snapped, simplify_tol)
                if len(simple) >= 4:
                    rings.append(simple)
            geom["coordinates"] = rings

        stitched.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": geom,
            }
        )

    stitched.sort(
        key=lambda feat: (
            str(feat.get("properties", {}).get("layer", "")),
            str(feat.get("properties", {}).get("class", "")),
            str(feat.get("properties", {}).get("name", "")).lower(),
            str(feat.get("properties", {}).get("id", "")),
        )
    )
    return stitched


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and stitch map-v1 basemap features deterministically")
    parser.add_argument("--output", required=True, help="Output GeoJSON path")
    parser.add_argument("--input-geojson", default="", help="Pre-extracted GeoJSON features")
    parser.add_argument("--pmtiles", default="", help="PMTiles path/URL for extraction")
    parser.add_argument("--center-lat", type=float, required=True)
    parser.add_argument("--center-lon", type=float, required=True)
    parser.add_argument("--radius-m", type=float, required=True)
    parser.add_argument("--width-px", type=int, default=940)
    parser.add_argument("--height-px", type=int, default=750)
    args = parser.parse_args()

    if not args.input_geojson and not args.pmtiles:
        raise SystemExit("Provide --input-geojson or --pmtiles")

    z = select_zoom(args.center_lat, args.radius_m, args.width_px, args.height_px)
    bbox = bbox_from_center(args.center_lat, args.center_lon, args.radius_m)

    if args.input_geojson:
        payload = json.loads(Path(args.input_geojson).read_text(encoding="utf-8"))
    else:
        payload = _load_geojson_from_pmtiles(args.pmtiles)

    features = payload.get("features", [])
    processed = process_features(features, extent_bbox=bbox, width_px=args.width_px, height_px=args.height_px)

    output_payload = {
        "type": "FeatureCollection",
        "metadata": {
            "zoom": z,
            "bbox": bbox,
            "feature_count": len(processed),
        },
        "features": processed,
    }
    Path(args.output).write_text(json.dumps(output_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(processed)} features to {args.output} (z={z})")


if __name__ == "__main__":
    main()
