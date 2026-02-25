"""Orchestration service for map-v1 style + render workflows."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    MAP_V1_CRITICAL_LABEL_CLASSES,
    MAP_V1_DATASET_VERSION,
    MAP_V1_DEFAULT_RADIUS_M,
    MAP_V1_DUCKDB_PATH,
    MAP_V1_RENDERER_VERSION,
)
from .data_service import LocalDataService
from .determinism import canonical_svg, pdf_structural_hash, svg_hash
from .duckdb_store import OvertureDuckDBStore
from .errors import (
    DeterminismViolationError,
    ExportError,
    MapDataUnavailableError,
    MapV1Error,
    StyleValidationError,
)
from .feature_selection import clip_area_features, filter_roads_for_extent, select_pois, simplify_roads
from .label_engine import LabelCandidate, place_labels
from .models import (
    ContentSpec,
    MapRenderRequestV1,
    MapRenderResponseV1,
    MapStyleGenerateRequestV1,
    MapStyleGenerateResponseV1,
    RenderArtifacts,
    RenderMetadata,
    RenderWarning,
    StationInput,
)
from .pdf_export import svg_to_pdf_bytes
from .style_director import generate_style_tokens
from .svg_renderer import render_svg
from .text_metrics import text_metrics_backend_name
from .utils import project_web_mercator, stable_hash
from .vector_purity import assert_vector_purity

logger = logging.getLogger(__name__)


class MapV1Service:
    """High-level service API used by FastAPI routes."""

    def __init__(self, projects_dir: Path, duckdb_path: Path | None = None):
        self.projects_dir = Path(projects_dir)
        self.store = OvertureDuckDBStore(duckdb_path or MAP_V1_DUCKDB_PATH)
        self.data = LocalDataService(self.store)

    def generate_style(self, payload: MapStyleGenerateRequestV1) -> MapStyleGenerateResponseV1:
        return generate_style_tokens(payload)

    def _project_dir_for(self, project_id: str | None) -> tuple[str, Path]:
        pid = (project_id or "_adhoc").strip()
        path = self.projects_dir / pid
        path.mkdir(parents=True, exist_ok=True)
        return pid, path

    def _load_analysis(self, project_id: str) -> dict:
        analysis_path = self.projects_dir / project_id / "analysis.json"
        if not analysis_path.exists():
            raise StyleValidationError(f"analysis.json not found for project '{project_id}'")
        with open(analysis_path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _extract_stations_from_analysis(analysis: dict) -> list[dict]:
        for slide in analysis.get("slides", []):
            if slide.get("type") == "travel_map":
                return list(slide.get("content", {}).get("stations", []))
        return []

    def _hydrate_render_request(self, request: MapRenderRequestV1) -> tuple[float, float, ContentSpec, list[dict], str]:
        """Resolve center/content from payload or project analysis."""
        if request.project_id:
            analysis = self._load_analysis(request.project_id)
            lat = analysis.get("lat")
            lon = analysis.get("lng")
            if lat is None or lon is None:
                raise StyleValidationError("project analysis missing lat/lng; run geocode before map-v1 render")

            building_name = analysis.get("brochure_name") or "Subject Property"
            stations = self._extract_stations_from_analysis(analysis)
            categories = list(request.content.poi_categories) if request.content else []
            if not categories:
                categories = sorted(request.style_tokens.rules.max_pois_per_category.keys())

            normalized_stations = []
            for station in stations:
                name = str(station.get("name", "")).strip()
                if not name:
                    continue
                normalized_stations.append(
                    StationInput(
                        name=name,
                        time=str(station.get("time", "")).strip(),
                        lat=station.get("lat"),
                        lon=station.get("lon"),
                    )
                )

            content = ContentSpec(
                building_name=building_name,
                stations=normalized_stations,
                poi_categories=categories,
            )
            return float(lat), float(lon), content, stations, analysis.get("location", "")

        if request.center is None or request.content is None:
            raise StyleValidationError("render request missing center/content")

        station_payload = [s.model_dump(mode="json") for s in request.content.stations]
        return request.center.lat, request.center.lon, request.content, station_payload, ""

    @staticmethod
    def _category_priority(request: MapRenderRequestV1) -> dict[str, int]:
        order = request.style_tokens.rules.label_priority
        if not order:
            return {}
        return {name.lower(): idx for idx, name in enumerate(order)}

    def render(self, request: MapRenderRequestV1) -> MapRenderResponseV1:
        start = time.perf_counter()
        stage_ms: dict[str, float] = {}

        t0 = time.perf_counter()
        center_lat, center_lon, content, stations_raw, _location = self._hydrate_render_request(request)
        stage_ms["hydrate"] = round((time.perf_counter() - t0) * 1000.0, 2)

        radius_m = request.extent.radius_m or MAP_V1_DEFAULT_RADIUS_M
        category_priority = self._category_priority(request)

        t0 = time.perf_counter()
        data = self.data.fetch(
            center_lat=center_lat,
            center_lon=center_lon,
            radius_m=radius_m,
            poi_categories=[c.lower() for c in content.poi_categories],
            category_priority=category_priority,
        )
        stage_ms["data_fetch"] = round((time.perf_counter() - t0) * 1000.0, 2)

        t0 = time.perf_counter()
        roads = filter_roads_for_extent(data.roads, center_lat, center_lon)
        roads = simplify_roads(roads)
        parks = clip_area_features(data.parks)
        buildings = clip_area_features(data.buildings)
        pois = select_pois(data.pois, center_lat, center_lon, request.style_tokens)
        stations = self.data.map_station_points(stations_raw, pois)
        stage_ms["feature_selection"] = round((time.perf_counter() - t0) * 1000.0, 2)

        # Build label candidates from selected features.
        t0 = time.perf_counter()
        candidates: list[LabelCandidate] = []
        bbox = data.bbox
        width = request.output.width_px
        height = request.output.height_px

        # Reserve area around subject marker.
        sub_x, sub_y = project_web_mercator(center_lon, center_lat, bbox, width, height)
        occupied = [(sub_x - 90, sub_y - 65, sub_x + 90, sub_y + 20)]

        priority_by_class = {
            name.lower(): len(request.style_tokens.rules.label_priority) - idx
            for idx, name in enumerate(request.style_tokens.rules.label_priority)
        }

        for poi in pois:
            px, py = project_web_mercator(poi.lon, poi.lat, bbox, width, height)
            cls = poi.category.lower()
            priority = priority_by_class.get(cls, 10)
            candidates.append(
                LabelCandidate(
                    text=poi.name,
                    label_class=cls,
                    anchor_x=px,
                    anchor_y=py,
                    priority=priority,
                    font_size=request.style_tokens.typography.label_size_pt,
                    critical=cls in MAP_V1_CRITICAL_LABEL_CLASSES,
                )
            )

        for station in stations:
            px, py = project_web_mercator(station["lon"], station["lat"], bbox, width, height)
            candidates.append(
                LabelCandidate(
                    text=str(station.get("name", "")),
                    label_class="station",
                    anchor_x=px,
                    anchor_y=py,
                    priority=120,
                    font_size=max(8.5, request.style_tokens.typography.label_size_pt + 0.8),
                    critical=True,
                )
            )

        for road in roads:
            if not road.name or len(road.coords) < 2:
                continue
            mid = road.coords[len(road.coords) // 2]
            px, py = project_web_mercator(mid[1], mid[0], bbox, width, height)
            cls = "road"
            candidates.append(
                LabelCandidate(
                    text=road.name.upper(),
                    label_class=cls,
                    anchor_x=px,
                    anchor_y=py,
                    priority=priority_by_class.get(cls, 6),
                    font_size=request.style_tokens.typography.road_label_size_pt,
                    critical=False,
                )
            )

        placement = place_labels(candidates, request.style_tokens, width, height, occupied_rects=occupied)
        stage_ms["label_layout"] = round((time.perf_counter() - t0) * 1000.0, 2)

        t0 = time.perf_counter()
        label_payload = []
        for label in placement.placements:
            font_size = (
                request.style_tokens.typography.road_label_size_pt
                if label.label_class == "road"
                else request.style_tokens.typography.label_size_pt
            )
            label_payload.append(
                {
                    "text": label.text,
                    "label_class": label.label_class,
                    "anchor_x": label.anchor_x,
                    "anchor_y": label.anchor_y,
                    "x": label.x,
                    "y": label.y,
                    "priority": label.priority,
                    "font_size": font_size,
                    "leader_to_x": label.leader_to_x,
                    "leader_to_y": label.leader_to_y,
                }
            )

        raw_svg = render_svg(
            width=width,
            height=height,
            bbox=bbox,
            style_tokens=request.style_tokens,
            roads=[r.model_dump(mode="json") for r in roads],
            waterways=[w.model_dump(mode="json") for w in data.waterways],
            parks=[p.model_dump(mode="json") for p in parks],
            buildings=[b.model_dump(mode="json") for b in buildings],
            pois=[p.model_dump(mode="json") for p in pois],
            stations=stations,
            subject={"lat": center_lat, "lon": center_lon, "name": content.building_name.upper()},
            labels=label_payload,
        )
        try:
            canonical_svg_bytes = canonical_svg(raw_svg)
            svg_content = canonical_svg_bytes.decode("utf-8")
        except Exception as exc:
            raise DeterminismViolationError("failed to canonicalize SVG output") from exc
        stage_ms["svg_render"] = round((time.perf_counter() - t0) * 1000.0, 2)

        t0 = time.perf_counter()
        pdf_content = svg_to_pdf_bytes(svg_content)
        stage_ms["pdf_export"] = round((time.perf_counter() - t0) * 1000.0, 2)

        t0 = time.perf_counter()
        try:
            purity = assert_vector_purity(svg_content, pdf_content)
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError("vector purity checks failed unexpectedly") from exc
        stage_ms["vector_purity"] = round((time.perf_counter() - t0) * 1000.0, 2)

        t0 = time.perf_counter()
        style_hash = stable_hash({"style": request.style_tokens.model_dump(mode="json")}, length=24)
        render_key = {
            "center": {"lat": center_lat, "lon": center_lon},
            "radius_m": radius_m,
            "output": request.output.model_dump(mode="json"),
            "content": content.model_dump(mode="json"),
            "style_tokens": request.style_tokens.model_dump(mode="json"),
            "dataset_version": MAP_V1_DATASET_VERSION,
            "renderer_version": MAP_V1_RENDERER_VERSION,
            "style_hash": style_hash,
        }
        request_hash = stable_hash(render_key, length=32)
        map_id = request_hash[:12]

        svg_digest = svg_hash(svg_content)
        pdf_digest = pdf_structural_hash(pdf_content)

        project_id, project_dir = self._project_dir_for(request.project_id)
        artifact_dir = project_dir / "maps_v1" / map_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        svg_path = artifact_dir / "map.svg"
        pdf_path = artifact_dir / "map.pdf"
        meta_path = artifact_dir / "metadata.json"
        style_path = artifact_dir / "style_tokens.json"

        svg_path.write_text(svg_content, encoding="utf-8")
        pdf_path.write_bytes(pdf_content)
        style_path.write_text(
            json.dumps(request.style_tokens.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        stage_ms["persist"] = round((time.perf_counter() - t0) * 1000.0, 2)

        metadata = RenderMetadata(
            request_hash=request_hash,
            dataset_version=MAP_V1_DATASET_VERSION,
            style_version=request.style_tokens.version,
            renderer_version=MAP_V1_RENDERER_VERSION,
            generated_at=datetime.now(tz=timezone.utc),
            placed_labels=placement.placed_labels,
            pushed_labels=placement.pushed_labels,
            dropped_labels=placement.dropped_labels,
            dropped_reason_counts=placement.dropped_reason_counts,
            critical_label_drops=placement.critical_label_drops,
            ui_warning_flags=placement.ui_warning_flags,
            stage_timings_ms=stage_ms,
            svg_hash=svg_digest,
            pdf_structural_hash=pdf_digest,
            text_metrics_backend=text_metrics_backend_name(),
            vector_purity=purity,
        )

        warnings: list[RenderWarning] = []
        if placement.critical_label_drops:
            warnings.append(
                RenderWarning(
                    code="CRITICAL_LABEL_DROPPED",
                    message="Some critical labels could not be placed collision-free.",
                    severity="warning",
                )
            )
        if placement.dropped_reason_counts.get("collision", 0) > 0:
            warnings.append(
                RenderWarning(
                    code="LABEL_DENSITY_HIGH",
                    message="Label density exceeded available space; lower-priority labels were removed.",
                    severity="info",
                )
            )

        response = MapRenderResponseV1(
            map_id=map_id,
            artifacts=RenderArtifacts(
                svg_url=f"/api/projects/{project_id}/maps/v1/{map_id}/map.svg",
                pdf_url=f"/api/projects/{project_id}/maps/v1/{map_id}/map.pdf",
            ),
            metadata=metadata,
            warnings=warnings,
            attribution="Data © OpenStreetMap contributors, Overture Maps Foundation (versioned local extract)",
        )

        total_ms = round((time.perf_counter() - start) * 1000.0, 2)
        response.metadata.stage_timings_ms["total"] = total_ms
        meta_path.write_text(json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(
            "map-v1 rendered project=%s map_id=%s labels=%d dropped=%d total_ms=%s",
            project_id,
            map_id,
            placement.placed_labels,
            placement.dropped_labels,
            total_ms,
        )

        return response


def map_http_exception(exc: Exception) -> tuple[int, dict[str, object]]:
    """Map internal exceptions to stable HTTP error payloads."""
    if isinstance(exc, MapV1Error):
        if exc.user_visible:
            return exc.status_code, exc.detail_payload()
        return 500, {
            "code": "EXPORT_FAILED",
            "message": "Internal render failure",
            "hint": "Check server logs for details.",
            "retryable": False,
        }
    return 500, {
        "code": "EXPORT_FAILED",
        "message": str(exc),
        "hint": "Check server logs for details.",
        "retryable": False,
    }
