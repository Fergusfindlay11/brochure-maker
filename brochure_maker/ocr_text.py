"""Small deterministic OCR fallback for outline-only PDF text.

Some marketing PDFs convert cover lettering to vector outlines. PyMuPDF and
Poppler then correctly report no text, but the editor still needs a semantic
text layer. This module keeps the fallback local and optional: if Tesseract is
not installed, extraction simply returns no OCR lines.
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image


def extract_ocr_text_lines(
    image_path: str | Path,
    *,
    page_number: int,
    page_width: float,
    page_height: float,
    psm: int = 4,
    min_confidence: float = 45.0,
) -> list[dict[str, Any]]:
    """Return line-level OCR boxes in the caller's coordinate system."""
    executable = shutil.which("tesseract")
    path = Path(image_path)
    if not executable or not path.is_file() or page_width <= 0 or page_height <= 0:
        return []

    try:
        result = subprocess.run(
            [executable, str(path), "stdout", "--psm", str(psm), "tsv"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []

    try:
        with Image.open(path) as image:
            image_width, image_height = image.size
    except Exception:
        image_width, image_height = int(page_width), int(page_height)
    scale_x = page_width / max(1.0, float(image_width))
    scale_y = page_height / max(1.0, float(image_height))

    rows = _parse_tesseract_tsv(result.stdout, min_confidence=min_confidence)
    lines: list[dict[str, Any]] = []
    for line_index, words in enumerate(_group_ocr_words_by_line(rows), start=1):
        text = _repair_ocr_text(" ".join(str(word["text"]) for word in words))
        if not _usable_ocr_line(text):
            continue
        confidence_values = [float(word["conf"]) for word in words]
        left = min(float(word["left"]) for word in words)
        top = min(float(word["top"]) for word in words)
        right = max(float(word["left"]) + float(word["width"]) for word in words)
        bottom = max(float(word["top"]) + float(word["height"]) for word in words)
        width = max(1.0, right - left)
        height = max(1.0, bottom - top)
        bbox = {
            "x": round(left * scale_x, 3),
            "y": round(top * scale_y, 3),
            "width": round(width * scale_x, 3),
            "height": round(height * scale_y, 3),
        }
        lines.append(
            {
                "id": f"p{page_number:03d}-ocr-text-{line_index:04d}",
                "text": text,
                "bbox": bbox,
                "confidence": round(sum(confidence_values) / max(1, len(confidence_values)), 3),
                "font_size": round(max(10.0, bbox["height"] * 0.92), 3),
                "role": _ocr_typography_role(page_number, text, bbox),
                "extraction_method": "Tesseract OCR fallback for outline-only PDF text",
            }
        )
    return lines


def _parse_tesseract_tsv(tsv: str, *, min_confidence: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    for row in reader:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or -1)
            left = float(row.get("left") or 0)
            top = float(row.get("top") or 0)
            width = float(row.get("width") or 0)
            height = float(row.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if confidence < min_confidence or width <= 1 or height <= 1:
            continue
        rows.append(
            {
                "block_num": str(row.get("block_num") or "0"),
                "par_num": str(row.get("par_num") or "0"),
                "line_num": str(row.get("line_num") or "0"),
                "word_num": int(float(row.get("word_num") or 0)),
                "text": text,
                "conf": confidence,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }
        )
    return rows


def _group_ocr_words_by_line(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["block_num"]), str(row["par_num"]), str(row["line_num"]))
        grouped.setdefault(key, []).append(row)
    return [
        sorted(words, key=lambda word: (float(word["left"]), int(word["word_num"])))
        for _key, words in sorted(
            grouped.items(),
            key=lambda item: (
                min(float(word["top"]) for word in item[1]),
                min(float(word["left"]) for word in item[1]),
            ),
        )
    ]


def _repair_ocr_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    # Tesseract often reads a leading "1" in address ranges as "(" on geometric
    # cover lettering, e.g. "(5-19 BRITTEN ST" instead of "15-19 BRITTEN ST".
    text = re.sub(r"^\((\d+-\d+\b)", r"1\1", text)
    return text


def _usable_ocr_line(text: str) -> bool:
    if not text:
        return False
    if len(text) > 96:
        return False
    if not re.search(r"[A-Za-z0-9]", text):
        return False
    if re.fullmatch(r"[\\/|_.-]+", text):
        return False
    return True


def _ocr_typography_role(page_number: int, text: str, bbox: dict[str, float]) -> str:
    compact = re.sub(r"[^A-Za-z0-9]+", "", text or "")
    height = float(bbox.get("height") or 0)
    if page_number == 1 and height >= 42 and len(compact) >= 4:
        return "cover-title"
    if height >= 34 and text.upper() == text:
        return "section-heading"
    if height <= 14:
        return "caption"
    return "body"
