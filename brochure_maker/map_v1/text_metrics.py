"""Deterministic text metrics shared by label layout and SVG rendering."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont

from .config import MAP_V1_1_ENABLE_STRICT, MAP_V1_FONTCONFIG_FILE
from .errors import FontMissingError

PX_PER_PT = 96.0 / 72.0


@dataclass(frozen=True)
class TextMeasureInput:
    font_family: str
    weight: str
    size_pt: float
    tracking: float
    text: str


@dataclass(frozen=True)
class TextMeasureResult:
    width_px: float
    height_px: float
    baseline_px: float
    halo_pad_px: float
    ascent_px: float
    descent_px: float


@dataclass(frozen=True)
class ResolvedFont:
    file: str
    family: str
    style: str


def _font_env() -> dict[str, str]:
    env = os.environ.copy()
    if MAP_V1_FONTCONFIG_FILE:
        env["FONTCONFIG_FILE"] = MAP_V1_FONTCONFIG_FILE
        env["FONTCONFIG_PATH"] = str(Path(MAP_V1_FONTCONFIG_FILE).resolve().parent)
    return env


def _weight_token(weight: str | int | float) -> str:
    text = str(weight).strip().lower()
    if text in {"400", "normal", "regular"}:
        return "Regular"
    if text in {"500", "medium"}:
        return "Medium"
    if text in {"600", "semibold", "demibold"}:
        return "Semibold"
    if text in {"700", "bold"}:
        return "Bold"
    if text in {"800", "extrabold", "ultrabold"}:
        return "ExtraBold"
    if text in {"300", "light"}:
        return "Light"
    return "Regular"


@lru_cache(maxsize=256)
def resolve_font(font_family: str, weight: str) -> ResolvedFont:
    if not shutil.which("fc-match"):
        raise FontMissingError("fontconfig fc-match is required for map-v1 text metrics")

    weight_token = _weight_token(weight)
    query = f"{font_family}:style={weight_token}"
    proc = subprocess.run(
        ["fc-match", "-f", "%{file}\t%{family}\t%{style}\n", query],
        capture_output=True,
        text=True,
        check=False,
        env=_font_env(),
    )
    line = (proc.stdout or "").strip()
    if proc.returncode != 0 or not line:
        raise FontMissingError(
            f"could not resolve font family '{font_family}' (weight {weight_token})",
            hint="Install the brochure font and verify with `fc-match`.",
        )

    parts = line.split("\t")
    file_path = parts[0].strip() if parts else ""
    family = parts[1].strip() if len(parts) > 1 else font_family
    style = parts[2].strip() if len(parts) > 2 else weight_token
    if not file_path or not Path(file_path).exists():
        raise FontMissingError(f"resolved font file missing for '{font_family}'")
    return ResolvedFont(file=file_path, family=family, style=style)


@lru_cache(maxsize=256)
def _font_vertical_metrics(font_file: str) -> tuple[float, float, float, float]:
    """Return (units_per_em, ascent, descent, line_gap)."""
    path = Path(font_file)
    if not path.exists():
        raise FontMissingError(f"font file does not exist: {font_file}")

    if path.suffix.lower() == ".ttc":
        collection = TTCollection(font_file)
        tt = collection.fonts[0]
    else:
        tt = TTFont(font_file)

    units_per_em = float(tt["head"].unitsPerEm)
    hhea = tt["hhea"]
    ascent = float(hhea.ascent)
    descent = abs(float(hhea.descent))
    line_gap = float(hhea.lineGap)
    return units_per_em, ascent, descent, line_gap


def _text_to_unicodes(text: str) -> str:
    if not text:
        return ""
    return ",".join(f"{ord(ch):04X}" for ch in text)


def text_metrics_backend_name() -> str:
    if shutil.which("hb-shape"):
        return "harfbuzz_cli_v1"
    return "fallback_fonttools_v1"


def _shape_width_px(font_file: str, text: str, size_px: float, strict: bool) -> float:
    if not text:
        return 0.0

    hb = shutil.which("hb-shape")
    if not hb:
        if strict:
            raise FontMissingError("hb-shape is required in strict mode for deterministic metrics")
        return 0.0

    proc = subprocess.run(
        [
            hb,
            "--output-format=json",
            f"--font-size={size_px:.6f}",
            f"--unicodes={_text_to_unicodes(text)}",
            font_file,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_font_env(),
    )
    if proc.returncode != 0:
        if strict:
            stderr = (proc.stderr or "").strip()
            raise FontMissingError(
                f"hb-shape failed while measuring text",
                hint=f"Check font shaping stack (hb-shape/fontconfig). {stderr}",
            )
        return 0.0

    try:
        glyphs = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        if strict:
            raise FontMissingError("invalid hb-shape output while measuring text") from exc
        return 0.0

    width = 0.0
    for glyph in glyphs:
        try:
            width += float(glyph.get("ax", 0.0))
        except Exception:
            continue
    return width


def _fallback_width_px(font_file: str, text: str, size_px: float) -> float:
    """Fallback when harfbuzz is unavailable: sum advance widths from font tables."""
    if not text:
        return 0.0
    path = Path(font_file)
    if path.suffix.lower() == ".ttc":
        tt = TTCollection(font_file).fonts[0]
    else:
        tt = TTFont(font_file)

    units_per_em = float(tt["head"].unitsPerEm)
    cmap = tt.getBestCmap() or {}
    hmtx = tt["hmtx"].metrics
    total_units = 0.0
    for ch in text:
        code = ord(ch)
        glyph_name = cmap.get(code)
        if not glyph_name and "space" in hmtx:
            glyph_name = "space"
        if not glyph_name:
            continue
        advance = hmtx.get(glyph_name, (0, 0))[0]
        total_units += float(advance)

    return (total_units / units_per_em) * size_px


@lru_cache(maxsize=16384)
def _measure_cached(
    font_family: str,
    weight: str,
    size_pt: float,
    tracking: float,
    text: str,
    halo_width_pt: float,
    strict: bool,
) -> TextMeasureResult:
    resolved = resolve_font(font_family, weight)
    size_px = float(size_pt) * PX_PER_PT
    units_per_em, ascent_u, descent_u, line_gap_u = _font_vertical_metrics(resolved.file)

    ascent_px = (ascent_u / units_per_em) * size_px
    descent_px = (descent_u / units_per_em) * size_px
    line_gap_px = (line_gap_u / units_per_em) * size_px
    height_px = max(1.0, ascent_px + descent_px + line_gap_px)
    baseline_px = max(0.0, ascent_px)

    width_px = _shape_width_px(resolved.file, text, size_px, strict=strict)
    if width_px <= 0.0 and text:
        width_px = _fallback_width_px(resolved.file, text, size_px)

    if text and tracking:
        width_px += float(tracking) * max(0, len(text) - 1)

    halo_pad_px = max(0.0, float(halo_width_pt) * PX_PER_PT) + 1.0

    return TextMeasureResult(
        width_px=round(max(0.0, width_px), 4),
        height_px=round(height_px, 4),
        baseline_px=round(baseline_px, 4),
        halo_pad_px=round(halo_pad_px, 4),
        ascent_px=round(ascent_px, 4),
        descent_px=round(descent_px, 4),
    )


def measure_text(
    measure_input: TextMeasureInput,
    *,
    halo_width_pt: float = 0.0,
    strict: bool | None = None,
) -> TextMeasureResult:
    return _measure_cached(
        measure_input.font_family.strip(),
        _weight_token(measure_input.weight),
        float(measure_input.size_pt),
        float(measure_input.tracking),
        measure_input.text,
        float(halo_width_pt),
        MAP_V1_1_ENABLE_STRICT if strict is None else bool(strict),
    )


def resolve_label_font(label_class: str, font_family_sans: str, font_family_serif: str) -> tuple[str, str]:
    cls = (label_class or "").lower()
    if cls in {"building"}:
        return font_family_serif, "Bold"
    if cls in {"station", "transit"}:
        return font_family_serif, "Semibold"
    return font_family_sans, "Regular"


def label_bbox_from_baseline(x: float, y: float, metrics: TextMeasureResult) -> tuple[float, float, float, float]:
    left = x - metrics.halo_pad_px
    top = y - metrics.ascent_px - metrics.halo_pad_px
    right = x + metrics.width_px + metrics.halo_pad_px
    bottom = y + metrics.descent_px + metrics.halo_pad_px
    return (round(left, 4), round(top, 4), round(right, 4), round(bottom, 4))


def rounded_halo_rect(
    x: float,
    y: float,
    metrics: TextMeasureResult,
    *,
    radius_px: float = 3.0,
) -> tuple[float, float, float, float, float]:
    left, top, right, bottom = label_bbox_from_baseline(x, y, metrics)
    return (
        left,
        top,
        max(0.0, right - left),
        max(0.0, bottom - top),
        max(0.0, min(radius_px, 0.5 * min(right - left, bottom - top))),
    )
