"""Build a reusable brochure design graph from exact PDF extraction evidence."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "brochure-maker.design-graph.v1"

TYPOGRAPHY_TOKEN_BY_ROLE = {
    "title": "title",
    "cover-title": "title",
    "heading": "heading",
    "section-heading": "heading",
    "body": "body",
    "caption": "caption",
    "caption/small print": "caption",
    "table": "table-status",
    "table-status": "table-status",
    "table/status": "table-status",
    "agent-contact": "agent-contact",
    "agent/contact": "agent-contact",
}

TEXT_ELEMENT_ROLE_BY_TOKEN = {
    "title": "cover-title",
    "heading": "section-heading",
    "body": "body",
    "caption": "caption",
    "table-status": "table-status",
    "agent-contact": "agent-contact",
}


def build_design_graph(
    model: dict[str, Any],
    *,
    inventory: dict[str, Any] | None = None,
    render_metadata: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Create a page/element graph that can drive rendering, QA, and export.

    The graph is deliberately built from reusable extraction evidence. It
    records both normalized coordinates for scoring and the source coordinates
    or UI-slot coordinates that produced each element.
    """
    inventory = inventory or model.get("inventory") if isinstance(model.get("inventory"), dict) else inventory or {}
    render_metadata = render_metadata if isinstance(render_metadata, dict) else {}
    field_config = render_metadata.get("field_config") if isinstance(render_metadata.get("field_config"), dict) else {}

    model_pages = model.get("pages") if isinstance(model.get("pages"), list) else []
    inventory_pages = inventory.get("pages") if isinstance(inventory.get("pages"), list) else []
    inventory_by_number = {
        int(page.get("page_number") or index + 1): page
        for index, page in enumerate(inventory_pages)
        if isinstance(page, dict)
    }
    page_sizes = {
        int(page.get("page_number") or index + 1): _page_size(page, render_metadata)
        for index, page in enumerate(model_pages)
        if isinstance(page, dict)
    }

    pages = []
    for index, page in enumerate(model_pages):
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or index + 1)
        page_size = page_sizes.get(page_number) or _page_size(page, render_metadata)
        inventory_page = inventory_by_number.get(page_number, {})
        elements = _page_elements(page, inventory_page, page_size, field_config)
        detected_features = _validated_detected_features(inventory_page.get("detected_features") or [], elements)
        role_counts = Counter(str(element.get("role") or "unknown") for element in elements)
        type_counts = Counter(str(element.get("type") or "unknown") for element in elements)
        pages.append(
            {
                "id": page.get("id") or f"page-{page_number:03d}",
                "page_number": page_number,
                "purpose": inventory_page.get("page_purpose") or "unknown",
                "layout_type": inventory_page.get("layout_type") or "",
                "size": page_size,
                "confidence": inventory_page.get("confidence"),
                "detected_features": detected_features,
                "static_elements": inventory_page.get("static_elements") or [],
                "editable_elements": inventory_page.get("editable_elements") or [],
                "recommended_extraction_methods": inventory_page.get("recommended_extraction_methods") or [],
                "element_counts": {
                    "by_type": dict(sorted(type_counts.items())),
                    "by_role": dict(sorted(role_counts.items())),
                },
                "elements": elements,
            }
        )

    return {
        "schema": SCHEMA,
        "version": 1,
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_pdf": model.get("source_pdf"),
        "page_count": len(pages),
        "coordinate_system": model.get("coordinate_system")
        or {"unit": "pt", "origin": "top-left", "bbox": "x, y, width, height"},
        "theme_tokens": _theme_tokens(inventory, field_config),
        "feature_counts": _feature_counts(pages, inventory),
        "pages": pages,
    }


def _validated_detected_features(features: list[Any], elements: list[dict[str, Any]]) -> list[str]:
    """Keep hard QA requirements tied to concrete editable graph evidence."""
    feature_set = {str(feature) for feature in features if str(feature or "").strip()}
    roles = {str(element.get("role") or "") for element in elements if isinstance(element, dict)}
    control_roles = {
        str(element.get("role") or "")
        for element in elements
        if isinstance(element, dict)
        and element.get("node_class") in {"editor-control", "editable-content", "structured-field"}
        and (not element.get("replaceable") or element.get("bbox"))
    }

    if "cover-title" in roles:
        feature_set.add("cover_title")
    else:
        feature_set.discard("cover_title")

    if "source-facade-mark" in control_roles:
        feature_set.add("source_facade_mark")
        feature_set.add("global_logo")
    else:
        feature_set.discard("source_facade_mark")

    if not control_roles.intersection({"source-facade-mark", "repeated-header-mark"}):
        feature_set.discard("global_logo")

    return sorted(feature_set)


def write_design_graph(
    model: dict[str, Any],
    output_path: str | Path,
    *,
    inventory: dict[str, Any] | None = None,
    render_metadata: dict[str, Any] | None = None,
    project_id: str | None = None,
) -> Path:
    """Write ``brochure.design.json`` and return its path."""
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = build_design_graph(
        model,
        inventory=inventory,
        render_metadata=render_metadata,
        project_id=project_id or path.parent.name,
    )
    path.write_text(json.dumps(graph, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _page_elements(
    page: dict[str, Any],
    inventory_page: dict[str, Any],
    page_size: dict[str, Any],
    field_config: dict[str, Any],
) -> list[dict[str, Any]]:
    page_number = int(page.get("page_number") or inventory_page.get("page_number") or 1)
    role_by_span_id = {
        str(block.get("id")): str(block.get("typography_role") or "body")
        for block in inventory_page.get("editable_text_blocks") or []
        if isinstance(block, dict) and block.get("id")
    }
    space_candidate_ids = {
        str(candidate)
        for region in inventory_page.get("space_plan_regions") or []
        if isinstance(region, dict)
        for candidate in region.get("candidate_image_regions") or []
    }
    image_regions = [region for region in inventory_page.get("image_regions") or [] if isinstance(region, dict)]
    image_region_by_id = {str(region.get("id") or ""): region for region in image_regions if region.get("id")}
    represented_image_region_ids: set[str] = set()

    elements: list[dict[str, Any]] = []
    for index, span in enumerate(page.get("text_spans") or [], start=1):
        if not isinstance(span, dict):
            continue
        span_id = str(span.get("id") or f"p{page_number:03d}-text-{index:04d}")
        if span_id not in role_by_span_id:
            continue
        role_name = role_by_span_id.get(span_id) or str(span.get("typography_role") or "body")
        typography_token = _typography_token(role_name)
        elements.append(
            _element(
                page_size=page_size,
                element_id=span_id,
                element_type="text",
                role=TEXT_ELEMENT_ROLE_BY_TOKEN.get(typography_token, "body"),
                bbox=span.get("bbox"),
                raw_coordinate_system="pdf-pt",
                typography_token=typography_token,
                colour_token=_colour_token(span.get("color")),
                editable=bool(span.get("editable", True)),
                replaceable=False,
                locked_static=False,
                source_attribution="PDF text span",
                confidence=0.9,
                node_class="editable-content",
                text=span.get("text") or "",
                metadata={"font": span.get("font") or {}},
            )
        )

    for index, image in enumerate(page.get("image_boxes") or [], start=1):
        if not isinstance(image, dict):
            continue
        image_id = str(image.get("id") or f"p{page_number:03d}-image-{index:04d}")
        region = image_region_by_id.get(image_id) or {}
        is_space_plan_candidate = image_id in space_candidate_ids
        role = str(region.get("role") or ("space-plan" if is_space_plan_candidate else "photo-region"))
        element_type = str(region.get("type") or ("floorplan" if role == "space-plan" else "image"))
        represented_image_region_ids.add(image_id)
        elements.append(
            _element(
                page_size=page_size,
                element_id=image_id,
                element_type=element_type,
                role=role,
                bbox=image.get("bbox"),
                raw_coordinate_system="pdf-pt",
                typography_token=None,
                colour_token=None,
                editable=False,
                replaceable=bool(region.get("replaceable", image.get("slot", True))),
                locked_static=bool(region.get("locked_static", False)),
                source_attribution=(region.get("source_evidence") or {}).get("method") or image.get("extraction_method") or "PyMuPDF image block",
                confidence=float(region.get("confidence") or 0.82),
                node_class="extracted-evidence",
                asset_id=image_id,
                asset_path=image.get("path"),
                metadata={
                    "source_size": {
                        "width": image.get("source_width"),
                        "height": image.get("source_height"),
                    },
                    "fit": region.get("fit_mode") or ("contain" if role == "space-plan" else "cover"),
                    "mask_mode": region.get("mask_mode"),
                    "semantic_role": role,
                },
            )
        )

    for index, region in enumerate(image_regions, start=1):
        region_id = str(region.get("id") or f"p{page_number:03d}-image-region-{index:04d}")
        if region_id in represented_image_region_ids:
            continue
        bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
        elements.append(
            _element(
                page_size=page_size,
                element_id=region_id,
                element_type=str(region.get("type") or "image"),
                role=str(region.get("role") or "photo-region"),
                bbox=bbox,
                raw_coordinate_system="pdf-pt",
                typography_token=None,
                colour_token=None,
                editable=bool(region.get("editable")),
                replaceable=bool(region.get("replaceable")),
                locked_static=bool(region.get("locked_static")),
                source_attribution=(region.get("source_evidence") or {}).get("method") or "semantic image-region classifier",
                confidence=float(region.get("confidence") or 0.7),
                node_class="semantic-concept",
                metadata={
                    "fit": region.get("fit_mode"),
                    "mask_mode": region.get("mask_mode"),
                    "source_evidence": region.get("source_evidence"),
                },
            )
        )

    elements.extend(_inventory_concept_elements(inventory_page, page_size))
    elements.extend(_field_config_elements(field_config, page_number, page_size))
    return _dedupe_elements(elements)


def _inventory_concept_elements(inventory_page: dict[str, Any], page_size: dict[str, Any]) -> list[dict[str, Any]]:
    page_number = int(inventory_page.get("page_number") or 1)
    elements: list[dict[str, Any]] = []

    for index, region in enumerate(inventory_page.get("source_logo_regions") or [], start=1):
        if not isinstance(region, dict):
            continue
        elements.append(
            _element(
                page_size=page_size,
                element_id=f"p{page_number:03d}-source-logo-inventory-{index}",
                element_type="logo",
                role=_logo_role(region.get("role"), page_number),
                bbox=region.get("bbox"),
                raw_coordinate_system="pdf-pt",
                typography_token=None,
                colour_token="accent_colour",
                editable=False,
                replaceable=bool(region.get("editable", True)),
                locked_static=False,
                source_attribution=region.get("extraction_method") or "source logo inventory",
                confidence=0.55 if not region.get("bbox") else 0.75,
                node_class="semantic-concept",
                metadata={"inventory_role": region.get("role"), "static_source": region.get("static_source")},
            )
        )

    for index, region in enumerate(inventory_page.get("repeated_brochure_logo_positions") or [], start=1):
        if not isinstance(region, dict):
            continue
        elements.append(
            _element(
                page_size=page_size,
                element_id=f"p{page_number:03d}-repeated-logo-inventory-{index}",
                element_type="logo",
                role="repeated-header-mark",
                bbox=region.get("bbox"),
                raw_coordinate_system="pdf-pt",
                typography_token=None,
                colour_token="accent_colour",
                editable=False,
                replaceable=bool(region.get("editable", True)),
                locked_static=False,
                source_attribution=region.get("extraction_method") or "repeated logo inventory",
                confidence=0.5 if not region.get("bbox") else 0.72,
                node_class="semantic-concept",
                metadata={"inventory_role": region.get("role")},
            )
        )

    for index, region in enumerate(inventory_page.get("amenity_icon_regions") or [], start=1):
        if not isinstance(region, dict):
            continue
        elements.append(
            _element(
                page_size=page_size,
                element_id=f"p{page_number:03d}-amenity-icon-{index:02d}",
                element_type="icon",
                role="amenity-icon",
                bbox=region.get("bbox"),
                raw_coordinate_system="pdf-pt",
                typography_token=None,
                colour_token="accent_colour",
                editable=bool(region.get("editable_label", True)),
                replaceable=bool(region.get("editable_icon", True)),
                locked_static=False,
                source_attribution=region.get("extraction_method") or "amenity inventory",
                confidence=0.7,
                node_class="semantic-concept",
                label=region.get("label") or "",
                metadata={"label_span_ids": region.get("label_span_ids") or []},
            )
        )

    for index, region in enumerate(inventory_page.get("map_regions") or [], start=1):
        if not isinstance(region, dict):
            continue
        elements.append(
            _element(
                page_size=page_size,
                element_id=f"p{page_number:03d}-map-inventory-{index}",
                element_type="map",
                role="map",
                bbox=region.get("bbox"),
                raw_coordinate_system="pdf-pt",
                typography_token=None,
                colour_token="map_colours",
                editable=True,
                replaceable=True,
                locked_static=False,
                source_attribution=region.get("extraction_method") or "map inventory",
                confidence=0.72,
                node_class="semantic-concept",
                metadata={"labels": region.get("labels") or []},
            )
        )

    for index, region in enumerate(inventory_page.get("space_plan_regions") or [], start=1):
        if not isinstance(region, dict):
            continue
        elements.append(
            _element(
                page_size=page_size,
                element_id=f"p{page_number:03d}-space-plan-inventory-{index}",
                element_type="floorplan",
                role="space-plan",
                bbox=region.get("bbox"),
                raw_coordinate_system="pdf-pt",
                typography_token=None,
                colour_token="accent_colour",
                editable=False,
                replaceable=bool(region.get("editable", True)),
                locked_static=False,
                source_attribution=region.get("plan_method") or "space plan inventory",
                confidence=0.58 if not region.get("bbox") else 0.78,
                node_class="semantic-concept",
                metadata={
                    "metadata_method": region.get("metadata_method"),
                    "replacement_style": region.get("replacement_style"),
                    "candidate_image_regions": region.get("candidate_image_regions") or [],
                },
            )
        )

    for index, block in enumerate(inventory_page.get("agent_contact_blocks") or [], start=1):
        if not isinstance(block, dict):
            continue
        elements.append(
            _element(
                page_size=page_size,
                element_id=f"p{page_number:03d}-contact-{index:02d}",
                element_type="contact",
                role="agent-contact",
                bbox=block.get("bbox"),
                raw_coordinate_system="pdf-pt",
                typography_token="agent-contact",
                colour_token="contact_text_colour",
                editable=bool(block.get("editable", True)),
                replaceable=False,
                locked_static=False,
                source_attribution=block.get("extraction_method") or "contact inventory",
                confidence=0.82,
                node_class="semantic-concept",
                text=block.get("text") or "",
                metadata={"span_id": block.get("span_id")},
            )
        )
    return elements


def _field_config_elements(field_config: dict[str, Any], page_number: int, page_size: dict[str, Any]) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    cover_title = field_config.get("cover_title") if isinstance(field_config.get("cover_title"), dict) else {}
    cover_title_groups = cover_title.get("groups") if isinstance(cover_title.get("groups"), list) else []
    if page_number == 1:
        for index, group in enumerate(cover_title_groups, start=1):
            if not isinstance(group, dict) or not group.get("targets"):
                continue
            elements.append(
                _element(
                    page_size=page_size,
                    element_id=f"cover-title-group-{index}",
                    element_type="text",
                    role="cover-title",
                    bbox=_bbox_from_slot(group),
                    raw_coordinate_system="exact-html-px",
                    typography_token="title",
                    colour_token=None,
                    editable=True,
                    replaceable=False,
                    locked_static=False,
                    source_attribution="exact editor cover-title group detection",
                    confidence=0.9,
                    node_class="structured-field",
                    text=group.get("value") or "",
                    metadata={
                        "targets": group.get("targets") or [],
                        "orientation": group.get("orientation"),
                        "bbox_source": group.get("bbox_source"),
                    },
                )
            )

    source_logos = field_config.get("source_logos") if isinstance(field_config.get("source_logos"), list) else []
    for index, slot in enumerate(source_logos, start=1):
        if not isinstance(slot, dict) or str(slot.get("page") or "") != str(page_number):
            continue
        elements.append(
            _slot_element(
                page_size,
                element_id=f"source-logo-{slot.get('key') or index}",
                element_type="logo",
                role=_logo_role(slot.get("label") or slot.get("kind"), page_number),
                slot=slot,
                colour_token="accent_colour",
                replaceable=True,
                source_attribution="exact editor source-logo slot detection",
                confidence=0.86,
                metadata={
                    "key": slot.get("key"),
                    "label": slot.get("label"),
                    "mask_mode": slot.get("mask_mode"),
                    "mask_colour": slot.get("mask_colour"),
                    "default_asset_url": slot.get("default_asset_url"),
                },
            )
        )

    space_plans = field_config.get("space_plans") if isinstance(field_config.get("space_plans"), list) else []
    for index, slot in enumerate(space_plans, start=1):
        if not isinstance(slot, dict) or str(slot.get("page") or "") != str(page_number):
            continue
        elements.append(
            _slot_element(
                page_size,
                element_id=f"space-plan-{slot.get('key') or index}",
                element_type="floorplan",
                role="space-plan",
                slot=slot,
                colour_token="accent_colour",
                replaceable=True,
                source_attribution="exact editor space-plan slot detection",
                confidence=0.88,
                metadata={"key": slot.get("key"), "label": slot.get("label"), "asset_url": slot.get("asset_url")},
            )
        )

    image_regions = field_config.get("image_regions") if isinstance(field_config.get("image_regions"), list) else []
    for index, region in enumerate(image_regions, start=1):
        if not isinstance(region, dict) or str(region.get("page") or region.get("page_number") or "") != str(page_number):
            continue
        region_id = str(region.get("id") or f"image-region-p{page_number}-{index}")
        elements.append(
            _element(
                page_size=page_size,
                element_id=f"rendered-{region_id}",
                element_type=str(region.get("type") or "image"),
                role=str(region.get("role") or "photo-region"),
                bbox=region.get("bbox"),
                raw_coordinate_system="exact-html-px",
                typography_token=None,
                colour_token=None,
                editable=bool(region.get("editable")),
                replaceable=bool(region.get("replaceable")),
                locked_static=bool(region.get("locked_static")),
                source_attribution="exact renderer image-region classifier",
                confidence=float(region.get("confidence") or 0.7),
                node_class="editor-control" if region.get("replaceable") else "semantic-concept",
                metadata={
                    "fit": region.get("fit_mode"),
                    "mask_mode": region.get("mask_mode"),
                    "source_evidence": region.get("source_evidence"),
                },
            )
        )

    service_icons = field_config.get("service_icons") if isinstance(field_config.get("service_icons"), list) else []
    for index, slot in enumerate(service_icons, start=1):
        if not isinstance(slot, dict) or str(slot.get("page") or "") != str(page_number):
            continue
        elements.append(
            _slot_element(
                page_size,
                element_id=f"service-icon-{slot.get('key') or index}",
                element_type="icon",
                role="service-icon",
                slot=slot,
                colour_token="accent_colour",
                replaceable=True,
                source_attribution="exact editor service-icon slot detection",
                confidence=0.82,
                label=slot.get("label") or "",
                metadata={"key": slot.get("key"), "icon_id": slot.get("icon_id"), "textTargets": slot.get("textTargets") or []},
            )
        )

    amenity_icons = _icon_slot_values(field_config.get("amenity_icons"))
    for index, slot in enumerate(amenity_icons, start=1):
        if not isinstance(slot, dict) or str(slot.get("page") or "") != str(page_number):
            continue
        elements.append(
            _slot_element(
                page_size,
                element_id=f"amenity-icon-slot-{slot.get('key') or index}",
                element_type="icon",
                role="amenity-icon",
                slot=slot,
                colour_token="accent_colour",
                replaceable=True,
                source_attribution="exact editor amenity-icon slot detection",
                confidence=0.82,
                label=slot.get("label") or "",
                metadata={"key": slot.get("key"), "icon_id": slot.get("icon_id"), "textTargets": slot.get("textTargets") or []},
            )
        )

    map_region = field_config.get("map_region") if isinstance(field_config.get("map_region"), dict) else {}
    if map_region and str(map_region.get("page") or "") == str(page_number):
        elements.append(
            _slot_element(
                page_size,
                element_id=str(map_region.get("id") or f"map-page-{page_number}"),
                element_type="map",
                role="map",
                slot=map_region,
                colour_token="map_colours",
                replaceable=True,
                source_attribution="exact editor map-region detection",
                confidence=0.85,
                metadata={
                    "mode": map_region.get("mode"),
                    "labels": map_region.get("labels") or [],
                    "content": map_region.get("content") or {},
                },
            )
        )

    agency_logos = field_config.get("agency_logos") if isinstance(field_config.get("agency_logos"), dict) else {}
    for key, slot in agency_logos.items():
        if not isinstance(slot, dict) or str(slot.get("page") or "") != str(page_number):
            continue
        elements.append(
            _slot_element(
                page_size,
                element_id=f"agency-logo-{key}",
                element_type="agency-logo",
                role="agency-logo",
                slot=slot,
                colour_token="light_text_colour",
                replaceable=True,
                source_attribution="exact editor agency-logo slot detection",
                confidence=0.8,
                label=slot.get("label") or "",
                metadata={
                    "key": key,
                    "default_asset_url": slot.get("defaultAssetUrl") or slot.get("default_asset_url"),
                    "sourceContactKeys": slot.get("sourceContactKeys") or [],
                },
            )
        )

    return elements


def _slot_element(
    page_size: dict[str, Any],
    *,
    element_id: str,
    element_type: str,
    role: str,
    slot: dict[str, Any],
    colour_token: str,
    replaceable: bool,
    source_attribution: str,
    confidence: float,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bbox = _bbox_from_slot(slot)
    return _element(
        page_size=page_size,
        element_id=element_id,
        element_type=element_type,
        role=role,
        bbox=bbox,
        raw_coordinate_system="exact-html-px",
        typography_token=None,
        colour_token=colour_token,
        editable=False,
        replaceable=replaceable,
        locked_static=False,
        source_attribution=source_attribution,
        confidence=confidence,
        node_class="editor-control",
        label=label,
        metadata=metadata or {},
    )


def _element(
    *,
    page_size: dict[str, Any],
    element_id: str,
    element_type: str,
    role: str,
    bbox: Any,
    raw_coordinate_system: str,
    typography_token: str | None,
    colour_token: str | None,
    editable: bool,
    replaceable: bool,
    locked_static: bool,
    source_attribution: str,
    confidence: float,
    node_class: str = "extracted-evidence",
    text: str | None = None,
    asset_id: str | None = None,
    asset_path: str | None = None,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_bbox = _clean_bbox(bbox)
    element: dict[str, Any] = {
        "id": element_id,
        "type": element_type,
        "role": role,
        "bbox": _normalise_bbox(raw_bbox, page_size),
        "bbox_raw": raw_bbox,
        "bbox_rendered": raw_bbox if raw_coordinate_system == "exact-html-px" else None,
        "raw_pdf_bbox": raw_bbox if raw_coordinate_system == "pdf-pt" else None,
        "raw_bbox": raw_bbox,
        "raw_coordinate_system": raw_coordinate_system,
        "typography_token": typography_token,
        "colour_token": colour_token,
        "editable": editable,
        "replaceable": replaceable,
        "locked_static": locked_static,
        "source_attribution": source_attribution,
        "confidence": round(float(confidence), 3),
        "node_class": node_class,
        "evidence": [
            {
                "extractor": source_attribution,
                "coordinate_system": raw_coordinate_system,
                "confidence": round(float(confidence), 3),
                "has_bbox": bool(raw_bbox),
            }
        ],
    }
    if text is not None:
        element["text"] = text
    if asset_id:
        element["asset_id"] = asset_id
    if asset_path:
        element["asset_path"] = asset_path
    if label is not None:
        element["label"] = label
    if metadata:
        element["metadata"] = metadata
    return element


def _theme_tokens(inventory: dict[str, Any], field_config: dict[str, Any]) -> dict[str, Any]:
    systems = inventory.get("global_systems") if isinstance(inventory.get("global_systems"), dict) else {}
    palette = systems.get("palette") if isinstance(systems.get("palette"), dict) else {}
    typography = systems.get("typography") if isinstance(systems.get("typography"), dict) else {}
    rendered_typography = field_config.get("typography") if isinstance(field_config.get("typography"), dict) else {}
    return {
        "palette": palette,
        "typography": {
            "inventory_roles": typography,
            "editor_roles": rendered_typography.get("roles") or {},
            "fonts": rendered_typography.get("fonts") or [],
        },
        "global_controls": {
            "palette": [
                "accent colour",
                "dark/background colour",
                "light/text colour",
                "map category colours",
            ],
            "typography": [
                "title font",
                "heading font",
                "body font",
                "caption font",
                "table/status font",
                "agent/contact font",
            ],
            "logo": _controls(systems, "logo"),
            "amenity_icons": _controls(systems, "amenity_icons"),
            "images": _controls(systems, "images"),
            "map": _controls(systems, "map"),
            "agents": _controls(systems, "agents"),
        },
    }


def _feature_counts(pages: list[dict[str, Any]], inventory: dict[str, Any]) -> dict[str, Any]:
    systems = inventory.get("global_systems") if isinstance(inventory.get("global_systems"), dict) else {}
    inventory_counts = systems.get("feature_counts") if isinstance(systems.get("feature_counts"), dict) else {}
    type_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    for page in pages:
        for element in page.get("elements") or []:
            type_counts[str(element.get("type") or "unknown")] += 1
            role_counts[str(element.get("role") or "unknown")] += 1
    return {
        "inventory_features": inventory_counts,
        "graph_element_types": dict(sorted(type_counts.items())),
        "graph_element_roles": dict(sorted(role_counts.items())),
    }


def _page_size(page: dict[str, Any], render_metadata: dict[str, Any]) -> dict[str, Any]:
    size = page.get("size") if isinstance(page.get("size"), dict) else {}
    width = _float(size.get("width")) or _float(render_metadata.get("page_width")) or 1.0
    height = _float(size.get("height")) or _float(render_metadata.get("page_height")) or 1.0
    return {"width": round(width, 3), "height": round(height, 3), "unit": size.get("unit") or "pt"}


def _clean_bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    x = _float(value.get("x", value.get("left")))
    y = _float(value.get("y", value.get("top")))
    width = _float(value.get("width"))
    height = _float(value.get("height"))
    if x is None or y is None or width is None or height is None:
        return None
    return {"x": round(x, 3), "y": round(y, 3), "width": round(width, 3), "height": round(height, 3)}


def _bbox_from_slot(slot: dict[str, Any]) -> dict[str, float] | None:
    return _clean_bbox(
        {
            "x": slot.get("left"),
            "y": slot.get("top"),
            "width": slot.get("width"),
            "height": slot.get("height"),
        }
    )


def _normalise_bbox(bbox: dict[str, float] | None, page_size: dict[str, Any]) -> dict[str, float] | None:
    if not bbox:
        return None
    width = max(_float(page_size.get("width")) or 1.0, 1.0)
    height = max(_float(page_size.get("height")) or 1.0, 1.0)
    return {
        "x": round(bbox["x"] / width, 6),
        "y": round(bbox["y"] / height, 6),
        "width": round(bbox["width"] / width, 6),
        "height": round(bbox["height"] / height, 6),
    }


def _typography_token(role: str) -> str:
    return TYPOGRAPHY_TOKEN_BY_ROLE.get(str(role or "").strip().lower(), "body")


def _colour_token(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if re.fullmatch(r"#[0-9a-f]{6}", text):
        return f"pdf-colour:{text}"
    return text


def _logo_role(value: Any, page_number: int) -> str:
    text = str(value or "").lower()
    if page_number == 1 or "large" in text or "cover" in text:
        return "source-facade-mark"
    return "repeated-header-mark"


def _controls(systems: dict[str, Any], key: str) -> list[str]:
    value = systems.get(key)
    if isinstance(value, dict) and isinstance(value.get("controls"), list):
        return [str(item) for item in value["controls"]]
    return []


def _icon_slot_values(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for element in elements:
        key = str(element.get("id") or "")
        if not key:
            continue
        if key in seen:
            suffix = 2
            candidate = f"{key}-{suffix}"
            while candidate in seen:
                suffix += 1
                candidate = f"{key}-{suffix}"
            element = dict(element)
            element["id"] = candidate
            key = candidate
        seen.add(key)
        deduped.append(element)
    return deduped
