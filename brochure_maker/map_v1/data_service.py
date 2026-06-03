"""Data service for map-v1 (DuckDB local indexed store)."""

from __future__ import annotations

from dataclasses import dataclass

from .duckdb_store import OvertureDuckDBStore
from .errors import MapDataUnavailableError
from .models import AreaFeature, PoiFeature, RoadFeature
from .utils import bbox_from_center


@dataclass
class MapDataBundle:
    bbox: tuple[float, float, float, float]
    roads: list[RoadFeature]
    waterways: list[RoadFeature]
    parks: list[AreaFeature]
    buildings: list[AreaFeature]
    pois: list[PoiFeature]


class LocalDataService:
    """Runtime adapter that fetches deterministic feature sets from DuckDB."""

    def __init__(self, store: OvertureDuckDBStore):
        self.store = store

    def fetch(
        self,
        *,
        center_lat: float,
        center_lon: float,
        radius_m: int,
        poi_categories: list[str],
        category_priority: dict[str, int] | None = None,
    ) -> MapDataBundle:
        self.store.assert_ready()

        bbox = bbox_from_center(center_lat, center_lon, radius_m)

        roads, waterways = self.store.query_roads_and_water(bbox)
        parks, buildings = self.store.query_areas(bbox)
        pois = self.store.query_pois(
            bbox,
            poi_categories,
            limit=300,
            category_priority=category_priority,
        )

        if not roads and not parks and not waterways and not buildings:
            raise MapDataUnavailableError(
                "MAP_DATA_UNAVAILABLE: no basemap features in requested extent"
            )

        return MapDataBundle(
            bbox=bbox,
            roads=roads,
            waterways=waterways,
            parks=parks,
            buildings=buildings,
            pois=pois,
        )

    @staticmethod
    def map_station_points(
        stations: list[dict],
        pois: list[PoiFeature],
    ) -> list[dict]:
        """Resolve station points without calling external APIs.

        Priority:
        1) Explicit lat/lon already in station payload.
        2) Name match against local POIs in transit/station categories.
        """
        by_name = {
            p.name.strip().lower(): p
            for p in pois
            if p.name and p.category in {"transit", "station", "train_station", "subway_station"}
        }

        resolved: list[dict] = []
        for station in stations:
            name = str(station.get("name", "")).strip()
            if not name:
                continue

            lat = station.get("lat")
            lon = station.get("lon")
            if lat is not None and lon is not None:
                resolved.append({
                    "name": name,
                    "time": station.get("time", ""),
                    "lat": float(lat),
                    "lon": float(lon),
                })
                continue

            poi = by_name.get(name.lower())
            if poi:
                resolved.append({
                    "name": name,
                    "time": station.get("time", ""),
                    "lat": poi.lat,
                    "lon": poi.lon,
                })

        return resolved
