"""Deterministic style token generation and validation for map-v1."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from .config import MAP_V1_STYLE_VERSION
from .errors import StyleValidationError
from .models import (
    MapStyleGenerateRequestV1,
    MapStyleGenerateResponseV1,
    PaletteTokens,
    RuleTokens,
    StyleTokens,
    StyleValidationReport,
    TypographyTokens,
)
from .utils import (
    blend,
    contrast_ratio,
    decode_image_reference,
    force_contrast,
    parse_hex,
    stable_hash,
    to_hex,
)


@dataclass(frozen=True)
class _VibeAdjustments:
    warmth: float = 0.0
    saturation: float = 0.0
    lightness: float = 0.0


_VIBE_KEYWORDS: dict[str, _VibeAdjustments] = {
    "luxury": _VibeAdjustments(warmth=0.08, saturation=-0.10, lightness=-0.04),
    "minimal": _VibeAdjustments(warmth=0.00, saturation=-0.12, lightness=0.02),
    "warm": _VibeAdjustments(warmth=0.14, saturation=0.04, lightness=0.02),
    "cool": _VibeAdjustments(warmth=-0.14, saturation=0.02, lightness=0.02),
    "bold": _VibeAdjustments(warmth=0.00, saturation=0.20, lightness=-0.03),
    "vintage": _VibeAdjustments(warmth=0.10, saturation=-0.08, lightness=0.04),
    "clean": _VibeAdjustments(warmth=0.00, saturation=-0.06, lightness=0.05),
    "editorial": _VibeAdjustments(warmth=0.02, saturation=-0.04, lightness=0.00),
}


def _dominant_from_image(style_image_ref: str) -> str | None:
    payload = decode_image_reference(style_image_ref)
    if not payload:
        return None

    with Image.open(io.BytesIO(payload)) as image:
        rgb = image.convert("RGB")
        small = rgb.resize((64, 64))
        paletted = small.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
        palette = paletted.getpalette() or []
        counts = paletted.getcolors()
        if not counts:
            return None
        counts.sort(reverse=True, key=lambda item: item[0])
        most_common_index = counts[0][1]
        base = most_common_index * 3
        if base + 2 >= len(palette):
            return None
        r = palette[base]
        g = palette[base + 1]
        b = palette[base + 2]
        return to_hex((r, g, b, 255))


def _apply_vibe(base_hex: str, vibe_text: str) -> str:
    vibe = (vibe_text or "").lower()
    if not vibe:
        return base_hex

    total = _VibeAdjustments()
    for keyword, adj in _VIBE_KEYWORDS.items():
        if keyword in vibe:
            total = _VibeAdjustments(
                warmth=total.warmth + adj.warmth,
                saturation=total.saturation + adj.saturation,
                lightness=total.lightness + adj.lightness,
            )

    r, g, b, a = parse_hex(base_hex)

    # Warmth shifts red/blue balance deterministically.
    r = max(0, min(255, round(r + 255 * total.warmth)))
    b = max(0, min(255, round(b - 255 * total.warmth)))

    # Saturation approximation by blending toward/away from neutral gray.
    gray = round((r + g + b) / 3)
    sat_gain = max(-0.35, min(0.35, total.saturation))
    r = max(0, min(255, round(r + (r - gray) * sat_gain)))
    g = max(0, min(255, round(g + (g - gray) * sat_gain)))
    b = max(0, min(255, round(b + (b - gray) * sat_gain)))

    # Lightness shift.
    l_gain = max(-0.25, min(0.25, total.lightness))
    if l_gain >= 0:
        r = round(r + (255 - r) * l_gain)
        g = round(g + (255 - g) * l_gain)
        b = round(b + (255 - b) * l_gain)
    else:
        r = round(r * (1 + l_gain))
        g = round(g * (1 + l_gain))
        b = round(b * (1 + l_gain))

    return to_hex((r, g, b, a))


def _build_palette(seed_hex: str, brochure_hex: str) -> PaletteTokens:
    # Transparent base so brochure/PDF page colour shows through.
    map_background = "#00000000"
    land_fill = "#00000000"

    water = blend(seed_hex, "#A8D5F2", 0.55)
    park = blend(seed_hex, "#A9D8A5", 0.50)

    road_minor_casing = blend(brochure_hex, "#0A0A0A", 0.35)
    road_minor_fill = blend(brochure_hex, "#FAFAFA", 0.88)
    road_major_casing = blend(brochure_hex, "#060606", 0.55)
    road_major_fill = blend(brochure_hex, "#FDFDFD", 0.92)

    label_halo = blend(brochure_hex, "#FFFFFF", 0.92)
    label_text = force_contrast(blend(seed_hex, "#101010", 0.70), label_halo, minimum=4.5)

    poi_fill = force_contrast(blend(seed_hex, "#121212", 0.64), label_halo, minimum=4.5)
    poi_stroke = label_halo

    building_fill = force_contrast(blend(seed_hex, "#1A1A1A", 0.52), label_halo, minimum=3.0)
    building_stroke = blend(building_fill, "#FFFFFF", 0.18)

    station_fill = blend(seed_hex, "#C73737", 0.30)
    station_stroke = blend(station_fill, "#FFFFFF", 0.25)

    return PaletteTokens(
        map_background=map_background,
        land_fill=land_fill,
        water_fill=water,
        park_fill=park,
        road_minor_casing=road_minor_casing,
        road_minor_fill=road_minor_fill,
        road_major_casing=road_major_casing,
        road_major_fill=road_major_fill,
        label_text=label_text,
        label_halo=label_halo,
        poi_icon_fill=poi_fill,
        poi_icon_stroke=poi_stroke,
        building_fill=building_fill,
        building_stroke=building_stroke,
        station_fill=station_fill,
        station_stroke=station_stroke,
    )


def _default_rules() -> RuleTokens:
    return RuleTokens(
        max_pois_total=28,
        max_pois_per_category={
            "cafe": 6,
            "restaurant": 6,
            "transit": 6,
            "gym": 3,
            "school": 3,
            "bar": 3,
            "park": 4,
        },
        label_priority=["building", "transit", "station", "park", "school", "cafe", "restaurant", "road"],
        push_radius_steps_px=[20, 32, 46, 62],
        max_leader_line_px=65,
        label_halo_mode="bbox_rect",
    )


def _default_typography() -> TypographyTokens:
    return TypographyTokens(
        font_family_sans="Helvetica",
        font_family_serif="Baskerville",
        label_size_pt=9.0,
        road_label_size_pt=8.0,
        halo_width_pt=1.4,
        tracking=0.0,
    )


def _validate_and_fix(tokens: StyleTokens) -> StyleValidationReport:
    report = StyleValidationReport()

    ratio = contrast_ratio(tokens.palette.label_text, tokens.palette.label_halo)
    if ratio < 4.5:
        tokens.palette.label_text = force_contrast(tokens.palette.label_text, tokens.palette.label_halo, 4.5)
        report.fixed_fields.append("palette.label_text")

    if contrast_ratio(tokens.palette.poi_icon_fill, tokens.palette.label_halo) < 4.5:
        tokens.palette.poi_icon_fill = force_contrast(tokens.palette.poi_icon_fill, tokens.palette.label_halo, 4.5)
        report.fixed_fields.append("palette.poi_icon_fill")

    # Ensure major/minor road distinction.
    if tokens.palette.road_major_fill == tokens.palette.road_minor_fill:
        tokens.palette.road_major_fill = blend(tokens.palette.road_major_fill, "#FDFDFD", 0.35)
        report.fixed_fields.append("palette.road_major_fill")

    if tokens.rules.label_halo_mode != "bbox_rect":
        raise StyleValidationError("unsupported label_halo_mode; expected 'bbox_rect'")

    return report


def generate_style_tokens(payload: MapStyleGenerateRequestV1) -> MapStyleGenerateResponseV1:
    seed = payload.brochure_primary_hex

    image_hex = _dominant_from_image(payload.style_image_ref)
    if image_hex:
        seed = blend(seed, image_hex, 0.45)

    seed = _apply_vibe(seed, payload.vibe_text)

    style_tokens = StyleTokens(
        version=MAP_V1_STYLE_VERSION,
        palette=_build_palette(seed, payload.brochure_primary_hex),
        typography=_default_typography(),
        rules=_default_rules(),
    )
    report = _validate_and_fix(style_tokens)

    style_hash = stable_hash({
        "style_version": MAP_V1_STYLE_VERSION,
        "style_tokens": style_tokens.model_dump(mode="json"),
    }, length=24)

    return MapStyleGenerateResponseV1(
        style_tokens=style_tokens,
        style_version=MAP_V1_STYLE_VERSION,
        style_hash=style_hash,
        validation_report=report,
    )
