"""Data Service — DuckDB Spatial local POI store + Overture ingestion.

Provides:
- Ingest pipeline: Overture Parquet -> DuckDB with RTREE index.
- Runtime queries: bbox + category + rank with deterministic ordering.
- Dataset versioning and manifest tracking.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import DatasetUnavailableError, POIQueryTimeoutError

logger = logging.getLogger(__name__)

_DB_DIR = Path(os.environ.get("MAP_DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "map_data")))
_DB_PATH = _DB_DIR / "pois.duckdb"

# Category taxonomy: Overture -> renderer category
CATEGORY_TAXONOMY: dict[str, str] = {
    "eat_and_drink": "restaurant",
    "restaurant": "restaurant",
    "cafe": "cafe",
    "coffee_shop": "cafe",
    "bar": "bar",
    "pub": "pub",
    "fast_food": "restaurant",
    "food_court": "restaurant",
    "pharmacy": "pharmacy",
    "hospital": "pharmacy",
    "cinema": "cinema",
    "theater": "cinema",
    "bank": "bank",
    "atm": "bank",
    "supermarket": "supermarket",
    "grocery": "supermarket",
    "convenience_store": "convenience",
    "convenience": "convenience",
    "gym": "fitness_centre",
    "fitness_center": "fitness_centre",
    "fitness_centre": "fitness_centre",
    "sports_centre": "sports_centre",
    "hotel": "hotel",
    "motel": "hotel",
    "hostel": "hotel",
    "museum": "museum",
    "art_gallery": "gallery",
    "gallery": "gallery",
    "park": "park",
    "garden": "park",
}

# Per-category priority (lower = higher priority).
CATEGORY_PRIORITY: dict[str, int] = {
    "restaurant": 3,
    "cafe": 4,
    "bar": 5,
    "pub": 5,
    "supermarket": 2,
    "convenience": 6,
    "fitness_centre": 7,
    "sports_centre": 7,
    "hotel": 4,
    "museum": 1,
    "gallery": 2,
    "pharmacy": 3,
    "cinema": 5,
    "bank": 6,
    "park": 2,
}


@dataclass
class POIRecord:
    """A normalised POI record from the local store."""

    overture_id: str
    name: str
    category: str
    lat: float
    lon: float
    source: str = "overture"
    release_id: str = ""
    rank: float = 0.0


@dataclass
class DatasetManifest:
    """Metadata about the currently loaded dataset."""

    release_id: str = ""
    record_count: int = 0
    checksum: str = ""
    ingested_at: str = ""
    categories: list[str] = field(default_factory=list)


def _get_db():
    """Lazy-import DuckDB and return a connection."""
    try:
        import duckdb
    except ImportError:
        raise DatasetUnavailableError(
            detail="DuckDB is not installed. Run: pip install duckdb"
        )
    if not _DB_PATH.exists():
        raise DatasetUnavailableError(
            detail=f"POI database not found at {_DB_PATH}. Run the ingestion pipeline first."
        )
    conn = duckdb.connect(str(_DB_PATH), read_only=True)
    conn.execute("INSTALL spatial; LOAD spatial;")
    return conn


# ──────────────────────────────────────────────
#  Ingestion Pipeline
# ──────────────────────────────────────────────

def ingest_overture_parquet(
    parquet_path: str | Path,
    release_id: str = "unknown",
) -> DatasetManifest:
    """Read Overture Parquet release into DuckDB with RTREE index.

    Creates the POI table, normalises categories, and builds spatial index.
    """
    try:
        import duckdb
    except ImportError:
        raise DatasetUnavailableError(
            detail="DuckDB is not installed. Run: pip install duckdb"
        )

    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        raise DatasetUnavailableError(
            detail=f"Parquet file not found: {parquet_path}"
        )

    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(_DB_PATH))

    try:
        conn.execute("INSTALL spatial; LOAD spatial;")

        # Create table from parquet
        conn.execute("DROP TABLE IF EXISTS pois;")
        conn.execute(f"""
            CREATE TABLE pois AS
            SELECT
                id AS overture_id,
                names.primary AS name,
                categories.main AS raw_category,
                ST_Y(geometry) AS lat,
                ST_X(geometry) AS lon,
                ST_GeomFromWKB(geometry) AS geom,
                sources[1].dataset AS source,
                '{release_id}' AS overture_release_id
            FROM read_parquet('{parquet_path}')
            WHERE names.primary IS NOT NULL
              AND names.primary != ''
        """)

        # Add normalised category column
        conn.execute("ALTER TABLE pois ADD COLUMN category VARCHAR;")
        conn.execute("ALTER TABLE pois ADD COLUMN category_priority INTEGER DEFAULT 99;")
        conn.execute("ALTER TABLE pois ADD COLUMN rank DOUBLE DEFAULT 0.0;")

        # Derive bbox columns for fast prefiltering
        conn.execute("ALTER TABLE pois ADD COLUMN minx DOUBLE;")
        conn.execute("ALTER TABLE pois ADD COLUMN miny DOUBLE;")
        conn.execute("ALTER TABLE pois ADD COLUMN maxx DOUBLE;")
        conn.execute("ALTER TABLE pois ADD COLUMN maxy DOUBLE;")
        conn.execute("""
            UPDATE pois SET
                minx = lon,
                miny = lat,
                maxx = lon,
                maxy = lat
        """)

        # Normalise categories
        for raw, normalised in CATEGORY_TAXONOMY.items():
            priority = CATEGORY_PRIORITY.get(normalised, 99)
            conn.execute(
                f"UPDATE pois SET category = ?, category_priority = ? "
                f"WHERE LOWER(raw_category) = ?",
                [normalised, priority, raw.lower()],
            )

        # Build RTREE spatial index
        try:
            conn.execute("CREATE INDEX pois_geom_rtree ON pois USING RTREE (geom);")
            logger.info("RTREE spatial index created.")
        except Exception as e:
            logger.warning("Could not create RTREE index (DuckDB version may not support it): %s", e)
            # Build traditional index on bbox columns instead
            conn.execute("CREATE INDEX pois_bbox_idx ON pois (minx, miny, maxx, maxy);")
            logger.info("Fallback bbox index created.")

        # Build index on category + priority for fast filtering
        conn.execute("CREATE INDEX pois_cat_idx ON pois (category, category_priority);")

        count = conn.execute("SELECT COUNT(*) FROM pois").fetchone()[0]

        # Compute checksum
        checksum_data = conn.execute(
            "SELECT MD5(STRING_AGG(overture_id, ',' ORDER BY overture_id)) FROM pois"
        ).fetchone()[0]

        categories = [
            row[0] for row in
            conn.execute("SELECT DISTINCT category FROM pois WHERE category IS NOT NULL ORDER BY category").fetchall()
        ]

        manifest = DatasetManifest(
            release_id=release_id,
            record_count=count,
            checksum=checksum_data or "",
            ingested_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            categories=categories,
        )

        # Persist manifest
        manifest_path = _DB_DIR / "manifest.json"
        manifest_path.write_text(json.dumps({
            "release_id": manifest.release_id,
            "record_count": manifest.record_count,
            "checksum": manifest.checksum,
            "ingested_at": manifest.ingested_at,
            "categories": manifest.categories,
        }, indent=2))

        logger.info(
            "Ingested %d POIs from %s (release: %s)",
            count, parquet_path.name, release_id,
        )
        return manifest

    finally:
        conn.close()


# ──────────────────────────────────────────────
#  Runtime Queries
# ──────────────────────────────────────────────

def query_pois_bbox(
    south: float,
    west: float,
    north: float,
    east: float,
    categories: list[str] | None = None,
    max_results: int = 100,
    timeout_ms: int = 5000,
) -> list[POIRecord]:
    """Query POIs within a bounding box with deterministic ordering.

    Uses bbox prefilter columns + spatial intersects.
    Results are always ORDER BY category_priority ASC, rank DESC, overture_id ASC
    for determinism.
    """
    conn = _get_db()

    try:
        # Build envelope literal
        envelope_wkt = (
            f"POLYGON(({west} {south}, {east} {south}, "
            f"{east} {north}, {west} {north}, {west} {south}))"
        )

        where_parts = [
            f"minx >= {west} AND maxx <= {east}",
            f"miny >= {south} AND maxy <= {north}",
            f"ST_Intersects(geom, ST_GeomFromText('{envelope_wkt}'))",
            "category IS NOT NULL",
        ]

        if categories:
            cat_list = ", ".join(f"'{c}'" for c in categories)
            where_parts.append(f"category IN ({cat_list})")

        where_clause = " AND ".join(where_parts)

        query = f"""
            SELECT overture_id, name, category, lat, lon, source, overture_release_id, rank
            FROM pois
            WHERE {where_clause}
            ORDER BY category_priority ASC, rank DESC, overture_id ASC
            LIMIT {max_results}
        """

        start = time.monotonic()
        rows = conn.execute(query).fetchall()
        elapsed_ms = (time.monotonic() - start) * 1000

        if elapsed_ms > timeout_ms:
            raise POIQueryTimeoutError(
                detail=f"POI query took {elapsed_ms:.0f}ms (budget: {timeout_ms}ms)",
                context={"elapsed_ms": elapsed_ms, "result_count": len(rows)},
            )

        return [
            POIRecord(
                overture_id=row[0],
                name=row[1],
                category=row[2],
                lat=row[3],
                lon=row[4],
                source=row[5] or "overture",
                release_id=row[6] or "",
                rank=row[7] or 0.0,
            )
            for row in rows
        ]

    finally:
        conn.close()


def get_dataset_manifest() -> DatasetManifest | None:
    """Load the current dataset manifest, or None if not ingested."""
    manifest_path = _DB_DIR / "manifest.json"
    if not manifest_path.exists():
        return None
    data = json.loads(manifest_path.read_text())
    return DatasetManifest(**data)


def is_dataset_available() -> bool:
    """Check if the POI dataset is initialised and queryable."""
    return _DB_PATH.exists()
