"""Deterministic hashing helpers for SVG and PDF artifacts."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

import fitz

from .errors import DeterminismViolationError, ExportError
from .utils import canonical_json

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _fmt_num(value: str, precision: int = 4) -> str:
    try:
        number = float(value)
    except Exception:
        return value
    rendered = f"{number:.{precision}f}"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _normalize_numbers(text: str, precision: int = 4) -> str:
    if not text:
        return text

    def repl(match: re.Match[str]) -> str:
        return _fmt_num(match.group(0), precision=precision)

    return _NUM_RE.sub(repl, text)


def _canonicalize_element(el: ET.Element) -> None:
    if el.attrib:
        normalized: dict[str, str] = {}
        for key in sorted(el.attrib.keys()):
            normalized[key] = _normalize_numbers(el.attrib[key], precision=4)
        el.attrib.clear()
        el.attrib.update(normalized)

    if el.text:
        el.text = el.text.strip()
    if el.tail:
        el.tail = el.tail.strip()

    for child in list(el):
        _canonicalize_element(child)


def canonical_svg(svg: str) -> bytes:
    try:
        root = ET.fromstring(svg.encode("utf-8"))
    except Exception as exc:
        raise DeterminismViolationError("invalid SVG produced by renderer") from exc
    _canonicalize_element(root)
    return ET.tostring(root, encoding="utf-8", method="xml")


def svg_hash(svg: str) -> str:
    canonical = canonical_svg(svg)
    return hashlib.sha256(canonical).hexdigest()


def _round_tuple(values: tuple[float, ...], precision: int = 3) -> tuple[float, ...]:
    return tuple(round(float(v), precision) for v in values)


def pdf_structural_hash(pdf_bytes: bytes) -> str:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ExportError("generated PDF is invalid") from exc

    model: list[dict[str, object]] = []
    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_data: dict[str, object] = {
            "index": page_index,
            "size": _round_tuple((page.rect.width, page.rect.height)),
            "text": [],
            "drawings": [],
            "images": [],
        }

        text_dict = page.get_text("dict")
        text_spans: list[dict[str, object]] = []
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span_text = str(span.get("text", "")).strip()
                    if not span_text:
                        continue
                    text_spans.append(
                        {
                            "text": span_text,
                            "font": span.get("font", ""),
                            "size": round(float(span.get("size", 0.0)), 3),
                            "bbox": _round_tuple(tuple(span.get("bbox", (0, 0, 0, 0)))),
                        }
                    )
        text_spans.sort(key=lambda item: (item["font"], item["size"], item["text"], item["bbox"]))
        page_data["text"] = text_spans

        drawings = []
        for draw in page.get_drawings():
            rect = draw.get("rect")
            if rect is None:
                continue
            width = draw.get("width", 0.0)
            try:
                width_v = round(float(width or 0.0), 3)
            except Exception:
                width_v = 0.0
            drawings.append(
                {
                    "fill": str(draw.get("fill", "")),
                    "color": str(draw.get("color", "")),
                    "width": width_v,
                    "rect": _round_tuple((rect.x0, rect.y0, rect.x1, rect.y1)),
                }
            )
        drawings.sort(key=lambda item: (item["width"], item["fill"], item["color"], item["rect"]))
        page_data["drawings"] = drawings

        images = []
        for image in page.get_images(full=True):
            xref = int(image[0])
            for rect in page.get_image_rects(xref):
                images.append({"xref": xref, "rect": _round_tuple((rect.x0, rect.y0, rect.x1, rect.y1))})
        images.sort(key=lambda item: (item["xref"], item["rect"]))
        page_data["images"] = images
        model.append(page_data)

    serialized = canonical_json(model).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
