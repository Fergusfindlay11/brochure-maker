"""Generate high-fidelity editable HTML from a PDF's exact page layout."""

from __future__ import annotations

import html
import io
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import fitz

from brochure_maker.image_region_classifier import classify_page_image_regions, detect_space_plan_bbox
from brochure_maker.ocr_text import extract_ocr_text_lines
from brochure_maker.pdf_vector_overlay import extract_page_svg_paths


PDFTOHTML_ZOOM = 4 / 3

TYPOGRAPHY_ROLE_LABELS: dict[str, str] = {
    "cover-title": "Title font",
    "section-heading": "Heading font",
    "body": "Body font",
    "caption": "Caption font",
    "table-status": "Table/status font",
    "agent-contact": "Agent/contact font",
}

TYPOGRAPHY_ROLE_VARS: dict[str, str] = {
    "cover-title": "--exact-font-cover-title",
    "section-heading": "--exact-font-section-heading",
    "body": "--exact-font-body",
    "caption": "--exact-font-caption",
    "table-status": "--exact-font-table-status",
    "agent-contact": "--exact-font-agent-contact",
}

CONTACT_PHONE_RE = re.compile(
    r"(?:(?:M|T|P)\s*:\s*)?"
    r"(?:(?:\+\d{1,3})\s*)?"
    r"(?:\(\d+\)\s*)?"
    r"(?:0?\d[\d\s]{5,}\d)",
    flags=re.IGNORECASE,
)


@lru_cache(maxsize=12)
def _cached_rgb_image(image_path: str) -> Any | None:
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            return image.convert("RGB").copy()
    except Exception:
        return None


def _load_rgb_image(image_path: Path | str | None) -> Any | None:
    if not image_path:
        return None
    return _cached_rgb_image(str(Path(image_path)))


EXACT_ICON_LIBRARY: dict[str, str] = {
    "refurbished": '<svg viewBox="0 0 96 72" fill="none"><path d="M10 58h76M18 58V20l18 16V20l18 16V20l24 18v20" stroke="currentColor" stroke-width="3"/><path d="M27 45h10M47 45h10M67 45h10" stroke="currentColor" stroke-width="3"/></svg>',
    "plug": '<svg viewBox="0 0 96 72" fill="none"><path d="M18 42h60M36 26v16M60 26v16" stroke="currentColor" stroke-width="3"/><path d="M31 42c0 14 34 14 34 0" stroke="currentColor" stroke-width="3"/><path d="M48 55v11" stroke="currentColor" stroke-width="3"/></svg>',
    "gym": '<svg viewBox="0 0 96 72" fill="none"><path d="M24 36h48M10 26v20M18 22v28M78 22v28M86 26v20" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>',
    "lift": '<svg viewBox="0 0 96 72" fill="none"><rect x="30" y="8" width="36" height="56" stroke="currentColor" stroke-width="3"/><path d="M48 16v40M39 27l9-9 9 9M39 45l9 9 9-9" stroke="currentColor" stroke-width="3"/></svg>',
    "shower": '<svg viewBox="0 0 96 72" fill="none"><path d="M26 26h44a8 8 0 0 1 8 8v4H18v-4a8 8 0 0 1 8-8Z" stroke="currentColor" stroke-width="3"/><path d="M48 26V10M58 26V10M28 48v14M40 48v14M52 48v14M64 48v14" stroke="currentColor" stroke-width="3" stroke-dasharray="5 6"/></svg>',
    "bike": '<svg viewBox="0 0 96 72" fill="none"><circle cx="26" cy="50" r="14" stroke="currentColor" stroke-width="3"/><circle cx="70" cy="50" r="14" stroke="currentColor" stroke-width="3"/><path d="M26 50l18-26h14l12 26M44 24l18 26H26M58 24h13M42 18h12" stroke="currentColor" stroke-width="3"/></svg>',
    "kitchenette": '<svg viewBox="0 0 96 72" fill="none"><path d="M26 18h26a16 16 0 0 1 0 32H26V18Z" stroke="currentColor" stroke-width="3"/><path d="M52 24h10a10 10 0 0 1 0 20H52M32 30h12M32 38h12M28 56h34" stroke="currentColor" stroke-width="3"/></svg>',
    "aircon": '<svg viewBox="0 0 96 72" fill="none"><rect x="20" y="18" width="56" height="20" rx="8" stroke="currentColor" stroke-width="3"/><path d="M35 48c4 5-4 7 0 12M48 48c4 5-4 7 0 12M61 48c4 5-4 7 0 12M39 28h18" stroke="currentColor" stroke-width="3"/></svg>',
    "trunking": '<svg viewBox="0 0 96 72" fill="none"><rect x="18" y="20" width="60" height="30" stroke="currentColor" stroke-width="3"/><path d="M30 38h10M48 38h10M66 30h6v8h-6zM28 50v6M40 50v6M52 50v6M64 50v6" stroke="currentColor" stroke-width="3"/></svg>',
    "facade": '<svg viewBox="0 0 96 72" fill="none"><path d="M18 64V28h60v36M12 28h72M22 20h52M31 20V9c0-7 12-7 12 0v11M53 20V9c0-7 12-7 12 0v11M30 38h12v18H30V38ZM54 38h12v18H54V38Z" stroke="currentColor" stroke-width="3"/></svg>',
    "meeting": '<svg viewBox="0 0 96 72" fill="none"><path d="M34 62h28L56 38H40l-6 24Z" stroke="currentColor" stroke-width="3"/><circle cx="48" cy="18" r="8" stroke="currentColor" stroke-width="3"/><circle cx="25" cy="30" r="7" stroke="currentColor" stroke-width="3"/><circle cx="71" cy="30" r="7" stroke="currentColor" stroke-width="3"/><path d="M17 62l4-22h14M79 62l-4-22H61" stroke="currentColor" stroke-width="3"/></svg>',
    "fibre": '<svg viewBox="0 0 96 72" fill="none"><path d="M20 38c17-18 39-18 56 0M30 49c11-11 25-11 36 0M41 60c4-4 10-4 14 0" stroke="currentColor" stroke-width="3"/><circle cx="48" cy="64" r="3" fill="currentColor"/></svg>',
    "broom": '<svg viewBox="0 0 96 72" fill="none"><path d="M58 10L28 40M28 40l-8 20 24-7-16-13ZM61 10l9 9" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "gear": '<svg viewBox="0 0 96 72" fill="none"><circle cx="48" cy="36" r="12" stroke="currentColor" stroke-width="3"/><path d="M48 10v9M48 53v9M22 36h9M65 36h9M30 18l6 6M60 48l6 6M30 54l6-6M60 24l6-6" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>',
    "health": '<svg viewBox="0 0 96 72" fill="none"><rect x="24" y="12" width="48" height="48" rx="8" stroke="currentColor" stroke-width="3"/><path d="M48 24v24M36 36h24" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>',
    "apple": '<svg viewBox="0 0 96 72" fill="none"><path d="M48 25c-16-14-34 0-26 25 5 15 15 15 26 8 11 7 21 7 26-8 8-25-10-39-26-25Z" stroke="currentColor" stroke-width="3"/><path d="M50 23c2-9 8-14 18-14M48 24V12" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>',
    "leaf": '<svg viewBox="0 0 96 72" fill="none"><path d="M48 62C48 38 66 17 82 10c-1 24-14 44-34 52ZM48 62C48 38 30 17 14 10c1 24 14 44 34 52Z" stroke="currentColor" stroke-width="3"/><path d="M48 62V18" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>',
    "restaurant": '<svg viewBox="0 0 72 72" fill="none"><path d="M20 10v24M28 10v24M20 24h8M24 34v28M50 10v52M44 10c-5 10-5 22 6 28" stroke="currentColor" stroke-width="3"/></svg>',
    "coffee": '<svg viewBox="0 0 72 72" fill="none"><path d="M18 30h30v12a14 14 0 0 1-14 14h-2a14 14 0 0 1-14-14V30Z" stroke="currentColor" stroke-width="3"/><path d="M48 34h5a7 7 0 0 1 0 14h-5M24 18v-8M34 18v-8M44 18v-8" stroke="currentColor" stroke-width="3"/></svg>',
    "wine": '<svg viewBox="0 0 72 72" fill="none"><path d="M26 10h20v14c0 8-5 14-10 14S26 32 26 24V10ZM36 38v20M26 58h20" stroke="currentColor" stroke-width="3"/></svg>',
    "bed": '<svg viewBox="0 0 72 72" fill="none"><path d="M12 48h48V34a8 8 0 0 0-8-8H12v22ZM12 24v34M60 44v14M18 26v12h16" stroke="currentColor" stroke-width="3"/></svg>',
}


class ExactPdfLayoutError(RuntimeError):
    """Raised when exact PDF layout generation cannot complete."""


def _run_pdf_tool(cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run a PDF CLI with a real process-group timeout.

    Some damaged PDFs can leave Poppler helpers spinning after the parent call
    times out. Starting a new session lets us terminate the whole process group
    before falling back to PyMuPDF or reporting a bounded failure.
    """
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_pdf_tool(process)
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr) from exc
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


def _terminate_pdf_tool(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def prepare_exact_pdf_source(pdf_path: str | Path, project_dir: str | Path) -> tuple[Path, dict[str, Any]]:
    """Prepare a PDF for exact extraction while preserving the uploaded source.

    qpdf is good at normalising damaged page trees that can make Poppler or
    PyMuPDF spin on otherwise viewable brochures. The original upload remains
    `source.pdf`; this repaired copy is only the extraction input.
    """
    source_path = Path(pdf_path).expanduser().resolve()
    project_path = Path(project_dir).expanduser().resolve()
    metadata: dict[str, Any] = {
        "input_pdf": str(source_path),
        "processed_pdf": str(source_path),
        "repair_tool": None,
        "repaired": False,
        "warnings": [],
    }
    qpdf = shutil.which("qpdf")
    if not qpdf:
        metadata["warnings"].append("qpdf not available; using uploaded PDF directly")
        return source_path, metadata

    repaired_path = project_path / "source.qpdf.pdf"
    cmd = [
        qpdf,
        "--warning-exit-0",
        "--object-streams=generate",
        str(source_path),
        str(repaired_path),
    ]
    try:
        result = _run_pdf_tool(cmd, timeout=45)
    except subprocess.TimeoutExpired:
        metadata["repair_tool"] = "qpdf"
        metadata["warnings"].append("qpdf timed out; using uploaded PDF directly")
        return source_path, metadata
    metadata["repair_tool"] = "qpdf"
    if result.stdout.strip():
        metadata["stdout"] = result.stdout.strip()[-4000:]
    if result.stderr.strip():
        metadata["stderr"] = result.stderr.strip()[-4000:]
    if result.returncode != 0 or not repaired_path.exists() or repaired_path.stat().st_size <= 0:
        metadata["warnings"].append("qpdf repair failed; using uploaded PDF directly")
        return source_path, metadata

    metadata["processed_pdf"] = str(repaired_path)
    metadata["repaired"] = True
    return repaired_path, metadata


def generate_exact_pdf_layout(
    pdf_path: str | Path,
    project_dir: str | Path,
    project_id: str,
    *,
    output_path: str | Path | None = None,
    force_source_preserve_vector_ops: bool = False,
) -> dict[str, Any]:
    """Convert a PDF into layered, editable, high-fidelity HTML.

    Poppler's ``pdftohtml`` gives us the best practical starting point for
    fidelity: raster backgrounds contain photos/vector artwork while text is
    emitted as absolutely positioned HTML. We then add editor affordances for
    text, image replacement, colour, logo, and page layout testing.
    """
    pdf_path = Path(pdf_path)
    project_dir = Path(project_dir)
    output_path = Path(output_path) if output_path else project_dir / "brochure.html"

    converter = shutil.which("pdftohtml")
    if not converter:
        raise ExactPdfLayoutError("pdftohtml is required for exact PDF layout import.")

    assets_dir = project_dir / "exact_assets"
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    font_css = _extract_fonts(pdf_path, assets_dir / "fonts", project_id)

    with tempfile.TemporaryDirectory(prefix="brochure_exact_") as tmp:
        tmp_dir = Path(tmp)
        target = tmp_dir / "exact.html"
        cmd = [
            converter,
            "-c",
            "-s",
            "-fontfullname",
            "-zoom",
            str(PDFTOHTML_ZOOM),
            str(pdf_path),
            str(target),
        ]
        try:
            result = _run_pdf_tool(cmd, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise ExactPdfLayoutError("pdftohtml timed out while extracting editable PDF layout") from exc
        if result.returncode != 0:
            raise ExactPdfLayoutError(result.stderr.strip() or "pdftohtml failed")

        html_files = sorted(tmp_dir.glob("*.html"))
        if not html_files:
            raise ExactPdfLayoutError("pdftohtml did not produce an HTML file")
        raw_html = html_files[0].read_text(encoding="utf-8", errors="replace")

        for image_path in sorted(tmp_dir.glob("*.png")):
            shutil.copy2(image_path, assets_dir / image_path.name)

    css, colour_roles, font_styles = _extract_poppler_css(raw_html)
    exact_model = _load_exact_layout_model(project_dir)
    exact_inventory = _load_exact_inventory(project_dir)
    pages = _extract_pages(
        raw_html,
        project_id,
        pdf_path,
        assets_dir,
        colour_roles,
        font_styles,
        exact_model=exact_model,
        exact_inventory=exact_inventory,
        force_source_preserve_vector_ops=force_source_preserve_vector_ops,
    )
    if not pages:
        raise ExactPdfLayoutError("No pages were extracted from pdftohtml output")

    page_width = max(page["width"] for page in pages)
    page_height = max(page["height"] for page in pages)
    _merge_model_image_regions(pages, exact_model)
    _supplement_model_schedule_text(pages)
    _merge_inventory_map_regions(pages, exact_inventory)
    _refresh_source_preserved_edit_after_region_merges(pages)
    cover_title_bboxes = _model_cover_title_bboxes(exact_model, pages)
    field_config = _build_structured_field_config(pages, cover_title_bboxes=cover_title_bboxes)
    inventory_theme = _theme_colours_from_inventory(exact_inventory)
    if inventory_theme:
        field_config["theme_colours"] = {**_theme_colours_from_pages(pages), **inventory_theme}
    _apply_default_structured_field_layouts(pages, field_config)
    rendered = _render_exact_html(
        project_id=project_id,
        page_width=page_width,
        page_height=page_height,
        pages=pages,
        poppler_css=css,
        font_css=font_css,
        field_config=field_config,
    )

    output_path.write_text(rendered, encoding="utf-8")
    return {
        "mode": "exact_pdf_layout",
        "page_count": len(pages),
        "page_width": page_width,
        "page_height": page_height,
        "text_count": sum(page["text_count"] for page in pages),
        "image_slot_count": sum(len(page["image_slots"]) for page in pages),
        "assets_dir": str(assets_dir),
        "output_path": str(output_path),
        "field_config": field_config,
    }


def _extract_fonts(pdf_path: Path, fonts_dir: Path, project_id: str) -> str:
    fonts_dir.mkdir(parents=True, exist_ok=True)
    rules: list[str] = []
    seen: set[tuple[str, str]] = set()

    doc = fitz.open(pdf_path)
    try:
        for page in doc:
            for font_info in page.get_fonts(full=True):
                xref = font_info[0]
                try:
                    font_name, ext, _font_type, font_bytes = doc.extract_font(xref)
                except Exception:
                    continue
                if not font_bytes or ext == "n/a":
                    continue

                family = str(font_name or font_info[3] or f"pdf-font-{xref}")
                key = (family, ext)
                if key in seen:
                    continue
                seen.add(key)

                safe_name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", family)
                output_ext = ext.lower()
                if output_ext == "cff":
                    converted = _wrap_cff_as_otf(font_bytes, safe_name)
                    if converted:
                        font_bytes = converted
                        output_ext = "otf"
                filename = f"{safe_name}.{output_ext}"
                (fonts_dir / filename).write_bytes(font_bytes)
                if not _font_has_browser_usable_cmap(font_bytes, output_ext):
                    rules.append(
                        f"/* Skipped embedded PDF font {safe_name}: no browser-usable Unicode cmap for editable text. */"
                    )
                    continue
                fmt = "truetype" if output_ext == "ttf" else "opentype"
                url = f"/api/projects/{project_id}/exact_assets/fonts/{filename}"
                family_names = [family]
                stable_family = _stable_font_family(family)
                if stable_family and stable_family != family:
                    family_names.append(stable_family)
                for css_family in family_names:
                    rules.append(
                        "@font-face { "
                        f"font-family: '{_css_escape_family(css_family)}'; "
                        f"src: url('{url}') format('{fmt}'); "
                        "font-display: swap; "
                        "}"
                    )
    finally:
        doc.close()

    return "\n".join(rules)


def _font_has_browser_usable_cmap(font_bytes: bytes, ext: str | None = None) -> bool:
    """Return whether a PDF font can safely render normal editable Unicode text.

    Some embedded PDF subsets are valid for the PDF renderer but expose no
    Unicode cmap to browsers. If we register those as ``@font-face`` rules, the
    DOM text is present but glyphs such as m, j, digits, or @ render blank. The
    CSS stack already contains stable and fallback families, so skipping the
    unsafe face is the least destructive reusable repair.
    """
    if (ext or "").lower() in {"n/a", ""}:
        return False
    try:
        from fontTools.ttLib import TTFont
    except Exception:
        return True
    try:
        font = TTFont(io.BytesIO(font_bytes), lazy=True)
        cmap = font.get("cmap")
        if not cmap or not getattr(cmap, "tables", None):
            return False
        codepoints: set[int] = set()
        for table in cmap.tables:
            codepoints.update(int(codepoint) for codepoint in table.cmap.keys())
        # Allow display subsets used for short headings, but reject fonts that
        # expose no ordinary Latin codepoints at all. Those are the subsets that
        # make DOM text present while common glyphs render blank.
        basic = set(ord(ch) for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@.")
        return len(codepoints.intersection(basic)) >= 3
    except Exception:
        return True


def _wrap_cff_as_otf(font_bytes: bytes, family: str) -> bytes | None:
    """Wrap a raw embedded CFF subset in an OpenType container for browsers."""
    try:
        from fontTools.agl import toUnicode
        from fontTools.cffLib import CFFFontSet
        from fontTools.fontBuilder import FontBuilder
        from fontTools.misc.psCharStrings import T2WidthExtractor
    except Exception:
        return None

    source = io.BytesIO(font_bytes)
    cff = CFFFontSet()
    try:
        cff.decompile(source, None)
        top = cff[cff.fontNames[0]]
        glyph_order = list(top.charset)
        if ".notdef" not in glyph_order:
            glyph_order.insert(0, ".notdef")

        cmap: dict[int, str] = {}
        for glyph_name in glyph_order:
            if glyph_name == ".notdef":
                continue
            try:
                unicode_value = toUnicode(glyph_name)
            except Exception:
                continue
            if len(unicode_value) == 1:
                cmap[ord(unicode_value)] = glyph_name

        units_per_em = 1000
        default_width = int(getattr(top.Private, "defaultWidthX", 552) or 552)
        local_subrs = getattr(top.Private, "Subrs", [])
        nominal_width = int(getattr(top.Private, "nominalWidthX", 0) or 0)
        metrics = {}
        extracted_widths: list[int] = []
        for glyph_name in glyph_order:
            width = default_width
            if glyph_name in top.CharStrings:
                try:
                    extractor = T2WidthExtractor(
                        local_subrs,
                        cff.GlobalSubrs,
                        nominal_width,
                        default_width,
                        private=top.Private,
                    )
                    extractor.execute(top.CharStrings[glyph_name])
                    width = int(extractor.width or default_width)
                except Exception:
                    width = default_width
            if glyph_name not in {".notdef", "space"} and width > 0:
                extracted_widths.append(width)
            metrics[glyph_name] = (width, 0)
        if "space" in metrics:
            metrics["space"] = (max(240, default_width // 2), 0)

        builder = FontBuilder(units_per_em, isTTF=False)
        builder.setupGlyphOrder(glyph_order)
        builder.setupCharacterMap(cmap)
        builder.setupHorizontalMetrics(metrics)
        builder.setupHorizontalHeader(ascent=900, descent=-250)
        builder.setupNameTable(
            {
                "familyName": family,
                "styleName": "Regular",
                "uniqueFontIdentifier": family,
                "fullName": family,
                "psName": re.sub(r"[^A-Za-z0-9-]+", "", family)[:63] or "PdfSubsetFont",
                "version": "Version 1.0",
            }
        )
        builder.setupOS2(
            sTypoAscender=900,
            sTypoDescender=-250,
            usWinAscent=900,
            usWinDescent=250,
        )
        builder.setupPost()
        char_strings = {glyph_name: top.CharStrings[glyph_name] for glyph_name in glyph_order if glyph_name in top.CharStrings}
        private: dict[str, Any] = {}
        for key in (
            "BlueValues",
            "OtherBlues",
            "FamilyBlues",
            "FamilyOtherBlues",
            "StdHW",
            "StdVW",
            "StemSnapH",
            "StemSnapV",
            "BlueScale",
            "BlueShift",
            "BlueFuzz",
            "LanguageGroup",
            "ExpansionFactor",
            "defaultWidthX",
            "nominalWidthX",
        ):
            if hasattr(top.Private, key):
                private[key] = getattr(top.Private, key)
        if extracted_widths:
            extracted_widths.sort()
            fallback_width = extracted_widths[len(extracted_widths) // 2]
            private["defaultWidthX"] = max(360, min(620, fallback_width))
            private["nominalWidthX"] = 0
        builder.setupCFF(
            re.sub(r"[^A-Za-z0-9-]+", "", family)[:63] or "PdfSubsetFont",
            {
                "FontBBox": getattr(top, "FontBBox", [-100, -250, 1000, 900]),
                "FontMatrix": getattr(top, "FontMatrix", [0.001, 0, 0, 0.001, 0, 0]),
            },
            char_strings,
            private,
        )
        output = io.BytesIO()
        builder.save(output)
        return output.getvalue()
    except Exception:
        return None


def _extract_poppler_css(raw_html: str) -> tuple[str, dict[str, str], dict[str, dict[str, Any]]]:
    style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", raw_html, flags=re.DOTALL | re.IGNORECASE)
    declarations: list[str] = []
    roles: dict[str, str] = {}
    font_styles: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()

    for block in style_blocks:
        block = block.replace("<!--", "").replace("-->", "")
        for match in re.finditer(r"(\.ft[0-9]+)\s*\{([^}]*)\}", block, flags=re.DOTALL):
            selector, body = match.groups()
            ft_class = selector[1:]
            font_styles[ft_class] = _parse_font_style_declarations(body)
            rule = f"{selector}{{{_normalise_font_fallbacks(body)}}}"
            if rule not in seen:
                seen.add(rule)
                declarations.append(rule)

            colour_match = re.search(r"color\s*:\s*(#[0-9a-fA-F]{6})", body)
            if colour_match:
                roles[ft_class] = _colour_role(colour_match.group(1))

    return "p{margin:0;padding:0;}\n" + "\n".join(declarations), roles, font_styles


def _extract_pages(
    raw_html: str,
    project_id: str,
    pdf_path: Path,
    assets_dir: Path,
    colour_roles: dict[str, str],
    font_styles: dict[str, dict[str, Any]],
    *,
    exact_model: dict[str, Any] | None = None,
    exact_inventory: dict[str, Any] | None = None,
    force_source_preserve_vector_ops: bool = False,
) -> list[dict[str, Any]]:
    doc = fitz.open(pdf_path)
    try:
        model_pages = _pages_by_number(exact_model)
        inventory_pages = _pages_by_number(exact_inventory)
        page_matches = list(
            re.finditer(
                r'<div id="page([0-9]+)-div" style="position:relative;width:([0-9]+)px;height:([0-9]+)px;">(.*?)</div>',
                raw_html,
                flags=re.DOTALL | re.IGNORECASE,
            )
        )

        pages: list[dict[str, Any]] = []
        for match in page_matches:
            page_num = int(match.group(1))
            width = int(match.group(2))
            height = int(match.group(3))
            inner = match.group(4)
            source_preserve_only = _should_source_preserve_pdf_page_ops(
                model_pages.get(page_num, {}),
                inventory_pages.get(page_num, {}),
            )
            skip_vector_ops = force_source_preserve_vector_ops or source_preserve_only
            full_background = _render_full_page_background(
                pdf_path,
                doc[page_num - 1],
                assets_dir,
                page_num,
                width,
                height,
            )
            body, text_count, text_entries = _prepare_page_inner(
                inner,
                page_num,
                project_id,
                colour_roles,
                full_background,
                font_styles,
                background_path=assets_dir / full_background,
            )
            if not text_entries:
                ocr_body, ocr_count, ocr_entries = _ocr_text_layer_for_page(
                    assets_dir / full_background,
                    page_num=page_num,
                    width=width,
                    height=height,
                    font_styles=font_styles,
                    start_index=text_count,
                )
                if ocr_entries:
                    body += "\n" + ocr_body
                    text_count += ocr_count
                    text_entries.extend(ocr_entries)
            raw_image_slots = (
                _image_slots_for_page(
                    doc[page_num - 1],
                    width,
                    height,
                    assets_dir,
                    project_id,
                    page_num,
                    assets_dir / full_background,
                )
                if page_num <= len(doc) and not skip_vector_ops
                else _model_image_slots_for_page(
                    model_pages.get(page_num, {}),
                    {"page_num": page_num, "width": width, "height": height},
                    assets_dir / full_background,
                )
            )
            if page_num <= len(doc) and not skip_vector_ops:
                raw_image_slots.extend(
                    _decorative_vector_artwork_slots_for_page(
                        doc[page_num - 1],
                        width,
                        height,
                        assets_dir,
                        project_id,
                        page_num,
                        assets_dir / full_background,
                        text_entries,
                        raw_image_slots,
                    )
                )
            image_classification = classify_page_image_regions(
                page_number=page_num,
                width=width,
                height=height,
                text_entries=text_entries,
                image_slots=raw_image_slots,
                background_path=assets_dir / full_background,
            )
            image_slots = image_classification["image_slots"]
            image_regions = image_classification["image_regions"]
            image_only_page = _should_promote_full_page_rendered_image(
                width=width,
                height=height,
                text_entries=text_entries,
                image_regions=image_regions,
            )
            if image_only_page:
                image_slots, image_regions = _promote_full_page_rendered_image_slot(
                    page_num=page_num,
                    width=width,
                    height=height,
                    image_slots=image_slots,
                    image_regions=image_regions,
                    source_url=f"/api/projects/{project_id}/exact_assets/{full_background}",
                )
            source_image_marks = (
                _source_image_marks_for_page(
                    doc[page_num - 1],
                    width,
                    height,
                    assets_dir,
                    project_id,
                    page_num,
                )
                if page_num <= len(doc) and not skip_vector_ops
                else []
            )
            vector_layer = (
                _render_vector_layer(
                    doc[page_num - 1],
                    width,
                    height,
                    image_slots=image_slots,
                    background_path=assets_dir / full_background,
                    assets_dir=assets_dir,
                )
                if page_num <= len(doc) and not skip_vector_ops
                else ""
            )
            vector_path_count = vector_layer.count("<path ")
            source_preserved_edit = skip_vector_ops or _should_source_preserve_edit(
                vector_layer=vector_layer,
                image_regions=image_regions,
                text_entries=text_entries,
            )
            background_panels = _render_background_panels(
                width,
                height,
                assets_dir / full_background,
                image_slots,
            )
            dark_fill_stats = (
                _dark_vector_fill_stats(doc[page_num - 1])
                if page_num <= len(doc) and not skip_vector_ops
                else []
            )
            body = background_panels + vector_layer + body
            if image_only_page:
                body = _suppress_all_text_interaction(body, "full-page-rendered-image")
            body += "\n" + _render_image_slots(page_num, image_slots)
            if source_preserved_edit:
                body = _suppress_source_preserved_interaction_hotspots(body, width, height)
            pages.append(
                {
                    "page_num": page_num,
                    "width": width,
                    "height": height,
                    "body": body,
                    "text_count": text_count,
                    "text_entries": text_entries,
                    "image_slots": image_slots,
                    "image_regions": image_regions,
                    "source_image_marks": source_image_marks,
                    "dark_fill_stats": dark_fill_stats,
                    "background_path": str(assets_dir / full_background),
                    "source_preserved_edit": source_preserved_edit,
                    "image_only_page": image_only_page,
                    "vector_path_count": vector_path_count,
                }
            )
        return pages
    finally:
        doc.close()


def _pages_by_number(source: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not isinstance(source, dict):
        return {}
    pages = source.get("pages")
    if not isinstance(pages, list):
        return {}
    by_number: dict[int, dict[str, Any]] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        try:
            page_number = int(page.get("page_number") or page.get("page_num") or 0)
        except (TypeError, ValueError):
            page_number = 0
        if page_number > 0:
            by_number[page_number] = page
    return by_number


def _should_source_preserve_pdf_page_ops(model_page: dict[str, Any], inventory_page: dict[str, Any]) -> bool:
    """Avoid unsafe in-process vector/image PDF operations on dense plan pages."""
    features = {str(feature) for feature in (inventory_page.get("detected_features") or [])}
    purpose = _compact_text(str(inventory_page.get("page_purpose") or inventory_page.get("purpose") or ""))
    if inventory_page.get("space_plan_regions"):
        return True
    if inventory_page.get("map_regions"):
        return True
    if features.intersection({"space_plan", "floor_metadata", "map", "map_context", "transport_symbols"}):
        if features.intersection({"space_plan", "floor_metadata"}) or "floor" in purpose or "plan" in purpose:
            return True
    model_regions = [
        region
        for region in (model_page.get("image_regions") or [])
        if isinstance(region, dict)
    ]
    for region in model_regions:
        role = str(region.get("role") or region.get("type") or "")
        if role in {"space-plan", "floor-plan", "map"}:
            return True
    return False


def _model_image_slots_for_page(
    model_page: dict[str, Any],
    render_page: dict[str, Any],
    background_path: Path,
) -> list[dict[str, Any]]:
    """Create bounded replacement slots from model regions on unsafe PDFs."""
    if not isinstance(model_page, dict) or not background_path.is_file():
        return []
    roles = {"photo-region", "photo-grid", "hero-photo", "artwork-image"}
    page_num = int(render_page.get("page_num") or model_page.get("page_number") or 0)
    slots: list[dict[str, Any]] = []
    for index, region in enumerate(model_page.get("image_regions") or [], start=1):
        if not isinstance(region, dict):
            continue
        role = str(region.get("role") or region.get("type") or "")
        if role not in roles:
            continue
        scaled = _scale_model_image_region(region, model_page, render_page)
        bbox = _bbox_from_semantic_region(scaled)
        width = float(bbox.get("width") or 0)
        height = float(bbox.get("height") or 0)
        if width < 40 or height < 40:
            continue
        asset_url = _crop_source_region_asset(
            background_path,
            bbox,
            f"page{page_num:03d}-model-image-{len(slots) + 1:02d}.png",
        )
        if not asset_url:
            continue
        evidence = scaled.get("source_evidence") if isinstance(scaled.get("source_evidence"), dict) else {}
        slots.append(
            {
                **bbox,
                "id": str(scaled.get("id") or f"page-{page_num}-model-image-{index}"),
                "asset_url": asset_url,
                "mask_colour": _sample_mask_colour(background_path, bbox),
                "photo_score": 0.0,
                "fit": "cover",
                "image_role": role,
                "candidate_role": role,
                "semantic_confidence": scaled.get("confidence"),
                "source_evidence": {
                    **evidence,
                    "method": "exact-layout model region crop",
                    "reason": "PDF was repaired; avoided unsafe embedded image extraction",
                },
            }
        )
        if len(slots) >= 12:
            break
    return _dedupe_image_slots_by_bbox(slots)


def _prepare_page_inner(
    inner: str,
    page_num: int,
    project_id: str,
    colour_roles: dict[str, str],
    full_background: str,
    font_styles: dict[str, dict[str, Any]] | None = None,
    background_path: Path | None = None,
) -> tuple[str, int, list[dict[str, Any]]]:
    inner = re.sub(
        r'<img ([^>]*?)src="([^"]+)"([^>]*)/>',
        lambda m: _rewrite_background_image(m, project_id, full_background),
        inner,
        flags=re.IGNORECASE,
    )

    text_index = 0
    text_entries: list[dict[str, Any]] = []

    def replace_text(match: re.Match[str]) -> str:
        nonlocal text_index
        next_index = text_index + 1
        attrs, content = match.groups()
        class_match = re.search(r'class="([^"]+)"', attrs)
        classes = class_match.group(1).split() if class_match else []
        ft_class = next((cls for cls in classes if cls.startswith("ft")), "")
        role = colour_roles.get(ft_class, "body")
        font_style = dict((font_styles or {}).get(ft_class, {}))
        if "pdf-text" not in classes:
            classes.append("pdf-text")
        if class_match:
            attrs = attrs[: class_match.start()] + f'class="{" ".join(classes)}"' + attrs[class_match.end() :]
        else:
            attrs += f' class="{" ".join(classes)}"'

        save_id = f"exact-page{page_num}-text{next_index}"
        plain_text = _plain_text_from_html(content)
        typography_role = _detect_typography_role(page_num, next_index, plain_text, font_style)
        normalised_text = _normalise_schedule_table_text(plain_text, typography_role)
        if normalised_text != plain_text:
            plain_text = normalised_text
            content = _html_from_plain_text(plain_text)
            typography_role = _detect_typography_role(page_num, next_index, plain_text, font_style)
        if not _text_span_visible_on_rendered_page(background_path, attrs, plain_text, role, font_style):
            return ""
        text_index = next_index
        text_entries.append(
            {
                "save_id": save_id,
                "page_num": page_num,
                "text_index": text_index,
                "plain": plain_text,
                "ft_class": ft_class,
                "typography_role": typography_role,
                "font_style": font_style,
                "top": _style_number(attrs, "top"),
                "left": _style_number(attrs, "left"),
            }
        )
        return (
            f'<p {attrs} contenteditable="true" spellcheck="false" '
            f'data-save-id="{save_id}" data-slot-id="page-{page_num}-text-{text_index}" '
            f'data-typography-role="{html.escape(typography_role, quote=True)}" '
            f'{_typography_data_attrs(font_style)}'
            f'data-colour-role="{role}" data-plain-text="{html.escape(plain_text, quote=True)}" '
            f'data-original-html="{html.escape(content, quote=True)}">{content}</p>'
        )

    inner = re.sub(r"<p\s+([^>]*)>(.*?)</p>", replace_text, inner, flags=re.DOTALL | re.IGNORECASE)
    return inner, text_index, text_entries


def _ocr_text_layer_for_page(
    background_path: Path,
    *,
    page_num: int,
    width: int,
    height: int,
    font_styles: dict[str, dict[str, Any]] | None = None,
    start_index: int = 0,
) -> tuple[str, int, list[dict[str, Any]]]:
    lines = extract_ocr_text_lines(
        background_path,
        page_number=page_num,
        page_width=float(width),
        page_height=float(height),
    )
    if not lines:
        return "", 0, []

    chunks: list[str] = []
    entries: list[dict[str, Any]] = []
    for offset, line in enumerate(lines, start=1):
        text = str(line.get("text") or "").strip()
        bbox = line.get("bbox") if isinstance(line.get("bbox"), dict) else {}
        if not text or not bbox:
            continue
        index = start_index + len(entries) + 1
        save_id = f"exact-page{page_num}-text{index}"
        role = str(line.get("role") or "body")
        left = float(bbox.get("x") or 0)
        top = float(bbox.get("y") or 0)
        box_width = max(1.0, float(bbox.get("width") or 1))
        box_height = max(1.0, float(bbox.get("height") or 1))
        mask_colour = _sample_mask_colour(
            background_path,
            {"left": left, "top": top, "width": box_width, "height": box_height},
        )
        font_style = _ocr_font_style(role, line, font_styles or {})
        foreground_colour = _sample_ocr_foreground_colour(
            background_path,
            {"left": left, "top": top, "width": box_width, "height": box_height},
            mask_colour,
        )
        if foreground_colour:
            font_style["color"] = foreground_colour
        colour_role = _colour_role(str(font_style.get("color") or ""))
        style = _ocr_text_style(left, top, box_width, box_height, role, font_style, mask_colour)
        escaped_text = html.escape(text)
        entries.append(
            {
                "save_id": save_id,
                "page_num": page_num,
                "text_index": index,
                "plain": text,
                "ft_class": "",
                "typography_role": role,
                "font_style": font_style,
                "top": top,
                "left": left,
                "ocr_fallback": True,
                "mask_colour": mask_colour,
            }
        )
        chunks.append(
            f'<p class="pdf-text exact-ocr-text" style="{html.escape(style, quote=True)}" '
            f'contenteditable="true" spellcheck="false" data-ocr-fallback="true" '
            f'data-ocr-mask-colour="{html.escape(mask_colour, quote=True)}" '
            f'data-save-id="{save_id}" data-slot-id="page-{page_num}-text-{index}" '
            f'data-typography-role="{html.escape(role, quote=True)}" '
            f'{_typography_data_attrs(font_style)}'
            f'data-colour-role="{html.escape(colour_role, quote=True)}" '
            f'data-plain-text="{html.escape(text, quote=True)}" '
            f'data-original-html="{html.escape(escaped_text, quote=True)}">{escaped_text}</p>'
        )
    return "\n".join(chunks), len(entries), entries


def _ocr_font_style(
    role: str,
    line: dict[str, Any],
    font_styles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    preferred = _preferred_ocr_font(role, font_styles)
    font_size = max(10.0, float(line.get("font_size") or 0))
    colour = str(preferred.get("color") or "")
    if role in {"cover-title", "section-heading"}:
        colour = "var(--exact-accent)"
    return {
        **preferred,
        "font_size": f"{font_size:.2f}px",
        "font_size_px": font_size,
        "line_height": f"{max(font_size * 1.05, font_size + 2.0):.2f}px",
        "letter_spacing": "0.18em" if role == "cover-title" else preferred.get("letter_spacing", ""),
        "font_weight": preferred.get("font_weight") or ("700" if role in {"cover-title", "section-heading"} else ""),
        "color": colour,
    }


def _preferred_ocr_font(role: str, font_styles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    styles = [dict(style) for style in font_styles.values() if isinstance(style, dict)]
    if not styles:
        return {"source_font_family": "Arial", "stable_font_family": "Arial", "color": "#000000"}

    def score(style: dict[str, Any]) -> tuple[float, float]:
        family = str(style.get("stable_font_family") or style.get("source_font_family") or "").lower()
        size = float(style.get("font_size_px") or 0)
        bold = 1.0 if any(token in family for token in ("bold", "demibold", "medium", "heavy")) else 0.0
        if role in {"cover-title", "section-heading"}:
            return (bold, size)
        return (0.0 if bold else 1.0, -abs(size - 22.0))

    return sorted(styles, key=score, reverse=True)[0]


def _ocr_text_style(
    left: float,
    top: float,
    width: float,
    height: float,
    role: str,
    font_style: dict[str, Any],
    mask_colour: str = "#ffffff",
) -> str:
    family = _font_family_stack(str(font_style.get("stable_font_family") or font_style.get("source_font_family") or "Arial"))
    colour = str(font_style.get("color") or "inherit")
    declarations = {
        "position": "absolute",
        "top": _px(top),
        "left": _px(left),
        "width": _px(width + 12.0),
        "min-height": _px(height),
        "white-space": "nowrap",
        "font-family": family,
        "font-size": str(font_style.get("font_size") or f"{height:.2f}px"),
        "line-height": str(font_style.get("line_height") or f"{height:.2f}px"),
        "letter-spacing": str(font_style.get("letter_spacing") or ("0.18em" if role == "cover-title" else "0")),
        "font-weight": str(font_style.get("font_weight") or ("700" if role in {"cover-title", "section-heading"} else "400")),
        "color": colour,
        "--exact-ocr-mask-colour": mask_colour,
        "z-index": "7",
    }
    return ";".join(f"{key}:{value}" for key, value in declarations.items() if value) + ";"


def _typography_data_attrs(font_style: dict[str, Any]) -> str:
    attrs: list[str] = []
    mapping = [
        ("stable_font_family", "data-font-alias"),
        ("stable_font_family", "data-font-family"),
        ("source_font_family", "data-source-font-family"),
        ("font_size", "data-font-size"),
        ("line_height", "data-line-height"),
        ("letter_spacing", "data-letter-spacing"),
        ("font_weight", "data-font-weight"),
        ("font_style", "data-font-style"),
    ]
    for key, attr in mapping:
        value = font_style.get(key)
        if value not in (None, ""):
            attrs.append(f'{attr}="{html.escape(str(value), quote=True)}"')
    transform_scale = font_style.get("transform_scale")
    if transform_scale not in (None, ""):
        try:
            scale = float(transform_scale)
        except (TypeError, ValueError):
            scale = 0.0
        if scale > 0:
            attrs.append(f'data-pdf-transform-scale="{scale:.3f}"')
    return (" ".join(attrs) + " ") if attrs else ""


def _suppress_source_preserved_interaction_hotspots(body: str, page_width: int, page_height: int) -> str:
    """Make broken Poppler text fragments inert on source-preserved pages.

    Complex map/vector pages keep the rendered PDF as the visual truth. Some
    Poppler text classes on those pages contain extreme transform matrices,
    turning tiny street-label fragments into page-sized invisible editable
    hitboxes. They must not activate on hover/click; the source PDF render
    already carries their visual appearance.
    """

    page_area = max(1.0, float(page_width) * float(page_height))

    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        if "pdf-text" not in tag or 'contenteditable="true"' not in tag:
            return tag
        transform_scale = _html_attr_float(tag, "data-pdf-transform-scale")
        style = _html_attr_value(tag, "style")
        explicit_width = _style_value_px(style, "width")
        explicit_height = _style_value_px(style, "height")
        area_ratio = (
            (explicit_width * explicit_height) / page_area
            if explicit_width is not None and explicit_height is not None
            else 0.0
        )
        if transform_scale < 8.0 and area_ratio < 0.12:
            return tag
        return _mark_text_tag_interaction_suppressed(
            tag,
            "extreme-transform-hotspot" if transform_scale >= 8.0 else "oversized-source-preserved-hotspot",
        )

    return re.sub(r"<p\b[^>]*\bpdf-text\b[^>]*>", replace, body, flags=re.IGNORECASE)


def _suppress_all_text_interaction(body: str, reason: str) -> str:
    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        if "pdf-text" not in tag or 'contenteditable="true"' not in tag:
            return tag
        return _mark_text_tag_interaction_suppressed(tag, reason)

    return re.sub(r"<p\b[^>]*\bpdf-text\b[^>]*>", replace, body, flags=re.IGNORECASE)


def _mark_text_tag_interaction_suppressed(tag: str, reason: str) -> str:
    updated = re.sub(r'\bcontenteditable\s*=\s*"true"', 'contenteditable="false"', tag, count=1, flags=re.IGNORECASE)
    updated = _append_class_to_tag(updated, "exact-inert-pdf-text")
    if "data-interaction-suppressed" not in updated:
        updated = updated[:-1] + ' data-interaction-suppressed="true">'
    if "data-suppression-reason" not in updated:
        updated = updated[:-1] + f' data-suppression-reason="{html.escape(reason, quote=True)}">'
    if "aria-hidden" not in updated:
        updated = updated[:-1] + ' aria-hidden="true">'
    return updated


def _append_class_to_tag(tag: str, class_name: str) -> str:
    class_match = re.search(r'\bclass\s*=\s*"([^"]*)"', tag, flags=re.IGNORECASE)
    if not class_match:
        return tag[:-1] + f' class="{html.escape(class_name, quote=True)}">'
    classes = class_match.group(1).split()
    if class_name in classes:
        return tag
    classes.append(class_name)
    return tag[: class_match.start(1)] + " ".join(classes) + tag[class_match.end(1) :]


def _html_attr_value(tag: str, attr: str) -> str:
    match = re.search(rf'\b{re.escape(attr)}\s*=\s*"([^"]*)"', tag, flags=re.IGNORECASE)
    return html.unescape(match.group(1)) if match else ""


def _html_attr_float(tag: str, attr: str) -> float:
    value = _html_attr_value(tag, attr)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _style_value_px(style: str, prop: str) -> float | None:
    match = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)px", style or "", flags=re.I)
    return float(match.group(1)) if match else None


def _detect_typography_role(page_num: int, text_index: int, plain_text: str, font_style: dict[str, Any]) -> str:
    text = re.sub(r"\s+", " ", plain_text or "").strip()
    compact = re.sub(r"[^A-Za-z0-9@£]+", "", text).lower()
    family = str(font_style.get("stable_font_family") or font_style.get("source_font_family") or "").lower()
    size = float(font_style.get("font_size_px") or 0)

    if page_num == 1 and text and size >= 30:
        return "cover-title"
    if _looks_like_table_text(compact, page_num):
        return "table-status"
    if _looks_like_agent_text(text):
        return "agent-contact"
    if text.startswith("*") or "indicative purposes" in text.lower() or size <= 9:
        return "caption"
    if _looks_like_section_heading_candidate(text, family, font_style):
        return "section-heading"
    if size >= 30 and ("dala" in family or "heading" in family or "title" in family):
        return "section-heading"
    if size >= 40:
        return "section-heading"
    return "body"


def _looks_like_agent_text(text: str) -> bool:
    lower = text.lower()
    if "@" in text or re.search(r"\b0\d{3,4}\s?\d{3}\s?\d{3}\b", text):
        return True
    if _looks_like_contact_name_title_line(text):
        return True
    if any(token in lower for token in ("street", "station", "market", "cafe", "coffee", "restaurant", "fitness", "gym", "circle")):
        return False
    return bool(re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,2}", text.strip()))


def _looks_like_table_text(compact: str, page_num: int) -> bool:
    table_terms = {
        "floor",
        "sqft",
        "sqm",
        "status",
        "quotingrent",
        "servicecharge",
        "rates",
        "catb",
        "let",
        "letlet",
        "total",
        "g",
    }
    if compact in table_terms:
        return True
    if compact in {"statusquotingrent", "servicechargerates"}:
        return True
    if page_num in {2, 8} and re.fullmatch(r"\d{1,5}", compact):
        return True
    if page_num in {2, 8} and ("psf" in compact or compact.startswith("£")):
        return True
    return False


def _normalise_schedule_table_text(value: str, typography_role: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()
    if typography_role != "table-status" or not text:
        return text
    compact = _compact_text(text)
    replacements = {
        "floor": "FLOOR",
        "sqft": "SQ FT",
        "sqm": "SQ M",
        "status": "STATUS",
        "quotingrent": "QUOTING RENT",
        "statusquotingrent": "STATUS QUOTING RENT",
        "servicecharge": "SERVICE CHARGE",
        "rates": "RATES",
        "servicechargerates": "SERVICE CHARGE RATES",
        "catb": "CAT B",
    }
    return replacements.get(compact, text)


def _looks_like_section_heading_candidate(text: str, family: str, font_style: dict[str, Any]) -> bool:
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value or len(value) > 64:
        return False
    if "@" in value or re.search(r"\b0\d[\d\s]{8,}\b", value):
        return False
    size = float(font_style.get("font_size_px") or 0)
    if size < 28:
        return False
    words = re.findall(r"[A-Za-z0-9]+", value)
    if not words or len(words) > 5:
        return False
    lower_family = family.lower()
    weight = str(font_style.get("font_weight") or "").lower()
    is_bold = "bold" in lower_family or weight in {"bold", "600", "700", "800", "900"}
    if not is_bold:
        return False
    return True


def _plain_text_from_html(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _html_from_plain_text(value: str) -> str:
    return "<br/>".join(html.escape(line) for line in str(value or "").splitlines())


def _style_number(attrs: str, prop: str) -> float | None:
    match = re.search(rf"{re.escape(prop)}\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)px", attrs, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def _rewrite_background_image(match: re.Match[str], project_id: str, full_background: str) -> str:
    before, _src, after = match.groups()
    filename = Path(full_background).name
    url = f"/api/projects/{project_id}/exact_assets/{filename}"
    attrs = f"{before}{after}"
    if not re.search(r"\bdraggable\s*=", attrs, flags=re.IGNORECASE):
        attrs += ' draggable="false"'
    return f'<img class="pdf-bg" {attrs} src="{url}"/>'


def _text_span_visible_on_rendered_page(
    background_path: Path | None,
    attrs: str,
    plain_text: str,
    colour_role: str,
    font_style: dict[str, Any],
) -> bool:
    """Suppress PDF text objects that are occluded in the rendered source page.

    Some brochures include text objects beneath photo masks or later-drawn
    artwork. Poppler still exposes those spans, and rendering them as editable
    HTML places them on top of the page. A small rendered-page colour check
    lets us keep visible captions while dropping hidden/occluded text.
    """
    expected_colour = _expected_visible_text_colour(colour_role)
    if not background_path or not expected_colour or not plain_text.strip():
        return True
    top = _style_number(attrs, "top")
    left = _style_number(attrs, "left")
    if top is None or left is None:
        return True
    try:
        rgb = _load_rgb_image(background_path)
        if rgb is None:
            return True
        font_size = _font_style_size_px(font_style) or 12.0
        line_height = _font_style_line_height_px(font_style) or max(1.0, font_size * 1.2)
        lines = [line for line in plain_text.splitlines() if line.strip()] or [plain_text]
        longest = max((len(line) for line in lines), default=1)
        width = max(8.0, longest * font_size * 0.52)
        height = max(line_height, len(lines) * line_height)
        pad = max(2, int(font_size * 0.18))
        x0 = max(0, int(left) - pad)
        y0 = max(0, int(top) - pad)
        x1 = min(rgb.width, int(left + width) + pad)
        y1 = min(rgb.height, int(top + height) + pad)
        if x1 <= x0 or y1 <= y0:
            return True
        crop = rgb.crop((x0, y0, x1, y1))
        total = max(1, crop.width * crop.height)
        match_count = 0
        tolerance = 72 if colour_role == "light" else 64
        for pixel in crop.getdata():
            if _rgb_distance(pixel, expected_colour) <= tolerance:
                match_count += 1
        required = max(8, int(total * 0.0025))
        return match_count >= required
    except Exception:
        return True


def _expected_visible_text_colour(colour_role: str) -> tuple[int, int, int] | None:
    if colour_role == "accent":
        return (255, 234, 0)
    if colour_role == "light":
        return (255, 255, 255)
    if colour_role in {"dark", "body"}:
        return (51, 49, 50)
    return None


def _font_style_size_px(font_style: dict[str, Any]) -> float:
    value = font_style.get("font_size_px") or font_style.get("font_size")
    if isinstance(value, str):
        value = value.replace("px", "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _font_style_line_height_px(font_style: dict[str, Any]) -> float:
    value = font_style.get("line_height_px") or font_style.get("line_height")
    if isinstance(value, str):
        value = value.replace("px", "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _render_vector_layer(
    page: fitz.Page,
    css_width: int,
    css_height: int,
    *,
    image_slots: list[dict[str, Any]] | None = None,
    background_path: Path | None = None,
    assets_dir: Path | None = None,
) -> str:
    background_elements: list[str] = []
    overlay_elements: list[str] = []
    page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
    for vector_path in extract_page_svg_paths(page, precision=3, include_invisible=False):
        vector_for_style = dict(vector_path)
        inferred_opacity = _infer_vector_fill_opacity(
            page,
            vector_for_style,
            css_width,
            css_height,
            image_slots=image_slots or [],
            background_path=background_path,
            assets_dir=assets_dir,
            page_area=page_area,
        )
        if inferred_opacity is not None:
            vector_for_style["fill_opacity"] = inferred_opacity
        style = _vector_path_style(vector_for_style)
        if not style:
            continue
        bbox = vector_path.get("bbox") or {}
        bbox_attr = ",".join(
            f"{float(bbox.get(key, 0)):.3f}" for key in ("x", "y", "width", "height")
        )
        class_name = "pdf-vector-shape"
        try:
            area = float(bbox.get("width") or 0) * float(bbox.get("height") or 0)
        except (TypeError, ValueError):
            area = 0
        target = background_elements if area / page_area >= 0.88 else overlay_elements
        if target is background_elements:
            class_name += " pdf-vector-page-background"
        inferred_attr = (
            f' data-inferred-fill-opacity="{inferred_opacity:.3f}"'
            if inferred_opacity is not None
            else ""
        )
        target.append(
            f'<path class="{class_name}" d="{html.escape(vector_path["d"], quote=True)}" '
            f'data-vector-id="{html.escape(str(vector_path["id"]), quote=True)}" '
            f'data-bbox="{html.escape(bbox_attr, quote=True)}" '
            f'data-original-fill="{html.escape(_paint_hex(vector_path.get("fill")), quote=True)}" '
            f'data-original-stroke="{html.escape(_paint_hex(vector_path.get("stroke")), quote=True)}" '
            f'{inferred_attr} style="{style}"/>'
        )
    if not background_elements and not overlay_elements:
        return ""
    view_box = (
        f'viewBox="0 0 {float(page.rect.width):.3f} {float(page.rect.height):.3f}" '
        'preserveAspectRatio="none" aria-hidden="true"'
    )
    layers: list[str] = []
    if background_elements:
        layers.append(f'<svg class="pdf-vector-layer" {view_box}>' + "".join(background_elements) + "</svg>")
    if overlay_elements:
        layers.append(f'<svg class="pdf-vector-overlay-layer" {view_box}>' + "".join(overlay_elements) + "</svg>")
    return "".join(layers)


def _infer_vector_fill_opacity(
    page: fitz.Page,
    vector_path: dict[str, Any],
    css_width: int,
    css_height: int,
    *,
    image_slots: list[dict[str, Any]],
    background_path: Path | None,
    assets_dir: Path | None,
    page_area: float,
) -> float | None:
    """Recover missing alpha for PDF overlay rectangles by comparing against the rendered page.

    Some brochures encode translucent photo scrims as regular filled vector paths,
    but PyMuPDF can expose them without fill opacity. Comparing the rendered PDF
    pixels with the extracted photo underneath lets us infer the alpha without
    baking in project coordinates.
    """
    existing_opacity = vector_path.get("fill_opacity")
    if existing_opacity is not None:
        try:
            if float(existing_opacity) < 0.995:
                return None
        except (TypeError, ValueError):
            return None
    if not background_path or not assets_dir:
        return None
    fill = _paint_hex(vector_path.get("fill"))
    if _colour_role(fill) != "dark":
        return None
    bbox = vector_path.get("bbox") if isinstance(vector_path.get("bbox"), dict) else {}
    try:
        pdf_width = float(bbox.get("width") or 0)
        pdf_height = float(bbox.get("height") or 0)
        area = pdf_width * pdf_height
    except (TypeError, ValueError):
        return None
    if area / max(1.0, page_area) < 0.015 or area / max(1.0, page_area) >= 0.88:
        return None
    scale_x = css_width / max(1.0, float(page.rect.width))
    scale_y = css_height / max(1.0, float(page.rect.height))
    rect = {
        "left": float(bbox.get("x") or 0) * scale_x,
        "top": float(bbox.get("y") or 0) * scale_y,
        "width": pdf_width * scale_x,
        "height": pdf_height * scale_y,
    }
    if rect["width"] < 20 or rect["height"] < 20:
        return None
    slot = _best_image_slot_for_rect(rect, image_slots)
    if not slot:
        return None
    image_path = _asset_path_from_image_url(assets_dir, str(slot.get("asset_url") or ""))
    if not image_path or not image_path.exists():
        return None
    try:
        from PIL import Image

        rendered = _load_rgb_image(background_path)
        if rendered is None:
            return None
        with Image.open(image_path) as source_image:
            source = source_image.convert("RGB")
        rendered_crop = _crop_mean(rendered, rect)
        source_crop = _crop_mean_for_slot_source(source, slot, rect)
    except Exception:
        return None
    fill_rgb = _hex_to_rgb(fill)
    if not fill_rgb:
        return None
    alphas: list[float] = []
    for channel in range(3):
        denominator = fill_rgb[channel] - source_crop[channel]
        if abs(denominator) < 4:
            continue
        alpha = (rendered_crop[channel] - source_crop[channel]) / denominator
        if 0.02 <= alpha <= 0.98:
            alphas.append(alpha)
    if len(alphas) < 2:
        return None
    alphas.sort()
    alpha = alphas[len(alphas) // 2]
    if alpha >= 0.94:
        return None
    return round(max(0.04, min(0.92, alpha)), 3)


def _best_image_slot_for_rect(rect: dict[str, float], image_slots: list[dict[str, Any]]) -> dict[str, Any] | None:
    best_slot: dict[str, Any] | None = None
    best_score = 0.0
    rect_area = max(1.0, float(rect["width"]) * float(rect["height"]))
    for slot in image_slots:
        slot_rect = {
            "left": float(slot.get("left") or 0),
            "top": float(slot.get("top") or 0),
            "width": float(slot.get("width") or 0),
            "height": float(slot.get("height") or 0),
        }
        intersection = _rect_intersection_area(rect, slot_rect)
        if intersection <= 0:
            continue
        score = intersection / rect_area
        if score > best_score:
            best_score = score
            best_slot = slot
    return best_slot if best_score >= 0.45 else None


def _rect_intersection_area(a: dict[str, float], b: dict[str, float]) -> float:
    left = max(float(a["left"]), float(b["left"]))
    top = max(float(a["top"]), float(b["top"]))
    right = min(float(a["left"]) + float(a["width"]), float(b["left"]) + float(b["width"]))
    bottom = min(float(a["top"]) + float(a["height"]), float(b["top"]) + float(b["height"]))
    return max(0.0, right - left) * max(0.0, bottom - top)


def _asset_path_from_image_url(assets_dir: Path, asset_url: str) -> Path | None:
    marker = "/exact_assets/"
    if marker not in asset_url:
        return None
    relative = asset_url.split(marker, 1)[1].lstrip("/")
    return assets_dir / relative


def _crop_mean(image: Any, rect: dict[str, float]) -> tuple[float, float, float]:
    from PIL import ImageStat

    width, height = image.size
    left = max(0, min(width - 1, int(round(rect["left"]))))
    top = max(0, min(height - 1, int(round(rect["top"]))))
    right = max(left + 1, min(width, int(round(rect["left"] + rect["width"]))))
    bottom = max(top + 1, min(height, int(round(rect["top"] + rect["height"]))))
    mean = ImageStat.Stat(image.crop((left, top, right, bottom))).mean
    return float(mean[0]), float(mean[1]), float(mean[2])


def _crop_mean_for_slot_source(source: Any, slot: dict[str, Any], rect: dict[str, float]) -> tuple[float, float, float]:
    from PIL import ImageStat

    src_width, src_height = source.size
    slot_left = float(slot.get("left") or 0)
    slot_top = float(slot.get("top") or 0)
    slot_width = max(1.0, float(slot.get("width") or 1))
    slot_height = max(1.0, float(slot.get("height") or 1))
    scale = max(slot_width / src_width, slot_height / src_height)
    rendered_width = src_width * scale
    rendered_height = src_height * scale
    offset_x = slot_left + (slot_width - rendered_width) / 2
    offset_y = slot_top + (slot_height - rendered_height) / 2
    left = (float(rect["left"]) - offset_x) / scale
    top = (float(rect["top"]) - offset_y) / scale
    right = (float(rect["left"]) + float(rect["width"]) - offset_x) / scale
    bottom = (float(rect["top"]) + float(rect["height"]) - offset_y) / scale
    crop = (
        max(0, min(src_width - 1, int(round(left)))),
        max(0, min(src_height - 1, int(round(top)))),
        max(1, min(src_width, int(round(right)))),
        max(1, min(src_height, int(round(bottom)))),
    )
    if crop[2] <= crop[0]:
        crop = (crop[0], crop[1], min(src_width, crop[0] + 1), crop[3])
    if crop[3] <= crop[1]:
        crop = (crop[0], crop[1], crop[2], min(src_height, crop[1] + 1))
    mean = ImageStat.Stat(source.crop(crop)).mean
    return float(mean[0]), float(mean[1]), float(mean[2])


def _hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", value or "")
    if not match:
        return None
    raw = match.group(1)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _vector_path_style(vector_path: dict[str, Any]) -> str:
    fill = _svg_paint(vector_path.get("fill"), "fill", vector_path.get("fill_opacity"))
    stroke = _svg_paint(vector_path.get("stroke"), "stroke", vector_path.get("stroke_opacity"))
    width = vector_path.get("stroke_width")
    styles: list[str] = []
    styles.append(f"fill:{fill};" if fill else "fill:none;")
    if fill and vector_path.get("fill_opacity") is not None and float(vector_path["fill_opacity"]) < 0.995:
        styles.append(f"fill-opacity:{float(vector_path['fill_opacity']):.3f};")
    if stroke and width is not None and float(width) > 0:
        styles.append(f"stroke:{stroke};")
        styles.append(f"stroke-width:{float(width):.3f};")
        if vector_path.get("stroke_opacity") is not None and float(vector_path["stroke_opacity"]) < 0.995:
            styles.append(f"stroke-opacity:{float(vector_path['stroke_opacity']):.3f};")
    else:
        styles.append("stroke:none;")
    line_join = vector_path.get("line_join")
    if line_join:
        styles.append(f"stroke-linejoin:{line_join};")
    line_cap = vector_path.get("line_cap")
    if line_cap:
        styles.append(f"stroke-linecap:{line_cap};")
    fill_rule = vector_path.get("fill_rule")
    if fill_rule:
        styles.append(f"fill-rule:{fill_rule};")
    return "".join(styles)


def _dark_vector_fill_stats(page: fitz.Page) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    for vector_path in extract_page_svg_paths(page, precision=3, include_invisible=False):
        fill = _paint_hex(vector_path.get("fill"))
        if not fill or _colour_role(fill) != "dark":
            continue
        bbox = vector_path.get("bbox") if isinstance(vector_path.get("bbox"), dict) else {}
        try:
            area = float(bbox.get("width") or 0) * float(bbox.get("height") or 0)
        except (TypeError, ValueError):
            area = 0
        if area <= 0:
            continue
        stats.append({"colour": fill, "area": area})
    return stats


def _drawing_path_data(
    items: list[tuple[Any, ...]],
    scale_x: float,
    scale_y: float,
) -> tuple[str, list[tuple[float, float, float, float]]]:
    commands: list[str] = []
    rects: list[tuple[float, float, float, float]] = []
    current: tuple[float, float] | None = None

    def point(value: Any) -> tuple[float, float]:
        return float(value.x) * scale_x, float(value.y) * scale_y

    def move_to(x: float, y: float) -> None:
        nonlocal current
        if current != (x, y):
            commands.append(f"M{x:.3f} {y:.3f}")
            current = (x, y)

    for item in items:
        if not item:
            continue
        op = item[0]
        if op == "re" and len(item) >= 2:
            rect = item[1]
            rects.append(
                (
                    float(rect.x0) * scale_x,
                    float(rect.y0) * scale_y,
                    float(rect.width) * scale_x,
                    float(rect.height) * scale_y,
                )
            )
            continue
        if op == "l" and len(item) >= 3:
            start = point(item[1])
            end = point(item[2])
            move_to(*start)
            commands.append(f"L{end[0]:.3f} {end[1]:.3f}")
            current = end
            continue
        if op == "c" and len(item) >= 5:
            start = point(item[1])
            c1 = point(item[2])
            c2 = point(item[3])
            end = point(item[4])
            move_to(*start)
            commands.append(
                f"C{c1[0]:.3f} {c1[1]:.3f} "
                f"{c2[0]:.3f} {c2[1]:.3f} "
                f"{end[0]:.3f} {end[1]:.3f}"
            )
            current = end
            continue
        if op == "qu" and len(item) >= 2:
            quad = item[1]
            points = [point(p) for p in (quad.ul, quad.ur, quad.lr, quad.ll)]
            move_to(*points[0])
            commands.extend(f"L{x:.3f} {y:.3f}" for x, y in points[1:])
            commands.append("Z")
            current = points[0]
    return " ".join(commands), rects


def _drawing_style(drawing: dict[str, Any]) -> str:
    fill = _svg_paint(drawing.get("fill"), "fill", drawing.get("fill_opacity"))
    stroke = _svg_paint(drawing.get("color"), "stroke", drawing.get("stroke_opacity"))
    width = drawing.get("width")
    styles: list[str] = []
    if fill:
        styles.append(f"fill:{fill};")
    else:
        styles.append("fill:none;")
    if stroke:
        styles.append(f"stroke:{stroke};")
        if width is not None:
            styles.append(f"stroke-width:{float(width) * PDFTOHTML_ZOOM:.3f};")
        styles.append("vector-effect:non-scaling-stroke;")
    else:
        styles.append("stroke:none;")
    line_join = drawing.get("lineJoin")
    if line_join is not None:
        styles.append("stroke-linejoin:round;" if int(line_join) == 1 else "stroke-linejoin:miter;")
    line_cap = drawing.get("lineCap")
    if isinstance(line_cap, (list, tuple)) and line_cap:
        styles.append("stroke-linecap:round;" if int(line_cap[0]) == 1 else "stroke-linecap:butt;")
    return "".join(styles)


def _svg_paint(colour: Any, kind: str, opacity: Any = None) -> str:
    if not colour:
        return ""
    hex_colour = _paint_hex(colour)
    role = _colour_role(hex_colour)
    if role == "accent":
        value = "var(--exact-accent)"
    elif role == "dark":
        value = "var(--exact-dark)"
    else:
        value = hex_colour
    return value


def _paint_hex(colour: Any) -> str:
    if not colour:
        return ""
    if isinstance(colour, str):
        lower = colour.lower()
        return lower if lower.startswith("#") else lower
    return _rgb_tuple_to_hex(colour)


def _rgb_tuple_to_hex(colour: Any) -> str:
    red, green, blue = [max(0, min(255, round(float(channel) * 255))) for channel in colour[:3]]
    return f"#{red:02x}{green:02x}{blue:02x}"


def _render_full_page_background(
    pdf_path: Path,
    page: fitz.Page,
    assets_dir: Path,
    page_num: int,
    width: int,
    height: int,
) -> str:
    output = assets_dir / f"page{page_num:03d}-full.png"
    rendered = False
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        with tempfile.TemporaryDirectory(prefix="brochure_pdf_bg_") as tmp:
            prefix = Path(tmp) / "page"
            try:
                result = _run_pdf_tool(
                    [
                        pdftoppm,
                        "-png",
                        "-r",
                        "96",
                        "-f",
                        str(page_num),
                        "-l",
                        str(page_num),
                        str(pdf_path),
                        str(prefix),
                    ],
                    timeout=12,
                )
                candidates = sorted(Path(tmp).glob("page-*.png"))
                if result.returncode == 0 and candidates:
                    shutil.copy2(candidates[0], output)
                    rendered = True
            except subprocess.TimeoutExpired:
                rendered = False

    if not rendered:
        model_background = assets_dir.parent / "exact_layout_model" / "backgrounds" / f"page-{page_num:03d}.png"
        if model_background.exists():
            shutil.copy2(model_background, output)
            rendered = True

    if not rendered:
        pix = page.get_pixmap(
            matrix=fitz.Matrix(PDFTOHTML_ZOOM, PDFTOHTML_ZOOM),
            colorspace=fitz.csRGB,
            alpha=False,
        )
        pix.save(output)

    from PIL import Image

    with Image.open(output) as image:
        if image.width != width or image.height != height:
            resized = image.resize((width, height), Image.Resampling.LANCZOS)
            resized.save(output)

    return output.name


def _image_slots_for_page(
    page: fitz.Page,
    css_width: int,
    css_height: int,
    assets_dir: Path,
    project_id: str,
    page_num: int,
    background_path: Path,
) -> list[dict[str, Any]]:
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    scale_x = css_width / page_width
    scale_y = css_height / page_height
    slots: list[dict[str, Any]] = []
    images_dir = assets_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[dict[str, Any]] = []

    def add_candidate(
        bbox: tuple[float, float, float, float],
        image_bytes: bytes,
        ext: str,
        *,
        source_rank: int,
    ) -> None:
        x0, y0, x1, y1 = bbox
        width = (x1 - x0) * scale_x
        height = (y1 - y0) * scale_y
        css_bbox = {
            "left": round(x0 * scale_x, 2),
            "top": round(y0 * scale_y, 2),
            "width": round(width, 2),
            "height": round(height, 2),
        }
        photo_score = _image_photo_likelihood_score(image_bytes)
        page_coverage = (width * height) / max(1.0, float(css_width * css_height))
        if photo_score < 20.0 and page_coverage < 0.35:
            return
        candidates.append(
            {
                "pdf_bbox": bbox,
                "css_bbox": css_bbox,
                "image_bytes": image_bytes,
                "ext": ext,
                "area": width * height,
                "photo_score": photo_score,
                "source_rank": source_rank,
            }
        )

    for info in page.get_image_info(xrefs=True):
        bbox = info.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = [float(v) for v in bbox]
        width = (x1 - x0) * scale_x
        height = (y1 - y0) * scale_y
        if width < 90 or height < 90:
            continue
        xref = int(info.get("xref") or 0)
        extracted: dict[str, Any] = {}
        if xref:
            try:
                extracted = page.parent.extract_image(xref)
            except Exception:
                extracted = {}
        image_bytes = extracted.get("image")
        if not image_bytes or len(image_bytes) < 10_000:
            continue
        add_candidate((x0, y0, x1, y1), image_bytes, _safe_extension(extracted.get("ext", "png")), source_rank=1)

    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_IMAGES)
    for block in text_dict.get("blocks", []):
        if block.get("type") != 1:
            continue
        bbox = block.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = [float(v) for v in bbox]
        width = (x1 - x0) * scale_x
        height = (y1 - y0) * scale_y
        if width < 90 or height < 90:
            continue
        image_bytes = block.get("image")
        if not image_bytes or len(image_bytes) < 10_000:
            continue
        add_candidate((x0, y0, x1, y1), image_bytes, _safe_extension(block.get("ext", "png")), source_rank=0)

    seen: list[tuple[float, float, float, float]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-float(item["photo_score"]), int(item["source_rank"]), -float(item["area"])),
    ):
        pdf_bbox = candidate["pdf_bbox"]
        if _overlaps_existing(pdf_bbox, seen):
            continue
        seen.append(pdf_bbox)
        image_bytes = candidate["image_bytes"]
        ext = candidate["ext"]
        filename = f"page{page_num:03d}-image{len(slots) + 1:02d}.{ext}"
        (images_dir / filename).write_bytes(image_bytes)
        css_bbox = candidate["css_bbox"]
        slots.append(
            {
                **css_bbox,
                "asset_url": f"/api/projects/{project_id}/exact_assets/images/{filename}",
                "mask_colour": _sample_mask_colour(background_path, css_bbox),
                "tone_filter": _image_tone_filter(background_path, css_bbox, image_bytes),
                "photo_score": round(float(candidate["photo_score"]), 3),
            }
        )
    refined_slots = _refine_image_slots_with_raster_components(background_path, slots)
    refined_slots = [_trim_light_sidebar_from_slot(background_path, slot) for slot in refined_slots]
    return _dedupe_image_slots_by_bbox(refined_slots)


def _decorative_vector_artwork_slots_for_page(
    page: fitz.Page,
    css_width: int,
    css_height: int,
    assets_dir: Path,
    project_id: str,
    page_num: int,
    background_path: Path,
    text_entries: list[dict[str, Any]],
    existing_slots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose large cover/editorial vector artwork as a replaceable image slot.

    Some brochures, including Anchor House, draw the main visual artwork as
    filled PDF vectors rather than embedded images. The ordinary image extractor
    cannot see those regions, so this detector finds a large decorative vector
    block on sparse pages and crops the rendered PDF truth into an editable slot.
    """
    if not _page_is_sparse_decorative_artwork_context(text_entries):
        return []
    paths = extract_page_svg_paths(page, precision=3, include_invisible=False)
    bbox = _decorative_vector_artwork_bbox_from_paths(
        paths,
        page_width=float(page.rect.width),
        page_height=float(page.rect.height),
        css_width=float(css_width),
        css_height=float(css_height),
        existing_slots=existing_slots,
    )
    if not bbox:
        return []
    asset_url = _crop_decorative_artwork_asset(
        background_path,
        assets_dir=assets_dir,
        project_id=project_id,
        page_num=page_num,
        bbox=bbox,
        css_width=css_width,
        css_height=css_height,
    )
    if not asset_url:
        return []
    return [
        {
            **bbox,
            "id": f"page-{page_num}-vector-artwork-1",
            "asset_url": asset_url,
            "mask_colour": _sample_mask_colour(background_path, bbox),
            "photo_score": 0.0,
            "fit": "cover",
            "candidate_role": "artwork-image",
            "semantic_confidence": bbox.get("confidence", 0.86),
            "source_evidence": {
                "method": "large decorative PDF vector artwork crop",
                "reason": bbox.get("reason") or "large vector-art region on sparse cover/editorial page",
                "source_bbox": bbox.get("source_bbox"),
            },
        }
    ]


def _decorative_vector_artwork_bbox_from_paths(
    paths: list[dict[str, Any]],
    *,
    page_width: float,
    page_height: float,
    css_width: float,
    css_height: float,
    existing_slots: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    page_area = max(1.0, page_width * page_height)
    scale_x = css_width / max(1.0, page_width)
    scale_y = css_height / max(1.0, page_height)
    candidates: list[dict[str, Any]] = []
    for path in paths:
        if not isinstance(path, dict):
            continue
        bbox = path.get("bbox") if isinstance(path.get("bbox"), dict) else {}
        if not bbox:
            continue
        width = float(bbox.get("width") or 0)
        height = float(bbox.get("height") or 0)
        area_ratio = (width * height) / page_area
        if area_ratio < 0.12 or area_ratio > 0.88:
            continue
        if width < page_width * 0.35 or height < page_height * 0.22:
            continue
        if not path.get("fill") and not path.get("stroke"):
            continue
        css_bbox = {
            "left": round(float(bbox.get("x") or 0) * scale_x, 2),
            "top": round(float(bbox.get("y") or 0) * scale_y, 2),
            "width": round(width * scale_x, 2),
            "height": round(height * scale_y, 2),
        }
        if existing_slots and _overlaps_image_slots(css_bbox, existing_slots, threshold=0.35):
            continue
        candidates.append(
            {
                **css_bbox,
                "confidence": round(min(0.92, 0.72 + area_ratio * 0.22), 3),
                "area_ratio": round(area_ratio, 4),
                "source_bbox": bbox,
                "reason": "large non-page-background vector artwork region",
            }
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (float(item["area_ratio"]), float(item["width"]) * float(item["height"])))


def _page_is_sparse_decorative_artwork_context(text_entries: list[dict[str, Any]]) -> bool:
    texts = [str(entry.get("plain") or entry.get("text") or "").strip() for entry in text_entries if isinstance(entry, dict)]
    compact = re.sub(r"[^a-z0-9]+", "", " ".join(texts).lower())
    if not compact:
        return True
    blocked_tokens = (
        "station",
        "restaurants",
        "connectivity",
        "furtherinformation",
        "viewings",
        "floor",
        "sqft",
        "sqm",
        "amenities",
        "schedule",
        "accommodation",
    )
    if any(token in compact for token in blocked_tokens):
        return False
    meaningful_count = sum(1 for text in texts if len(re.sub(r"\W+", "", text)) >= 2)
    return meaningful_count <= 24


def _crop_decorative_artwork_asset(
    background_path: Path,
    *,
    assets_dir: Path,
    project_id: str,
    page_num: int,
    bbox: dict[str, Any],
    css_width: int,
    css_height: int,
) -> str | None:
    try:
        from PIL import Image

        with Image.open(background_path) as source:
            rgb = source.convert("RGB")
            scale_x = rgb.width / max(1.0, float(css_width))
            scale_y = rgb.height / max(1.0, float(css_height))
            left = int(max(0, round(float(bbox.get("left") or 0) * scale_x)))
            top = int(max(0, round(float(bbox.get("top") or 0) * scale_y)))
            right = int(min(rgb.width, round((float(bbox.get("left") or 0) + float(bbox.get("width") or 0)) * scale_x)))
            bottom = int(min(rgb.height, round((float(bbox.get("top") or 0) + float(bbox.get("height") or 0)) * scale_y)))
            if right <= left or bottom <= top:
                return None
            images_dir = assets_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            filename = f"page{page_num:03d}-artwork01.png"
            rgb.crop((left, top, right, bottom)).save(images_dir / filename)
        return f"/api/projects/{project_id}/exact_assets/images/{filename}"
    except Exception:
        return None


def _image_photo_likelihood_score(image_bytes: bytes) -> float:
    try:
        from PIL import Image, ImageStat

        with Image.open(io.BytesIO(image_bytes)) as image:
            sample = image.convert("RGB").resize((40, 40), Image.Resampling.LANCZOS)
            stat = ImageStat.Stat(sample)
            channel_std = sum(float(value) for value in stat.stddev[:3]) / 3.0
            pixels = list(sample.getdata())
            saturation = 0.0
            for red, green, blue in pixels:
                high = max(red, green, blue)
                low = min(red, green, blue)
                saturation += (high - low) / max(1, high)
            saturation /= max(1, len(pixels))
            unique_ratio = len(set(pixels)) / max(1, len(pixels))
            return channel_std + (saturation * 48.0) + (unique_ratio * 18.0)
    except Exception:
        return 0.0


def _dedupe_image_slots_by_bbox(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate replacement slots after raster bbox refinement.

    Native PDF extraction can expose both a decorative texture/background image
    and the actual photo for the same visual area. Raster refinement may then
    move the decorative candidate onto the visible photo region. Keep the more
    photo-like slot so the editor exposes the real replaceable picture.
    """
    seen: list[tuple[float, float, float, float]] = []
    kept: list[tuple[int, dict[str, Any]]] = []
    for index, slot in sorted(
        enumerate(slots),
        key=lambda item: (
            -float(item[1].get("photo_score") or 0),
            -(float(item[1].get("width") or 0) * float(item[1].get("height") or 0)),
            item[0],
        ),
    ):
        left = float(slot.get("left") or 0)
        top = float(slot.get("top") or 0)
        width = float(slot.get("width") or 0)
        height = float(slot.get("height") or 0)
        rect = (left, top, left + width, top + height)
        if _overlaps_existing(rect, seen):
            continue
        seen.append(rect)
        kept.append((index, slot))
    return [slot for _, slot in sorted(kept, key=lambda item: item[0])]


def _trim_light_sidebar_from_slot(background_path: Path, slot: dict[str, Any]) -> dict[str, Any]:
    """Trim photo replacement slots that include a separate light page rail."""
    try:
        rgb = _load_rgb_image(background_path)
        if rgb is None:
            return slot
        page_width, page_height = rgb.size
        left = float(slot.get("left") or 0)
        top = max(0, int(round(float(slot.get("top") or 0))))
        width = float(slot.get("width") or 0)
        height = float(slot.get("height") or 0)
        right = min(page_width, int(round(left + width)))
        bottom = min(page_height, int(round(float(slot.get("top") or 0) + height)))
        if width <= 0 or height <= 0 or right < page_width * 0.94 or bottom <= top:
            return slot
        scan_limit = max(int(round(left)), right - int(max(48, min(width * 0.28, 260))))
        rail_left: int | None = None
        for x in range(right - 1, scan_limit, -4):
            if _column_is_light_sidebar(rgb, x, top, bottom):
                rail_left = x
                continue
            if rail_left is not None:
                break
        if rail_left is None:
            return slot
        rail_width = right - rail_left
        if rail_width < 42 or rail_width > width * 0.28:
            return slot
        trimmed_width = rail_left - left
        if trimmed_width < width * 0.45:
            return slot
        updated = dict(slot)
        updated["width"] = round(trimmed_width, 2)
        updated["mask_colour"] = _sample_mask_colour(background_path, updated)
        updated["bbox_source"] = "light sidebar edge refinement"
        return updated
    except Exception:
        return slot


def _column_is_light_sidebar(image: Any, x: int, top: int, bottom: int) -> bool:
    if bottom <= top:
        return False
    step = max(1, (bottom - top) // 80)
    samples = [image.getpixel((x, y)) for y in range(top, bottom, step)]
    if not samples:
        return False
    light = 0
    low_saturation = 0
    for pixel in samples:
        red, green, blue = [int(value) for value in pixel[:3]]
        luma = _relative_luminance((red, green, blue))
        if luma >= 218:
            light += 1
        if max(red, green, blue) - min(red, green, blue) <= 28:
            low_saturation += 1
    total = len(samples)
    return (light / total) >= 0.74 and (low_saturation / total) >= 0.72


def _refine_image_slots_with_raster_components(background_path: Path, slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    components = _detect_photo_components(background_path)
    if not components or not slots:
        return slots
    slot_rects = [
        {key: float(slot.get(key) or 0) for key in ("left", "top", "width", "height")}
        for slot in slots
    ]
    used: set[int] = set()
    refined: list[dict[str, Any]] = []
    for slot in slots:
        slot_rect = {key: float(slot.get(key) or 0) for key in ("left", "top", "width", "height")}
        slot_area = max(1.0, slot_rect["width"] * slot_rect["height"])
        best_index: int | None = None
        best_component: dict[str, float] | None = None
        best_score = 0.0
        for index, component in enumerate(components):
            if index in used:
                continue
            comp_area = max(1.0, float(component["width"]) * float(component["height"]))
            overlap = _rect_intersection_area(slot_rect, component)
            if overlap <= 0:
                continue
            component_coverage = overlap / comp_area
            slot_coverage = overlap / slot_area
            if component_coverage < 0.55 or slot_coverage < 0.16:
                continue
            if _photo_component_spans_multiple_slots(component, slot_rect, slot_rects):
                continue
            size_similarity = min(slot_area / comp_area, comp_area / slot_area)
            score = component_coverage + (size_similarity * 0.35)
            if score > best_score:
                best_score = score
                best_index = index
                best_component = component
        if best_component and _image_component_is_better_bbox(slot_rect, best_component):
            updated = dict(slot)
            for key in ("left", "top", "width", "height"):
                updated[key] = round(float(best_component[key]), 2)
            updated["mask_colour"] = _sample_mask_colour(background_path, updated)
            image_bytes = _slot_image_bytes(background_path, updated)
            if image_bytes:
                updated["tone_filter"] = _image_tone_filter(background_path, updated, image_bytes)
            else:
                updated.pop("tone_filter", None)
            updated["bbox_source"] = "raster visible photo component"
            refined.append(updated)
            if best_index is not None:
                used.add(best_index)
        else:
            refined.append(slot)
    return refined


def _photo_component_spans_multiple_slots(
    component: dict[str, float],
    current_slot: dict[str, float],
    slot_rects: list[dict[str, float]],
) -> bool:
    """Reject merged raster clusters that cover several independently extracted photos.

    Raster thresholding can merge adjacent photos through labels, thin rules, or
    small non-background bridges. In that case the native PDF image bboxes are
    better evidence than expanding one slot over its neighbours.
    """
    comp_area = max(1.0, float(component["width"]) * float(component["height"]))
    current_overlap = _rect_intersection_area(current_slot, component)
    if current_overlap / comp_area >= 0.72:
        return False

    claimed_slots = 0
    for slot in slot_rects:
        slot_area = max(1.0, float(slot["width"]) * float(slot["height"]))
        overlap = _rect_intersection_area(slot, component)
        if overlap <= 0:
            continue
        slot_coverage = overlap / slot_area
        component_share = overlap / comp_area
        if slot_coverage >= 0.35 and component_share >= 0.08:
            claimed_slots += 1
            if claimed_slots >= 2:
                return True
    return False


def _slot_image_bytes(background_path: Path, slot: dict[str, Any]) -> bytes | None:
    asset_url = str(slot.get("asset_url") or "")
    marker = "/exact_assets/"
    if marker not in asset_url:
        return None
    rel = asset_url.split(marker, 1)[1].lstrip("/")
    path = background_path.parent / rel
    try:
        return path.read_bytes()
    except OSError:
        return None


def _image_component_is_better_bbox(slot: dict[str, float], component: dict[str, float]) -> bool:
    slot_area = max(1.0, float(slot["width"]) * float(slot["height"]))
    comp_area = max(1.0, float(component["width"]) * float(component["height"]))
    area_delta = abs(slot_area - comp_area) / max(slot_area, comp_area)
    edge_delta = max(
        abs(float(slot["left"]) - float(component["left"])),
        abs(float(slot["top"]) - float(component["top"])),
        abs((float(slot["left"]) + float(slot["width"])) - (float(component["left"]) + float(component["width"]))),
        abs((float(slot["top"]) + float(slot["height"])) - (float(component["top"]) + float(component["height"]))),
    )
    return area_delta >= 0.08 or edge_delta >= 10


def _detect_photo_components(image_path: Path) -> list[dict[str, float]]:
    try:
        rgb = _load_rgb_image(image_path)
        if rgb is None:
            return []
        scale = max(1, min(6, int(max(rgb.width, rgb.height) / 280)))
        small = rgb.resize((max(1, rgb.width // scale), max(1, rgb.height // scale)))
    except Exception:
        return []

    width, height = small.size
    if width <= 0 or height <= 0:
        return []
    background = _dominant_edge_colour(small)
    base_components = _detect_photo_components_for_threshold(small, background, scale, threshold=55)
    expanded_components = _detect_photo_components_for_threshold(small, background, scale, threshold=35)
    return _merge_photo_component_thresholds(base_components, expanded_components)


def _detect_photo_components_for_threshold(
    small: Any,
    background: tuple[int, int, int],
    scale: int,
    *,
    threshold: int,
) -> list[dict[str, float]]:
    width, height = small.size
    mask = [False] * (width * height)
    for y in range(height):
        for x in range(width):
            mask[y * width + x] = _rgb_distance(small.getpixel((x, y)), background) > threshold

    seen = [False] * (width * height)
    components: list[dict[str, float]] = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if not mask[index] or seen[index]:
                continue
            stack = [(x, y)]
            seen[index] = True
            points = 0
            min_x = max_x = x
            min_y = max_y = y
            while stack:
                current_x, current_y = stack.pop()
                points += 1
                min_x = min(min_x, current_x)
                max_x = max(max_x, current_x)
                min_y = min(min_y, current_y)
                max_y = max(max_y, current_y)
                for next_x, next_y in ((current_x + 1, current_y), (current_x - 1, current_y), (current_x, current_y + 1), (current_x, current_y - 1)):
                    if 0 <= next_x < width and 0 <= next_y < height:
                        next_index = next_y * width + next_x
                        if mask[next_index] and not seen[next_index]:
                            seen[next_index] = True
                            stack.append((next_x, next_y))
            comp_width = (max_x - min_x + 1) * scale
            comp_height = (max_y - min_y + 1) * scale
            comp_area = points * scale * scale
            bbox_area = max(1, comp_width * comp_height)
            density = comp_area / bbox_area
            if comp_width < 80 or comp_height < 80 or comp_area < 8000 or density < 0.25:
                continue
            components.append(
                {
                    "left": float(min_x * scale),
                    "top": float(min_y * scale),
                    "width": float(comp_width),
                    "height": float(comp_height),
                    "area": float(comp_area),
                    "density": float(density),
                }
            )
    return sorted(components, key=lambda item: item["area"], reverse=True)


def _merge_photo_component_thresholds(
    base_components: list[dict[str, float]],
    expanded_components: list[dict[str, float]],
) -> list[dict[str, float]]:
    if not base_components:
        return expanded_components
    if not expanded_components:
        return base_components
    merged: list[dict[str, float]] = []
    used_expanded: set[int] = set()
    for base in base_components:
        best_index: int | None = None
        best_score = 0.0
        for index, expanded in enumerate(expanded_components):
            overlap = _rect_intersection_area(base, expanded)
            if overlap <= 0:
                continue
            base_area = max(1.0, float(base["width"]) * float(base["height"]))
            expanded_area = max(1.0, float(expanded["width"]) * float(expanded["height"]))
            base_coverage = overlap / base_area
            expanded_coverage = overlap / expanded_area
            if base_coverage < 0.72:
                continue
            score = base_coverage + expanded_coverage
            if score > best_score:
                best_index = index
                best_score = score
        if best_index is None:
            merged.append(base)
            continue
        expanded = expanded_components[best_index]
        used_expanded.add(best_index)
        if _expanded_component_adds_meaningful_photo_edge(base, expanded):
            merged.append(expanded)
        else:
            merged.append(base)
    for index, expanded in enumerate(expanded_components):
        if index not in used_expanded:
            merged.append(expanded)
    return _dedupe_photo_components(sorted(merged, key=lambda item: item["area"], reverse=True))


def _expanded_component_adds_meaningful_photo_edge(base: dict[str, float], expanded: dict[str, float]) -> bool:
    base_area = max(1.0, float(base["width"]) * float(base["height"]))
    expanded_area = max(1.0, float(expanded["width"]) * float(expanded["height"]))
    area_ratio = expanded_area / base_area
    base_left = float(base["left"])
    base_top = float(base["top"])
    base_right = base_left + float(base["width"])
    base_bottom = base_top + float(base["height"])
    expanded_left = float(expanded["left"])
    expanded_top = float(expanded["top"])
    expanded_right = expanded_left + float(expanded["width"])
    expanded_bottom = expanded_top + float(expanded["height"])
    edge_expansion = max(
        base_left - expanded_left,
        base_top - expanded_top,
        expanded_right - base_right,
        expanded_bottom - base_bottom,
        0.0,
    )
    return edge_expansion >= 16.0 and area_ratio >= 1.12


def _dedupe_photo_components(components: list[dict[str, float]]) -> list[dict[str, float]]:
    deduped: list[dict[str, float]] = []
    for component in components:
        if any(_iou_css_rect(component, existing) >= 0.92 for existing in deduped):
            continue
        deduped.append(component)
    return deduped


def _iou_css_rect(a: dict[str, float], b: dict[str, float]) -> float:
    overlap = _rect_intersection_area(a, b)
    area_a = max(1.0, float(a["width"]) * float(a["height"]))
    area_b = max(1.0, float(b["width"]) * float(b["height"]))
    return overlap / max(1.0, area_a + area_b - overlap)


def _dominant_edge_colour(image: Any) -> tuple[int, int, int]:
    from collections import Counter

    width, height = image.size
    samples: list[tuple[int, int, int]] = []
    for x in range(width):
        samples.append(image.getpixel((x, 0)))
        samples.append(image.getpixel((x, height - 1)))
    for y in range(height):
        samples.append(image.getpixel((0, y)))
        samples.append(image.getpixel((width - 1, y)))
    if not samples:
        return (255, 255, 255)

    def quantise(pixel: tuple[int, int, int]) -> tuple[int, int, int]:
        return tuple(max(0, min(255, int(round(channel / 16) * 16))) for channel in pixel[:3])

    return Counter(quantise(sample) for sample in samples).most_common(1)[0][0]


def _rgb_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((float(left[index]) - float(right[index])) ** 2 for index in range(3)) ** 0.5


def _source_image_marks_for_page(
    page: fitz.Page,
    css_width: int,
    css_height: int,
    assets_dir: Path,
    project_id: str,
    page_num: int,
) -> list[dict[str, Any]]:
    marks: list[dict[str, Any]] = []
    images_dir = assets_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    scale_x = css_width / float(page.rect.width or 1)
    scale_y = css_height / float(page.rect.height or 1)
    seen: set[int] = set()
    for info in page.get_image_info(xrefs=True):
        bbox = info.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = [float(value) for value in bbox]
        width = (x1 - x0) * scale_x
        height = (y1 - y0) * scale_y
        if width < 16 or height < 16 or width > 180 or height > 180:
            continue
        xref = int(info.get("xref") or 0)
        if not xref or xref in seen:
            continue
        seen.add(xref)
        try:
            extracted = page.parent.extract_image(xref)
        except Exception:
            continue
        image_bytes = extracted.get("image")
        if not image_bytes or len(image_bytes) < 500:
            continue
        image_bytes, ext = _source_mark_asset_bytes(image_bytes, extracted.get("ext", "png"))
        filename = f"page{page_num:03d}-source-mark-{len(marks) + 1:02d}.{ext}"
        (images_dir / filename).write_bytes(image_bytes)
        marks.append(
            {
                "left": round(x0 * scale_x, 2),
                "top": round(y0 * scale_y, 2),
                "width": round(width, 2),
                "height": round(height, 2),
                "asset_url": f"/api/projects/{project_id}/exact_assets/images/{filename}",
            }
        )
    return marks


def _source_mark_asset_bytes(image_bytes: bytes, ext_hint: str = "png") -> tuple[bytes, str]:
    """Recover transparent backgrounds for small PDF logo/mark images.

    Some PDFs expose white source marks as RGB images against a black matte,
    even though the page uses them as transparent artwork. For replacement
    slots, keep the visible mark and make the dominant edge matte transparent.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            rgba = image.convert("RGBA")
            width, height = rgba.size
            if width <= 0 or height <= 0 or width * height > 90_000:
                return image_bytes, _safe_extension(ext_hint)
            edge_pixels: list[tuple[int, int, int]] = []
            for x in range(width):
                edge_pixels.append(rgba.getpixel((x, 0))[:3])
                edge_pixels.append(rgba.getpixel((x, height - 1))[:3])
            for y in range(height):
                edge_pixels.append(rgba.getpixel((0, y))[:3])
                edge_pixels.append(rgba.getpixel((width - 1, y))[:3])
            if not edge_pixels:
                return image_bytes, _safe_extension(ext_hint)
            from collections import Counter

            def quantise(pixel: tuple[int, int, int]) -> tuple[int, int, int]:
                return tuple(max(0, min(255, int(round(channel / 16) * 16))) for channel in pixel[:3])

            background = Counter(quantise(pixel) for pixel in edge_pixels).most_common(1)[0][0]
            pixels = list(rgba.getdata())
            distances = [_rgb_distance(pixel[:3], background) for pixel in pixels]
            foreground = [distance for distance in distances if distance > 58]
            if len(foreground) < max(18, int(len(pixels) * 0.015)):
                return image_bytes, _safe_extension(ext_hint)
            if len(foreground) > int(len(pixels) * 0.78):
                return image_bytes, _safe_extension(ext_hint)
            output = []
            transparent = 0
            for pixel, distance in zip(pixels, distances):
                if distance <= 42:
                    output.append((pixel[0], pixel[1], pixel[2], 0))
                    transparent += 1
                else:
                    output.append(pixel)
            if transparent < int(len(pixels) * 0.12):
                return image_bytes, _safe_extension(ext_hint)
            rgba.putdata(output)
            buffer = io.BytesIO()
            rgba.save(buffer, format="PNG")
            return buffer.getvalue(), "png"
    except Exception:
        return image_bytes, _safe_extension(ext_hint)


def _render_image_slots(page_num: int, slots: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for index, slot in enumerate(slots, start=1):
        style = (
            f"left:{slot['left']}px;top:{slot['top']}px;"
            f"width:{slot['width']}px;height:{slot['height']}px;"
        )
        save_id = f"exact-page{page_num}-image{index}"
        mask_role = _mask_colour_role(slot["mask_colour"])
        mask_background = "var(--exact-dark)" if mask_role == "dark" else slot["mask_colour"]
        mask_style = f"{style}background:{mask_background};"
        asset_url = html.escape(slot["asset_url"], quote=True)
        fit_mode = html.escape(str(slot.get("fit") or "cover"), quote=True)
        image_role = html.escape(str(slot.get("image_role") or "photo-region"), quote=True)
        photo_styles = [f'background-image:url(&quot;{asset_url}&quot;)']
        photo_img = f'<img class="slot-photo-img" src="{asset_url}" alt="">'
        if slot.get("tone_filter"):
            photo_styles.append(str(slot["tone_filter"]))
        chunks.append(
            f'<div class="exact-image-mask" style="{mask_style}" '
            f'data-mask-for="{save_id}" data-mask-role="{mask_role}"></div>'
            f'<div class="exact-image-slot has-image" style="{style}" data-save-id="{save_id}" '
            f'data-image-slot="{save_id}" data-slot-id="page-{page_num}-image-{index}" '
            f'data-fit="{fit_mode}" data-image-role="{image_role}" '
            f'data-original-left="{slot["left"]}" data-original-top="{slot["top"]}" '
            f'data-original-width="{slot["width"]}" data-original-height="{slot["height"]}" '
            f'data-source-image="{asset_url}">'
            f'<div class="slot-photo" style="{";".join(photo_styles)}">{photo_img}</div>'
            '<input type="file" accept="image/*" aria-label="Replace image"/>'
            '<div class="slot-chip">Image</div>'
            '<div class="slot-controls">'
            '<button type="button" data-fit="cover">Fill</button>'
            '<button type="button" data-fit="contain">Fit</button>'
            '<button type="button" data-fit="cover-left">Left crop</button>'
            '</div>'
            '</div>'
        )
    return "\n".join(chunks)


def _render_background_panels(
    width: int,
    height: int,
    background_path: Path,
    image_slots: list[dict[str, Any]],
) -> str:
    panels = _background_panels_for_page(width, height, background_path, image_slots)
    chunks: list[str] = []
    for index, panel in enumerate(panels, start=1):
        colour = html.escape(str(panel["colour"]), quote=True)
        chunks.append(
            '<div class="exact-background-panel" '
            f'data-background-panel="{index}" data-background-role="{html.escape(str(panel["role"]), quote=True)}" '
            f'style="left:{panel["left"]:.2f}px;top:{panel["top"]:.2f}px;'
            f'width:{panel["width"]:.2f}px;height:{panel["height"]:.2f}px;'
            f'background:{colour};"></div>'
        )
    return "\n".join(chunks)


def _background_panels_for_page(
    width: int,
    height: int,
    background_path: Path,
    image_slots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover large page-background regions that are not PDF vector paths.

    Poppler/PyMuPDF can expose photos and text cleanly while omitting plain
    page fills from the editable layer. Sampling the rendered PDF outside image
    slots restores those large light/dark panels without fixing coordinates for
    a specific brochure.
    """
    if width <= 0 or height <= 0 or not background_path.is_file():
        return []
    try:
        from PIL import Image

        rgb = _load_rgb_image(background_path)
        if rgb is None:
            return []
        if rgb.width != width or rgb.height != height:
            rgb = rgb.resize((width, height), Image.Resampling.LANCZOS)
        split = width / 2.0
        candidates = [
            {"left": 0.0, "top": 0.0, "width": split, "height": float(height)},
            {"left": split, "top": 0.0, "width": float(width) - split, "height": float(height)},
        ]
        panels: list[dict[str, Any]] = []
        for candidate in candidates:
            colour = _sample_background_panel_colour(rgb, candidate, image_slots)
            if not colour:
                continue
            panel = {
                **candidate,
                "colour": colour,
                "role": _background_panel_role(colour),
            }
            panels.append(panel)
        return _merge_adjacent_background_panels(panels)
    except Exception:
        return []


def _sample_background_panel_colour(
    image: Any,
    rect: dict[str, float],
    image_slots: list[dict[str, Any]],
) -> str:
    left = max(0, int(round(float(rect["left"]))))
    top = max(0, int(round(float(rect["top"]))))
    right = min(image.width, int(round(float(rect["left"]) + float(rect["width"]))))
    bottom = min(image.height, int(round(float(rect["top"]) + float(rect["height"]))))
    if right <= left or bottom <= top:
        return ""

    slot_rects = [_expanded_rect(slot, 10.0) for slot in image_slots]
    samples: list[tuple[int, int, int]] = []
    step = max(4, int(max(image.width, image.height) / 240))
    for y in range(top, bottom, step):
        for x in range(left, right, step):
            if any(_point_in_rect(x, y, slot) for slot in slot_rects):
                continue
            samples.append(image.getpixel((x, y))[:3])
    if len(samples) < 600:
        return ""

    from collections import Counter

    def quantise(pixel: tuple[int, int, int]) -> tuple[int, int, int]:
        return tuple(max(0, min(255, int(round(channel / 16) * 16))) for channel in pixel[:3])

    dominant, count = Counter(quantise(pixel) for pixel in samples).most_common(1)[0]
    if count / len(samples) < 0.28:
        return ""
    cluster = [pixel for pixel in samples if _rgb_distance(pixel, dominant) <= 26]
    if len(cluster) < max(300, int(len(samples) * 0.18)):
        return ""
    red = int(round(sum(pixel[0] for pixel in cluster) / len(cluster)))
    green = int(round(sum(pixel[1] for pixel in cluster) / len(cluster)))
    blue = int(round(sum(pixel[2] for pixel in cluster) / len(cluster)))
    if red >= 240 and green >= 240 and blue >= 240:
        return "#ffffff"
    if max(red, green, blue) <= 64 and max(red, green, blue) - min(red, green, blue) <= 14:
        return _rgb_to_hex((red, green, blue))
    if max(red, green, blue) - min(red, green, blue) <= 18 and (max(red, green, blue) <= 80 or min(red, green, blue) >= 210):
        return _rgb_to_hex((red, green, blue))
    return ""


def _expanded_rect(rect: dict[str, Any], amount: float) -> dict[str, float]:
    left = float(rect.get("left") or 0) - amount
    top = float(rect.get("top") or 0) - amount
    width = float(rect.get("width") or 0) + amount * 2
    height = float(rect.get("height") or 0) + amount * 2
    return {"left": left, "top": top, "width": width, "height": height}


def _point_in_rect(x: int, y: int, rect: dict[str, float]) -> bool:
    return rect["left"] <= x <= rect["left"] + rect["width"] and rect["top"] <= y <= rect["top"] + rect["height"]


def _background_panel_role(colour: str) -> str:
    parsed = _hex_to_rgb(colour)
    if not parsed:
        return "neutral"
    red, green, blue = parsed
    if min(red, green, blue) >= 220:
        return "light"
    if max(red, green, blue) <= 90:
        return "dark"
    return "neutral"


def _merge_adjacent_background_panels(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(panels) != 2:
        return panels
    left, right = panels
    if str(left.get("colour")).lower() != str(right.get("colour")).lower():
        return panels
    merged = dict(left)
    merged["width"] = float(left["width"]) + float(right["width"])
    return [merged]


def _rgb_to_hex(pixel: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, int(pixel[0]))),
        max(0, min(255, int(pixel[1]))),
        max(0, min(255, int(pixel[2]))),
    )


def _overlaps_existing(
    candidate: tuple[float, float, float, float],
    existing: list[tuple[float, float, float, float]],
) -> bool:
    cx0, cy0, cx1, cy1 = candidate
    candidate_area = max(0.0, cx1 - cx0) * max(0.0, cy1 - cy0)
    if candidate_area <= 0:
        return True
    for x0, y0, x1, y1 in existing:
        ix0 = max(cx0, x0)
        iy0 = max(cy0, y0)
        ix1 = min(cx1, x1)
        iy1 = min(cy1, y1)
        intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        if intersection / candidate_area > 0.92:
            return True
    return False


def _safe_extension(value: str) -> str:
    ext = re.sub(r"[^A-Za-z0-9]+", "", value or "png").lower()
    if ext in {"jpg", "jpeg", "png", "webp"}:
        return "jpg" if ext == "jpeg" else ext
    return "png"


def _image_tone_filter(background_path: Path, bbox: dict[str, float], image_bytes: bytes) -> str:
    """Approximate PDF image toning when extracted photos lose page-level blending."""
    try:
        from PIL import Image

        background = _load_rgb_image(background_path)
        if background is None:
            return ""
        with Image.open(io.BytesIO(image_bytes)) as source_image:
            source = source_image.convert("RGB")
        bg_width, bg_height = background.size
        left = float(bbox["left"])
        top = float(bbox["top"])
        width = max(1.0, float(bbox["width"]))
        height = max(1.0, float(bbox["height"]))
        crop_box = (
            max(0, round(left)),
            max(0, round(top)),
            min(bg_width, round(left + width)),
            min(bg_height, round(top + height)),
        )
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            return ""
        sample_width = max(8, min(120, crop_box[2] - crop_box[0]))
        sample_height = max(8, min(120, crop_box[3] - crop_box[1]))
        background_sample = background.crop(crop_box).resize((sample_width, sample_height), Image.Resampling.LANCZOS)
        source_sample = source.resize((sample_width, sample_height), Image.Resampling.LANCZOS)
        background_luma = sorted(_relative_luminance(pixel) for pixel in background_sample.getdata())
        source_luma = sorted(
            _relative_luminance(pixel)
            for pixel in source_sample.getdata()
            if _relative_luminance(pixel) >= 24
        )
        if len(background_luma) < 32 or len(source_luma) < 32:
            return ""
        bg_q15 = _quantile(background_luma, 0.15)
        bg_q50 = _quantile(background_luma, 0.50)
        bg_q85 = _quantile(background_luma, 0.85)
        src_q15 = _quantile(source_luma, 0.15)
        src_q50 = _quantile(source_luma, 0.50)
        src_q85 = _quantile(source_luma, 0.85)
        if src_q50 < 24:
            return ""
        brightness = max(0.42, min(1.15, bg_q50 / src_q50))
        bg_range = max(1.0, bg_q85 - bg_q15)
        src_range = max(1.0, src_q85 - src_q15)
        contrast = max(0.45, min(1.12, bg_range / src_range))
        filters: list[str] = []
        if brightness < 0.92 or brightness > 1.06:
            filters.append(f"brightness({brightness:.3f})")
        if contrast < 0.88 or contrast > 1.08:
            filters.append(f"contrast({contrast:.3f})")
        if not filters:
            return ""
        return "filter:" + " ".join(filters)
    except Exception:
        return ""


def _point_sample_image_brightness_filter(background: Any, source: Any, left: float, top: float, width: float, height: float) -> str:
    bg_width, bg_height = background.size
    src_width, src_height = source.size
    ratios: list[float] = []
    for gy in range(1, 6):
        for gx in range(1, 6):
            fx = gx / 6
            fy = gy / 6
            bg_x = round(left + width * fx)
            bg_y = round(top + height * fy)
            if not (0 <= bg_x < bg_width and 0 <= bg_y < bg_height):
                continue
            src_x = min(src_width - 1, max(0, round(src_width * fx)))
            src_y = min(src_height - 1, max(0, round(src_height * fy)))
            bg_luma = _relative_luminance(background.getpixel((bg_x, bg_y)))
            src_luma = _relative_luminance(source.getpixel((src_x, src_y)))
            if src_luma < 24:
                continue
            ratios.append(bg_luma / src_luma)
    if len(ratios) < 8:
        return ""
    ratios.sort()
    sample = ratios[min(len(ratios) - 1, max(0, int(len(ratios) * 0.35)))]
    if sample >= 0.92:
        return ""
    brightness = max(0.42, min(0.95, sample))
    return f"filter:brightness({brightness:.3f})"


def _quantile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, round((len(values) - 1) * fraction)))
    return float(values[index])


def _relative_luminance(pixel: tuple[int, int, int] | Any) -> float:
    red, green, blue = [float(value) for value in pixel[:3]]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _mask_colour_role(colour: str) -> str:
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", colour or "")
    if not match:
        return "light"
    red = int(match.group(1)[0:2], 16)
    green = int(match.group(1)[2:4], 16)
    blue = int(match.group(1)[4:6], 16)
    return "dark" if max(red, green, blue) < 90 else "light"


def _theme_colours_from_pages(pages: list[dict[str, Any]]) -> dict[str, str]:
    dark_weights: dict[str, float] = {}
    for page in pages:
        for item in page.get("dark_fill_stats") or []:
            if not isinstance(item, dict):
                continue
            colour = str(item.get("colour") or "").lower()
            if not colour.startswith("#"):
                continue
            try:
                area = float(item.get("area") or 0)
            except (TypeError, ValueError):
                area = 0
            if area <= 0:
                continue
            dark_weights[colour] = dark_weights.get(colour, 0.0) + area
    dark = "#333132"
    if dark_weights:
        dark = sorted(dark_weights.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return {"accent": "#ffea00", "dark": dark}


def _normalise_css_hex_colour(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if re.fullmatch(r"#[0-9a-f]{6}", candidate) else ""


def _theme_colours_from_inventory(inventory: dict[str, Any]) -> dict[str, str]:
    systems = inventory.get("global_systems") if isinstance(inventory, dict) else {}
    palette = systems.get("palette") if isinstance(systems, dict) else {}
    if not isinstance(palette, dict):
        return {}

    theme: dict[str, str] = {}
    accent = _normalise_css_hex_colour(palette.get("accent_colour") or palette.get("accent"))
    dark = _normalise_css_hex_colour(palette.get("dark_background_colour") or palette.get("dark"))
    light = _normalise_css_hex_colour(palette.get("light_text_colour") or palette.get("light"))
    if accent:
        theme["accent"] = accent
    if dark:
        theme["dark"] = dark
    if light:
        theme["light"] = light
    return theme


def _contrast_colour_for(colour: str) -> str:
    rgb = _hex_to_rgb(colour)
    if not rgb:
        return "#ffffff"
    return "#1d1d1b" if _relative_luminance(rgb) >= 160 else "#ffffff"


def _contrast_rgba(text_colour: str, alpha: float) -> str:
    return f"rgba(29,29,27,{alpha:.2f})" if text_colour == "#1d1d1b" else f"rgba(255,255,255,{alpha:.2f})"


def _sample_mask_colour(background_path: Path, bbox: dict[str, float]) -> str:
    try:
        rgb = _load_rgb_image(background_path)
        if rgb is None:
            return "#ffffff"
        width, height = rgb.size
        left = max(0, int(bbox["left"]))
        top = max(0, int(bbox["top"]))
        right = min(width - 1, int(bbox["left"] + bbox["width"]))
        bottom = min(height - 1, int(bbox["top"] + bbox["height"]))
        probes: list[tuple[int, int]] = []
        margin = 6
        for x in (left - margin, right + margin, left + margin, right - margin):
            for y in (top - margin, bottom + margin, top + margin, bottom - margin):
                if 0 <= x < width and 0 <= y < height:
                    probes.append((x, y))
        probes.extend([(8, 8), (width - 9, 8), (8, height - 9), (width - 9, height - 9)])
        colours = [rgb.getpixel(point) for point in probes if 0 <= point[0] < width and 0 <= point[1] < height]
        if not colours:
            return "#ffffff"
        colours.sort(key=lambda colour: sum(colour))
        median = colours[len(colours) // 2]
        return f"#{median[0]:02x}{median[1]:02x}{median[2]:02x}"
    except Exception:
        return "#ffffff"


def _sample_ocr_foreground_colour(background_path: Path, bbox: dict[str, float], mask_colour: str) -> str:
    try:
        from collections import Counter

        rgb = _load_rgb_image(background_path)
        background = _hex_to_rgb(mask_colour) or (255, 255, 255)
        if rgb is None:
            return ""
        image_width, image_height = rgb.size
        left = max(0, int(float(bbox.get("left") or 0)))
        top = max(0, int(float(bbox.get("top") or 0)))
        right = min(image_width, int(float(bbox.get("left") or 0) + float(bbox.get("width") or 0)))
        bottom = min(image_height, int(float(bbox.get("top") or 0) + float(bbox.get("height") or 0)))
        if right <= left or bottom <= top:
            return ""
        crop = rgb.crop((left, top, right, bottom))
        step = max(1, int(max(crop.size) / 80))
        samples: list[tuple[int, int, int]] = []
        for y in range(0, crop.height, step):
            for x in range(0, crop.width, step):
                red, green, blue = crop.getpixel((x, y))[:3]
                pixel = (red, green, blue)
                if _rgb_distance(pixel, background) < 42:
                    continue
                if max(pixel) - min(pixel) < 12 and _rgb_distance(pixel, background) < 90:
                    continue
                samples.append(tuple(max(0, min(255, int(round(channel / 8) * 8))) for channel in pixel))
        if not samples:
            return ""
        colour, _count = Counter(samples).most_common(1)[0]
        return f"#{colour[0]:02x}{colour[1]:02x}{colour[2]:02x}"
    except Exception:
        return ""


def _render_exact_html(
    *,
    project_id: str,
    page_width: int,
    page_height: int,
    pages: list[dict[str, Any]],
    poppler_css: str,
    font_css: str,
    field_config: dict[str, Any] | None = None,
) -> str:
    field_config = field_config or _build_structured_field_config(pages)
    typography_config = field_config.get("typography") or {}
    theme_colours = _theme_colours_from_pages(pages)
    configured_theme = field_config.get("theme_colours")
    if isinstance(configured_theme, dict):
        theme_colours.update(
            {
                key: colour
                for key, value in configured_theme.items()
                if (colour := _normalise_css_hex_colour(value))
            }
        )
    dark_colour = _normalise_css_hex_colour(theme_colours.get("dark")) or "#333132"
    accent_colour = _normalise_css_hex_colour(theme_colours.get("accent")) or "#ffea00"
    light_colour = _normalise_css_hex_colour(theme_colours.get("light")) or "#ffffff"
    ui_surface_colour = dark_colour
    ui_text_colour = _contrast_colour_for(ui_surface_colour)
    accent_contrast_colour = _contrast_colour_for(accent_colour)
    ui_muted_colour = _contrast_rgba(ui_text_colour, 0.62)
    ui_border_colour = _contrast_rgba(ui_text_colour, 0.16)
    ui_button_background = _contrast_rgba(ui_text_colour, 0.08)
    ui_button_text = _contrast_rgba(ui_text_colour, 0.88)
    page_markup = "\n".join(
        (
            f'<section class="exact-page-frame" data-page-num="{page["page_num"]}" '
            f'style="--exact-frame-w:{page["width"]}px;--exact-frame-h:{page["height"]}px;">'
            f'<div class="exact-page" id="page{page["page_num"]}" '
            f'data-page-num="{page["page_num"]}" data-picture-layout="{_default_picture_layout(page)}" '
            f'data-default-picture-layout="{_default_picture_layout(page)}" '
            f'{_source_preserved_edit_attrs(page)}'
            f'{_image_only_page_attrs(page)}'
            f'style="width:{page["width"]}px;height:{page["height"]}px;">'
            f'{_page_body_for_render(page)}'
            f'{_render_semantic_overlays(page, field_config)}'
            '</div></section>'
        )
        for page in pages
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Exact PDF Layout</title>
<style>
{font_css}
{poppler_css}
:root {{
  --exact-page-w: {page_width}px;
  --exact-page-h: {page_height}px;
  --exact-accent: {accent_colour};
  --exact-dark: {dark_colour};
  --exact-light: {light_colour};
  --exact-accent-contrast: {accent_contrast_colour};
  --exact-ui-surface: {ui_surface_colour};
  --exact-ui-text: {ui_text_colour};
  --exact-ui-muted: {ui_muted_colour};
  --exact-ui-border: {ui_border_colour};
  --exact-ui-button-bg: {ui_button_background};
  --exact-ui-button-text: {ui_button_text};
  --exact-scale: 1;
  --exact-icon-scale: 1;
  --exact-icon-stroke-width: 3;
  --exact-icon-color: var(--exact-accent);
  {_render_typography_root_vars(typography_config)}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: #171717;
  color: white;
  font-family: Arial, sans-serif;
  padding: 72px 22px 32px;
}}
.exact-toolbar {{
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 10000;
  min-height: 54px;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 16px;
  overflow-x: auto;
  background: color-mix(in srgb, var(--exact-ui-surface) 96%, transparent);
  color: var(--exact-ui-text);
  border-bottom: 1px solid var(--exact-ui-border);
  backdrop-filter: blur(12px);
}}
.exact-toolbar strong {{
  font-size: 12px;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  white-space: nowrap;
}}
.exact-tool-group {{
  display: flex;
  align-items: center;
  gap: 7px;
  padding-left: 12px;
  border-left: 1px solid var(--exact-ui-border);
}}
.exact-tool-group label {{
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--exact-ui-muted);
}}
.exact-toolbar input[type="color"] {{
  width: 32px;
  height: 28px;
  padding: 0;
  border: 1px solid var(--exact-ui-border);
  background: transparent;
}}
.exact-toolbar input[type="range"] {{ width: 110px; }}
.exact-toolbar input[type="file"] {{
  display: none;
}}
.exact-toolbar button,
.exact-toolbar select,
.logo-upload-label,
.slot-controls button {{
  border: 1px solid var(--exact-ui-border);
  background: var(--exact-ui-button-bg);
  color: var(--exact-ui-button-text);
  border-radius: 4px;
  height: 28px;
  padding: 0 9px;
  font: 11px Arial, sans-serif;
}}
.logo-upload-label {{
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}}
.exact-toolbar button.is-active,
.slot-controls button.is-active {{ background: var(--exact-accent); color: var(--exact-accent-contrast); }}
.exact-fields-panel {{
  position: fixed;
  top: 54px;
  right: 0;
  bottom: 0;
  z-index: 10002;
  width: min(390px, calc(100vw - 28px));
  overflow-y: auto;
  padding: 18px;
  background: rgba(27, 27, 26, 0.98);
  border-left: 1px solid rgba(255,255,255,0.14);
  box-shadow: -20px 0 40px rgba(0,0,0,0.36);
  transform: translateX(100%);
  transition: transform 0.18s ease;
}}
body.fields-open .exact-fields-panel {{ transform: translateX(0); }}
.exact-fields-head {{
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}}
.exact-fields-head h2 {{
  flex: 1;
  margin: 0;
  font-size: 13px;
  letter-spacing: 0.13em;
  text-transform: uppercase;
}}
.exact-global-summary {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0 0 14px;
}}
.exact-global-summary span {{
  border: 1px solid rgba(255,255,255,0.13);
  border-radius: 4px;
  padding: 5px 7px;
  background: rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.76);
  font: 10px Arial, sans-serif;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}}
.exact-fields-close {{
  width: 28px;
  height: 28px;
  padding: 0;
}}
.exact-field-section {{
  padding: 15px 0;
  border-top: 1px solid rgba(255,255,255,0.11);
}}
.exact-field-section:first-of-type {{ border-top: 0; }}
.exact-field-section h3 {{
  margin: 0 0 10px;
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--exact-accent);
}}
.exact-field-row {{
  display: grid;
  grid-template-columns: 102px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  margin-bottom: 9px;
}}
.exact-field-row label {{
  padding-top: 8px;
  color: rgba(255,255,255,0.62);
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.exact-field-row input,
.exact-field-row select,
.exact-field-row textarea {{
  width: 100%;
  border: 1px solid rgba(255,255,255,0.16);
  border-radius: 4px;
  padding: 8px 9px;
  background: rgba(255,255,255,0.08);
  color: rgba(255,255,255,0.93);
  font: 12px/1.35 Arial, sans-serif;
}}
.exact-field-row textarea {{
  min-height: 72px;
  resize: vertical;
}}
.exact-feature-status input {{
  color: rgba(255,255,255,0.68);
  font-style: italic;
}}
.exact-source-logo-list {{
  padding: 8px 9px;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 4px;
  background: rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.82);
  font: 10px/1.45 Arial, sans-serif;
}}
.exact-source-logo-map-row + .exact-source-logo-map-row {{
  margin-top: 5px;
}}
.exact-logo-actions {{
  display: flex;
  gap: 7px;
  align-items: center;
}}
.exact-logo-actions input[type="file"] {{ display: none; }}
.exact-logo-actions label,
.exact-logo-actions button {{
  height: 26px;
  border: 1px solid rgba(255,255,255,0.16);
  border-radius: 4px;
  padding: 0 8px;
  display: inline-flex;
  align-items: center;
  background: rgba(255,255,255,0.08);
  color: rgba(255,255,255,0.86);
  font: 10px Arial, sans-serif;
  cursor: pointer;
}}
.exact-subrow {{
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr);
  gap: 7px;
  align-items: center;
  margin-top: 6px;
}}
.exact-subrow span {{
  color: rgba(255,255,255,0.56);
  font: 10px Arial, sans-serif;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.exact-field-hidden {{
  visibility: hidden !important;
  pointer-events: none !important;
}}
.exact-brand-logo-slot {{
  position: absolute;
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  color: white;
  pointer-events: auto;
  border: 1px solid transparent;
  border-radius: 2px;
  overflow: hidden;
}}
.exact-brand-logo-slot:hover {{
  border-color: rgba(255,255,255,0.34);
  background: rgba(255,255,255,0.04);
}}
.exact-brand-logo-slot .logo-mask {{
  position: absolute;
  inset: -5px;
  background: var(--exact-dark);
}}
.exact-brand-logo-slot:not(.is-active) .logo-mask,
.exact-brand-logo-slot:not(.is-active) .logo-output,
.exact-brand-logo-slot:not(.is-active) img {{
  display: none;
}}
.exact-brand-logo-slot.has-default-agency-logo:not(.is-active) .exact-agency-logo-default,
.exact-brand-logo-slot.has-default-agency-logo:not(.is-active) .exact-agency-logo-default img {{
  display: block;
}}
.exact-brand-logo-slot.is-active .exact-agency-logo-default {{
  display: none;
}}
.exact-agency-logo-default {{
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  pointer-events: none;
}}
.exact-agency-logo-default img {{
  width: 100%;
  height: 100%;
  object-fit: contain;
}}
.exact-brand-logo-slot .logo-output {{
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 100%;
  color: white;
  line-height: 1.05;
  white-space: pre-line;
}}
.exact-brand-logo-slot img {{
  position: relative;
  z-index: 1;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}}
.agency-logo-upload {{
  position: absolute;
  inset: 0;
  z-index: 4;
  opacity: 0;
  cursor: pointer;
}}
.agency-logo-chip {{
  position: absolute;
  left: 6px;
  top: 6px;
  z-index: 3;
  display: none;
  padding: 3px 6px;
  border-radius: 3px;
  background: rgba(0,0,0,0.62);
  color: white;
  font: 9px Arial, sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.exact-brand-logo-slot:hover .agency-logo-chip {{
  display: block;
}}
.logo-output-agency {{
  font-size: clamp(24px, 18%, 48px);
  font-weight: 700;
  letter-spacing: 0.01em;
  text-transform: uppercase;
  line-height: 1.04;
}}
.logo-output-agency small {{
  display: block;
  margin-top: 4px;
  font-size: 0.42em;
  font-weight: 400;
  letter-spacing: 0.04em;
}}
.exact-pages {{
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 28px;
}}
.exact-page-frame {{
  position: relative;
  width: calc(var(--exact-frame-w, var(--exact-page-w)) * var(--exact-scale));
  height: calc(var(--exact-frame-h, var(--exact-page-h)) * var(--exact-scale));
  flex: 0 0 auto;
}}
.exact-page {{
  position: absolute;
  top: 0;
  left: 0;
  overflow: hidden;
  background: var(--exact-dark);
  transform: scale(var(--exact-scale));
  transform-origin: top left;
  box-shadow: 0 18px 55px rgba(0,0,0,0.48);
}}
.pdf-bg {{
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 0;
  pointer-events: none;
  user-select: none;
  -webkit-user-drag: none;
  display: none;
}}
.pdf-vector-layer {{
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
  pointer-events: none;
}}
.exact-background-panel {{
  position: absolute;
  z-index: 0;
  pointer-events: none;
}}
.pdf-vector-overlay-layer {{
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 2;
  pointer-events: none;
}}
.pdf-vector-shape {{
  transition: fill 0.16s, stroke 0.16s;
}}
.pdf-vector-shape.source-logo-hidden {{
  opacity: 0 !important;
}}
.pdf-vector-shape.source-amenity-icon-hidden {{
  opacity: 0 !important;
}}
.exact-page[data-picture-layout="original"] .pdf-bg {{
  display: block;
}}
.exact-page[data-picture-layout="original"] .pdf-vector-layer {{
  display: none;
}}
.exact-page[data-picture-layout="original"] .pdf-vector-overlay-layer {{
  display: none;
}}
.exact-page[data-source-preserved-edit="true"][data-picture-layout="editable"] .pdf-bg,
.exact-page[data-source-preserved-edit="true"][data-picture-layout="text-review"] .pdf-bg {{
  display: block;
}}
.exact-page[data-source-preserved-edit="true"][data-picture-layout="editable"] .pdf-vector-layer,
.exact-page[data-source-preserved-edit="true"][data-picture-layout="editable"] .pdf-vector-overlay-layer,
.exact-page[data-source-preserved-edit="true"][data-picture-layout="editable"] .exact-background-panel,
.exact-page[data-source-preserved-edit="true"][data-picture-layout="text-review"] .pdf-vector-layer,
.exact-page[data-source-preserved-edit="true"][data-picture-layout="text-review"] .pdf-vector-overlay-layer,
.exact-page[data-source-preserved-edit="true"][data-picture-layout="text-review"] .exact-background-panel {{
  display: none;
}}
.pdf-text {{
  z-index: 4;
  min-height: 1em;
  outline: none;
  cursor: text;
}}
.pdf-text[contenteditable="true"] * {{
  font-family: inherit !important;
  font-size: inherit !important;
  font-weight: inherit;
  font-style: inherit;
  line-height: inherit !important;
  color: inherit !important;
  letter-spacing: inherit;
}}
{_render_typography_role_css(typography_config)}
#page1 .pdf-text[data-save-id="exact-page1-text1"],
#page1 .pdf-text[data-save-id="exact-page1-text2"],
#page1 .pdf-text[data-save-id="exact-page1-text3"],
#page1 .pdf-text[data-save-id="exact-page1-text4"] {{
  letter-spacing: 0.26em;
  font-kerning: none;
}}
#page1 .pdf-text[data-save-id="exact-page1-text1"] {{
  letter-spacing: 0.06em;
}}
.exact-page[data-picture-layout="original"] .pdf-text:not(:focus):not([data-edited="true"]),
.exact-page[data-picture-layout="image-review"] .pdf-text:not(:focus):not([data-edited="true"]) {{
  color: transparent !important;
}}
.exact-page[data-source-preserved-edit="true"][data-picture-layout="editable"] .pdf-text:not([data-active-edit="true"]):not([data-edited="true"]),
.exact-page[data-source-preserved-edit="true"][data-picture-layout="text-review"] .pdf-text:not([data-active-edit="true"]):not([data-edited="true"]) {{
  color: transparent !important;
}}
.exact-page[data-image-only-page="true"] .pdf-text:not([data-edited="true"]) {{
  color: transparent !important;
  pointer-events: none !important;
}}
.exact-page[data-source-preserved-edit="true"] .pdf-text[data-active-edit="true"]:not([data-edited="true"]) {{
  background: rgba(255,255,255,0.72);
}}
.exact-ocr-text:not([data-active-edit="true"]):not([data-edited="true"]) {{
  color: transparent !important;
}}
.exact-ocr-text:focus,
.exact-ocr-text[data-active-edit="true"],
.exact-ocr-text[data-edited="true"] {{
  background: var(--exact-ocr-mask-colour, rgba(255,255,255,0.92)) !important;
}}
.pdf-text:hover {{ box-shadow: 0 0 0 1px rgba(255,255,255,0.28); }}
.pdf-text:focus {{
  background: rgba(255,255,255,0.12);
  box-shadow: 0 0 0 2px var(--exact-accent);
}}
.exact-inert-pdf-text,
.exact-inert-pdf-text:hover,
.exact-inert-pdf-text:focus {{
  pointer-events: none !important;
  user-select: none !important;
  cursor: default !important;
  box-shadow: none !important;
  outline: none !important;
  background: transparent !important;
}}
.exact-page[data-source-preserved-edit="true"] .pdf-text.exact-inert-pdf-text {{
  color: transparent !important;
}}
.pdf-text[data-title-stack="true"] {{
  display: flex;
  align-items: center;
  justify-content: center;
  white-space: normal !important;
  overflow: visible;
}}
.exact-title-stack {{
  display: flex;
  flex-direction: column-reverse;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  height: 100%;
  line-height: 0.82;
  text-align: center;
}}
.exact-title-stack span {{
  display: block;
}}
.pdf-text[data-title-rotated="true"] {{
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  white-space: nowrap !important;
  overflow: visible;
  line-height: 0.82;
}}
.exact-title-rotated {{
  display: inline-block;
  white-space: nowrap;
  transform: rotate(-90deg);
  transform-origin: center center;
  line-height: 0.82;
}}
.pdf-text[data-contact-semantic-block="true"] {{
  overflow: visible;
  white-space: normal !important;
  font-family: Arial, sans-serif !important;
  background: transparent;
}}
.exact-page[data-picture-layout="editable"] .pdf-text[data-contact-semantic-block="true"],
.exact-page[data-picture-layout="text-review"] .pdf-text[data-contact-semantic-block="true"],
body.export-clean .pdf-text[data-contact-semantic-block="true"] {{
  background: var(--exact-contact-mask, transparent);
  color: var(--exact-contact-name-color, var(--exact-light)) !important;
}}
.exact-contact-semantic {{
  display: inline-block;
  min-width: 100%;
  white-space: normal;
  font-family: Arial, sans-serif !important;
}}
.exact-contact-name {{
  display: block;
  margin-bottom: 0.72em;
  color: var(--exact-contact-name-color, inherit) !important;
  font-weight: inherit;
  font-family: Arial, sans-serif !important;
}}
.exact-contact-row {{
  display: block;
  color: var(--exact-light) !important;
  font-family: Arial, sans-serif !important;
  font-weight: normal;
}}
.exact-contact-prefix {{
  display: inline-block;
  min-width: 1.2em;
  color: var(--exact-accent) !important;
  font-family: Arial, sans-serif !important;
}}
.exact-contact-value {{
  font-family: Arial, sans-serif !important;
}}
.exact-page[data-picture-layout="text-review"] .pdf-text {{
  background: rgba(255,255,255,0.1);
}}
.exact-page[data-picture-layout="text-review"] .pdf-vector-layer,
.exact-page[data-picture-layout="text-review"] .pdf-vector-overlay-layer,
.exact-page[data-picture-layout="text-review"] .exact-image-slot {{
  opacity: 0.42;
}}
.pdf-text[data-edited="true"] {{
  padding: 2px 4px;
  margin: -2px -4px;
}}
.pdf-text[data-colour-role="light"][data-edited="true"],
.pdf-text[data-colour-role="accent"][data-edited="true"] {{
  background: var(--exact-dark);
}}
.pdf-text[data-colour-role="dark"][data-edited="true"],
.pdf-text[data-colour-role="body"][data-edited="true"] {{
  background: rgba(255,255,255,0.92);
}}
.exact-page[data-picture-layout="image-review"] .exact-image-slot {{
  opacity: 1;
  border-color: var(--exact-accent);
}}
.exact-page[data-picture-layout="image-review"] .slot-controls {{
  display: flex;
}}
.exact-page[data-picture-layout="editable"] .exact-image-mask,
.exact-page[data-picture-layout="image-review"] .exact-image-mask,
.exact-page[data-picture-layout="text-review"] .exact-image-mask {{
  display: block;
}}
.exact-page[data-picture-layout="editable"] .exact-image-slot,
.exact-page[data-picture-layout="text-review"] .exact-image-slot {{
  opacity: 1;
}}
.exact-page[data-picture-layout="original"] .exact-image-slot {{
  pointer-events: none;
  opacity: 0 !important;
}}
.exact-page[data-picture-layout="original"] .exact-space-plan-slot {{
  pointer-events: auto;
  opacity: 0.01 !important;
}}
.exact-page[data-picture-layout="original"] .exact-space-plan-slot:hover,
.exact-page[data-picture-layout="original"] .exact-space-plan-slot.has-image {{
  opacity: 1 !important;
}}
.exact-page[data-picture-layout="original"] .slot-chip,
.exact-page[data-picture-layout="original"] .slot-controls {{
  display: none !important;
}}
.exact-page[data-picture-layout="original"] .exact-image-mask {{
  display: none !important;
}}
.exact-page[data-picture-layout="original"] .exact-image-mask[data-mask-for^="space-plan:"] {{
  display: block !important;
  opacity: 0;
}}
.exact-page[data-picture-layout="original"] .exact-space-plan-slot.has-image + .exact-image-mask,
.exact-page[data-picture-layout="original"] .exact-image-mask[data-space-plan-mask="true"].has-image {{
  opacity: 1;
}}
.exact-image-mask {{
  position: absolute;
  z-index: 1;
  display: none;
  pointer-events: none;
}}
.exact-image-mask[data-space-plan-mask="true"]:not(.has-image) {{
  display: none !important;
}}
.exact-image-slot {{
  position: absolute;
  z-index: 3;
  overflow: hidden;
  border: 1px solid transparent;
  opacity: 0;
  transition: opacity 0.16s, border-color 0.16s;
}}
.exact-image-slot.has-image {{
  opacity: 1;
}}
.exact-image-slot:hover {{
  opacity: 1;
  border-color: rgba(255,255,255,0.42);
}}
.slot-photo {{
  position: absolute;
  inset: 0;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
}}
.slot-photo-img {{
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
  object-position: center;
}}
.exact-image-slot[data-fit="contain"] .slot-photo-img {{
  object-fit: contain;
}}
.exact-image-slot[data-fit="cover-left"] .slot-photo-img {{
  object-position: left center;
}}
.exact-image-slot input {{
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
  z-index: 3;
}}
.slot-chip {{
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 2;
  padding: 4px 7px;
  background: rgba(0,0,0,0.62);
  color: white;
  border-radius: 3px;
  font: 10px Arial, sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.slot-controls {{
  position: absolute;
  right: 8px;
  bottom: 8px;
  z-index: 4;
  display: none;
  gap: 5px;
  pointer-events: auto;
}}
.exact-image-slot:hover .slot-controls,
.exact-image-slot.has-image .slot-controls {{ display: flex; }}
.exact-page[data-picture-layout="editable"] .slot-chip,
.exact-page[data-picture-layout="editable"] .slot-controls,
.exact-page[data-picture-layout="text-review"] .slot-chip,
.exact-page[data-picture-layout="text-review"] .slot-controls {{
  display: none;
}}
.exact-page[data-picture-layout="editable"] .exact-image-slot:hover .slot-chip,
.exact-page[data-picture-layout="editable"] .exact-image-slot:hover .slot-controls,
.exact-page[data-picture-layout="text-review"] .exact-image-slot:hover .slot-chip,
.exact-page[data-picture-layout="text-review"] .exact-image-slot:hover .slot-controls {{
  display: flex;
}}
.exact-image-slot[data-fit="contain"] .slot-photo {{
  background-size: contain;
  background-color: rgba(0,0,0,0.22);
}}
.exact-image-slot[data-fit="cover-left"] .slot-photo {{
  background-size: cover;
  background-position: left center;
}}
.exact-space-plan-slot {{
  z-index: 7;
  background: transparent;
}}
.exact-space-plan-slot[data-fit="contain"] .slot-photo {{
  background-color: #fff;
}}
.exact-table-image-slot {{
  z-index: 7;
  background: transparent;
}}
.exact-table-image-slot[data-fit="contain"] .slot-photo {{
  background-color: #fff;
}}
.exact-space-plan-slot.has-image .slot-photo {{
  background-color: #fff;
}}
.exact-logo {{
  position: absolute;
  z-index: 5;
  display: none;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  color: var(--exact-accent);
  pointer-events: none;
}}
.exact-logo svg {{ width: 100%; height: 100%; }}
.exact-logo img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
.exact-page.show-logo .exact-logo {{ display: flex; }}
.exact-source-logo-slot {{
  position: absolute;
  z-index: 8;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--exact-accent);
  pointer-events: auto;
  border: 1px solid transparent;
  border-radius: 2px;
  overflow: hidden;
}}
.exact-source-logo-slot:hover {{
  border-color: rgba(255,255,255,0.34);
  background: rgba(255,255,255,0.04);
}}
.exact-source-logo-slot.is-active {{
  background: transparent;
}}
.exact-source-logo-slot[data-source-logo-mask-role="dark"] .exact-source-logo-mask {{
  background: var(--exact-dark);
}}
.exact-source-logo-mask {{
  position: absolute;
  inset: -4px;
  display: none;
  background: var(--source-logo-mask, var(--exact-dark));
}}
.exact-source-logo-slot.is-active .exact-source-logo-mask {{
  display: block;
}}
.exact-source-logo-slot.is-active[data-source-logo-mask-mode="vector"] .exact-source-logo-mask,
.exact-source-logo-slot.is-active[data-source-logo-mask-mode="photo"] .exact-source-logo-mask {{
  display: none;
}}
.exact-source-logo-art {{
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transform-origin: center;
}}
.exact-source-logo-art svg,
.exact-source-logo-art img {{
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}}
.exact-source-logo-default {{
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}}
.exact-source-logo-default img {{
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}}
.exact-source-logo-slot.is-active .exact-source-logo-default {{
  display: none;
}}
.exact-amenity-icon-slot {{
  position: absolute;
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 3px;
  padding: 10px;
  background: transparent;
  color: var(--exact-icon-color);
  cursor: pointer;
  opacity: 0.08;
  transition: opacity 0.16s, border-color 0.16s, background 0.16s;
}}
.exact-amenity-icon-slot svg {{
  width: 100%;
  height: 100%;
  transform: scale(var(--exact-icon-scale));
  transform-origin: center;
}}
.exact-amenity-icon-slot svg * {{
  stroke: currentColor !important;
  stroke-width: var(--exact-icon-stroke-width) !important;
  fill: none !important;
}}
.exact-amenity-icon-slot:hover,
.exact-amenity-icon-slot.is-active {{
  opacity: 1;
  border-color: rgba(255,255,255,0.28);
  background: transparent;
}}
.exact-page[data-picture-layout="original"] .exact-amenity-icon-slot {{
  opacity: 0.02;
  pointer-events: auto;
}}
.exact-page[data-picture-layout="original"] .exact-amenity-icon-slot:hover,
.exact-page[data-picture-layout="original"] .exact-amenity-icon-slot.is-active {{
  opacity: 1;
}}
.exact-map-area {{
  position: absolute;
  inset: 0;
  z-index: 2;
  pointer-events: none;
}}
.exact-map-controls {{
  position: absolute;
  right: 22px;
  top: 20px;
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 7px;
  border-radius: 4px;
  background: rgba(24,24,24,0.78);
  border: 1px solid rgba(255,255,255,0.14);
  pointer-events: auto;
}}
.exact-map-status {{
  max-width: 210px;
  color: rgba(255,255,255,0.78);
  font: 10px/1.25 Arial, sans-serif;
}}
.exact-map-controls button {{
  height: 26px;
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 4px;
  padding: 0 8px;
  background: rgba(255,255,255,0.1);
  color: rgba(255,255,255,0.88);
  font: 10px Arial, sans-serif;
  cursor: pointer;
}}
.exact-generated-map {{
  position: absolute;
  inset: 0;
  z-index: -1;
  background: var(--exact-dark);
  pointer-events: none;
}}
.exact-map-area.has-generated-map .exact-generated-map {{
  z-index: 0;
}}
.exact-page[data-picture-layout="original"] .exact-map-controls {{
  display: none;
}}
.icon-picker-overlay {{
  position: fixed;
  inset: 0;
  display: none;
  z-index: 12000;
  background: rgba(0,0,0,0.14);
}}
.icon-picker-overlay.open {{ display: block; }}
.icon-picker-panel {{
  position: fixed;
  padding: 12px;
  border: 1px solid rgba(255,255,255,0.16);
  border-radius: 6px;
  background: rgba(24,24,24,0.98);
  box-shadow: 0 18px 44px rgba(0,0,0,0.38);
}}
.ip-title {{
  margin-bottom: 10px;
  color: rgba(255,255,255,0.82);
  font: 11px Arial, sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.icon-grid {{
  display: grid;
  grid-template-columns: repeat(5, 44px);
  gap: 7px;
}}
.icon-opt {{
  width: 44px;
  height: 44px;
  padding: 8px;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 4px;
  cursor: pointer;
  color: var(--exact-accent);
}}
.icon-opt:hover {{ background: rgba(255,255,255,0.12); }}
.icon-opt svg {{ width: 100%; height: 100%; }}
.save-state {{
  margin-left: auto;
  color: rgba(255,255,255,0.52);
  font-size: 11px;
}}
body.export-clean {{
  padding: 0;
  background: white;
}}
body.export-clean .exact-toolbar {{
  display: none;
}}
body.export-clean .exact-fields-panel {{
  display: none;
}}
body.export-clean .exact-amenity-icon-slot:not(.is-active),
body.export-clean .exact-map-controls,
body.export-clean .icon-picker-overlay {{
  display: none !important;
}}
body.export-clean .exact-pages {{
  gap: 0;
}}
body.export-clean .exact-page-frame {{
  width: var(--exact-frame-w, var(--exact-page-w));
  height: var(--exact-frame-h, var(--exact-page-h));
  page-break-after: always;
}}
body.export-clean .exact-page {{
  transform: none;
  box-shadow: none;
}}
body.export-clean .exact-image-slot {{
  border: none;
}}
body.export-clean .slot-chip,
body.export-clean .slot-controls,
body.export-clean .exact-image-slot input {{
  display: none !important;
}}
@media print {{
  body {{ padding: 0; background: white; }}
  .exact-toolbar {{ display: none; }}
  .exact-pages {{ gap: 0; }}
  .exact-page-frame {{
    width: var(--exact-frame-w, var(--exact-page-w));
    height: var(--exact-frame-h, var(--exact-page-h));
    page-break-after: always;
  }}
  .exact-page {{ transform: none; box-shadow: none; }}
  .exact-image-slot {{ border: none; }}
  .slot-chip, .slot-controls {{ display: none !important; }}
}}
</style>
</head>
<body class="fields-open">
<div class="exact-toolbar">
  <strong>Exact PDF Layout</strong>
  <div class="exact-tool-group">
	    <label for="colourPreset">Colours</label>
	    <select id="colourPreset">
	      <option value="extracted" selected>Extracted PDF</option>
	      <option value="custom">Custom</option>
	      <option value="yellow-dark">Yellow / Charcoal</option>
	      <option value="blue">Blue</option>
      <option value="copper">Copper</option>
      <option value="forest">Forest</option>
    </select>
    <label for="accentColour">Accent</label>
    <input id="accentColour" type="color" value="{accent_colour}">
    <label for="darkColour">Dark</label>
    <input id="darkColour" type="color" value="{dark_colour}">
  </div>
  <div class="exact-tool-group">
    <label for="logoSelect">Source facade logo</label>
    <select id="logoSelect">
      <option value="none">PDF source</option>
      <option value="grid">Grid</option>
      <option value="diamond">Diamond</option>
      <option value="facade">Facade</option>
      <option value="upload">Uploaded</option>
    </select>
    <label class="logo-upload-label" for="logoUpload">Upload</label>
    <input id="logoUpload" type="file" accept="image/*">
    <label for="logoSize">Size</label>
    <input id="logoSize" type="range" min="24" max="130" value="56">
    <label for="logoPosition">Pos</label>
    <select id="logoPosition">
      <option value="source">Source</option>
      <option value="top-left">Top left</option>
      <option value="top-right">Top right</option>
      <option value="bottom-left">Bottom left</option>
      <option value="bottom-right">Bottom right</option>
    </select>
  </div>
  <button type="button" id="fieldsToggle" aria-controls="fieldsPanel" aria-expanded="true">Global controls</button>
  <div class="exact-tool-group">
    <label>Layout</label>
    <button type="button" data-layout-mode="editable" class="is-active">Edit</button>
    <button type="button" data-layout-mode="original">Original</button>
    <button type="button" data-layout-mode="image-review">Images</button>
    <button type="button" data-layout-mode="text-review">Text</button>
  </div>
  <div class="exact-tool-group">
    <label for="pictureLayout">Pictures</label>
    <select id="pictureLayout">
      <option value="original">Original crops</option>
      <option value="fit">Fit all</option>
      <option value="left-crop">Left crop</option>
      <option value="grid">Grid</option>
      <option value="hero-stack">Hero stack</option>
    </select>
  </div>
  <div class="exact-tool-group">
    <label for="zoomRange">Zoom</label>
    <input id="zoomRange" type="range" min="35" max="100" value="72">
  </div>
  <span class="save-state" id="saveState">Ready</span>
</div>
{_render_fields_panel(field_config)}
<div class="icon-picker-overlay" id="iconPickerOverlay">
  <div class="icon-picker-panel" id="iconPickerPanel" style="width:300px;">
    <div class="ip-title">Choose icon - click to apply</div>
    <div class="icon-picker-cats" id="iconPickerCats" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;"></div>
    <div class="icon-grid" id="iconGrid"></div>
  </div>
</div>
<main class="exact-pages" data-project-id="{html.escape(project_id)}">
{page_markup}
</main>
<script src="/static/icons/svg-library.js"></script>
<script src="/static/js/icon-picker.js"></script>
<script>
window.__PROJECT_ID__ = "{html.escape(project_id)}";
window.EXACT_ICON_BANK = {json.dumps(EXACT_ICON_LIBRARY)};
window.__EXACT_AGENCY_LOGOS__ = {json.dumps(field_config["agency_logos"])};
window.__EXACT_CONTACT_DEFS__ = {json.dumps(field_config["contact_defs"])};
window.__EXACT_AMENITY_ICONS__ = {json.dumps(field_config["amenity_icons"])};
window.__EXACT_SERVICE_ICONS__ = {json.dumps(field_config["service_icons"])};
window.__EXACT_SPACE_PLANS__ = {json.dumps(field_config["space_plans"])};
window.__EXACT_IMAGE_REGIONS__ = {json.dumps(field_config.get("image_regions", []))};
window.__EXACT_MAP_REGION__ = {json.dumps(field_config["map_region"])};
window.__EXACT_MAP_REGIONS__ = {json.dumps(field_config.get("map_regions", []))};
window.__EXACT_SOURCE_LOGOS__ = {json.dumps(field_config["source_logos"])};
window.__EXACT_TYPOGRAPHY__ = {json.dumps(typography_config)};
window.__EXACT_THEME__ = {json.dumps({"accent": accent_colour, "dark": dark_colour, "light": light_colour})};
{_exact_editor_js()}
</script>
</body>
</html>
"""


def _page_body_for_render(page: dict[str, Any]) -> str:
    body = str(page.get("body") or "")
    if page.get("source_preserved_edit"):
        body = _suppress_source_preserved_interaction_hotspots(
            body,
            int(page.get("width") or 0),
            int(page.get("height") or 0),
        )
    return body


def _default_picture_layout(page: dict[str, Any]) -> str:
    """Open the editor in genuinely editable mode.

    Clean export forces source-preserved presentation mode separately. The
    editor itself must expose clickable text, image, icon, and logo controls by
    default; otherwise a visual-fidelity pass can hide the very features the
    user needs to edit.
    """
    return "editable"


def _source_preserved_edit_attrs(page: dict[str, Any]) -> str:
    if not page.get("source_preserved_edit"):
        return ""
    path_count = int(page.get("vector_path_count") or 0)
    return f'data-source-preserved-edit="true" data-vector-path-count="{path_count}" '


def _image_only_page_attrs(page: dict[str, Any]) -> str:
    if not page.get("image_only_page"):
        return ""
    return 'data-image-only-page="true" '


def _should_promote_full_page_rendered_image(
    *,
    width: int,
    height: int,
    text_entries: list[dict[str, Any]],
    image_regions: list[dict[str, Any]],
) -> bool:
    """Treat heavily annotated full-page photo pages as one replaceable image."""
    if len(text_entries) < 24:
        return False
    page_area = max(1.0, float(width) * float(height))
    for region in image_regions:
        if not isinstance(region, dict):
            continue
        if str(region.get("role") or "") not in {"hero-photo", "artwork-image", "photo-region"}:
            continue
        bbox = _bbox_from_semantic_region(region)
        area_ratio = (float(bbox.get("width") or 0) * float(bbox.get("height") or 0)) / page_area
        if area_ratio >= 0.82:
            return True
    return False


def _promote_full_page_rendered_image_slot(
    *,
    page_num: int,
    width: int,
    height: int,
    image_slots: list[dict[str, Any]],
    image_regions: list[dict[str, Any]],
    source_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bbox = {"left": 0.0, "top": 0.0, "width": float(width), "height": float(height)}
    source_evidence = {
        "method": "full rendered PDF page crop",
        "reason": "full-page annotated photo/artwork is best edited as one image",
    }
    promoted_slot = {
        **bbox,
        "id": f"page-{page_num}-rendered-image",
        "asset_url": source_url,
        "mask_colour": "transparent",
        "photo_score": 0.0,
        "fit": "cover",
        "image_role": "artwork-image",
        "candidate_role": "artwork-image",
        "semantic_confidence": 0.91,
        "source_evidence": source_evidence,
    }
    other_slots = [
        slot
        for slot in image_slots
        if not (
            float(slot.get("width") or 0) * float(slot.get("height") or 0)
            >= float(width) * float(height) * 0.70
        )
    ]
    promoted_region = {
        "id": f"p{page_num:03d}-rendered-image",
        "page_number": page_num,
        "type": "image",
        "role": "artwork-image",
        "bbox": bbox,
        "confidence": 0.91,
        "editable": True,
        "replaceable": True,
        "fit_mode": "cover",
        "mask_mode": "replaceable-mask",
        "locked_static": False,
        "source_evidence": source_evidence,
    }
    other_regions = []
    for region in image_regions:
        if not isinstance(region, dict):
            continue
        region_bbox = _bbox_from_semantic_region(region)
        region_area = float(region_bbox.get("width") or 0) * float(region_bbox.get("height") or 0)
        if (
            str(region.get("role") or "") in {"hero-photo", "photo-region", "artwork-image"}
            and region_area >= float(width) * float(height) * 0.70
        ):
            continue
        other_regions.append(region)
    return [promoted_slot, *other_slots], [promoted_region, *other_regions]


def _should_source_preserve_edit(
    *,
    vector_layer: str,
    image_regions: list[dict[str, Any]],
    text_entries: list[dict[str, Any]],
) -> bool:
    """Keep the rendered PDF as the edit base when vector reconstruction is risky.

    Dense maps and illustrated directory/table pages often contain thousands of
    clipped PDF paths. Re-emitting those paths without the full PDF graphics
    state can create giant white fragments over the page. In that case the
    reusable safer behaviour is source-preserved visual fidelity with editable
    hotspots above it.
    """
    path_count = vector_layer.count("<path ")
    if path_count >= 1200:
        return True
    if len(vector_layer) >= 520_000 and path_count >= 600:
        return True
    region_roles = {str(region.get("role") or "") for region in image_regions if isinstance(region, dict)}
    page_text = "\n".join(str(entry.get("plain") or "") for entry in text_entries if isinstance(entry, dict)).lower()
    if "map" in region_roles and path_count >= 120:
        return True
    if (
        path_count >= 250
        and any(token in page_text for token in ("connectivity", "restaurants", "bars / cafes", "south kensington"))
    ):
        return True
    if path_count >= 220 and any(token in page_text for token in ("amenities", "pubs", "restaurants", "corporate offices")):
        return True
    return False


def _render_typography_root_vars(config: dict[str, Any]) -> str:
    roles = config.get("roles") if isinstance(config, dict) else {}
    if not isinstance(roles, dict):
        roles = {}
    lines: list[str] = []
    for role, var_name in TYPOGRAPHY_ROLE_VARS.items():
        role_config = roles.get(role) if isinstance(roles.get(role), dict) else {}
        lines.append(f"{var_name}: {_css_font_stack(role_config.get('fontFamily'))};")
    return "\n  ".join(lines)


def _render_typography_role_css(config: dict[str, Any]) -> str:
    return (
        ".pdf-text * {\n"
        "  font-family: inherit !important;\n"
        "  font-size: inherit !important;\n"
        "  font-weight: inherit;\n"
        "  font-style: inherit;\n"
        "  line-height: inherit !important;\n"
        "  color: inherit !important;\n"
        "  letter-spacing: inherit;\n"
        "}"
    )


def _build_structured_field_config(
    pages: list[dict[str, Any]],
    *,
    cover_title_bboxes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entries = [entry for page in pages for entry in page.get("text_entries", [])]
    plan_or_map_pages = _plan_or_map_page_numbers(pages)
    map_regions = _build_map_regions(pages)
    map_region = map_regions[0] if map_regions else {}
    map_label_fields = _build_map_label_fields_for_regions(map_regions)
    reserved_map_label_targets = {
        str(target)
        for field in map_label_fields
        for target in field.get("targets", [])
        if target
    }
    amenity_entries = [
        entry
        for entry in entries
        if int(entry.get("page_num") or 0) not in plan_or_map_pages
        and str(entry.get("save_id") or "") not in reserved_map_label_targets
    ]

    def target(label: str, *, page_num: int | None = None, contains: bool = False) -> dict[str, Any] | None:
        return _find_text_entry(entries, label, page_num=page_num, contains=contains)

    cover_title = _build_cover_title_field(entries, cover_title_bboxes=cover_title_bboxes)
    cover_offer = _build_cover_offer_field(entries, set(cover_title["targets"]))
    amenities_title = _find_section_heading(amenity_entries, ("amenities", "features", "specification", "highlights"))
    amenity_defs = _build_amenity_defs(amenity_entries)
    amenities: list[dict[str, Any]] = []
    for key, label, value, entry, hidden_entries in amenity_defs:
        amenities.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "targets": [entry["save_id"]] if entry else [],
                "hide_targets": [hidden["save_id"] for hidden in hidden_entries if hidden],
                "kind": "html-lines" if "\n" in value else "plain",
                "icon_id": _icon_for_amenity(value),
                "anchor": {
                    "page_num": entry.get("page_num"),
                    "left": entry.get("left"),
                    "top": entry.get("top"),
                }
                if entry
                else {},
            }
        )
    _enrich_amenity_fields_with_inventory(amenities, pages)

    contacts = _build_contact_fields(entries)
    agency_logos = _build_agency_logo_defs(contacts, entries, pages)
    source_logos = _build_source_logo_defs(pages)
    space_plans = _build_space_plan_defs(pages)
    table_images = _build_table_image_defs(pages)
    service_icons = _build_service_icon_defs(entries)
    image_regions = _field_config_image_regions_from_pages(pages)
    return {
        "cover_title": cover_title,
        "cover_offer": cover_offer,
        "amenities_title": {
            "targets": [amenities_title["save_id"]] if amenities_title else [],
            "value": str((amenities_title or {}).get("plain") or ""),
        },
        "amenities": amenities,
        "amenity_icons": _build_amenity_icon_defs(amenities, pages),
        "service_icons": service_icons,
        "source_logos": source_logos,
        "space_plans": space_plans,
        "table_images": table_images,
        "image_regions": image_regions,
        "typography": _build_typography_config(entries),
        "map_region": map_region,
        "map_regions": map_regions,
        "map_label_fields": map_label_fields,
        "contacts": contacts,
        "agency_logos": agency_logos,
        "contact_defs": {
            contact["key"]: {
                "target": contact["targets"][0] if contact["targets"] else "",
                "hidden": contact["hide_targets"],
                "prefixes": contact.get("line_prefixes") or [],
            }
            for contact in contacts
        },
    }


def _plan_or_map_page_numbers(pages: list[dict[str, Any]]) -> set[int]:
    blocked: set[int] = set()
    for page in pages:
        try:
            page_num = int(page.get("page_num") or 0)
        except (TypeError, ValueError):
            page_num = 0
        if page_num <= 0:
            continue
        roles = {
            str(region.get("role") or region.get("type") or "")
            for region in page.get("image_regions", []) or []
            if isinstance(region, dict)
        }
        features = {str(feature) for feature in page.get("detected_features", []) or []}
        if (
            roles.intersection({"space-plan", "floor-plan", "map"})
            or features.intersection({"map", "space_plan"})
            or bool(page.get("map_regions"))
            or bool(page.get("space_plan_regions"))
        ):
            blocked.add(page_num)
    return blocked


def _field_config_image_region(region: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    bbox = _bbox_from_semantic_region(region)
    return {
        **region,
        "page": str(page.get("page_num")),
        **bbox,
    }


def _enrich_amenity_fields_with_inventory(amenities: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
    groups: list[dict[str, Any]] = []
    for page in pages:
        for group in page.get("inventory_amenity_label_groups") or []:
            if isinstance(group, dict):
                groups.append(group)
    if not groups:
        return
    groups_by_value = {
        _compact_text(str(group.get("label") or group.get("value") or "")): group
        for group in groups
        if _compact_text(str(group.get("label") or group.get("value") or ""))
    }
    for amenity in amenities:
        key = _compact_text(str(amenity.get("value") or ""))
        group = groups_by_value.get(key)
        if not group:
            continue
        value = str(group.get("value") or amenity.get("value") or "")
        if value:
            amenity["value"] = value
        if int(group.get("line_count") or 0) > 1 or "\n" in value:
            amenity["kind"] = "html-lines"
        bbox = group.get("bbox") if isinstance(group.get("bbox"), dict) else {}
        if bbox:
            amenity["bbox"] = bbox
            anchor = amenity.get("anchor") if isinstance(amenity.get("anchor"), dict) else {}
            if not anchor:
                anchor = {}
                amenity["anchor"] = anchor
            anchor.setdefault("page_num", group.get("page_num"))
            anchor.setdefault("left", bbox.get("left"))
            anchor.setdefault("top", bbox.get("top"))


def _field_config_image_regions_from_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    seen: set[str] = set()
    rendered_image_roles = {"photo-region", "photo-grid", "hero-photo", "artwork-image"}
    for page in pages:
        for index, slot in enumerate(page.get("image_slots", []) or [], start=1):
            if not isinstance(slot, dict):
                continue
            role = str(slot.get("image_role") or slot.get("candidate_role") or "")
            if role not in rendered_image_roles:
                continue
            region = {
                "id": slot.get("id") or f"p{int(page.get('page_num') or 0):03d}-image-slot-{index:04d}",
                "page_number": int(page.get("page_num") or 0),
                "type": "image",
                "role": role,
                "bbox": {key: slot.get(key) for key in ("left", "top", "width", "height")},
                "confidence": slot.get("semantic_confidence"),
                "editable": True,
                "replaceable": True,
                "fit_mode": slot.get("fit") or "cover",
                "mask_mode": "replaceable-mask",
                "locked_static": False,
                "source_evidence": slot.get("semantic_evidence") or slot.get("source_evidence"),
            }
            item = _field_config_image_region(region, page)
            key = _image_region_key(item)
            if key in seen or _overlaps_existing_field_region(item, regions):
                continue
            seen.add(key)
            regions.append(item)
    return regions


def _overlaps_existing_field_region(item: dict[str, Any], regions: list[dict[str, Any]]) -> bool:
    item_kind = str(item.get("role") or "")
    if item_kind not in {"photo-region", "photo-grid", "hero-photo", "artwork-image", "space-plan", "map"}:
        return False
    page = str(item.get("page") or item.get("page_number") or "")
    for region in regions:
        region_kind = str(region.get("role") or "")
        if region_kind not in {"photo-region", "photo-grid", "hero-photo", "artwork-image", "space-plan", "map"}:
            continue
        if str(region.get("page") or region.get("page_number") or "") != page:
            continue
        if _field_region_iou(item, region) >= 0.82:
            return True
    return False


def _field_region_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1 = float(a.get("left") or 0)
    ay1 = float(a.get("top") or 0)
    ax2 = ax1 + float(a.get("width") or 0)
    ay2 = ay1 + float(a.get("height") or 0)
    bx1 = float(b.get("left") or 0)
    by1 = float(b.get("top") or 0)
    bx2 = bx1 + float(b.get("width") or 0)
    by2 = by1 + float(b.get("height") or 0)
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    intersection = (ix2 - ix1) * (iy2 - iy1)
    area_a = max((ax2 - ax1) * (ay2 - ay1), 0.0)
    area_b = max((bx2 - bx1) * (by2 - by1), 0.0)
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def _image_region_key(region: dict[str, Any]) -> str:
    return json.dumps(
        {
            "page": region.get("page") or region.get("page_number"),
            "role": region.get("role"),
            "left": round(float(region.get("left") or 0), 1),
            "top": round(float(region.get("top") or 0), 1),
            "width": round(float(region.get("width") or 0), 1),
            "height": round(float(region.get("height") or 0), 1),
        },
        sort_keys=True,
    )


def _build_typography_config(entries: list[dict[str, Any]]) -> dict[str, Any]:
    font_lookup: dict[str, dict[str, Any]] = {}
    role_counts: dict[str, dict[str, int]] = {role: {} for role in TYPOGRAPHY_ROLE_LABELS}
    role_styles: dict[str, dict[str, dict[str, Any]]] = {role: {} for role in TYPOGRAPHY_ROLE_LABELS}

    for entry in entries:
        font_style = dict(entry.get("font_style") or {})
        stable = str(font_style.get("stable_font_family") or "").strip()
        source = str(font_style.get("source_font_family") or stable).strip()
        if not stable:
            continue
        font_lookup.setdefault(
            stable,
            {
                "family": stable,
                "sourceFamily": source,
                "label": _font_label(stable, source),
            },
        )
        role = str(entry.get("typography_role") or "body")
        if role not in role_counts:
            role = "body"
        role_counts[role][stable] = role_counts[role].get(stable, 0) + 1
        role_styles[role].setdefault(stable, font_style)

    fonts = sorted(font_lookup.values(), key=lambda item: str(item.get("label") or item.get("family") or "").lower())
    fallback_family = fonts[0]["family"] if fonts else "Arial"
    body_family = _most_common_role_font("body", role_counts) or fallback_family
    roles: dict[str, dict[str, Any]] = {}
    for role, label in TYPOGRAPHY_ROLE_LABELS.items():
        family = _most_common_role_font(role, role_counts) or body_family
        style = role_styles.get(role, {}).get(family) or {}
        roles[role] = {
            "label": label,
            "cssVar": TYPOGRAPHY_ROLE_VARS[role],
            "fontFamily": family,
            "sourceFontFamily": style.get("source_font_family") or font_lookup.get(family, {}).get("sourceFamily") or family,
            "fontSize": style.get("font_size") or "",
            "lineHeight": style.get("line_height") or "",
            "letterSpacing": style.get("letter_spacing") or "",
            "fontWeight": style.get("font_weight") or "",
        }
    return {"fonts": fonts, "roles": roles}


def _most_common_role_font(role: str, role_counts: dict[str, dict[str, int]]) -> str | None:
    counts = role_counts.get(role) or {}
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[0][0]


def _load_exact_layout_model(project_dir: Path) -> dict[str, Any]:
    model_path = project_dir / "exact_layout_model" / "exact-layout.json"
    if not model_path.exists():
        return {}
    try:
        data = json.loads(model_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_exact_inventory(project_dir: Path) -> dict[str, Any]:
    inventory_path = project_dir / "exact_layout_model" / "extraction-inventory.json"
    if not inventory_path.exists():
        return {}
    try:
        data = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge_model_image_regions(pages: list[dict[str, Any]], exact_model: dict[str, Any]) -> None:
    """Supplement renderer regions with the model-stage semantic classifier.

    The upload pipeline creates ``exact-layout.json`` before rendering HTML.
    That model can detect semantic regions such as vector floor plans that are
    not ordinary PDF image XObjects, so the renderer must consume those regions
    instead of relying only on its local image-slot pass.
    """
    if not exact_model:
        return
    model_pages = exact_model.get("pages") if isinstance(exact_model.get("pages"), list) else []
    by_page = {
        int(page.get("page_number") or 0): page
        for page in model_pages
        if isinstance(page, dict)
    }
    for page in pages:
        page_num = int(page.get("page_num") or 0)
        model_page = by_page.get(page_num)
        if not isinstance(model_page, dict):
            continue
        page["model_text_spans"] = _scaled_model_text_entries(model_page, page)
        existing = [
            region
            for region in page.get("image_regions") or []
            if isinstance(region, dict)
        ]
        seen = {_semantic_region_signature(region) for region in existing}
        for region in model_page.get("image_regions") or []:
            if not isinstance(region, dict):
                continue
            scaled_region = _scale_model_image_region(region, model_page, page)
            signature = _semantic_region_signature(scaled_region)
            if signature in seen:
                continue
            seen.add(signature)
            existing.append(scaled_region)
        page["image_regions"] = existing


def _scaled_model_text_entries(model_page: dict[str, Any], render_page: dict[str, Any]) -> list[dict[str, Any]]:
    size = model_page.get("size") if isinstance(model_page.get("size"), dict) else {}
    model_width = float(size.get("width") or 0)
    model_height = float(size.get("height") or 0)
    render_width = float(render_page.get("width") or model_width or 1)
    render_height = float(render_page.get("height") or model_height or 1)
    scale_x = render_width / model_width if model_width else 1.0
    scale_y = render_height / model_height if model_height else 1.0
    entries: list[dict[str, Any]] = []
    for index, span in enumerate(model_page.get("text_spans") or [], start=1):
        if not isinstance(span, dict):
            continue
        bbox = span.get("bbox") if isinstance(span.get("bbox"), dict) else {}
        text = str(span.get("text") or "").strip()
        if not text or not bbox:
            continue
        font = span.get("font") if isinstance(span.get("font"), dict) else {}
        source_family = str(font.get("family") or "").strip()
        model_font_size = float(font.get("size") or 0) * scale_y
        if model_font_size <= 0:
            model_font_size = float(bbox.get("height") or 10) * scale_y
        line_height = float(bbox.get("height") or 0) * scale_y
        colour = str(span.get("color") or "")
        font_style = {
            "source_font_family": source_family,
            "stable_font_family": _stable_font_family(source_family) or source_family,
            "font_size": f"{model_font_size:.2f}px",
            "font_size_px": model_font_size,
            "line_height": f"{line_height:.2f}px" if line_height > 0 else "",
            "line_height_px": line_height,
            "letter_spacing": "0",
            "font_weight": "700" if bool(font.get("is_bold")) else "400",
            "font_style": "italic" if bool(font.get("is_italic")) else "",
            "color": colour,
        }
        entries.append(
            {
                "save_id": f"model-p{model_page.get('page_number')}-text-{index}",
                "model_id": str(span.get("id") or ""),
                "plain": text,
                "left": float(bbox.get("x") or 0) * scale_x,
                "top": float(bbox.get("y") or 0) * scale_y,
                "width": float(bbox.get("width") or 0) * scale_x,
                "height": float(bbox.get("height") or 0) * scale_y,
                "font_style": font_style,
                "colour_role": _colour_role(colour),
                "source": "exact-layout model text span",
            }
        )
    return entries


def _supplement_model_schedule_text(pages: list[dict[str, Any]]) -> None:
    for page in pages:
        model_entries = [entry for entry in page.get("model_text_spans") or [] if isinstance(entry, dict)]
        if not model_entries:
            continue
        supplements = _model_schedule_text_supplements(page, model_entries)
        if not supplements:
            continue
        body = str(page.get("body") or "")
        existing_entries = [entry for entry in page.get("text_entries") or [] if isinstance(entry, dict)]
        text_index = max((int(entry.get("text_index") or 0) for entry in existing_entries), default=int(page.get("text_count") or 0))
        for model_entry in supplements:
            text_index += 1
            save_id = f"exact-page{int(page.get('page_num') or 0)}-text{text_index}"
            rendered, entry = _render_model_schedule_text_entry(page, model_entry, save_id, text_index)
            if not rendered:
                text_index -= 1
                continue
            body += "\n" + rendered
            existing_entries.append(entry)
        page["body"] = body
        page["text_entries"] = existing_entries
        page["text_count"] = text_index


def _model_schedule_text_supplements(page: dict[str, Any], model_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table_bands = _model_schedule_table_bands(page, model_entries)
    if not table_bands:
        return []
    existing_entries = [entry for entry in page.get("text_entries") or [] if isinstance(entry, dict)]
    supplements: list[dict[str, Any]] = []
    for model_entry in sorted(model_entries, key=lambda entry: (float(entry.get("top") or 0), float(entry.get("left") or 0))):
        if not _model_entry_in_schedule_bands(model_entry, table_bands):
            continue
        if not _looks_like_schedule_table_entry(str(model_entry.get("plain") or "")):
            continue
        normalised = _normalise_schedule_table_text(str(model_entry.get("plain") or ""), "table-status")
        candidate = {**model_entry, "plain": normalised}
        if _schedule_model_entry_already_rendered(candidate, existing_entries):
            continue
        supplements.append(candidate)
    return supplements


def _model_schedule_table_bands(page: dict[str, Any], model_entries: list[dict[str, Any]]) -> list[dict[str, float]]:
    page_height = float(page.get("height") or 1)
    heading_entries = [
        entry
        for entry in model_entries
        if _looks_like_schedule_table_heading(str(entry.get("plain") or ""), entry.get("font_style") if isinstance(entry.get("font_style"), dict) else {})
    ]
    if not heading_entries:
        return []
    heading_entries.sort(key=lambda entry: (float(entry.get("top") or 0), float(entry.get("left") or 0)))
    groups: list[list[dict[str, Any]]] = []
    for entry in heading_entries:
        if not groups or abs(float(entry.get("top") or 0) - float(groups[-1][0].get("top") or 0)) > 34:
            groups.append([entry])
        else:
            groups[-1].append(entry)

    bands: list[dict[str, float]] = []
    for index, group in enumerate(groups):
        left = min(float(entry.get("left") or 0) for entry in group) - 28.0
        right = max(_text_entry_right(entry) for entry in group) + 42.0
        top = min(float(entry.get("top") or 0) for entry in group) - 12.0
        next_top = float(groups[index + 1][0].get("top") or 0) - 8.0 if index + 1 < len(groups) else page_height
        bottom = min(next_top, max(float(entry.get("top") or 0) for entry in group) + max(92.0, page_height * 0.18))
        bands.append({"left": left, "right": right, "top": top, "bottom": bottom})
    return bands


def _model_entry_in_schedule_bands(entry: dict[str, Any], bands: list[dict[str, float]]) -> bool:
    left = float(entry.get("left") or 0)
    top = float(entry.get("top") or 0)
    right = _text_entry_right(entry)
    for band in bands:
        if top < float(band.get("top") or 0) or top > float(band.get("bottom") or 0):
            continue
        if right < float(band.get("left") or 0) or left > float(band.get("right") or 0):
            continue
        return True
    return False


def _looks_like_schedule_table_heading(value: str, font_style: dict[str, Any]) -> bool:
    compact = _compact_text(value)
    if compact in {
        "floor",
        "sqft",
        "sqm",
        "status",
        "quotingrent",
        "statusquotingrent",
        "servicecharge",
        "rates",
        "servicechargerates",
    }:
        return True
    colour = str(font_style.get("color") or "").lower()
    font_size = _font_style_size_px(font_style)
    return colour == "#ffea00" and font_size >= 16 and any(token in compact for token in ("floor", "status", "rent", "rates", "charge"))


def _looks_like_schedule_table_entry(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()
    if not text or "@" in text or len(text) > 48:
        return False
    compact = _compact_text(text)
    if _looks_like_schedule_table_heading(text, {}):
        return True
    if compact in {"catb", "let", "letlet", "total", "g"}:
        return True
    if "psf" in compact or compact.startswith("£"):
        return True
    if re.fullmatch(r"[0-9,.]+", text):
        return True
    if re.fullmatch(r"[0-9,.]+\s+SQ\s*(?:FT|M)", text, flags=re.IGNORECASE):
        return True
    return False


def _schedule_model_entry_already_rendered(candidate: dict[str, Any], existing_entries: list[dict[str, Any]]) -> bool:
    candidate_compact = _compact_text(str(candidate.get("plain") or ""))
    candidate_left = float(candidate.get("left") or 0)
    candidate_top = float(candidate.get("top") or 0)
    candidate_right = _text_entry_right(candidate)
    candidate_height = max(8.0, float(candidate.get("height") or _font_size_px(candidate) * 1.2 or 8.0))
    for existing in existing_entries:
        existing_compact = _compact_text(str(existing.get("plain") or ""))
        same_text = candidate_compact == existing_compact
        same_heading_group = (
            candidate_compact in {"status", "quotingrent", "servicecharge", "rates"}
            and candidate_compact in existing_compact
        )
        if not same_text and not same_heading_group:
            continue
        existing_top = float(existing.get("top") or 0)
        if abs(candidate_top - existing_top) > max(8.0, candidate_height * 0.75):
            continue
        existing_left = float(existing.get("left") or 0)
        existing_right = _text_entry_right(existing)
        overlap = min(candidate_right, existing_right) - max(candidate_left, existing_left)
        close_left = abs(candidate_left - existing_left) <= 14.0
        if overlap > 0 or close_left:
            return True
    return False


def _render_model_schedule_text_entry(
    page: dict[str, Any],
    model_entry: dict[str, Any],
    save_id: str,
    text_index: int,
) -> tuple[str, dict[str, Any]]:
    page_num = int(page.get("page_num") or 0)
    plain_text = str(model_entry.get("plain") or "").strip()
    if not plain_text:
        return "", {}
    font_style = dict(model_entry.get("font_style") if isinstance(model_entry.get("font_style"), dict) else {})
    role = _detect_typography_role(page_num, text_index, plain_text, font_style)
    role = "table-status" if _looks_like_schedule_table_entry(plain_text) else role
    colour_role = str(model_entry.get("colour_role") or _colour_role(str(font_style.get("color") or "")) or "body")
    font_size = _font_style_size_px(font_style) or max(10.0, float(model_entry.get("height") or 12) * 0.76)
    line_height = _font_style_line_height_px(font_style) or max(font_size * 1.18, float(model_entry.get("height") or 0))
    left = float(model_entry.get("left") or 0)
    top = float(model_entry.get("top") or 0)
    width = max(float(model_entry.get("width") or 0), len(plain_text) * font_size * 0.48)
    height = max(float(model_entry.get("height") or 0), line_height)
    family = str(font_style.get("stable_font_family") or font_style.get("source_font_family") or "").strip()
    colour = str(font_style.get("color") or "")
    style_parts = [
        "position:absolute",
        f"top:{top:.2f}px",
        f"left:{left:.2f}px",
        "white-space:nowrap",
        f"font-size:{font_size:.2f}px",
        f"line-height:{line_height:.2f}px",
    ]
    if family:
        style_parts.append(f"font-family:{_css_font_stack(family)}")
    if colour:
        style_parts.append(f"color:{colour}")
    content = _html_from_plain_text(plain_text)
    entry = {
        "save_id": save_id,
        "page_num": page_num,
        "text_index": text_index,
        "plain": plain_text,
        "ft_class": "",
        "typography_role": role,
        "font_style": font_style,
        "top": top,
        "left": left,
        "width": width,
        "height": height,
        "source": "exact-layout model schedule supplement",
    }
    rendered = (
        f'<p class="pdf-text exact-model-text" style="{html.escape(";".join(style_parts), quote=True)}" '
        f'contenteditable="true" spellcheck="false" data-model-text-source="{html.escape(str(model_entry.get("model_id") or ""), quote=True)}" '
        f'data-save-id="{save_id}" data-slot-id="page-{page_num}-text-{text_index}" '
        f'data-typography-role="{html.escape(role, quote=True)}" '
        f'{_typography_data_attrs(font_style)}'
        f'data-colour-role="{html.escape(colour_role, quote=True)}" '
        f'data-plain-text="{html.escape(plain_text, quote=True)}" '
        f'data-original-html="{html.escape(content, quote=True)}">{content}</p>'
    )
    return rendered, entry


def _merge_inventory_map_regions(pages: list[dict[str, Any]], inventory: dict[str, Any]) -> None:
    """Promote inventory-proven map pages into renderer map slots.

    Text-only map heuristics are intentionally conservative: contact pages,
    comparison tables, and cover copy often contain place names and road labels
    but should not become editable map controls. The extraction inventory has
    already classified page purpose, so use it as the stronger source for
    secondary maps while keeping the historic single best text map as fallback.
    """
    inventory_pages = inventory.get("pages") if isinstance(inventory.get("pages"), list) else []
    by_page = {
        int(page.get("page_number") or 0): page
        for page in inventory_pages
        if isinstance(page, dict)
    }
    for page in pages:
        page_num = int(page.get("page_num") or 0)
        inventory_page = by_page.get(page_num)
        if not isinstance(inventory_page, dict):
            continue
        purpose = _compact_text(str(inventory_page.get("page_purpose") or inventory_page.get("purpose") or ""))
        detected = {
            _compact_text(str(feature))
            for feature in (inventory_page.get("detected_features") or [])
            if feature
        }
        page["inventory_page_purpose"] = purpose
        page["inventory_detected_features"] = sorted(detected)
        page["inventory_amenity_label_groups"] = _inventory_amenity_label_groups(inventory_page, page)
        map_expected = purpose in {"connectivitymap", "locationmap"} or (
            "map" in detected and "contactsandterms" not in purpose and "cover" not in purpose
        )
        page["inventory_map_expected"] = bool(map_expected)
        if not map_expected:
            continue
        existing = [region for region in page.get("image_regions") or [] if isinstance(region, dict)]
        for index, region in enumerate(inventory_page.get("map_regions") or [], start=1):
            if not isinstance(region, dict):
                continue
            bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
            if not bbox:
                continue
            model_width = float((inventory_page.get("size") or {}).get("width") or page.get("width") or 1)
            model_height = float((inventory_page.get("size") or {}).get("height") or page.get("height") or 1)
            render_width = float(page.get("width") or model_width)
            render_height = float(page.get("height") or model_height)
            scale_x = render_width / model_width if model_width else 1.0
            scale_y = render_height / model_height if model_height else 1.0
            item = {
                "id": f"p{page_num:03d}-inventory-map-{index}",
                "type": "map",
                "role": "map",
                "bbox": {
                    "left": round(float(bbox.get("x") if bbox.get("x") is not None else bbox.get("left") or 0) * scale_x, 2),
                    "top": round(float(bbox.get("y") if bbox.get("y") is not None else bbox.get("top") or 0) * scale_y, 2),
                    "width": round(float(bbox.get("width") or 0) * scale_x, 2),
                    "height": round(float(bbox.get("height") or 0) * scale_y, 2),
                },
                "confidence": inventory_page.get("confidence") or 0.7,
                "editable": True,
                "replaceable": True,
                "fit_mode": "preserve-source",
                "mask_mode": "source-preserved",
                "source_evidence": {
                    "source": "extraction-inventory map_regions",
                    "page_purpose": inventory_page.get("page_purpose") or inventory_page.get("purpose"),
                    "extraction_method": region.get("extraction_method"),
                },
            }
            item["bbox"] = _trim_inventory_map_bbox_away_from_table_sections(item["bbox"], page, purpose)
            if _map_image_region_false_positive(item["bbox"], page) and purpose != "connectivitymap":
                continue
            if any(_field_region_iou(_field_config_image_region(item, page), _field_config_image_region(existing_region, page)) >= 0.50 for existing_region in existing):
                continue
            existing.append(item)
        page["image_regions"] = existing


def _inventory_amenity_label_groups(inventory_page: dict[str, Any], render_page: dict[str, Any]) -> list[dict[str, Any]]:
    regions = inventory_page.get("amenity_icon_regions") if isinstance(inventory_page.get("amenity_icon_regions"), list) else []
    if not regions:
        return []
    blocks = {
        str(block.get("id") or ""): block
        for block in (inventory_page.get("editable_text_blocks") or [])
        if isinstance(block, dict) and block.get("id")
    }
    model_size = inventory_page.get("size") if isinstance(inventory_page.get("size"), dict) else {}
    scale_x = float(render_page.get("width") or model_size.get("width") or 1) / float(model_size.get("width") or render_page.get("width") or 1)
    scale_y = float(render_page.get("height") or model_size.get("height") or 1) / float(model_size.get("height") or render_page.get("height") or 1)
    groups: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        span_ids = [str(item) for item in (region.get("label_span_ids") or []) if item]
        span_blocks = [blocks[span_id] for span_id in span_ids if span_id in blocks]
        if span_blocks:
            span_blocks.sort(key=lambda block: (float((block.get("bbox") or {}).get("y") or 0), float((block.get("bbox") or {}).get("x") or 0)))
            lines = [str(block.get("text") or "").strip() for block in span_blocks if str(block.get("text") or "").strip()]
        else:
            lines = [str(region.get("label") or "").strip()]
        lines = [line for line in lines if line]
        if not lines:
            continue
        bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
        x = bbox.get("x") if bbox.get("x") is not None else bbox.get("left")
        y = bbox.get("y") if bbox.get("y") is not None else bbox.get("top")
        groups.append(
            {
                "page_num": int(inventory_page.get("page_number") or render_page.get("page_num") or 0),
                "label": " ".join(lines),
                "value": "\n".join(lines),
                "line_count": len(lines),
                "bbox": {
                    "left": round(float(x or 0) * scale_x, 2),
                    "top": round(float(y or 0) * scale_y, 2),
                    "width": round(float(bbox.get("width") or 0) * scale_x, 2),
                    "height": round(float(bbox.get("height") or 0) * scale_y, 2),
                },
                "source": "extraction-inventory amenity_icon_regions",
            }
        )
    return groups


def _refresh_source_preserved_edit_after_region_merges(pages: list[dict[str, Any]]) -> None:
    """Re-run source-preserved safety after model/inventory regions are merged.

    Some reliable map evidence arrives after the Poppler page body has already
    been assembled. Without this refresh, vector-heavy map pages can keep their
    reconstructed SVG layer active even though the merged semantic map region
    says the source PDF render should be the visual truth.
    """

    for page in pages:
        if page.get("source_preserved_edit"):
            continue
        should_preserve = _should_source_preserve_edit(
            vector_layer=str(page.get("body") or ""),
            image_regions=[region for region in page.get("image_regions") or [] if isinstance(region, dict)],
            text_entries=[entry for entry in page.get("text_entries") or [] if isinstance(entry, dict)],
        )
        if not should_preserve:
            continue
        page["source_preserved_edit"] = True
        page["body"] = _suppress_source_preserved_interaction_hotspots(
            str(page.get("body") or ""),
            int(page.get("width") or 0),
            int(page.get("height") or 0),
        )


def _trim_inventory_map_bbox_away_from_table_sections(
    bbox: dict[str, float],
    page: dict[str, Any],
    purpose: str,
) -> dict[str, float]:
    """Keep inventory map controls off adjacent schedule/table content.

    Some vector maps are followed by comparison tables. PDF text extraction can
    include the table rows in the map inventory region because both contain
    numbered markers and place names. Use repeated table headings as evidence
    for the boundary instead of brochure-specific coordinates.
    """

    if purpose != "connectivitymap":
        return bbox
    page_height = float(page.get("height") or 1)
    page_width = float(page.get("width") or 1)
    top = float(bbox.get("top") or 0)
    height = float(bbox.get("height") or 0)
    if height <= page_height * 0.55:
        return bbox
    left = float(bbox.get("left") or 0)
    right = left + float(bbox.get("width") or 0)
    table_tops: list[float] = []
    for entry in page.get("text_entries") or []:
        text = str(entry.get("plain") or "").strip()
        compact = _compact_text(text)
        if compact not in {"scheme", "proposal", "statusnotes", "status", "notes"}:
            continue
        entry_top = float(entry.get("top") or 0)
        if entry_top <= page_height * 0.24:
            continue
        entry_left = float(entry.get("left") or 0)
        if entry_left < left - page_width * 0.08 or entry_left > right + page_width * 0.08:
            continue
        table_tops.append(entry_top)
    if not table_tops:
        return bbox
    table_top = min(table_tops)
    new_bottom = max(top + page_height * 0.18, table_top - 14.0)
    body_rights: list[float] = []
    for entry in page.get("text_entries") or []:
        text = str(entry.get("plain") or "").strip()
        if len(text) < 28:
            continue
        entry_top = float(entry.get("top") or 0)
        if entry_top < max(0.0, top) or entry_top >= table_top:
            continue
        entry_left = float(entry.get("left") or 0)
        entry_right = _text_entry_right(entry)
        if entry_left >= left or entry_right < left - page_width * 0.08:
            continue
        body_rights.append(entry_right)
    new_left = left
    if body_rights:
        new_left = min(right - page_width * 0.20, max(left, max(body_rights) + 14.0))
    if left < page_width * 0.45 and right > page_width * 0.60:
        new_left = min(right - page_width * 0.20, max(new_left, page_width * 0.45))
    if new_bottom >= top + height and new_left <= left:
        return bbox
    trimmed = dict(bbox)
    if new_left > left:
        trimmed["left"] = round(new_left, 2)
        trimmed["width"] = round(max(1.0, right - new_left), 2)
    if new_bottom < top + height:
        trimmed["height"] = round(max(1.0, new_bottom - top), 2)
    return trimmed


def _scale_model_image_region(
    region: dict[str, Any],
    model_page: dict[str, Any],
    render_page: dict[str, Any],
) -> dict[str, Any]:
    size = model_page.get("size") if isinstance(model_page.get("size"), dict) else {}
    model_width = float(size.get("width") or 0)
    model_height = float(size.get("height") or 0)
    render_width = float(render_page.get("width") or 0)
    render_height = float(render_page.get("height") or 0)
    scale_x = render_width / model_width if model_width and render_width else 1.0
    scale_y = render_height / model_height if model_height and render_height else 1.0
    if abs(scale_x - 1.0) < 0.001 and abs(scale_y - 1.0) < 0.001:
        return dict(region)
    scaled = dict(region)
    bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
    left = float(bbox.get("left") if bbox.get("left") is not None else bbox.get("x") or 0)
    top = float(bbox.get("top") if bbox.get("top") is not None else bbox.get("y") or 0)
    width = float(bbox.get("width") or 0)
    height = float(bbox.get("height") or 0)
    scaled["bbox"] = {
        "left": round(left * scale_x, 2),
        "top": round(top * scale_y, 2),
        "width": round(width * scale_x, 2),
        "height": round(height * scale_y, 2),
    }
    source = scaled.get("source_evidence") if isinstance(scaled.get("source_evidence"), dict) else {}
    scaled["source_evidence"] = {
        **source,
        "coordinate_scale": {"x": round(scale_x, 6), "y": round(scale_y, 6)},
    }
    return scaled


def _semantic_region_signature(region: dict[str, Any]) -> tuple[str, int, int, int, int]:
    bbox = _bbox_from_semantic_region(region)
    role = str(region.get("role") or region.get("semantic_role") or "")
    return (
        role,
        round(float(bbox.get("left") or 0)),
        round(float(bbox.get("top") or 0)),
        round(float(bbox.get("width") or 0)),
        round(float(bbox.get("height") or 0)),
    )


def _model_cover_title_bboxes(exact_model: dict[str, Any], pages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return PyMuPDF-derived CSS bboxes for vertical cover-title groups."""
    if not exact_model or not pages:
        return {}
    model_pages = exact_model.get("pages") if isinstance(exact_model.get("pages"), list) else []
    model_page = next((page for page in model_pages if int(page.get("page_number") or 0) == 1), None)
    css_page = next((page for page in pages if int(page.get("page_num") or 0) == 1), pages[0])
    if not isinstance(model_page, dict) or not isinstance(css_page, dict):
        return {}
    size = model_page.get("size") if isinstance(model_page.get("size"), dict) else {}
    model_width = float(size.get("width") or 0)
    model_height = float(size.get("height") or 0)
    css_width = float(css_page.get("width") or 0)
    css_height = float(css_page.get("height") or 0)
    if model_width <= 0 or model_height <= 0 or css_width <= 0 or css_height <= 0:
        return {}
    scale_x = css_width / model_width
    scale_y = css_height / model_height

    entries: list[dict[str, Any]] = []
    direct: dict[str, dict[str, Any]] = {}
    spans = model_page.get("text_spans") if isinstance(model_page.get("text_spans"), list) else []
    for index, span in enumerate(spans, start=1):
        if not isinstance(span, dict):
            continue
        text = _plain_text_from_html(str(span.get("text") or ""))
        if not text.strip():
            continue
        bbox = span.get("bbox") if isinstance(span.get("bbox"), dict) else {}
        css_bbox = _scale_pdf_bbox_to_css(bbox, scale_x, scale_y)
        if not css_bbox:
            continue
        font = span.get("font") if isinstance(span.get("font"), dict) else {}
        font_size = float(font.get("size") or 0) * scale_y
        entry = {
            "save_id": str(span.get("id") or f"model-cover-title-{index}"),
            "plain": text,
            "left": css_bbox["left"],
            "top": css_bbox["top"],
            "width": css_bbox["width"],
            "height": css_bbox["height"],
            "font_style": {"font_size_px": font_size},
        }
        entries.append(entry)
        compact = _compact_text(text)
        if len(compact) >= 2:
            direct[compact] = {**css_bbox, "source": "PyMuPDF text span", "value": text.strip()}

    for group in _vertical_cover_title_groups(entries):
        compact = _compact_text(str(group.get("value") or ""))
        if compact and compact not in direct:
            direct[compact] = {
                "left": float(group.get("left") or 0),
                "top": float(group.get("top") or 0),
                "width": float(group.get("width") or 0),
                "height": float(group.get("height") or 0),
                "source": "PyMuPDF grouped vertical text spans",
                "value": str(group.get("value") or ""),
            }
    return direct


def _scale_pdf_bbox_to_css(bbox: dict[str, Any], scale_x: float, scale_y: float) -> dict[str, float] | None:
    try:
        x = float(bbox.get("x") or 0)
        y = float(bbox.get("y") or 0)
        width = float(bbox.get("width") or 0)
        height = float(bbox.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {
        "left": round(x * scale_x, 2),
        "top": round(y * scale_y, 2),
        "width": round(width * scale_x, 2),
        "height": round(height * scale_y, 2),
    }


def _apply_default_structured_field_layouts(pages: list[dict[str, Any]], field_config: dict[str, Any]) -> None:
    """Bake default structured text group geometry into the generated HTML.

    The editor JavaScript can still update these fields after edits, but clean
    export strips scripts. Applying high-confidence grouped title layout here
    keeps the initial editor, reload state, and clean export in sync.
    """
    cover_title = field_config.get("cover_title") if isinstance(field_config.get("cover_title"), dict) else {}
    groups = cover_title.get("groups") if isinstance(cover_title.get("groups"), list) else []
    contacts = field_config.get("contacts") if isinstance(field_config.get("contacts"), list) else []
    amenities = field_config.get("amenities") if isinstance(field_config.get("amenities"), list) else []
    for page in pages:
        body = str(page.get("body") or "")
        if int(page.get("page_num") or 0) != 1:
            if contacts:
                body = _apply_contact_groups_to_body(body, page, contacts)
            if amenities:
                body = _apply_amenity_groups_to_body(body, page, amenities)
            page["body"] = body
            continue
        if groups:
            body = _apply_cover_title_groups_to_body(body, groups)
        if contacts:
            body = _apply_contact_groups_to_body(body, page, contacts)
        if amenities:
            body = _apply_amenity_groups_to_body(body, page, amenities)
        page["body"] = body


def _apply_amenity_groups_to_body(body: str, page: dict[str, Any], amenities: list[dict[str, Any]]) -> str:
    page_num = int(page.get("page_num") or 0)
    for amenity in amenities:
        anchor = amenity.get("anchor") if isinstance(amenity.get("anchor"), dict) else {}
        if int(anchor.get("page_num") or 0) != page_num:
            continue
        targets = [str(target) for target in amenity.get("targets", []) if target]
        if not targets:
            continue
        value = str(amenity.get("value") or "")
        if not value:
            continue
        bbox = amenity.get("bbox") if isinstance(amenity.get("bbox"), dict) else {}
        lines = [line for line in value.splitlines() if line.strip()] or [value]
        html_value = _html_from_plain_text(value)
        style_updates = {
            "white-space": "normal",
            "z-index": "7",
        }
        if bbox:
            line_count = max(1, len(lines))
            line_height = max(13.0, _number(bbox.get("height")) / line_count)
            style_updates.update(
                {
                    "left": _px(bbox.get("left", anchor.get("left"))),
                    "top": _px(bbox.get("top", anchor.get("top"))),
                    "width": _px(max(72.0, _number(bbox.get("width")) + 8.0)),
                    "min-height": _px(max(line_height, _number(bbox.get("height")) + 4.0)),
                    "line-height": _px(line_height),
                }
            )
        body = _replace_text_element_by_save_id(
            body,
            targets[0],
            html_value,
            style_updates=style_updates,
            attr_updates={
                "data-structured-field": "true",
                "data-amenity-label": "true",
                "data-field-kind": str(amenity.get("kind") or "plain"),
                "data-plain-text": value,
                "data-original-html": html_value,
            },
        )
        for hidden_id in list(amenity.get("hide_targets") or []):
            body = _replace_text_element_by_save_id(
                body,
                str(hidden_id),
                "",
                class_additions=["exact-field-hidden"],
                attr_updates={
                    "contenteditable": "false",
                    "aria-hidden": "true",
                    "data-structured-field": "true",
                    "data-plain-text": "",
                    "data-original-html": "",
                },
            )
    return body


def _apply_contact_groups_to_body(body: str, page: dict[str, Any], contacts: list[dict[str, Any]]) -> str:
    page_num = int(page.get("page_num") or 0)
    for contact in contacts:
        anchor = contact.get("anchor") if isinstance(contact.get("anchor"), dict) else {}
        if int(anchor.get("page_num") or 0) != page_num:
            continue
        targets = [str(target) for target in contact.get("targets", []) if target]
        if not targets:
            continue
        html_value = _contact_semantic_html(contact)
        style_updates = {
            "white-space": "normal",
            "line-height": "1.16",
            "z-index": "7",
        }
        font_style = anchor.get("font_style") if isinstance(anchor.get("font_style"), dict) else {}
        if font_style.get("color"):
            style_updates["--exact-contact-name-color"] = str(font_style.get("color"))
        bbox = contact.get("bbox") if isinstance(contact.get("bbox"), dict) else None
        if bbox:
            style_updates.update(
                {
                    "left": _px(bbox.get("left", anchor.get("left"))),
                    "top": _px(bbox.get("top", anchor.get("top"))),
                    "width": _px(max(180.0, _number(bbox.get("right")) - _number(bbox.get("left")) + 12.0)),
                    "min-height": _px(max(62.0, _number(bbox.get("bottom")) - _number(bbox.get("top")) + 8.0)),
                }
            )
            background_path = Path(str(page.get("background_path") or ""))
            if background_path.exists():
                style_updates["--exact-contact-mask"] = _sample_mask_colour(
                    background_path,
                    {
                        "left": _number(bbox.get("left")),
                        "top": _number(bbox.get("top")),
                        "width": max(180.0, _number(bbox.get("right")) - _number(bbox.get("left")) + 12.0),
                        "height": max(62.0, _number(bbox.get("bottom")) - _number(bbox.get("top")) + 8.0),
                    },
                )
        body = _replace_text_element_by_save_id(
            body,
            targets[0],
            html_value,
            style_updates=style_updates,
            attr_updates={
                "data-contact-semantic-block": "true",
                "data-structured-field": "true",
                "data-typography-role": "agent-contact",
                "data-plain-text": _contact_plain_text(contact),
                "data-original-html": html_value,
            },
        )
        for hidden_id in list(contact.get("hide_targets") or []):
            body = _replace_text_element_by_save_id(
                body,
                str(hidden_id),
                "",
                class_additions=["exact-field-hidden"],
                attr_updates={
                    "contenteditable": "false",
                    "aria-hidden": "true",
                    "data-structured-field": "true",
                    "data-plain-text": "",
                    "data-original-html": "",
                },
            )
    return body


def _contact_plain_text(contact: dict[str, Any]) -> str:
    return "\n".join(
        part
        for part in (
            str(contact.get("name") or "").strip(),
            str(contact.get("phone") or "").strip(),
            str(contact.get("email") or "").strip(),
        )
        if part
    )


def _contact_semantic_html(contact: dict[str, Any]) -> str:
    name = str(contact.get("name") or contact.get("label") or "").strip()
    phone = str(contact.get("phone") or "").strip()
    email = str(contact.get("email") or "").strip()
    prefixes = {str(item).upper() for item in contact.get("line_prefixes") or []}
    html_lines: list[str] = []
    if name:
        html_lines.append(f'<span class="exact-contact-name">{html.escape(name)}</span>')
    if phone:
        if "M" in prefixes or "T" in prefixes:
            html_lines.append(
                '<span class="exact-contact-row"><span class="exact-contact-prefix">M</span>'
                f'<span class="exact-contact-value">{html.escape(phone)}</span></span>'
            )
        else:
            html_lines.append(f'<span class="exact-contact-row">{html.escape(phone)}</span>')
    if email:
        if "E" in prefixes:
            html_lines.append(
                '<span class="exact-contact-row"><span class="exact-contact-prefix">E</span>'
                f'<span class="exact-contact-value">{html.escape(email)}</span></span>'
            )
        else:
            html_lines.append(f'<span class="exact-contact-row">{html.escape(email)}</span>')
    return '<span class="exact-contact-semantic">' + "\n".join(html_lines) + "</span>"


def _apply_cover_title_groups_to_body(body: str, groups: list[dict[str, Any]]) -> str:
    for group in groups:
        targets = [str(target) for target in group.get("targets", []) if target]
        if not targets:
            continue
        value = str(group.get("value") or "")
        orientation = str(group.get("orientation") or "")
        grouped = False
        if orientation == "rotated-counterclockwise":
            body = _replace_text_element_by_save_id(
                body,
                targets[0],
                _rotated_title_html(value),
                style_updates={
                    "left": _px(group.get("left")),
                    "top": _px(group.get("top")),
                    "width": _px(group.get("width")),
                    "height": _px(group.get("height")),
                    "white-space": "nowrap",
                    "display": "flex",
                    "align-items": "center",
                    "justify-content": "center",
                    "text-align": "center",
                },
                attr_updates={
                    "data-title-rotated": "true",
                    "data-structured-field": "true",
                    "data-plain-text": value,
                    "data-original-html": _rotated_title_html(value),
                },
            )
            grouped = True
        elif orientation in {"vertical-bottom-up", "vertical-glyphs"}:
            attr_updates = {
                "data-title-stack": "true",
                "data-structured-field": "true",
                "data-plain-text": value,
                "data-original-html": _title_stack_html(value),
            }
            if orientation == "vertical-glyphs":
                attr_updates["data-title-glyph-group"] = "true"
            body = _replace_text_element_by_save_id(
                body,
                targets[0],
                _title_stack_html(value),
                style_updates={
                    "left": _px(group.get("left")),
                    "top": _px(group.get("top")),
                    "width": _px(group.get("width")),
                    "height": _px(group.get("height")),
                    "white-space": "normal",
                    "display": "flex",
                    "align-items": "center",
                    "justify-content": "center",
                    "text-align": "center",
                },
                attr_updates=attr_updates,
            )
            grouped = True
        if grouped:
            for hidden_id in targets[1:]:
                body = _replace_text_element_by_save_id(
                    body,
                    hidden_id,
                    "",
                    class_additions=["exact-field-hidden"],
                    attr_updates={
                        "contenteditable": "false",
                        "aria-hidden": "true",
                        "data-structured-field": "true",
                        "data-plain-text": "",
                        "data-original-html": "",
                    },
                )
            continue
    return body


def _replace_text_element_by_save_id(
    body: str,
    save_id: str,
    inner_html: str | None,
    *,
    style_updates: dict[str, str] | None = None,
    attr_updates: dict[str, str] | None = None,
    class_additions: list[str] | None = None,
) -> str:
    pattern = re.compile(
        r'(<p\b(?=[^>]*\bdata-save-id="' + re.escape(save_id) + r'")[^>]*>)(.*?)(</p>)',
        flags=re.DOTALL | re.IGNORECASE,
    )

    def repl(match: re.Match[str]) -> str:
        tag = match.group(1)
        if style_updates:
            tag = _merge_tag_style(tag, style_updates)
        if attr_updates:
            for name, value in attr_updates.items():
                tag = _set_tag_attr(tag, name, value)
        if class_additions:
            tag = _add_tag_classes(tag, class_additions)
        return f"{tag}{match.group(2) if inner_html is None else inner_html}{match.group(3)}"

    return pattern.sub(repl, body, count=1)


def _set_tag_attr(tag: str, name: str, value: str) -> str:
    escaped = html.escape(str(value), quote=True)
    attr_re = re.compile(r'(\s' + re.escape(name) + r'=)(["\']).*?\2', flags=re.DOTALL | re.IGNORECASE)
    if attr_re.search(tag):
        return attr_re.sub(lambda match: f"{match.group(1)}{match.group(2)}{escaped}{match.group(2)}", tag, count=1)
    return tag[:-1] + f' {name}="{escaped}">'


def _add_tag_classes(tag: str, additions: list[str]) -> str:
    class_re = re.compile(r'(\sclass=)(["\'])(.*?)\2', flags=re.DOTALL | re.IGNORECASE)
    match = class_re.search(tag)
    if not match:
        return tag[:-1] + f' class="{html.escape(" ".join(additions), quote=True)}">'
    classes = [part for part in match.group(3).split() if part]
    for addition in additions:
        if addition not in classes:
            classes.append(addition)
    return tag[: match.start()] + f'{match.group(1)}{match.group(2)}{" ".join(classes)}{match.group(2)}' + tag[match.end() :]


def _merge_tag_style(tag: str, updates: dict[str, str]) -> str:
    style_re = re.compile(r'(\sstyle=)(["\'])(.*?)\2', flags=re.DOTALL | re.IGNORECASE)
    match = style_re.search(tag)
    styles: dict[str, str] = {}
    if match:
        for chunk in match.group(3).split(";"):
            if ":" not in chunk:
                continue
            key, value = chunk.split(":", 1)
            styles[key.strip().lower()] = value.strip()
    for key, value in updates.items():
        if value:
            styles[key] = value
    style_value = ";".join(f"{key}:{value}" for key, value in styles.items())
    if style_value:
        style_value += ";"
    if match:
        return tag[: match.start()] + f'{match.group(1)}{match.group(2)}{html.escape(style_value, quote=True)}{match.group(2)}' + tag[match.end() :]
    return tag[:-1] + f' style="{html.escape(style_value, quote=True)}">'


def _title_stack_html(value: str) -> str:
    characters = [char for char in str(value) if char != "\r"] or [""]
    return '<span class="exact-title-stack">' + "".join(f"<span>{html.escape(char)}</span>" for char in characters) + "</span>"


def _rotated_title_html(value: str) -> str:
    return f'<span class="exact-title-rotated">{html.escape(str(value))}</span>'


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _px(value: Any) -> str:
    return f"{_number(value):.2f}px"


def _font_label(stable: str, source: str) -> str:
    if source and source != stable:
        return f"{stable} ({source})"
    return stable


def _render_semantic_overlays(page: dict[str, Any], config: dict[str, Any]) -> str:
    page_num = str(page.get("page_num"))
    pieces: list[str] = []
    for logo in config.get("source_logos", []):
        if str(logo.get("page")) != page_num:
            continue
        key = str(logo.get("key") or f"source-logo-{page_num}")
        label = str(logo.get("label") or "Source facade logo")
        mask_colour = str(logo.get("mask_colour") or "var(--exact-dark)")
        mask_role = _mask_colour_role(mask_colour)
        mask_background = "var(--exact-dark)" if mask_role == "dark" else mask_colour
        default_asset = str(logo.get("default_asset_url") or "")
        default_class = " has-default-source-logo" if default_asset else ""
        default_markup = (
            '<div class="exact-source-logo-default">'
            f'<img src="{html.escape(default_asset, quote=True)}" alt="">'
            "</div>"
            if default_asset
            else ""
        )
        pieces.append(
            f'<div class="exact-source-logo-slot logo-zone{default_class}" '
            f'data-source-logo-slot="{html.escape(key, quote=True)}" '
            f'data-source-logo-kind="{html.escape(str(logo.get("kind") or "facade"), quote=True)}" '
            f'data-default-logo-kind="{html.escape(str(logo.get("kind") or "facade"), quote=True)}" '
            f'data-source-logo-mask-role="{mask_role}" '
            f'data-source-logo-mask-mode="{html.escape(str(logo.get("mask_mode") or "sample"), quote=True)}" '
            f'data-default-source-logo-mask-mode="{html.escape(str(logo.get("mask_mode") or "sample"), quote=True)}" '
            f'data-default-source-logo-asset="{html.escape(default_asset, quote=True)}" '
            f'data-logo-label="{html.escape(label, quote=True)}" '
            f'title="Replace {html.escape(label, quote=True)}" '
            f'style="left:{float(logo["left"]):.2f}px;top:{float(logo["top"]):.2f}px;'
            f'width:{float(logo["width"]):.2f}px;height:{float(logo["height"]):.2f}px;'
            f'--source-logo-mask:{html.escape(mask_background, quote=True)};" '
            f'data-original-left="{float(logo["left"]):.2f}" '
            f'data-original-top="{float(logo["top"]):.2f}" '
            f'data-original-width="{float(logo["width"]):.2f}" '
            f'data-original-height="{float(logo["height"]):.2f}">{default_markup}</div>'
        )

    for plan in config.get("space_plans", []):
        if str(plan.get("page")) != page_num:
            continue
        key = str(plan.get("key") or f"space-plan-p{page_num}")
        label = str(plan.get("label") or "Space plan")
        mask_colour = str(plan.get("mask_colour") or "#ffffff")
        mask_background = "var(--exact-dark)" if _mask_colour_role(mask_colour) == "dark" else mask_colour
        style = (
            f'left:{float(plan["left"]):.2f}px;top:{float(plan["top"]):.2f}px;'
            f'width:{float(plan["width"]):.2f}px;height:{float(plan["height"]):.2f}px;'
        )
        save_id = f"space-plan:{key}"
        asset_url = str(plan.get("asset_url") or "")
        slot_class = "exact-image-slot exact-space-plan-slot has-image" if asset_url else "exact-image-slot exact-space-plan-slot"
        escaped_asset = html.escape(asset_url, quote=True)
        photo_style = f' style="background-image:url(&quot;{escaped_asset}&quot;)"' if asset_url else ""
        photo_img = f'<img class="slot-photo-img" src="{escaped_asset}" alt="">' if asset_url else ""
        source_attr = f' data-source-image="{escaped_asset}"' if asset_url else ""
        mask_active_class = " has-image" if asset_url else ""
        pieces.append(
            f'<div class="exact-image-mask exact-space-plan-mask{mask_active_class}" '
            f'style="{style}background:{html.escape(mask_background, quote=True)};" '
            f'data-mask-for="{html.escape(save_id, quote=True)}" data-space-plan-mask="true"></div>'
            f'<div class="{slot_class}" '
            f'data-source-plan-slot="{html.escape(key, quote=True)}" '
            f'data-space-plan-region="{html.escape(key, quote=True)}" '
            f'data-save-id="{html.escape(save_id, quote=True)}" '
            f'data-image-slot="{html.escape(save_id, quote=True)}" '
            f'data-slot-id="{html.escape(key, quote=True)}" data-fit="contain" '
            f'title="Replace {html.escape(label, quote=True)}" '
            f'style="{style}" '
            f'data-original-left="{float(plan["left"]):.2f}" '
            f'data-original-top="{float(plan["top"]):.2f}" '
            f'data-original-width="{float(plan["width"]):.2f}" '
            f'data-original-height="{float(plan["height"]):.2f}"'
            f'{source_attr}>'
            f'<div class="slot-photo"{photo_style}>{photo_img}</div>'
            f'<input type="file" accept="image/*" aria-label="Replace {html.escape(label, quote=True)}"/>'
            '<div class="slot-chip">Space plan</div>'
            '<div class="slot-controls">'
            '<button type="button" data-fit="cover">Fill</button>'
            '<button type="button" data-fit="contain">Fit</button>'
            '<button type="button" data-fit="cover-left">Left crop</button>'
            '</div></div>'
        )

    for table_image in config.get("table_images", []):
        if str(table_image.get("page")) != page_num:
            continue
        key = str(table_image.get("key") or f"table-image-p{page_num}")
        label = str(table_image.get("label") or "Schedule")
        mask_colour = str(table_image.get("mask_colour") or "#ffffff")
        mask_background = "var(--exact-dark)" if _mask_colour_role(mask_colour) == "dark" else mask_colour
        style = (
            f'left:{float(table_image["left"]):.2f}px;top:{float(table_image["top"]):.2f}px;'
            f'width:{float(table_image["width"]):.2f}px;height:{float(table_image["height"]):.2f}px;'
        )
        save_id = f"table-image:{key}"
        asset_url = str(table_image.get("asset_url") or "")
        escaped_asset = html.escape(asset_url, quote=True)
        photo_style = f' style="background-image:url(&quot;{escaped_asset}&quot;)"' if asset_url else ""
        photo_img = f'<img class="slot-photo-img" src="{escaped_asset}" alt="">' if asset_url else ""
        source_attr = f' data-source-image="{escaped_asset}"' if asset_url else ""
        mask_active_class = " has-image" if asset_url else ""
        slot_class = "exact-image-slot exact-table-image-slot has-image" if asset_url else "exact-image-slot exact-table-image-slot"
        pieces.append(
            f'<div class="exact-image-mask exact-table-image-mask{mask_active_class}" '
            f'style="{style}background:{html.escape(mask_background, quote=True)};" '
            f'data-mask-for="{html.escape(save_id, quote=True)}" data-table-image-mask="true"></div>'
            f'<div class="{slot_class}" '
            f'data-table-image-region="{html.escape(key, quote=True)}" '
            f'data-save-id="{html.escape(save_id, quote=True)}" '
            f'data-image-slot="{html.escape(save_id, quote=True)}" '
            f'data-slot-id="{html.escape(key, quote=True)}" data-fit="contain" '
            f'title="Replace {html.escape(label, quote=True)}" '
            f'style="{style}" '
            f'data-original-left="{float(table_image["left"]):.2f}" '
            f'data-original-top="{float(table_image["top"]):.2f}" '
            f'data-original-width="{float(table_image["width"]):.2f}" '
            f'data-original-height="{float(table_image["height"]):.2f}"'
            f'{source_attr}>'
            f'<div class="slot-photo"{photo_style}>{photo_img}</div>'
            f'<input type="file" accept="image/*" aria-label="Replace {html.escape(label, quote=True)}"/>'
            f'<div class="slot-chip">{html.escape(label[:18], quote=True)}</div>'
            '<div class="slot-controls">'
            '<button type="button" data-fit="cover">Fill</button>'
            '<button type="button" data-fit="contain">Fit</button>'
            '<button type="button" data-fit="cover-left">Left crop</button>'
            '</div></div>'
        )

    agency_logos = config.get("agency_logos") if isinstance(config.get("agency_logos"), dict) else {}
    for logo_id, logo in agency_logos.items():
        if not isinstance(logo, dict) or str(logo.get("page")) != page_num:
            continue
        default_asset = str(logo.get("defaultAssetUrl") or "")
        class_name = html.escape(str(logo.get("className") or "agency"), quote=True)
        default_markup = (
            '<div class="exact-agency-logo-default">'
            f'<img src="{html.escape(default_asset, quote=True)}" alt="">'
            "</div>"
            if default_asset
            else ""
        )
        default_class = " has-default-agency-logo" if default_asset else ""
        logo_id_escaped = html.escape(str(logo_id), quote=True)
        label = html.escape(str(logo.get("label") or logo_id or "Agency"), quote=True)
        pieces.append(
            f'<div class="exact-brand-logo-slot{default_class}" '
            f'data-agency-logo-slot="{logo_id_escaped}" data-brand-logo-slot="{logo_id_escaped}" '
            f'data-default-agency-logo-asset="{html.escape(default_asset, quote=True)}" '
            f'data-source-detection="{html.escape(str(logo.get("sourceDetection") or ""), quote=True)}" '
            f'title="Replace {label} logo" '
            f'style="left:{float(logo["left"]):.2f}px;top:{float(logo["top"]):.2f}px;'
            f'width:{float(logo["width"]):.2f}px;height:{float(logo["height"]):.2f}px;">'
            f'{default_markup}<div class="logo-mask"></div>'
            f'<div class="logo-output logo-output-{class_name}"></div>'
            f'<input class="agency-logo-upload" type="file" accept="image/*" '
            f'data-agency-logo-slot-upload="{logo_id_escaped}" aria-label="Replace {label} logo">'
            '<div class="agency-logo-chip">Logo</div></div>'
        )

    for icon in _amenity_icon_values(config.get("amenity_icons", {})):
        if str(icon.get("page")) != page_num:
            continue
        icon_key = str(icon.get("key") or icon.get("id"))
        icon_id = str(icon.get("icon_id") or icon.get("defaultIcon") or "office")
        pieces.append(
            '<button type="button" class="highlight-icon exact-amenity-icon-slot" '
            f'data-amenity-icon-slot="{html.escape(icon_key, quote=True)}" '
            f'data-icon-slot="{html.escape(icon_key, quote=True)}" '
            f'data-icon-id="{html.escape(icon_id, quote=True)}" '
            f'data-default-icon-id="{html.escape(icon_id, quote=True)}" '
            f'title="Change {html.escape(str(icon.get("label") or "amenity icon"), quote=True)} icon" '
            f'style="left:{float(icon["left"]):.2f}px;top:{float(icon["top"]):.2f}px;'
            f'width:{float(icon["width"]):.2f}px;height:{float(icon["height"]):.2f}px;"></button>'
        )

    for icon in config.get("service_icons", []):
        if str(icon.get("page")) != page_num:
            continue
        icon_key = str(icon.get("key") or icon.get("id"))
        icon_id = str(icon.get("icon_id") or icon.get("defaultIcon") or "office")
        pieces.append(
            '<button type="button" class="highlight-icon exact-amenity-icon-slot exact-service-icon-slot" '
            f'data-service-icon-slot="{html.escape(icon_key, quote=True)}" '
            f'data-service-group="{html.escape(str(icon.get("group") or ""), quote=True)}" '
            f'data-icon-slot="{html.escape(icon_key, quote=True)}" '
            f'data-icon-id="{html.escape(icon_id, quote=True)}" '
            f'data-default-icon-id="{html.escape(icon_id, quote=True)}" '
            f'title="Change {html.escape(str(icon.get("label") or "service icon"), quote=True)} icon" '
            f'style="left:{float(icon["left"]):.2f}px;top:{float(icon["top"]):.2f}px;'
            f'width:{float(icon["width"]):.2f}px;height:{float(icon["height"]):.2f}px;"></button>'
        )

    map_regions = config.get("map_regions") if isinstance(config.get("map_regions"), list) else []
    if not map_regions and config.get("map_region"):
        map_regions = [config.get("map_region") or {}]
    for map_index, map_region in enumerate(map_regions, start=1):
        if not map_region or str(map_region.get("page")) != page_num:
            continue
        slot_id = str(map_region.get("id") or f"map-page-{page_num}-{map_index}")
        pieces.append(
            f'<div class="map-area exact-map-area map-loaded" data-slot-id="{html.escape(slot_id, quote=True)}" '
            f'data-save-id="{html.escape(str(map_region["save_id"]), quote=True)}" '
            'data-exact-map-area="true" data-map-v1="source-pdf" data-map-source="pdf-exact" '
            f'data-map-vibe="{html.escape(str(map_region.get("vibe") or ""), quote=True)}" '
            f'data-map-center="{html.escape(json.dumps(map_region.get("center") or {}), quote=True)}" '
            f'data-map-content="{html.escape(json.dumps(map_region.get("content") or {}), quote=True)}" '
            f'data-map-label-count="{len(map_region.get("labels", []))}" '
            f'data-map-labels="{html.escape(json.dumps(map_region.get("labels", [])), quote=True)}" '
            f'style="left:{float(map_region.get("left", 0)):.2f}px;top:{float(map_region.get("top", 0)):.2f}px;'
            f'width:{float(map_region.get("width", page.get("width", 0))):.2f}px;height:{float(map_region.get("height", page.get("height", 0))):.2f}px;">'
            '<div class="exact-map-controls">'
            '<span class="exact-map-status" data-exact-map-status>PDF map preserved</span>'
            '<button type="button" class="map-generate-btn" data-exact-map-generate>Regenerate</button>'
            "</div></div>"
        )
    return "".join(pieces)


def _build_source_logo_defs(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find the brochure's existing facade marks so logo editing replaces them in-place."""
    slots: list[dict[str, Any]] = []
    for page in pages:
        background_path = Path(str(page.get("background_path") or ""))
        if not background_path.is_file():
            continue
        if not _should_detect_source_logos_for_page(page):
            continue
        scale = _background_to_page_scale(background_path, page)
        page_slots: list[dict[str, Any]] = []
        for predicate in _source_logo_predicates_for_page(page):
            page_slots = []
            for component in _detect_accent_components(background_path, predicate):
                page_component = _scale_component_to_page(component, scale)
                default_mark = _best_source_image_mark(page_component, page.get("source_image_marks", []))
                is_cover_brand = _looks_like_cover_brand_mark_fragment(page_component, page)
                if not default_mark and not is_cover_brand:
                    page_num = int(page.get("page_num") or 0)
                    if page_num > 1:
                        if predicate is not _is_yellow_accent_pixel:
                            continue
                        page_height = float(page.get("height") or 1)
                        if float(page_component.get("top") or 0) > max(180.0, page_height * 0.18):
                            continue
                    if predicate is _is_light_source_logo_mark_pixel and not (
                        _looks_like_small_header_mark(page_component, page)
                        and _is_centered_photo_header_mark(page_component, page)
                    ):
                        continue
                    if predicate is _is_coloured_source_logo_mark_pixel:
                        yellow_count = _count_accent_pixels(
                            background_path,
                            int(component["left"]),
                            int(component["top"]),
                            int(component["left"] + component["width"]),
                            int(component["top"] + component["height"]),
                            _is_yellow_accent_pixel,
                        )
                        if yellow_count < 50 and not _has_heading_context_below(page_component, page.get("text_entries", [])):
                            continue
                    if predicate is _is_accent_pixel and not (
                        _has_heading_context_below(page_component, page.get("text_entries", []))
                        or _has_subject_property_context_below(page_component, page.get("text_entries", []))
                        or (
                            _looks_like_small_header_mark(page_component, page)
                            and _is_centered_photo_header_mark(page_component, page)
                        )
                    ):
                        continue
                if not _looks_like_source_logo_component(page_component, page):
                    continue
                bbox = {
                    "left": round(page_component["left"], 2),
                    "top": round(page_component["top"], 2),
                    "width": round(page_component["width"], 2),
                    "height": round(page_component["height"], 2),
                }
                overlaps_photo = _overlaps_image_slots(page_component, page.get("image_slots", []), threshold=0.25)
                if overlaps_photo and not default_mark and not is_cover_brand:
                    if not (
                        predicate is _is_light_source_logo_mark_pixel
                        and _looks_like_small_header_mark(page_component, page)
                        and _is_centered_photo_header_mark(page_component, page)
                    ):
                        continue
                if _component_outside_page_bounds(page_component, page):
                    continue
                page_slots.append(
                    {
                        "page": str(page.get("page_num")),
                        "key": f'facade-p{page.get("page_num")}-{len(page_slots) + 1}',
                        "label": _source_logo_label(page, page_component),
                        "kind": "facade",
                        "mask_colour": _sample_mask_colour(background_path, _component_bbox(component)),
                        "mask_mode": "photo" if overlaps_photo else "sample",
                        "default_asset_url": str((default_mark or {}).get("asset_url") or ""),
                        "accent_count": component.get("accent_count", 0),
                        "density": component.get("density", 0),
                        **bbox,
                    }
                )
            if page_slots:
                break
        page_slots = _merge_source_logo_slot_fragments(page_slots, page)
        slots.extend(_dedupe_source_logo_slots(page_slots))
    return slots


def _source_logo_predicates_for_page(page: dict[str, Any]) -> tuple[Any, ...]:
    if page.get("source_preserved_edit") and not page.get("source_image_marks"):
        return (_is_yellow_accent_pixel, _is_coloured_source_logo_mark_pixel)
    return (_is_yellow_accent_pixel, _is_coloured_source_logo_mark_pixel, _is_light_source_logo_mark_pixel, _is_accent_pixel)


def _should_detect_source_logos_for_page(page: dict[str, Any]) -> bool:
    if page.get("source_preserved_edit") and not page.get("source_image_marks"):
        try:
            return int(page.get("page_num") or 0) == 1
        except (TypeError, ValueError):
            return False
    return True


def _detect_accent_components(image_path: Path, pixel_test: Any | None = None) -> list[dict[str, float]]:
    predicate = pixel_test or _is_accent_pixel
    rgb = _load_rgb_image(image_path)
    if rgb is None:
        return []
    width, height = rgb.size
    pixels = rgb.load()
    block = 4
    grid_w = (width + block - 1) // block
    grid_h = (height + block - 1) // block
    accent_grid = [[False for _ in range(grid_w)] for _ in range(grid_h)]
    for gy in range(grid_h):
        y0 = gy * block
        y1 = min(height, y0 + block)
        for gx in range(grid_w):
            x0 = gx * block
            x1 = min(width, x0 + block)
            found = False
            for y in range(y0, y1):
                if found:
                    break
                for x in range(x0, x1):
                    if predicate(pixels[x, y]):
                        found = True
                        break
            accent_grid[gy][gx] = found

    dilated = [row[:] for row in accent_grid]
    radius = 1
    for gy, row in enumerate(accent_grid):
        for gx, active in enumerate(row):
            if not active:
                continue
            for yy in range(max(0, gy - radius), min(grid_h, gy + radius + 1)):
                for xx in range(max(0, gx - radius), min(grid_w, gx + radius + 1)):
                    dilated[yy][xx] = True

    seen = [[False for _ in range(grid_w)] for _ in range(grid_h)]
    components: list[dict[str, float]] = []
    for gy in range(grid_h):
        for gx in range(grid_w):
            if seen[gy][gx] or not dilated[gy][gx]:
                continue
            stack = [(gx, gy)]
            seen[gy][gx] = True
            cells: list[tuple[int, int]] = []
            while stack:
                x, y = stack.pop()
                cells.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < grid_w and 0 <= ny < grid_h and not seen[ny][nx] and dilated[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            min_x = max(0, (min(x for x, _ in cells) - radius) * block)
            min_y = max(0, (min(y for _, y in cells) - radius) * block)
            max_x = min(width, (max(x for x, _ in cells) + radius + 2) * block)
            max_y = min(height, (max(y for _, y in cells) + radius + 2) * block)
            count = _count_accent_pixels_from_loaded(pixels, width, height, min_x, min_y, max_x, max_y, predicate)
            area = max(1, (max_x - min_x) * (max_y - min_y))
            if count < 50 or area < 500:
                continue
            components.append(
                {
                    "left": float(min_x),
                    "top": float(min_y),
                    "width": float(max_x - min_x),
                    "height": float(max_y - min_y),
                    "accent_count": float(count),
                    "density": float(count) / float(area),
                }
            )
    return sorted(components, key=lambda item: item["accent_count"], reverse=True)


def _background_to_page_scale(image_path: Path, page: dict[str, Any]) -> tuple[float, float]:
    try:
        rgb = _load_rgb_image(image_path)
        if rgb is None:
            return (1.0, 1.0)
        image_width = float(rgb.width or 1)
        image_height = float(rgb.height or 1)
    except Exception:
        return (1.0, 1.0)
    page_width = float(page.get("width") or image_width or 1)
    page_height = float(page.get("height") or image_height or 1)
    return (page_width / max(1.0, image_width), page_height / max(1.0, image_height))


def _scale_component_to_page(component: dict[str, float], scale: tuple[float, float]) -> dict[str, float]:
    scale_x, scale_y = scale
    scaled = dict(component)
    scaled["left"] = float(component.get("left") or 0) * scale_x
    scaled["top"] = float(component.get("top") or 0) * scale_y
    scaled["width"] = float(component.get("width") or 0) * scale_x
    scaled["height"] = float(component.get("height") or 0) * scale_y
    return scaled


def _component_bbox(component: dict[str, float]) -> dict[str, float]:
    return {
        "left": float(component.get("left") or 0),
        "top": float(component.get("top") or 0),
        "width": float(component.get("width") or 0),
        "height": float(component.get("height") or 0),
    }


def _count_accent_pixels_from_loaded(
    pixels: Any,
    width: int,
    height: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
    pixel_test: Any | None = None,
) -> int:
    predicate = pixel_test or _is_accent_pixel
    return sum(
        1
        for y in range(max(0, top), min(height, bottom))
        for x in range(max(0, left), min(width, right))
        if predicate(pixels[x, y])
    )


def _count_accent_pixels(image_path: Path, left: int, top: int, right: int, bottom: int, pixel_test: Any | None = None) -> int:
    try:
        predicate = pixel_test or _is_accent_pixel
        rgb = _load_rgb_image(image_path)
        if rgb is None:
            return 0
        pixels = rgb.load()
        return _count_accent_pixels_from_loaded(pixels, rgb.width, rgb.height, left, top, right, bottom, predicate)
    except Exception:
        return 0


def _is_accent_pixel(pixel: tuple[int, int, int] | Any) -> bool:
    red, green, blue = [int(value) for value in pixel[:3]]
    source_mark = _is_source_logo_mark_pixel(pixel)
    near_black_mark = max(red, green, blue) <= 48 and (max(red, green, blue) - min(red, green, blue)) <= 18
    return source_mark or near_black_mark


def _is_source_logo_mark_pixel(pixel: tuple[int, int, int] | Any) -> bool:
    return _is_coloured_source_logo_mark_pixel(pixel) or _is_light_source_logo_mark_pixel(pixel)


def _is_coloured_source_logo_mark_pixel(pixel: tuple[int, int, int] | Any) -> bool:
    red, green, blue = [int(value) for value in pixel[:3]]
    yellow_accent = _is_yellow_accent_pixel(pixel)
    warm_brown_accent = 55 <= red <= 165 and 25 <= green <= 110 and blue <= 95 and red >= green + 12 and red >= blue + 22
    return yellow_accent or warm_brown_accent


def _is_light_source_logo_mark_pixel(pixel: tuple[int, int, int] | Any) -> bool:
    red, green, blue = [int(value) for value in pixel[:3]]
    return red >= 225 and green >= 225 and blue >= 225


def _is_yellow_accent_pixel(pixel: tuple[int, int, int] | Any) -> bool:
    red, green, blue = [int(value) for value in pixel[:3]]
    return red > 135 and green > 125 and blue < 120 and (red - blue) > 80 and (green - blue) > 80


def _looks_like_source_logo_component(component: dict[str, float], page: dict[str, Any]) -> bool:
    width = component["width"]
    height = component["height"]
    if width <= 0 or height <= 0:
        return False
    density = component.get("density", 0)
    aspect = width / height
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    if width > page_width * 0.92 and height > page_height * 0.92:
        return False

    is_cover_brand = _looks_like_cover_brand_mark_fragment(component, page)
    overlaps_image_slot = _overlaps_image_slots(component, page.get("image_slots", []), threshold=0.45)
    if overlaps_image_slot and not (
        (_looks_like_small_header_mark(component, page) and _is_centered_header_mark(component, page))
        or is_cover_brand
    ):
        return False
    if _overlaps_text_component(component, page.get("text_entries", [])):
        return False
    if is_cover_brand:
        return True
    if _is_probably_text_component(component, page.get("text_entries", [])) and not _has_subject_property_context_below(component, page.get("text_entries", [])):
        return False
    if _looks_like_small_header_mark(component, page):
        return True
    if density > 0.28:
        return False

    is_large_facade = height > page_height * 0.45 and 0.34 <= aspect <= 0.82 and 0.015 <= density <= 0.13
    if is_large_facade:
        return True

    is_small_facade = 38 <= width <= 125 and 72 <= height <= 190 and 0.38 <= aspect <= 0.9 and 0.045 <= density <= 0.24
    if not is_small_facade:
        return False

    near_page_top = component["top"] < 165
    return near_page_top


def _overlaps_text_component(component: dict[str, float], entries: list[dict[str, Any]]) -> bool:
    comp_rect = {
        "left": float(component["left"]),
        "top": float(component["top"]),
        "width": float(component["width"]),
        "height": float(component["height"]),
    }
    for entry in entries:
        text = str(entry.get("plain") or "").strip()
        if not text:
            continue
        left = entry.get("left")
        top = entry.get("top")
        if left is None or top is None:
            continue
        font_size = _font_size_px(entry) or 18
        text_rect = {
            "left": float(left),
            "top": float(top),
            "width": max(10.0, min(520.0, len(text) * font_size * 0.56)),
            "height": max(12.0, font_size * 1.35),
        }
        overlap = _rect_intersection_area(comp_rect, text_rect)
        if overlap / max(1.0, min(comp_rect["width"] * comp_rect["height"], text_rect["width"] * text_rect["height"])) > 0.35:
            return True
    return False


def _is_probably_text_component(component: dict[str, float], entries: list[dict[str, Any]]) -> bool:
    center_x = component["left"] + component["width"] / 2
    for entry in entries:
        compact = _compact_text(str(entry.get("plain") or ""))
        if len(compact) < 5:
            continue
        if _looks_like_heading_context_entry(entry):
            continue
        top = float(entry.get("top") or -10_000)
        left = float(entry.get("left") or -10_000)
        if component["top"] - 8 <= top <= component["top"] + component["height"] + 8 and abs(left - center_x) < 360:
            return True
    return False


def _has_heading_context_below(component: dict[str, float], entries: list[dict[str, Any]]) -> bool:
    bottom = component["top"] + component["height"]
    center_x = component["left"] + component["width"] / 2
    for entry in entries:
        text = str(entry.get("plain") or "").strip()
        compact = _compact_text(text)
        if not compact:
            continue
        top = float(entry.get("top") or -10_000)
        left = float(entry.get("left") or -10_000)
        if top < bottom - 20 or top > bottom + 170:
            continue
        if abs(left - center_x) > 260:
            continue
        if _looks_like_short_heading_context(text) or _looks_like_heading_context_entry(entry):
            return True
    return False


def _has_subject_property_context_below(component: dict[str, float], entries: list[dict[str, Any]]) -> bool:
    bottom = component["top"] + component["height"]
    center_x = component["left"] + component["width"] / 2
    for entry in entries:
        text = str(entry.get("plain") or "").strip()
        if not text:
            continue
        top = float(entry.get("top") or -10_000)
        left = float(entry.get("left") or -10_000)
        if top < bottom - 28 or top > bottom + 180:
            continue
        if abs(left - center_x) > 320:
            continue
        if _looks_like_subject_property_label(text):
            return True
    return False


def _looks_like_heading_context_entry(entry: dict[str, Any]) -> bool:
    text = str(entry.get("plain") or "").strip()
    role = str(entry.get("typography_role") or "")
    if role in {"cover-title", "section-heading"}:
        return True
    return _font_size_px(entry) >= 28 and _looks_like_short_heading_context(text)


def _looks_like_short_heading_context(text: str) -> bool:
    value = str(text or "").strip()
    if not value or len(value) > 90:
        return False
    if "@" in value or re.search(r"\b0\d[\d\s]{8,}\b", value):
        return False
    words = re.findall(r"[A-Za-z0-9]+", value)
    if not words or len(words) > 4:
        return False
    upperish = sum(1 for word in words if word.isupper() or word.isdigit())
    titleish = sum(1 for word in words if word[:1].isupper()) >= max(1, len(words) - 1)
    return upperish >= max(1, len(words) - 1) or titleish


def _overlaps_image_slots(component: dict[str, float], slots: list[dict[str, Any]], *, threshold: float) -> bool:
    comp_rect = {
        "left": component["left"],
        "top": component["top"],
        "width": component["width"],
        "height": component["height"],
    }
    for slot in slots:
        slot_rect = {
            "left": float(slot.get("left") or 0),
            "top": float(slot.get("top") or 0),
            "width": float(slot.get("width") or 0),
            "height": float(slot.get("height") or 0),
        }
        if _rect_overlap_ratio_py(comp_rect, slot_rect) >= threshold:
            return True
    return False


def _rect_overlap_ratio_py(a: dict[str, float], b: dict[str, float]) -> float:
    intersection = _rect_intersection_area(a, b)
    area = max(1.0, a["width"] * a["height"])
    return intersection / area


def _rect_intersection_area(a: dict[str, float], b: dict[str, float]) -> float:
    left = max(a["left"], b["left"])
    top = max(a["top"], b["top"])
    right = min(a["left"] + a["width"], b["left"] + b["width"])
    bottom = min(a["top"] + a["height"], b["top"] + b["height"])
    return max(0.0, right - left) * max(0.0, bottom - top)


def _best_source_image_mark(component: dict[str, float], marks: list[dict[str, Any]]) -> dict[str, Any] | None:
    component_rect = {
        "left": float(component.get("left") or 0),
        "top": float(component.get("top") or 0),
        "width": float(component.get("width") or 0),
        "height": float(component.get("height") or 0),
    }
    best: tuple[float, dict[str, Any]] | None = None
    for mark in marks:
        mark_rect = {
            "left": float(mark.get("left") or 0),
            "top": float(mark.get("top") or 0),
            "width": float(mark.get("width") or 0),
            "height": float(mark.get("height") or 0),
        }
        overlap = _rect_intersection_area(component_rect, mark_rect)
        base = max(1.0, min(component_rect["width"] * component_rect["height"], mark_rect["width"] * mark_rect["height"]))
        ratio = overlap / base
        if ratio < 0.35:
            continue
        if best is None or ratio > best[0]:
            best = (ratio, mark)
    return best[1] if best else None


def _dedupe_source_logo_slots(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for slot in sorted(slots, key=lambda item: (float(item["top"]), float(item["left"]))):
        rect = {key: float(slot[key]) for key in ("left", "top", "width", "height")}
        if any(_rect_overlap_ratio_py(rect, {key: float(existing[key]) for key in ("left", "top", "width", "height")}) > 0.55 for existing in kept):
            continue
        slot["key"] = f'facade-p{slot["page"]}-{len(kept) + 1}'
        kept.append(slot)
    return kept


def _merge_source_logo_slot_fragments(slots: list[dict[str, Any]], page: dict[str, Any]) -> list[dict[str, Any]]:
    if len(slots) <= 1:
        return slots
    grouped: list[list[dict[str, Any]]] = []
    for slot in sorted(slots, key=lambda item: (float(item["top"]), float(item["left"]))):
        matched = False
        rect = {key: float(slot[key]) for key in ("left", "top", "width", "height")}
        for group in grouped:
            union = _union_slot_bbox(group)
            expanded = {
                "left": union["left"] - 42,
                "top": union["top"] - 42,
                "width": union["width"] + 84,
                "height": union["height"] + 84,
            }
            if _rect_overlap_ratio_py(rect, expanded) > 0:
                group.append(slot)
                matched = True
                break
        if not matched:
            grouped.append([slot])

    merged: list[dict[str, Any]] = []
    for group in grouped:
        if len(group) == 1:
            merged.append(group[0])
            continue
        bbox = _union_slot_bbox(group)
        page_height = float(page.get("height") or 1)
        aspect = bbox["width"] / max(1.0, bbox["height"])
        combined_density = sum(float(item.get("accent_count") or 0) for item in group) / max(1.0, bbox["width"] * bbox["height"])
        if (
            bbox["height"] > page_height * 0.38
            and 0.25 <= aspect <= 1.2
            and 0.006 <= combined_density <= 0.18
        ) or _looks_like_cover_brand_bbox(bbox, page, combined_density):
            base = dict(group[0])
            base.update({key: round(bbox[key], 2) for key in ("left", "top", "width", "height")})
            base["accent_count"] = sum(float(item.get("accent_count") or 0) for item in group)
            base["density"] = combined_density
            base["mask_colour"] = base.get("mask_colour") or "#ffffff"
            merged.append(base)
        else:
            merged.extend(group)
    return merged


def _union_slot_bbox(slots: list[dict[str, Any]]) -> dict[str, float]:
    left = min(float(slot["left"]) for slot in slots)
    top = min(float(slot["top"]) for slot in slots)
    right = max(float(slot["left"]) + float(slot["width"]) for slot in slots)
    bottom = max(float(slot["top"]) + float(slot["height"]) for slot in slots)
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def _looks_like_small_header_mark(component: dict[str, float], page: dict[str, Any]) -> bool:
    width = float(component.get("width") or 0)
    height = float(component.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    density = float(component.get("density") or 0)
    aspect = width / height
    near_top = float(component.get("top") or 0) <= min(95.0, page_height * 0.16)
    plausible_size = 18 <= width <= max(110.0, page_width * 0.16) and 10 <= height <= max(88.0, page_height * 0.14)
    plausible_shape = 0.25 <= aspect <= 4.8 and 0.025 <= density <= 0.72
    return near_top and plausible_size and plausible_shape


def _looks_like_cover_brand_mark_fragment(component: dict[str, float], page: dict[str, Any]) -> bool:
    """Detect large cover brand marks that are extracted as disconnected glyph chunks."""
    if int(page.get("page_num") or 0) != 1:
        return False
    width = float(component.get("width") or 0)
    height = float(component.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    density = float(component.get("density") or 0)
    center_x = float(component.get("left") or 0) + width / 2
    top = float(component.get("top") or 0)
    centered = page_width * 0.30 <= center_x <= page_width * 0.72
    cover_band = page_height * 0.20 <= top <= page_height * 0.54
    plausible_size = 42 <= width <= max(280.0, page_width * 0.24) and 64 <= height <= max(240.0, page_height * 0.24)
    sparse_mark = 0.001 <= density <= 0.12
    return centered and cover_band and plausible_size and sparse_mark and _has_cover_title_context_below(component, page)


def _looks_like_cover_brand_bbox(bbox: dict[str, float], page: dict[str, Any], density: float) -> bool:
    width = float(bbox.get("width") or 0)
    height = float(bbox.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    aspect = width / height
    center_x = float(bbox.get("left") or 0) + width / 2
    top = float(bbox.get("top") or 0)
    centered = page_width * 0.32 <= center_x <= page_width * 0.68
    cover_band = page_height * 0.18 <= top <= page_height * 0.54
    plausible_size = page_width * 0.06 <= width <= page_width * 0.28 and page_height * 0.06 <= height <= page_height * 0.24
    return centered and cover_band and plausible_size and 0.001 <= density <= 0.12 and 0.6 <= aspect <= 2.8


def _has_cover_title_context_below(component: dict[str, float], page: dict[str, Any]) -> bool:
    bottom = float(component.get("top") or 0) + float(component.get("height") or 0)
    center_x = float(component.get("left") or 0) + float(component.get("width") or 0) / 2
    page_width = float(page.get("width") or 1)
    for entry in page.get("text_entries") or []:
        text = str(entry.get("plain") or "").strip()
        if not text:
            continue
        top = float(entry.get("top") or -10_000)
        if top < bottom + 24 or top > bottom + 300:
            continue
        left = float(entry.get("left") or -10_000)
        if abs(left - center_x) > page_width * 0.34:
            continue
        if (
            _looks_like_subject_property_label(text)
            or _looks_like_short_heading_context(text)
            or _looks_like_spaced_cover_property_title(text)
        ):
            return True
    return False


def _looks_like_spaced_cover_property_title(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return False
    if "@" in text or re.search(r"\b0\d[\d\s]{8,}\b", text):
        return False
    compact = _compact_text(text)
    if not re.match(r"^\d{1,4}", compact):
        return False
    alpha = re.sub(r"[^a-z]+", "", compact)
    if len(alpha) < 6:
        return False
    words = re.findall(r"[A-Za-z0-9]+", text)
    if not words:
        return False
    single_letter_words = sum(1 for word in words if len(word) == 1 and word.isalpha())
    upperish = sum(1 for word in words if word.isupper() or word.isdigit())
    return single_letter_words >= 4 or upperish >= max(2, len(words) - 1)


def _is_centered_header_mark(component: dict[str, float], page: dict[str, Any]) -> bool:
    page_width = float(page.get("width") or 1)
    center_x = float(component.get("left") or 0) + float(component.get("width") or 0) / 2
    return page_width * 0.30 <= center_x <= page_width * 0.70


def _is_centered_photo_header_mark(component: dict[str, float], page: dict[str, Any]) -> bool:
    page_width = float(page.get("width") or 1)
    center_x = float(component.get("left") or 0) + float(component.get("width") or 0) / 2
    return page_width * 0.36 <= center_x <= page_width * 0.64


def _component_outside_page_bounds(component: dict[str, float], page: dict[str, Any]) -> bool:
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    left = float(component.get("left") or 0)
    top = float(component.get("top") or 0)
    width = float(component.get("width") or 0)
    height = float(component.get("height") or 0)
    margin_x = max(4.0, page_width * 0.025)
    margin_y = max(4.0, page_height * 0.025)
    return (
        left < -margin_x
        or top < -margin_y
        or left + width > page_width + margin_x
        or top + height > page_height + margin_y
    )


def _source_logo_label(page: dict[str, Any], component: dict[str, float]) -> str:
    page_num = page.get("page_num")
    if component["height"] > float(page.get("height") or 1) * 0.45:
        return f"large facade mark page {page_num}"
    if _has_heading_context_below(component, page.get("text_entries", [])):
        return f"section facade mark page {page_num}"
    return f"header facade mark page {page_num}"


def _find_text_entry(
    entries: list[dict[str, Any]],
    label: str,
    *,
    page_num: int | None = None,
    contains: bool = False,
) -> dict[str, Any] | None:
    expected = _compact_text(label)
    candidates = [entry for entry in entries if page_num is None or entry.get("page_num") == page_num]
    for entry in candidates:
        actual = _compact_text(entry.get("plain", ""))
        if (contains and expected in actual) or (not contains and actual == expected):
            return entry
    return None


def _build_cover_title_field(
    entries: list[dict[str, Any]],
    *,
    cover_title_bboxes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    page_entries = [entry for entry in entries if entry.get("page_num") == 1]
    if not page_entries:
        return {"targets": [], "value": "", "groups": []}

    candidates = [
        entry
        for entry in page_entries
        if entry.get("typography_role") == "cover-title"
    ]
    if not candidates:
        sizes = [_font_size_px(entry) for entry in page_entries]
        median = _median_number(sizes) or 0
        candidates = [
            entry
            for entry in page_entries
            if _font_size_px(entry) >= max(28.0, median * 1.45)
            and _looks_like_cover_title_piece(str(entry.get("plain") or ""))
        ]
    if not candidates:
        candidates = [
            entry
            for entry in sorted(page_entries, key=lambda item: (float(item.get("top") or 0), float(item.get("left") or 0)))
            if _looks_like_cover_title_piece(str(entry.get("plain") or ""))
        ][:4]

    vertical_groups = _vertical_cover_title_groups(candidates)
    if vertical_groups:
        _apply_model_cover_title_bboxes(vertical_groups, cover_title_bboxes or {})
        targets = [
            save_id
            for group in vertical_groups
            for save_id in group.get("targets", [])
            if save_id
        ]
        return {
            "targets": targets,
            "value": "\n".join(str(group.get("value") or "") for group in vertical_groups if group.get("value")),
            "groups": vertical_groups,
        }

    candidates = sorted(candidates, key=lambda item: (float(item.get("top") or 0), float(item.get("left") or 0)))
    return {
        "targets": [entry["save_id"] for entry in candidates if entry.get("save_id")],
        "value": "\n".join(str(entry.get("plain") or "").strip() for entry in candidates if str(entry.get("plain") or "").strip()),
        "groups": [],
    }


def _vertical_cover_title_groups(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    glyphs = [
        entry
        for entry in candidates
        if entry.get("save_id")
        and 1 <= len(str(entry.get("plain") or "").strip()) <= 2
        and str(entry.get("plain") or "").strip()
    ]
    if len(glyphs) < 6 or len(glyphs) < max(4, int(len(candidates) * 0.65)):
        return []

    columns: list[list[dict[str, Any]]] = []
    for entry in sorted(glyphs, key=lambda item: (float(item.get("left") or 0), float(item.get("top") or 0))):
        left = float(entry.get("left") or 0)
        matched = False
        for column in columns:
            column_left = _median_number([float(item.get("left") or 0) for item in column])
            if abs(left - column_left) <= 18:
                column.append(entry)
                matched = True
                break
        if not matched:
            columns.append([entry])

    columns = [column for column in columns if len(column) >= 2]
    if len(columns) < 2:
        return []

    groups: list[dict[str, Any]] = []
    for column in sorted(columns, key=lambda group: _median_number([float(item.get("left") or 0) for item in group])):
        ordered = sorted(column, key=lambda item: float(item.get("top") or 0), reverse=True)
        value = "".join(str(item.get("plain") or "").strip() for item in ordered)
        if len(_compact_text(value)) < 2:
            continue
        left, top, width, height = _entry_group_bbox(ordered)
        groups.append(
            {
                "value": value,
                "targets": [str(item.get("save_id") or "") for item in ordered if item.get("save_id")],
                "segment_lengths": [max(1, len(str(item.get("plain") or "").strip())) for item in ordered if item.get("save_id")],
                "orientation": "vertical-glyphs",
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }
        )

    return groups if len(groups) >= 2 else []


def _apply_model_cover_title_bboxes(
    groups: list[dict[str, Any]],
    cover_title_bboxes: dict[str, dict[str, Any]],
) -> None:
    """Use PyMuPDF geometry to correct Poppler vertical title group hitboxes."""
    for group in groups:
        key = _compact_text(str(group.get("value") or ""))
        bbox = cover_title_bboxes.get(key)
        if not bbox:
            bbox = _best_cover_title_bbox_match(key, cover_title_bboxes)
        if not bbox:
            continue
        group.update({name: bbox[name] for name in ("left", "top", "width", "height") if name in bbox})
        if bbox.get("value"):
            group["value"] = str(bbox.get("value") or group.get("value") or "")
        group["bbox_source"] = bbox.get("source") or "PyMuPDF text geometry"
        if group["bbox_source"] == "PyMuPDF text span" and _looks_like_rotated_cover_title_line(str(group.get("value") or "")):
            group["orientation"] = "rotated-counterclockwise"


def _best_cover_title_bbox_match(
    key: str,
    cover_title_bboxes: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Find a near-match when Poppler has dropped a glyph from cover lettering."""
    if not key:
        return None
    best_key = ""
    best_distance = 999
    for candidate in cover_title_bboxes:
        if not candidate:
            continue
        distance = _small_edit_distance(key, candidate)
        allowance = max(1, int(round(max(len(key), len(candidate)) * 0.16)))
        if distance <= allowance and distance < best_distance:
            best_key = candidate
            best_distance = distance
    return cover_title_bboxes.get(best_key) if best_key else None


def _small_edit_distance(left: str, right: str) -> int:
    """Bounded Levenshtein distance for short cover-title runs."""
    if left == right:
        return 0
    if abs(len(left) - len(right)) > 2:
        return 99
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        row_min = index
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            value = min(
                previous[right_index] + 1,
                current[right_index - 1] + 1,
                previous[right_index - 1] + cost,
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > 2:
            return 99
        previous = current
    return previous[-1]


def _looks_like_rotated_cover_title_line(value: str) -> bool:
    compact = _compact_text(value)
    if not compact:
        return False
    alnum = re.sub(r"[^A-Za-z0-9]+", "", value)
    digit_count = sum(1 for char in alnum if char.isdigit())
    return digit_count >= max(2, len(alnum) - 1)


def _entry_group_bbox(entries: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    rects: list[tuple[float, float, float, float]] = []
    for entry in entries:
        left = float(entry.get("left") or 0)
        top = float(entry.get("top") or 0)
        width = float(entry.get("width") or 0) or max(24.0, _font_size_px(entry) * 0.85)
        height = float(entry.get("height") or 0) or max(24.0, _font_size_px(entry) * 0.85)
        rects.append((left, top, left + width, top + height))
    if not rects:
        return 0.0, 0.0, 0.0, 0.0
    left = min(rect[0] for rect in rects)
    top = min(rect[1] for rect in rects)
    right = max(rect[2] for rect in rects)
    bottom = max(rect[3] for rect in rects)
    return round(left, 2), round(top, 2), round(right - left, 2), round(bottom - top, 2)


def _build_cover_offer_field(entries: list[dict[str, Any]], title_targets: set[str]) -> dict[str, Any]:
    page_entries = [
        entry
        for entry in entries
        if entry.get("page_num") == 1
        and entry.get("save_id") not in title_targets
        and str(entry.get("typography_role") or "body") not in {"caption", "table-status", "agent-contact"}
        and _looks_like_cover_offer(str(entry.get("plain") or ""))
    ]
    page_entries = sorted(page_entries, key=lambda item: (float(item.get("top") or 0), float(item.get("left") or 0)))
    return {
        "targets": [entry["save_id"] for entry in page_entries if entry.get("save_id")],
        "value": "\n".join(str(entry.get("plain") or "").strip() for entry in page_entries if str(entry.get("plain") or "").strip()),
    }


def _find_section_heading(entries: list[dict[str, Any]], tokens: tuple[str, ...]) -> dict[str, Any] | None:
    token_set = {_compact_text(token) for token in tokens}
    for entry in entries:
        text = str(entry.get("plain") or "")
        compact = _compact_text(text)
        words = _normalized_words(text)
        if compact in token_set:
            return entry
        if len(words) <= 3 and any(word in token_set for word in words) and _looks_like_heading_context_entry(entry):
            return entry
        if len(words) <= 3 and any(token and token in compact for token in token_set) and _looks_like_heading_context_entry(entry):
            return entry
    return None


def _font_size_px(entry: dict[str, Any]) -> float:
    style = entry.get("font_style") if isinstance(entry.get("font_style"), dict) else {}
    value = style.get("font_size_px") or style.get("font_size") or 0
    if isinstance(value, str):
        value = value.replace("px", "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _median_number(values: list[float]) -> float:
    clean = sorted(value for value in values if value > 0)
    if not clean:
        return 0.0
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2


def _looks_like_cover_title_piece(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 80:
        return False
    if "@" in text or re.search(r"\b0\d[\d\s]{8,}\b", text):
        return False
    words = re.findall(r"[A-Za-z0-9]+", text)
    if not words:
        return False
    upperish = sum(1 for word in words if word.isupper() or word.isdigit())
    return upperish >= max(1, len(words) - 1) or len(words) <= 3


def _looks_like_cover_offer(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 220:
        return False
    if "@" in text or re.search(r"\b0\d[\d\s]{8,}\b", text):
        return False
    return True


def _build_amenity_defs(entries: list[dict[str, Any]]) -> list[tuple[str, str, str, dict[str, Any] | None, list[dict[str, Any]]]]:
    return _infer_amenity_defs(entries)


def _infer_amenity_defs(entries: list[dict[str, Any]]) -> list[tuple[str, str, str, dict[str, Any] | None, list[dict[str, Any]]]]:
    sections = _amenity_icon_section_candidates(entries)
    defs: list[tuple[str, str, str, dict[str, Any] | None, list[dict[str, Any]]]] = []
    seen: set[str] = set()
    for section in sections:
        for group in section["groups"]:
            entry = group[0]
            value = "\n".join(str(item.get("plain") or "").strip() for item in group if str(item.get("plain") or "").strip())
            compact = _compact_text(value)
            if compact in seen:
                continue
            seen.add(compact)
            index = len(defs) + 1
            defs.append((f"amenity{index}", _amenity_control_label(value, index), value, entry, group[1:]))
            if len(defs) >= 36:
                return defs
    if not defs:
        for group in _amenity_feature_groups_without_heading(entries):
            entry = group[0]
            value = "\n".join(str(item.get("plain") or "").strip() for item in group if str(item.get("plain") or "").strip())
            compact = _compact_text(value)
            if compact in seen:
                continue
            seen.add(compact)
            index = len(defs) + 1
            defs.append((f"amenity{index}", _amenity_control_label(value, index), value, entry, group[1:]))
            if len(defs) >= 36:
                return defs
    return defs


def _amenity_control_label(value: str, index: int) -> str:
    first = next((line.strip() for line in str(value or "").splitlines() if line.strip()), "")
    if not first:
        return f"Item {index}"
    return first[:42]


def _amenity_feature_groups_without_heading(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Find icon/feature grids where the brochure omits an Amenities heading."""
    by_page: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        try:
            page_num = int(entry.get("page_num") or 0)
        except (TypeError, ValueError):
            continue
        by_page.setdefault(page_num, []).append(entry)

    best_groups: list[list[dict[str, Any]]] = []
    best_score = 0.0
    for page_num, page_entries in by_page.items():
        page_text = " ".join(str(entry.get("plain") or "") for entry in page_entries)
        if _looks_like_numbered_map_or_directory_page(page_text):
            continue
        candidates = [
            entry
            for entry in page_entries
            if _looks_like_headingless_feature_label(str(entry.get("plain") or ""))
            and not _looks_like_section_heading_candidate(
                str(entry.get("plain") or ""),
                str((entry.get("font_style") or {}).get("stable_font_family") or ""),
                entry.get("font_style") if isinstance(entry.get("font_style"), dict) else {},
            )
        ]
        groups = [
            group
            for group in _filter_amenity_icon_label_groups(_group_feature_label_entries(candidates))
            if not _looks_like_headingless_feature_group_noise(group)
        ]
        if len(groups) < 4:
            continue
        score = _amenity_feature_grid_score(groups)
        if score > best_score:
            best_score = score
            best_groups = groups
    return best_groups if best_score >= 4.0 else []


def _looks_like_headingless_feature_group_noise(group: list[dict[str, Any]]) -> bool:
    value = "\n".join(str(item.get("plain") or "").strip() for item in group if str(item.get("plain") or "").strip())
    compact = _compact_text(value)
    if compact.startswith("page") or compact in {"anchorhouse"}:
        return True
    return _looks_like_spaced_photo_caption(value)


def _looks_like_headingless_feature_label(value: str) -> bool:
    text = str(value or "").strip()
    compact = _compact_text(text)
    if compact.startswith("page") or compact in {"anchorhouse", "carpark", "onsitegym", "onsitecafe"}:
        # Spaced photo captions and footers are not amenity controls.
        words = _normalized_words(text)
        if len(words) >= 3 and sum(1 for word in words if len(word) <= 2) >= len(words) - 1:
            return False
    if _looks_like_spaced_photo_caption(text):
        return False
    if compact in {"cafe", "coffee"}:
        return True
    return _looks_like_amenity_label(text)


def _looks_like_spaced_photo_caption(value: str) -> bool:
    words = _normalized_words(value)
    compact = _compact_text(value)
    if compact.startswith("page"):
        return True
    if len(words) >= 4 and sum(1 for word in words if len(word) <= 2) >= len(words) - 1:
        return True
    return False


def _looks_like_numbered_map_or_directory_page(text: str) -> bool:
    compact = _compact_text(text)
    if "connectivity" in compact or "walktimes" in compact or "restaurants" in compact:
        numbers = len(re.findall(r"\b\d{1,2}\b", text or ""))
        return numbers >= 8
    return False


def _group_feature_label_entries(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    sorted_entries = sorted(
        entries,
        key=lambda entry: (entry.get("page_num") or 0, float(entry.get("top") or 0), float(entry.get("left") or 0)),
    )
    for entry in sorted_entries:
        page = entry.get("page_num")
        left = float(entry.get("left") or 0)
        top = float(entry.get("top") or 0)
        matched = False
        for group in groups:
            last = group[-1]
            if (
                last.get("page_num") == page
                and abs(float(last.get("left") or 0) - left) <= 72
                and 0 < top - float(last.get("top") or 0) <= 48
            ):
                group.append(entry)
                matched = True
                break
        if not matched:
            groups.append([entry])
    return sorted(groups, key=lambda group: (group[0].get("page_num") or 0, float(group[0].get("top") or 0), float(group[0].get("left") or 0)))


def _amenity_feature_grid_score(groups: list[list[dict[str, Any]]]) -> float:
    labels = [
        "\n".join(str(item.get("plain") or "").strip() for item in group if str(item.get("plain") or "").strip())
        for group in groups
    ]
    compact = " ".join(_compact_text(label) for label in labels)
    terms = (
        "bike",
        "bicycle",
        "fibre",
        "fiber",
        "gym",
        "shower",
        "lift",
        "parking",
        "car",
        "cafe",
        "coffee",
        "air",
        "vrf",
        "office",
        "fitted",
    )
    term_hits = sum(1 for term in terms if term in compact)
    lefts = sorted(float(group[0].get("left") or 0) for group in groups)
    columns = 0
    last_left: float | None = None
    for left in lefts:
        if last_left is None or abs(left - last_left) > 120:
            columns += 1
            last_left = left
    return len(groups) * 0.55 + term_hits * 0.7 + max(0, columns - 1) * 0.45


def _amenity_icon_section_candidates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    headings = _section_heading_candidates(entries, ("amenities", "features", "specification", "highlights"))
    sections: list[dict[str, Any]] = []
    for heading in headings:
        page_num = heading.get("page_num")
        heading_top = float(heading.get("top") or 0)
        heading_left = float(heading.get("left") or 0)
        candidates = [
            entry
            for entry in entries
            if entry.get("page_num") == page_num
            and float(entry.get("top") or 0) > heading_top + 16
            and float(entry.get("left") or 0) >= max(0.0, heading_left - 120)
            and _looks_like_amenity_label(entry.get("plain", ""))
        ]
        groups = _filter_amenity_icon_label_groups(_group_label_entries(candidates))
        if not groups:
            continue
        score = _amenity_icon_section_score(heading, groups)
        if score <= 0:
            continue
        sections.append({"heading": heading, "groups": groups, "score": score})
    sections.sort(
        key=lambda section: (
            -float(section["score"]),
            int(section["heading"].get("page_num") or 0),
            float(section["heading"].get("top") or 0),
        )
    )
    return sections


def _section_heading_candidates(entries: list[dict[str, Any]], tokens: tuple[str, ...]) -> list[dict[str, Any]]:
    token_set = {_compact_text(token) for token in tokens}
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        text = str(entry.get("plain") or "")
        compact = _compact_text(text)
        words = _normalized_words(text)
        if compact in token_set:
            candidates.append(entry)
            continue
        if len(words) <= 3 and any(word in token_set for word in words) and _looks_like_heading_context_entry(entry):
            candidates.append(entry)
            continue
        if len(words) <= 3 and any(token and token in compact for token in token_set) and _looks_like_heading_context_entry(entry):
            candidates.append(entry)
    return sorted(candidates, key=lambda entry: (entry.get("page_num") or 0, float(entry.get("top") or 0), float(entry.get("left") or 0)))


def _filter_amenity_icon_label_groups(groups: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    filtered: list[list[dict[str, Any]]] = []
    for group in groups:
        value = "\n".join(str(item.get("plain") or "").strip() for item in group if str(item.get("plain") or "").strip())
        if not _looks_like_amenity_icon_label_group(value):
            continue
        filtered.append(group)
    return filtered


def _looks_like_amenity_icon_label_group(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.match(r"^(?:0\d|\d{2})\s+[A-Z]", text):
        return False
    compact = _compact_text(text)
    if compact in {"destination", "services", "lines", "walktime", "airports", "viatransport"}:
        return False
    words = _normalized_words(text)
    if not words or len(words) > 10:
        return False
    return _looks_like_amenity_label(text)


def _amenity_icon_section_score(heading: dict[str, Any], groups: list[list[dict[str, Any]]]) -> float:
    compact_heading = _compact_text(str(heading.get("plain") or ""))
    if len(groups) < 2 and compact_heading != "amenities":
        return 0.0
    lefts = sorted(float(group[0].get("left") or 0) for group in groups)
    columns = 0
    last_left: float | None = None
    for left in lefts:
        if last_left is None or abs(left - last_left) > 120:
            columns += 1
            last_left = left
    labels = [
        "\n".join(str(item.get("plain") or "").strip() for item in group if str(item.get("plain") or "").strip())
        for group in groups
    ]
    avg_words = sum(len(_normalized_words(label)) for label in labels) / max(1, len(labels))
    page_bonus = 1.0 if columns >= 2 else 0.35
    heading_bonus = 0.7 if compact_heading in {"features", "keyfeatures", "summaryspecification", "specification", "highlights"} else 0.35
    return len(groups) * page_bonus + heading_bonus - max(0.0, avg_words - 6.0) * 0.2


def _group_label_entries(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    sorted_entries = sorted(
        entries,
        key=lambda entry: (entry.get("page_num") or 0, float(entry.get("left") or 0), float(entry.get("top") or 0)),
    )
    for entry in sorted_entries:
        page = entry.get("page_num")
        left = float(entry.get("left") or 0)
        top = float(entry.get("top") or 0)
        matched = False
        for group in groups:
            last = group[-1]
            if (
                last.get("page_num") == page
                and abs(float(last.get("left") or 0) - left) <= 34
                and 0 < top - float(last.get("top") or 0) <= 28
            ):
                group.append(entry)
                matched = True
                break
        if not matched:
            groups.append([entry])
    return sorted(groups, key=lambda group: (group[0].get("page_num") or 0, float(group[0].get("top") or 0), float(group[0].get("left") or 0)))


def _looks_like_amenity_label(value: str) -> bool:
    text = str(value or "").strip()
    compact = _compact_text(text)
    if not text or compact in {"amenities", "features", "specification", "highlights"}:
        return False
    if "@" in text or re.search(r"\b0\d[\d\s]{8,}\b", text):
        return False
    if len(text) > 96 or len(text) < 3:
        return False
    if re.search(r"\b(?:sq\s*ft|sqft|sq\s*m|sqm|duplex)\b", text, flags=re.IGNORECASE):
        return False
    words = _normalized_words(text)
    if len(words) > 8:
        return False
    if re.search(r"[.;:]\s*$", text) and len(words) > 3:
        return False
    if re.search(r"\b[A-Z]{1,2}\d[A-Z0-9]?\b", text.upper()) and len(re.findall(r"[A-Za-z0-9]+", text)) > 2:
        return False
    if compact.isdigit():
        return False
    if re.match(r"^(?:0\d|\d{2})\s+[A-Z]", text):
        return False
    map_words = ("station", "street", "market", "restaurant", "sushi", "wine", "bank", "fitness")
    if any(word in compact for word in map_words):
        return False
    return True


def _icon_for_amenity(value: str) -> str:
    compact = _compact_text(value)
    if "clean" in compact or "waste" in compact:
        return "broom"
    if "maint" in compact or "repair" in compact:
        return "gear"
    if "snack" in compact:
        return "apple"
    if "health" in compact or "safety" in compact:
        return "health"
    if "broadband" in compact or "fibre" in compact or "fiber" in compact or "wifi" in compact:
        return "wifi"
    if "electric" in compact or "demiseelectric" in compact:
        return "lightning"
    if "rates" in compact or "businessrates" in compact:
        return "rates"
    if "foliage" in compact or "leaf" in compact or "plant" in compact:
        return "leaf"
    if "plug" in compact or "catb" in compact:
        return "plug"
    if "gym" in compact:
        return "gym"
    if "lift" in compact:
        return "lift"
    if "shower" in compact:
        return "shower"
    if "bike" in compact or "cycle" in compact:
        return "bicycle"
    if "kitchen" in compact or "coffee" in compact or "tea" in compact:
        return "coffee"
    if "air" in compact or "condition" in compact or "service" in compact:
        return "lightning"
    if "trunk" in compact or "desk" in compact or "perimeter" in compact:
        return "desk"
    if "facade" in compact or "period" in compact or "victorian" in compact:
        return "warehouse"
    if "meeting" in compact:
        return "meeting"
    return "office"


def _compact_text(value: str) -> str:
    unescaped = html.unescape(value).replace("\xa0", " ").lower()
    normalized = unicodedata.normalize("NFKD", unescaped)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", ascii_text)


def _normalized_words(value: str) -> list[str]:
    unescaped = html.unescape(value).replace("\xa0", " ").lower()
    normalized = unicodedata.normalize("NFKD", unescaped)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", ascii_text)


def _slug_key(value: str, fallback: str) -> str:
    compact = _compact_text(value)
    if not compact:
        return fallback
    if compact[0].isdigit():
        compact = f"{fallback}{compact}"
    return compact[:36]


def _unique_key(key: str, used: set[str]) -> str:
    base = key or "item"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _build_contact_fields(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _infer_contact_fields(entries)


def _infer_contact_fields(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contact_entries = [entry for entry in entries if _looks_like_contact_text(entry.get("plain", ""))]
    fields: list[dict[str, Any]] = []
    used_targets: set[str] = set()
    used_keys: set[str] = set()
    for entry in contact_entries:
        if entry["save_id"] in used_targets:
            continue
        block = _contact_block_entries(entry, entries)
        target = block[0] if block else entry
        plain = _contact_plain_from_block(block)
        if _looks_like_footer_metadata_contact(plain):
            used_targets.update(item["save_id"] for item in block if item.get("save_id"))
            continue
        email_match = re.search(r"[\w.+-]+\s*@\s*[\w.-]+\.[A-Za-z]{2,}", plain)
        phone_match = CONTACT_PHONE_RE.search(plain)
        email = re.sub(r"\s+", "", email_match.group(0)) if email_match else ""
        name = _infer_contact_name_from_block(block, plain) or _contact_name_from_email(email)
        phone = _normalise_contact_phone(phone_match.group(0)) if phone_match else ""
        if _looks_like_footer_phone_only_contact(name, email, phone):
            used_targets.update(item["save_id"] for item in block if item.get("save_id"))
            continue
        if not (phone or email):
            continue
        index = len(fields) + 1
        key = _unique_key(_slug_key(name or email or f"contact-{index}", "contact"), used_keys)
        label = name if name else f"Contact {index}"
        bbox = _entries_bbox(block)
        fields.append(
            {
                "key": key,
                "label": label,
                "name": name or label,
                "phone": phone,
                "email": email,
                "value": "\n".join(part for part in (name or label, phone, email) if part),
                "targets": [target["save_id"]],
                "hide_targets": [item["save_id"] for item in block[1:] if item.get("save_id")],
                "anchor": target,
                "bbox": bbox,
                "line_prefixes": sorted(
                    {
                        _contact_line_prefix(str(item.get("plain") or ""))
                        for item in block
                        if _contact_line_prefix(str(item.get("plain") or ""))
                    }
                ),
            }
        )
        used_targets.update(item["save_id"] for item in block if item.get("save_id"))
        if len(fields) >= 8:
            break
    return _enrich_contact_duplicates(fields)


def _contact_block_entries(entry: dict[str, Any], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    page = entry.get("page_num")
    x = float(entry.get("left") or 0)
    y = float(entry.get("top") or 0)
    same_page = [candidate for candidate in entries if candidate.get("page_num") == page]
    block = [
        candidate
        for candidate in same_page
        if abs(float(candidate.get("left") or 0) - x) <= 135
        and -45 <= float(candidate.get("top") or 0) - y <= 35
        and _looks_like_contact_block_text(str(candidate.get("plain") or ""))
    ]
    block.sort(key=_line_then_left_sort_key)
    return block or [entry]


def _line_then_left_sort_key(entry: dict[str, Any]) -> tuple[int, float]:
    return (int(round(float(entry.get("top") or 0) / 4.0)), float(entry.get("left") or 0))


def _contact_plain_from_block(block: list[dict[str, Any]]) -> str:
    lines: list[list[dict[str, Any]]] = []
    for item in sorted(block, key=lambda entry: (float(entry.get("top") or 0), float(entry.get("left") or 0))):
        top = float(item.get("top") or 0)
        for line in lines:
            line_top = float(line[0].get("top") or 0)
            if abs(top - line_top) <= 4:
                line.append(item)
                break
        else:
            lines.append([item])
    pieces: list[str] = []
    for line in lines:
        line.sort(key=lambda entry: float(entry.get("left") or 0))
        text = " ".join(str(item.get("plain") or "").strip() for item in line if str(item.get("plain") or "").strip())
        if text:
            pieces.append(text)
    return " ".join(pieces)


def _looks_like_contact_block_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if _looks_like_footer_metadata_contact(text):
        return False
    if "@" in text or CONTACT_PHONE_RE.search(text):
        return True
    if _looks_like_contact_name_title_line(text):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){0,3}", text):
        return True
    if re.fullmatch(r"[\w.+-]+", text):
        return True
    return False


def _looks_like_footer_metadata_contact(text: str) -> bool:
    compact = _compact_text(text)
    return any(
        token in compact
        for token in (
            "designandproduction",
            "designedandproduced",
            "producedbycre8te",
            "stuartchapmandesign",
            "commercialleasecode",
            "misrepresentationact",
        )
    )


def _infer_contact_name_from_block(block: list[dict[str, Any]], plain: str) -> str:
    line_name = _contact_name_from_line_fragments(block)
    if line_name:
        return line_name
    for item in block:
        text = str(item.get("plain") or "").strip()
        title_line_name = _contact_name_from_title_line(text)
        if title_line_name:
            return title_line_name
    prefix = re.split(r"\b0\d[\d\s]{5,}\b|[\w.+-]+\s*@\s*[\w.-]+\.[A-Za-z]{2,}", plain)[0].strip(" -|,")
    match = re.search(r"\b[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}\b", prefix)
    if match:
        return match.group(0).strip()
    for item in block:
        text = str(item.get("plain") or "").strip()
        if re.fullmatch(r"[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3}", text) and "@" not in text:
            return text
    match = re.search(r"\b[A-Z][A-Za-z.'-]+\b", prefix)
    return match.group(0).strip() if match else ""


def _contact_name_from_line_fragments(block: list[dict[str, Any]]) -> str:
    lines: list[list[dict[str, Any]]] = []
    for item in sorted(block, key=_line_then_left_sort_key):
        text = str(item.get("plain") or "").strip()
        if not text or "@" in text or CONTACT_PHONE_RE.search(text):
            continue
        if not re.fullmatch(r"[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,2}", text):
            continue
        top = float(item.get("top") or 0)
        for line in lines:
            if abs(top - float(line[0].get("top") or 0)) <= 4:
                line.append(item)
                break
        else:
            lines.append([item])
    for line in lines:
        line.sort(key=lambda entry: float(entry.get("left") or 0))
        name = " ".join(str(item.get("plain") or "").strip() for item in line)
        if re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}", name):
            return name
    return ""


def _looks_like_contact_name_title_line(text: str) -> bool:
    return bool(_contact_name_from_title_line(text))


def _contact_name_from_title_line(text: str) -> str:
    value = str(text or "").replace("\xa0", " ").strip()
    if not value or "@" in value or CONTACT_PHONE_RE.search(value):
        return ""
    if not re.search(
        r"\b(?:surveyor|director|partner|agent|consultant|leasing|advisor|associate|graduate|senior|head)\b",
        value,
        flags=re.IGNORECASE,
    ):
        return ""
    prefix = re.split(r"\s*[|,-]\s*", value, maxsplit=1)[0].strip()
    if re.fullmatch(r"[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){1,3}", prefix):
        return prefix
    return ""


def _contact_name_from_email(email: str) -> str:
    local = str(email or "").split("@", 1)[0].strip()
    if not local or local.lower() in {"info", "hello", "enquiries", "office", "contact", "team"}:
        return ""
    parts = [part for part in re.split(r"[._+-]+", local) if part and not part.isdigit()]
    if not parts or len(parts) > 3:
        return ""
    return " ".join(part[:1].upper() + part[1:].lower() for part in parts)


def _normalise_contact_phone(value: str) -> str:
    value = re.sub(r"^\s*(?:M|T|P)\s*:\s*", "", str(value or ""), flags=re.IGNORECASE)
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("07"):
        return f"{digits[:5]} {digits[5:8]} {digits[8:]}"
    if len(digits) == 11 and digits.startswith("020"):
        return f"020 {digits[3:7]} {digits[7:]}"
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _contact_line_prefix(value: str) -> str:
    text = str(value or "").strip().upper()
    if text in {"M", "E", "T", "P"}:
        return text
    match = re.match(r"^([METP])\s*:", text)
    return match.group(1) if match else ""


def _looks_like_footer_phone_only_contact(name: str, email: str, phone: str) -> bool:
    if email:
        return False
    compact_name = _compact_text(name)
    digits = re.sub(r"\D+", "", phone)
    return bool(digits.startswith("020") and compact_name in {"design", "production", "contact"})


def _enrich_contact_duplicates(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_name_by_email: dict[str, str] = {}
    for field in fields:
        email = str(field.get("email") or "").strip().lower()
        name = str(field.get("name") or "").replace("\xa0", " ").strip()
        if not email or not name:
            continue
        current = best_name_by_email.get(email, "")
        if len(_normalized_words(name)) > len(_normalized_words(current)):
            best_name_by_email[email] = name
    for field in fields:
        email = str(field.get("email") or "").strip().lower()
        best = best_name_by_email.get(email, "")
        if not best:
            continue
        name = str(field.get("name") or "").replace("\xa0", " ").strip()
        if len(_normalized_words(best)) > len(_normalized_words(name)):
            field["name"] = best
            field["label"] = best
            field["value"] = "\n".join(part for part in (best, field.get("phone"), field.get("email")) if part)
    return fields


def _infer_contact_name(entry: dict[str, Any], entries: list[dict[str, Any]], email_start: int | None) -> str:
    plain = str(entry.get("plain") or "").strip()
    prefix = plain[:email_start].strip(" -|,") if email_start else plain
    prefix = CONTACT_PHONE_RE.sub("", prefix).strip(" -|,")
    name_match = re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b", prefix)
    if name_match:
        return name_match.group(0).strip()

    same_page = [candidate for candidate in entries if candidate.get("page_num") == entry.get("page_num")]
    x = float(entry.get("left") or 0)
    y = float(entry.get("top") or 0)
    preceding = [
        candidate
        for candidate in same_page
        if 0 <= y - float(candidate.get("top") or 0) <= 70
        and abs(float(candidate.get("left") or 0) - x) <= 80
        and re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b", str(candidate.get("plain") or ""))
    ]
    preceding.sort(key=lambda candidate: float(candidate.get("top") or 0), reverse=True)
    if preceding:
        return str(preceding[0].get("plain") or "").strip()
    return ""


def _looks_like_contact_text(text: str) -> bool:
    value = str(text or "")
    if _looks_like_footer_metadata_contact(value):
        return False
    return "@" in value or bool(CONTACT_PHONE_RE.search(value))


def _build_amenity_icon_defs(amenities: list[dict[str, Any]], pages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    first_page = pages[0] if pages else {"page_num": 1, "width": 640, "height": 480}
    page_width = float(first_page.get("width") or 640)
    fallback_page = str(first_page.get("page_num") or 1)
    fallback_columns = [0.06, 0.40, 0.74]
    fallback_rows = [0.24, 0.40, 0.56, 0.72]
    slots: dict[str, dict[str, Any]] = {}

    for index, amenity in enumerate(amenities):
        anchor = amenity.get("anchor") or {}
        page_num = anchor.get("page_num") or 4 if len(pages) >= 4 else fallback_page
        left = anchor.get("left")
        top = anchor.get("top")
        if left is None:
            left = page_width * fallback_columns[index % 3]
        if top is None:
            page_height = float(first_page.get("height") or 480)
            top = page_height * fallback_rows[min(index // 3, len(fallback_rows) - 1)]
        icon_width = 120
        icon_height = 94
        slots[amenity["key"]] = {
            "id": amenity["key"],
            "key": amenity["key"],
            "label": amenity["label"],
            "page": str(page_num),
            "left": max(8, round(float(left), 2)),
            "top": max(8, round(float(top) - 132, 2)),
            "width": icon_width,
            "height": icon_height,
            "icon_id": amenity.get("icon_id") or "office",
            "defaultIcon": amenity.get("icon_id") or "office",
            "textTargets": amenity.get("targets", []),
        }
    return slots


def _build_space_plan_defs(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for page in pages:
        if _looks_like_plan_number_schedule_without_space_plan(page):
            continue
        label_regions = _space_plan_regions_from_labels(page)
        if label_regions:
            background_path = Path(str(page.get("background_path") or ""))
            if not background_path.is_file():
                continue
            page_num = int(page.get("page_num") or 0)
            for index, region in enumerate(label_regions, start=1):
                bbox = _bbox_from_semantic_region(region)
                key = f"space-plan-p{page_num}-{index}"
                asset_url = _crop_source_region_asset(background_path, bbox, f"page{page_num:03d}-space-plan-{index:02d}.png")
                slots.append(
                    {
                        "page": str(page.get("page_num")),
                        "key": key,
                        "label": str(region.get("label") or _space_plan_label(page)),
                        "left": bbox["left"],
                        "top": bbox["top"],
                        "width": bbox["width"],
                        "height": bbox["height"],
                        "mask_colour": _sample_mask_colour(background_path, bbox),
                        "asset_url": asset_url,
                        "fit": "contain",
                        "confidence": region.get("confidence"),
                        "source_evidence": region.get("source_evidence"),
                    }
                )
            continue
        region = _space_plan_region_from_image_regions(page)
        if _looks_like_availability_schedule_without_space_plan(page):
            continue
        if not _has_space_plan_context(page) and region is None:
            continue
        background_path = Path(str(page.get("background_path") or ""))
        if not background_path.is_file():
            continue
        if region is None:
            detected_bbox = detect_space_plan_bbox(
                background_path=background_path,
                width=float(page.get("width") or 0),
                height=float(page.get("height") or 0),
                text_entries=page.get("text_entries") or [],
                image_slots=page.get("image_slots") or [],
            )
            if detected_bbox:
                region = {
                    "bbox": detected_bbox,
                    "confidence": detected_bbox.get("confidence"),
                    "source_evidence": {"method": "rendered-page linework/grid detection"},
                }
        if region is not None:
            bbox = _bbox_from_semantic_region(region)
            key = f'space-plan-p{page.get("page_num")}-1'
            asset_url = _crop_source_region_asset(background_path, bbox, f"page{int(page.get('page_num') or 0):03d}-space-plan-01.png")
            slots.append(
                {
                    "page": str(page.get("page_num")),
                    "key": key,
                    "label": _space_plan_label(page),
                    "left": bbox["left"],
                    "top": bbox["top"],
                    "width": bbox["width"],
                    "height": bbox["height"],
                    "mask_colour": _sample_mask_colour(background_path, bbox),
                    "asset_url": asset_url,
                    "fit": "contain",
                    "confidence": region.get("confidence"),
                    "source_evidence": region.get("source_evidence"),
                }
            )
            continue
        candidates = [
            component
            for component in _detect_accent_components(background_path, _is_yellow_accent_pixel)
            if _looks_like_space_plan_component(component, page)
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda item: item["accent_count"], reverse=True)
        component = candidates[0]
        bbox = _expand_space_plan_bbox(component, page)
        key = f'space-plan-p{page.get("page_num")}-1'
        asset_url = _crop_source_region_asset(background_path, bbox, f"page{int(page.get('page_num') or 0):03d}-space-plan-01.png")
        slots.append(
            {
                "page": str(page.get("page_num")),
                "key": key,
                "label": _space_plan_label(page),
                "left": bbox["left"],
                "top": bbox["top"],
                "width": bbox["width"],
                "height": bbox["height"],
                "mask_colour": _sample_mask_colour(background_path, bbox),
                "asset_url": asset_url,
            }
        )
    return slots


def _space_plan_regions_from_labels(page: dict[str, Any]) -> list[dict[str, Any]]:
    source_entries = list(page.get("text_entries") or []) + list(page.get("model_text_spans") or [])
    entries = [
        entry
        for entry in source_entries
        if _looks_like_floor_plan_label(str(entry.get("plain") or ""))
    ]
    if sum(1 for entry in entries if _looks_like_plan_reference_schedule_label(str(entry.get("plain") or ""))) >= 2:
        return []
    if len(entries) < 2:
        return []
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    entries.sort(key=lambda entry: (float(entry.get("top") or 0), float(entry.get("left") or 0)))
    regions: list[dict[str, Any]] = []
    for entry in entries[:8]:
        label = str(entry.get("plain") or "Space plan").strip()
        left = float(entry.get("left") or 0)
        top = float(entry.get("top") or 0)
        label_width = max(float(entry.get("width") or 0), len(label) * 9.5)
        region_width = min(page_width * 0.42, max(page_width * 0.24, label_width * 2.45))
        if left > page_width * 0.58:
            region_width = min(region_width, page_width - left + page_width * 0.05)
        center_x = left + label_width / 2.0
        region_left = max(0.0, min(page_width - region_width, center_x - region_width / 2.0))
        region_height = min(page_height * 0.40, max(page_height * 0.22, top - page_height * 0.12))
        region_top = max(0.0, top - region_height - 18.0)
        region = {
            "bbox": {
                "left": round(region_left, 2),
                "top": round(region_top, 2),
                "width": round(region_width, 2),
                "height": round(region_height, 2),
            },
            "label": label.title() if label.isupper() else label,
            "confidence": 0.78,
            "source_evidence": {"method": "floor plan label inferred source crop", "label": label},
        }
        if any(_field_region_iou(_bbox_from_semantic_region(existing), _bbox_from_semantic_region(region)) >= 0.62 for existing in regions):
            continue
        regions.append(region)
    return regions


def _looks_like_floor_plan_label(value: str) -> bool:
    text = str(value or "").strip()
    compact = _compact_text(text)
    if not compact:
        return False
    if _looks_like_plan_reference_schedule_label(text):
        return False
    if "proposed" in compact:
        return _looks_like_proposed_drawing_label(text)
    if len(text) > 42 or re.search(r"[.;:]", text):
        return False
    return bool(re.search(r"\b(?:ground|lower|upper|[1-9](?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth)\s+floor\b", text, flags=re.IGNORECASE))


def _looks_like_proposed_drawing_label(text: str) -> bool:
    if ";" in text:
        return False
    compact = _compact_text(text)
    if "floorspace" in compact:
        return False
    if len(text) > 96:
        return False
    return bool(
        re.search(
            r"^\s*proposed\s+(?:(?:lower|upper|ground|first|second|third|fourth|fifth|sixth|seventh|eighth|roof|front|rear|side|site|north|south|east|west)\s+)*(?:floor|elevation|elevations|section|sections)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _looks_like_plan_reference_schedule_label(value: str) -> bool:
    text = str(value or "").strip()
    if len(text) <= 42:
        return False
    compact = _compact_text(text)
    has_plan_word = any(token in compact for token in ("floorplan", "elevation", "elevations", "section", "sections"))
    if not has_plan_word:
        return False
    reference_like = (
        ";" in text
        or re.search(r"\b[A-Z0-9]{2,}(?:[-_][A-Z0-9]{1,}){3,}\b", text, flags=re.IGNORECASE) is not None
    )
    return reference_like and any(token in compact for token in ("existing", "proposed", "plannos", "reference"))


def _looks_like_plan_number_schedule_without_space_plan(page: dict[str, Any]) -> bool:
    entries = list(page.get("text_entries") or []) + list(page.get("model_text_spans") or [])
    texts = [str(entry.get("plain") or entry.get("text") or "") for entry in entries if isinstance(entry, dict)]
    compact = _compact_text(" ".join(texts))
    if not compact:
        return False
    schedule_context = any(token in compact for token in ("plannos", "draftdecisionletter", "decisionletter", "reference"))
    plan_terms = sum(1 for token in ("floorplan", "elevation", "elevations", "sections", "existing", "proposed") if token in compact)
    reference_labels = sum(1 for text in texts if _looks_like_plan_reference_schedule_label(text))
    return schedule_context and plan_terms >= 3 and reference_labels >= 2


def _looks_like_availability_schedule_without_space_plan(page: dict[str, Any]) -> bool:
    entries = [entry for entry in page.get("text_entries") or [] if isinstance(entry, dict)]
    compact = _compact_text(" ".join(str(entry.get("plain") or "") for entry in entries))
    if not all(token in compact for token in ("floor", "sqft", "status")):
        return False
    if not any(token in compact for token in ("catb", "let", "quotingrent", "servicecharge", "psf")):
        return False
    return not any(_looks_like_floor_plan_label(str(entry.get("plain") or "")) for entry in entries)


def _space_plan_region_from_image_regions(page: dict[str, Any]) -> dict[str, Any] | None:
    regions = [
        region
        for region in page.get("image_regions") or []
        if isinstance(region, dict) and region.get("role") == "space-plan" and isinstance(region.get("bbox"), dict)
    ]
    if not regions:
        return None
    return max(regions, key=lambda item: float(item.get("confidence") or 0))


def _bbox_from_semantic_region(region: dict[str, Any]) -> dict[str, float]:
    bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
    return {
        "left": round(float(bbox.get("left") or bbox.get("x") or 0), 2),
        "top": round(float(bbox.get("top") or bbox.get("y") or 0), 2),
        "width": round(float(bbox.get("width") or 0), 2),
        "height": round(float(bbox.get("height") or 0), 2),
    }


def _has_space_plan_context(page: dict[str, Any]) -> bool:
    if _looks_like_plan_number_schedule_without_space_plan(page):
        return False
    compact = _compact_text(" ".join(str(entry.get("plain") or "") for entry in page.get("text_entries", [])))
    if "floor" not in compact and "spaceplan" not in compact:
        return False
    if any(_looks_like_floor_plan_label(str(entry.get("plain") or "")) for entry in page.get("text_entries", [])):
        return True
    return any(token in compact for token in ("desks", "breakout", "meetingroom", "phonebooths", "floorplans", "spaceplan"))


def _looks_like_space_plan_component(component: dict[str, float], page: dict[str, Any]) -> bool:
    width = float(component.get("width") or 0)
    height = float(component.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    density = float(component.get("density") or 0)
    if _overlaps_image_slots(component, page.get("image_slots", []), threshold=0.25):
        return False
    return (
        height > page_height * 0.35
        and page_width * 0.08 <= width <= page_width * 0.45
        and 0.14 <= density <= 0.78
        and float(component.get("accent_count") or 0) > 3500
    )


def _expand_space_plan_bbox(component: dict[str, float], page: dict[str, Any]) -> dict[str, float]:
    page_width = float(page.get("width") or component["left"] + component["width"])
    page_height = float(page.get("height") or component["top"] + component["height"])
    pad_left = max(18.0, float(component["width"]) * 0.08)
    pad_right = max(76.0, float(component["width"]) * 0.36)
    pad_top = max(52.0, float(component["height"]) * 0.11)
    pad_bottom = max(60.0, float(component["height"]) * 0.08)
    left = max(0.0, float(component["left"]) - pad_left)
    top = max(0.0, float(component["top"]) - pad_top)
    right = min(page_width, float(component["left"]) + float(component["width"]) + pad_right)
    bottom = min(page_height, float(component["top"]) + float(component["height"]) + pad_bottom)
    return {
        "left": round(left, 2),
        "top": round(top, 2),
        "width": round(max(1.0, right - left), 2),
        "height": round(max(1.0, bottom - top), 2),
    }


def _space_plan_label(page: dict[str, Any]) -> str:
    for entry in page.get("text_entries", []):
        compact = _compact_text(str(entry.get("plain") or ""))
        if "floor" in compact and len(compact) <= 24:
            return str(entry.get("plain") or "Space plan").replace("\n", " ").strip() or "Space plan"
    return f'Page {page.get("page_num")} space plan'


def _crop_source_region_asset(image_path: Path, bbox: dict[str, float], filename: str) -> str:
    try:
        assets_dir = image_path.parent
        if assets_dir.name != "exact_assets":
            return ""
        project_id = assets_dir.parent.name
        images_dir = assets_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        output_path = images_dir / filename
        image = _load_rgb_image(image_path)
        if image is None:
            return ""
        left = max(0, int(float(bbox["left"])))
        top = max(0, int(float(bbox["top"])))
        right = min(image.width, int(float(bbox["left"]) + float(bbox["width"])))
        bottom = min(image.height, int(float(bbox["top"]) + float(bbox["height"])))
        if right <= left or bottom <= top:
            return ""
        image.crop((left, top, right, bottom)).save(output_path)
        return f"/api/projects/{project_id}/exact_assets/images/{filename}"
    except Exception:
        return ""


def _build_table_image_defs(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for page in pages:
        region = _table_image_region_from_text(page)
        if region is None:
            continue
        background_path = Path(str(page.get("background_path") or ""))
        if not background_path.is_file():
            continue
        page_num = int(page.get("page_num") or 0)
        bbox = _bbox_from_semantic_region(region)
        key = f"table-image-p{page_num}-1"
        asset_url = _crop_source_region_asset(background_path, bbox, f"page{page_num:03d}-table-image-01.png")
        slots.append(
            {
                "page": str(page_num),
                "key": key,
                "label": str(region.get("label") or "Schedule"),
                "left": bbox["left"],
                "top": bbox["top"],
                "width": bbox["width"],
                "height": bbox["height"],
                "mask_colour": _sample_mask_colour(background_path, bbox),
                "asset_url": asset_url,
                "fit": "contain",
                "confidence": region.get("confidence"),
                "source_evidence": region.get("source_evidence"),
            }
        )
    return slots


def _table_image_region_from_text(page: dict[str, Any]) -> dict[str, Any] | None:
    entries = [entry for entry in page.get("text_entries") or [] if str(entry.get("plain") or "").strip()]
    if not entries:
        return None
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    compact_page = _compact_text(" ".join(str(entry.get("plain") or "") for entry in entries))
    if "accommodationschedule" in compact_page or "tenancyschedule" in compact_page:
        heading = _table_heading_entry(entries, ("accommodation", "tenancy", "schedule"))
        if heading is None:
            return None
        heading_top = float(heading.get("top") or 0)
        left_limit = page_width * 0.48
        region_entries = [
            entry
            for entry in entries
            if float(entry.get("left") or 0) <= left_limit
            and heading_top - 12 <= float(entry.get("top") or 0) <= page_height * 0.88
        ]
        return _table_region_from_entries(region_entries, page, "Accommodation schedule")

    if "permitteddevelopment" in compact_page:
        heading = _table_heading_entry(entries, ("permitted", "development"))
        if heading is None:
            return None
        heading_top = float(heading.get("top") or 0)
        heading_left = float(heading.get("left") or page_width * 0.62)
        region_entries = [
            entry
            for entry in entries
            if float(entry.get("left") or 0) >= max(page_width * 0.52, heading_left - 35)
            and heading_top - 20 <= float(entry.get("top") or 0) <= page_height * 0.92
        ]
        return _table_region_from_entries(region_entries, page, "Tenancy schedule")

    if "planningportal" in compact_page and "floor" in compact_page:
        anchor_entries = [
            entry
            for entry in entries
            if any(token in _compact_text(str(entry.get("plain") or "")) for token in ("floor", "planningportal", "units"))
        ]
        if len(anchor_entries) >= 2:
            left = min(float(entry.get("left") or 0) for entry in anchor_entries)
            top = min(float(entry.get("top") or 0) for entry in anchor_entries)
            bbox = {
                "left": round(max(page_width * 0.56, left - page_width * 0.19), 2),
                "top": round(max(page_height * 0.28, top - page_height * 0.26), 2),
                "width": round(page_width - max(page_width * 0.56, left - page_width * 0.19) - 44.0, 2),
                "height": round(page_height * 0.93 - max(page_height * 0.28, top - page_height * 0.26), 2),
            }
            return {
                "bbox": bbox,
                "label": "Tenancy schedule",
                "confidence": 0.72,
                "source_evidence": {"method": "planning portal/floor table inferred source crop"},
            }
    return None


def _table_heading_entry(entries: list[dict[str, Any]], tokens: tuple[str, ...]) -> dict[str, Any] | None:
    required = {_compact_text(token) for token in tokens if token}
    best: tuple[float, dict[str, Any]] | None = None
    for entry in entries:
        text = str(entry.get("plain") or "").strip()
        compact = _compact_text(str(entry.get("plain") or ""))
        if not compact:
            continue
        matched = {token for token in required if token in compact}
        if not matched:
            continue
        words = re.findall(r"[A-Za-z]+", text)
        upperish = words and sum(1 for word in words if word.isupper()) >= max(1, len(words) - 1)
        font_size = _font_size_px(entry)
        if len(matched) == 1 and len(required) > 1 and not (upperish and font_size >= 20):
            continue
        if not upperish and font_size < 18:
            continue
        score = font_size + len(matched) * 8 + (8 if upperish else 0)
        if best is None or score > best[0]:
            best = (score, entry)
    return best[1] if best else None


def _table_region_from_entries(entries: list[dict[str, Any]], page: dict[str, Any], label: str) -> dict[str, Any] | None:
    if len(entries) < 3:
        return None
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    left = min(float(entry.get("left") or 0) for entry in entries)
    top = min(float(entry.get("top") or 0) for entry in entries)
    right = max(_text_entry_right(entry) for entry in entries)
    bottom = max(float(entry.get("top") or 0) + max(16.0, _font_size_px(entry) * 1.35) for entry in entries)
    pad_x = 24.0
    pad_y = 22.0
    bbox = {
        "left": round(max(0.0, left - pad_x), 2),
        "top": round(max(0.0, top - pad_y), 2),
        "width": round(min(page_width, right + pad_x) - max(0.0, left - pad_x), 2),
        "height": round(min(page_height, bottom + pad_y) - max(0.0, top - pad_y), 2),
    }
    if bbox["width"] < page_width * 0.12 or bbox["height"] < page_height * 0.12:
        return None
    return {
        "bbox": bbox,
        "label": label,
        "confidence": 0.80,
        "source_evidence": {"method": "schedule/table heading inferred source crop", "label": label},
    }


def _text_entry_right(entry: dict[str, Any]) -> float:
    left = float(entry.get("left") or 0)
    width = float(entry.get("width") or 0)
    if width > 0:
        return left + width
    text = str(entry.get("plain") or "")
    return left + max(20.0, min(520.0, len(text) * max(7.0, _font_size_px(entry) * 0.58)))


def _build_service_icon_defs(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    amenity_pages = {
        entry.get("page_num")
        for entry in entries
        if _compact_text(str(entry.get("plain") or "")) in {"amenities", "features", "specification", "highlights"}
    }
    heading_pages = {
        entry.get("page_num")
        for entry in entries
        if _looks_like_service_section_heading_entry(entry)
    }
    label_counts: dict[Any, int] = {}
    for entry in entries:
        page_num = entry.get("page_num")
        if page_num in amenity_pages:
            continue
        if _looks_like_service_icon_label(str(entry.get("plain") or "")):
            label_counts[page_num] = label_counts.get(page_num, 0) + 1
    service_pages = heading_pages | {page_num for page_num, count in label_counts.items() if count >= 3}
    candidates = [
        entry
        for entry in entries
        if entry.get("page_num") not in amenity_pages
        and entry.get("page_num") in service_pages
        and _looks_like_service_icon_label(str(entry.get("plain") or ""))
    ]
    candidates.sort(key=lambda entry: (entry.get("page_num") or 0, float(entry.get("top") or 0), float(entry.get("left") or 0)))
    used_targets: set[str] = set()
    used_keys: set[str] = set()
    for group_entries in _group_label_entries(candidates):
        entry = group_entries[0]
        if not entry or entry["save_id"] in used_targets:
            continue
        label = " ".join(str(item.get("plain") or "").strip() for item in group_entries if str(item.get("plain") or "").strip())
        left = float(entry.get("left") or 0)
        top = float(entry.get("top") or 0)
        icon_id = _icon_for_amenity(label)
        key = _unique_key(_slug_key(label, "service"), used_keys)
        fields.append(
            {
                "key": key,
                "label": label,
                "group": _service_icon_group(entry, entries),
                "page": str(entry.get("page_num") or ""),
                "left": round(max(0.0, left - 6.0), 2),
                "top": round(max(0.0, top - 112.0), 2),
                "width": 86,
                "height": 86,
                "icon_id": icon_id,
                "defaultIcon": icon_id,
                "textTargets": [item["save_id"] for item in group_entries if item.get("save_id")],
            }
        )
        used_targets.update(item["save_id"] for item in group_entries if item.get("save_id"))
        if len(fields) >= 18:
            break
    return fields


def _looks_like_service_icon_label(value: str) -> bool:
    text = str(value or "").strip()
    if not _looks_like_amenity_label(text):
        return False
    words = _normalized_words(text)
    exact_tokens = {
        "waste",
        "repair",
        "repairs",
        "health",
        "safety",
        "broadband",
        "rates",
        "coffee",
        "tea",
        "snack",
        "snacks",
        "foliage",
        "rental",
        "care",
        "security",
        "reception",
        "meeting",
        "meetings",
        "wifi",
        "wi-fi",
    }
    stem_tokens = ("clean", "maint", "electric")
    return any(word in exact_tokens for word in words) or any(
        word.startswith(stem) for stem in stem_tokens for word in words
    )


def _service_icon_group(entry: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    page = entry.get("page_num")
    top = float(entry.get("top") or 0)
    headings = [
        candidate
        for candidate in entries
        if candidate.get("page_num") == page
        and float(candidate.get("top") or 0) < top
        and _looks_like_service_section_heading_entry(candidate)
    ]
    headings.sort(key=lambda candidate: float(candidate.get("top") or 0), reverse=True)
    return str(headings[0].get("plain") or "Services") if headings else "Services"


def _looks_like_section_heading(value: str) -> bool:
    compact = _compact_text(value)
    return any(token in compact for token in ("services", "customisation", "customization", "solution", "features", "amenities"))


def _looks_like_service_section_heading_entry(entry: dict[str, Any]) -> bool:
    text = str(entry.get("plain") or "").strip()
    compact = _compact_text(text)
    if not compact:
        return False
    if not any(token in compact for token in ("services", "customisation", "customization", "managedsolution")):
        return False
    words = _normalized_words(text)
    if _looks_like_spaced_caps(text):
        return True
    if len(words) > 4:
        return False
    return _font_size_px(entry) >= 22 or _looks_like_spaced_caps(text)


def _looks_like_spaced_caps(text: str) -> bool:
    value = str(text or "").replace("\xa0", " ")
    letters = re.findall(r"[A-Za-z]", value)
    if len(letters) < 4:
        return False
    uppercase = sum(1 for letter in letters if letter.isupper())
    spaces = len(re.findall(r"\s", value))
    return uppercase >= max(4, int(len(letters) * 0.8)) and spaces >= max(3, len(letters) // 2)


def _build_map_region(pages: list[dict[str, Any]]) -> dict[str, Any]:
    regions = _build_map_regions(pages)
    return regions[0] if regions else {}


def _build_map_regions(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not pages:
        return []

    regions: list[dict[str, Any]] = []
    primary = _build_primary_text_map_region(pages)
    if primary:
        regions.append(primary)

    primary_page = str(primary.get("page") or "") if primary else ""
    for region in _map_regions_from_image_regions(pages):
        if primary_page and str(region.get("page") or "") == primary_page:
            continue
        if not any(
            str(existing.get("page") or "") == str(region.get("page") or "")
            and _field_region_iou(existing, region) >= 0.50
            for existing in regions
        ):
            regions.append(region)

    regions.sort(key=lambda region: int(region.get("page") or 0))
    return regions


def _build_primary_text_map_region(pages: list[dict[str, Any]]) -> dict[str, Any]:
    scored_pages: list[tuple[int, int, dict[str, Any]]] = []
    for page in pages:
        page_text = " ".join(entry.get("plain", "") for entry in page.get("text_entries", []))
        map_score = _map_page_score(page_text, page.get("text_entries", []))
        scored_pages.append((map_score, len(page.get("text_entries", [])), page))
    scored_pages.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for best_score, _label_count, map_page in scored_pages:
        raw_label_entries = _map_label_entries(map_page.get("text_entries", []))
        label_entries = _map_label_entries_for_region(raw_label_entries, map_page)
        if not _has_map_label_distribution(label_entries, map_page):
            continue
        if _map_label_region_quality(label_entries) < 3:
            continue
        if best_score < 5 and len(label_entries) < 3:
            continue
        return _map_region_from_text_page(map_page)
    return {}


def _map_region_from_text_page(map_page: dict[str, Any]) -> dict[str, Any]:
    raw_label_entries = _map_label_entries(map_page.get("text_entries", []))
    label_entries = _map_label_entries_for_region(raw_label_entries, map_page)
    if not label_entries:
        return {}
    bbox = _entries_bbox(label_entries)
    left = max(0, int((bbox or {}).get("left", 0) - 35))
    top_pad = 8 if float((bbox or {}).get("top", 0)) > float(map_page.get("height") or 1) * 0.25 else 35
    top = max(0, int((bbox or {}).get("top", 0) - top_pad))
    right = min(int(map_page.get("width") or 640), int((bbox or {}).get("right", int(map_page.get("width") or 640)) + 35))
    bottom = min(int(map_page.get("height") or 480), int((bbox or {}).get("bottom", int(map_page.get("height") or 480)) + 35))
    left, top, right, bottom = _nudge_map_region_away_from_body_text(map_page, label_entries, left, top, right, bottom)

    labels = [
        {
            "key": _map_label_key(entry, index),
            "text": _normalise_map_label_text(str(entry.get("plain") or "")),
            "save_id": entry.get("save_id", ""),
            "targets": entry.get("save_ids") or [entry.get("save_id", "")],
            "left": entry.get("left"),
            "top": entry.get("top"),
            "width": entry.get("width"),
            "height": entry.get("height"),
            "rank": _map_label_rank(_normalise_map_label_text(str(entry.get("plain") or ""))),
        }
        for index, entry in enumerate(label_entries, start=1)
        if _normalise_map_label_text(str(entry.get("plain") or ""))
    ]
    page_text = " ".join(str(entry.get("plain") or "") for entry in map_page.get("text_entries", []))
    center = _infer_map_center(page_text)
    content = _infer_map_content(page_text, labels)

    return {
        "id": f"map-page-{map_page.get('page_num') or 1}",
        "save_id": f"exact-page{map_page.get('page_num') or 1}-map",
        "page": str(map_page.get("page_num") or 1),
        "left": left,
        "top": top,
        "width": max(1, right - left),
        "height": max(1, bottom - top),
        "mode": "source-pdf",
        "label": "Location Map",
        "vibe": "Exact PDF brochure map with dark background, transport hubs, restaurants, fitness, cafes, and highlighted subject property",
        "center": center,
        "content": content,
        "labels": labels,
    }


def _nudge_map_region_away_from_body_text(
    page: dict[str, Any],
    label_entries: list[dict[str, Any]],
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> tuple[int, int, int, int]:
    label_targets = {
        str(target)
        for entry in label_entries
        for target in (entry.get("save_ids") or [entry.get("save_id")])
        if target
    }
    body_bottom = float(top)
    for entry in page.get("text_entries") or []:
        save_id = str(entry.get("save_id") or "")
        if save_id and save_id in label_targets:
            continue
        text = str(entry.get("plain") or "").strip()
        if len(text) < 14:
            continue
        entry_left = float(entry.get("left") or 0)
        entry_top = float(entry.get("top") or 0)
        if entry_top > top + 80:
            continue
        entry_right = _text_entry_right(entry)
        if entry_right < left or entry_left > right:
            continue
        if _looks_like_map_region_false_positive(
            text,
            left=entry_left,
            top=entry_top,
            page_width=float(page.get("width") or 1),
            page_height=float(page.get("height") or 1),
        ):
            body_bottom = max(body_bottom, entry_top + max(18.0, _font_size_px(entry) * 1.4))
    if body_bottom > top:
        top = min(bottom - 80, int(body_bottom + 10))
    return left, top, right, bottom


def _map_region_from_image_regions(pages: list[dict[str, Any]]) -> dict[str, Any]:
    regions = _map_regions_from_image_regions(pages)
    return regions[0] if regions else {}


def _map_regions_from_image_regions(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[tuple[int, float, int, dict[str, Any], dict[str, Any]]] = []
    for page in pages:
        page_num = int(page.get("page_num") or 0)
        if not page_num:
            continue
        for region in page.get("image_regions") or []:
            if not isinstance(region, dict) or str(region.get("role") or region.get("type") or "") != "map":
                continue
            source_evidence = region.get("source_evidence") if isinstance(region.get("source_evidence"), dict) else {}
            source = str(source_evidence.get("source") or "")
            source_is_pdf_image_map = source.startswith("PyMuPDF image")
            source_is_confident_pdf_map = _image_region_is_confident_pdf_map(source_evidence)
            if page.get("inventory_page_purpose") is not None and not page.get("inventory_map_expected") and not source_is_confident_pdf_map:
                continue
            bbox = _bbox_from_semantic_region(region)
            bbox = _scale_fractional_region_to_page(bbox, page)
            width = float(bbox.get("width") or 0)
            height = float(bbox.get("height") or 0)
            if width <= 0 or height <= 0:
                continue
            source_is_rendered_asset = source.startswith("/api/projects/")
            source_is_inventory_map = source == "extraction-inventory map_regions" and bool(
                page.get("inventory_map_expected")
            )
            if not source_is_rendered_asset and not source_is_inventory_map and not source_is_pdf_image_map:
                continue
            if _map_region_has_non_map_text_panel_overlap(region):
                continue
            if _map_image_region_false_positive(bbox, page, source_evidence=source_evidence) and not (
                source_is_inventory_map and str(page.get("inventory_page_purpose") or "") == "connectivitymap"
            ):
                continue
            preferred = 2 if (source_is_rendered_asset or source_is_pdf_image_map) else 1
            candidates.append((preferred, width * height, page_num, page, bbox))
    if not candidates:
        return []
    candidates.sort(key=lambda item: (item[2], -item[0], -item[1]))
    regions: list[dict[str, Any]] = []
    for _preferred, _area, page_num, page, bbox in candidates:
        region = {
            "id": f"map-page-{page_num}",
            "save_id": f"exact-page{page_num}-map",
            "page": str(page_num),
            "left": float(bbox.get("left") or 0),
            "top": float(bbox.get("top") or 0),
            "width": max(1.0, float(bbox.get("width") or page.get("width") or 1)),
            "height": max(1.0, float(bbox.get("height") or page.get("height") or 1)),
            "mode": "source-pdf",
            "label": "Location Map",
            "vibe": "Source PDF brochure map preserved as an editable map region",
            "center": {},
            "content": {},
            "labels": [],
        }
        if any(str(existing.get("page") or "") == str(page_num) and _field_region_iou(existing, region) >= 0.50 for existing in regions):
            continue
        regions.append(region)
    return regions


def _image_region_is_confident_pdf_map(source_evidence: dict[str, Any]) -> bool:
    evidence_context = source_evidence.get("context") if isinstance(source_evidence.get("context"), dict) else {}
    return (
        str(source_evidence.get("source") or "").startswith("PyMuPDF image")
        and bool(evidence_context.get("has_map"))
        and not bool(evidence_context.get("is_contact_page"))
        and "map" in _compact_text(str(source_evidence.get("reason") or ""))
    )


def _map_region_has_non_map_text_panel_overlap(region: dict[str, Any]) -> bool:
    evidence = region.get("source_evidence") if isinstance(region.get("source_evidence"), dict) else {}
    overlap = evidence.get("text_overlap") if isinstance(evidence.get("text_overlap"), dict) else {}
    entries_inside = int(overlap.get("entries_inside") or 0)
    samples = [str(sample or "").strip() for sample in overlap.get("samples") or [] if str(sample or "").strip()]
    if entries_inside < 8 or not samples:
        return False
    compact_samples = [_compact_text(sample) for sample in samples]
    non_map_tokens = (
        "hotel",
        "studentaccommodation",
        "accommodation",
        "coliving",
        "selfstorage",
        "occupancyrates",
        "planningpermission",
        "market",
        "investorconfidence",
    )
    if any(any(token in compact for token in non_map_tokens) for compact in compact_samples):
        return True
    long_samples = [sample for sample in samples if len(sample) >= 34 and not _looks_like_station_label(sample)]
    return len(long_samples) >= 2


def _map_image_region_false_positive(
    bbox: dict[str, float],
    page: dict[str, Any],
    *,
    source_evidence: dict[str, Any] | None = None,
) -> bool:
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    width = float(bbox.get("width") or 0)
    height = float(bbox.get("height") or 0)
    area_ratio = (width * height) / max(1.0, page_width * page_height)
    labels = _map_label_entries_for_region(_map_label_entries(page.get("text_entries", [])), page)
    quality = _map_label_region_quality(labels)
    compact = _compact_text(" ".join(str(entry.get("plain") or "") for entry in page.get("text_entries", [])))
    tableish = any(token in compact for token in ("investmentsummary", "proposal", "schedule", "statusnotes", "comparableschemes"))
    confident_pdf_map = _image_region_is_confident_pdf_map(source_evidence or {})
    if area_ratio >= 0.22 and (len(labels) < 8 or quality < 10):
        if confident_pdf_map and not tableish:
            return False
        return True
    if area_ratio < 0.58:
        return False
    if len(labels) >= 5:
        return quality < 14
    if tableish or area_ratio >= 0.72:
        return True
    return False


def _map_label_region_quality(labels: list[dict[str, Any]]) -> int:
    score = 0
    for label in labels:
        text = _normalise_map_label_text(str(label.get("plain") or label.get("text") or ""))
        compact = _compact_text(text)
        if not compact:
            continue
        if re.search(r"\b\d{3,}\b", text) or any(token in compact for token in ("sqft", "upperfloors", "capital", "housing")):
            continue
        if _looks_like_station_label(text):
            score += 2
            continue
        if any(token in compact for token in ("road", "street", "rd", "st", "square", "broadway", "olympia", "baronscourt")):
            score += 1
            continue
        alpha_words = re.findall(r"[A-Za-z]+", text)
        if alpha_words and all(word.isupper() for word in alpha_words) and 4 <= len(compact) <= 18:
            score += 1
    return score


def _scale_fractional_region_to_page(region: dict[str, float], page: dict[str, Any]) -> dict[str, float]:
    page_width = float(page.get("width") or 0)
    page_height = float(page.get("height") or 0)
    if page_width <= 0 or page_height <= 0:
        return region
    width = float(region.get("width") or 0)
    height = float(region.get("height") or 0)
    left = float(region.get("left") or 0)
    top = float(region.get("top") or 0)
    if width <= 1.5 and height <= 1.5 and abs(left) <= 1.5 and abs(top) <= 1.5:
        return {
            "left": round(left * page_width, 2),
            "top": round(top * page_height, 2),
            "width": round(width * page_width, 2),
            "height": round(height * page_height, 2),
        }
    return region


def _map_page_score(page_text: str, entries: list[dict[str, Any]]) -> int:
    compact = _compact_text(page_text)
    tokens = (
        "station",
        "street",
        "market",
        "circle",
        "rail",
        "tube",
        "underground",
        "metro",
        "minutes",
        "walk",
        "restaurant",
        "fitness",
        "gym",
        "cafe",
        "coffee",
        "hotel",
        "park",
        "square",
    )
    score = sum(1 for token in tokens if token in compact)
    label_entries = _map_label_entries(entries)
    label_like = len(label_entries)
    if label_like >= 20:
        score += 4
    elif label_like >= 8:
        score += 2
    elif "location" in compact and label_like >= 3:
        score += 4
    return score


def _build_map_label_fields(map_region: dict[str, Any], *, max_fields: int = 42) -> list[dict[str, Any]]:
    labels = [label for label in map_region.get("labels", []) if label.get("save_id") and label.get("text")]
    labels.sort(key=lambda label: (_map_label_sort_rank(label), float(label.get("top") or 0), float(label.get("left") or 0)))
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label in labels:
        text = str(label.get("text") or "").strip()
        compact = _compact_text(text)
        if not compact or compact in seen:
            continue
        seen.add(compact)
        fields.append(
            {
                "key": f"mapLabel:{label.get('key')}",
                "label": text[:42],
                "value": text,
                "targets": [str(target) for target in (label.get("targets") or [label.get("save_id")]) if target],
                "rank": _map_label_sort_rank(label),
            }
        )
        if len(fields) >= max_fields:
            break
    return fields


def _build_map_label_fields_for_regions(map_regions: list[dict[str, Any]], *, max_fields: int = 42) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for region in map_regions:
        for field in _build_map_label_fields(region, max_fields=max_fields):
            target_key = ",".join(str(target) for target in field.get("targets") or [])
            unique_key = f"{field.get('key')}:{target_key}"
            if unique_key in seen:
                continue
            seen.add(unique_key)
            if len(map_regions) > 1:
                field = {
                    **field,
                    "label": f'P{region.get("page")} {field.get("label")}',
                    "map_save_id": region.get("save_id"),
                }
            fields.append(field)
            if len(fields) >= max_fields:
                return fields
    return fields


def _map_label_sort_rank(label: dict[str, Any]) -> int:
    rank = label.get("rank")
    try:
        return int(rank) if rank is not None else 999
    except (TypeError, ValueError):
        return 999


def _map_label_key(entry: dict[str, Any], index: int) -> str:
    compact = _compact_text(str(entry.get("plain") or ""))
    if compact:
        compact = compact[:34]
    return f"{index:03d}-{compact or 'label'}"


def _map_label_rank(text: str) -> int:
    compact = _compact_text(text)
    if _looks_like_subject_property_label(text):
        return 0
    if _looks_like_station_label(text):
        return 1
    if any(token in compact for token in ("station", "circle", "market", "street")):
        return 2
    if any(token in compact for token in ("fitness", "gym", "coffee", "cafe", "restaurant", "sushi", "wine")):
        return 3
    return 4


def _infer_map_center(page_text: str) -> dict[str, float]:
    return {}


def _infer_map_content(page_text: str, labels: list[dict[str, Any]]) -> dict[str, Any]:
    compact = _compact_text(page_text)
    subject_label = next((label for label in labels if _looks_like_subject_property_label(str(label.get("text") or ""))), None)
    building_name = str((subject_label or {}).get("text") or "").strip() or "Subject Property"
    station_names = []
    for label in labels:
        text = str(label.get("text") or "").strip()
        if _looks_like_station_label(text):
            station_names.append({"name": text.title() if text.isupper() else text})
        if len(station_names) >= 4:
            break
    categories = []
    category_tests = {
        "restaurant": ("restaurant", "sushi", "brasserie", "market", "pizza", "burger"),
        "cafe": ("coffee", "cafe", "kitchen", "notes"),
        "fitness": ("fitness", "gym", "athletic", "healthclub", "wellness"),
        "hotel": ("hotel", "inn", "suites", "hostel", "lodging"),
    }
    for category, tokens in category_tests.items():
        if any(token in compact for token in tokens):
            categories.append(category)
    if not categories and labels:
        categories = ["restaurant", "cafe", "fitness"]
    return {
        "building_name": building_name,
        "stations": station_names,
        "poi_categories": categories,
    }


def _looks_like_subject_property_label(value: str) -> bool:
    text = _normalise_map_label_text(str(value or "").strip())
    compact = _compact_text(text)
    if not text or len(text) > 60:
        return False
    if any(token in compact for token in ("minutewalk", "walk", "minute")):
        return False
    if any(token in compact for token in ("subjectproperty", "propertymarker", "youarehere")):
        return True
    alpha_words = re.findall(r"[A-Za-z]+", text)
    if re.match(r"^\d{1,4}[a-z]", compact) and len(alpha_words) >= 2:
        return True
    return bool(re.search(r"\b\d{1,4}\s+[A-Za-z]", text)) and len(alpha_words) >= 2


def _looks_like_station_label(value: str) -> bool:
    text = _normalise_map_label_text(str(value or "").strip())
    compact = _compact_text(text)
    if not text or len(text) > 42:
        return False
    if any(token in compact for token in ("station", "rail", "tube", "metro", "underground", "overground", "elizabethline")):
        return True
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return False
    mostly_upper = sum(1 for word in words if word.isupper()) >= max(1, len(words) - 1)
    titleish = sum(1 for word in words if word[:1].isupper()) >= max(1, len(words) - 1)
    known_stations = {
        "bank",
        "moorgate",
        "liverpoolst",
        "liverpoolstreet",
        "farringdon",
        "barbican",
        "chancerylane",
        "oldstreet",
    }
    return compact in known_stations and (mostly_upper or titleish)


def _amenity_icon_values(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _map_label_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    deny = {"", "location", "connectivity", "amenities", "features", "specification", "highlights"}
    for entry in entries:
        plain = str(entry.get("plain") or "").strip()
        if not plain:
            continue
        compact = _compact_text(plain)
        if compact in deny or (compact.isdigit() and len(compact) <= 2):
            continue
        if len(compact) <= 2 and not re.fullmatch(r"[A-Z]{1,2}", plain.replace("\xa0", " ").strip()):
            continue
        left = entry.get("left")
        top = entry.get("top")
        if left is None or top is None:
            continue
        if len(plain) <= 2 and not plain.isdigit() and not re.fullmatch(r"[A-Z]", plain.replace("\xa0", " ").strip()):
            continue
        if not _looks_like_map_label_entry(plain):
            continue
        labels.append(entry)
    return labels


def _map_label_entries_for_region(entries: list[dict[str, Any]], page: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep labels that describe the visual map, not surrounding copy/tables.

    Brochures often place amenity lists, transport tables, and narrative copy
    beside a rendered map. Those labels are map-ish semantically, but using
    them for the map bbox creates giant hover/regenerate regions that cover
    editable text. This filter keeps the geographically distributed labels and
    removes dense sidebar/table columns before the bbox is computed.
    """
    merged = _merge_map_label_fragments(entries)
    if not merged:
        return []
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    dense_buckets = _dense_non_map_label_buckets(merged, page_width, page_height)
    sidebar_cutoff = _map_sidebar_cutoff(merged, dense_buckets)
    filtered: list[dict[str, Any]] = []
    for entry in merged:
        text = _normalise_map_label_text(str(entry.get("plain") or ""))
        if not text:
            continue
        left = float(entry.get("left") or 0)
        top = float(entry.get("top") or 0)
        bucket = int(left // 34)
        if bucket in dense_buckets:
            continue
        if sidebar_cutoff and left < sidebar_cutoff and not _looks_like_subject_property_label(text):
            continue
        if _looks_like_map_region_false_positive(text, left=left, top=top, page_width=page_width, page_height=page_height):
            continue
        filtered.append(entry)

    if len(filtered) >= 3:
        return filtered

    # If filtering was too strict, retain only the strongest geographic labels
    # rather than falling back to the full page.
    strong = [
        entry
        for entry in merged
        if _looks_like_station_label(str(entry.get("plain") or ""))
        or _looks_like_subject_property_label(str(entry.get("plain") or ""))
    ]
    return strong if len(strong) >= 3 else filtered


def _dense_non_map_label_buckets(entries: list[dict[str, Any]], page_width: float, page_height: float) -> set[int]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        text = _normalise_map_label_text(str(entry.get("plain") or ""))
        if not text:
            continue
        left = float(entry.get("left") or 0)
        buckets.setdefault(int(left // 34), []).append(entry)
    dense: set[int] = set()
    for bucket, bucket_entries in buckets.items():
        if len(bucket_entries) < 5:
            continue
        tops = [float(entry.get("top") or 0) for entry in bucket_entries]
        vertical_span = max(tops) - min(tops)
        if vertical_span < page_height * 0.07:
            continue
        falseish = 0
        for entry in bucket_entries:
            text = _normalise_map_label_text(str(entry.get("plain") or ""))
            compact = _compact_text(text)
            if _looks_like_station_label(text) or _looks_like_subject_property_label(text):
                continue
            if len(text) > 18 or any(token in compact for token in _MAP_FALSE_POSITIVE_TOKENS):
                falseish += 1
        average_len = sum(len(str(entry.get("plain") or "")) for entry in bucket_entries) / max(1, len(bucket_entries))
        bucket_left = bucket * 34.0
        sidebar_list_like = (
            len(bucket_entries) >= 5
            and bucket_left < page_width * 0.34
            and average_len >= 12
            and vertical_span >= page_height * 0.07
        )
        if falseish >= max(3, len(bucket_entries) // 2) or average_len >= 16 or sidebar_list_like:
            dense.add(bucket)
    return dense


def _map_sidebar_cutoff(entries: list[dict[str, Any]], dense_buckets: set[int]) -> float:
    if not dense_buckets:
        return 0.0
    rights: list[float] = []
    for entry in entries:
        left = float(entry.get("left") or 0)
        if int(left // 34) not in dense_buckets:
            continue
        rights.append(left + max(20.0, min(280.0, len(str(entry.get("plain") or "")) * 8.8)))
    return max(rights) + 42.0 if rights else 0.0


_MAP_FALSE_POSITIVE_TOKENS = {
    "amenities",
    "pubsrestaurants",
    "corporateoffices",
    "livingsectorschemes",
    "otherlivingsectorschemes",
    "investmentsummary",
    "proposal",
    "schemes",
    "scheme",
    "statusnotes",
    "schedule",
    "office",
    "offices",
    "consented",
    "ongoing",
    "application",
    "completed",
    "validated",
    "appeal",
    "permitted",
    "developmentrights",
}


def _looks_like_map_region_false_positive(
    text: str,
    *,
    left: float,
    top: float,
    page_width: float,
    page_height: float,
) -> bool:
    compact = _compact_text(text)
    if not compact:
        return True
    if _looks_like_station_label(text) or _looks_like_subject_property_label(text):
        return False
    if len(compact) == 1 and not compact.isdigit():
        return True
    if len(compact) <= 3 and not compact.isdigit():
        return True
    if compact in _MAP_FALSE_POSITIVE_TOKENS or any(token in compact for token in _MAP_FALSE_POSITIVE_TOKENS):
        return True
    if top < page_height * 0.22 and "house" not in compact:
        return True
    if len(text) > 28 and not _looks_like_subject_property_label(text):
        return True
    if left < page_width * 0.34 and top < page_height * 0.38 and len(text) > 14:
        return True
    words = re.findall(r"[A-Za-z]+", text)
    if len(words) >= 4 and not _looks_like_subject_property_label(text):
        return True
    return False


def _merge_map_label_fragments(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(entries, key=lambda entry: (float(entry.get("top") or 0), float(entry.get("left") or 0)))
    used: set[int] = set()
    merged: list[dict[str, Any]] = []
    for index, entry in enumerate(ordered):
        if index in used:
            continue
        used.add(index)
        cluster = [entry]
        if _looks_like_mergeable_map_fragment(str(entry.get("plain") or "")):
            current = entry
            while True:
                next_index: int | None = None
                next_entry: dict[str, Any] | None = None
                for candidate_index, candidate in enumerate(ordered):
                    if candidate_index in used:
                        continue
                    if not _looks_like_mergeable_map_fragment(str(candidate.get("plain") or "")):
                        continue
                    if _map_fragments_are_adjacent(current, candidate):
                        next_index = candidate_index
                        next_entry = candidate
                        break
                if next_index is None or next_entry is None:
                    break
                used.add(next_index)
                cluster.append(next_entry)
                current = next_entry
        if len(cluster) == 1 and _looks_like_fragmented_road_label(str(entry.get("plain") or "")):
            continue
        merged.append(_merged_map_label_entry(cluster))
    return merged


def _merged_map_label_entry(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    if not cluster:
        return {}
    cluster = sorted(cluster, key=lambda entry: (float(entry.get("left") or 0), float(entry.get("top") or 0)))
    text = _normalise_map_label_text(" ".join(str(entry.get("plain") or "").strip() for entry in cluster))
    left = min(float(entry.get("left") or 0) for entry in cluster)
    top = min(float(entry.get("top") or 0) for entry in cluster)
    right = max(float(entry.get("left") or 0) + max(20.0, min(220.0, len(str(entry.get("plain") or "")) * 9.0)) for entry in cluster)
    bottom = max(float(entry.get("top") or 0) + 24.0 for entry in cluster)
    base = dict(cluster[0])
    base.update(
        {
            "plain": text,
            "save_id": cluster[0].get("save_id", ""),
            "save_ids": [entry.get("save_id", "") for entry in cluster if entry.get("save_id")],
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top,
        }
    )
    return base


def _map_fragments_are_adjacent(left_entry: dict[str, Any], right_entry: dict[str, Any]) -> bool:
    left_x = float(left_entry.get("left") or 0)
    right_x = float(right_entry.get("left") or 0)
    if right_x <= left_x:
        return False
    dx = right_x - left_x
    dy = abs(float(right_entry.get("top") or 0) - float(left_entry.get("top") or 0))
    return dx <= 140 and dy <= 46


def _looks_like_mergeable_map_fragment(text: str) -> bool:
    value = str(text or "").replace("\xa0", " ").strip()
    if not value or re.search(r"\d", value):
        return False
    words = re.findall(r"[A-Za-z]+", value)
    if not words or not all(word.isupper() for word in words):
        return False
    compact = _compact_text(value)
    if len(compact) < 1 or len(compact) > 18:
        return False
    if compact in {"bank", "moorgate", "cornhill", "broadgate", "finsbury", "circus"}:
        return False
    return True


def _normalise_map_label_text(value: str) -> str:
    text = str(value or "").replace("\xa0", " ").strip()
    if not text:
        return ""
    groups = [group.strip() for group in re.split(r"\s{2,}", text) if group.strip()]
    if len(groups) > 1:
        return " ".join(_normalise_map_label_group(group) for group in groups if group).strip()
    return _normalise_map_label_group(text)


def _normalise_map_label_group(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    words = re.findall(r"[A-Za-z0-9]+", text)
    alpha_words = [word for word in words if re.search(r"[A-Za-z]", word)]
    if words and not alpha_words and all(word.isdigit() for word in words):
        return "".join(words)
    if not alpha_words:
        return text
    all_upper = all(word.isupper() for word in alpha_words)
    spaced = " " in text and all(len(word) <= 5 for word in alpha_words)
    if all_upper and spaced:
        collapsed = "".join(words)
        return _prettify_collapsed_map_label(collapsed)
    return text


def _prettify_collapsed_map_label(value: str) -> str:
    collapsed = str(value or "").upper()
    number_prefix = re.match(r"^(\d{1,4})([A-Z].*)$", collapsed)
    prefix = ""
    if number_prefix:
        prefix = number_prefix.group(1) + " "
        collapsed = number_prefix.group(2)
    suffixes = ("STREET", "MARKET", "CIRCLE", "CIRCUS", "GARDEN", "GREEN", "LANE", "ROAD", "SQUARE", "WALL")
    for suffix in suffixes:
        if collapsed.endswith(suffix) and len(collapsed) > len(suffix) + 2:
            return f"{prefix}{collapsed[:-len(suffix)]} {suffix}"
    if collapsed.endswith("ST") and len(collapsed) > 4:
        return f"{prefix}{collapsed[:-2]} ST"
    return prefix + collapsed


def _looks_like_map_label_entry(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 46:
        return False
    if "@" in text or re.search(r"\b0\d[\d\s]{8,}\b", text):
        return False
    if re.search(r"[.;:]\s*$", text):
        return False
    compact = _compact_text(text)
    if _looks_like_subject_property_label(text):
        return True
    words = re.findall(r"[A-Za-z0-9]+", text)
    if not words or len(words) > 5:
        return False
    transit_route_tokens = (
        "elizabeth",
        "circle",
        "hammersmith",
        "metropolitan",
        "thameslink",
        "central",
        "northern",
        "piccadilly",
        "victoria",
    )
    if any(token in compact for token in transit_route_tokens) and any(separator in text for separator in (",", "&", " - ")):
        return False
    if _looks_like_station_label(text):
        return True
    place_tokens = (
        "bank",
        "market",
        "street",
        "lane",
        "road",
        "square",
        "circle",
        "circus",
        "garden",
        "green",
        "exchange",
        "broadgate",
        "moorgate",
        "farringdon",
        "barbican",
        "clerkenwell",
        "liverpool",
        "chancery",
        "restaurant",
        "cafe",
        "coffee",
        "sushi",
        "wine",
        "fitness",
        "gym",
        "hotel",
    )
    alpha_words = [word for word in words if re.search(r"[A-Za-z]", word)]
    if not alpha_words:
        return False
    all_upper = all(word.isupper() for word in alpha_words)
    titleish = sum(1 for word in alpha_words if word[:1].isupper()) >= max(1, len(alpha_words) - 1)
    if any(token in compact for token in place_tokens):
        return len(alpha_words) <= 4 and (all_upper or titleish)
    if all_upper:
        return True
    if len(alpha_words) == 1:
        return False
    return titleish and len(alpha_words) <= 4


def _looks_like_fragmented_road_label(text: str) -> bool:
    compact = _compact_text(text)
    if len(compact) < 4 or len(compact) > 10:
        return False
    words = re.findall(r"[A-Za-z]+", text)
    if not words or not all(word.isupper() for word in words):
        return False
    if len(words) == 1:
        road_fragments = {"moor", "treet", "chur", "chs", "bishopsga"}
        return compact in road_fragments
    known_short_labels = {"bank", "moorgate", "cornhill", "broadgate", "stpauls"}
    if compact in known_short_labels:
        return False
    place_tokens = ("street", "market", "circle", "circus", "garden", "green", "lane", "road", "square")
    if any(token in compact for token in place_tokens):
        return False
    return True


def _has_map_label_distribution(labels: list[dict[str, Any]], page: dict[str, Any]) -> bool:
    if len(labels) < 3:
        return False
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    non_footer_labels = [
        label
        for label in labels
        if not (
            _looks_like_subject_property_label(str(label.get("plain") or label.get("text") or ""))
            and float(label.get("top") or 0) >= page_height * 0.82
        )
    ]
    if len(non_footer_labels) < 3:
        return False
    lefts = [float(label.get("left") or 0) for label in non_footer_labels]
    tops = [float(label.get("top") or 0) for label in non_footer_labels]
    if not lefts or not tops:
        return False
    spread_x = max(lefts) - min(lefts)
    spread_y = max(tops) - min(tops)
    if len(non_footer_labels) >= 10:
        return spread_x >= page_width * 0.30 and spread_y >= page_height * 0.30
    return spread_x >= page_width * 0.25 and spread_y >= page_height * 0.18


def _entries_bbox(entries: list[dict[str, Any]]) -> dict[str, float] | None:
    if not entries:
        return None
    lefts = [float(entry.get("left") or 0) for entry in entries]
    tops = [float(entry.get("top") or 0) for entry in entries]
    rights = [float(entry.get("left") or 0) + max(20, min(260, len(str(entry.get("plain") or "")) * 9)) for entry in entries]
    bottoms = [float(entry.get("top") or 0) + 28 for entry in entries]
    return {"left": min(lefts), "top": min(tops), "right": max(rights), "bottom": max(bottoms)}


def _build_agency_logo_defs(
    contacts: list[dict[str, Any]],
    entries: list[dict[str, Any]] | None = None,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    logos: dict[str, dict[str, Any]] = {}
    if not contacts:
        return logos
    entries_by_id = {str(entry.get("save_id") or ""): entry for entry in (entries or []) if entry.get("save_id")}
    entries_by_page: dict[str, list[dict[str, Any]]] = {}
    for entry in entries or []:
        page_key = str(entry.get("page_num") or "")
        entries_by_page.setdefault(page_key, []).append(entry)
    pages_by_num = {str(page.get("page_num") or ""): page for page in (pages or [])}

    grouped: dict[str, list[dict[str, Any]]] = {}
    group_order: list[str] = []
    for contact in contacts:
        key = _agency_group_key(contact)
        if key not in grouped:
            grouped[key] = []
            group_order.append(key)
        grouped[key].append(contact)

    for index, group_key in enumerate(group_order[:6], start=1):
        group_contacts = sorted(
            grouped[group_key],
            key=lambda contact: (
                int((contact.get("anchor") or {}).get("page_num") or 1),
                float((contact.get("anchor") or {}).get("top") or 0),
                float((contact.get("anchor") or {}).get("left") or 0),
            ),
        )
        contact = group_contacts[0]
        anchor = contact.get("anchor") or {}
        page = str(anchor.get("page_num") or 1)
        label = _agency_label_from_contact(contact, index)
        contact_bbox = _contact_group_bbox(group_contacts, entries_by_id)
        detected = _detect_agency_logo_region(
            pages_by_num.get(page),
            contact_bbox,
            page_entries=entries_by_page.get(page, []),
            fallback_label=label,
            slot_index=index,
        )
        if detected:
            left = detected["left"]
            top = detected["top"]
            width = detected["width"]
            height = detected["height"]
        else:
            left = max(24, float((contact_bbox or anchor).get("left") or 68) - 6)
            top = _agency_logo_top_for_contact_group(group_contacts, entries_by_id, entries_by_page.get(page, []))
            width = min(380, max(170, len(label.replace("|||", " ")) * 13))
            height = 96
        logos[f"agency{index}"] = {
            "label": label.replace("|||", " "),
            "page": page,
            "left": round(left, 2),
            "top": round(top, 2),
            "width": round(width, 2),
            "height": round(height, 2),
            "className": "agency",
            "defaultText": label,
            "defaultAssetUrl": (detected or {}).get("asset_url", ""),
            "sourceDetection": (detected or {}).get("source", "contact-anchor-fallback"),
            "sourceContactKeys": [str(item.get("key") or "") for item in group_contacts],
        }
    return logos


def _contact_group_bbox(
    contacts: list[dict[str, Any]],
    entries_by_id: dict[str, dict[str, Any]],
) -> dict[str, float] | None:
    contact_entries: list[dict[str, Any]] = []
    for contact in contacts:
        for save_id in list(contact.get("targets") or []) + list(contact.get("hide_targets") or []):
            entry = entries_by_id.get(str(save_id))
            if entry:
                contact_entries.append(entry)
        anchor = contact.get("anchor") if isinstance(contact.get("anchor"), dict) else None
        if anchor:
            contact_entries.append(anchor)
    return _entries_bbox(contact_entries)


def _detect_agency_logo_region(
    page: dict[str, Any] | None,
    contact_bbox: dict[str, float] | None,
    *,
    page_entries: list[dict[str, Any]] | None = None,
    fallback_label: str,
    slot_index: int,
) -> dict[str, Any] | None:
    if not page or not contact_bbox:
        return None
    background_path = Path(str(page.get("background_path") or ""))
    if not background_path.is_file():
        return None
    try:
        image = _load_rgb_image(background_path)
        if image is None:
            return None
        page_width, page_height = image.size
    except Exception:
        return None

    contact_left = float(contact_bbox.get("left") or 0)
    contact_top = float(contact_bbox.get("top") or 0)
    contact_right = float(contact_bbox.get("right") or contact_left + 170)
    contact_bottom = float(contact_bbox.get("bottom") or contact_top + 60)
    column_left = max(0.0, contact_left - 28)
    column_right = min(float(page_width), max(contact_right + 72, contact_left + 230))
    candidates = [
        {
            "source": "raster logo region above contact group",
            "left": column_left,
            "top": max(0.0, contact_top - 190),
            "right": column_right,
            "bottom": max(0.0, contact_top - 18),
            "direction_bias": 1.0,
        },
        {
            "source": "raster logo region below contact group",
            "left": column_left,
            "top": min(float(page_height), contact_bottom + 18),
            "right": column_right,
            "bottom": min(float(page_height), contact_bottom + 210),
            "direction_bias": 0.96,
        },
    ]
    detections: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["bottom"] - candidate["top"] < 28 or candidate["right"] - candidate["left"] < 56:
            continue
        foreground = _foreground_bbox_in_region(background_path, candidate)
        if not foreground:
            continue
        if _overlaps_text_component(foreground, page_entries or []):
            continue
        width = foreground["width"]
        height = foreground["height"]
        if width < 58 or height < 24:
            continue
        score = (foreground["foreground_count"] * 1.0) + (width * height * 0.08) + (height * 14)
        score *= float(candidate["direction_bias"])
        detections.append({**foreground, "score": score, "source": candidate["source"]})
    if not detections:
        return None

    best = max(detections, key=lambda item: float(item.get("score") or 0))
    bbox = {
        "left": max(0.0, best["left"] - 4),
        "top": max(0.0, best["top"] - 4),
        "width": min(float(page_width), best["left"] + best["width"] + 8) - max(0.0, best["left"] - 4),
        "height": min(float(page_height), best["top"] + best["height"] + 8) - max(0.0, best["top"] - 4),
    }
    filename = f"agency-logo-p{page.get('page_num')}-{_slug_key(fallback_label, 'agency')}-{slot_index}.png"
    asset_url = _crop_source_region_asset(background_path, bbox, filename)
    return {**bbox, "asset_url": asset_url, "source": best["source"], "score": round(float(best["score"]), 2)}


def _foreground_bbox_in_region(image_path: Path, candidate: dict[str, float]) -> dict[str, float] | None:
    try:
        rgb = _load_rgb_image(image_path)
        if rgb is None:
            return None
        left = max(0, int(float(candidate["left"])))
        top = max(0, int(float(candidate["top"])))
        right = min(rgb.width, int(float(candidate["right"])))
        bottom = min(rgb.height, int(float(candidate["bottom"])))
        if right <= left or bottom <= top:
            return None
        crop = rgb.crop((left, top, right, bottom))
        background = _dominant_edge_colour(crop)
        xs: list[int] = []
        ys: list[int] = []
        row_bounds: dict[int, list[float]] = {}
        foreground_count = 0
        step = 2 if crop.width * crop.height > 50_000 else 1
        for y in range(0, crop.height, step):
            for x in range(0, crop.width, step):
                pixel = crop.getpixel((x, y))
                if not _is_agency_logo_foreground_pixel(pixel, background):
                    continue
                xs.append(x)
                ys.append(y)
                foreground_count += step * step
                bounds = row_bounds.setdefault(y, [float(x), float(x + step), 0.0])
                bounds[0] = min(bounds[0], float(x))
                bounds[1] = max(bounds[1], float(x + step))
                bounds[2] += step * step
        if not xs or foreground_count < 90:
            return None
        selected = _select_agency_logo_foreground_cluster(row_bounds, candidate, crop.height)
        if selected:
            x0 = int(selected["x0"])
            y0 = int(selected["y0"])
            x1 = int(selected["x1"])
            y1 = int(selected["y1"])
            foreground_count = int(selected["foreground_count"])
        else:
            x0 = min(xs)
            y0 = min(ys)
            x1 = max(xs) + step
            y1 = max(ys) + step
        return {
            "left": float(left + x0),
            "top": float(top + y0),
            "width": float(max(1, x1 - x0)),
            "height": float(max(1, y1 - y0)),
            "foreground_count": float(foreground_count),
        }
    except Exception:
        return None


def _select_agency_logo_foreground_cluster(
    row_bounds: dict[int, list[float]],
    candidate: dict[str, float],
    crop_height: int,
) -> dict[str, float] | None:
    if not row_bounds:
        return None
    rows = sorted(row_bounds)
    max_gap = 24
    clusters: list[list[int]] = []
    current: list[int] = []
    previous: int | None = None
    for row in rows:
        if previous is None or row - previous <= max_gap:
            current.append(row)
        else:
            if current:
                clusters.append(current)
            current = [row]
        previous = row
    if current:
        clusters.append(current)

    summaries: list[dict[str, float]] = []
    for cluster_rows in clusters:
        x0 = min(row_bounds[row][0] for row in cluster_rows)
        x1 = max(row_bounds[row][1] for row in cluster_rows)
        y0 = float(min(cluster_rows))
        y1 = float(max(cluster_rows) + 1)
        foreground_count = sum(row_bounds[row][2] for row in cluster_rows)
        width = x1 - x0
        height = y1 - y0
        if width < 46 or height < 16 or foreground_count < 80:
            continue
        summaries.append(
            {
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "width": width,
                "height": height,
                "foreground_count": foreground_count,
            }
        )
    if not summaries:
        return None

    source = str(candidate.get("source") or "")
    if "below contact" in source:
        # Agency marks beneath contacts often sit above tiny legal/footer text.
        # Keep the first substantial component and avoid folding footer copy
        # into the replaceable logo crop.
        return min(summaries, key=lambda item: (item["y0"], -item["foreground_count"]))
    if "above contact" in source:
        return max(
            summaries,
            key=lambda item: (
                item["foreground_count"] + item["width"] * item["height"] * 0.12,
                item["y1"] / max(1, crop_height),
            ),
        )
    return max(
        summaries,
        key=lambda item: item["foreground_count"] + item["width"] * item["height"] * 0.12,
    )


def _is_agency_logo_foreground_pixel(pixel: tuple[int, int, int], background: tuple[int, int, int]) -> bool:
    red, green, blue = [int(value) for value in pixel[:3]]
    distance = _rgb_distance((red, green, blue), background)
    brightness = max(red, green, blue)
    saturation = (max(red, green, blue) - min(red, green, blue)) / max(1, brightness)
    return distance > 42 and (brightness > 145 or saturation > 0.22)


def _agency_logo_top_for_contact_group(
    contacts: list[dict[str, Any]],
    entries_by_id: dict[str, dict[str, Any]],
    page_entries: list[dict[str, Any]],
) -> float:
    contact_entries: list[dict[str, Any]] = []
    for contact in contacts:
        for save_id in list(contact.get("targets") or []) + list(contact.get("hide_targets") or []):
            entry = entries_by_id.get(str(save_id))
            if entry:
                contact_entries.append(entry)
        anchor = contact.get("anchor") if isinstance(contact.get("anchor"), dict) else None
        if anchor:
            contact_entries.append(anchor)
    tops = [float(entry.get("top") or 0) for entry in contact_entries if entry.get("top") is not None]
    if not tops:
        return 54.0
    contact_top = min(tops)
    contact_bottom = max(
        float(entry.get("top") or 0) + max(12.0, _font_size_px(entry) * 1.35)
        for entry in contact_entries
        if entry.get("top") is not None
    )
    legal_candidates = [
        float(entry.get("top") or 0)
        for entry in page_entries
        if float(entry.get("top") or 0) > contact_bottom + 30
        and _font_size_px(entry) <= 11
        and len(str(entry.get("plain") or "")) >= 45
    ]
    legal_top = min(legal_candidates) if legal_candidates else None
    if legal_top and legal_top - contact_bottom >= 116:
        return round(max(contact_bottom + 16, legal_top - 126), 2)
    return round(max(24.0, contact_top - 126), 2)


def _agency_group_key(contact: dict[str, Any]) -> str:
    email = str(contact.get("email") or "").strip().lower()
    domain = email.split("@", 1)[1] if "@" in email else ""
    anchor = contact.get("anchor") or {}
    page = str(anchor.get("page_num") or 1)
    if domain:
        return f"page:{page}:domain:{domain}"
    left_bucket = int(float(anchor.get("left") or 0) // 160)
    return f"page:{page}:col:{left_bucket}"


def _agency_label_from_contact(contact: dict[str, Any], index: int) -> str:
    email = str(contact.get("email") or "").strip().lower()
    if "@" not in email:
        return f"Agency {index}"
    domain = email.split("@", 1)[1]
    domain = re.sub(r"^(realestate|property|commercial|office|offices)\.", "", domain)
    stem = domain.split(".", 1)[0]
    words = [word for word in re.split(r"[-_]+", stem) if word]
    if not words:
        return f"Agency {index}"
    label = " ".join(words).upper()
    if len(label) <= 4:
        return label
    return label


def _render_fields_panel(config: dict[str, Any]) -> str:
    cover_title = config["cover_title"]
    cover_offer = config["cover_offer"]
    amenities_title = config["amenities_title"]
    amenity_rows = "\n".join(_render_amenity_field(field) for field in config["amenities"])
    service_icon_rows = "\n".join(_render_service_icon_field(field) for field in config.get("service_icons", []))
    space_plan_rows = "\n".join(_render_space_plan_field(field) for field in config.get("space_plans", []))
    logo_rows = "\n".join(_render_logo_field(key, field) for key, field in config["agency_logos"].items())
    contact_rows = "\n".join(_render_contact_field(field) for field in config["contacts"])
    source_logo_map = _render_source_logo_map(config.get("source_logos", []))
    typography_rows = _render_typography_fields(config.get("typography") or {})
    theme_colours = config.get("theme_colours") if isinstance(config.get("theme_colours"), dict) else {}
    accent_colour = _normalise_css_hex_colour(theme_colours.get("accent")) or "#ffea00"
    global_icon_rows = _render_global_icon_style_rows(accent_colour)
    amenities_rows = _render_amenities_field_body(amenities_title, amenity_rows, bool(config["amenities"]))
    map_regions = config.get("map_regions") if isinstance(config.get("map_regions"), list) else []
    map_region = config.get("map_region") or (map_regions[0] if map_regions else {})
    map_label_rows = "\n".join(_render_map_label_field(field) for field in config.get("map_label_fields", []))
    if map_regions:
        pages_label = ", ".join(f'Page {html.escape(str(region.get("page") or ""))}' for region in map_regions)
        map_page_label = pages_label
    else:
        map_page_label = "Not detected"
    map_label_count = sum(len(region.get("labels", [])) for region in map_regions) if map_regions else len(map_region.get("labels", []))
    return f"""
<aside class="exact-fields-panel" id="fieldsPanel" aria-label="Global brochure controls and structured fields" data-global-control-panel="true">
  <div class="exact-fields-head">
    <h2>Global Controls</h2>
    <button type="button" class="exact-fields-close" id="fieldsClose" aria-label="Close fields panel">x</button>
  </div>
  <div class="exact-global-summary" data-global-controls-summary>
    <span>Colours</span>
    <span>Typography</span>
    <span>Logo system</span>
    <span>Images</span>
    <span>Icons</span>
    <span>Agents</span>
  </div>
  <section class="exact-field-section" data-field-section="global-logo" data-global-control-section="logo">
    <h3>Logo System</h3>
    <div class="exact-field-row">
      <label for="fieldGlobalLogoKind">Logo</label>
      <select id="fieldGlobalLogoKind" data-global-logo-field="type">
        <option value="none">PDF source</option>
        <option value="grid">Grid</option>
        <option value="diamond">Diamond</option>
        <option value="facade">Facade</option>
        <option value="upload">Uploaded</option>
      </select>
    </div>
    <div class="exact-field-row">
      <label for="fieldGlobalLogoUpload">Upload</label>
      <div class="exact-logo-actions">
        <label for="fieldGlobalLogoUpload">Upload logo</label>
        <input id="fieldGlobalLogoUpload" type="file" accept="image/*" data-global-logo-field="upload" data-global-logo-upload="image">
        <button type="button" data-global-logo-reset="image" data-global-logo-clear="image">Reset</button>
      </div>
    </div>
    <div class="exact-field-row">
      <label for="fieldGlobalLogoSize">Size</label>
      <input id="fieldGlobalLogoSize" type="number" min="24" max="130" step="1" value="56" data-global-logo-field="size">
    </div>
    <div class="exact-field-row">
      <label for="fieldGlobalLogoPosition">Position</label>
      <select id="fieldGlobalLogoPosition" data-global-logo-field="position">
        <option value="source">Source positions</option>
        <option value="top-left">Top left</option>
        <option value="top-right">Top right</option>
        <option value="bottom-left">Bottom left</option>
        <option value="bottom-right">Bottom right</option>
      </select>
    </div>
    {source_logo_map}
  </section>
  <section class="exact-field-section" data-field-section="brand" data-global-control-section="cover">
    <h3>Cover Text</h3>
    <div class="exact-field-row">
      <label for="fieldCoverTitle">Title</label>
      <textarea id="fieldCoverTitle" data-exact-field="coverTitle" data-field-kind="cover-title" data-title-groups="{html.escape(json.dumps(cover_title.get("groups") or []), quote=True)}" data-targets="{_csv_attr(cover_title["targets"])}">{html.escape(cover_title["value"])}</textarea>
    </div>
    <div class="exact-field-row">
      <label for="fieldCoverOffer">Offer</label>
      <textarea id="fieldCoverOffer" data-exact-field="coverOffer" data-field-kind="html-lines" data-targets="{_csv_attr(cover_offer["targets"])}">{html.escape(cover_offer["value"])}</textarea>
    </div>
  </section>
  <section class="exact-field-section" data-field-section="typography" data-global-control-section="typography">
    <h3>Global Typography</h3>
    {typography_rows}
  </section>
  <section class="exact-field-section" data-field-section="amenities" data-icon-contract="data-amenity-icon-field" data-global-control-section="icons">
    <h3>Amenities & Icon Bank</h3>
    {global_icon_rows}
    {amenities_rows}
  </section>
  <section class="exact-field-section" data-field-section="service-icons" data-icon-contract="data-service-icon-field">
    <h3>Services & Customisation Icons</h3>
    {service_icon_rows or '<div class="exact-field-row"><label>Status</label><input value="No service icons detected" readonly></div>'}
  </section>
  <section class="exact-field-section" data-field-section="space-plans">
    <h3>Space Plans</h3>
    {space_plan_rows or '<div class="exact-field-row"><label>Status</label><input value="No source space plans detected" readonly></div>'}
  </section>
  <section class="exact-field-section" data-field-section="map" data-global-control-section="map">
    <h3>Map System</h3>
    <div class="exact-field-row">
      <label>Source</label>
      <input data-map-field="source" value="{map_page_label}" readonly>
    </div>
    <div class="exact-field-row">
      <label>Actions</label>
      <div class="exact-logo-actions">
        <button type="button" data-exact-map-preserve>Use PDF map</button>
        <button type="button" data-exact-map-generate-field>Regenerate</button>
      </div>
    </div>
    <div class="exact-field-row">
      <label>Labels</label>
      <input data-map-field="label-count" value="{map_label_count} recovered labels" readonly>
    </div>
    {map_label_rows}
  </section>
  <section class="exact-field-section" data-field-section="logos" data-global-control-section="agency-logos">
    <h3>Agency Logos</h3>
    {logo_rows}
  </section>
  <section class="exact-field-section" data-field-section="agents" data-global-control-section="agents">
    <h3>Agent Details</h3>
    {contact_rows}
  </section>
</aside>
"""


def _render_source_logo_map(source_logos: list[dict[str, Any]]) -> str:
    if not source_logos:
        return (
            '<div class="exact-field-row exact-source-logo-map">'
            '<label>Mapped marks</label><div class="exact-source-logo-list">None detected</div></div>'
        )
    rows = []
    for item in source_logos:
        rows.append(
            '<div class="exact-source-logo-map-row">'
            f'Page {html.escape(str(item.get("page") or ""))}: '
            f'{html.escape(str(item.get("label") or "facade mark"))} '
            f'({float(item.get("left") or 0):.0f}, {float(item.get("top") or 0):.0f}, '
            f'{float(item.get("width") or 0):.0f} x {float(item.get("height") or 0):.0f})'
            '</div>'
        )
    return (
        '<div class="exact-field-row exact-source-logo-map">'
        '<label>Mapped marks</label><div class="exact-source-logo-list">'
        + "".join(rows)
        + "</div></div>"
    )


def _render_global_icon_style_rows(accent_colour: str = "#ffea00") -> str:
    accent_colour = _normalise_css_hex_colour(accent_colour) or "#ffea00"
    return f"""
    <div class="exact-field-row exact-global-icon-field">
      <label for="fieldGlobalIconSize">Icon size</label>
      <input id="fieldGlobalIconSize" type="range" min="70" max="130" value="100" data-global-icon-field="size">
    </div>
    <div class="exact-field-row exact-global-icon-field">
      <label for="fieldGlobalIconStroke">Icon stroke</label>
      <input id="fieldGlobalIconStroke" type="range" min="1" max="6" step="0.25" value="3" data-global-icon-field="strokeWidth">
    </div>
    <div class="exact-field-row exact-global-icon-field">
      <label for="fieldGlobalIconColour">Icon colour</label>
      <input id="fieldGlobalIconColour" type="color" value="{accent_colour}" data-global-icon-field="color">
    </div>
    """


def _render_amenities_field_body(amenities_title: dict[str, Any], amenity_rows: str, has_amenities: bool) -> str:
    title_targets = amenities_title.get("targets") or []
    if not has_amenities and not title_targets:
        return (
            '<div class="exact-field-row exact-feature-status is-not-detected">'
            '<label>Status</label>'
            '<input value="No amenities grid detected in this PDF" readonly>'
            "</div>"
        )
    return (
        '<div class="exact-field-row">'
        '<label for="fieldAmenitiesTitle">Title</label>'
        f'<input id="fieldAmenitiesTitle" data-exact-field="amenitiesTitle" '
        f'data-field-kind="spaced-caps" data-targets="{_csv_attr(title_targets)}" '
        f'value="{html.escape(str(amenities_title.get("value") or ""), quote=True)}">'
        "</div>"
        + amenity_rows
    )


def _render_map_label_field(field: dict[str, Any]) -> str:
    key = html.escape(str(field.get("key") or ""), quote=True)
    label = html.escape(str(field.get("label") or "Map label"))
    value = html.escape(str(field.get("value") or ""), quote=True)
    targets = _csv_attr(field.get("targets") or [])
    map_save_id = html.escape(str(field.get("map_save_id") or ""), quote=True)
    return (
        '<div class="exact-field-row exact-map-label-row">'
        f'<label>{label}</label>'
        f'<input data-map-field="label" data-map-label-field="{key}" '
        f'data-map-save-id="{map_save_id}" '
        f'data-exact-field="{key}" data-field-kind="plain" data-targets="{targets}" value="{value}">'
        '</div>'
    )


def _render_amenity_field(field: dict[str, Any]) -> str:
    attrs = (
        f'data-exact-field="{html.escape(field["key"], quote=True)}" '
        f'data-field-kind="{html.escape(field["kind"], quote=True)}" '
        f'data-targets="{_csv_attr(field["targets"])}"'
    )
    if field.get("hide_targets"):
        attrs += f' data-hide-targets="{_csv_attr(field["hide_targets"])}"'
    label = html.escape(field["label"])
    value = html.escape(field["value"], quote=True)
    icon_select = (
        '<div class="exact-subrow">'
        '<span>Icon</span>'
        f'<select data-icon-field="{html.escape(field["key"], quote=True)}" '
        f'data-amenity-icon-field="{html.escape(field["key"], quote=True)}">'
        f'{_render_icon_options(str(field.get("icon_id") or "office"))}'
        '</select></div>'
    )
    if field["kind"] == "html-lines":
        return f'<div class="exact-field-row"><label>{label}</label><div><textarea {attrs}>{html.escape(field["value"])}</textarea>{icon_select}</div></div>'
    return f'<div class="exact-field-row"><label>{label}</label><div><input {attrs} value="{value}">{icon_select}</div></div>'


def _render_service_icon_field(field: dict[str, Any]) -> str:
    key = html.escape(str(field.get("key") or ""), quote=True)
    label = html.escape(str(field.get("label") or "Service icon"))
    group = html.escape(str(field.get("group") or "Services"), quote=True)
    icon_id = str(field.get("icon_id") or field.get("defaultIcon") or "office")
    return (
        '<div class="exact-field-row exact-service-icon-field">'
        f'<label>{label}</label>'
        '<div>'
        f'<select data-icon-field="{key}" data-service-icon-field="{key}" data-service-group="{group}">'
        f'{_render_icon_options(icon_id)}'
        '</select>'
        f'<div class="exact-subrow"><span>Group</span><input value="{group}" readonly></div>'
        '</div></div>'
    )


def _render_space_plan_field(field: dict[str, Any]) -> str:
    key = html.escape(str(field.get("key") or ""), quote=True)
    label = html.escape(str(field.get("label") or "Space plan"), quote=True)
    page = html.escape(str(field.get("page") or ""), quote=True)
    upload_id = f"spacePlanUpload-{key}"
    return (
        '<div class="exact-field-row exact-space-plan-field">'
        f'<label>{label}</label>'
        '<div>'
        f'<input data-space-plan-field="{key}" data-space-plan-region="{key}" value="Page {page} mapped" readonly>'
        '<div class="exact-subrow exact-logo-actions">'
        '<span>Replace</span>'
        f'<label for="{upload_id}">Upload plan</label>'
        f'<input id="{upload_id}" type="file" accept="image/*" data-space-plan-upload-field="{key}">'
        f'<button type="button" data-space-plan-reset-field="{key}">Reset</button>'
        '</div>'
        '</div></div>'
    )


def _render_typography_fields(config: dict[str, Any]) -> str:
    roles = config.get("roles") if isinstance(config, dict) else {}
    fonts = config.get("fonts") if isinstance(config, dict) else []
    if not isinstance(roles, dict):
        roles = {}
    if not isinstance(fonts, list):
        fonts = []
    rows: list[str] = []
    for role, label in TYPOGRAPHY_ROLE_LABELS.items():
        role_config = roles.get(role) if isinstance(roles.get(role), dict) else {}
        selected = str(role_config.get("fontFamily") or "")
        rows.append(
            '<div class="exact-field-row exact-typography-field">'
            f'<label>{html.escape(label)}</label>'
            '<div>'
            f'<select data-typography-role-field="{html.escape(role, quote=True)}" '
            f'data-css-var="{html.escape(TYPOGRAPHY_ROLE_VARS[role], quote=True)}">'
            f'{_render_font_options(fonts, selected)}'
            '</select>'
            f'<div class="exact-subrow"><span>Source</span><input value="{html.escape(str(role_config.get("sourceFontFamily") or selected), quote=True)}" readonly></div>'
            '</div></div>'
        )
    return "\n".join(rows) or '<div class="exact-field-row"><label>Status</label><input value="No extracted fonts detected" readonly></div>'


def _render_font_options(fonts: list[Any], selected: str) -> str:
    if not fonts:
        return '<option value="Arial">Arial</option>'
    options: list[str] = []
    for item in fonts:
        if not isinstance(item, dict):
            continue
        family = str(item.get("family") or "")
        if not family:
            continue
        label = str(item.get("label") or family)
        options.append(
            f'<option value="{html.escape(family, quote=True)}"{" selected" if family == selected else ""}>{html.escape(label)}</option>'
        )
    return "".join(options)


def _render_icon_options(selected_icon: str) -> str:
    icon_options = [
        ("office", "Office"),
        ("plug", "Plug"),
        ("gym", "Gym"),
        ("lift", "Lift"),
        ("shower", "Shower"),
        ("bicycle", "Bike"),
        ("coffee", "Kitchenette"),
        ("lightning", "Air / Services"),
        ("desk", "Trunking"),
        ("warehouse", "Facade"),
        ("meeting", "Meeting"),
        ("wifi", "Fibre / WiFi"),
        ("broom", "Cleaning"),
        ("gear", "Maintenance"),
        ("health", "Health"),
        ("apple", "Snacks"),
        ("leaf", "Foliage"),
        ("reception", "Reception"),
        ("rates", "Document"),
        ("security", "Security"),
        ("train", "Transport"),
    ]
    return "".join(
        f'<option value="{html.escape(value, quote=True)}"{" selected" if value == selected_icon else ""}>{html.escape(label)}</option>'
        for value, label in icon_options
    )


def _render_logo_field(key: str, field: dict[str, Any]) -> str:
    safe_key = html.escape(key, quote=True)
    html_id = "fieldLogo" + re.sub(r"[^A-Za-z0-9]+", "", key).title()
    upload_id = "uploadLogo" + re.sub(r"[^A-Za-z0-9]+", "", key).title()
    return (
        '<div class="exact-field-row">'
        f'<label for="{html.escape(html_id, quote=True)}">{html.escape(str(field.get("label") or key).upper())}</label>'
        '<div>'
        f'<input id="{html.escape(html_id, quote=True)}" data-logo-field="{safe_key}" '
        f'data-logo-prop="text" value="{html.escape(str(field.get("defaultText") or ""), quote=True)}">'
        '<div class="exact-logo-actions">'
        f'<label for="{html.escape(upload_id, quote=True)}">Upload</label>'
        f'<input id="{html.escape(upload_id, quote=True)}" type="file" accept="image/*" data-logo-upload="{safe_key}">'
        f'<button type="button" data-logo-clear="{safe_key}">Reset</button>'
        '</div></div></div>'
    )


def _render_contact_field(field: dict[str, Any]) -> str:
    default = f'{field["name"]}|||{field["phone"]}|||{field["email"]}'
    attrs = (
        f'data-contact-field="{html.escape(field["key"], quote=True)}" '
        f'data-contact-default="{html.escape(default, quote=True)}" '
        f'data-targets="{_csv_attr(field["targets"])}"'
    )
    if field.get("hide_targets"):
        attrs += f' data-hide-targets="{_csv_attr(field["hide_targets"])}"'
    return (
        f'<div class="exact-field-row"><label>{html.escape(field["label"])}</label>'
        f'<textarea {attrs}>{html.escape(field["value"])}</textarea></div>'
    )


def _csv_attr(values: list[str]) -> str:
    return html.escape(",".join(values), quote=True)


def _exact_editor_js() -> str:
    return r"""
(function () {
	  var projectId = window.__PROJECT_ID__;
	  var saveState = document.getElementById('saveState');
	  var root = document.documentElement;
	  var EXACT_THEME = window.__EXACT_THEME__ || {};
	  var DEFAULT_ACCENT = String(EXACT_THEME.accent || '#ffea00').toLowerCase();
	  var DEFAULT_DARK = String(EXACT_THEME.dark || '#333132').toLowerCase();
	  var debounceTimer = null;
  var recolourTimer = null;
  var lastHash = '';
  var uploadedLogoDataUrl = '';
  var agencyLogoUploads = {};
  var LOGOS = {
    none: '',
    grid: '<svg viewBox="0 0 44 54" fill="none">' +
      Array.from({ length: 20 }).map(function (_, i) {
        var x = 2 + (i % 4) * 10;
        var y = 2 + Math.floor(i / 4) * 10;
        return '<rect x="' + x + '" y="' + y + '" width="7" height="7" stroke="currentColor" stroke-width="1.3"/>';
      }).join('') + '</svg>',
    diamond: '<svg viewBox="0 0 36 36" fill="none"><path d="M18 2L34 18L18 34L2 18Z" stroke="currentColor" stroke-width="1.6"/><path d="M18 9L27 18L18 27L9 18Z" stroke="currentColor" stroke-width="1.2"/></svg>',
    facade: '<svg viewBox="0 0 52 84" fill="none"><path d="M8 82V22h36v60M5 22h42M10 12h32M16 12V7c0-4 5-4 5 0v5M31 12V7c0-4 5-4 5 0v5M14 30h8v14h-8V30ZM30 30h8v14h-8V30ZM14 52h8v18h-8V52ZM30 52h8v18h-8V52ZM21 82V70h10v12" stroke="currentColor" stroke-width="1.2"/></svg>'
  };
	  var AGENCY_LOGO_DEFS = window.__EXACT_AGENCY_LOGOS__ || {};
	  var COLOUR_PRESETS = {
	    extracted: { accent: DEFAULT_ACCENT, dark: DEFAULT_DARK },
	    custom: null,
	    'yellow-dark': { accent: '#ffea00', dark: '#333132' },
    blue: { accent: '#00aaff', dark: '#1b2024' },
    copper: { accent: '#c8753d', dark: '#252321' },
    forest: { accent: '#7bbf7a', dark: '#202720' }
  };
  var CONTACT_DEFS = window.__EXACT_CONTACT_DEFS__ || {};
  var AMENITY_ICON_DEFS = window.__EXACT_AMENITY_ICONS__ || [];
  var MAP_REGION = window.__EXACT_MAP_REGION__ || {};
  var MAP_REGIONS = window.__EXACT_MAP_REGIONS__ || (MAP_REGION && MAP_REGION.save_id ? [MAP_REGION] : []);
  var SOURCE_LOGO_DEFS = window.__EXACT_SOURCE_LOGOS__ || [];
  var TYPOGRAPHY_CONFIG = window.__EXACT_TYPOGRAPHY__ || { roles: {}, fonts: [] };
  var activeTypographySettings = {};
  var activeGlobalIconStyle = {};
  var iconObserver = null;

  function setStatus(text) {
    if (saveState) saveState.textContent = text;
  }

  function updateScale() {
    if (document.body.classList.contains('export-clean')) {
      root.style.setProperty('--exact-scale', '1');
      return;
    }
    var range = document.getElementById('zoomRange');
    var requested = range ? Number(range.value) / 100 : 0.72;
    var maxWidth = Math.max(320, window.innerWidth - 44);
    var pageWidth = parseFloat(getComputedStyle(root).getPropertyValue('--exact-page-w')) || 1587;
    var fitScale = Math.min(1, maxWidth / pageWidth);
    root.style.setProperty('--exact-scale', Math.min(requested, fitScale).toFixed(4));
  }

	  function applyColours(accent, dark) {
	    accent = accent || document.getElementById('accentColour').value || DEFAULT_ACCENT;
	    dark = dark || document.getElementById('darkColour').value || DEFAULT_DARK;
    root.style.setProperty('--exact-accent', accent);
    root.style.setProperty('--exact-dark', dark);
    document.querySelectorAll('[data-colour-role="accent"]').forEach(function (el) { el.style.color = accent; });
    document.querySelectorAll('[data-colour-role="dark"]').forEach(function (el) { el.style.color = dark; });
    document.querySelectorAll('.exact-logo').forEach(function (el) { el.style.color = accent; });
    applyGlobalIconStyle(activeGlobalIconStyle);
    scheduleBackgroundRecolour(accent, dark);
  }

  function setColourPreset(name) {
    var preset = COLOUR_PRESETS[name || 'custom'];
    if (!preset) {
      var customSelect = document.getElementById('colourPreset');
      if (customSelect) customSelect.value = 'custom';
      return;
    }
    document.getElementById('accentColour').value = preset.accent;
    document.getElementById('darkColour').value = preset.dark;
    document.getElementById('colourPreset').value = name;
    applyColours(preset.accent, preset.dark);
  }

  function scheduleBackgroundRecolour(accent, dark) {
    if (recolourTimer) clearTimeout(recolourTimer);
    recolourTimer = setTimeout(function () {
      recolourBackgrounds(accent, dark);
    }, 120);
  }

  function recolourBackgrounds(accentHex, darkHex) {
	    var accent = hexToRgb(accentHex);
	    var dark = hexToRgb(darkHex);
	    var isDefault = normaliseHex(accentHex) === normaliseHex(DEFAULT_ACCENT) && normaliseHex(darkHex) === normaliseHex(DEFAULT_DARK);
    var key = normaliseHex(accentHex) + '|' + normaliseHex(darkHex);

    document.querySelectorAll('.pdf-bg').forEach(function (img) {
      if (!img.dataset.originalSrc) img.dataset.originalSrc = img.getAttribute('src') || '';
      var page = img.closest && img.closest('.exact-page');
      if (page && page.dataset.sourcePreservedEdit === 'true') {
        if (img.dataset.originalSrc && img.getAttribute('src') !== img.dataset.originalSrc) {
          img.setAttribute('src', img.dataset.originalSrc);
        }
        img.dataset.recolourKey = '';
        return;
      }
      if (isDefault) {
        if (img.dataset.originalSrc && img.getAttribute('src') !== img.dataset.originalSrc) {
          img.setAttribute('src', img.dataset.originalSrc);
        }
        img.dataset.recolourKey = '';
        return;
      }
      if (!accent || !dark || img.dataset.recolourKey === key) return;

      var source = img.dataset.originalSrc;
      if (!source) return;
      var work = new Image();
      work.onload = function () {
        var canvas = document.createElement('canvas');
        canvas.width = work.naturalWidth || work.width;
        canvas.height = work.naturalHeight || work.height;
        var context = canvas.getContext('2d');
        if (!context) return;
        context.drawImage(work, 0, 0);
        var imageData = context.getImageData(0, 0, canvas.width, canvas.height);
        var data = imageData.data;
        for (var i = 0; i < data.length; i += 4) {
          var r = data[i];
          var g = data[i + 1];
          var b = data[i + 2];
          if (isAccentPixel(r, g, b)) {
            data[i] = accent.r;
            data[i + 1] = accent.g;
            data[i + 2] = accent.b;
          } else if (isDarkInkPixel(r, g, b)) {
            data[i] = dark.r;
            data[i + 1] = dark.g;
            data[i + 2] = dark.b;
          }
        }
        context.putImageData(imageData, 0, 0);
        img.setAttribute('src', canvas.toDataURL('image/png'));
        img.dataset.recolourKey = key;
      };
      work.src = source;
    });
  }

  function isAccentPixel(r, g, b) {
    return r > 145 && g > 125 && b < 90 && Math.abs(r - g) < 95;
  }

  function isDarkInkPixel(r, g, b) {
    var max = Math.max(r, g, b);
    var min = Math.min(r, g, b);
    return max < 72 && min > 18 && (max - min) < 24;
  }

  function normaliseHex(value) {
    value = String(value || '').trim().toLowerCase();
    if (/^#[0-9a-f]{6}$/.test(value)) return value;
    return '';
  }

  function hexToRgb(value) {
    var hex = normaliseHex(value);
    if (!hex) return null;
    return {
      r: parseInt(hex.slice(1, 3), 16),
      g: parseInt(hex.slice(3, 5), 16),
      b: parseInt(hex.slice(5, 7), 16)
    };
  }

  function ensureLogos() {
    document.querySelectorAll('.exact-page').forEach(function (page) {
      if (page.querySelector('.exact-logo')) return;
      var logo = document.createElement('div');
      logo.className = 'exact-logo logo-zone';
      if (page.dataset.pageNum === '1') {
        logo.classList.add('cover-logo');
        logo.dataset.logoSize = 'large';
        logo.dataset.defaultLeft = '';
        logo.dataset.defaultRight = '64px';
        logo.dataset.defaultTop = '52px';
        logo.dataset.defaultBottom = '';
      } else {
        logo.dataset.logoSize = 'small';
        logo.dataset.defaultLeft = '42px';
        logo.dataset.defaultRight = '';
        logo.dataset.defaultTop = '54px';
        logo.dataset.defaultBottom = '';
      }
      page.appendChild(logo);
    });
  }

  function hasSourceLogoSlots() {
    return document.querySelectorAll('[data-source-logo-slot]').length > 0;
  }

  function sourceLogoMarkup(kind) {
    if (kind === 'upload' && uploadedLogoDataUrl) {
      return '<img src="' + uploadedLogoDataUrl + '" alt="">';
    }
    return LOGOS[kind] || '';
  }

  function sourceLogoDefaultMarkup(slot) {
    var asset = slot && slot.dataset ? (slot.dataset.defaultSourceLogoAsset || '') : '';
    if (!asset) return '';
    return '<div class="exact-source-logo-default"><img src="' + escapeHtml(asset) + '" alt=""></div>';
  }

  function resetSourceLogoSlot(slot) {
    if (!slot) return;
    slot.classList.remove('is-active');
    slot.dataset.logoActive = 'false';
    delete slot.dataset.sourceLogoMaskMode;
    slot.innerHTML = sourceLogoDefaultMarkup(slot);
    setSourceVectorVisibilityForSlot(slot, 'source-logo:' + slot.dataset.sourceLogoSlot, false, 0.08);
  }

  function renderSourceLogoSlot(slot, kind, scale) {
    var markup = sourceLogoMarkup(kind);
    if (!slot || !markup || kind === 'none') {
      resetSourceLogoSlot(slot);
      return;
    }
    slot.classList.add('is-active');
    slot.dataset.logoActive = 'true';
    var safeScale = Math.max(0.35, Math.min(2.35, Number(scale) || 1));
    slot.innerHTML = '<div class="exact-source-logo-mask"></div><div class="exact-source-logo-art" style="transform:scale(' + safeScale.toFixed(3) + ')">' + markup + '</div>';
    var hiddenVectors = setSourceVectorVisibilityForSlot(slot, 'source-logo:' + slot.dataset.sourceLogoSlot, true, 0.08);
    slot.dataset.sourceLogoMaskMode = hiddenVectors > 0 ? 'vector' : (slot.dataset.defaultSourceLogoMaskMode || 'sample');
  }

  function applySourceLogo(kind) {
    var size = Number(document.getElementById('logoSize').value || 56);
    var sourceScale = size / 56;
    document.querySelectorAll('[data-source-logo-slot]').forEach(function (slot) {
      renderSourceLogoSlot(slot, kind, sourceScale);
    });
    document.querySelectorAll('.exact-logo').forEach(function (logo) {
      logo.innerHTML = '';
      logo.classList.remove('is-active');
    });
    document.querySelectorAll('.exact-page').forEach(function (page) {
      page.classList.toggle('show-logo', Boolean(kind && kind !== 'none'));
    });
  }

  function applyLogo(kind) {
    if (hasSourceLogoSlots()) {
      applySourceLogo(kind);
      return;
    }
    ensureLogos();
    var size = Number(document.getElementById('logoSize').value || 56);
    var position = document.getElementById('logoPosition').value || 'source';
    document.querySelectorAll('.exact-page').forEach(function (page) {
      var logo = page.querySelector('.exact-logo');
      if (!logo) return;
      logo.innerHTML = kind === 'upload' && uploadedLogoDataUrl
        ? '<img src="' + uploadedLogoDataUrl + '" alt="">'
        : (LOGOS[kind] || '');
      positionLogo(logo, page, size, position);
      page.classList.toggle('show-logo', kind && kind !== 'none');
    });
  }

  function positionLogo(logo, page, size, position) {
    var smallPageLogo = position === 'source' && page.dataset.pageNum !== '1';
    var effectiveSize = smallPageLogo ? Math.round(size * 0.72) : size;
    logo.style.width = effectiveSize + 'px';
    logo.style.height = Math.round(effectiveSize * 1.22) + 'px';
    logo.style.left = '';
    logo.style.right = '';
    logo.style.top = '';
    logo.style.bottom = '';

    if (position === 'source') {
      if (logo.dataset.defaultLeft) logo.style.left = logo.dataset.defaultLeft;
      if (logo.dataset.defaultRight) logo.style.right = logo.dataset.defaultRight;
      if (logo.dataset.defaultTop) logo.style.top = logo.dataset.defaultTop;
      if (logo.dataset.defaultBottom) logo.style.bottom = logo.dataset.defaultBottom;
      return;
    }

    if (position.indexOf('top') === 0) logo.style.top = '42px';
    if (position.indexOf('bottom') === 0) logo.style.bottom = '42px';
    if (position.indexOf('left') > -1) logo.style.left = '42px';
    if (position.indexOf('right') > -1) logo.style.right = '42px';
  }

  function collectGlobalLogoState() {
    return {
      type: document.getElementById('logoSelect').value,
      uploadedLogoDataUrl: uploadedLogoDataUrl || null,
      coverSize: Number(document.getElementById('logoSize').value || 56),
      coverPosition: document.getElementById('logoPosition').value || 'source'
    };
  }

  function syncGlobalLogoControls() {
    var kind = document.getElementById('fieldGlobalLogoKind');
    var size = document.getElementById('fieldGlobalLogoSize');
    var position = document.getElementById('fieldGlobalLogoPosition');
    if (kind) kind.value = document.getElementById('logoSelect').value;
    if (size) size.value = document.getElementById('logoSize').value;
    if (position) position.value = document.getElementById('logoPosition').value;
  }

  function applyGlobalLogoState(logoState) {
    if (!logoState || typeof logoState !== 'object') return;
    if (logoState.uploadedLogoDataUrl) uploadedLogoDataUrl = logoState.uploadedLogoDataUrl;
    if (logoState.coverSize != null) document.getElementById('logoSize').value = logoState.coverSize;
    if (logoState.coverPosition) document.getElementById('logoPosition').value = logoState.coverPosition;
    if (logoState.type) document.getElementById('logoSelect').value = logoState.type;
    applyLogo(document.getElementById('logoSelect').value || 'none');
    syncGlobalLogoControls();
  }

  function setupGlobalLogoFields() {
    syncGlobalLogoControls();
    var kind = document.getElementById('fieldGlobalLogoKind');
    var size = document.getElementById('fieldGlobalLogoSize');
    var position = document.getElementById('fieldGlobalLogoPosition');
    var upload = document.getElementById('fieldGlobalLogoUpload');
    var reset = document.querySelector('[data-global-logo-reset]');
    if (kind) {
      kind.addEventListener('change', function () {
        document.getElementById('logoSelect').value = kind.value;
        applyLogo(kind.value);
        requestSave();
      });
    }
    if (size) {
      size.addEventListener('input', function () {
        document.getElementById('logoSize').value = size.value;
        applyLogo(document.getElementById('logoSelect').value);
        requestSave();
      });
    }
    if (position) {
      position.addEventListener('change', function () {
        document.getElementById('logoPosition').value = position.value;
        applyLogo(document.getElementById('logoSelect').value);
        requestSave();
      });
    }
    if (upload) {
      upload.addEventListener('change', function (event) {
        var file = event.target.files && event.target.files[0];
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function (readerEvent) {
          uploadedLogoDataUrl = readerEvent.target.result;
          document.getElementById('logoSelect').value = 'upload';
          if (kind) kind.value = 'upload';
          applyLogo('upload');
          requestSave();
        };
        reader.readAsDataURL(file);
      });
    }
    if (reset) {
      reset.addEventListener('click', function () {
        uploadedLogoDataUrl = '';
        document.getElementById('logoSelect').value = 'none';
        applyLogo('none');
        syncGlobalLogoControls();
        requestSave();
      });
    }
  }

  window.getBrochureLogoState = collectGlobalLogoState;
  window.applyBrochureLogoState = applyGlobalLogoState;
  window.getGlobalLogoState = collectGlobalLogoState;
  window.applyGlobalLogoState = applyGlobalLogoState;

  function applyLayoutMode(mode) {
    mode = mode || 'editable';
    document.querySelectorAll('.exact-page').forEach(function (page) {
      page.dataset.pictureLayout = mode;
    });
    document.querySelectorAll('[data-layout-mode]').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.dataset.layoutMode === mode);
    });
  }

  function slotPhoto(slot) {
    return slot.querySelector('.slot-photo');
  }

  function imageValueToUrl(imageValue) {
    var value = String(imageValue || '').trim();
    var match = value.match(/^url\((['"]?)(.*?)\1\)$/);
    return match ? match[2] : value;
  }

  function setSlotImage(slot, imageValue) {
    var photo = slotPhoto(slot);
    if (!photo || !imageValue) return;
    var imageUrl = imageValueToUrl(imageValue);
    if (imageValue.indexOf('url(') === 0) {
      photo.style.backgroundImage = imageValue;
    } else {
      photo.style.backgroundImage = 'url("' + imageValue + '")';
    }
    var img = photo.querySelector('.slot-photo-img');
    if (!img) {
      img = document.createElement('img');
      img.className = 'slot-photo-img';
      img.alt = '';
      photo.appendChild(img);
    }
    img.src = imageUrl;
    slot.classList.add('has-image');
    if (slot.dataset.sourcePlanSlot) {
      slot.dataset.sourcePlanActive = 'true';
      var mask = document.querySelector('[data-mask-for="' + slot.dataset.saveId + '"]');
      if (mask) mask.classList.add('has-image');
      setSourceVectorVisibilityForSlot(slot, 'space-plan:' + slot.dataset.sourcePlanSlot, true, 0.05);
    }
  }

  function resetSlotPosition(slot) {
    slot.style.left = slot.dataset.originalLeft + 'px';
    slot.style.top = slot.dataset.originalTop + 'px';
    slot.style.width = slot.dataset.originalWidth + 'px';
    slot.style.height = slot.dataset.originalHeight + 'px';
  }

  function setSlotFit(slot, fit) {
    slot.dataset.fit = fit || 'cover';
    updateFitButtons(slot);
  }

  function slotsForPage(page) {
    return Array.from(page.querySelectorAll('.exact-image-slot:not([data-source-plan-slot])'));
  }

  function applyPictureLayout(kind) {
    kind = kind || 'original';
    var select = document.getElementById('pictureLayout');
    if (select) select.value = kind;
    document.querySelectorAll('.exact-page').forEach(function (page) {
      page.dataset.imageLayout = kind;
      var slots = slotsForPage(page);
      slots.forEach(function (slot) {
        resetSlotPosition(slot);
        setSlotFit(slot, kind === 'fit' ? 'contain' : (kind === 'left-crop' ? 'cover-left' : 'cover'));
      });
      if (kind === 'grid') arrangeGrid(page, slots);
      if (kind === 'hero-stack') arrangeHeroStack(page, slots);
    });
  }

  function arrangeGrid(page, slots) {
    if (!slots.length) return;
    var bounds = slotBounds(slots);
    var columns = Math.ceil(Math.sqrt(slots.length));
    var rows = Math.ceil(slots.length / columns);
    var gap = 18;
    var cellW = (bounds.width - gap * (columns - 1)) / columns;
    var cellH = (bounds.height - gap * (rows - 1)) / rows;
    slots.forEach(function (slot, index) {
      var column = index % columns;
      var row = Math.floor(index / columns);
      slot.style.left = (bounds.left + column * (cellW + gap)) + 'px';
      slot.style.top = (bounds.top + row * (cellH + gap)) + 'px';
      slot.style.width = cellW + 'px';
      slot.style.height = cellH + 'px';
      setSlotFit(slot, 'cover');
    });
  }

  function arrangeHeroStack(page, slots) {
    if (slots.length < 2) return;
    var bounds = slotBounds(slots);
    var gap = 18;
    var heroW = Math.max(bounds.width * 0.58, bounds.width - 260);
    var stackW = bounds.width - heroW - gap;
    slots[0].style.left = bounds.left + 'px';
    slots[0].style.top = bounds.top + 'px';
    slots[0].style.width = heroW + 'px';
    slots[0].style.height = bounds.height + 'px';
    setSlotFit(slots[0], 'cover');
    var stackH = (bounds.height - gap * (slots.length - 2)) / (slots.length - 1);
    slots.slice(1).forEach(function (slot, index) {
      slot.style.left = (bounds.left + heroW + gap) + 'px';
      slot.style.top = (bounds.top + index * (stackH + gap)) + 'px';
      slot.style.width = stackW + 'px';
      slot.style.height = stackH + 'px';
      setSlotFit(slot, 'cover');
    });
  }

  function slotBounds(slots) {
    var left = Math.min.apply(null, slots.map(function (slot) { return Number(slot.dataset.originalLeft); }));
    var top = Math.min.apply(null, slots.map(function (slot) { return Number(slot.dataset.originalTop); }));
    var right = Math.max.apply(null, slots.map(function (slot) { return Number(slot.dataset.originalLeft) + Number(slot.dataset.originalWidth); }));
    var bottom = Math.max.apply(null, slots.map(function (slot) { return Number(slot.dataset.originalTop) + Number(slot.dataset.originalHeight); }));
    return { left: left, top: top, width: right - left, height: bottom - top };
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function normaliseText(value) {
    return String(value == null ? '' : value)
      .replace(/\u00a0/g, ' ')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .trim();
  }

  function normalisePlainText(value) {
    return normaliseText(value).replace(/\s+/g, ' ');
  }

  function plainTextFromHtml(value) {
    var container = document.createElement('div');
    container.innerHTML = String(value == null ? '' : value);
    container.querySelectorAll('br').forEach(function (br) {
      br.replaceWith(' ');
    });
    return normalisePlainText(container.textContent || '');
  }

  function shouldApplySavedTextState(el, item) {
    if (!el || !item || typeof item !== 'object' || typeof item.html !== 'string') return false;
    if (item.edited === true) return true;
    if (item.edited !== false) return true;
    var savedPlain = plainTextFromHtml(item.html);
    var currentPlain = normalisePlainText(el.getAttribute('data-plain-text') || '');
    var originalPlain = plainTextFromHtml(el.getAttribute('data-original-html') || '');
    return !(savedPlain && currentPlain && savedPlain !== currentPlain && (!originalPlain || originalPlain === currentPlain));
  }

  function normaliseEditedTextMarkup(el) {
    if (!el) return;
    el.querySelectorAll('[style]').forEach(function (child) {
      child.style.fontFamily = '';
      child.style.fontSize = '';
      child.style.fontWeight = '';
      child.style.fontStyle = '';
      child.style.lineHeight = '';
      child.style.color = '';
      child.style.letterSpacing = '';
      if (!child.getAttribute('style')) child.removeAttribute('style');
    });
    el.querySelectorAll('font').forEach(function (fontEl) {
      unwrapNode(fontEl);
    });
    applyTextTypography(el);
  }

  function unwrapNode(node) {
    if (!node || !node.parentNode) return;
    while (node.firstChild) node.parentNode.insertBefore(node.firstChild, node);
    node.parentNode.removeChild(node);
  }

  function textFontStack(family) {
    if (!family) return '';
    return '"' + String(family).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '", Arial, sans-serif';
  }

  function roleConfig(role) {
    return (TYPOGRAPHY_CONFIG.roles && TYPOGRAPHY_CONFIG.roles[role]) || {};
  }

  function roleCssVar(role) {
    return roleConfig(role).cssVar || ({
      'cover-title': '--exact-font-cover-title',
      'section-heading': '--exact-font-section-heading',
      body: '--exact-font-body',
      caption: '--exact-font-caption',
      'table-status': '--exact-font-table-status',
      'agent-contact': '--exact-font-agent-contact'
    })[role] || '';
  }

  function applyTextTypography(el) {
    if (!el) return;
    var role = el.dataset.typographyRole || 'body';
    var configured = activeTypographySettings[role] || {};
    var defaults = roleConfig(role);
    var changed = Boolean(configured.changed || (configured.fontFamily && configured.fontFamily !== defaults.fontFamily));
    if (!changed) {
      el.style.fontFamily = '';
      return;
    }
    var cssVar = roleCssVar(role);
    if (cssVar) {
      var fallback = el.dataset.fontAlias || el.dataset.fontFamily || '';
      el.style.fontFamily = 'var(' + cssVar + (fallback ? ', ' + textFontStack(fallback) : ', inherit') + ')';
    }
  }

  function collectTypographySettings() {
    var settings = {};
    document.querySelectorAll('[data-typography-role-field]').forEach(function (select) {
      var defaults = roleConfig(select.dataset.typographyRoleField);
      settings[select.dataset.typographyRoleField] = {
        fontFamily: select.value,
        cssVar: select.dataset.cssVar || roleCssVar(select.dataset.typographyRoleField),
        defaultFontFamily: defaults.fontFamily || '',
        changed: select.value !== (defaults.fontFamily || '')
      };
    });
    return settings;
  }

  function applyTypographySettings(settings) {
    var roleSettings = settings || {};
    activeTypographySettings = roleSettings;
    Object.keys((TYPOGRAPHY_CONFIG.roles || {})).forEach(function (role) {
      var configured = roleSettings[role] || {};
      var roleDefaults = roleConfig(role);
      var family = configured.fontFamily || roleDefaults.fontFamily || '';
      var cssVar = configured.cssVar || roleDefaults.cssVar || roleCssVar(role);
      if (cssVar && family) root.style.setProperty(cssVar, textFontStack(family));
      var control = document.querySelector('[data-typography-role-field="' + role + '"]');
      if (control && family) control.value = family;
    });
    document.querySelectorAll('.pdf-text').forEach(applyTextTypography);
  }

  function collectTextTypography(el) {
    var computed = getComputedStyle(el);
    return {
      role: el.dataset.typographyRole || 'body',
      fontAlias: el.dataset.fontAlias || el.dataset.fontFamily || '',
      sourceFontFamily: el.dataset.sourceFontFamily || '',
      computedFontFamily: computed.fontFamily,
      fontSize: el.dataset.fontSize || computed.fontSize,
      lineHeight: el.dataset.lineHeight || computed.lineHeight,
      letterSpacing: el.dataset.letterSpacing || computed.letterSpacing,
      fontWeight: el.dataset.fontWeight || computed.fontWeight,
      fontStyle: el.dataset.fontStyle || computed.fontStyle,
      colourRole: el.dataset.colourRole || ''
    };
  }

  function collectTextLayout(el) {
    var layout = {};
    var styles = {};
    [
      ['left', 'left'],
      ['top', 'top'],
      ['width', 'width'],
      ['height', 'height'],
      ['white-space', 'whiteSpace'],
      ['display', 'display'],
      ['align-items', 'alignItems'],
      ['justify-content', 'justifyContent'],
      ['text-align', 'textAlign']
    ].forEach(function (pair) {
      var value = el.style[pair[1]];
      if (value) styles[pair[0]] = value;
    });
    if (Object.keys(styles).length) layout.styles = styles;
    if (el.dataset.titleStack === 'true') layout.titleStack = true;
    if (el.classList.contains('exact-field-hidden')) layout.hidden = true;
    return Object.keys(layout).length ? layout : null;
  }

  function applyTextLayout(el, layout) {
    if (!el || !layout || typeof layout !== 'object') return;
    var styles = layout.styles || {};
    Object.keys(styles).forEach(function (key) {
      if (typeof styles[key] === 'string') el.style.setProperty(key, styles[key]);
    });
    if (layout.titleStack) el.dataset.titleStack = 'true';
    if (layout.hidden) el.classList.add('exact-field-hidden');
  }

  function insertPlainTextAtSelection(text) {
    var selection = window.getSelection();
    if (!selection || !selection.rangeCount) {
      document.execCommand('insertText', false, text);
      return;
    }
    selection.deleteFromDocument();
    var range = selection.getRangeAt(0);
    var node = document.createTextNode(text);
    range.insertNode(node);
    range.setStartAfter(node);
    range.setEndAfter(node);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function targetElements(value) {
    return String(value || '')
      .split(',')
      .map(function (id) { return id.trim(); })
      .filter(Boolean)
      .map(function (id) { return document.querySelector('[data-save-id="' + id + '"]'); })
      .filter(Boolean);
  }

  function setEditableHtml(el, htmlValue, options) {
    if (!el) return;
    el.innerHTML = htmlValue;
    normaliseEditedTextMarkup(el);
    el.dataset.edited = 'true';
    if (!options || !options.preserveNoWrap) {
      fitStructuredTextBox(el, htmlValue);
    }
  }

  function fitStructuredTextBox(el, htmlValue) {
    el.dataset.structuredField = 'true';
    if (!el.dataset.originalBoxWidth) {
      var scale = parseFloat(getComputedStyle(root).getPropertyValue('--exact-scale')) || 1;
      el.dataset.originalBoxWidth = Math.ceil((el.getBoundingClientRect().width || 0) / scale);
    }
    var textLength = (el.textContent || '').trim().length;
    if (htmlValue.indexOf('<br') === -1 && textLength <= 24) return;
    var baseWidth = Number(el.dataset.originalBoxWidth || 0);
    var width = Math.max(baseWidth, textLength > 64 ? 260 : 175);
    el.style.width = width + 'px';
    el.style.whiteSpace = 'normal';
  }

  function spacedCaps(value) {
    return escapeHtml(normaliseText(value).toUpperCase());
  }

  function fieldHtml(kind, value) {
    if (kind === 'spaced-caps') return spacedCaps(value);
    return escapeHtml(normaliseText(value)).replace(/\n/g, '<br/>');
  }

  function parseTitleGroups(input) {
    if (!input || !input.dataset.titleGroups) return [];
    try {
      var groups = JSON.parse(input.dataset.titleGroups || '[]');
      return Array.isArray(groups) ? groups : [];
    } catch (err) {
      return [];
    }
  }

  function splitTextSegments(value) {
    return Array.from(String(value == null ? '' : value));
  }

  function numberOrNull(value) {
    var numeric = Number(value);
    return isFinite(numeric) ? numeric : null;
  }

  function titleStackHtml(line) {
    var characters = splitTextSegments(line).filter(function (ch) { return ch !== '\r'; });
    if (!characters.length) characters = [''];
    return '<span class="exact-title-stack">' + characters.map(function (ch) {
      return '<span>' + escapeHtml(ch) + '</span>';
    }).join('') + '</span>';
  }

  function rotatedTitleHtml(line) {
    return '<span class="exact-title-rotated">' + escapeHtml(line || '') + '</span>';
  }

  function renderRotatedTitleGroup(group, line) {
    var ids = Array.isArray(group.targets) ? group.targets : [];
    if (!ids.length) return {};
    var primary = document.querySelector('[data-save-id="' + ids[0] + '"]');
    if (!primary) return {};
    var left = numberOrNull(group.left);
    var top = numberOrNull(group.top);
    var width = numberOrNull(group.width);
    var height = numberOrNull(group.height);
    if (left != null) primary.style.left = left + 'px';
    if (top != null) primary.style.top = top + 'px';
    if (width != null) primary.style.width = Math.max(1, width) + 'px';
    if (height != null) primary.style.height = Math.max(1, height) + 'px';
    primary.style.whiteSpace = 'nowrap';
    primary.style.display = 'flex';
    primary.style.alignItems = 'center';
    primary.style.justifyContent = 'center';
    primary.style.textAlign = 'center';
    primary.dataset.titleRotated = 'true';
    primary.dataset.structuredField = 'true';
    setEditableHtml(primary, rotatedTitleHtml(line), { preserveNoWrap: true });
    ids.slice(1).forEach(function (saveId) {
      var target = document.querySelector('[data-save-id="' + saveId + '"]');
      if (!target) return;
      target.innerHTML = '';
      target.classList.add('exact-field-hidden');
      target.dataset.edited = 'true';
    });
    var touched = {};
    ids.forEach(function (id) { touched[id] = true; });
    return touched;
  }

  function renderStackedTitleGroup(group, line) {
    var ids = Array.isArray(group.targets) ? group.targets : [];
    if (!ids.length) return {};
    var primary = document.querySelector('[data-save-id="' + ids[0] + '"]');
    if (!primary) return {};
    var left = numberOrNull(group.left);
    var top = numberOrNull(group.top);
    var width = numberOrNull(group.width);
    var height = numberOrNull(group.height);
    if (left != null) primary.style.left = left + 'px';
    if (top != null) primary.style.top = top + 'px';
    if (width != null) primary.style.width = Math.max(1, width) + 'px';
    if (height != null) primary.style.height = Math.max(1, height) + 'px';
    primary.style.whiteSpace = 'normal';
    primary.dataset.titleStack = 'true';
    primary.dataset.structuredField = 'true';
    setEditableHtml(primary, titleStackHtml(line), { preserveNoWrap: true });
    ids.slice(1).forEach(function (saveId) {
      var target = document.querySelector('[data-save-id="' + saveId + '"]');
      if (!target) return;
      target.innerHTML = '';
      target.classList.add('exact-field-hidden');
      target.dataset.edited = 'true';
    });
    var touched = {};
    ids.forEach(function (id) { touched[id] = true; });
    return touched;
  }

  function distributeTitleLine(line, group) {
    var targets = Array.isArray(group.targets) ? group.targets : [];
    var segmentLengths = Array.isArray(group.segment_lengths) ? group.segment_lengths : [];
    var characters = splitTextSegments(line);
    var pieces = [];
    var cursor = 0;
    targets.forEach(function (_target, index) {
      var length = Math.max(1, Number(segmentLengths[index] || 1));
      if (index === targets.length - 1) {
        pieces.push(characters.slice(cursor).join(''));
      } else {
        pieces.push(characters.slice(cursor, cursor + length).join(''));
      }
      cursor += length;
    });
    return pieces;
  }

  function syncStructuredInput(input) {
    if (!input) return;
    var kind = input.dataset.fieldKind || 'plain';
    var targets = targetElements(input.dataset.targets);
    if (!targets.length) return;
    input.dataset.structuredActive = 'true';

    if (kind === 'cover-title') {
      var lines = normaliseText(input.value).split('\n');
      var groups = parseTitleGroups(input);
      if (groups.length) {
        var touched = {};
        groups.forEach(function (group, groupIndex) {
          var ids = Array.isArray(group.targets) ? group.targets : [];
          if (group.orientation === 'rotated-counterclockwise') {
            var rotatedTouched = renderRotatedTitleGroup(group, lines[groupIndex] || '');
            Object.keys(rotatedTouched).forEach(function (saveId) { touched[saveId] = true; });
            return;
          }
          if (group.orientation === 'vertical-bottom-up' || group.orientation === 'vertical-glyphs') {
            var stackedTouched = renderStackedTitleGroup(group, lines[groupIndex] || '');
            Object.keys(stackedTouched).forEach(function (saveId) { touched[saveId] = true; });
            return;
          }
          var pieces = distributeTitleLine(lines[groupIndex] || '', group);
          ids.forEach(function (saveId, index) {
            var target = document.querySelector('[data-save-id="' + saveId + '"]');
            if (!target) return;
            touched[saveId] = true;
            setEditableHtml(target, escapeHtml(pieces[index] || ''), { preserveNoWrap: true });
          });
        });
        targets.forEach(function (target) {
          if (!touched[target.dataset.saveId]) setEditableHtml(target, '', { preserveNoWrap: true });
        });
      } else {
        targets.forEach(function (target, index) {
          setEditableHtml(target, escapeHtml(lines[index] || ''), { preserveNoWrap: true });
        });
      }
    } else {
      setEditableHtml(targets[0], fieldHtml(kind, input.value), { preserveNoWrap: kind === 'spaced-caps' });
      if (kind === 'spaced-caps') {
        targets[0].style.letterSpacing = '0.16em';
        targets[0].style.whiteSpace = 'nowrap';
      }
      targets.slice(1).forEach(function (target) {
        setEditableHtml(target, '');
      });
    }

    targetElements(input.dataset.hideTargets).forEach(function (target) {
      target.classList.add('exact-field-hidden');
      target.dataset.edited = 'true';
    });
  }

  function syncContactInput(input) {
    if (!input) return;
    var id = input.dataset.contactField;
    var def = CONTACT_DEFS[id];
    var targetId = (input.dataset.targets || (def && def.target) || '').split(',').filter(Boolean)[0];
    var hiddenIds = (input.dataset.hideTargets || ((def && def.hidden) || []).join(',')).split(',').filter(Boolean);
    var target = document.querySelector('[data-save-id="' + targetId + '"]');
    if (!target) return;
    input.dataset.structuredActive = 'true';
    var lines = normaliseText(input.value).split('\n').filter(function (line) { return line.trim(); });
    target.dataset.typographyRole = 'agent-contact';
    target.dataset.contactSemanticBlock = 'true';
    setEditableHtml(target, contactFieldHtml(lines, (def && def.prefixes) || []));
    applyTextTypography(target);
    hiddenIds.forEach(function (saveId) {
      var hidden = document.querySelector('[data-save-id="' + saveId + '"]');
      if (hidden) {
        hidden.classList.add('exact-field-hidden');
        hidden.dataset.edited = 'true';
      }
    });
  }

  function contactFieldHtml(lines, prefixes) {
    var useM = prefixes.indexOf('M') !== -1 || prefixes.indexOf('T') !== -1;
    var useE = prefixes.indexOf('E') !== -1;
    var html = [];
    if (lines[0]) html.push('<span class="exact-contact-name">' + escapeHtml(lines[0]) + '</span>');
    if (lines[1]) {
      html.push(
        useM
          ? '<span class="exact-contact-row"><span class="exact-contact-prefix">M</span><span class="exact-contact-value">' + escapeHtml(lines[1]) + '</span></span>'
          : '<span class="exact-contact-row">' + escapeHtml(lines[1]) + '</span>'
      );
    }
    if (lines[2]) {
      html.push(
        useE
          ? '<span class="exact-contact-row"><span class="exact-contact-prefix">E</span><span class="exact-contact-value">' + escapeHtml(lines[2]) + '</span></span>'
          : '<span class="exact-contact-row">' + escapeHtml(lines[2]) + '</span>'
      );
    }
    for (var i = 3; i < lines.length; i += 1) {
      html.push('<span class="exact-contact-row">' + escapeHtml(lines[i]) + '</span>');
    }
    return '<span class="exact-contact-semantic">' + html.join('\n') + '</span>';
  }

  function collectStructuredFields() {
    var fields = {};
    document.querySelectorAll('[data-exact-field]').forEach(function (input) {
      if (input.dataset.structuredActive === 'true') fields[input.dataset.exactField] = input.value;
    });
    document.querySelectorAll('[data-contact-field]').forEach(function (input) {
      if (input.dataset.structuredActive === 'true') fields['contact:' + input.dataset.contactField] = input.value;
    });
    return fields;
  }

  function applyStructuredFields(fields) {
    if (!fields || typeof fields !== 'object') return;
    Object.keys(fields).forEach(function (key) {
      if (key.indexOf('contact:') === 0) {
        var contact = document.querySelector('[data-contact-field="' + key.slice(8) + '"]');
        if (contact) {
          contact.value = fields[key];
          syncContactInput(contact);
        }
        return;
      }
      var input = document.querySelector('[data-exact-field="' + key + '"]');
      if (input) {
        input.value = fields[key];
        syncStructuredInput(input);
      }
    });
  }

  function setupStructuredFields() {
    var toggle = document.getElementById('fieldsToggle');
    var close = document.getElementById('fieldsClose');
    if (toggle) {
      toggle.addEventListener('click', function () {
        document.body.classList.toggle('fields-open');
        toggle.setAttribute('aria-expanded', document.body.classList.contains('fields-open') ? 'true' : 'false');
      });
    }
    if (close) {
      close.addEventListener('click', function () {
        document.body.classList.remove('fields-open');
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
      });
    }
    document.querySelectorAll('[data-exact-field]').forEach(function (input) {
      input.addEventListener('input', function () {
        syncStructuredInput(input);
        requestSave();
      });
    });
    document.querySelectorAll('[data-contact-field]').forEach(function (input) {
      input.addEventListener('input', function () {
        syncContactInput(input);
        requestSave();
      });
    });
  }

  function iconSvg(iconId) {
    var svg = '';
    if (window.ICON_LOOKUP && window.ICON_LOOKUP[iconId]) {
      svg = window.ICON_LOOKUP[iconId];
    } else if (window.EXACT_ICON_BANK && window.EXACT_ICON_BANK[iconId]) {
      svg = window.EXACT_ICON_BANK[iconId];
    } else {
      svg = LOGOS.facade;
    }
    return String(svg)
      .replace(/rgba\(255,255,255,0\.85\)/g, 'currentColor')
      .replace(/#ffffff/gi, 'currentColor')
      .replace(/#fff/gi, 'currentColor');
  }

  function renderAmenityIconSlot(slot, iconId, active) {
    if (!slot) return;
    iconId = iconId || slot.dataset.iconId || slot.dataset.defaultIconId || 'office';
    slot.dataset.iconId = iconId;
    slot.innerHTML = iconSvg(iconId);
    applyGlobalIconStyle(activeGlobalIconStyle);
    if (active) {
      slot.classList.add('is-active');
      slot.dataset.iconActive = 'true';
      setSourceVectorVisibilityForSlot(slot, 'amenity:' + slot.dataset.iconSlot, true, 0.18);
    } else {
      slot.classList.remove('is-active');
      slot.dataset.iconActive = 'false';
      setSourceVectorVisibilityForSlot(slot, 'amenity:' + slot.dataset.iconSlot, false, 0.18);
    }
  }

  function collectGlobalIconStyle() {
    var size = document.querySelector('[data-global-icon-field="size"]');
    var stroke = document.querySelector('[data-global-icon-field="strokeWidth"]');
    var colour = document.querySelector('[data-global-icon-field="color"]');
    return {
      size: size ? size.value : '100',
      strokeWidth: stroke ? stroke.value : '3',
      color: colour ? colour.value : '',
      colorCustom: Boolean(colour && colour.dataset.custom === 'true')
    };
  }

	  function applyGlobalIconStyle(style) {
	    var accent = document.getElementById('accentColour').value || DEFAULT_ACCENT;
    var next = style && typeof style === 'object' ? style : {};
    var size = Math.max(70, Math.min(130, Number(next.size || 100)));
    var stroke = Math.max(1, Math.min(6, Number(next.strokeWidth || 3)));
    var color = next.colorCustom ? (next.color || accent) : accent;
    activeGlobalIconStyle = {
      size: String(size),
      strokeWidth: String(stroke),
      color: color,
      colorCustom: Boolean(next.colorCustom)
    };
    root.style.setProperty('--exact-icon-scale', (size / 100).toFixed(3));
    root.style.setProperty('--exact-icon-stroke-width', stroke.toFixed(2));
    root.style.setProperty('--exact-icon-color', color);
    var sizeInput = document.querySelector('[data-global-icon-field="size"]');
    var strokeInput = document.querySelector('[data-global-icon-field="strokeWidth"]');
    var colourInput = document.querySelector('[data-global-icon-field="color"]');
    if (sizeInput) sizeInput.value = String(size);
    if (strokeInput) strokeInput.value = String(stroke);
    if (colourInput) {
      colourInput.value = color;
      colourInput.dataset.custom = activeGlobalIconStyle.colorCustom ? 'true' : 'false';
    }
    document.querySelectorAll('.exact-amenity-icon-slot, .icon-opt').forEach(function (el) { el.style.color = color; });
  }

  function collectAmenityIconFields() {
    var icons = {};
    document.querySelectorAll('[data-icon-slot]').forEach(function (slot) {
      if (slot.dataset.iconActive !== 'true' && slot.dataset.iconId === slot.dataset.defaultIconId) return;
      icons[slot.dataset.iconSlot] = {
        iconId: slot.dataset.iconId || slot.dataset.defaultIconId || 'office',
        html: slot.innerHTML,
        active: slot.dataset.iconActive === 'true' || slot.classList.contains('is-active')
      };
    });
    return icons;
  }

  function applyAmenityIconFields(fields) {
    if (!fields || typeof fields !== 'object') return;
    Object.keys(fields).forEach(function (key) {
      var item = typeof fields[key] === 'string' ? { iconId: fields[key], active: true } : (fields[key] || {});
      var slot = document.querySelector('[data-icon-slot="' + key + '"]');
      var select = document.querySelector('[data-icon-field="' + key + '"]');
      if (!slot) return;
      if (select && item.iconId) select.value = item.iconId;
      renderAmenityIconSlot(slot, item.iconId || slot.dataset.defaultIconId, item.active !== false);
    });
  }

  function syncAmenityIconFromSlot(slot, shouldSave) {
    if (!slot || !slot.dataset.iconSlot) return;
    var select = document.querySelector('[data-icon-field="' + slot.dataset.iconSlot + '"]');
    if (select) select.value = slot.dataset.iconId || slot.dataset.defaultIconId || 'office';
    slot.classList.add('is-active');
    slot.dataset.iconActive = 'true';
    setSourceVectorVisibilityForSlot(slot, 'amenity:' + slot.dataset.iconSlot, true, 0.18);
    if (shouldSave) requestSave();
  }

  function setupAmenityIconFields() {
    document.querySelectorAll('[data-icon-slot]').forEach(function (slot) {
      renderAmenityIconSlot(slot, slot.dataset.iconId || slot.dataset.defaultIconId, false);
      slot.addEventListener('click', function (event) {
        event.preventDefault();
        if (event.stopImmediatePropagation) event.stopImmediatePropagation();
        else event.stopPropagation();
        openExactIconPicker(slot, event);
      });
    });
    document.querySelectorAll('[data-icon-field]').forEach(function (select) {
      select.addEventListener('change', function () {
        var slot = document.querySelector('[data-icon-slot="' + select.dataset.iconField + '"]');
        renderAmenityIconSlot(slot, select.value, true);
        requestSave();
      });
    });
    if (iconObserver) iconObserver.disconnect();
    iconObserver = new MutationObserver(function (records) {
      records.forEach(function (record) {
        var slot = record.target.closest && record.target.closest('[data-icon-slot]');
        if (slot) syncAmenityIconFromSlot(slot, true);
      });
    });
    document.querySelectorAll('[data-icon-slot]').forEach(function (slot) {
      iconObserver.observe(slot, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-icon-id'] });
    });
  }

  function iconCategories() {
    if (window.SVG_ICONS) return window.SVG_ICONS;
    return { Amenities: window.EXACT_ICON_BANK || {} };
  }

  function openExactIconPicker(slot, event) {
    var overlay = document.getElementById('iconPickerOverlay');
    var panel = document.getElementById('iconPickerPanel');
    var grid = document.getElementById('iconGrid');
    var cats = document.getElementById('iconPickerCats');
    if (!overlay || !panel || !grid || !cats) return;
    function renderGrid(cat) {
      var icons = iconCategories()[cat] || {};
      grid.innerHTML = Object.keys(icons).map(function (id) {
        return '<button type="button" class="icon-opt" data-icon-id="' + escapeHtml(id) + '" title="' + escapeHtml(id) + '">' + iconSvg(id) + '</button>';
      }).join('');
      grid.querySelectorAll('[data-icon-id]').forEach(function (button) {
        button.addEventListener('click', function (clickEvent) {
          clickEvent.preventDefault();
          clickEvent.stopPropagation();
          renderAmenityIconSlot(slot, button.dataset.iconId, true);
          var select = document.querySelector('[data-icon-field="' + slot.dataset.iconSlot + '"]');
          if (select) select.value = button.dataset.iconId;
          overlay.classList.remove('open');
          requestSave();
        });
      });
    }
    var categoryNames = Object.keys(iconCategories());
    var activeCat = categoryNames.indexOf('Amenities') >= 0 ? 'Amenities' : categoryNames[0];
    cats.innerHTML = categoryNames.map(function (cat) {
      return '<button type="button" class="cat-tab" data-cat="' + escapeHtml(cat) + '">' + escapeHtml(cat) + '</button>';
    }).join('');
    cats.querySelectorAll('[data-cat]').forEach(function (button) {
      button.addEventListener('click', function (clickEvent) {
        clickEvent.preventDefault();
        clickEvent.stopPropagation();
        renderGrid(button.dataset.cat);
      });
    });
    renderGrid(activeCat);
    overlay.classList.add('open');
    var rect = slot.getBoundingClientRect();
    panel.style.left = Math.max(8, Math.min(window.innerWidth - 318, rect.left - 70)) + 'px';
    panel.style.top = Math.max(64, Math.min(window.innerHeight - 380, rect.bottom + 10)) + 'px';
    if (!overlay.dataset.exactPickerBound) {
      overlay.dataset.exactPickerBound = 'true';
      overlay.addEventListener('click', function (overlayEvent) {
        if (overlayEvent.target === overlay) overlay.classList.remove('open');
      });
    }
  }

  function setSourceVectorVisibilityForSlot(slot, ownerId, hidden, threshold) {
    if (!slot) return 0;
    var page = slot.closest('.exact-page');
    if (!page) return 0;
    var changed = 0;
    var pageWidth = Number(page.style.width.replace('px', '')) || page.clientWidth;
    var pageHeight = Number(page.style.height.replace('px', '')) || page.clientHeight;
    var slotRect = {
      left: parseFloat(slot.style.left) || 0,
      top: parseFloat(slot.style.top) || 0,
      width: parseFloat(slot.style.width) || slot.offsetWidth || 0,
      height: parseFloat(slot.style.height) || slot.offsetHeight || 0
    };
    page.querySelectorAll('.pdf-vector-layer, .pdf-vector-overlay-layer').forEach(function (svg) {
      if (!svg.viewBox || !svg.viewBox.baseVal) return;
      var viewBox = svg.viewBox.baseVal;
      var scaleX = pageWidth / (viewBox.width || pageWidth);
      var scaleY = pageHeight / (viewBox.height || pageHeight);
      svg.querySelectorAll('.pdf-vector-shape[data-bbox]').forEach(function (shape) {
        var parts = (shape.dataset.bbox || '').split(',').map(Number);
        if (parts.length !== 4 || parts.some(function (value) { return !isFinite(value); })) return;
        var shapeRect = {
          left: parts[0] * scaleX,
          top: parts[1] * scaleY,
          width: parts[2] * scaleX,
          height: parts[3] * scaleY
        };
        var overlap = rectOverlapRatio(shapeRect, slotRect);
        if (overlap < (threshold || 0.25)) return;
        if (hidden) {
          shape.classList.add(ownerId.indexOf('amenity:') === 0 ? 'source-amenity-icon-hidden' : 'source-logo-hidden');
          shape.dataset.logoHiddenBy = ownerId;
          changed += 1;
        } else if (shape.dataset.logoHiddenBy === ownerId) {
          shape.classList.remove('source-logo-hidden');
          shape.classList.remove('source-amenity-icon-hidden');
          delete shape.dataset.logoHiddenBy;
          changed += 1;
        }
      });
    });
    return changed;
  }

  function ensureAgencyLogoSlots() {
    Object.keys(AGENCY_LOGO_DEFS).forEach(function (id) {
      var def = AGENCY_LOGO_DEFS[id];
      var page = document.querySelector('.exact-page[data-page-num="' + def.page + '"]');
      if (!page || page.querySelector('[data-agency-logo-slot="' + id + '"]')) return;
      var slot = document.createElement('div');
      slot.className = 'exact-brand-logo-slot';
      if (def.defaultAssetUrl) slot.classList.add('has-default-agency-logo');
      slot.dataset.agencyLogoSlot = id;
      slot.dataset.brandLogoSlot = id;
      if (def.defaultAssetUrl) slot.dataset.defaultAgencyLogoAsset = def.defaultAssetUrl;
      if (def.sourceDetection) slot.dataset.sourceDetection = def.sourceDetection;
      slot.style.left = def.left + 'px';
      slot.style.top = def.top + 'px';
      slot.style.width = def.width + 'px';
      slot.style.height = def.height + 'px';
      setAgencyLogoSlotHtml(slot, id, agencyLogoDefaultMarkup(def));
      page.appendChild(slot);
    });
  }

  function agencyLogoDefaultMarkup(def) {
    if (def && def.defaultAssetUrl) {
      return '<div class="exact-agency-logo-default"><img src="' + escapeHtml(def.defaultAssetUrl) + '" alt=""></div>' +
        '<div class="logo-mask"></div><div class="logo-output logo-output-' + escapeHtml(def.className || 'agency') + '"></div>';
    }
    return '<div class="logo-mask"></div><div class="logo-output logo-output-' + escapeHtml((def && def.className) || 'agency') + '"></div>';
  }

  function agencyLogoUploadChrome(id) {
    var def = AGENCY_LOGO_DEFS[id] || {};
    var label = def.label || id || 'Agency';
    return '<input class="agency-logo-upload" type="file" accept="image/*" ' +
      'data-agency-logo-slot-upload="' + escapeHtml(id) + '" ' +
      'aria-label="Replace ' + escapeHtml(label) + ' logo">' +
      '<div class="agency-logo-chip">Logo</div>';
  }

  function setAgencyLogoSlotHtml(slot, id, content) {
    if (!slot) return;
    slot.innerHTML = String(content || '') + agencyLogoUploadChrome(id);
  }

  function renderAgencyLogoText(id, value) {
    var def = AGENCY_LOGO_DEFS[id];
    var slot = document.querySelector('[data-agency-logo-slot="' + id + '"]');
    if (!def || !slot) return;
    var output = slot.querySelector('.logo-output');
    if (!output) return;
    var lines = String(value || def.defaultText || '').replace(/\|\|\|/g, '\n').split('\n');
    output.className = 'logo-output logo-output-' + def.className;
    output.innerHTML = lines.map(escapeHtml).join('<br/>');
  }

  function setSourceLogoVisibility(id, hidden) {
    var slot = document.querySelector('[data-agency-logo-slot="' + id + '"]');
    setSourceVectorVisibilityForSlot(slot, id, hidden, 0.25);
  }

  function rectOverlapRatio(a, b) {
    var left = Math.max(a.left, b.left);
    var top = Math.max(a.top, b.top);
    var right = Math.min(a.left + a.width, b.left + b.width);
    var bottom = Math.min(a.top + a.height, b.top + b.height);
    var overlap = Math.max(0, right - left) * Math.max(0, bottom - top);
    var area = Math.max(1, a.width * a.height);
    return overlap / area;
  }

  function applyAgencyLogo(id, options) {
    ensureAgencyLogoSlots();
    var def = AGENCY_LOGO_DEFS[id];
    var slot = document.querySelector('[data-agency-logo-slot="' + id + '"]');
    var input = document.querySelector('[data-logo-field="' + id + '"]');
    if (!def || !slot) return;
    options = options || {};
    if (input && options.text != null) input.value = options.text;
    if (options.uploadedLogoDataUrl) agencyLogoUploads[id] = options.uploadedLogoDataUrl;
    var dataUrl = agencyLogoUploads[id] || '';
    var active = options.active || dataUrl || (input && input.dataset.logoActive === 'true');
    if (!active) {
      slot.classList.remove('is-active');
      setSourceLogoVisibility(id, false);
      return;
    }
    slot.classList.add('is-active');
    setSourceLogoVisibility(id, true);
    if (input) input.dataset.logoActive = 'true';
    var text = input ? input.value : def.defaultText;
    if (dataUrl) {
      setAgencyLogoSlotHtml(slot, id, '<div class="logo-mask"></div><img src="' + dataUrl + '" alt="">');
    } else if (!slot.querySelector('.logo-output')) {
      setAgencyLogoSlotHtml(slot, id, '<div class="logo-mask"></div><div class="logo-output logo-output-' + def.className + '"></div>');
      renderAgencyLogoText(id, text);
    } else {
      renderAgencyLogoText(id, text);
    }
  }

  function clearAgencyLogo(id) {
    var def = AGENCY_LOGO_DEFS[id];
    var slot = document.querySelector('[data-agency-logo-slot="' + id + '"]');
    var input = document.querySelector('[data-logo-field="' + id + '"]');
    if (input && def) {
      input.value = def.defaultText;
      input.dataset.logoActive = 'false';
    }
    delete agencyLogoUploads[id];
    if (slot) {
      slot.classList.remove('is-active');
      setSourceLogoVisibility(id, false);
      setAgencyLogoSlotHtml(slot, id, agencyLogoDefaultMarkup(def || {}));
    }
  }

  function collectAgencyLogos() {
    var logos = {};
    Object.keys(AGENCY_LOGO_DEFS).forEach(function (id) {
      var slot = document.querySelector('[data-agency-logo-slot="' + id + '"]');
      var input = document.querySelector('[data-logo-field="' + id + '"]');
      if (!slot || !slot.classList.contains('is-active')) return;
      logos[id] = {
        active: true,
        text: input ? input.value : AGENCY_LOGO_DEFS[id].defaultText,
        uploadedLogoDataUrl: agencyLogoUploads[id] || ''
      };
    });
    return logos;
  }

  function applyAgencyLogoState(logos) {
    if (!logos || typeof logos !== 'object') return;
    Object.keys(logos).forEach(function (id) {
      applyAgencyLogo(id, logos[id] || {});
    });
  }

  function handleAgencyLogoUpload(id, file) {
    if (!id || !file) return;
    var reader = new FileReader();
    reader.onload = function (readerEvent) {
      agencyLogoUploads[id] = readerEvent.target.result;
      var textInput = document.querySelector('[data-logo-field="' + id + '"]');
      if (textInput) textInput.dataset.logoActive = 'true';
      applyAgencyLogo(id, { active: true, uploadedLogoDataUrl: agencyLogoUploads[id] });
      requestSave();
    };
    reader.readAsDataURL(file);
  }

  function setupAgencyLogoFields() {
    ensureAgencyLogoSlots();
    if (!document.body.dataset.agencyLogoUploadBound) {
      document.body.dataset.agencyLogoUploadBound = 'true';
      document.addEventListener('change', function (event) {
        var input = event.target && event.target.closest ? event.target.closest('[data-agency-logo-slot-upload]') : null;
        if (!input) return;
        handleAgencyLogoUpload(input.dataset.agencyLogoSlotUpload, input.files && input.files[0]);
      });
    }
    document.querySelectorAll('[data-logo-field]').forEach(function (input) {
      input.addEventListener('input', function () {
        input.dataset.logoActive = 'true';
        applyAgencyLogo(input.dataset.logoField, { active: true, text: input.value });
        requestSave();
      });
    });
    document.querySelectorAll('[data-logo-upload]').forEach(function (input) {
      input.addEventListener('change', function (event) {
        var file = event.target.files && event.target.files[0];
        handleAgencyLogoUpload(input.dataset.logoUpload, file);
      });
    });
    document.querySelectorAll('[data-logo-clear]').forEach(function (button) {
      button.addEventListener('click', function () {
        clearAgencyLogo(button.dataset.logoClear);
        requestSave();
      });
    });
  }

  function exactMapArea(scope) {
    if (scope && scope.closest) {
      var scoped = scope.closest('[data-exact-map-area]');
      if (scoped) return scoped;
    }
    return document.querySelector('[data-exact-map-area]');
  }

  function readJsonDataset(el, key, fallback) {
    if (!el) return fallback;
    try {
      return JSON.parse(el.dataset[key] || '');
    } catch (err) {
      return fallback;
    }
  }

  function setExactMapStatus(text, area) {
    var rootNode = area || document;
    rootNode.querySelectorAll('[data-exact-map-status]').forEach(function (el) {
      el.textContent = text;
    });
  }

  function ensureGeneratedMapLayer(area) {
    if (!area) return null;
    var layer = area.querySelector('.exact-generated-map');
    if (!layer) {
      layer = document.createElement('div');
      layer.className = 'exact-generated-map';
      area.insertBefore(layer, area.firstChild);
    }
    return layer;
  }

  function collectMapLabels(area) {
    var labels = readJsonDataset(area, 'mapLabels', []);
    var byKey = {};
    labels.forEach(function (label) {
      if (label && label.key) byKey[label.key] = Object.assign({}, label);
    });
    document.querySelectorAll('[data-map-label-field]').forEach(function (input) {
      var mapSaveId = input.dataset.mapSaveId || '';
      if (mapSaveId && area && mapSaveId !== (area.dataset.saveId || '')) return;
      var rawKey = input.dataset.mapLabelField || '';
      var key = rawKey.indexOf('mapLabel:') === 0 ? rawKey.slice(9) : rawKey;
      if (!key) return;
      var existing = byKey[key] || { key: key };
      existing.text = input.value;
      existing.save_id = (input.dataset.targets || existing.save_id || '').split(',').filter(Boolean)[0] || existing.save_id || '';
      byKey[key] = existing;
    });
    var nextLabels = Object.keys(byKey).map(function (key) { return byKey[key]; });
    nextLabels.sort(function (a, b) {
      return Number(a.rank || 999) - Number(b.rank || 999)
        || Number(a.top || 0) - Number(b.top || 0)
        || Number(a.left || 0) - Number(b.left || 0);
    });
    if (area) {
      area.dataset.mapLabels = JSON.stringify(nextLabels);
      area.dataset.mapLabelCount = String(nextLabels.length);
    }
    return nextLabels;
  }

  function applyMapLabels(labels, area) {
    if (!Array.isArray(labels)) return;
    area = exactMapArea(area);
    if (area) {
      area.dataset.mapLabels = JSON.stringify(labels);
      area.dataset.mapLabelCount = String(labels.length);
    }
    labels.forEach(function (label) {
      if (!label || !label.key) return;
      var selector = '[data-map-label-field="mapLabel:' + label.key + '"], [data-map-label-field="' + label.key + '"]';
      var input = Array.from(document.querySelectorAll(selector)).find(function (candidate) {
        return !candidate.dataset.mapSaveId || !area || candidate.dataset.mapSaveId === (area.dataset.saveId || '');
      });
      if (input && typeof label.text === 'string') {
        input.value = label.text;
        syncStructuredInput(input);
      }
    });
  }

  function collectExactMapStateForArea(area) {
    if (!area) return {};
    var generated = area.querySelector('.exact-generated-map');
    var labels = collectMapLabels(area);
    var styleTokens = readJsonDataset(area, 'mapV1StyleTokens', null);
    var metadata = readJsonDataset(area, 'mapV1Metadata', null);
    var warnings = readJsonDataset(area, 'mapV1Warnings', null);
    return {
      source: area.dataset.mapSource || 'pdf-exact',
      saveId: area.dataset.saveId || '',
      bounds: {
        left: parseFloat(area.style.left) || 0,
        top: parseFloat(area.style.top) || 0,
        width: parseFloat(area.style.width) || area.offsetWidth || 0,
        height: parseFloat(area.style.height) || area.offsetHeight || 0
      },
      labels: labels,
      center: readJsonDataset(area, 'mapCenter', {}),
      content: readJsonDataset(area, 'mapContent', {}),
      generatedHtml: area.classList.contains('has-generated-map') && generated ? generated.innerHTML : '',
      mapV1: {
        mapId: area.dataset.mapV1Id || '',
        styleHash: area.dataset.mapV1StyleHash || '',
        styleVersion: area.dataset.mapV1StyleVersion || '',
        styleKey: area.dataset.mapV1StyleKey || '',
        styleTokens: styleTokens,
        svgUrl: area.dataset.mapV1SvgUrl || '',
        pdfUrl: area.dataset.mapV1PdfUrl || '',
        warningFlags: area.dataset.mapV1WarningFlags || '',
        needsNudge: area.dataset.mapV1NeedsNudge || '',
        metadata: metadata,
        warnings: warnings
      }
    };
  }

  function collectExactMapState() {
    var areas = Array.from(document.querySelectorAll('[data-exact-map-area]'));
    if (!areas.length) return {};
    var maps = areas.map(function (area) { return collectExactMapStateForArea(area); });
    var first = Object.assign({}, maps[0] || {});
    first.maps = maps;
    return first;
  }

  function collectMapState() {
    return collectExactMapState();
  }

  function applyExactMapStateToArea(mapState, area) {
    if (!area || !mapState || typeof mapState !== 'object') return;
    if (Array.isArray(mapState.labels) && mapState.labels.some(function (label) { return label && label.key; })) {
      var extractedLabels = readJsonDataset(area, 'mapLabels', []);
      if (mapState.generatedHtml || mapState.source === 'generated-v1') {
        applyMapLabels(mapState.labels, area);
      }
    }
    if (mapState.center) area.dataset.mapCenter = JSON.stringify(mapState.center);
    if (mapState.content) area.dataset.mapContent = JSON.stringify(mapState.content);
    if (mapState.generatedHtml) {
      ensureGeneratedMapLayer(area).innerHTML = mapState.generatedHtml;
      area.classList.add('has-generated-map');
      area.dataset.mapSource = mapState.source || 'generated-v1';
      area.classList.add('source-map-hidden');
      setExactMapStatus('Generated map restored', area);
    } else {
      area.classList.remove('has-generated-map');
      area.classList.remove('source-map-hidden');
      area.dataset.mapSource = 'pdf-exact';
      setExactMapStatus('PDF map preserved', area);
    }
    var mapV1 = mapState.mapV1 || {};
    if (mapV1.mapId) area.dataset.mapV1Id = mapV1.mapId;
    if (mapV1.styleHash) area.dataset.mapV1StyleHash = mapV1.styleHash;
    if (mapV1.styleVersion) area.dataset.mapV1StyleVersion = mapV1.styleVersion;
    if (mapV1.styleKey) area.dataset.mapV1StyleKey = mapV1.styleKey;
    if (mapV1.styleTokens) area.dataset.mapV1StyleTokens = JSON.stringify(mapV1.styleTokens);
    if (mapV1.svgUrl) area.dataset.mapV1SvgUrl = mapV1.svgUrl;
    if (mapV1.pdfUrl) area.dataset.mapV1PdfUrl = mapV1.pdfUrl;
    if (mapV1.warningFlags) area.dataset.mapV1WarningFlags = mapV1.warningFlags;
    if (mapV1.needsNudge) area.dataset.mapV1NeedsNudge = mapV1.needsNudge;
    if (mapV1.metadata) area.dataset.mapV1Metadata = JSON.stringify(mapV1.metadata);
    if (mapV1.warnings) area.dataset.mapV1Warnings = JSON.stringify(mapV1.warnings);
  }

  function applyExactMapState(mapState) {
    if (!mapState || typeof mapState !== 'object') return;
    if (Array.isArray(mapState.maps)) {
      mapState.maps.forEach(function (entry) {
        var saveId = entry && entry.saveId ? String(entry.saveId) : '';
        var area = saveId ? document.querySelector('[data-exact-map-area][data-save-id="' + saveId + '"]') : null;
        applyExactMapStateToArea(entry, area || exactMapArea());
      });
      return;
    }
    applyExactMapStateToArea(mapState, exactMapArea());
  }

  function applyMapState(mapState) {
    applyExactMapState(mapState);
  }

  function preservePdfMap(area) {
    area = exactMapArea(area);
    if (!area) return;
    var generated = area.querySelector('.exact-generated-map');
    if (generated) generated.innerHTML = '';
    area.classList.remove('has-generated-map');
    area.classList.remove('source-map-hidden');
    area.dataset.mapSource = 'pdf-exact';
    setExactMapStatus('PDF map preserved', area);
    requestSave();
  }

  function exactMapRenderPayload(area, styleData) {
    var center = readJsonDataset(area, 'mapCenter', {});
    var content = readJsonDataset(area, 'mapContent', {});
    var payload = {
      style_tokens: styleData.style_tokens || {},
      output: {
        width_px: Math.min(3000, Math.max(200, Math.round(area.offsetWidth || parseFloat(area.style.width) || 940))),
        height_px: Math.min(3000, Math.max(200, Math.round(area.offsetHeight || parseFloat(area.style.height) || 750))),
        include_svg: true,
        include_pdf: true
      },
      options: { debug: false }
    };
    if (center && typeof center.lat === 'number' && typeof center.lon === 'number') {
      payload.center = center;
      payload.content = Object.assign({
        building_name: 'Subject Property',
        stations: [],
        poi_categories: ['restaurant', 'cafe', 'fitness']
      }, content || {});
      return payload;
    }
    payload.project_id = projectId;
    payload.extent = { radius_m: 900 };
    if (content && Object.keys(content).length) payload.content = content;
    return payload;
  }

  function mapLabelColour(label) {
    var text = String((label && label.text) || '').toLowerCase();
    if (label && Number(label.rank || 4) === 0) return 'var(--exact-accent)';
    if (text.indexOf('fitness') >= 0 || text.indexOf('gym') >= 0 || text.indexOf('athletic') >= 0 || text.indexOf('wellness') >= 0) return '#df6faf';
    if (text.indexOf('coffee') >= 0 || text.indexOf('cafe') >= 0 || text.indexOf('green') >= 0) return '#84d2a7';
    if (text.indexOf('wine') >= 0 || text.indexOf('bar') >= 0 || text.indexOf('market') >= 0 || text.indexOf('restaurant') >= 0) return '#ff7a2f';
    if (text.indexOf('hotel') >= 0 || text.indexOf('inn') >= 0 || text.indexOf('suites') >= 0) return '#198acb';
    return '#c9b3e4';
  }

  function buildExactLabelMapSvg(area) {
    var labels = collectMapLabels(area);
    var width = Math.max(200, Math.round(parseFloat(area.style.width) || area.offsetWidth || 940));
    var height = Math.max(200, Math.round(parseFloat(area.style.height) || area.offsetHeight || 750));
    var left = parseFloat(area.style.left) || 0;
    var top = parseFloat(area.style.top) || 0;
    var pieces = [
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + width + ' ' + height + '" width="100%" height="100%" role="img" aria-label="Recovered editable map">',
      '<rect width="100%" height="100%" fill="var(--exact-dark)"/>',
      '<g fill="none" stroke="rgba(255,234,0,0.34)" stroke-width="1.2" opacity="0.62">',
      '<path d="M' + (width * 0.10).toFixed(1) + ' ' + (height * 0.36).toFixed(1) + ' C' + (width * 0.36).toFixed(1) + ' ' + (height * 0.24).toFixed(1) + ' ' + (width * 0.68).toFixed(1) + ' ' + (height * 0.42).toFixed(1) + ' ' + (width * 0.92).toFixed(1) + ' ' + (height * 0.32).toFixed(1) + '"/>',
      '<path d="M' + (width * 0.15).toFixed(1) + ' ' + (height * 0.73).toFixed(1) + ' C' + (width * 0.42).toFixed(1) + ' ' + (height * 0.61).toFixed(1) + ' ' + (width * 0.58).toFixed(1) + ' ' + (height * 0.78).toFixed(1) + ' ' + (width * 0.88).toFixed(1) + ' ' + (height * 0.64).toFixed(1) + '"/>',
      '</g>'
    ];
    labels.forEach(function (label) {
      if (!label || !label.text) return;
      var x = Math.max(14, Math.min(width - 180, Number(label.left || 0) - left));
      var y = Math.max(20, Math.min(height - 18, Number(label.top || 0) - top));
      var rank = Number(label.rank || 4);
      var size = rank <= 1 ? 18 : (rank <= 2 ? 15 : 13);
      var weight = rank <= 1 ? 600 : 400;
      var colour = mapLabelColour(label);
      if (rank === 0) {
        pieces.push('<circle cx="' + x.toFixed(1) + '" cy="' + (y + 34).toFixed(1) + '" r="12" fill="var(--exact-accent)"/>');
      }
      pieces.push(
        '<text x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" fill="' + colour + '" ' +
        'font-family="Arial, sans-serif" font-size="' + size + '" font-weight="' + weight + '">' +
        escapeHtml(label.text) + '</text>'
      );
    });
    pieces.push('</svg>');
    return pieces.join('');
  }

  function applyExactFallbackMap(area) {
    area = exactMapArea(area);
    if (!area) return;
    var layer = ensureGeneratedMapLayer(area);
    if (!layer) return;
    layer.innerHTML = buildExactLabelMapSvg(area);
    area.classList.add('has-generated-map');
    area.classList.add('source-map-hidden');
    area.dataset.mapSource = 'generated-exact-labels';
    area.dataset.mapV1Id = 'exact-label-fallback';
    area.dataset.mapV1WarningFlags = 'dataset_unavailable_fallback';
    setExactMapStatus('Recovered editable label map applied', area);
    requestSave();
  }

	  function regenerateExactMap(area) {
    area = exactMapArea(area);
    if (!area) return;
    setExactMapStatus('Generating map...', area);
	    var accent = document.getElementById('accentColour').value || DEFAULT_ACCENT;
    var styleKey = [accent, area.dataset.mapVibe || '', area.dataset.mapLabelCount || '0'].join('|');
    var existingKey = area.dataset.mapV1StyleKey || '';
    var existingTokens = readJsonDataset(area, 'mapV1StyleTokens', null);
    var stylePromise = existingKey === styleKey && existingTokens
      ? Promise.resolve({ style_tokens: existingTokens, style_hash: area.dataset.mapV1StyleHash || '', style_version: area.dataset.mapV1StyleVersion || '' })
      : fetch('/api/maps/v1/style/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_id: projectId,
            brochure_primary_hex: accent,
            vibe_text: area.dataset.mapVibe || 'City office leasing brochure map in the exact PDF style'
          })
        }).then(function (res) {
          if (!res.ok) throw new Error('Style failed');
          return res.json();
        });
    stylePromise.then(function (style) {
      area.dataset.mapV1StyleKey = styleKey;
      area.dataset.mapV1StyleHash = style.style_hash || '';
      area.dataset.mapV1StyleVersion = style.style_version || '';
      area.dataset.mapV1StyleTokens = JSON.stringify(style.style_tokens || {});
      return fetch('/api/maps/v1/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(exactMapRenderPayload(area, style))
      }).then(function (res) {
        if (!res.ok) throw new Error('Render failed');
        return res.json().then(function (rendered) {
          return { style: style, rendered: rendered };
        });
      });
    }).then(function (result) {
      var artifacts = result.rendered.artifacts || {};
      var svgUrl = artifacts.svg_url || artifacts.svgUrl || '';
      if (!svgUrl) throw new Error('No SVG artifact');
      return fetch(svgUrl).then(function (res) {
        if (!res.ok) throw new Error('SVG unavailable');
        return res.text().then(function (svgText) {
          return { svgText: svgText, result: result, svgUrl: svgUrl };
        });
      });
    }).then(function (payload) {
      area = exactMapArea(area);
      var layer = ensureGeneratedMapLayer(area);
      if (!area || !layer) return;
      var rendered = payload.result.rendered || {};
      var style = payload.result.style || {};
      var artifacts = rendered.artifacts || {};
      layer.innerHTML = payload.svgText;
      area.classList.add('has-generated-map');
      area.classList.add('source-map-hidden');
      area.dataset.mapSource = 'generated-v1';
      area.dataset.mapV1Id = rendered.map_id || rendered.mapId || '';
      area.dataset.mapV1StyleHash = style.style_hash || '';
      area.dataset.mapV1StyleVersion = style.style_version || '';
      area.dataset.mapV1StyleTokens = JSON.stringify(style.style_tokens || {});
      area.dataset.mapV1SvgUrl = payload.svgUrl;
      area.dataset.mapV1PdfUrl = artifacts.pdf_url || artifacts.pdfUrl || '';
      area.dataset.mapV1Metadata = JSON.stringify(rendered.metadata || {});
      area.dataset.mapV1Warnings = JSON.stringify(rendered.warnings || []);
      area.dataset.mapV1WarningFlags = ((rendered.metadata || {}).ui_warning_flags || []).join(',');
      area.dataset.mapV1NeedsNudge = area.dataset.mapV1WarningFlags.indexOf('critical_label_drop') >= 0 ? '1' : '0';
      setExactMapStatus('Generated map applied', area);
      requestSave();
    }).catch(function () {
      applyExactFallbackMap(area);
    });
  }

  function setupExactMapControls() {
    document.querySelectorAll('[data-exact-map-generate], [data-exact-map-generate-field]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        event.preventDefault();
        regenerateExactMap(button.closest('[data-exact-map-area]'));
      });
    });
    document.querySelectorAll('[data-exact-map-preserve]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        event.preventDefault();
        preservePdfMap(button.closest('[data-exact-map-area]'));
      });
    });
  }

  function serialize() {
    var state = {
      exactLayout: true,
      editableLayerVersion: 5,
      colourPreset: document.getElementById('colourPreset').value,
      accentColour: document.getElementById('accentColour').value,
      darkColour: document.getElementById('darkColour').value,
      logo: document.getElementById('logoSelect').value,
      uploadedLogoDataUrl: uploadedLogoDataUrl,
      logoSize: document.getElementById('logoSize').value,
      logoPosition: document.getElementById('logoPosition').value,
      globalLogo: collectGlobalLogoState(),
      layoutMode: (document.querySelector('[data-layout-mode].is-active') || {}).dataset.layoutMode || 'editable',
      pictureLayout: document.getElementById('pictureLayout').value || 'original',
      zoom: document.getElementById('zoomRange').value,
      structuredFields: collectStructuredFields(),
      typography: collectTypographySettings(),
      globalIconStyle: collectGlobalIconStyle(),
      amenityIconFields: collectAmenityIconFields(),
      icons: collectAmenityIconFields(),
      agencyLogos: collectAgencyLogos(),
      exactMap: collectMapState(),
      mapState: collectMapState(),
      mapFields: collectMapState(),
      editableTexts: {},
      images: {}
    };
    document.querySelectorAll('.pdf-text[contenteditable="true"]').forEach(function (el) {
      state.editableTexts[el.dataset.saveId] = {
        html: el.innerHTML,
        edited: el.dataset.edited === 'true',
        typography: collectTextTypography(el),
        layout: collectTextLayout(el)
      };
    });
    document.querySelectorAll('.exact-image-slot').forEach(function (el) {
      var photo = slotPhoto(el);
      state.images[el.dataset.saveId] = {
        bgImage: photo ? (photo.style.backgroundImage || '') : '',
        fit: el.dataset.fit || 'cover',
        left: el.style.left,
        top: el.style.top,
        width: el.style.width,
        height: el.style.height
      };
    });
    return state;
  }

  function applyState(state) {
    if (!state || typeof state !== 'object') return;
    if (state.colourPreset && COLOUR_PRESETS[state.colourPreset]) {
      document.getElementById('colourPreset').value = state.colourPreset;
    }
    if (state.accentColour) document.getElementById('accentColour').value = state.accentColour;
    if (state.darkColour) document.getElementById('darkColour').value = state.darkColour;
    if (state.zoom) document.getElementById('zoomRange').value = state.zoom;
    if (state.logoSize) document.getElementById('logoSize').value = state.logoSize;
    if (state.logoPosition) document.getElementById('logoPosition').value = state.logoPosition;
    if (state.uploadedLogoDataUrl) uploadedLogoDataUrl = state.uploadedLogoDataUrl;
    applyColours(state.accentColour, state.darkColour);
    applyTypographySettings(state.typography);
    applyGlobalIconStyle(state.globalIconStyle);
    if (state.globalLogo) {
      applyGlobalLogoState(state.globalLogo);
    } else {
      applyLogo(state.logo || 'none');
      document.getElementById('logoSelect').value = state.logo || 'none';
      syncGlobalLogoControls();
    }
    applyLayoutMode(state.editableLayerVersion ? (state.layoutMode || 'editable') : 'editable');
    applyPictureLayout(state.pictureLayout || 'original');
    if (state.editableTexts) {
      Object.keys(state.editableTexts).forEach(function (saveId) {
        var el = document.querySelector('[data-save-id="' + saveId + '"]');
        var item = state.editableTexts[saveId];
        if (el && shouldApplySavedTextState(el, item)) {
          el.innerHTML = item.html;
          normaliseEditedTextMarkup(el);
          applyTextLayout(el, item.layout);
          if (item.edited) {
            el.dataset.edited = 'true';
          } else {
            delete el.dataset.edited;
          }
        }
      });
    }
    if (state.images && Number(state.editableLayerVersion || 0) >= 3) {
      Object.keys(state.images).forEach(function (saveId) {
        var el = document.querySelector('[data-save-id="' + saveId + '"]');
        var item = state.images[saveId] || {};
        if (!el) return;
        if (item.bgImage) {
          setSlotImage(el, item.bgImage);
        }
        if (item.fit) {
          setSlotFit(el, item.fit);
        }
        if (item.left && item.top && item.width && item.height && state.pictureLayout) {
          el.style.left = item.left;
          el.style.top = item.top;
          el.style.width = item.width;
          el.style.height = item.height;
        }
      });
    }
    applyStructuredFields(state.structuredFields);
    applyAmenityIconFields(state.amenityIconFields || state.icons);
    applyAgencyLogoState(state.agencyLogos);
    applyMapState(state.mapState || state.mapFields || state.exactMap);
    updateScale();
  }

  function simpleHash(str) {
    var hash = 0;
    for (var i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash) + str.charCodeAt(i);
      hash |= 0;
    }
    return String(hash);
  }

  function requestSave() {
    if (document.body.classList.contains('export-clean')) return;
    setStatus('Unsaved');
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(saveNow, 600);
  }

  function saveNow() {
    if (document.body.classList.contains('export-clean')) return;
    if (!projectId) return;
    var state = serialize();
    var hash = simpleHash(JSON.stringify(state));
    if (hash === lastHash) {
      setStatus('Saved');
      return;
    }
    setStatus('Saving...');
    fetch('/api/projects/' + projectId + '/state', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state)
    }).then(function (res) {
      if (res.ok) {
        lastHash = hash;
        setStatus('Saved');
      } else {
        setStatus('Save failed');
      }
    }).catch(function () { setStatus('Save failed'); });
  }

  function restore() {
    if (window.__EXACT_EMBEDDED_STATE__) {
      applyState(window.__EXACT_EMBEDDED_STATE__);
      lastHash = simpleHash(JSON.stringify(serialize()));
      setStatus('Saved');
      return;
    }
    if (!projectId) return;
    fetch('/api/projects/' + projectId + '/state')
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (state) {
        if (state) applyState(state);
        lastHash = simpleHash(JSON.stringify(serialize()));
        setStatus('Saved');
      })
      .catch(function () { setStatus('Ready'); });
  }

  function updateFitButtons(slot) {
    slot.querySelectorAll('[data-fit]').forEach(function (button) {
      button.classList.toggle('is-active', button.dataset.fit === slot.dataset.fit);
    });
  }

  document.getElementById('accentColour').addEventListener('input', function () {
    document.getElementById('colourPreset').value = 'custom';
    applyColours();
    requestSave();
  });
  document.getElementById('darkColour').addEventListener('input', function () {
    document.getElementById('colourPreset').value = 'custom';
    applyColours();
    requestSave();
  });
  document.getElementById('colourPreset').addEventListener('change', function (event) {
    setColourPreset(event.target.value);
    requestSave();
  });
  document.getElementById('logoSelect').addEventListener('change', function (event) {
    applyLogo(event.target.value);
    syncGlobalLogoControls();
    requestSave();
  });
  document.getElementById('logoUpload').addEventListener('change', function (event) {
    var file = event.target.files && event.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function (readerEvent) {
      uploadedLogoDataUrl = readerEvent.target.result;
      document.getElementById('logoSelect').value = 'upload';
      applyLogo('upload');
      syncGlobalLogoControls();
      requestSave();
    };
    reader.readAsDataURL(file);
  });
  document.getElementById('logoSize').addEventListener('input', function () {
    applyLogo(document.getElementById('logoSelect').value);
    syncGlobalLogoControls();
    requestSave();
  });
  document.getElementById('logoPosition').addEventListener('change', function () {
    applyLogo(document.getElementById('logoSelect').value);
    syncGlobalLogoControls();
    requestSave();
  });
  document.getElementById('zoomRange').addEventListener('input', function () {
    updateScale();
    requestSave();
  });
  document.getElementById('pictureLayout').addEventListener('change', function (event) {
    applyPictureLayout(event.target.value);
    requestSave();
  });
  document.querySelectorAll('[data-layout-mode]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      applyLayoutMode(btn.dataset.layoutMode);
      requestSave();
    });
  });
  function textUsesHotspotReveal(el) {
    if (!el) return false;
    if (el.dataset.interactionSuppressed === 'true') return false;
    if (String(el.getAttribute('contenteditable') || '').toLowerCase() === 'false') return false;
    var page = el.closest && el.closest('.exact-page');
    return el.dataset.ocrFallback === 'true' || Boolean(page && page.dataset.sourcePreservedEdit === 'true');
  }
  document.addEventListener('pointerdown', function (event) {
    document.querySelectorAll('.pdf-text[data-active-edit="true"]:not([data-edited="true"])').forEach(function (el) {
      if (event.target === el || el.contains(event.target)) return;
      delete el.dataset.activeEdit;
    });
  }, true);

  document.querySelectorAll('.pdf-text').forEach(function (el) {
    applyTextTypography(el);
    if (textUsesHotspotReveal(el)) {
      var activateTextEdit = function () {
        el.dataset.activeEdit = 'true';
      };
      var deactivateTextEdit = function () {
        if (el.dataset.edited !== 'true') delete el.dataset.activeEdit;
      };
      el.addEventListener('pointerdown', activateTextEdit);
      el.addEventListener('focus', activateTextEdit);
      el.addEventListener('blur', deactivateTextEdit);
      el.addEventListener('focusout', deactivateTextEdit);
    }
    el.addEventListener('paste', function (event) {
      event.preventDefault();
      var clipboard = event.clipboardData || window.clipboardData;
      var text = clipboard ? clipboard.getData('text/plain') : '';
      insertPlainTextAtSelection(text);
      normaliseEditedTextMarkup(el);
      var original = el.getAttribute('data-original-html') || '';
      el.dataset.edited = el.innerHTML === original ? 'false' : 'true';
      requestSave();
    });
    el.addEventListener('input', function () {
      normaliseEditedTextMarkup(el);
      var original = el.getAttribute('data-original-html') || '';
      el.dataset.edited = el.innerHTML === original ? 'false' : 'true';
      requestSave();
    });
  });
  document.querySelectorAll('[data-typography-role-field]').forEach(function (select) {
    select.addEventListener('change', function () {
      applyTypographySettings(collectTypographySettings());
      requestSave();
    });
  });
  document.querySelectorAll('[data-global-icon-field]').forEach(function (input) {
    input.addEventListener('input', function () {
      if (input.dataset.globalIconField === 'color') input.dataset.custom = 'true';
      applyGlobalIconStyle(collectGlobalIconStyle());
      requestSave();
    });
  });
  document.querySelectorAll('.exact-image-slot').forEach(function (slot) {
    updateFitButtons(slot);
    var slotInput = slot.querySelector('input');
    if (slotInput) slotInput.addEventListener('change', function (event) {
      var file = event.target.files && event.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function (readerEvent) {
        setSlotImage(slot, readerEvent.target.result);
        requestSave();
      };
      reader.readAsDataURL(file);
    });
    slot.querySelectorAll('[data-fit]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        setSlotFit(slot, button.dataset.fit);
        requestSave();
      });
    });
  });
  document.querySelectorAll('[data-space-plan-upload-field]').forEach(function (input) {
    input.addEventListener('change', function (event) {
      var key = input.dataset.spacePlanUploadField;
      var slot = document.querySelector('[data-source-plan-slot="' + key + '"]');
      var file = event.target.files && event.target.files[0];
      if (!slot || !file) return;
      var reader = new FileReader();
      reader.onload = function (readerEvent) {
        setSlotImage(slot, readerEvent.target.result);
        requestSave();
      };
      reader.readAsDataURL(file);
    });
  });
  document.querySelectorAll('[data-space-plan-reset-field]').forEach(function (button) {
    button.addEventListener('click', function () {
      var key = button.dataset.spacePlanResetField;
      var slot = document.querySelector('[data-source-plan-slot="' + key + '"]');
      if (!slot) return;
      var source = slot.dataset.sourceImage || '';
      if (source) setSlotImage(slot, source);
      setSlotFit(slot, 'contain');
      requestSave();
    });
  });
  window.addEventListener('resize', updateScale);
  window.addEventListener('beforeunload', function () {
    if (document.body.classList.contains('export-clean')) return;
    var state = serialize();
    navigator.sendBeacon('/api/projects/' + projectId + '/state', new Blob([JSON.stringify(state)], { type: 'application/json' }));
  });
  if (!hasSourceLogoSlots()) ensureLogos();
  setupGlobalLogoFields();
  setupStructuredFields();
  setupAmenityIconFields();
  setupAgencyLogoFields();
  setupExactMapControls();
  applyColours();
  applyPictureLayout('original');
  applyLayoutMode('editable');
  updateScale();
  restore();
}());
"""


def _normalise_font_fallbacks(css_body: str) -> str:
    return re.sub(
        r"font-family:([^;]+);",
        lambda match: f"font-family:{_font_family_stack(match.group(1))};",
        css_body,
    )


def _parse_font_style_declarations(css_body: str) -> dict[str, Any]:
    declarations: dict[str, str] = {}
    for key, value in re.findall(r"([A-Za-z-]+)\s*:\s*([^;]+)", css_body):
        declarations[key.lower()] = value.strip()

    source_family = _unquote_font_family(declarations.get("font-family", ""))
    stable_family = _stable_font_family(source_family)
    font_size = declarations.get("font-size", "")
    line_height = declarations.get("line-height", "")
    letter_spacing = declarations.get("letter-spacing", "")
    font_weight = declarations.get("font-weight", "")
    font_style = declarations.get("font-style", "")
    transform = _first_transform_declaration(declarations)

    parsed_size = _parse_px(font_size)
    return {
        "source_font_family": source_family,
        "stable_font_family": stable_family or source_family,
        "font_size": font_size,
        "font_size_px": parsed_size,
        "line_height": line_height,
        "letter_spacing": letter_spacing,
        "font_weight": font_weight,
        "font_style": font_style,
        "color": declarations.get("color", ""),
        "transform": transform,
        "transform_scale": _transform_matrix_scale(transform),
    }


def _first_transform_declaration(declarations: dict[str, str]) -> str:
    for key in ("transform", "-webkit-transform", "-moz-transform", "-ms-transform", "-o-transform"):
        value = declarations.get(key)
        if value:
            return value
    return ""


def _transform_matrix_scale(value: str) -> float:
    matrix = re.search(r"matrix\(([^)]*)\)", value or "", flags=re.IGNORECASE)
    if not matrix:
        return 0.0
    raw_numbers = [part.strip() for part in matrix.group(1).split(",")]
    if len(raw_numbers) < 4:
        return 0.0
    try:
        a, b, c, d = (float(raw_numbers[index]) for index in range(4))
    except (TypeError, ValueError):
        return 0.0
    return max((a * a + b * b) ** 0.5, (c * c + d * d) ** 0.5)


def _unquote_font_family(value: str) -> str:
    family = value.split(",", 1)[0] if value else ""
    return family.strip().strip("'\"")


def _stable_font_family(value: str) -> str:
    family = _unquote_font_family(value)
    previous = None
    while family and family != previous:
        previous = family
        family = re.sub(r"^[A-Z]{6}\+", "", family)
    return family


def _parse_px(value: str) -> float:
    match = re.search(r"(-?[0-9]+(?:\.[0-9]+)?)px", value or "")
    return float(match.group(1)) if match else 0.0


def _font_family_stack(value: str) -> str:
    source = _unquote_font_family(value)
    stable = _stable_font_family(source)
    families: list[str] = []
    if stable:
        families.append(stable)
    if source and source != stable:
        families.append(source)
    return ", ".join([_quote_font_family(family) for family in families] + ["Arial", "sans-serif"])


def _css_font_stack(family: str | None) -> str:
    if not family:
        return "inherit"
    return f"{_quote_font_family(str(family))}, Arial, sans-serif"


def _quote_font_family(value: str) -> str:
    family = value.strip().strip("'\"")
    return f"'{_css_escape_family(family)}'"


def _colour_role(colour: str) -> str:
    lower = colour.lower()
    if lower == "#ffea00":
        return "accent"
    if lower in {"#333132", "#282827", "#000000", "#1d1d1b", "#6e645f"}:
        return "dark"
    match = re.fullmatch(r"#([0-9a-f]{6})", lower)
    if match:
        red = int(match.group(1)[0:2], 16)
        green = int(match.group(1)[2:4], 16)
        blue = int(match.group(1)[4:6], 16)
        if max(red, green, blue) <= 96:
            return "dark"
    if lower == "#ffffff":
        return "light"
    return "body"


def _css_escape_family(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
