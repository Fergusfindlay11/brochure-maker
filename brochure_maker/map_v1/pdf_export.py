"""PDF export utilities for map-v1."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .config import (
    MAP_V1_1_ENABLE_STRICT,
    MAP_V1_FONTCONFIG_FILE,
    MAP_V1_FONT_DIR,
    MAP_V1_REQUIRED_FONTS,
    MAP_V1_RSVG_BIN,
)
from .errors import ExportError, FontMissingError

logger = logging.getLogger(__name__)


def _font_env() -> dict[str, str]:
    env = os.environ.copy()
    if MAP_V1_FONTCONFIG_FILE:
        env["FONTCONFIG_FILE"] = MAP_V1_FONTCONFIG_FILE
        env["FONTCONFIG_PATH"] = os.path.dirname(MAP_V1_FONTCONFIG_FILE) or "."
    if MAP_V1_FONT_DIR:
        existing = env.get("FONT_PATH", "")
        env["FONT_PATH"] = f"{MAP_V1_FONT_DIR}:{existing}" if existing else MAP_V1_FONT_DIR
    return env


def list_installed_fonts() -> set[str]:
    if not shutil.which("fc-list"):
        raise FontMissingError("fontconfig (fc-list) is required for map-v1 rendering")

    proc = subprocess.run(
        ["fc-list", ":", "family"],
        capture_output=True,
        text=True,
        check=False,
        env=_font_env(),
    )
    if proc.returncode != 0:
        raise FontMissingError("failed to query installed fonts via fc-list")

    fonts: set[str] = set()
    for line in proc.stdout.splitlines():
        for family in line.split(","):
            name = family.strip()
            if name:
                fonts.add(name)
    return fonts


def missing_required_fonts(required_fonts: list[str] | None = None) -> list[str]:
    required = required_fonts or MAP_V1_REQUIRED_FONTS
    installed = list_installed_fonts()
    missing = []
    for wanted in required:
        if not any(wanted.lower() == inst.lower() for inst in installed):
            missing.append(wanted)
    return missing


def assert_required_fonts(required_fonts: list[str] | None = None) -> None:
    missing = missing_required_fonts(required_fonts)
    if missing:
        raise FontMissingError("required map-v1 fonts missing: " + ", ".join(missing))


def _rsvg_binary() -> str | None:
    if os.path.sep in MAP_V1_RSVG_BIN:
        return MAP_V1_RSVG_BIN if os.path.exists(MAP_V1_RSVG_BIN) else None
    return shutil.which(MAP_V1_RSVG_BIN)


def _rsvg_version(binary: str) -> str:
    proc = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False, env=_font_env())
    if proc.returncode != 0:
        raise ExportError("rsvg-convert exists but --version failed")
    return (proc.stdout or proc.stderr or "").strip()


def assert_pdf_runtime_ready(strict: bool | None = None) -> None:
    strict_mode = MAP_V1_1_ENABLE_STRICT if strict is None else bool(strict)
    assert_required_fonts()
    rsvg = _rsvg_binary()
    if not rsvg:
        if strict_mode:
            raise ExportError("rsvg-convert binary not found")
        logger.warning("rsvg-convert not found; non-strict mode will use CairoSVG fallback")
        return

    version = _rsvg_version(rsvg)
    logger.info("map-v1 pdf backend: %s (%s)", rsvg, version)


def _svg_to_pdf_rsvg(svg: str, binary: str) -> bytes:
    proc = subprocess.run(
        [binary, "--format=pdf"],
        input=svg.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=_font_env(),
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise ExportError(f"rsvg-convert failed: {stderr or 'unknown error'}")
    if not proc.stdout:
        raise ExportError("rsvg-convert returned empty PDF output")
    return proc.stdout


def _svg_to_pdf_cairosvg(svg: str) -> bytes:
    brew_lib = "/opt/homebrew/lib"
    if os.path.isdir(brew_lib):
        existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if brew_lib not in existing.split(":"):
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = f"{brew_lib}:{existing}" if existing else brew_lib
    try:
        import cairosvg  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency error path
        raise ExportError("CairoSVG fallback unavailable and rsvg-convert is missing") from exc

    try:
        return cairosvg.svg2pdf(bytestring=svg.encode("utf-8"))
    except Exception as exc:
        raise ExportError(f"svg->pdf conversion failed via CairoSVG: {exc}") from exc


def svg_to_pdf_bytes(svg: str, required_fonts: list[str] | None = None, strict: bool | None = None) -> bytes:
    strict_mode = MAP_V1_1_ENABLE_STRICT if strict is None else bool(strict)
    assert_required_fonts(required_fonts)

    rsvg = _rsvg_binary()
    if rsvg:
        return _svg_to_pdf_rsvg(svg, rsvg)

    if strict_mode:
        raise ExportError("rsvg-convert is required in strict mode")

    logger.warning("rsvg-convert not found; using CairoSVG fallback because strict mode is disabled")
    return _svg_to_pdf_cairosvg(svg)
