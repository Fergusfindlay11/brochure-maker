#!/usr/bin/env python3
"""Ingest local map datasets into map-v1 DuckDB store.

Examples:
  python scripts/ingest_map_v1_duckdb.py \
    --db ./projects/map_v1_data.duckdb \
    --overture-parquet "/data/overture/places/*.parquet" \
    --release-id 2026-02-18.0

  python scripts/ingest_map_v1_duckdb.py \
    --db ./projects/map_v1_data.duckdb \
    --basemap-geojson ./data/basemap_features.geojson \
    --release-id local-basemap-v1
"""

from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path

from brochure_maker.map_v1.duckdb_store import OvertureDuckDBStore


def _iter_coords(geometry: dict):
    gtype = (geometry.get("type") or "").lower()
    coords = geometry.get("coordinates")

    if gtype == "point":
        if len(coords) >= 2:
            yield (float(coords[0]), float(coords[1]))
    elif gtype in ("linestring", "multipoint"):
        for pt in coords:
            if len(pt) >= 2:
                yield (float(pt[0]), float(pt[1]))
    elif gtype in ("polygon", "multilinestring"):
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


def _bbox_from_geometry(geometry: dict) -> tuple[float, float, float, float]:
    points = list(_iter_coords(geometry))
    if not points:
        raise ValueError("geometry has no coordinates")
    xs = [pt[0] for pt in points]
    ys = [pt[1] for pt in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _normalize_geojson_geometry(geometry: dict) -> tuple[str, list]:
    gtype = (geometry.get("type") or "").lower()
    coords = geometry.get("coordinates")

    if gtype == "linestring":
        return ("line", coords)
    if gtype == "multilinestring":
        # Flatten for deterministic single-path storage.
        merged = []
        for line in coords:
            merged.extend(line)
        return ("line", merged)
    if gtype == "polygon":
        return ("area", coords)
    if gtype == "multipolygon":
        # Flatten first polygon as outer shape for lightweight runtime.
        return ("area", coords[0] if coords else [])

    raise ValueError(f"unsupported geometry type '{geometry.get('type')}'")


def ingest_overture_places(store: OvertureDuckDBStore, parquet_glob: str, release_id: str) -> int:
    conn = store._connect()  # noqa: SLF001 - script-level usage
    has_geom = store._table_has_column("pois", "geom")  # noqa: SLF001 - script-level usage
    geom_insert_cols = ", geom" if has_geom else ""
    geom_insert_expr = ", ST_Point((CAST(bbox.xmin AS DOUBLE) + CAST(bbox.xmax AS DOUBLE)) / 2.0, (CAST(bbox.ymin AS DOUBLE) + CAST(bbox.ymax AS DOUBLE)) / 2.0)" if has_geom else ""

    # First attempt: overture places schema (struct columns).
    try:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO pois (
                id, name, category, source, license, release_id,
                lon, lat, minx, miny, maxx, maxy, priority
                {geom_insert_cols}
            )
            SELECT
                CAST(id AS VARCHAR) AS id,
                COALESCE(CAST(names.primary AS VARCHAR), CAST(name AS VARCHAR), 'Unnamed') AS name,
                LOWER(COALESCE(CAST(categories.primary AS VARCHAR), CAST(class AS VARCHAR), CAST(subtype AS VARCHAR), 'other')) AS category,
                'overture_places' AS source,
                COALESCE(CAST(license AS VARCHAR), 'mixed') AS license,
                ? AS release_id,
                (CAST(bbox.xmin AS DOUBLE) + CAST(bbox.xmax AS DOUBLE)) / 2.0 AS lon,
                (CAST(bbox.ymin AS DOUBLE) + CAST(bbox.ymax AS DOUBLE)) / 2.0 AS lat,
                CAST(bbox.xmin AS DOUBLE) AS minx,
                CAST(bbox.ymin AS DOUBLE) AS miny,
                CAST(bbox.xmax AS DOUBLE) AS maxx,
                CAST(bbox.ymax AS DOUBLE) AS maxy,
                COALESCE(CAST(confidence AS DOUBLE), CAST(rank AS DOUBLE), 0.0) AS priority
                {geom_insert_expr}
            FROM read_parquet(?)
            """,
            [release_id, parquet_glob],
        )
    except Exception:
        geom_insert_expr_fallback = ", ST_Point(CAST(lon AS DOUBLE), CAST(lat AS DOUBLE))" if has_geom else ""
        # Fallback schema for normalized parquet exports.
        conn.execute(
            f"""
            INSERT OR REPLACE INTO pois (
                id, name, category, source, license, release_id,
                lon, lat, minx, miny, maxx, maxy, priority
                {geom_insert_cols}
            )
            SELECT
                CAST(id AS VARCHAR),
                COALESCE(CAST(name AS VARCHAR), 'Unnamed'),
                LOWER(COALESCE(CAST(category AS VARCHAR), 'other')),
                COALESCE(CAST(source AS VARCHAR), 'overture_places'),
                COALESCE(CAST(license AS VARCHAR), 'mixed'),
                ?,
                CAST(lon AS DOUBLE),
                CAST(lat AS DOUBLE),
                CAST(minx AS DOUBLE),
                CAST(miny AS DOUBLE),
                CAST(maxx AS DOUBLE),
                CAST(maxy AS DOUBLE),
                COALESCE(CAST(priority AS DOUBLE), 0.0)
                {geom_insert_expr_fallback}
            FROM read_parquet(?)
            """,
            [release_id, parquet_glob],
        )

    return int(conn.execute("SELECT COUNT(*) FROM pois WHERE release_id = ?", [release_id]).fetchone()[0])


def ingest_basemap_geojson(store: OvertureDuckDBStore, geojson_path: Path, release_id: str) -> tuple[int, int]:
    payload = json.loads(geojson_path.read_text(encoding="utf-8"))
    features = payload.get("features", [])
    conn = store._connect()  # noqa: SLF001 - script-level usage

    linear_inserted = 0
    area_inserted = 0

    for idx, feat in enumerate(features):
        geom = feat.get("geometry")
        if not geom:
            continue
        props = feat.get("properties", {}) or {}

        try:
            shape_type, normalized = _normalize_geojson_geometry(geom)
        except ValueError:
            continue

        minx, miny, maxx, maxy = _bbox_from_geometry(geom)
        feature_id = str(props.get("id") or feat.get("id") or f"f_{idx}")
        name = str(props.get("name") or "")

        if shape_type == "line":
            layer = str(props.get("layer") or ("water" if props.get("waterway") else "road"))
            klass = str(props.get("class") or props.get("highway") or props.get("waterway") or "minor")
            conn.execute(
                """
                INSERT INTO basemap_linear (
                    id, layer, class, name, coords_json,
                    minx, miny, maxx, maxy, release_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    feature_id,
                    layer,
                    klass,
                    name,
                    json.dumps(normalized, separators=(",", ":")),
                    minx,
                    miny,
                    maxx,
                    maxy,
                    release_id,
                ],
            )
            linear_inserted += 1
            continue

        layer = str(props.get("layer") or ("park" if props.get("leisure") == "park" else "building"))
        klass = str(props.get("class") or props.get("leisure") or props.get("landuse") or layer)
        conn.execute(
            """
            INSERT INTO basemap_area (
                id, layer, class, name, rings_json,
                minx, miny, maxx, maxy, release_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                feature_id,
                layer,
                klass,
                name,
                json.dumps(normalized, separators=(",", ":")),
                minx,
                miny,
                maxx,
                maxy,
                release_id,
            ],
        )
        area_inserted += 1

    return linear_inserted, area_inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest map-v1 local indexed datasets into DuckDB")
    parser.add_argument("--db", required=True, help="DuckDB path")
    parser.add_argument("--release-id", required=True, help="Deterministic data release identifier")
    parser.add_argument("--overture-parquet", default="", help="Glob/path for Overture places parquet")
    parser.add_argument("--basemap-geojson", default="", help="GeoJSON basemap export path")
    parser.add_argument("--truncate", action="store_true", help="Truncate existing rows before ingest")
    parser.add_argument("--manifest-path", default="", help="Optional path for deterministic ingest manifest JSON")

    args = parser.parse_args()

    store = OvertureDuckDBStore(Path(args.db))
    store.ensure_schema()

    conn = store._connect()  # noqa: SLF001 - script-level usage
    if args.truncate:
        conn.execute("DELETE FROM pois")
        conn.execute("DELETE FROM basemap_linear")
        conn.execute("DELETE FROM basemap_area")

    poi_count = 0
    line_count = 0
    area_count = 0

    if args.overture_parquet:
        poi_count = ingest_overture_places(store, args.overture_parquet, args.release_id)
        print(f"Ingested/updated POIs: {poi_count}")

    if args.basemap_geojson:
        line_count, area_count = ingest_basemap_geojson(store, Path(args.basemap_geojson), args.release_id)
        print(f"Ingested basemap lines: {line_count}")
        print(f"Ingested basemap areas: {area_count}")

    manifest_payload = {
        "release_id": args.release_id,
        "db_path": str(Path(args.db).resolve()),
        "inputs": {
            "overture_parquet": args.overture_parquet,
            "basemap_geojson": args.basemap_geojson,
            "truncate": bool(args.truncate),
        },
        "counts": {
            "pois": int(poi_count),
            "basemap_linear": int(line_count),
            "basemap_area": int(area_count),
        },
    }
    checksum = hashlib.sha256(
        json.dumps(manifest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    manifest_payload["checksum"] = checksum

    manifest_path = Path(args.manifest_path) if args.manifest_path else Path(args.db).with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote ingest manifest: {manifest_path}")


if __name__ == "__main__":
    main()
