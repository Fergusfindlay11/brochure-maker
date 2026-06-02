"""Build a renderer-ready editable layout model from an existing PDF.

The model produced here is intentionally close to PDF coordinates: page units
are points, bboxes use top-left PDF page coordinates, and extracted assets are
written beside rendered page backgrounds for a later HTML editor/renderer.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import fitz  # PyMuPDF
from PIL import Image

from brochure_maker.image_region_classifier import classify_model_page_image_regions
from brochure_maker.ocr_text import extract_ocr_text_lines


DEFAULT_RENDER_DPI = 144
DEFAULT_MAX_COLORS = 8
TEXT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_IMAGES


def extract_exact_layout(
    pdf_path: str | Path,
    output_dir: str | Path,
    *,
    render_dpi: int = DEFAULT_RENDER_DPI,
    max_colors: int = DEFAULT_MAX_COLORS,
    min_image_area: float = 1.0,
) -> dict[str, Any]:
    """Extract an editable, exact-layout model from a PDF.

    Args:
        pdf_path: Source PDF to inspect.
        output_dir: Directory where page renders and embedded images are saved.
        render_dpi: DPI for page background PNGs.
        max_colors: Number of colors to keep for dominant/background/accent sets.
        min_image_area: Minimum image placement area in PDF points to persist.

    Returns:
        A JSON-serializable dict with page sizes, text spans, image boxes, color
        summaries, and paths to rendered page background images.
    """
    source_path = Path(pdf_path).expanduser().resolve()
    target_dir = Path(output_dir).expanduser().resolve()
    backgrounds_dir = target_dir / "backgrounds"
    images_dir = target_dir / "images"
    backgrounds_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    document_dominant: Counter[str] = Counter()
    document_background: Counter[str] = Counter()
    document_accent: Counter[str] = Counter()
    pages: list[dict[str, Any]] = []

    with fitz.open(source_path) as doc:
        for page_index, page in enumerate(doc):
            page_model = _extract_page_layout(
                page=page,
                page_index=page_index,
                backgrounds_dir=backgrounds_dir,
                images_dir=images_dir,
                render_dpi=render_dpi,
                max_colors=max_colors,
                min_image_area=min_image_area,
            )
            pages.append(page_model)
            document_dominant.update(page_model["colors"]["dominant"])
            document_background.update(page_model["colors"]["background"])
            document_accent.update(page_model["colors"]["accent"])

    model = {
        "schema": "brochure-maker.pdf-exact-layout.v1",
        "source_pdf": str(source_path),
        "output_dir": str(target_dir),
        "page_count": len(pages),
        "pages": pages,
        "colors": {
            "dominant": _top_counter_values(document_dominant, max_colors),
            "background": _top_counter_values(document_background, max_colors),
            "accent": _top_counter_values(document_accent, max_colors),
        },
        "coordinate_system": {
            "unit": "pt",
            "origin": "top-left",
            "bbox": "x, y, width, height",
        },
    }
    model["inventory"] = build_extraction_inventory(model)
    return model


def write_exact_layout_model(
    pdf_path: str | Path,
    output_dir: str | Path,
    *,
    model_filename: str = "exact-layout.json",
    render_dpi: int = DEFAULT_RENDER_DPI,
    max_colors: int = DEFAULT_MAX_COLORS,
    min_image_area: float = 1.0,
) -> dict[str, Any]:
    """Extract the exact layout model and write it as JSON.

    The returned dict includes a ``model_path`` field pointing at the JSON file.
    """
    model = extract_exact_layout(
        pdf_path,
        output_dir,
        render_dpi=render_dpi,
        max_colors=max_colors,
        min_image_area=min_image_area,
    )
    model_path = Path(output_dir).expanduser().resolve() / model_filename
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model["model_path"] = str(model_path)
    inventory_path = model_path.with_name("extraction-inventory.json")
    model["inventory_path"] = str(inventory_path)
    model_path.write_text(json.dumps(model, indent=2, sort_keys=True), encoding="utf-8")
    inventory_path.write_text(json.dumps(model["inventory"], indent=2, sort_keys=True), encoding="utf-8")
    return model


def build_extraction_inventory(model: dict[str, Any]) -> dict[str, Any]:
    """Build a semantic page inventory from the PDF evidence model.

    The inventory is deliberately evidence-first: it records what the reusable
    extraction pipeline found, what should become editable, and which extractor
    method is most appropriate for each brochure system.
    """
    pages = model.get("pages") if isinstance(model.get("pages"), list) else []
    inventory_pages = [_inventory_page(page, index, len(pages)) for index, page in enumerate(pages)]
    global_typography = _global_typography_roles(inventory_pages)
    global_palette = _global_palette_roles(model)
    feature_counts = Counter(
        feature
        for page in inventory_pages
        for feature in page.get("detected_features", [])
    )
    return {
        "schema": "brochure-maker.extraction-inventory.v1",
        "source_pdf": model.get("source_pdf"),
        "page_count": len(inventory_pages),
        "global_systems": {
            "palette": global_palette,
            "typography": global_typography,
            "logo": {
                "controls": [
                    "logo type/upload",
                    "source facade mark replacement",
                    "repeated header mark replacement",
                    "logo size",
                    "logo position",
                    "logo colour",
                ],
                "extraction_methods": ["vector detection", "raster component detection", "colour sampling"],
            },
            "amenity_icons": {
                "controls": ["icon bank", "global icon colour", "global icon size", "global icon stroke width"],
                "extraction_methods": ["PDF text labels", "vector detection", "semantic inference"],
            },
            "images": {
                "controls": ["picture layout mode", "image fit/crop mode", "space-plan replacement"],
                "extraction_methods": ["PyMuPDF images", "raster component detection", "semantic inference"],
            },
            "map": {
                "controls": [
                    "regenerate/preserve map",
                    "map label colour",
                    "POI category colours",
                    "subject marker style",
                ],
                "extraction_methods": ["PDF text", "vector detection", "colour sampling", "semantic inference"],
            },
            "agents": {
                "controls": ["agency logo replacement", "contact typography", "contact text colour"],
                "extraction_methods": ["PDF text regex", "nearby logo region detection", "semantic inference"],
            },
            "feature_counts": dict(sorted(feature_counts.items())),
        },
        "pages": inventory_pages,
    }


def _inventory_page(page: dict[str, Any], page_index: int, page_count: int) -> dict[str, Any]:
    page_number = int(page.get("page_number") or page_index + 1)
    text_spans = page.get("text_spans") if isinstance(page.get("text_spans"), list) else []
    image_boxes = page.get("image_boxes") if isinstance(page.get("image_boxes"), list) else []
    image_regions = page.get("image_regions") if isinstance(page.get("image_regions"), list) else []
    semantic_regions = page.get("semantic_regions") if isinstance(page.get("semantic_regions"), list) else []
    page_text = " ".join(str(span.get("text") or "") for span in text_spans)
    compact = _compact_semantic_text(page_text)
    text_space_plan_regions = _space_plan_inventory(page_number, compact, image_boxes)
    image_space_plan_regions = _space_plan_inventory_from_image_regions(page_number, image_regions)
    purpose = _infer_page_purpose(compact, page_number, page_count, text_spans, image_boxes, semantic_regions, image_regions)
    if _suppresses_contact_page_space_plan(purpose, compact, semantic_regions) or _suppresses_plan_reference_schedule_space_plan(compact, text_spans):
        text_space_plan_regions = []
        image_space_plan_regions = []
        image_regions = [region for region in image_regions if not (isinstance(region, dict) and region.get("role") == "space-plan")]
        purpose = _remove_purpose_features(purpose, {"space_plan", "floor_metadata"})
    space_plan_regions = text_space_plan_regions + image_space_plan_regions
    space_plan_candidate_ids = {
        str(candidate)
        for region in space_plan_regions
        if isinstance(region, dict)
        for candidate in region.get("candidate_image_regions") or []
    }
    photo_image_boxes = [
        image
        for image in image_boxes
        if str(image.get("id") or "") not in space_plan_candidate_ids
        and _image_box_region_role(image, image_regions) in {"", "photo-region", "photo-grid", "hero-photo"}
    ]
    purpose = _normalise_photo_region_feature(purpose, has_photo_regions=bool(photo_image_boxes))
    cover_title_glyph_ids = _cover_vertical_title_glyph_ids(page_number, text_spans)
    typography = [
        _inventory_typography_role(role, spans)
        for role, spans in _group_spans_by_typography_role(page_number, text_spans, cover_title_glyph_ids=cover_title_glyph_ids).items()
    ]
    editable_text_blocks = [
        {
            "id": span.get("id"),
            "text": span.get("text"),
            "bbox": span.get("bbox"),
            "typography_role": "title" if str(span.get("id") or "") in cover_title_glyph_ids else _span_typography_role(page_number, span),
            "font": span.get("font"),
            "color": span.get("color"),
            "editable": True,
            "extraction_method": "PDF text",
        }
        for span in text_spans
    ]
    photo_regions = [
        {
            "id": image.get("id"),
            "bbox": image.get("bbox"),
            "path": image.get("path"),
            "source_size": {
                "width": image.get("source_width"),
                "height": image.get("source_height"),
            },
            "editable": True,
            "extraction_method": image.get("extraction_method") or "PyMuPDF images",
        }
        for image in photo_image_boxes
    ]
    map_region = next((region for region in semantic_regions if region.get("kind") == "map"), None)
    if "map" not in set(purpose.get("features") or []):
        map_region = None
    contacts_region = next((region for region in semantic_regions if region.get("kind") == "contacts"), None)
    amenities_region = next((region for region in semantic_regions if region.get("kind") == "amenities"), None)
    return {
        "page_number": page_number,
        "page_purpose": purpose["purpose"],
        "layout_type": purpose["layout_type"],
        "confidence": purpose["confidence"],
        "size": page.get("size"),
        "background_colour_roles": _page_palette_roles(page),
        "typography_roles": typography,
        "editable_text_blocks": editable_text_blocks,
        "image_regions": image_regions,
        "photo_regions": photo_regions,
        "source_logo_regions": _source_logo_inventory(page_number, purpose, page, text_spans),
        "repeated_brochure_logo_positions": _repeated_logo_inventory(page_number, purpose, page),
        "amenity_icon_regions": _amenity_inventory(amenities_region, text_spans),
        "map_regions": _map_inventory(map_region, text_spans),
        "space_plan_regions": space_plan_regions,
        "agent_contact_blocks": _contact_inventory(contacts_region, text_spans),
        "static_elements": _static_elements_for_page(purpose, bool(map_region)),
        "editable_elements": _editable_elements_for_page(purpose, bool(amenities_region), bool(map_region), bool(contacts_region), bool(photo_image_boxes)),
        "detected_features": purpose["features"],
        "recommended_extraction_methods": _recommended_methods_for_features(purpose["features"]),
    }


def _infer_page_purpose(
    compact: str,
    page_number: int,
    page_count: int,
    text_spans: list[dict[str, Any]],
    image_boxes: list[dict[str, Any]],
    semantic_regions: list[dict[str, Any]],
    image_regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    region_kinds = {str(region.get("kind") or "") for region in semantic_regions}
    features: set[str] = set()
    purpose = "editorial"
    layout_type = "text"
    confidence = 0.55
    dense = re.sub(r"\s+", "", compact)

    if page_number == 1:
        purpose, layout_type, confidence = "cover", "dark cover with source mark", 0.82
        features.add("cover_title")
    elif _looks_like_cover_title_page(text_spans):
        purpose, layout_type, confidence = "editorial", "full-page image with chapter headings", max(confidence, 0.7)
        features.add("editable_text")
    if "floor" in compact and ("sqft" in compact or "sqm" in compact or "desks" in compact):
        purpose, layout_type, confidence = "floor plan and space plan", "photos plus plan metadata", max(confidence, 0.78)
        features.update({"space_plan", "floor_metadata", "photo_regions"})
    text_words = _semantic_words(" ".join(str(span.get("text") or "") for span in text_spans))
    if "amenities" in region_kinds:
        purpose, layout_type, confidence = "amenities", "amenity icon grid with photos", max(confidence, 0.82)
        features.update({"amenity_icons", "amenity_labels", "photo_regions"})
    service_headings = (
        "coreservices",
        "customisation",
        "customization",
        "managedoffice",
        "monthlyprice",
    )
    service_label_terms = (
        "dailycleaning",
        "maintenance",
        "healthsafety",
        "businessrates",
        "teacoffee",
        "healthysnacks",
        "foliage",
    )
    service_hit_count = sum(1 for token in service_label_terms if token in dense)
    service_grid_evidence = (
        any(token in dense for token in service_headings)
        or service_hit_count >= 3
    )
    if service_grid_evidence and not (page_count > 1 and page_number == page_count and "misrepresentationact" in compact):
        purpose, layout_type, confidence = "services and customisations", "service icon grid with contact panel", max(confidence, 0.78)
        features.update({"service_icons", "contact_text", "agency_logo"})
    location_intro = any(token in dense for token in ("position", "location", "neighbourhood", "neighborhood", "localarea", "amenitiesnearby"))
    if location_intro and image_boxes:
        purpose, layout_type, confidence = "location introduction", "photo montage with editorial panel", max(confidence, 0.78)
        features.update({"photo_regions", "location_copy", "map_context"})
        if "map" in region_kinds:
            features.update({"map", "map_labels", "transport_symbols", "subject_property_marker"})
    if not location_intro and "map" in region_kinds:
        purpose, layout_type, confidence = "connectivity map", "map labels with station summary", max(confidence, 0.84)
        features.update({"map", "map_labels", "transport_symbols", "subject_property_marker"})
    elif sum(token in dense for token in ("station", "transport", "underground", "rail", "metro", "minutewalk", "walk")) >= 2:
        features.add("map_context")
    is_final_terms_page = page_count > 1 and page_number == page_count
    is_contact_page = "contacts" in region_kinds and "service_icons" not in features
    if is_final_terms_page or is_contact_page or "misrepresentationact" in compact:
        has_agency_logo_evidence = _has_agency_logo_feature_evidence(
            semantic_regions,
            text_spans=text_spans,
            image_boxes=image_boxes,
            image_regions=image_regions or [],
        )
        layout_type = "agent contacts with agency logos" if has_agency_logo_evidence else "agent contacts and legal text"
        purpose, confidence = "contacts and terms", max(confidence, 0.82)
        features.update({"agent_contacts", "legal_copy"})
        if has_agency_logo_evidence:
            features.add("agency_logos")
    elif "contacts" in region_kinds:
        features.add("contact_text")
    if image_boxes:
        features.add("photo_regions")
    if not features:
        features.add("editable_text")
    return {"purpose": purpose, "layout_type": layout_type, "confidence": round(confidence, 2), "features": sorted(features)}


def _suppresses_contact_page_space_plan(purpose: dict[str, Any], compact: str, semantic_regions: list[dict[str, Any]]) -> bool:
    if purpose.get("purpose") != "contacts and terms":
        return False
    if any(token in compact for token in ("floorplan", "floorplans", "spaceplan", "spaceplans")):
        return False
    features = {str(feature) for feature in purpose.get("features") or []}
    region_kinds = {_compact_semantic_text(str(region.get("kind") or region.get("role") or "")) for region in semantic_regions}
    contact_or_terms = "legal_copy" in features or any(kind in region_kinds for kind in ("contacts", "agencylogos", "agentcontacts"))
    return contact_or_terms


def _has_agency_logo_feature_evidence(
    semantic_regions: list[dict[str, Any]],
    *,
    text_spans: list[dict[str, Any]],
    image_boxes: list[dict[str, Any]],
    image_regions: list[dict[str, Any]],
) -> bool:
    for region in image_regions:
        if str(region.get("role") or region.get("kind") or "") == "agency-logo":
            return True
    if any(_image_box_region_role(image, image_regions) == "agency-logo" for image in image_boxes):
        return True

    spans_by_id = {str(span.get("id") or ""): span for span in text_spans}
    for region in semantic_regions:
        kind = _compact_semantic_text(str(region.get("kind") or region.get("role") or ""))
        if kind not in {"agencylogos", "agencylogo"}:
            continue
        span_ids = [str(span_id) for span_id in region.get("span_ids") or []]
        region_spans = [spans_by_id[span_id] for span_id in span_ids if span_id in spans_by_id]
        if region_spans and _looks_like_agency_logo_text_band(region_spans):
            return True
        text_sample = str(region.get("text_sample") or "")
        if text_sample and _looks_like_agency_logo_text(text_sample):
            return True
    return False


def _suppresses_plan_reference_schedule_space_plan(compact: str, text_spans: list[dict[str, Any]]) -> bool:
    dense = re.sub(r"\s+", "", compact)
    schedule_context = any(token in dense for token in ("plannos", "draftdecisionletter", "decisionletter", "reference"))
    plan_terms = sum(1 for token in ("floorplan", "elevation", "elevations", "sections", "existing", "proposed") if token in dense)
    reference_rows = 0
    for span in text_spans:
        text = str(span.get("text") or "")
        if len(text) <= 42:
            continue
        row_compact = re.sub(r"\s+", "", _compact_semantic_text(text))
        has_plan_word = any(token in row_compact for token in ("floorplan", "elevation", "elevations", "section", "sections"))
        if not has_plan_word:
            continue
        if ";" in text or re.search(r"\b[A-Z0-9]{2,}(?:[-_][A-Z0-9]{1,}){3,}\b", text, flags=re.IGNORECASE):
            reference_rows += 1
    return schedule_context and plan_terms >= 3 and reference_rows >= 2


def _remove_purpose_features(purpose: dict[str, Any], features_to_remove: set[str]) -> dict[str, Any]:
    updated = dict(purpose)
    updated["features"] = sorted(feature for feature in purpose.get("features") or [] if str(feature) not in features_to_remove)
    return updated


def _normalise_photo_region_feature(purpose: dict[str, Any], *, has_photo_regions: bool) -> dict[str, Any]:
    """Keep inventory features aligned with semantic image classification.

    Floor-plan pages often contain embedded plan rasters rather than brochure
    photos. Those images should feed the space-plan replacement slot, not a
    separate photo-region requirement in the design graph/evaluator.
    """
    if has_photo_regions:
        return purpose
    features = [feature for feature in purpose.get("features") or [] if feature != "photo_regions"]
    return {**purpose, "features": sorted(features)}


def _page_palette_roles(page: dict[str, Any]) -> dict[str, Any]:
    colors = page.get("colors") if isinstance(page.get("colors"), dict) else {}
    background = colors.get("background") if isinstance(colors.get("background"), list) else []
    accent = colors.get("accent") if isinstance(colors.get("accent"), list) else []
    evidence = colors.get("evidence") if isinstance(colors.get("evidence"), dict) else {}
    text_colours = evidence.get("text") if isinstance(evidence.get("text"), list) else []
    dark = next((color for color in background if _is_dark_hex(color)), background[0] if background else "#000000")
    light = next((color for color in background if _is_light_hex(color)), "#ffffff")
    return {
        "dark_background": dark,
        "light_background": light,
        "accent": accent[0] if accent else "",
        "text_colours": text_colours[:6],
        "method": "colour sampling plus PDF text/vector paint evidence",
    }


def _group_spans_by_typography_role(
    page_number: int,
    spans: list[dict[str, Any]],
    *,
    cover_title_glyph_ids: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    cover_title_glyph_ids = cover_title_glyph_ids or _cover_vertical_title_glyph_ids(page_number, spans)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        span_id = str(span.get("id") or "")
        role = "title" if span_id and span_id in cover_title_glyph_ids else _span_typography_role(page_number, span)
        grouped.setdefault(role, []).append(span)
    return grouped


def _cover_vertical_title_glyph_ids(page_number: int, spans: list[dict[str, Any]]) -> set[str]:
    """Find cover title lettering extracted as separate rotated glyph spans.

    Some PDFs encode a vertical cover title by placing each rotated character as
    an individual text span. The glyphs share a font/style and line up in a few
    narrow columns with a large vertical spread, so classify the whole cluster as
    the title typography role rather than many section-heading fragments.
    """
    if page_number != 1:
        return set()

    clusters: dict[tuple[str, float, str], list[dict[str, Any]]] = {}
    for span in spans:
        span_id = str(span.get("id") or "")
        text = re.sub(r"\s+", "", str(span.get("text") or ""))
        bbox = span.get("bbox") if isinstance(span.get("bbox"), dict) else {}
        font = span.get("font") if isinstance(span.get("font"), dict) else {}
        size = float(font.get("size") or 0)
        width = float(bbox.get("width") or 0)
        height = float(bbox.get("height") or 0)
        if not span_id or len(text) != 1 or size < 24 or width <= 0 or height <= 0:
            continue
        if width < height * 1.35 or height > size * 0.7:
            continue
        key = (str(font.get("family") or ""), round(size, 1), str(span.get("color") or ""))
        clusters.setdefault(key, []).append(span)

    title_ids: set[str] = set()
    for glyphs in clusters.values():
        if len(glyphs) < 6:
            continue
        bboxes = [glyph.get("bbox") for glyph in glyphs if isinstance(glyph.get("bbox"), dict)]
        if len(bboxes) < 6:
            continue
        xs = sorted(float(bbox.get("x") or 0) for bbox in bboxes)
        ys = [float(bbox.get("y") or 0) for bbox in bboxes]
        size = float((glyphs[0].get("font") or {}).get("size") or 0)
        x_tolerance = max(5.0, size * 0.35)
        columns: list[list[float]] = []
        for x in xs:
            if not columns or abs(columns[-1][-1] - x) > x_tolerance:
                columns.append([x])
            else:
                columns[-1].append(x)
        if len(columns) > 4:
            continue
        if max(ys) - min(ys) < max(90.0, size * 3):
            continue
        if max(len(column) for column in columns) < 3:
            continue
        title_ids.update(str(glyph.get("id") or "") for glyph in glyphs if glyph.get("id"))
    return title_ids


def _span_typography_role(page_number: int, span: dict[str, Any]) -> str:
    text = re.sub(r"\s+", " ", str(span.get("text") or "")).strip()
    lower = text.lower()
    font = span.get("font") if isinstance(span.get("font"), dict) else {}
    family = str(font.get("family") or "").lower()
    size = float(font.get("size") or 0)
    if page_number == 1 and size >= 40:
        return "title"
    if "@" in lower or re.search(r"\b0\d[\d\s]{8,}\b", lower):
        return "agent/contact"
    if lower.startswith("*") or "indicative purposes" in lower or size <= 7:
        return "caption/small print"
    if _looks_like_table_text(_compact_semantic_text(text), page_number):
        return "table/status"
    if _looks_like_heading_span(text, font) or size >= 28 or ("dala" in family and size >= 16):
        return "heading"
    return "body"


def _inventory_typography_role(role: str, spans: list[dict[str, Any]]) -> dict[str, Any]:
    font_counts: Counter[str] = Counter()
    size_counts: Counter[float] = Counter()
    colour_counts: Counter[str] = Counter()
    for span in spans:
        font = span.get("font") if isinstance(span.get("font"), dict) else {}
        font_counts[str(font.get("family") or "")] += 1
        size_counts[float(font.get("size") or 0)] += 1
        colour_counts[str(span.get("color") or "")] += 1
    return {
        "role": role,
        "font_family": font_counts.most_common(1)[0][0] if font_counts else "",
        "font_size": size_counts.most_common(1)[0][0] if size_counts else "",
        "colour": colour_counts.most_common(1)[0][0] if colour_counts else "",
        "span_count": len(spans),
        "extraction_method": "PyMuPDF font/text spans",
    }


def _global_typography_roles(inventory_pages: list[dict[str, Any]]) -> dict[str, Any]:
    role_fonts: dict[str, Counter[str]] = {}
    for page in inventory_pages:
        for role in page.get("typography_roles", []):
            name = str(role.get("role") or "")
            font = str(role.get("font_family") or "")
            if not name or not font:
                continue
            role_fonts.setdefault(name, Counter())[font] += int(role.get("span_count") or 1)
    return {
        role: {
            "font_family": counts.most_common(1)[0][0],
            "controls": ["font family", "font size", "line height", "letter spacing", "weight"],
        }
        for role, counts in sorted(role_fonts.items())
        if counts
    }


def _global_palette_roles(model: dict[str, Any]) -> dict[str, Any]:
    colors = model.get("colors") if isinstance(model.get("colors"), dict) else {}
    background = colors.get("background") if isinstance(colors.get("background"), list) else []
    accent = colors.get("accent") if isinstance(colors.get("accent"), list) else []
    return {
        "accent_colour": accent[0] if accent else "",
        "dark_background_colour": next((color for color in background if _is_dark_hex(color)), background[0] if background else ""),
        "light_text_colour": "#ffffff",
        "map_category_colours": "derive from PDF text/vector colours when map labels are present",
        "method": "page colour sampling plus extracted text/vector colour evidence",
    }


def _source_logo_inventory(
    page_number: int,
    purpose: dict[str, Any],
    page: dict[str, Any],
    text_spans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    features = set(purpose.get("features") or [])
    if "source_facade_mark" not in features and "global_logo" not in features and page_number > 1:
        if not features.intersection({"space_plan", "amenity_icons", "map", "service_icons"}):
            return []
    role = "cover/large mark" if page_number == 1 else "repeated header mark"
    return [
        {
            "role": role,
            "editable": True,
            "static_source": "original PDF vector/raster mark should be hidden when replacement is active",
            "extraction_method": "vector detection, then palette-aware raster component detection",
        }
    ]


def _repeated_logo_inventory(page_number: int, purpose: dict[str, Any], page: dict[str, Any]) -> list[dict[str, Any]]:
    if page_number == 1:
        return []
    if any(feature in set(purpose.get("features") or []) for feature in ("global_logo", "source_facade_mark", "space_plan", "amenity_icons", "map")):
        return [
            {
                "role": "small recurring brochure mark",
                "editable": True,
                "extraction_method": "vector detection near page header/footer bands",
            }
        ]
    return []


def _amenity_inventory(region: dict[str, Any] | None, text_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not region:
        return []
    span_ids = set(region.get("span_ids") or [])
    label_spans = []
    for span in text_spans:
        if span.get("id") not in span_ids:
            continue
        text = str(span.get("text") or "").strip()
        if not text or text.upper() == "AMENITIES":
            continue
        label_spans.append(span)

    rows = []
    for group in _group_nearby_label_spans(label_spans):
        label = " ".join(str(span.get("text") or "").strip() for span in group).strip()
        rows.append(
            {
                "label": label,
                "label_span_ids": [span.get("id") for span in group],
                "bbox": _union_bboxes([span["bbox"] for span in group if isinstance(span.get("bbox"), dict)]),
                "editable_label": True,
                "editable_icon": True,
                "extraction_method": "PDF text label plus nearby vector icon grouping",
            }
        )
    return rows


def _group_nearby_label_spans(spans: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for span in sorted(spans, key=lambda item: (float(item.get("bbox", {}).get("x", 0)), float(item.get("bbox", {}).get("y", 0)))):
        bbox = span.get("bbox") if isinstance(span.get("bbox"), dict) else {}
        x = float(bbox.get("x", 0))
        y = float(bbox.get("y", 0))
        matched = False
        for group in groups:
            last_bbox = group[-1].get("bbox") if isinstance(group[-1].get("bbox"), dict) else {}
            if abs(float(last_bbox.get("x", 0)) - x) <= 32 and 0 <= y - float(last_bbox.get("y", 0)) <= 24:
                group.append(span)
                matched = True
                break
        if not matched:
            groups.append([span])
    return groups


def _map_inventory(region: dict[str, Any] | None, text_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not region:
        return []
    span_ids = set(region.get("span_ids") or [])
    labels = [
        {"text": span.get("text"), "span_id": span.get("id"), "bbox": span.get("bbox"), "colour": span.get("color")}
        for span in text_spans
        if span.get("id") in span_ids
    ]
    return [
        {
            "bbox": region.get("bbox"),
            "labels": labels,
            "editable": "map labels and generated map state; original decorative map layer can be preserved",
            "extraction_method": "PDF text labels, vector detection, colour sampling, semantic inference",
        }
    ]


def _space_plan_inventory(page_number: int, compact: str, image_boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if "floor" not in compact or not any(token in compact for token in ("sqft", "sqm", "desks")):
        return []
    return [
        {
            "page_number": page_number,
            "metadata_method": "PDF text",
            "plan_method": "vector detection with raster fallback",
            "replacement_style": "image/SVG replacement slot",
            "candidate_image_regions": [image.get("id") for image in image_boxes],
            "editable": True,
        }
    ]


def _space_plan_inventory_from_image_regions(page_number: int, image_regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for region in image_regions:
        if not isinstance(region, dict) or region.get("role") != "space-plan":
            continue
        regions.append(
            {
                "page_number": page_number,
                "bbox": region.get("bbox"),
                "metadata_method": "PDF text plus rendered-page classifier",
                "plan_method": (region.get("source_evidence") or {}).get("method") or "semantic image-region classifier",
                "replacement_style": "contain-fit image/SVG replacement slot",
                "candidate_image_regions": [region.get("id")],
                "editable": True,
                "confidence": region.get("confidence"),
            }
        )
    return regions


def _image_box_region_role(image: dict[str, Any], image_regions: list[dict[str, Any]]) -> str:
    image_id = str(image.get("id") or "")
    for region in image_regions:
        if not isinstance(region, dict):
            continue
        if str(region.get("id") or "") == image_id:
            return str(region.get("role") or "")
    return ""


def _contact_inventory(region: dict[str, Any] | None, text_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not region:
        return []
    span_ids = set(region.get("span_ids") or [])
    return [
        {
            "text": span.get("text"),
            "span_id": span.get("id"),
            "bbox": span.get("bbox"),
            "editable": True,
            "extraction_method": "PDF text plus email/phone semantic grouping",
        }
        for span in text_spans
        if span.get("id") in span_ids
    ]


def _static_elements_for_page(purpose: dict[str, Any], has_map: bool) -> list[str]:
    features = set(purpose.get("features") or [])
    static = ["source PDF vector/raster background where no editable replacement is active"]
    if "source_facade_mark" in features:
        static.append("original facade mark, hidden by replacement")
    if has_map:
        static.append("decorative map geometry when preserving the source map")
    if "space_plan" in features:
        static.append("original floor plan geometry unless replaced")
    return static


def _editable_elements_for_page(
    purpose: dict[str, Any],
    has_amenities: bool,
    has_map: bool,
    has_contacts: bool,
    has_images: bool,
) -> list[str]:
    editable = ["PDF text blocks with extracted typography"]
    features = set(purpose.get("features") or [])
    if has_images:
        editable.append("photo/image slots")
    if has_amenities or "amenity_icons" in features:
        editable.append("amenity labels and icon choices")
    if "service_icons" in features:
        editable.append("service/customisation icon choices")
    if has_map:
        editable.append("map labels, map preservation/regeneration state")
    if has_contacts:
        editable.append("agent/contact text")
    if "agency_logos" in features:
        editable.append("agency logo replacement")
    if "space_plan" in features:
        editable.append("space-plan replacement and metadata text")
    return editable


def _recommended_methods_for_features(features: list[str]) -> list[str]:
    methods = {
        "cover_title": "PDF text",
        "editable_text": "PDF text",
        "photo_regions": "PyMuPDF images",
        "source_facade_mark": "vector detection",
        "global_logo": "vector detection",
        "space_plan": "vector detection",
        "floor_metadata": "PDF text",
        "amenity_icons": "vector detection",
        "amenity_labels": "PDF text",
        "service_icons": "vector detection",
        "contact_text": "PDF text regex",
        "agency_logo": "vector/raster component detection",
        "agency_logos": "vector/raster component detection",
        "map": "PDF text plus vector detection",
        "map_labels": "PDF text",
        "transport_symbols": "vector detection",
        "subject_property_marker": "semantic inference",
        "agent_contacts": "PDF text regex",
        "legal_copy": "PDF text",
        "location_copy": "PDF text",
        "map_context": "semantic inference",
    }
    return sorted({methods.get(feature, "semantic inference") for feature in features})


def _extract_page_layout(
    *,
    page: fitz.Page,
    page_index: int,
    backgrounds_dir: Path,
    images_dir: Path,
    render_dpi: int,
    max_colors: int,
    min_image_area: float,
) -> dict[str, Any]:
    page_number = page_index + 1
    text_dict = page.get_text("dict", flags=TEXT_FLAGS)
    background = _render_page_background(page, page_number, backgrounds_dir, render_dpi)
    text_spans = _extract_text_spans(text_dict, page_number)
    if not text_spans:
        text_spans = _ocr_lines_to_text_spans(
            extract_ocr_text_lines(
                background["path"],
                page_number=page_number,
                page_width=float(page.rect.width),
                page_height=float(page.rect.height),
            ),
            page_number,
        )
    colors = _augment_colour_roles(
        _extract_page_colors(background["path"], max_colors=max_colors),
        _extract_evidence_colors(page, text_spans, max_colors=max_colors),
        max_colors=max_colors,
    )

    image_boxes = _extract_image_boxes(
        text_dict,
        page=page,
        page_number=page_number,
        images_dir=images_dir,
        min_image_area=min_image_area,
    )
    semantic_regions = _infer_semantic_regions(text_spans, page_number, page.rect.width, page.rect.height)
    page_model = {
        "id": f"page-{page_number:03d}",
        "page_index": page_index,
        "page_number": page_number,
        "rotation": page.rotation,
        "size": {
            "width": _round(page.rect.width),
            "height": _round(page.rect.height),
            "unit": "pt",
        },
        "media_box": _rect_to_bbox(page.mediabox),
        "crop_box": _rect_to_bbox(page.cropbox),
        "background": background,
        "background_path": background["path"],
        "text_spans": text_spans,
        "image_boxes": image_boxes,
        "colors": colors,
        "semantic_regions": semantic_regions,
    }
    page_model["image_regions"] = classify_model_page_image_regions(page_model)
    return page_model


def _ocr_lines_to_text_spans(lines: list[dict[str, Any]], page_number: int) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        bbox = line.get("bbox") if isinstance(line.get("bbox"), dict) else {}
        text = str(line.get("text") or "").strip()
        if not text or not bbox:
            continue
        font_size = float(line.get("font_size") or bbox.get("height") or 0)
        spans.append(
            {
                "id": f"p{page_number:03d}-ocr-text-{index:04d}",
                "text": text,
                "bbox": bbox,
                "origin": [_round(float(bbox.get("x") or 0)), _round(float(bbox.get("y") or 0) + font_size)],
                "block_bbox": bbox,
                "line_bbox": bbox,
                "block_index": index - 1,
                "line_index": 0,
                "span_index": 0,
                "font": {
                    "family": "OCRFallback",
                    "size": _round(font_size),
                    "flags": 16 if line.get("role") in {"cover-title", "section-heading"} else 0,
                    "is_bold": line.get("role") in {"cover-title", "section-heading"},
                    "is_italic": False,
                },
                "color": "#000000",
                "editable": True,
                "ocr_fallback": True,
                "confidence": line.get("confidence"),
                "extraction_method": line.get("extraction_method") or "Tesseract OCR fallback",
            }
        )
    return spans


def _extract_text_spans(text_dict: dict[str, Any], page_number: int) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    span_counter = 0

    for block_index, block in enumerate(text_dict.get("blocks", [])):
        if block.get("type") != 0:
            continue

        block_bbox = _list_to_bbox(block.get("bbox", [0, 0, 0, 0]))
        for line_index, line in enumerate(block.get("lines", [])):
            line_bbox = _list_to_bbox(line.get("bbox", [0, 0, 0, 0]))
            for source_span_index, span in enumerate(line.get("spans", [])):
                text = span.get("text", "")
                if not text or not text.strip():
                    continue

                span_counter += 1
                flags = int(span.get("flags", 0) or 0)
                spans.append(
                    {
                        "id": f"p{page_number:03d}-text-{span_counter:04d}",
                        "text": text,
                        "bbox": _list_to_bbox(span.get("bbox", [0, 0, 0, 0])),
                        "origin": [_round(v) for v in span.get("origin", [])],
                        "block_bbox": block_bbox,
                        "line_bbox": line_bbox,
                        "block_index": block_index,
                        "line_index": line_index,
                        "span_index": source_span_index,
                        "font": {
                            "family": span.get("font", ""),
                            "size": _round(span.get("size", 0)),
                            "flags": flags,
                            "is_bold": bool(flags & 16),
                            "is_italic": bool(flags & 2),
                        },
                        "color": _int_to_hex(span.get("color", 0)),
                        "editable": True,
                    }
                )

    return spans


def _extract_image_boxes(
    text_dict: dict[str, Any],
    *,
    page: fitz.Page,
    page_number: int,
    images_dir: Path,
    min_image_area: float,
) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    image_counter = 0

    for block_index, block in enumerate(text_dict.get("blocks", [])):
        if block.get("type") != 1:
            continue

        bbox = _list_to_bbox(block.get("bbox", [0, 0, 0, 0]))
        if bbox["width"] * bbox["height"] < min_image_area:
            continue

        image_bytes = block.get("image")
        if not image_bytes:
            continue

        image_counter += 1
        ext = _safe_extension(block.get("ext", "png"))
        image_path = images_dir / f"page-{page_number:03d}-image-{image_counter:04d}.{ext}"
        image_path.write_bytes(image_bytes)

        images.append(
            {
                "id": f"p{page_number:03d}-image-{image_counter:04d}",
                "bbox": bbox,
                "path": str(image_path),
                "block_index": block_index,
                "source_width": int(block.get("width", 0) or 0),
                "source_height": int(block.get("height", 0) or 0),
                "extension": ext,
                "colorspace": block.get("colorspace"),
                "xres": block.get("xres"),
                "yres": block.get("yres"),
                "transform": [_round(v) for v in block.get("transform", [])],
                "slot": True,
                "extraction_method": "PyMuPDF text-dict image block",
            }
        )

    image_counter = _append_xref_image_boxes(
        page=page,
        page_number=page_number,
        images_dir=images_dir,
        min_image_area=min_image_area,
        existing=images,
        image_counter=image_counter,
    )
    return images


def _append_xref_image_boxes(
    *,
    page: fitz.Page,
    page_number: int,
    images_dir: Path,
    min_image_area: float,
    existing: list[dict[str, Any]],
    image_counter: int,
) -> int:
    """Add image placement boxes from image xrefs when text-dict blocks miss them."""
    try:
        image_refs = page.get_images(full=True)
    except Exception:
        return image_counter

    for image_ref in image_refs:
        if not image_ref:
            continue
        xref = int(image_ref[0])
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        if not rects:
            continue
        try:
            extracted = page.parent.extract_image(xref) if page.parent else {}
        except Exception:
            extracted = {}
        image_bytes = extracted.get("image") if isinstance(extracted, dict) else None
        ext = _safe_extension(extracted.get("ext", "png") if isinstance(extracted, dict) else "png")
        width = int(extracted.get("width", 0) or 0) if isinstance(extracted, dict) else 0
        height = int(extracted.get("height", 0) or 0) if isinstance(extracted, dict) else 0

        for rect_index, rect in enumerate(rects, start=1):
            bbox = _rect_to_bbox(rect)
            if bbox["width"] * bbox["height"] < min_image_area:
                continue
            if _bbox_overlaps_any(bbox, [item.get("bbox") for item in existing if isinstance(item.get("bbox"), dict)]):
                continue
            if not image_bytes:
                continue
            image_counter += 1
            image_path = images_dir / f"page-{page_number:03d}-image-{image_counter:04d}.{ext}"
            image_path.write_bytes(image_bytes)
            existing.append(
                {
                    "id": f"p{page_number:03d}-image-{image_counter:04d}",
                    "bbox": bbox,
                    "path": str(image_path),
                    "xref": xref,
                    "placement_index": rect_index,
                    "source_width": width,
                    "source_height": height,
                    "extension": ext,
                    "slot": True,
                    "extraction_method": "PyMuPDF image xref placement",
                }
            )
    return image_counter


def _infer_semantic_regions(
    text_spans: list[dict[str, Any]],
    page_number: int,
    page_width: float,
    page_height: float,
) -> list[dict[str, Any]]:
    """Infer reusable semantic page regions from text clusters.

    These regions are intentionally generic. They do not try to name a specific
    brochure, but they give the exact-layout route a stable starting point for
    editable amenities, maps, contact blocks, and agency/logo areas.
    """
    if not text_spans:
        return []

    regions: list[dict[str, Any]] = []
    page_text = " ".join(span["text"] for span in text_spans)
    compact_text = _compact_semantic_text(page_text)

    amenity_heading = _amenity_icon_section_heading(text_spans)
    if amenity_heading:
        heading_box = amenity_heading.get("bbox") if isinstance(amenity_heading.get("bbox"), dict) else {}
        heading_left = float(heading_box.get("x", 0))
        heading_top = float(heading_box.get("y", 0))
        amenity_spans = [
            span
            for span in text_spans
            if float(span.get("bbox", {}).get("x", 0)) >= max(0.02 * page_width, heading_left - 0.08 * page_width)
            and heading_top + 0.015 * page_height <= float(span.get("bbox", {}).get("y", 0)) <= 0.90 * page_height
            and _looks_like_amenity_icon_label_text(str(span.get("text") or ""))
        ]
        if amenity_spans:
            regions.append(_spans_region("amenities", page_number, amenity_spans))

    map_tokens = (
        "station",
        "street",
        "market",
        "circle",
        "transport",
        "rail",
        "tube",
        "underground",
        "metro",
        "restaurant",
        "fitness",
        "cafe",
    )
    map_score = sum(token in compact_text for token in map_tokens)
    map_label_spans = _map_label_spans_for_region(text_spans, page_width, page_height)
    if map_score >= 3 and len(map_label_spans) >= 3 and _has_distributed_map_label_spans(map_label_spans, page_width, page_height):
        regions.append(_spans_region("map", page_number, map_label_spans))

    contact_spans = [span for span in text_spans if _looks_like_contact_text(span["text"])]
    if contact_spans:
        regions.append(_spans_region("contacts", page_number, contact_spans))
        contact_top = min(float(span_item.get("bbox", {}).get("y") or 0) for span_item in contact_spans)
        band_top = max(0.0, contact_top - max(160.0, page_height * 0.24))
        logo_band_spans = [
            span
            for span in text_spans
            if band_top <= float(span.get("bbox", {}).get("y") or 0) <= contact_top - 6.0
            and not _looks_like_contact_text(str(span.get("text") or ""))
        ]
        if _looks_like_agency_logo_text_band(logo_band_spans):
            regions.append(_spans_region("agency_logos", page_number, logo_band_spans))

    if page_number <= 2:
        if len(text_spans) <= 3:
            regions.append(_spans_region("cover_title", page_number, text_spans))
            return regions
        large_title_spans = [
            span
            for span in text_spans
            if span["font"]["size"] >= max(14.0, _median_font_size(text_spans) * 1.35)
        ]
        if large_title_spans:
            regions.append(_spans_region("cover_title", page_number, large_title_spans))

    return regions


def _amenity_icon_section_heading(text_spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        span
        for span in text_spans
        if _looks_like_semantic_section_heading(span, ("amenities", "features", "specification", "highlights"))
    ]
    for heading in candidates:
        page_width = max((float(span.get("bbox", {}).get("x", 0) + span.get("bbox", {}).get("width", 0)) for span in text_spans), default=0.0)
        if _has_icon_grid_label_evidence(heading, text_spans, page_width):
            return heading
    return None


def _looks_like_semantic_section_heading(span: dict[str, Any], tokens: tuple[str, ...]) -> bool:
    text = str(span.get("text") or "")
    compact = _compact_semantic_text(text).replace(" ", "")
    token_set = {_compact_semantic_text(token).replace(" ", "") for token in tokens}
    if compact in token_set:
        return True
    words = _semantic_words(text)
    return len(words) <= 3 and any(token and token in compact for token in token_set)


def _has_icon_grid_label_evidence(heading: dict[str, Any], text_spans: list[dict[str, Any]], page_width: float) -> bool:
    heading_box = heading.get("bbox") if isinstance(heading.get("bbox"), dict) else {}
    heading_left = float(heading_box.get("x", 0))
    heading_top = float(heading_box.get("y", 0))
    spans = [
        span
        for span in text_spans
        if float(span.get("bbox", {}).get("y", 0)) > heading_top + 12
        and float(span.get("bbox", {}).get("x", 0)) >= max(0.0, heading_left - max(90.0, page_width * 0.08))
        and _looks_like_amenity_icon_label_text(str(span.get("text") or ""))
    ]
    if len(spans) < 2:
        return False
    lefts = sorted(float(span.get("bbox", {}).get("x", 0)) for span in spans)
    columns = 0
    previous: float | None = None
    for left in lefts:
        if previous is None or abs(left - previous) > 120:
            columns += 1
            previous = left
    return columns >= 1 and len(spans) >= 2


def _looks_like_amenity_icon_label_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if "\t" in text or re.search(r"\b\d+\s*mins?\b", text, flags=re.IGNORECASE):
        return False
    compact = _compact_semantic_text(text).replace(" ", "")
    if len(compact) <= 2:
        return False
    if compact in {"amenities", "features", "specification", "highlights", "summaryspecification", "services", "lines"}:
        return False
    if re.match(r"^(?:0\d|\d{2})\s+[A-Z]", text):
        return False
    poi_tokens = (
        "station",
        "street",
        "market",
        "restaurant",
        "coffee",
        "cafe",
        "bank",
        "walktime",
        "exchange",
        "pizza",
        "pilgrim",
        "brasserie",
        "burger",
        "lobster",
        "gymbox",
        "broadleaf",
        "black",
        "sheep",
        "social",
        "soho",
        "yautcha",
        "rebel",
        "river",
        "thames",
        "cannon",
        "monument",
        "moorgate",
        "heathrow",
        "stansted",
        "destination",
        "airport",
        "airports",
        "transport",
        "londonbridge",
        "londoncity",
        "lloyd",
        "leadenhall",
        "england",
        "coleman",
        "bishop",
        "gracechurch",
        "cornhill",
        "threadneedle",
        "poultry",
        "kingwilliam",
        "oldbroad",
        "wall",
        "marks",
        "plaza",
        "fenchurch",
        "keywalk",
        "travel",
        "indicative",
        "building",
        "bevi",
        "undsdit",
        "grace",
        "hurch",
        "hbu",
        "throgm",
        "thr",
        "adnee",
        "cornhi",
        "nhall",
        "nce",
        "wiliam",
        "royal",
        "mary",
        "axe",
        "nno",
        "lest",
        "ast",
        "iams",
        "mes",
    )
    if any(token in compact for token in poi_tokens):
        return False
    words = _semantic_words(text)
    return 1 <= len(words) <= 8


def _spans_region(kind: str, page_number: int, spans: list[dict[str, Any]]) -> dict[str, Any]:
    bbox = _union_bboxes([span["bbox"] for span in spans])
    return {
        "id": f"p{page_number:03d}-{kind}",
        "kind": kind,
        "bbox": bbox,
        "span_ids": [span["id"] for span in spans],
        "text_sample": " ".join(span["text"] for span in spans)[:240],
    }


def _union_bboxes(boxes: list[dict[str, float]]) -> dict[str, float]:
    left = min(box["x"] for box in boxes)
    top = min(box["y"] for box in boxes)
    right = max(box["x"] + box["width"] for box in boxes)
    bottom = max(box["y"] + box["height"] for box in boxes)
    return {"x": _round(left), "y": _round(top), "width": _round(right - left), "height": _round(bottom - top)}


def _looks_like_contact_text(text: str) -> bool:
    lowered = text.lower()
    return "@" in lowered or bool(re.search(r"\b0\d[\d\s]{8,}\b", lowered))


def _looks_like_agency_logo_text_band(spans: list[dict[str, Any]]) -> bool:
    if not spans:
        return False
    text = " ".join(str(span.get("text") or "") for span in spans)
    if not _looks_like_agency_logo_text(text):
        return False
    sizes = [float((span.get("font") or {}).get("size") or 0) for span in spans if isinstance(span.get("font"), dict)]
    has_prominent_font = any(
        float((span.get("font") or {}).get("size") or 0) >= 14.0
        or bool((span.get("font") or {}).get("is_bold"))
        or "bold" in str((span.get("font") or {}).get("family") or "").lower()
        for span in spans
        if isinstance(span.get("font"), dict)
    )
    return has_prominent_font or not sizes


def _looks_like_agency_logo_text(text: str) -> bool:
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value or len(value) > 160:
        return False
    lowered = value.lower()
    if "@" in lowered or re.search(r"\b0\d[\d\s]{8,}\b", lowered):
        return False
    prose_terms = (
        "permission",
        "condition",
        "council",
        "website",
        "building",
        "development",
        "floor",
        "sq ft",
        "status",
        "available",
        "road",
        "pavement",
        "planning",
        "reference",
        "policy",
        "policies",
    )
    if any(term in lowered for term in prose_terms):
        return False
    words = re.findall(r"[A-Za-z0-9&]+", value)
    if not words or len(words) > 8:
        return False
    alpha_words = [word for word in words if re.search(r"[A-Za-z]", word)]
    if not alpha_words:
        return False
    uppercaseish = sum(1 for word in alpha_words if word.isupper() and len(word) > 1) >= max(1, len(alpha_words) - 1)
    titleish = sum(1 for word in alpha_words if word[:1].isupper()) >= max(1, len(alpha_words) - 1)
    return uppercaseish or titleish


def _looks_like_heading_span(text: str, font: dict[str, Any]) -> bool:
    value = re.sub(r"\s+", " ", text or "").strip()
    if not value or len(value) > 64:
        return False
    if "@" in value or re.search(r"\b0\d[\d\s]{8,}\b", value):
        return False
    words = re.findall(r"[A-Za-z0-9]+", value)
    if not words or len(words) > 5:
        return False
    size = float(font.get("size") or 0)
    if size < 22:
        return False
    family = str(font.get("family") or "").lower()
    return bool(font.get("is_bold")) or "bold" in family


def _looks_like_cover_title_page(spans: list[dict[str, Any]]) -> bool:
    if len(spans) > 12 or not spans:
        return False
    sizes = [float((span.get("font") or {}).get("size") or 0) for span in spans if span.get("font")]
    if not sizes:
        return False
    large_count = sum(1 for size in sizes if size >= max(32.0, _median_font_size(spans) * 2.0))
    text = " ".join(str(span.get("text") or "") for span in spans)
    uppercase_tokens = re.findall(r"\b[A-Z0-9]{2,}\b", text)
    return large_count >= 2 and len(uppercase_tokens) >= 2


def _looks_like_table_text(compact: str, page_number: int) -> bool:
    if not compact:
        return False
    table_tokens = (
        "sqft",
        "sqm",
        "status",
        "rent",
        "rates",
        "servicecharge",
        "available",
        "let",
        "total",
        "catb",
    )
    if compact.isdigit():
        return True
    return any(token in compact for token in table_tokens)


def _median_font_size(spans: list[dict[str, Any]]) -> float:
    sizes = sorted(float(span["font"]["size"]) for span in spans if span.get("font"))
    if not sizes:
        return 10.0
    mid = len(sizes) // 2
    if len(sizes) % 2:
        return sizes[mid]
    return (sizes[mid - 1] + sizes[mid]) / 2


def _compact_semantic_text(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum() or char.isspace())


def _semantic_words(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return set(re.findall(r"[a-z0-9]+", ascii_text.lower()))


def _has_section_heading(text_spans: list[dict[str, Any]], tokens: tuple[str, ...]) -> bool:
    token_set = {_compact_semantic_text(token).replace(" ", "") for token in tokens}
    for span in text_spans:
        text = str(span.get("text") or "").strip()
        compact = _compact_semantic_text(text).replace(" ", "")
        if compact in token_set:
            return True
        words = _semantic_words(text)
        if words.intersection(token_set) and len(words) <= 3:
            return True
    return False


_SEMANTIC_MAP_FALSE_POSITIVE_TOKENS = {
    "amenities",
    "pubsrestaurants",
    "corporateoffices",
    "investmentsummary",
    "proposal",
    "scheme",
    "schemes",
    "schedule",
    "statusnotes",
    "validated",
    "application",
    "ongoing",
    "consented",
    "completed",
    "appeal",
    "developmentrights",
}


def _map_label_spans_for_region(
    text_spans: list[dict[str, Any]],
    page_width: float,
    page_height: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for span in text_spans:
        text = str(span.get("text") or "").strip()
        bbox = span.get("bbox") if isinstance(span.get("bbox"), dict) else {}
        if not text or not bbox:
            continue
        compact = _compact_semantic_text(text).replace(" ", "")
        left = float(bbox.get("x") or 0)
        top = float(bbox.get("y") or 0)
        if compact in _SEMANTIC_MAP_FALSE_POSITIVE_TOKENS or any(token in compact for token in _SEMANTIC_MAP_FALSE_POSITIVE_TOKENS):
            continue
        stationish = any(token in compact for token in ("station", "rail", "tube", "metro", "underground", "overground"))
        if len(text) > 46 or "@" in text or re.search(r"\b0\d[\d\s]{8,}\b", text):
            continue
        if re.search(r"[.;:]\s*$", text):
            continue
        words = re.findall(r"[A-Za-z0-9]+", text)
        if not words or len(words) > 5:
            continue
        if not stationish and left < page_width * 0.34 and top < page_height * 0.38 and len(text) > 14:
            continue
        if len(words) >= 4 and not re.match(r"^\d{1,4}\s+[A-Za-z]", text):
            continue
        candidates.append(span)

    dense_buckets: set[int] = set()
    buckets: dict[int, list[dict[str, Any]]] = {}
    for span in candidates:
        bbox = span.get("bbox") if isinstance(span.get("bbox"), dict) else {}
        left = float(bbox.get("x") or 0)
        if left <= page_width * 0.42:
            buckets.setdefault(int(left // 26), []).append(span)
    for bucket, bucket_spans in buckets.items():
        if len(bucket_spans) < 5:
            continue
        tops = [float((span.get("bbox") or {}).get("y") or 0) for span in bucket_spans]
        vertical_span = max(tops) - min(tops)
        average_len = sum(len(str(span.get("text") or "")) for span in bucket_spans) / max(1, len(bucket_spans))
        bucket_left = bucket * 26.0
        if vertical_span >= page_height * 0.16 or (
            len(bucket_spans) >= 5
            and bucket_left < page_width * 0.42
            and average_len >= 12
            and vertical_span >= page_height * 0.07
        ):
            dense_buckets.add(bucket)
    return [
        span
        for span in candidates
        if int(float((span.get("bbox") or {}).get("x") or 0) // 26) not in dense_buckets
    ]


def _has_distributed_map_label_spans(text_spans: list[dict[str, Any]], page_width: float, page_height: float) -> bool:
    labels: list[dict[str, Any]] = []
    for span in text_spans:
        text = str(span.get("text") or "").strip()
        bbox = span.get("bbox") if isinstance(span.get("bbox"), dict) else {}
        if not text or not bbox:
            continue
        if len(text) > 46 or "@" in text or re.search(r"\b0\d[\d\s]{8,}\b", text):
            continue
        if re.search(r"[.;:]\s*$", text):
            continue
        words = re.findall(r"[A-Za-z0-9]+", text)
        if not words or len(words) > 5:
            continue
        compact = _compact_semantic_text(text).replace(" ", "")
        if any(token in compact for token in ("elizabeth", "circle", "hammersmith", "metropolitan", "thameslink", "central")) and any(
            separator in text for separator in (",", "&", " - ")
        ):
            continue
        titleish = sum(1 for word in words if word[:1].isupper()) >= max(1, len(words) - 1)
        upperish = all(word.isupper() for word in words if re.search(r"[A-Za-z]", word))
        placeish = any(
            token in compact
            for token in (
                "bank",
                "market",
                "street",
                "lane",
                "square",
                "circle",
                "circus",
                "garden",
                "green",
                "exchange",
                "moorgate",
                "farringdon",
                "barbican",
                "liverpool",
                "cafe",
                "coffee",
                "sushi",
                "wine",
                "fitness",
                "gym",
                "hotel",
            )
        )
        if not (upperish or (titleish and (placeish or len(words) > 1))):
            continue
        if _looks_like_footer_address_label(text, bbox, page_height):
            continue
        labels.append(span)

    if len(labels) < 3:
        return False
    lefts = [float(span["bbox"].get("x") or 0) for span in labels]
    tops = [float(span["bbox"].get("y") or 0) for span in labels]
    return (max(lefts) - min(lefts)) >= page_width * 0.25 and (max(tops) - min(tops)) >= page_height * 0.18


def _looks_like_footer_address_label(text: str, bbox: dict[str, Any], page_height: float) -> bool:
    if float(bbox.get("y") or 0) < page_height * 0.82:
        return False
    value = str(text or "").strip()
    if not re.match(r"^\d{1,5}(?:\s*[-–]\s*\d{1,5})?\s+[A-Za-z]", value):
        return False
    words = re.findall(r"[A-Za-z]+", value)
    if not words or len(words) > 5:
        return False
    address_words = {
        "street",
        "st",
        "road",
        "rd",
        "lane",
        "ln",
        "avenue",
        "ave",
        "yard",
        "place",
        "square",
        "mews",
        "circus",
    }
    compact_words = {word.lower() for word in words}
    return bool(compact_words.intersection(address_words))


def _render_page_background(
    page: fitz.Page,
    page_number: int,
    backgrounds_dir: Path,
    render_dpi: int,
) -> dict[str, Any]:
    zoom = render_dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
    background_path = backgrounds_dir / f"page-{page_number:03d}.png"
    pix.save(background_path)

    return {
        "path": str(background_path),
        "dpi": render_dpi,
        "width_px": pix.width,
        "height_px": pix.height,
        "scale": _round(zoom),
    }


def _extract_page_colors(image_path: str, *, max_colors: int) -> dict[str, list[str]]:
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        sample = _resized_sample(rgb, max_size=180)

        dominant_counter = _quantized_counter(sample.getdata())
        background_counter = _quantized_counter(_edge_pixels(sample))
        background = _top_counter_values(background_counter, max_colors)

        dominant = _top_counter_values(dominant_counter, max_colors)
        accent = _accent_colors(dominant_counter, background, max_colors)

    return {
        "dominant": dominant,
        "background": background,
        "accent": accent,
    }


def _extract_evidence_colors(
    page: fitz.Page,
    text_spans: list[dict[str, Any]],
    *,
    max_colors: int,
) -> dict[str, list[str]]:
    text_counter = Counter(str(span.get("color") or "") for span in text_spans if span.get("color"))
    vector_counter: Counter[str] = Counter()
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for drawing in drawings:
        for key in ("color", "fill"):
            colour = _fitz_colour_to_hex(drawing.get(key))
            if colour:
                vector_counter[colour] += 1
    combined = text_counter + vector_counter
    return {
        "text": _top_counter_values(text_counter, max_colors),
        "vector": _top_counter_values(vector_counter, max_colors),
        "accent_candidates": [
            colour
            for colour, _count in combined.most_common(max_colors * 3)
            if _is_accent_candidate(colour, [])
        ][:max_colors],
    }


def _augment_colour_roles(
    sampled: dict[str, list[str]],
    evidence: dict[str, list[str]],
    *,
    max_colors: int,
) -> dict[str, Any]:
    background = list(sampled.get("background") or [])
    accent_counter: Counter[str] = Counter()
    for rank, colour in enumerate(evidence.get("accent_candidates") or []):
        if _is_accent_candidate(colour, background):
            accent_counter[colour] += max_colors * 3 - rank
    for colour in list(evidence.get("text") or []) + list(evidence.get("vector") or []):
        if _is_accent_candidate(colour, background):
            accent_counter[colour] += max_colors
    for rank, colour in enumerate(sampled.get("accent") or []):
        if _is_accent_candidate(colour, background):
            accent_counter[colour] += max_colors - rank

    accents = _top_counter_values(accent_counter, max_colors)
    if not accents:
        accents = list(sampled.get("accent") or [])[:max_colors]
    return {
        "dominant": sampled.get("dominant") or [],
        "background": background,
        "accent": accents,
        "evidence": evidence,
    }


def _resized_sample(image: Image.Image, *, max_size: int) -> Image.Image:
    width, height = image.size
    longest = max(width, height)
    if longest <= max_size:
        return image.copy()
    scale = max_size / longest
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.Resampling.BILINEAR)


def _edge_pixels(image: Image.Image) -> Iterable[tuple[int, int, int]]:
    width, height = image.size
    band = max(1, min(width, height) // 20)
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            if x < band or y < band or x >= width - band or y >= height - band:
                yield pixels[x, y]


def _quantized_counter(pixels: Iterable[tuple[int, int, int]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for red, green, blue in pixels:
        counter[_rgb_to_hex(_quantize(red), _quantize(green), _quantize(blue))] += 1
    return counter


def _accent_colors(
    dominant_counter: Counter[str],
    background_colors: list[str],
    max_colors: int,
) -> list[str]:
    background_rgb = [_hex_to_rgb(color) for color in background_colors]
    accents: list[str] = []

    for color, _count in dominant_counter.most_common():
        rgb = _hex_to_rgb(color)
        if any(_rgb_distance(rgb, bg) < 42 for bg in background_rgb):
            continue
        if _is_near_white(rgb) or _is_near_black(rgb):
            continue
        accents.append(color)
        if len(accents) >= max_colors:
            break

    if accents:
        return accents

    return [
        color
        for color, _count in dominant_counter.most_common(max_colors)
        if color not in set(background_colors)
    ][:max_colors]


def _fitz_colour_to_hex(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return _int_to_hex(value)
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        channels = [max(0, min(255, round(float(channel) * 255))) for channel in value[:3]]
        return _rgb_to_hex(channels[0], channels[1], channels[2])
    return None


def _is_accent_candidate(color: str, background_colors: list[str]) -> bool:
    try:
        rgb = _hex_to_rgb(color)
    except Exception:
        return False
    if _is_near_white(rgb) or _is_near_black(rgb):
        return False
    if max(rgb) - min(rgb) < 28:
        return False
    background_rgb = [_hex_to_rgb(bg) for bg in background_colors if isinstance(bg, str) and bg.startswith("#")]
    if any(_rgb_distance(rgb, bg) < 42 for bg in background_rgb):
        return False
    return True


def _is_dark_hex(color: str) -> bool:
    try:
        return max(_hex_to_rgb(color)) < 96
    except Exception:
        return False


def _is_light_hex(color: str) -> bool:
    try:
        return min(_hex_to_rgb(color)) > 210
    except Exception:
        return False


def _bbox_overlaps_any(candidate: dict[str, float], existing: list[dict[str, float]]) -> bool:
    candidate_area = max(0.0, candidate["width"]) * max(0.0, candidate["height"])
    if candidate_area <= 0:
        return True
    cx0, cy0 = candidate["x"], candidate["y"]
    cx1, cy1 = candidate["x"] + candidate["width"], candidate["y"] + candidate["height"]
    for box in existing:
        bx0, by0 = box["x"], box["y"]
        bx1, by1 = box["x"] + box["width"], box["y"] + box["height"]
        ix0, iy0 = max(cx0, bx0), max(cy0, by0)
        ix1, iy1 = min(cx1, bx1), min(cy1, by1)
        intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        if intersection / candidate_area > 0.88:
            return True
    return False


def _top_counter_values(counter: Counter[str], limit: int) -> list[str]:
    return [color for color, _count in counter.most_common(limit)]


def _list_to_bbox(values: Iterable[float]) -> dict[str, float]:
    x0, y0, x1, y1 = [float(value) for value in values]
    return {
        "x": _round(x0),
        "y": _round(y0),
        "width": _round(x1 - x0),
        "height": _round(y1 - y0),
    }


def _rect_to_bbox(rect: fitz.Rect) -> dict[str, float]:
    return _list_to_bbox([rect.x0, rect.y0, rect.x1, rect.y1])


def _int_to_hex(value: Any) -> str:
    color = int(value or 0) & 0xFFFFFF
    return f"#{color:06x}"


def _safe_extension(value: Any) -> str:
    ext = str(value or "png").lower().strip(".")
    return "".join(char for char in ext if char.isalnum()) or "png"


def _quantize(value: int, step: int = 16) -> int:
    return max(0, min(255, (int(value) // step) * step))


def _rgb_to_hex(red: int, green: int, blue: int) -> str:
    return f"#{red:02x}{green:02x}{blue:02x}"


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    clean = color.lstrip("#")
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def _rgb_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right)) ** 0.5


def _is_near_white(rgb: tuple[int, int, int]) -> bool:
    return min(rgb) >= 224


def _is_near_black(rgb: tuple[int, int, int]) -> bool:
    return max(rgb) <= 32


def _round(value: Any) -> float:
    return round(float(value), 3)
