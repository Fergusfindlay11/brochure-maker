"""Pydantic models for the v1 map pipeline API.

Covers:
- POST /api/maps/v1/style/generate  (request + response)
- POST /api/maps/v1/render          (request + response)
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ──────────────────────────────────────────────
#  Shared / Enum Types
# ──────────────────────────────────────────────

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


class OutputFormat(str, Enum):
    svg = "svg"
    pdf = "pdf"
    both = "both"


class LabelHaloMode(str, Enum):
    bbox_rect = "bbox_rect"
    none = "none"


# ──────────────────────────────────────────────
#  Style Tokens — the deterministic bridge
# ──────────────────────────────────────────────

class RoadStyleTokens(BaseModel):
    """Colours and widths for road rendering."""
    minor_casing: str
    minor_fill: str
    major_casing: str
    major_fill: str
    minor_casing_width: float = 5.5
    minor_fill_width: float = 3.5
    major_casing_width: float = 14.0
    major_fill_width: float = 10.0
    minor_casing_opacity: float = 0.50
    minor_fill_opacity: float = 0.28
    major_casing_opacity: float = 0.80
    major_fill_opacity: float = 0.50


class LabelStyleTokens(BaseModel):
    """Typography and colour tokens for labels."""
    street_colour: str
    street_halo: str
    street_font_size: float = 10.0
    street_font_weight: int = 600
    street_letter_spacing: float = 2.2
    poi_colour: str
    poi_halo: str
    poi_font_size: float = 11.0
    poi_font_weight: int = 500
    park_colour: str
    water_colour: str
    neighbourhood_colour: str
    neighbourhood_opacity: float = 0.10
    label_halo_mode: LabelHaloMode = LabelHaloMode.bbox_rect


class MarkerStyleTokens(BaseModel):
    """Tokens for building marker and station markers."""
    building_fill: str
    building_stroke: str
    building_label_colour: str
    building_label_font_size: float = 28.0
    station_roundel_colour: str = "#CC3333"


class FeatureStyleTokens(BaseModel):
    """Colours for map features (parks, water, buildings)."""
    park_fill: str
    park_opacity: float = 0.45
    water_stroke: str
    water_opacity: float = 0.70
    block_fill: str
    block_opacity: float = 0.15


class StyleTokens(BaseModel):
    """Complete resolved style tokens for deterministic rendering."""
    base_hex: str
    primary_hex: str
    is_light_base: bool
    text_primary: str
    text_secondary: str
    text_stroke: str
    roads: RoadStyleTokens
    labels: LabelStyleTokens
    markers: MarkerStyleTokens
    features: FeatureStyleTokens
    font_families: dict[str, str] = Field(default_factory=lambda: {
        "serif": "Playfair Display",
        "sans": "Jost",
    })

    def compute_hash(self) -> str:
        """SHA-256 of the canonical JSON representation."""
        canonical = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ──────────────────────────────────────────────
#  Style Generate Endpoint
# ──────────────────────────────────────────────

class MapStyleGenerateRequestV1(BaseModel):
    """POST /api/maps/v1/style/generate"""
    brochure_primary_hex: str
    vibe_text: str | None = None
    style_image_ref: str | None = None

    @field_validator("brochure_primary_hex")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        v = v.strip()
        if not _HEX_RE.match(v):
            raise ValueError(f"Invalid hex colour: {v!r}")
        # Normalise 3-digit hex to 6-digit
        h = v.lstrip("#")
        if len(h) == 3:
            h = h[0] * 2 + h[1] * 2 + h[2] * 2
        return f"#{h.lower()}"


class StyleValidationReport(BaseModel):
    """Validation details for generated style tokens."""
    valid: bool = True
    fixes_applied: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MapStyleGenerateResponseV1(BaseModel):
    """Response from POST /api/maps/v1/style/generate"""
    style_tokens: StyleTokens
    style_version: str
    style_hash: str
    validation_report: StyleValidationReport


# ──────────────────────────────────────────────
#  Render Endpoint
# ──────────────────────────────────────────────

class MapCenter(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class MapExtent(BaseModel):
    radius_m: int = Field(default=350, ge=50, le=5000)
    width_px: int = Field(default=940, ge=200, le=4000)
    height_px: int = Field(default=750, ge=200, le=3000)


class MapOutput(BaseModel):
    format: OutputFormat = OutputFormat.both


class MapContent(BaseModel):
    """Content to render on the map."""
    building_name: str = ""
    stations: list[dict[str, Any]] = Field(default_factory=list)
    poi_max_count: int = Field(default=15, ge=0, le=50)
    street_label_max: int = Field(default=4, ge=0, le=20)


class MapRenderRequestV1(BaseModel):
    """POST /api/maps/v1/render

    Requires resolved style_tokens. Raw vibe_text and style_image_ref
    are explicitly disallowed.
    """
    center: MapCenter
    extent: MapExtent = Field(default_factory=MapExtent)
    output: MapOutput = Field(default_factory=MapOutput)
    content: MapContent = Field(default_factory=MapContent)
    style_tokens: StyleTokens
    debug: bool = False

    @model_validator(mode="before")
    @classmethod
    def _reject_raw_style_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for forbidden in ("vibe_text", "style_image_ref"):
                if forbidden in data:
                    raise ValueError(
                        f"'{forbidden}' is not allowed in the render endpoint. "
                        f"Use /api/maps/v1/style/generate first."
                    )
        return data


# ──────────────────────────────────────────────
#  Render Response
# ──────────────────────────────────────────────

class LabelMetrics(BaseModel):
    placed_labels: int = 0
    pushed_labels: int = 0
    dropped_labels: int = 0
    dropped_reason_counts: dict[str, int] = Field(default_factory=dict)
    critical_label_drops: list[str] = Field(default_factory=list)


class RenderMetadata(BaseModel):
    label_metrics: LabelMetrics = Field(default_factory=LabelMetrics)
    dataset_version: str = ""
    style_version: str = ""
    renderer_version: str = "1.0.0"
    request_hash: str = ""


class UIWarningFlag(BaseModel):
    code: str
    message: str
    severity: str = "warning"  # "warning" | "info"


class MapRenderResponseV1(BaseModel):
    """Response from POST /api/maps/v1/render"""
    svg_url: str | None = None
    svg_content: str | None = None
    pdf_url: str | None = None
    metadata: RenderMetadata = Field(default_factory=RenderMetadata)
    warnings: list[str] = Field(default_factory=list)
    ui_warning_flags: list[UIWarningFlag] = Field(default_factory=list)
    attribution: str = "Data: Overture Maps Foundation. Basemap: OpenStreetMap contributors."
