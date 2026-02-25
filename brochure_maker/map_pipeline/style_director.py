"""Style Director — generates and validates deterministic style tokens.

Anchors palette to brochure global colour. Same inputs + same model version
produce identical style_tokens (deterministic).

Keeps base/background map elements transparent by default.
Computes adaptive road/label/icon colours for contrast.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import logging
import re
from typing import Any

from .errors import StyleValidationError
from .models import (
    FeatureStyleTokens,
    LabelHaloMode,
    LabelStyleTokens,
    MapStyleGenerateRequestV1,
    MapStyleGenerateResponseV1,
    MarkerStyleTokens,
    RoadStyleTokens,
    StyleTokens,
    StyleValidationReport,
)

logger = logging.getLogger(__name__)

STYLE_VERSION = "1.0.0"

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ──────────────────────────────────────────────
#  Colour Helpers (pure, deterministic)
# ──────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _luminance(hex_colour: str) -> float:
    r, g, b = _hex_to_rgb(hex_colour)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _darken(hex_colour: str, factor: float = 0.75) -> str:
    r, g, b = _hex_to_rgb(hex_colour)
    return _rgb_to_hex(int(r * factor), int(g * factor), int(b * factor))


def _lighten(hex_colour: str, factor: float = 0.3) -> str:
    r, g, b = _hex_to_rgb(hex_colour)
    return _rgb_to_hex(
        int(r + (255 - r) * factor),
        int(g + (255 - g) * factor),
        int(b + (255 - b) * factor),
    )


def _tint(base: str, accent: str, ratio: float = 0.4) -> str:
    br, bg, bb = _hex_to_rgb(base)
    ar, ag, ab = _hex_to_rgb(accent)
    return _rgb_to_hex(
        max(0, min(255, int(br + (ar - br) * ratio))),
        max(0, min(255, int(bg + (ag - bg) * ratio))),
        max(0, min(255, int(bb + (ab - bb) * ratio))),
    )


def _complement(hex_colour: str) -> str:
    r, g, b = _hex_to_rgb(hex_colour)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h = (h + 0.5) % 1.0
    s = max(0.3, min(0.7, s))
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return _rgb_to_hex(int(r2 * 255), int(g2 * 255), int(b2 * 255))


def _rgba(hex_colour: str, opacity: float) -> str:
    r, g, b = _hex_to_rgb(hex_colour)
    return f"rgba({r},{g},{b},{opacity:.2f})"


ACCENT_GREEN = "#2d7a3a"


# ──────────────────────────────────────────────
#  Deterministic Style Generation
# ──────────────────────────────────────────────

def _generate_tokens_from_primary(primary_hex: str) -> StyleTokens:
    """Derive complete style tokens from a single brochure primary colour.

    This is the core deterministic derivation — no LLM calls.
    """
    base_hex = primary_hex
    lum = _luminance(base_hex)
    is_light = lum > 0.55

    # Road colours
    road_dark = _darken(base_hex, factor=0.70)
    road_label_colour = _darken(base_hex, factor=0.90)

    # Park colours
    park_fill = _tint(base_hex, ACCENT_GREEN, 0.40)
    park_opacity = 0.45 if is_light else 0.55

    # Water colours
    water_stroke = _complement(base_hex)
    water_opacity = 0.70

    # Block (building footprint) colours
    block_fill = _darken(base_hex, factor=0.92)
    block_opacity = round((0.10 + 0.20) / 2, 2)

    if is_light:
        text_primary = _darken(base_hex, factor=0.15)
        text_secondary = _darken(base_hex, factor=0.35)
        text_stroke = _rgba(base_hex, 0.85)
        street_stroke = _lighten(base_hex, factor=0.18)
        street_label = _darken(base_hex, factor=0.30)
        poi_label = _darken(base_hex, factor=0.12)
        park_label = _darken(park_fill, factor=0.25)
        water_label = _darken(water_stroke, factor=0.40)
        neighbourhood_colour = text_primary
    else:
        text_primary = _lighten(base_hex, factor=0.85)
        text_secondary = _lighten(base_hex, factor=0.55)
        text_stroke = _rgba(base_hex, 0.85)
        street_stroke = _lighten(base_hex, factor=0.25)
        street_label = _lighten(base_hex, factor=0.55)
        poi_label = _lighten(base_hex, factor=0.80)
        park_label = _lighten(park_fill, factor=0.65)
        water_label = _lighten(water_stroke, factor=0.50)
        neighbourhood_colour = text_primary

    # Building marker uses original brand colour
    building_primary = primary_hex
    building_dark = _darken(primary_hex, factor=0.80)

    # Minor roads: residential/unclassified etc.
    # Major roads: trunk/primary/secondary/tertiary
    return StyleTokens(
        base_hex=base_hex,
        primary_hex=primary_hex,
        is_light_base=is_light,
        text_primary=text_primary,
        text_secondary=text_secondary,
        text_stroke=text_stroke,
        roads=RoadStyleTokens(
            minor_casing=road_dark,
            minor_fill="white",
            major_casing=road_dark,
            major_fill="white",
            minor_casing_width=5.5,
            minor_fill_width=3.5,
            major_casing_width=14.0,
            major_fill_width=10.0,
            minor_casing_opacity=0.50,
            minor_fill_opacity=0.28,
            major_casing_opacity=0.80,
            major_fill_opacity=0.50,
        ),
        labels=LabelStyleTokens(
            street_colour=street_label,
            street_halo=text_stroke,
            street_font_size=10.0,
            street_font_weight=600,
            street_letter_spacing=2.2,
            poi_colour=poi_label,
            poi_halo=text_stroke,
            poi_font_size=11.0,
            poi_font_weight=500,
            park_colour=park_label,
            water_colour=water_label,
            neighbourhood_colour=neighbourhood_colour,
            neighbourhood_opacity=0.10,
            label_halo_mode=LabelHaloMode.bbox_rect,
        ),
        markers=MarkerStyleTokens(
            building_fill=building_primary,
            building_stroke=building_dark,
            building_label_colour=text_primary,
            building_label_font_size=28.0,
            station_roundel_colour="#CC3333",
        ),
        features=FeatureStyleTokens(
            park_fill=park_fill,
            park_opacity=park_opacity,
            water_stroke=water_stroke,
            water_opacity=water_opacity,
            block_fill=block_fill,
            block_opacity=block_opacity,
        ),
    )


# ──────────────────────────────────────────────
#  Validation & Fixer Rules
# ──────────────────────────────────────────────

def _validate_and_fix(tokens: StyleTokens) -> tuple[StyleTokens, StyleValidationReport]:
    """Validate style tokens and apply fixer rules where possible."""
    fixes: list[str] = []
    warnings: list[str] = []
    data = tokens.model_dump()

    def _fix_hex(path: str, value: str) -> str:
        if not _HEX_RE.match(value) and not value.startswith("rgba(") and value != "white":
            fixed = "#808080"
            fixes.append(f"{path}: invalid colour {value!r} -> {fixed}")
            return fixed
        return value

    # Validate key hex fields
    for field in ("base_hex", "primary_hex", "text_primary", "text_secondary"):
        val = data.get(field, "")
        data[field] = _fix_hex(field, val)

    # Validate contrast: text_primary must contrast with base_hex
    base_lum = _luminance(data["base_hex"])
    text_lum = _luminance(data["text_primary"])
    contrast = abs(base_lum - text_lum)
    if contrast < 0.3:
        warnings.append(
            f"Low contrast between base ({data['base_hex']}) "
            f"and text_primary ({data['text_primary']}): {contrast:.2f}"
        )

    valid = len(fixes) == 0 and len(warnings) == 0
    report = StyleValidationReport(valid=valid, fixes_applied=fixes, warnings=warnings)

    if fixes:
        tokens = StyleTokens(**data)

    return tokens, report


# ──────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────

def generate_style(
    request: MapStyleGenerateRequestV1,
) -> MapStyleGenerateResponseV1:
    """Generate deterministic style tokens from a brochure primary colour.

    Same inputs + same STYLE_VERSION always produce identical tokens.
    """
    primary = request.brochure_primary_hex

    try:
        tokens = _generate_tokens_from_primary(primary)
    except Exception as e:
        raise StyleValidationError(
            detail=f"Failed to derive style tokens from {primary!r}: {e}"
        )

    tokens, report = _validate_and_fix(tokens)
    style_hash = tokens.compute_hash()

    return MapStyleGenerateResponseV1(
        style_tokens=tokens,
        style_version=STYLE_VERSION,
        style_hash=style_hash,
        validation_report=report,
    )
