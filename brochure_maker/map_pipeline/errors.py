"""Error taxonomy for the map pipeline.

Every failure produces an actionable error code + remediation hint.
No silent fallbacks.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class MapErrorCode(str, Enum):
    """Canonical error codes for the map pipeline."""

    DATASET_UNAVAILABLE = "DATASET_UNAVAILABLE"
    PMTILES_READ_ERROR = "PMTILES_READ_ERROR"
    POI_QUERY_TIMEOUT = "POI_QUERY_TIMEOUT"
    FONT_MISSING = "FONT_MISSING"
    STYLE_VALIDATION_FAILED = "STYLE_VALIDATION_FAILED"
    RENDER_DETERMINISM_VIOLATION = "RENDER_DETERMINISM_VIOLATION"
    EXPORT_FAILED = "EXPORT_FAILED"
    INVALID_REQUEST = "INVALID_REQUEST"
    VECTOR_PURITY_FAILED = "VECTOR_PURITY_FAILED"


# Human-readable remediation hints per error code.
_REMEDIATION: dict[MapErrorCode, str] = {
    MapErrorCode.DATASET_UNAVAILABLE: (
        "The local POI dataset is not initialised. "
        "Run the ingestion pipeline or check the DuckDB file path."
    ),
    MapErrorCode.PMTILES_READ_ERROR: (
        "Could not read basemap tiles. "
        "Verify the PMTiles archive exists and is not corrupted."
    ),
    MapErrorCode.POI_QUERY_TIMEOUT: (
        "POI query exceeded the latency budget. "
        "Try a smaller extent or check DuckDB index health."
    ),
    MapErrorCode.FONT_MISSING: (
        "A required font is not available in the runtime environment. "
        "Ensure fonts are bundled in the container image."
    ),
    MapErrorCode.STYLE_VALIDATION_FAILED: (
        "The style tokens failed schema validation. "
        "Check the input colour values and vibe text."
    ),
    MapErrorCode.RENDER_DETERMINISM_VIOLATION: (
        "Internal: render output did not match expected hash. "
        "This is a bug — please report it."
    ),
    MapErrorCode.EXPORT_FAILED: (
        "SVG-to-PDF conversion failed. "
        "Check that librsvg / rsvg-convert is installed."
    ),
    MapErrorCode.INVALID_REQUEST: (
        "The request payload is invalid. Check required fields."
    ),
    MapErrorCode.VECTOR_PURITY_FAILED: (
        "The output contains disallowed raster content. "
        "Review the rendering pipeline for rasterisation leaks."
    ),
}


class MapPipelineError(Exception):
    """Base exception for all map pipeline errors."""

    def __init__(
        self,
        code: MapErrorCode,
        detail: str = "",
        context: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.detail = detail or _REMEDIATION.get(code, "Unknown error.")
        self.context = context or {}
        super().__init__(f"[{code.value}] {self.detail}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.code.value,
            "detail": self.detail,
            "remediation": _REMEDIATION.get(self.code, ""),
            "context": self.context,
        }


class DatasetUnavailableError(MapPipelineError):
    def __init__(self, detail: str = "", **kw: Any) -> None:
        super().__init__(MapErrorCode.DATASET_UNAVAILABLE, detail, **kw)


class PMTilesReadError(MapPipelineError):
    def __init__(self, detail: str = "", **kw: Any) -> None:
        super().__init__(MapErrorCode.PMTILES_READ_ERROR, detail, **kw)


class POIQueryTimeoutError(MapPipelineError):
    def __init__(self, detail: str = "", **kw: Any) -> None:
        super().__init__(MapErrorCode.POI_QUERY_TIMEOUT, detail, **kw)


class FontMissingError(MapPipelineError):
    def __init__(self, detail: str = "", **kw: Any) -> None:
        super().__init__(MapErrorCode.FONT_MISSING, detail, **kw)


class StyleValidationError(MapPipelineError):
    def __init__(self, detail: str = "", **kw: Any) -> None:
        super().__init__(MapErrorCode.STYLE_VALIDATION_FAILED, detail, **kw)


class ExportFailedError(MapPipelineError):
    def __init__(self, detail: str = "", **kw: Any) -> None:
        super().__init__(MapErrorCode.EXPORT_FAILED, detail, **kw)


class VectorPurityError(MapPipelineError):
    def __init__(self, detail: str = "", **kw: Any) -> None:
        super().__init__(MapErrorCode.VECTOR_PURITY_FAILED, detail, **kw)
