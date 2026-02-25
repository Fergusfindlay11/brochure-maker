"""Typed errors for Map V1 pipeline with stable API payload contracts."""

from __future__ import annotations


class MapV1Error(Exception):
    """Base class for map-v1 errors."""

    code = "EXPORT_FAILED"
    status_code = 500
    hint = "Check map-v1 service logs."
    retryable = False
    user_visible = True

    def __init__(self, message: str | None = None, *, hint: str | None = None, retryable: bool | None = None):
        super().__init__(message or self.default_message())
        self.message = message or self.default_message()
        if hint is not None:
            self.hint = hint
        if retryable is not None:
            self.retryable = retryable

    @classmethod
    def default_message(cls) -> str:
        return cls.__name__

    def detail_payload(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }


class MapDataUnavailableError(MapV1Error):
    code = "DATASET_UNAVAILABLE"
    status_code = 503
    hint = "Verify local DuckDB/PMTiles datasets are ingested and mounted."
    retryable = False

    @classmethod
    def default_message(cls) -> str:
        return "Required map dataset is unavailable."


class PMTilesReadError(MapV1Error):
    code = "PMTILES_READ_ERROR"
    status_code = 502
    hint = "Check PMTiles path/object availability and retry extraction."
    retryable = True

    @classmethod
    def default_message(cls) -> str:
        return "Basemap extraction from PMTiles failed."


class PoiQueryTimeoutError(MapV1Error):
    code = "POI_QUERY_TIMEOUT"
    status_code = 504
    hint = "Reduce extent/category filters or retry shortly."
    retryable = True

    @classmethod
    def default_message(cls) -> str:
        return "POI query exceeded timeout budget."


class FontMissingError(MapV1Error):
    code = "FONT_MISSING"
    status_code = 500
    hint = "Install required brochure fonts and refresh Fontconfig cache."
    retryable = False

    @classmethod
    def default_message(cls) -> str:
        return "Required rendering fonts are missing."


class StyleValidationError(MapV1Error):
    code = "STYLE_VALIDATION_FAILED"
    status_code = 422
    hint = "Adjust style inputs or regenerate style tokens."
    retryable = False

    @classmethod
    def default_message(cls) -> str:
        return "Style token validation failed."


class DeterminismViolationError(MapV1Error):
    code = "RENDER_DETERMINISM_VIOLATION"
    status_code = 500
    hint = "Re-run render and inspect determinism logs."
    retryable = False
    user_visible = False

    @classmethod
    def default_message(cls) -> str:
        return "Determinism validation failed."


class ExportError(MapV1Error):
    code = "EXPORT_FAILED"
    status_code = 500
    hint = "Check SVG/PDF export backend and vector purity constraints."
    retryable = False

    @classmethod
    def default_message(cls) -> str:
        return "Map export failed."
