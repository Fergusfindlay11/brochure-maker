"""Orchestrator — ties the pipeline modules together for the render endpoint.

Handles the full render flow:
1. Extract basemap features (Overpass fallback or PMTiles).
2. Query POIs from DuckDB (if available) or extract from Overpass.
3. Select features (filter, quota).
4. Render SVG via multi-pass renderer.
5. Export to PDF if requested.
6. Return response with metadata.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from .basemap import extract_basemap, BasemapExtraction
from .data_service import (
    POIRecord,
    is_dataset_available,
    query_pois_bbox,
    get_dataset_manifest,
)
from .errors import MapPipelineError
from .feature_selection import (
    SelectedFeatures,
    SelectedPOI,
    select_features,
)
from .models import (
    MapRenderRequestV1,
    MapRenderResponseV1,
    RenderMetadata,
    LabelMetrics,
    UIWarningFlag,
    OutputFormat,
)
from .pdf_export import (
    svg_to_pdf,
    check_svg_purity,
    check_pdf_purity,
    compute_pdf_structural_hash,
)
from .svg_renderer import render_svg, compute_svg_hash

logger = logging.getLogger(__name__)

RENDERER_VERSION = "1.0.0"


async def render_map(
    request: MapRenderRequestV1,
    artifacts_dir: Path | None = None,
) -> MapRenderResponseV1:
    """Execute the full render pipeline.

    Args:
        request: Validated render request.
        artifacts_dir: Optional directory to write SVG/PDF files.

    Returns:
        MapRenderResponseV1 with SVG content, optional PDF, metadata, warnings.
    """
    start_time = time.monotonic()
    warnings: list[str] = []
    ui_warnings: list[UIWarningFlag] = []

    lat = request.center.lat
    lng = request.center.lng
    radius_m = request.extent.radius_m
    width = request.extent.width_px
    height = request.extent.height_px
    tokens = request.style_tokens

    # Step 1: Extract basemap features
    logger.info("Extracting basemap features for (%.5f, %.5f) r=%dm", lat, lng, radius_m)
    basemap = await extract_basemap(lat, lng, radius_m, width, height)

    # Step 2: Query POIs
    pois: list[POIRecord] = []
    dataset_version = ""

    if is_dataset_available():
        try:
            import math
            lat_offset = radius_m / 111320.0
            lon_offset = radius_m / (111320.0 * math.cos(math.radians(lat)))
            south = lat - lat_offset
            north = lat + lat_offset
            west = lng - lon_offset
            east = lng + lon_offset

            pois = query_pois_bbox(south, west, north, east)
            manifest = get_dataset_manifest()
            if manifest:
                dataset_version = manifest.release_id
            logger.info("Queried %d POIs from DuckDB", len(pois))
        except MapPipelineError as e:
            warnings.append(f"DuckDB POI query failed: {e.detail}")
            logger.warning("DuckDB query failed, using Overpass POIs: %s", e)
        except Exception as e:
            warnings.append(f"POI query failed: {str(e)}")
            logger.warning("POI query failed: %s", e)

    # Fallback: extract POIs from Overpass features
    if not pois:
        overpass_pois = _extract_pois_from_overpass(basemap, lat, lng, radius_m)
        pois = overpass_pois
        if not dataset_version:
            dataset_version = "overpass-live"

    # Step 3: Extract additional features from Overpass (rail stations, neighbourhoods)
    overpass_features = await _get_overpass_extra_features(lat, lng, radius_m)

    # Step 4: Select features
    selected = select_features(
        roads=basemap.roads,
        parks=basemap.parks,
        water=basemap.water,
        buildings=basemap.buildings,
        pois=pois,
        center_lat=lat,
        center_lon=lng,
        radius_m=radius_m,
        max_pois=request.content.poi_max_count,
        overpass_features=overpass_features,
    )

    # Step 5: Prepare station data
    station_data = await _prepare_stations(
        request.content.stations, lat, lng,
    )

    # Step 6: Render SVG
    svg_content, label_metrics = render_svg(
        features=selected,
        center_lat=lat,
        center_lon=lng,
        width=width,
        height=height,
        radius_m=radius_m,
        tokens=tokens,
        building_name=request.content.building_name,
        stations=station_data,
        debug=request.debug,
    )

    # Vector purity check
    try:
        check_svg_purity(svg_content)
    except MapPipelineError as e:
        warnings.append(f"Vector purity warning: {e.detail}")

    # Emit UI warnings for critical label drops
    if label_metrics.critical_label_drops:
        ui_warnings.append(UIWarningFlag(
            code="CRITICAL_LABEL_DROPPED",
            message=f"Critical labels could not be placed: {', '.join(label_metrics.critical_label_drops)}",
            severity="warning",
        ))

    # Step 7: Export PDF if requested
    svg_url = None
    pdf_url = None

    if artifacts_dir:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        svg_path = artifacts_dir / "map.svg"
        svg_path.write_text(svg_content, encoding="utf-8")
        svg_url = str(svg_path)

        if request.output.format in (OutputFormat.pdf, OutputFormat.both):
            try:
                pdf_path = artifacts_dir / "map.pdf"
                pdf_bytes = svg_to_pdf(svg_content, pdf_path, width, height)
                check_pdf_purity(pdf_bytes)
                pdf_url = str(pdf_path)
            except MapPipelineError as e:
                warnings.append(f"PDF export failed: {e.detail}")
                logger.warning("PDF export failed: %s", e)

    # Build response
    svg_hash = compute_svg_hash(svg_content)
    request_hash = hashlib.sha256(
        json.dumps(request.model_dump(), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]

    elapsed = time.monotonic() - start_time
    logger.info("Render complete in %.1fs (SVG hash: %s)", elapsed, svg_hash)

    return MapRenderResponseV1(
        svg_url=svg_url,
        svg_content=svg_content,
        pdf_url=pdf_url,
        metadata=RenderMetadata(
            label_metrics=label_metrics,
            dataset_version=dataset_version,
            style_version=tokens.compute_hash(),
            renderer_version=RENDERER_VERSION,
            request_hash=request_hash,
        ),
        warnings=warnings,
        ui_warning_flags=ui_warnings,
    )


def _extract_pois_from_overpass(
    basemap: BasemapExtraction,
    center_lat: float,
    center_lon: float,
    radius_m: int,
) -> list[POIRecord]:
    """Convert Overpass basemap features to POIRecord format as fallback."""
    # Overpass features don't directly contain typed POI data in our basemap
    # extraction, so we return empty and let the Overpass extra features handle it.
    return []


async def _get_overpass_extra_features(
    lat: float,
    lon: float,
    radius_m: int,
) -> dict[str, Any]:
    """Get additional Overpass features (POIs, stations, neighbourhoods)."""
    try:
        from brochure_maker.illustrated_map import (
            fetch_overpass_data,
            _parse_overpass_elements,
        )
        raw = await fetch_overpass_data(lat, lon, radius_m)
        parsed = _parse_overpass_elements(raw)

        # Convert POIs to POIRecord format
        poi_records = []
        for poi in parsed.get("pois", []):
            poi_records.append(POIRecord(
                overture_id=f"overpass_{poi['lat']}_{poi['lon']}",
                name=poi.get("name", ""),
                category=poi.get("type", ""),
                lat=poi["lat"],
                lon=poi["lon"],
                source="overpass",
            ))

        return {
            "pois": poi_records,
            "rail_stations": parsed.get("rail_stations", []),
            "neighbourhoods": parsed.get("neighbourhoods", []),
        }
    except Exception as e:
        logger.warning("Failed to fetch Overpass extra features: %s", e)
        return {"pois": [], "rail_stations": [], "neighbourhoods": []}


async def _prepare_stations(
    stations_input: list[dict[str, Any]],
    center_lat: float,
    center_lon: float,
) -> list[dict[str, Any]]:
    """Prepare station data with coordinates.

    If stations already have lat/lon, use them directly.
    Otherwise, attempt geocoding.
    """
    prepared = []
    for st in stations_input:
        if st.get("lat") is not None and st.get("lon") is not None:
            prepared.append(st)
        elif st.get("name"):
            # Try geocoding
            try:
                from brochure_maker.map_generator import geocode_stations
                geocoded = await geocode_stations([st], "")
                if geocoded:
                    prepared.append({**st, **geocoded[0]})
            except Exception:
                pass  # Skip stations we can't geocode
    return prepared
