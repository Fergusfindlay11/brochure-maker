"""Extract text, images, colours, and page renders from a PDF brochure."""

import base64
import io
import os
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image


def extract_pdf(pdf_path: str, project_dir: str) -> dict:
    """Extract all content from a PDF brochure.

    Returns a structured dict with page-level data including text blocks,
    image paths, page renders, and dominant colours.
    """
    doc = fitz.open(pdf_path)
    images_dir = os.path.join(project_dir, "images")
    renders_dir = os.path.join(project_dir, "renders")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(renders_dir, exist_ok=True)

    pages = []
    all_colours = Counter()

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_data = _extract_page(page, page_num, images_dir, renders_dir, all_colours)
        pages.append(page_data)

    # Top colours across entire document
    top_colours = [colour for colour, _ in all_colours.most_common(10)]

    doc.close()
    return {
        "page_count": len(pages),
        "pages": pages,
        "dominant_colours": top_colours,
    }


def _extract_page(
    page: fitz.Page,
    page_num: int,
    images_dir: str,
    renders_dir: str,
    all_colours: Counter,
) -> dict:
    """Extract data from a single PDF page."""
    rect = page.rect
    width, height = rect.width, rect.height

    # Extract text blocks with position and style info
    text_blocks = _extract_text_blocks(page)

    # Extract embedded images
    image_paths = _extract_images(page, page_num, images_dir)

    # Render page as PNG for AI visual analysis
    render_path = _render_page(page, page_num, renders_dir)
    render_b64 = _render_page_base64(page)

    # Extract dominant colours from the page render
    page_colours = _extract_colours(page)
    all_colours.update(page_colours)

    return {
        "page_num": page_num + 1,
        "width": width,
        "height": height,
        "text_blocks": text_blocks,
        "images": image_paths,
        "render_path": render_path,
        "render_base64": render_b64,
        "colours": [c for c, _ in Counter(page_colours).most_common(5)],
    }


def _extract_text_blocks(page: fitz.Page) -> list:
    """Extract text with position, font, size, and colour information."""
    blocks = []
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # text block
            continue

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue

                # Convert colour int to hex
                colour_int = span.get("color", 0)
                colour_hex = "#{:06x}".format(colour_int)

                blocks.append({
                    "text": text,
                    "bbox": list(span.get("bbox", [0, 0, 0, 0])),
                    "font": span.get("font", ""),
                    "size": round(span.get("size", 12), 1),
                    "colour": colour_hex,
                    "flags": span.get("flags", 0),
                })

    return blocks


def _extract_images(page: fitz.Page, page_num: int, images_dir: str) -> list[str]:
    """Extract embedded images from the page."""
    paths = []
    image_list = page.get_images(full=True)

    for img_idx, img_info in enumerate(image_list):
        xref = img_info[0]
        try:
            base_image = page.parent.extract_image(xref)
            if not base_image:
                continue

            image_bytes = base_image["image"]
            ext = base_image.get("ext", "png")

            # Skip tiny images (likely decorative/icons)
            width = base_image.get("width", 0)
            height = base_image.get("height", 0)
            if width < 50 or height < 50:
                continue

            filename = f"page{page_num + 1}_img{img_idx + 1}.{ext}"
            filepath = os.path.join(images_dir, filename)
            with open(filepath, "wb") as f:
                f.write(image_bytes)
            paths.append(filepath)
        except Exception:
            continue

    return paths


def _render_page(page: fitz.Page, page_num: int, renders_dir: str, dpi: int = 150) -> str:
    """Render page as PNG file."""
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)

    filename = f"page{page_num + 1}.png"
    filepath = os.path.join(renders_dir, filename)
    pix.save(filepath)
    return filepath


def _render_page_base64(page: fitz.Page, dpi: int = 150) -> str:
    """Render page as base64 encoded PNG for sending to AI."""
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)

    img_bytes = pix.tobytes("png")
    return base64.b64encode(img_bytes).decode("utf-8")


def _extract_colours(page: fitz.Page) -> list[str]:
    """Extract dominant colours from a page render."""
    # Render at low res for colour sampling
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
    img_bytes = pix.tobytes("png")

    img = Image.open(io.BytesIO(img_bytes))
    img = img.convert("RGB")

    # Resize to small for fast colour counting
    img_small = img.resize((80, 80), Image.Resampling.LANCZOS)
    pixels = list(img_small.getdata())

    # Quantise to reduce noise
    quantised = []
    for r, g, b in pixels:
        qr = (r // 16) * 16
        qg = (g // 16) * 16
        qb = (b // 16) * 16
        hex_colour = "#{:02x}{:02x}{:02x}".format(qr, qg, qb)
        quantised.append(hex_colour)

    return quantised
