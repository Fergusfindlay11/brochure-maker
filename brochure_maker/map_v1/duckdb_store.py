"""DuckDB-backed local indexed store for map-v1."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .config import MAP_V1_1_ENABLE_STRICT, MAP_V1_POI_QUERY_TIMEOUT_MS
from .errors import MapDataUnavailableError, PoiQueryTimeoutError
from .models import AreaFeature, PoiFeature, RoadFeature

logger = logging.getLogger(__name__)


class OvertureDuckDBStore:
    """Query adapter around a local DuckDB file."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._duckdb = None
        self._conn = None
        self._spatial_enabled = False

    def _connect(self):
        if self._conn is not None:
            return self._conn

        try:
            import duckdb  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency error path
            raise MapDataUnavailableError("duckdb dependency missing; install map-v1 requirements") from exc

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._duckdb = duckdb
        self._conn = duckdb.connect(str(self.db_path))

        try:
            self._conn.execute("LOAD spatial")
            self._spatial_enabled = True
        except Exception:
            try:
                self._conn.execute("INSTALL spatial")
                self._conn.execute("LOAD spatial")
                self._spatial_enabled = True
            except Exception as err:
                self._spatial_enabled = False
                if MAP_V1_1_ENABLE_STRICT:
                    raise MapDataUnavailableError(
                        "duckdb spatial extension unavailable in strict mode",
                        hint="Install DuckDB spatial extension in the runtime image.",
                    ) from err
                logger.warning("duckdb spatial extension unavailable: %s", err)

        self.ensure_schema()
        return self._conn

    def _table_has_column(self, table_name: str, column_name: str) -> bool:
        conn = self._connect()
        rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        for row in rows:
            if len(row) >= 2 and str(row[1]) == column_name:
                return True
        return False

    def ensure_schema(self) -> None:
        conn = self._connect()

        geom_col = ", geom GEOMETRY" if self._spatial_enabled else ""
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS pois (
                id TEXT PRIMARY KEY,
                name TEXT,
                category TEXT,
                source TEXT,
                license TEXT,
                release_id TEXT,
                lon DOUBLE,
                lat DOUBLE,
                minx DOUBLE,
                miny DOUBLE,
                maxx DOUBLE,
                maxy DOUBLE,
                priority DOUBLE
                {geom_col}
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS basemap_linear (
                id TEXT,
                layer TEXT,
                class TEXT,
                name TEXT,
                coords_json TEXT,
                minx DOUBLE,
                miny DOUBLE,
                maxx DOUBLE,
                maxy DOUBLE,
                release_id TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS basemap_area (
                id TEXT,
                layer TEXT,
                class TEXT,
                name TEXT,
                rings_json TEXT,
                minx DOUBLE,
                miny DOUBLE,
                maxx DOUBLE,
                maxy DOUBLE,
                release_id TEXT
            )
            """
        )

        if self._spatial_enabled and not self._table_has_column("pois", "geom"):
            conn.execute("ALTER TABLE pois ADD COLUMN geom GEOMETRY")

        if self._spatial_enabled:
            conn.execute("UPDATE pois SET geom = ST_Point(lon, lat) WHERE geom IS NULL AND lon IS NOT NULL AND lat IS NOT NULL")

        # Deterministic query speedups.
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_pois_bbox ON pois(minx, maxx, miny, maxy)",
            "CREATE INDEX IF NOT EXISTS idx_pois_category ON pois(category)",
            "CREATE INDEX IF NOT EXISTS idx_pois_priority ON pois(priority)",
            "CREATE INDEX IF NOT EXISTS idx_line_bbox ON basemap_linear(minx, maxx, miny, maxy)",
            "CREATE INDEX IF NOT EXISTS idx_area_bbox ON basemap_area(minx, maxx, miny, maxy)",
        ):
            try:
                conn.execute(statement)
            except Exception:
                pass

        if self._spatial_enabled:
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pois_geom_rtree ON pois USING RTREE (geom)")
            except Exception:
                try:
                    conn.execute("CREATE INDEX idx_pois_geom_rtree ON pois USING RTREE (geom)")
                except Exception as err:
                    if MAP_V1_1_ENABLE_STRICT:
                        raise MapDataUnavailableError(
                            "failed creating RTREE index on pois.geom",
                            hint="Ensure DuckDB spatial RTREE support is available.",
                        ) from err
                    logger.warning("rtree index creation skipped: %s", err)

    def _row_count(self, table_name: str) -> int:
        conn = self._connect()
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
        except Exception:
            return 0

    def assert_ready(self) -> None:
        if self._row_count("pois") <= 0:
            raise MapDataUnavailableError("no POI rows in DuckDB store. Run ingestion first.")
        if self._row_count("basemap_linear") <= 0 and self._row_count("basemap_area") <= 0:
            raise MapDataUnavailableError("no basemap rows in DuckDB store. Ingest basemap features first.")

    @staticmethod
    def _category_priority_case(category_priority: dict[str, int] | None) -> tuple[str, list[Any]]:
        if not category_priority:
            return ("9999", [])

        parts = ["CASE LOWER(category)"]
        params: list[Any] = []
        for category, rank in sorted(category_priority.items(), key=lambda item: (item[1], item[0])):
            parts.append("WHEN ? THEN ?")
            params.extend([category.lower(), int(rank)])
        parts.append("ELSE 9999 END")
        return (" ".join(parts), params)

    def query_pois(
        self,
        bbox: tuple[float, float, float, float],
        categories: list[str],
        limit: int,
        *,
        category_priority: dict[str, int] | None = None,
        timeout_ms: int | None = None,
    ) -> list[PoiFeature]:
        conn = self._connect()
        west, south, east, north = bbox
        timeout_budget = timeout_ms if timeout_ms is not None else MAP_V1_POI_QUERY_TIMEOUT_MS

        where = [
            "minx <= ?",
            "maxx >= ?",
            "miny <= ?",
            "maxy >= ?",
        ]
        params: list[Any] = [east, west, north, south]

        if categories:
            placeholders = ",".join(["?"] * len(categories))
            where.append(f"LOWER(category) IN ({placeholders})")
            params.extend([c.lower() for c in categories])

        if self._spatial_enabled and self._table_has_column("pois", "geom"):
            where.append("ST_Intersects(geom, ST_MakeEnvelope(?, ?, ?, ?))")
            params.extend([west, south, east, north])

        category_priority_sql, category_priority_params = self._category_priority_case(category_priority)
        params = category_priority_params + params + [limit]

        start = time.perf_counter()
        rows = conn.execute(
            f"""
            SELECT
                id, name, category, lat, lon, priority, source, license,
                {category_priority_sql} AS category_priority
            FROM pois
            WHERE {' AND '.join(where)}
            ORDER BY category_priority ASC, priority DESC, id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms > timeout_budget:
            raise PoiQueryTimeoutError(
                f"POI query exceeded timeout ({elapsed_ms:.1f}ms > {timeout_budget}ms)",
                retryable=True,
            )

        return [
            PoiFeature(
                id=str(row[0]),
                name=str(row[1] or ""),
                category=str(row[2] or "other").lower(),
                lat=float(row[3]),
                lon=float(row[4]),
                priority=float(row[5] or 0.0),
                source=str(row[6] or ""),
                license=str(row[7] or ""),
            )
            for row in rows
        ]

    def query_roads_and_water(
        self,
        bbox: tuple[float, float, float, float],
    ) -> tuple[list[RoadFeature], list[RoadFeature]]:
        conn = self._connect()
        west, south, east, north = bbox

        rows = conn.execute(
            """
            SELECT id, layer, class, name, coords_json
            FROM basemap_linear
            WHERE minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ?
            ORDER BY layer, class, LOWER(name), id
            """,
            [east, west, north, south],
        ).fetchall()

        roads: list[RoadFeature] = []
        waterways: list[RoadFeature] = []
        for row in rows:
            layer = str(row[1] or "")
            coords = json.loads(str(row[4] or "[]"))
            feature = RoadFeature(
                id=str(row[0]),
                road_class=str(row[2] or "minor").lower(),
                name=str(row[3] or ""),
                coords=[(float(pt[1]), float(pt[0])) for pt in coords if len(pt) >= 2],
            )
            if layer == "water":
                waterways.append(feature)
            else:
                roads.append(feature)

        return roads, waterways

    def query_areas(
        self,
        bbox: tuple[float, float, float, float],
    ) -> tuple[list[AreaFeature], list[AreaFeature]]:
        conn = self._connect()
        west, south, east, north = bbox

        rows = conn.execute(
            """
            SELECT id, layer, class, name, rings_json
            FROM basemap_area
            WHERE minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ?
            ORDER BY layer, class, LOWER(name), id
            """,
            [east, west, north, south],
        ).fetchall()

        parks: list[AreaFeature] = []
        buildings: list[AreaFeature] = []
        for row in rows:
            layer = str(row[1] or "")
            rings = json.loads(str(row[4] or "[]"))
            feature = AreaFeature(
                id=str(row[0]),
                area_class=str(row[2] or "").lower() or layer,
                name=str(row[3] or ""),
                rings=[[(float(pt[1]), float(pt[0])) for pt in ring if len(pt) >= 2] for ring in rings],
            )
            if layer == "park":
                parks.append(feature)
            else:
                buildings.append(feature)

        return parks, buildings
