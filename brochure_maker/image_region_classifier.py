"""Semantic image-region classification for exact PDF brochure imports.

The exact importer sees several different things as "images": real photos,
rendered paper textures, maps, plans, logo rasters, and panels. This module
keeps that decision in one deterministic place so the renderer only exposes
replacement controls for regions that are actually replaceable brochure assets.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


PHOTO_ROLES = {"photo-region", "photo-grid", "hero-photo"}
ARTWORK_ROLES = {"artwork-image"}
STATIC_ROLES = {"background-texture", "decorative-panel", "static-vector-art", "table-image"}
REPLACEABLE_ROLES = PHOTO_ROLES | ARTWORK_ROLES | {"space-plan", "map", "logo", "agency-logo"}


def classify_page_image_regions(
    *,
    page_number: int,
    width: float,
    height: float,
    text_entries: list[dict[str, Any]],
    image_slots: list[dict[str, Any]],
    background_path: str | Path,
    semantic_regions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify rendered-page image slots and detect source-preserved regions.

    ``image_slots`` are in the same coordinate system as ``width``/``height``.
    ``background_path`` may be higher DPI; detected raster bboxes are scaled
    back into the requested page coordinate system.
    """
    background = Path(background_path)
    context = _page_context(page_number, text_entries, semantic_regions or [])
    classified_slots: list[dict[str, Any]] = []
    regions: list[dict[str, Any]] = []

    for index, slot in enumerate(image_slots, start=1):
        role, confidence, evidence = _classify_slot(
            slot=slot,
            slot_index=index,
            page_width=float(width),
            page_height=float(height),
            background_path=background,
            context=context,
        )
        fit_mode = _fit_mode_for_role(role, slot)
        replaceable = role in REPLACEABLE_ROLES
        bbox = _slot_bbox(slot)
        region_id = str(slot.get("id") or slot.get("slot_id") or slot.get("data_slot_id") or f"p{page_number:03d}-image-region-{index:04d}")
        region = _region(
            region_id=region_id,
            page_number=page_number,
            role=role,
            bbox=bbox,
            confidence=confidence,
            editable=replaceable,
            replaceable=replaceable,
            fit_mode=fit_mode,
            mask_mode="source-preserved" if not replaceable else "replaceable-mask",
            source_evidence=evidence,
        )
        regions.append(region)
        if role in PHOTO_ROLES | ARTWORK_ROLES:
            updated = dict(slot)
            updated["image_role"] = role
            updated["fit"] = fit_mode
            updated["semantic_confidence"] = confidence
            updated["semantic_evidence"] = evidence
            classified_slots.append(updated)

    _promote_photo_grid_regions(regions, classified_slots, width, height)

    if context["has_space_plan"]:
        space_bbox = detect_space_plan_bbox(
            background_path=background,
            width=float(width),
            height=float(height),
            text_entries=text_entries,
            image_slots=classified_slots,
        )
        if space_bbox and not _overlaps_role(space_bbox, regions, {"space-plan"}, threshold=0.45):
            regions.append(
                _region(
                    region_id=f"space-plan-p{page_number}-detected-1",
                    page_number=page_number,
                    role="space-plan",
                    bbox=space_bbox,
                    confidence=float(space_bbox.get("confidence") or 0.74),
                    editable=True,
                    replaceable=True,
                    fit_mode="contain",
                    mask_mode="source-preserved-until-replaced",
                    source_evidence={
                        "method": "rendered-page linework/grid detection",
                        "reason": space_bbox.get("reason") or "floor-plan context and dense linework",
                    },
                )
            )

    return {
        "schema": "brochure-maker.image-regions.v1",
        "page_number": page_number,
        "image_slots": classified_slots,
        "image_regions": regions,
    }


def classify_model_page_image_regions(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify image evidence from ``pdf_exact_layout`` model pages."""
    page_number = int(page.get("page_number") or 1)
    size = page.get("size") if isinstance(page.get("size"), dict) else {}
    width = float(size.get("width") or page.get("width") or 1)
    height = float(size.get("height") or page.get("height") or 1)
    slots: list[dict[str, Any]] = []
    for image in page.get("image_boxes") or []:
        if not isinstance(image, dict):
            continue
        bbox = image.get("bbox") if isinstance(image.get("bbox"), dict) else {}
        slots.append(
            {
                "id": image.get("id"),
                "left": bbox.get("x", bbox.get("left", 0)),
                "top": bbox.get("y", bbox.get("top", 0)),
                "width": bbox.get("width", 0),
                "height": bbox.get("height", 0),
                "asset_path": image.get("path"),
                "asset_url": image.get("asset_url"),
                "source_evidence": image.get("extraction_method"),
            }
        )
    text_entries = [
        {
            "plain": span.get("text"),
            "top": (span.get("bbox") or {}).get("y"),
            "left": (span.get("bbox") or {}).get("x"),
        }
        for span in page.get("text_spans") or []
        if isinstance(span, dict)
    ]
    classified = classify_page_image_regions(
        page_number=page_number,
        width=width,
        height=height,
        text_entries=text_entries,
        image_slots=slots,
        background_path=page.get("background_path") or (page.get("background") or {}).get("path") or "",
        semantic_regions=page.get("semantic_regions") if isinstance(page.get("semantic_regions"), list) else [],
    )
    return classified["image_regions"]


def detect_space_plan_bbox(
    *,
    background_path: str | Path,
    width: float,
    height: float,
    text_entries: list[dict[str, Any]],
    image_slots: list[dict[str, Any]] | None = None,
) -> dict[str, float] | None:
    """Detect a large floor/space-plan drawing from rendered page pixels."""
    if not _context_text_has_space_plan(text_entries):
        return None
    path = Path(background_path)
    if not path.is_file():
        return None
    try:
        with Image.open(path) as source:
            rgb = source.convert("RGB")
    except Exception:
        return None

    image_width, image_height = rgb.size
    if image_width <= 0 or image_height <= 0 or width <= 0 or height <= 0:
        return None

    scale_x = image_width / float(width)
    scale_y = image_height / float(height)
    right_limit_px = _content_right_limit_px(rgb)
    search_left_px = int(max(0, image_width * 0.04))
    search_right_px = int(min(right_limit_px, image_width * 0.93))
    heading_bottom_px = _max_heading_bottom(text_entries) * scale_y + 16
    search_top_px = int(max(image_height * 0.13, min(heading_bottom_px, image_height * 0.32)))
    search_bottom_px = int(image_height * 0.92)
    if search_right_px <= search_left_px or search_bottom_px <= search_top_px:
        return None

    background = _dominant_light_background(rgb, search_left_px, search_right_px, search_top_px, search_bottom_px)
    cell = max(10, int(round(min(image_width / max(width, 1), image_height / max(height, 1)) * 18)))
    cols = max(1, math.ceil((search_right_px - search_left_px) / cell))
    rows = max(1, math.ceil((search_bottom_px - search_top_px) / cell))
    active = [[False for _ in range(cols)] for _ in range(rows)]

    for row in range(rows):
        y0 = search_top_px + row * cell
        y1 = min(search_bottom_px, y0 + cell)
        for col in range(cols):
            x0 = search_left_px + col * cell
            x1 = min(search_right_px, x0 + cell)
            samples = 0
            ink = 0
            step_x = max(1, (x1 - x0) // 4)
            step_y = max(1, (y1 - y0) // 4)
            for y in range(y0, y1, step_y):
                for x in range(x0, x1, step_x):
                    samples += 1
                    pixel = rgb.getpixel((x, y))
                    if _is_plan_ink(pixel, background):
                        ink += 1
            active[row][col] = samples > 0 and (ink / samples) >= 0.12

    active = _dilate_grid(active, passes=1)
    components = _grid_components(active)
    candidates: list[dict[str, float]] = []
    for comp in components:
        min_col, min_row, max_col, max_row, cells = comp
        left = search_left_px + min_col * cell
        top = search_top_px + min_row * cell
        right = min(search_right_px, search_left_px + (max_col + 1) * cell)
        bottom = min(search_bottom_px, search_top_px + (max_row + 1) * cell)
        bbox_width = right - left
        bbox_height = bottom - top
        if bbox_width < image_width * 0.16 or bbox_height < image_height * 0.16:
            continue
        if bbox_width > image_width * 0.82 or bbox_height > image_height * 0.78:
            continue
        cell_density = cells / max(1, (max_col - min_col + 1) * (max_row - min_row + 1))
        score = (bbox_width * bbox_height) * (0.72 + min(0.35, cell_density))
        candidates.append(
            {
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "score": score,
                "cell_density": cell_density,
            }
        )
    if not candidates:
        pixel_component = _detect_space_plan_by_pixel_components(
            rgb,
            background=background,
            search_left_px=search_left_px,
            search_right_px=search_right_px,
            search_top_px=search_top_px,
            search_bottom_px=search_bottom_px,
            scale_x=scale_x,
            scale_y=scale_y,
            image_slots=image_slots or [],
        )
        if pixel_component and not (image_slots and _overlaps_any_slot(pixel_component, image_slots, threshold=0.45)):
            return pixel_component
        return None
    best = max(candidates, key=lambda item: item["score"])
    pad_x = max(cell * 1.5, (best["right"] - best["left"]) * 0.035)
    pad_y = max(cell * 1.5, (best["bottom"] - best["top"]) * 0.04)
    left_px = max(0.0, best["left"] - pad_x)
    top_px = max(0.0, best["top"] - pad_y)
    right_px = min(float(search_right_px), best["right"] + pad_x)
    bottom_px = min(float(search_bottom_px), best["bottom"] + pad_y)
    bbox = {
        "left": round(left_px / scale_x, 2),
        "top": round(top_px / scale_y, 2),
        "width": round(max(1.0, (right_px - left_px) / scale_x), 2),
        "height": round(max(1.0, (bottom_px - top_px) / scale_y), 2),
        "confidence": round(min(0.9, 0.68 + float(best.get("cell_density") or 0)), 3),
        "reason": "floor-plan context plus grouped rendered linework",
    }
    if image_slots and _overlaps_any_slot(bbox, image_slots, threshold=0.45):
        return _detect_space_plan_by_pixel_components(
            rgb,
            background=background,
            search_left_px=search_left_px,
            search_right_px=search_right_px,
            search_top_px=search_top_px,
            search_bottom_px=search_bottom_px,
            scale_x=scale_x,
            scale_y=scale_y,
            image_slots=image_slots,
        )
    return bbox


def _detect_space_plan_by_pixel_components(
    image: Image.Image,
    *,
    background: tuple[int, int, int],
    search_left_px: int,
    search_right_px: int,
    search_top_px: int,
    search_bottom_px: int,
    scale_x: float,
    scale_y: float,
    image_slots: list[dict[str, Any]],
) -> dict[str, float] | None:
    """Fallback for plans made of pale fills and thin linework.

    The grid detector is intentionally conservative. Some brochures draw floor
    plans with very thin grey furniture lines over a pale office fill, so a
    coarse cell sample can miss the plan even though the rendered component is
    visually obvious.
    """
    if search_right_px <= search_left_px or search_bottom_px <= search_top_px:
        return None
    step = max(3, int(round(min(scale_x, scale_y) * 2)))
    cols = max(1, (search_right_px - search_left_px) // step)
    rows = max(1, (search_bottom_px - search_top_px) // step)
    mask = [False] * (cols * rows)
    for row in range(rows):
        y = search_top_px + row * step
        for col in range(cols):
            x = search_left_px + col * step
            pixel = image.getpixel((x, y))
            mask[row * cols + col] = _is_space_plan_component_pixel(pixel, background)

    seen = [False] * len(mask)
    components: list[tuple[int, int, int, int, int, dict[str, float]]] = []
    for row in range(rows):
        for col in range(cols):
            index = row * cols + col
            if seen[index] or not mask[index]:
                continue
            stack = [(col, row)]
            seen[index] = True
            count = 0
            min_col = max_col = col
            min_row = max_row = row
            while stack:
                current_col, current_row = stack.pop()
                count += 1
                min_col = min(min_col, current_col)
                max_col = max(max_col, current_col)
                min_row = min(min_row, current_row)
                max_row = max(max_row, current_row)
                for next_col, next_row in (
                    (current_col + 1, current_row),
                    (current_col - 1, current_row),
                    (current_col, current_row + 1),
                    (current_col, current_row - 1),
                ):
                    if 0 <= next_col < cols and 0 <= next_row < rows:
                        next_index = next_row * cols + next_col
                        if not seen[next_index] and mask[next_index]:
                            seen[next_index] = True
                            stack.append((next_col, next_row))
            width_px = (max_col - min_col + 1) * step
            height_px = (max_row - min_row + 1) * step
            if count < 400 or width_px < image.width * 0.18 or height_px < image.height * 0.16:
                continue
            if width_px > image.width * 0.80 or height_px > image.height * 0.72:
                continue
            bbox = _component_bbox_to_css(
                search_left_px=search_left_px,
                search_top_px=search_top_px,
                min_col=min_col,
                min_row=min_row,
                max_col=max_col,
                max_row=max_row,
                step=step,
                search_right_px=search_right_px,
                search_bottom_px=search_bottom_px,
                scale_x=scale_x,
                scale_y=scale_y,
            )
            if image_slots and _overlaps_any_slot(bbox, image_slots, threshold=0.18):
                continue
            components.append((count, min_col, min_row, max_col, max_row, bbox))
    if not components:
        return None

    count, _min_col, _min_row, _max_col, _max_row, bbox = max(
        components,
        key=lambda item: item[0] * max(1, item[3] - item[1] + 1) * max(1, item[4] - item[2] + 1),
    )
    bbox["confidence"] = 0.82
    bbox["reason"] = "floor-plan context plus rendered colour/linework component"
    return bbox


def _component_bbox_to_css(
    *,
    search_left_px: int,
    search_top_px: int,
    min_col: int,
    min_row: int,
    max_col: int,
    max_row: int,
    step: int,
    search_right_px: int,
    search_bottom_px: int,
    scale_x: float,
    scale_y: float,
) -> dict[str, float]:
    left_px = search_left_px + min_col * step
    top_px = search_top_px + min_row * step
    right_px = min(search_right_px, search_left_px + (max_col + 1) * step)
    bottom_px = min(search_bottom_px, search_top_px + (max_row + 1) * step)
    pad_x = max(step * 5, (right_px - left_px) * 0.035)
    pad_y = max(step * 5, (bottom_px - top_px) * 0.04)
    left_px = max(0.0, left_px - pad_x)
    top_px = max(0.0, top_px - pad_y)
    right_px = min(float(search_right_px), right_px + pad_x)
    bottom_px = min(float(search_bottom_px), bottom_px + pad_y)
    return {
        "left": round(left_px / scale_x, 2),
        "top": round(top_px / scale_y, 2),
        "width": round(max(1.0, (right_px - left_px) / scale_x), 2),
        "height": round(max(1.0, (bottom_px - top_px) / scale_y), 2),
    }


def _is_space_plan_component_pixel(pixel: tuple[int, int, int], background: tuple[int, int, int]) -> bool:
    red, green, blue = [int(value) for value in pixel[:3]]
    luma = _luma(pixel)
    if luma < 42 or luma > 248:
        return False
    distance = _rgb_distance(pixel, background)
    saturation = _saturation(pixel)
    if distance > 20 and saturation < 0.42:
        return True
    if red > 160 and green > 140 and blue < 180 and red - blue > 20 and green - blue > 10:
        return True
    return False


def _classify_slot(
    *,
    slot: dict[str, Any],
    slot_index: int,
    page_width: float,
    page_height: float,
    background_path: Path,
    context: dict[str, Any],
) -> tuple[str, float, dict[str, Any]]:
    bbox = _slot_bbox(slot)
    area = max(0.0, float(bbox["width"]) * float(bbox["height"]))
    page_area = max(1.0, page_width * page_height)
    area_ratio = area / page_area
    touches_right = float(bbox["left"]) + float(bbox["width"]) >= page_width * 0.92
    touches_left = float(bbox["left"]) <= page_width * 0.04
    full_height = float(bbox["height"]) >= page_height * 0.82
    stats = _slot_visual_stats(slot, background_path, page_width, page_height)
    evidence = {
        "slot_index": slot_index,
        "area_ratio": round(area_ratio, 4),
        "touches_edge": touches_left or touches_right,
        "full_height": full_height,
        "mean_luma": stats.get("mean_luma"),
        "std_luma": stats.get("std_luma"),
        "mean_saturation": stats.get("mean_saturation"),
        "unique_ratio": stats.get("unique_ratio"),
        "context": {key: context[key] for key in sorted(context) if isinstance(context.get(key), bool)},
        "source": slot.get("source_evidence") or slot.get("asset_url") or slot.get("asset_path") or "",
    }
    text_overlap = _slot_text_overlap_metrics(bbox, context.get("text_entries") or [])
    evidence["text_overlap"] = text_overlap
    forced_role = str(slot.get("candidate_role") or slot.get("forced_role") or "").strip()
    if forced_role in REPLACEABLE_ROLES | STATIC_ROLES | ARTWORK_ROLES:
        return forced_role, float(slot.get("semantic_confidence") or slot.get("confidence") or 0.84), {
            **evidence,
            "reason": f"upstream detector assigned {forced_role}",
            "forced_role": forced_role,
        }

    if context["is_contact_page"] and area_ratio >= 0.10 and int(text_overlap.get("contact_like_entries") or 0) >= 3:
        return "background-texture", 0.88, {
            **evidence,
            "reason": "large contact-page image overlaps contact text, so it is preserved as page background rather than exposed as a photo slot",
        }

    unique_ratio = float(stats.get("unique_ratio") or 0)
    mean_luma = float(stats.get("mean_luma") or 0)
    std_luma = float(stats.get("std_luma") or 0)
    map_like_visual = unique_ratio < 0.72 or mean_luma < 130 or std_luma < 38
    if (
        context["has_map"]
        and area_ratio >= 0.12
        and (stats.get("mean_saturation", 0) or 0) < 0.22
        and map_like_visual
    ):
        return "map", 0.72, {**evidence, "reason": "map page context with low-saturation embedded region"}

    if full_height and (touches_left or touches_right) and area_ratio >= 0.20:
        low_sat = float(stats.get("mean_saturation") or 0) < 0.18
        dark_or_flat = float(stats.get("mean_luma") or 0) < 95 or float(stats.get("std_luma") or 0) < 36
        if float(stats.get("mean_luma") or 0) < 68 or (low_sat and dark_or_flat):
            return "background-texture", 0.86, {**evidence, "reason": "large edge-aligned low-saturation panel"}

    if area_ratio >= 0.26 and float(stats.get("std_luma") or 0) < 24 and float(stats.get("mean_saturation") or 0) < 0.16:
        return "decorative-panel", 0.78, {**evidence, "reason": "large flat non-photo panel"}

    if context["is_contact_page"] and area_ratio < 0.04:
        return "agency-logo", 0.68, {**evidence, "reason": "compact image on contacts page"}

    if context["has_map"] and not context["is_contact_page"] and area_ratio < 0.008:
        return "static-vector-art", 0.74, {
            **evidence,
            "reason": "compact raster marker on a map page; preserved with the map rather than exposed as a separate logo slot",
        }

    if area_ratio < 0.006:
        return "logo", 0.66, {**evidence, "reason": "compact raster mark outside photo region"}

    if (
        area_ratio >= 0.04
        and float(stats.get("mean_luma") or 0) > 220
        and float(stats.get("std_luma") or 0) < 10
        and float(stats.get("mean_saturation") or 0) < 0.08
    ):
        return "decorative-panel", 0.82, {**evidence, "reason": "large flat light panel/background, not a photo"}

    if area_ratio >= 0.50:
        return "hero-photo", 0.74, {**evidence, "reason": "large replaceable photo region"}

    return "photo-region", 0.7, {**evidence, "reason": "default replaceable photo candidate"}


def _promote_photo_grid_regions(regions: list[dict[str, Any]], slots: list[dict[str, Any]], width: float, height: float) -> None:
    photos = [region for region in regions if region.get("role") in PHOTO_ROLES and region.get("replaceable")]
    if len(photos) < 3:
        return
    total_area = 0.0
    for region in photos:
        bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
        total_area += float(bbox.get("width") or 0) * float(bbox.get("height") or 0)
    if total_area / max(1.0, float(width) * float(height)) < 0.12:
        return
    for region in photos:
        if region.get("role") == "photo-region":
            region["role"] = "photo-grid"
            region["source_evidence"]["photo_grid"] = "three or more replaceable photo regions on page"
    for slot in slots:
        if slot.get("image_role") == "photo-region":
            slot["image_role"] = "photo-grid"


def _region(
    *,
    region_id: str,
    page_number: int,
    role: str,
    bbox: dict[str, float],
    confidence: float,
    editable: bool,
    replaceable: bool,
    fit_mode: str,
    mask_mode: str,
    source_evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": region_id,
        "page_number": page_number,
        "type": _type_for_role(role),
        "role": role,
        "bbox": {key: round(float(bbox.get(key) or 0), 2) for key in ("left", "top", "width", "height")},
        "confidence": round(float(confidence), 3),
        "editable": bool(editable),
        "replaceable": bool(replaceable),
        "fit_mode": fit_mode,
        "mask_mode": mask_mode,
        "locked_static": role in STATIC_ROLES,
        "source_evidence": source_evidence,
    }


def _type_for_role(role: str) -> str:
    if role == "space-plan":
        return "floorplan"
    if role == "map":
        return "map"
    if role in {"logo", "agency-logo"}:
        return "agency-logo" if role == "agency-logo" else "logo"
    if "icon" in role:
        return "icon"
    return "image"


def _fit_mode_for_role(role: str, slot: dict[str, Any]) -> str:
    if role in {"space-plan", "map", "logo", "agency-logo", "table-image"}:
        return "contain"
    if role in ARTWORK_ROLES:
        return str(slot.get("fit") or "cover")
    return str(slot.get("fit") or "cover")


def _slot_bbox(slot: dict[str, Any]) -> dict[str, float]:
    return {
        "left": float(slot.get("left") or slot.get("x") or 0),
        "top": float(slot.get("top") or slot.get("y") or 0),
        "width": float(slot.get("width") or 0),
        "height": float(slot.get("height") or 0),
    }


def _page_context(page_number: int, text_entries: list[dict[str, Any]], semantic_regions: list[dict[str, Any]]) -> dict[str, Any]:
    text = _compact_text(" ".join(str(entry.get("plain") or entry.get("text") or "") for entry in text_entries))
    region_kinds = {_compact_text(str(region.get("kind") or region.get("role") or "")) for region in semantic_regions}
    is_contact_page = (
        any(token in text for token in ("furtherinformation", "viewings", "lettingagents", "misrepresentation"))
        or any(kind in region_kinds for kind in ("contacts", "agencylogos", "agentcontacts", "agentcontact"))
    )
    has_plan_semantics = any(kind in region_kinds for kind in ("spaceplan", "floorplan", "floorplans"))
    has_explicit_plan_text = _context_text_has_explicit_space_plan(text)
    has_space_plan_text = _context_text_has_space_plan(text_entries)
    return {
        "page_number": page_number,
        "has_space_plan": has_plan_semantics or (has_space_plan_text and not (is_contact_page and not has_explicit_plan_text)),
        "has_map": "map" in region_kinds or sum(token in text for token in ("station", "walktime", "transport", "bankstation", "underground")) >= 2,
        "is_contact_page": is_contact_page,
        "text_entries": text_entries,
    }


def _slot_text_overlap_metrics(bbox: dict[str, float], text_entries: list[dict[str, Any]]) -> dict[str, Any]:
    inside = 0
    contact_like = 0
    samples: list[str] = []
    left = float(bbox.get("left") or 0)
    top = float(bbox.get("top") or 0)
    right = left + float(bbox.get("width") or 0)
    bottom = top + float(bbox.get("height") or 0)
    for entry in text_entries:
        try:
            x = float(entry.get("left") if entry.get("left") is not None else (entry.get("bbox") or {}).get("x") or 0)
            y = float(entry.get("top") if entry.get("top") is not None else (entry.get("bbox") or {}).get("y") or 0)
        except (TypeError, ValueError):
            continue
        if not (left <= x <= right and top <= y <= bottom):
            continue
        inside += 1
        text = str(entry.get("plain") or entry.get("text") or "").strip()
        compact = _compact_text(text)
        if text and len(samples) < 5:
            samples.append(text[:50])
        if (
            "@" in text
            or re.search(r"\b0\d[\d\s]{5,}\b", text)
            or compact in {"m", "e", "t", "p"}
            or re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}", text)
        ):
            contact_like += 1
    return {
        "entries_inside": inside,
        "contact_like_entries": contact_like,
        "samples": samples,
    }


def _context_text_has_space_plan(text_entries: list[dict[str, Any]]) -> bool:
    text = _compact_text(" ".join(str(entry.get("plain") or entry.get("text") or "") for entry in text_entries))
    return "floor" in text and any(token in text for token in ("sqft", "sqm", "workstations", "desks", "floorplans", "spaceplan"))


def _context_text_has_explicit_space_plan(compact_text: str) -> bool:
    return any(token in compact_text for token in ("floorplan", "floorplans", "spaceplan", "spaceplans"))


def _compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _slot_visual_stats(slot: dict[str, Any], background_path: Path, page_width: float, page_height: float) -> dict[str, float]:
    image = _open_slot_image(slot, background_path, page_width, page_height)
    if image is None:
        return {}
    try:
        sample = image.convert("RGB").resize((40, 40), Image.Resampling.LANCZOS)
        stat = ImageStat.Stat(sample)
        pixels = list(sample.getdata())
        saturations: list[float] = []
        lumas: list[float] = []
        for red, green, blue in pixels:
            high = max(red, green, blue)
            low = min(red, green, blue)
            saturations.append((high - low) / max(1, high))
            lumas.append(_luma((red, green, blue)))
        return {
            "mean_luma": round(sum(lumas) / max(1, len(lumas)), 3),
            "std_luma": round(sum(float(value) for value in stat.stddev[:3]) / 3.0, 3),
            "mean_saturation": round(sum(saturations) / max(1, len(saturations)), 4),
            "unique_ratio": round(len(set(pixels)) / max(1, len(pixels)), 4),
        }
    finally:
        image.close()


def _open_slot_image(slot: dict[str, Any], background_path: Path, page_width: float, page_height: float) -> Image.Image | None:
    asset_path = _asset_path_for_slot(slot, background_path)
    if asset_path and asset_path.is_file():
        try:
            return Image.open(asset_path).copy()
        except Exception:
            pass
    if not background_path.is_file():
        return None
    try:
        with Image.open(background_path) as source:
            rgb = source.convert("RGB")
            scale_x = rgb.width / max(1.0, page_width)
            scale_y = rgb.height / max(1.0, page_height)
            bbox = _slot_bbox(slot)
            left = int(max(0, round(bbox["left"] * scale_x)))
            top = int(max(0, round(bbox["top"] * scale_y)))
            right = int(min(rgb.width, round((bbox["left"] + bbox["width"]) * scale_x)))
            bottom = int(min(rgb.height, round((bbox["top"] + bbox["height"]) * scale_y)))
            if right <= left or bottom <= top:
                return None
            return rgb.crop((left, top, right, bottom))
    except Exception:
        return None


def _asset_path_for_slot(slot: dict[str, Any], background_path: Path) -> Path | None:
    direct = slot.get("asset_path")
    if direct:
        path = Path(str(direct)).expanduser()
        if path.is_file():
            return path
    asset_url = str(slot.get("asset_url") or "")
    marker = "/exact_assets/"
    if marker in asset_url:
        rel = asset_url.split(marker, 1)[1].lstrip("/")
        return background_path.parent / rel
    return None


def _content_right_limit_px(image: Image.Image) -> int:
    width, height = image.size
    if width <= 0 or height <= 0:
        return width
    dark_columns: list[int] = []
    for x in range(width - 1, int(width * 0.35), -max(2, width // 360)):
        samples = 0
        dark = 0
        for y in range(int(height * 0.08), int(height * 0.92), max(3, height // 120)):
            samples += 1
            pixel = image.getpixel((x, y))
            if _luma(pixel) < 76 and _saturation(pixel) < 0.25:
                dark += 1
        if samples and dark / samples >= 0.72:
            dark_columns.append(x)
        elif dark_columns and (max(dark_columns) - min(dark_columns)) >= width * 0.12:
            return max(0, min(dark_columns) - max(8, width // 160))
    return width


def _dominant_light_background(image: Image.Image, left: int, right: int, top: int, bottom: int) -> tuple[int, int, int]:
    samples: list[tuple[int, int, int]] = []
    step_x = max(1, (right - left) // 48)
    step_y = max(1, (bottom - top) // 48)
    for y in range(top, bottom, step_y):
        for x in (left, min(right - 1, left + step_x), max(left, right - step_x - 1)):
            pixel = image.getpixel((x, y))
            if _luma(pixel) > 130 and _saturation(pixel) < 0.22:
                samples.append(_quantize(pixel, 16))
    if not samples:
        for y in range(top, bottom, step_y):
            for x in range(left, right, step_x):
                pixel = image.getpixel((x, y))
                if _luma(pixel) > 130:
                    samples.append(_quantize(pixel, 16))
    return Counter(samples).most_common(1)[0][0] if samples else (240, 240, 240)


def _is_plan_ink(pixel: tuple[int, int, int], background: tuple[int, int, int]) -> bool:
    luma = _luma(pixel)
    if luma < 54 or luma > 248:
        return False
    distance = _rgb_distance(pixel, background)
    if distance > 30 and _saturation(pixel) < 0.34:
        return True
    red, green, blue = pixel
    if red > 165 and green > 145 and blue < 170 and red - blue > 22 and green - blue > 16:
        return True
    return False


def _max_heading_bottom(text_entries: list[dict[str, Any]]) -> float:
    candidates: list[float] = []
    for entry in text_entries:
        text = str(entry.get("plain") or entry.get("text") or "").lower()
        if "not to scale" in text or "indicative" in text:
            continue
        if "floor" in text or "sq ft" in text or "sq m" in text:
            try:
                candidates.append(float(entry.get("top") or 0) + 80)
            except (TypeError, ValueError):
                pass
    return max(candidates, default=0.0)


def _dilate_grid(active: list[list[bool]], *, passes: int) -> list[list[bool]]:
    result = [row[:] for row in active]
    for _ in range(passes):
        next_grid = [row[:] for row in result]
        for y, row in enumerate(result):
            for x, value in enumerate(row):
                if not value:
                    continue
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny = y + dy
                        nx = x + dx
                        if 0 <= ny < len(result) and 0 <= nx < len(row):
                            next_grid[ny][nx] = True
        result = next_grid
    return result


def _grid_components(active: list[list[bool]]) -> list[tuple[int, int, int, int, int]]:
    if not active:
        return []
    rows = len(active)
    cols = len(active[0]) if active[0] else 0
    seen = [[False for _ in range(cols)] for _ in range(rows)]
    components: list[tuple[int, int, int, int, int]] = []
    for y in range(rows):
        for x in range(cols):
            if seen[y][x] or not active[y][x]:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            min_x = max_x = x
            min_y = max_y = y
            cells = 0
            while stack:
                cx, cy = stack.pop()
                cells += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < cols and 0 <= ny < rows and not seen[ny][nx] and active[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            components.append((min_x, min_y, max_x, max_y, cells))
    return components


def _overlaps_any_slot(bbox: dict[str, float], slots: list[dict[str, Any]], *, threshold: float) -> bool:
    for slot in slots:
        if _rect_overlap_ratio(bbox, _slot_bbox(slot)) >= threshold:
            return True
    return False


def _overlaps_role(bbox: dict[str, float], regions: list[dict[str, Any]], roles: set[str], *, threshold: float) -> bool:
    for region in regions:
        if region.get("role") not in roles:
            continue
        other = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
        if _rect_overlap_ratio(bbox, other) >= threshold:
            return True
    return False


def _rect_overlap_ratio(a: dict[str, float], b: dict[str, float]) -> float:
    left = max(float(a.get("left") or 0), float(b.get("left") or 0))
    top = max(float(a.get("top") or 0), float(b.get("top") or 0))
    right = min(float(a.get("left") or 0) + float(a.get("width") or 0), float(b.get("left") or 0) + float(b.get("width") or 0))
    bottom = min(float(a.get("top") or 0) + float(a.get("height") or 0), float(b.get("top") or 0) + float(b.get("height") or 0))
    if right <= left or bottom <= top:
        return 0.0
    overlap = (right - left) * (bottom - top)
    area = max(1.0, min(float(a.get("width") or 0) * float(a.get("height") or 0), float(b.get("width") or 0) * float(b.get("height") or 0)))
    return overlap / area


def _luma(pixel: tuple[int, int, int]) -> float:
    return (0.2126 * pixel[0]) + (0.7152 * pixel[1]) + (0.0722 * pixel[2])


def _saturation(pixel: tuple[int, int, int]) -> float:
    high = max(pixel)
    low = min(pixel)
    return (high - low) / max(1, high)


def _rgb_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((float(left[index]) - float(right[index])) ** 2 for index in range(3)) ** 0.5


def _quantize(pixel: tuple[int, int, int], step: int) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(round(channel / step) * step))) for channel in pixel[:3])
