"""Pydantic models for map-v1 APIs and internals."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_HEX_CHARS = set("0123456789abcdefABCDEF")


def _is_hex_colour(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if not value.startswith("#"):
        return False
    raw = value[1:]
    if len(raw) not in (6, 8):
        return False
    return all(ch in _HEX_CHARS for ch in raw)


class LatLon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)


class ExtentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    radius_m: int = Field(900, ge=100, le=4000)


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width_px: int = Field(940, ge=200, le=3000)
    height_px: int = Field(750, ge=200, le=3000)
    include_svg: bool = True
    include_pdf: bool = True

    @model_validator(mode="after")
    def _validate_outputs(self) -> "OutputSpec":
        if not self.include_svg or not self.include_pdf:
            raise ValueError("map-v1 render requires include_svg=true and include_pdf=true")
        return self


class StationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    time: str = Field("", max_length=80)
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)


class ContentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    building_name: str = Field(..., min_length=1, max_length=200)
    stations: list[StationInput] = Field(default_factory=list)
    poi_categories: list[str] = Field(default_factory=list)


class PaletteTokens(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map_background: str
    land_fill: str
    water_fill: str
    park_fill: str
    road_minor_casing: str
    road_minor_fill: str
    road_major_casing: str
    road_major_fill: str
    label_text: str
    label_halo: str
    poi_icon_fill: str
    poi_icon_stroke: str
    building_fill: str
    building_stroke: str
    station_fill: str
    station_stroke: str

    @field_validator("*")
    @classmethod
    def _validate_hex(cls, value: str) -> str:
        if not _is_hex_colour(value):
            raise ValueError(f"invalid colour '{value}', expected #RRGGBB or #RRGGBBAA")
        return value.upper()


class TypographyTokens(BaseModel):
    model_config = ConfigDict(extra="forbid")

    font_family_sans: str = Field("Helvetica", min_length=1, max_length=120)
    font_family_serif: str = Field("Baskerville", min_length=1, max_length=120)
    label_size_pt: float = Field(9.0, ge=5.0, le=20.0)
    road_label_size_pt: float = Field(8.0, ge=5.0, le=20.0)
    halo_width_pt: float = Field(1.4, ge=0.1, le=6.0)
    tracking: float = Field(0.0, ge=-1.0, le=8.0)


class RuleTokens(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_pois_total: int = Field(28, ge=0, le=200)
    max_pois_per_category: dict[str, int] = Field(default_factory=dict)
    label_priority: list[str] = Field(default_factory=list)
    push_radius_steps_px: list[int] = Field(default_factory=lambda: [20, 32, 46, 62])
    max_leader_line_px: int = Field(65, ge=0, le=200)
    label_halo_mode: Literal["bbox_rect"] = "bbox_rect"


class StyleTokens(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(..., min_length=1, max_length=24)
    palette: PaletteTokens
    typography: TypographyTokens
    rules: RuleTokens


class MapStyleGenerateRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brochure_primary_hex: str
    vibe_text: str = Field("", max_length=500)
    style_image_ref: str = Field("", max_length=4096)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("brochure_primary_hex")
    @classmethod
    def _validate_base_hex(cls, value: str) -> str:
        if not _is_hex_colour(value):
            raise ValueError("brochure_primary_hex must be #RRGGBB or #RRGGBBAA")
        return value.upper()


class StyleValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MapStyleGenerateResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style_tokens: StyleTokens
    style_version: str
    style_hash: str
    validation_report: StyleValidationReport


class RenderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debug: bool = False


class MapRenderRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    center: LatLon | None = None
    extent: ExtentSpec = Field(default_factory=ExtentSpec)
    output: OutputSpec = Field(default_factory=OutputSpec)
    content: ContentSpec | None = None
    style_tokens: StyleTokens
    options: RenderOptions = Field(default_factory=RenderOptions)

    @model_validator(mode="after")
    def _validate_source(self) -> "MapRenderRequestV1":
        if self.project_id:
            return self
        if self.center is None or self.content is None:
            raise ValueError("either project_id or (center + content) is required")
        return self


class RenderArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    svg_url: str
    pdf_url: str


class RenderWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: str = Field("info")


class RenderMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_hash: str
    dataset_version: str
    style_version: str
    renderer_version: str
    generated_at: datetime
    placed_labels: int = 0
    pushed_labels: int = 0
    dropped_labels: int = 0
    dropped_reason_counts: dict[str, int] = Field(default_factory=dict)
    critical_label_drops: int = 0
    ui_warning_flags: list[str] = Field(default_factory=list)
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)
    svg_hash: str = ""
    pdf_structural_hash: str = ""
    text_metrics_backend: str = ""
    vector_purity: dict[str, bool] = Field(default_factory=dict)


class MapRenderResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map_id: str
    artifacts: RenderArtifacts
    metadata: RenderMetadata
    warnings: list[RenderWarning] = Field(default_factory=list)
    attribution: str


class LabelPlacement(BaseModel):
    """Internal label placement payload returned by label_engine."""

    model_config = ConfigDict(extra="forbid")

    text: str
    label_class: str
    anchor_x: float
    anchor_y: float
    x: float
    y: float
    priority: int
    pushed: bool = False
    dropped: bool = False
    drop_reason: str = ""
    leader_to_x: float | None = None
    leader_to_y: float | None = None


class PoiFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    category: str
    lat: float
    lon: float
    priority: float = 0.0
    source: str = ""
    license: str = ""


class RoadFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    road_class: str = "minor"
    name: str = ""
    coords: list[tuple[float, float]] = Field(default_factory=list)


class AreaFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    area_class: str
    name: str = ""
    rings: list[list[tuple[float, float]]] = Field(default_factory=list)


class RenderScene(BaseModel):
    """Projected scene consumed by svg renderer."""

    model_config = ConfigDict(extra="forbid")

    width: int
    height: int
    roads: list[dict[str, Any]] = Field(default_factory=list)
    parks: list[dict[str, Any]] = Field(default_factory=list)
    waterways: list[dict[str, Any]] = Field(default_factory=list)
    buildings: list[dict[str, Any]] = Field(default_factory=list)
    pois: list[dict[str, Any]] = Field(default_factory=list)
    stations: list[dict[str, Any]] = Field(default_factory=list)
    labels: list[dict[str, Any]] = Field(default_factory=list)
    subject: dict[str, Any]
