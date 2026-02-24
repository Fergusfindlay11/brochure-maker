"""Text Metrics Subsystem.

Provides precise text measurement used by BOTH the label engine (for bbox
computation) and the SVG renderer (for consistent font declarations).

Attempts to use Pango/HarfBuzz for real shaping. Falls back to a calibrated
heuristic when Pango is not available (with a logged warning).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_HAS_PANGO = False

try:
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo  # type: ignore[attr-defined]

    _HAS_PANGO = True
    logger.info("Pango text metrics available — using real shaping.")
except (ImportError, ValueError):
    logger.warning(
        "Pango not available — using heuristic text metrics. "
        "Install PyGObject + Pango for precise measurements."
    )


@dataclass(frozen=True, slots=True)
class TextBBox:
    """Precise bounding box for a text run."""

    width_px: float
    height_px: float
    baseline_px: float
    halo_padding: float = 0.0

    @property
    def padded_width(self) -> float:
        return self.width_px + 2 * self.halo_padding

    @property
    def padded_height(self) -> float:
        return self.height_px + 2 * self.halo_padding


# ──────────────────────────────────────────────
#  Pango Backend
# ──────────────────────────────────────────────

def _measure_pango(
    text: str,
    font_family: str,
    weight: int,
    size_pt: float,
    letter_spacing: float = 0.0,
) -> TextBBox:
    """Measure text using Pango/HarfBuzz for precise shaping."""
    fm = PangoCairo.FontMap.get_default()
    ctx = fm.create_context()

    font_desc = Pango.FontDescription()
    font_desc.set_family(font_family)
    font_desc.set_weight(_pango_weight(weight))
    font_desc.set_absolute_size(size_pt * Pango.SCALE * 96.0 / 72.0)

    layout = Pango.Layout.new(ctx)
    layout.set_font_description(font_desc)
    layout.set_text(text, -1)

    if letter_spacing > 0:
        attrs = Pango.AttrList()
        spacing_attr = Pango.attr_letter_spacing_new(
            int(letter_spacing * Pango.SCALE)
        )
        attrs.insert(spacing_attr)
        layout.set_attributes(attrs)

    ink_rect, logical_rect = layout.get_pixel_extents()
    baseline = layout.get_baseline() / Pango.SCALE

    return TextBBox(
        width_px=logical_rect.width,
        height_px=logical_rect.height,
        baseline_px=baseline,
    )


def _pango_weight(css_weight: int) -> int:
    """Convert CSS font-weight to Pango weight enum value."""
    weight_map = {
        100: 100,  # THIN
        200: 200,  # ULTRALIGHT
        300: 300,  # LIGHT
        400: 400,  # NORMAL
        500: 500,  # MEDIUM
        600: 600,  # SEMIBOLD
        700: 700,  # BOLD
        800: 800,  # ULTRABOLD
        900: 900,  # HEAVY
    }
    return weight_map.get(css_weight, 400)


# ──────────────────────────────────────────────
#  Heuristic Backend (calibrated fallback)
# ──────────────────────────────────────────────

# Average character width as a fraction of font size, calibrated per category.
_CHAR_WIDTH_FACTORS: dict[str, float] = {
    "serif": 0.58,
    "sans": 0.52,
    "mono": 0.60,
}

# Height factor relative to font size.
_HEIGHT_FACTOR = 1.25
_BASELINE_FACTOR = 0.82


def _measure_heuristic(
    text: str,
    font_family: str,
    weight: int,
    size_pt: float,
    letter_spacing: float = 0.0,
) -> TextBBox:
    """Heuristic text measurement when Pango is not available."""
    category = _font_category(font_family)
    char_w = _CHAR_WIDTH_FACTORS.get(category, 0.55)

    # Bold text is ~5% wider
    if weight >= 700:
        char_w *= 1.05

    width = len(text) * size_pt * char_w + max(0, len(text) - 1) * letter_spacing
    height = size_pt * _HEIGHT_FACTOR
    baseline = size_pt * _BASELINE_FACTOR

    return TextBBox(
        width_px=round(width, 1),
        height_px=round(height, 1),
        baseline_px=round(baseline, 1),
    )


def _font_category(family: str) -> str:
    family_lower = family.lower()
    if any(kw in family_lower for kw in ("serif", "playfair", "georgia", "times")):
        if "sans" in family_lower:
            return "sans"
        return "serif"
    if any(kw in family_lower for kw in ("mono", "courier", "consolas")):
        return "mono"
    return "sans"


# ──────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────

def measure_text(
    text: str,
    font_family: str = "Jost",
    weight: int = 400,
    size_pt: float = 12.0,
    letter_spacing: float = 0.0,
    halo_width: float = 0.0,
) -> TextBBox:
    """Measure a text run and return its bounding box.

    Uses Pango if available, otherwise a calibrated heuristic.

    Args:
        text: The string to measure.
        font_family: CSS font-family name.
        weight: CSS font-weight (100-900).
        size_pt: Font size in points.
        letter_spacing: Extra spacing between characters in px.
        halo_width: If > 0, adds padding for a text halo/stroke.
    """
    if not text:
        return TextBBox(width_px=0, height_px=0, baseline_px=0, halo_padding=halo_width)

    if _HAS_PANGO:
        bbox = _measure_pango(text, font_family, weight, size_pt, letter_spacing)
    else:
        bbox = _measure_heuristic(text, font_family, weight, size_pt, letter_spacing)

    if halo_width > 0:
        return TextBBox(
            width_px=bbox.width_px,
            height_px=bbox.height_px,
            baseline_px=bbox.baseline_px,
            halo_padding=halo_width,
        )
    return bbox


def has_pango() -> bool:
    """Return True if the Pango backend is available."""
    return _HAS_PANGO
