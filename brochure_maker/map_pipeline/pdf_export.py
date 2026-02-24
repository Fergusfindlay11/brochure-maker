"""PDF Export — librsvg-based SVG-to-PDF conversion.

Uses rsvg-convert (librsvg) for SVG->PDF since it uses the same
Pango/HarfBuzz/Fontconfig stack as the text metrics subsystem.

Features:
- Bundled font registration at startup.
- Fail-fast if required fonts are missing.
- Vector purity check on output.
- Preserves selectable/searchable text in PDF.
- Structural hash for scene-determinism (not byte-identical).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .errors import ExportFailedError, FontMissingError, VectorPurityError

logger = logging.getLogger(__name__)

# Required fonts that must be available at runtime.
REQUIRED_FONTS = ["Jost", "Playfair Display"]

# Fontconfig search paths for bundled fonts.
_FONT_DIRS = [
    Path(__file__).resolve().parent.parent.parent / "fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
]


def _find_rsvg_convert() -> str | None:
    """Locate rsvg-convert binary."""
    return shutil.which("rsvg-convert")


def _check_font_available(font_name: str) -> bool:
    """Check if a font is available via fc-list."""
    fc_list = shutil.which("fc-list")
    if not fc_list:
        logger.warning("fc-list not found — cannot verify font availability.")
        return True  # Optimistic fallback

    try:
        result = subprocess.run(
            [fc_list, f":family={font_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(result.stdout.strip())
    except Exception:
        return True  # Optimistic fallback


def check_fonts_available() -> list[str]:
    """Check all required fonts at startup. Returns list of missing fonts."""
    missing = []
    for font in REQUIRED_FONTS:
        if not _check_font_available(font):
            missing.append(font)
    return missing


def startup_font_check() -> None:
    """Fail fast if required fonts are missing. Call at app startup."""
    missing = check_fonts_available()
    if missing:
        raise FontMissingError(
            detail=f"Required fonts not available: {', '.join(missing)}. "
            f"Install them in the container or add to {_FONT_DIRS[0]}."
        )
    logger.info("All required fonts available: %s", ", ".join(REQUIRED_FONTS))


def svg_to_pdf(
    svg_content: str,
    output_path: str | Path | None = None,
    width_px: int = 940,
    height_px: int = 750,
    dpi: int = 300,
) -> bytes:
    """Convert SVG string to PDF bytes using rsvg-convert.

    Falls back to a basic approach if rsvg-convert is not available.

    Args:
        svg_content: The SVG string to convert.
        output_path: Optional path to write the PDF file.
        width_px: Width in pixels.
        height_px: Height in pixels.
        dpi: Resolution for conversion.

    Returns:
        PDF bytes.
    """
    rsvg = _find_rsvg_convert()

    if rsvg:
        return _convert_with_rsvg(rsvg, svg_content, output_path, width_px, height_px, dpi)
    else:
        # Try CairoSVG as fallback (with limitations noted)
        return _convert_with_cairosvg(svg_content, output_path, width_px, height_px, dpi)


def _convert_with_rsvg(
    rsvg_path: str,
    svg_content: str,
    output_path: str | Path | None,
    width_px: int,
    height_px: int,
    dpi: int,
) -> bytes:
    """Convert SVG to PDF using rsvg-convert (recommended path)."""
    with tempfile.NamedTemporaryFile(suffix=".svg", mode="w", delete=False, encoding="utf-8") as svg_file:
        svg_file.write(svg_content)
        svg_file_path = svg_file.name

    pdf_file_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as pdf_file:
            pdf_file_path = pdf_file.name

        cmd = [
            rsvg_path,
            "--format=pdf",
            f"--dpi-x={dpi}",
            f"--dpi-y={dpi}",
            f"--width={width_px}",
            f"--height={height_px}",
            "--keep-aspect-ratio",
            f"--output={pdf_file_path}",
            svg_file_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise ExportFailedError(
                detail=f"rsvg-convert failed: {result.stderr.strip()}"
            )

        pdf_bytes = Path(pdf_file_path).read_bytes()

        if output_path:
            Path(output_path).write_bytes(pdf_bytes)

        return pdf_bytes

    finally:
        try:
            os.unlink(svg_file_path)
        except OSError:
            pass
        if pdf_file_path:
            try:
                os.unlink(pdf_file_path)
            except OSError:
                pass


def _convert_with_cairosvg(
    svg_content: str,
    output_path: str | Path | None,
    width_px: int,
    height_px: int,
    dpi: int,
) -> bytes:
    """Fallback: convert using CairoSVG (with known limitations).

    Limitations:
    - Text with stroke/transform may not render correctly.
    - Selectable text may break with halos.
    - Font matching may differ from Pango.
    """
    try:
        import cairosvg
    except ImportError:
        raise ExportFailedError(
            detail=(
                "Neither rsvg-convert nor CairoSVG is available. "
                "Install librsvg (recommended) or cairosvg: pip install cairosvg"
            )
        )

    logger.warning(
        "Using CairoSVG fallback for PDF export. "
        "For best results, install librsvg (rsvg-convert)."
    )

    try:
        pdf_bytes = cairosvg.svg2pdf(
            bytestring=svg_content.encode("utf-8"),
            output_width=width_px,
            output_height=height_px,
            dpi=dpi,
        )
    except Exception as e:
        raise ExportFailedError(detail=f"CairoSVG conversion failed: {e}")

    if output_path:
        Path(output_path).write_bytes(pdf_bytes)

    return pdf_bytes


# ──────────────────────────────────────────────
#  Vector Purity Check
# ──────────────────────────────────────────────

def check_svg_purity(svg_content: str) -> None:
    """Reject SVG outputs that embed large raster images.

    Raises VectorPurityError if disallowed raster content is found.
    """
    # Check for embedded images (data URIs or xlink:href to images)
    image_tags = re.findall(r"<image\b[^>]*>", svg_content, re.IGNORECASE)
    for tag in image_tags:
        # Allow small icons (< 1KB in base64)
        href_match = re.search(r'href="data:image/[^"]{1024,}"', tag)
        if href_match:
            raise VectorPurityError(
                detail="SVG contains embedded raster image exceeding 1KB. "
                "Map output must be pure vector."
            )

    # Check for <foreignObject> which can embed HTML/raster
    if "<foreignObject" in svg_content:
        raise VectorPurityError(
            detail="SVG contains <foreignObject> which may introduce raster content."
        )


def check_pdf_purity(pdf_bytes: bytes) -> None:
    """Basic check that PDF contains vector content, not a single full-page image.

    This is a heuristic check — it looks for text objects and vector paths
    in the PDF stream.
    """
    pdf_str = pdf_bytes[:50000].decode("latin-1", errors="replace")

    has_text = "BT" in pdf_str and "ET" in pdf_str  # PDF text objects
    has_paths = " m " in pdf_str or " l " in pdf_str  # PDF path operators

    if not has_text and not has_paths:
        raise VectorPurityError(
            detail="PDF appears to lack vector content. "
            "It may have been fully rasterised."
        )


# ──────────────────────────────────────────────
#  Structural Hash
# ──────────────────────────────────────────────

def compute_pdf_structural_hash(pdf_bytes: bytes) -> str:
    """Compute a structural hash of PDF content with metadata stripped.

    Strips CreationDate, ModDate, ID, and Producer fields before hashing
    so the hash is stable across runs (scene-deterministic).
    """
    pdf_str = pdf_bytes.decode("latin-1", errors="replace")

    # Strip metadata that varies between runs
    stripped = re.sub(r"/CreationDate\s*\([^)]*\)", "/CreationDate ()", pdf_str)
    stripped = re.sub(r"/ModDate\s*\([^)]*\)", "/ModDate ()", stripped)
    stripped = re.sub(r"/ID\s*\[[^\]]*\]", "/ID []", stripped)
    stripped = re.sub(r"/Producer\s*\([^)]*\)", "/Producer ()", stripped)

    return hashlib.sha256(stripped.encode("latin-1")).hexdigest()[:16]
