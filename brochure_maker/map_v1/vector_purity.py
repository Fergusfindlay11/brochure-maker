"""Vector purity checks for SVG and PDF map artifacts."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import fitz

from .errors import ExportError


def check_svg_vector_purity(svg: str, *, allow_images: bool = False) -> bool:
    try:
        root = ET.fromstring(svg.encode("utf-8"))
    except Exception as exc:
        raise ExportError("invalid SVG output") from exc

    for element in root.iter():
        tag = element.tag.split("}")[-1].lower()
        if tag == "image" and not allow_images:
            return False
    return True


def check_pdf_vector_purity(pdf_bytes: bytes) -> bool:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ExportError("invalid PDF output") from exc

    has_text = False
    has_vector = False
    full_page_raster_pages = 0

    for page in doc:
        page_rect = page.rect
        page_area = max(1.0, page_rect.width * page_rect.height)

        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            if block.get("type") == 0 and str(block.get("text", "")).strip():
                has_text = True
                break
        if not has_text:
            for block in text_dict.get("blocks", []):
                if block.get("type") == 0:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if str(span.get("text", "")).strip():
                                has_text = True
                                break
                        if has_text:
                            break
                    if has_text:
                        break

        drawings = page.get_drawings()
        if drawings:
            has_vector = True

        images = page.get_images(full=True)
        if images:
            max_ratio = 0.0
            for image in images:
                xref = int(image[0])
                for rect in page.get_image_rects(xref):
                    area = max(0.0, rect.width * rect.height)
                    max_ratio = max(max_ratio, area / page_area)
            if max_ratio >= 0.95:
                full_page_raster_pages += 1

    if full_page_raster_pages == doc.page_count and not has_vector:
        return False
    if not has_vector:
        return False
    if not has_text:
        return False
    return True


def assert_vector_purity(svg: str, pdf_bytes: bytes) -> dict[str, bool]:
    svg_ok = check_svg_vector_purity(svg)
    if not svg_ok:
        raise ExportError(
            "SVG vector purity failed: embedded raster image detected",
            hint="Remove SVG <image> layers from map render path.",
        )

    pdf_ok = check_pdf_vector_purity(pdf_bytes)
    if not pdf_ok:
        raise ExportError(
            "PDF vector purity failed: rasterized output or missing text/vector objects",
            hint="Ensure rsvg-convert path and text/halo rendering modes remain vector-safe.",
        )

    return {"svg_ok": svg_ok, "pdf_ok": pdf_ok}
