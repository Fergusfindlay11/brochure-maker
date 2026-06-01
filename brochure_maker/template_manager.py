"""Save, load, list, and apply reusable brochure templates."""

from __future__ import annotations

from copy import deepcopy
import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional, List

from brochure_maker.layout_variants import (
    DEFAULT_LAYOUT_ID,
    layout_variants_for,
    normalize_slide_type,
    resolve_layout_variant,
    supported_slide_types,
)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_STORE = BASE_DIR / "saved_templates"
TEMPLATES_STORE.mkdir(exist_ok=True)


def save_template(name: str, analysis: dict, preview_path: Optional[str] = None) -> dict:
    """Save a brochure analysis as a reusable template.

    Strips source wording/images while preserving style, slide sequence, slide
    types, layout ids, and slot schema metadata.
    """
    template_dir = TEMPLATES_STORE / _safe_name(name)
    template_dir.mkdir(parents=True, exist_ok=True)

    slides = analysis.get("slides", [])

    template_def = {
        "name": name,
        "version": 2,
        "colour_scheme": analysis.get("colour_scheme", {}),
        "typography": analysis.get("typography", {}),
        "logo": deepcopy(analysis.get("logo", {})),
        "slide_count": len(slides),
        "slide_sequence": [],
        "slides": [],
        "clone_plan": {
            "mode": "strict_structure",
            "minimum_confidence": 95,
            "preserve": [
                "colour_scheme",
                "typography",
                "slide_sequence",
            "slide_types",
            "layout_ids",
            "slot_schemas",
            "logo",
        ],
        },
    }

    for index, slide in enumerate(slides, start=1):
        raw_slide_type = slide.get("type") or slide.get("slide_type")
        slide_type = normalize_slide_type(raw_slide_type)
        requested_layout_id = slide.get("layout_id") or DEFAULT_LAYOUT_ID
        layout = resolve_layout_variant(slide_type, requested_layout_id)
        slot_schema = _extract_slot_schema(slide.get("content", {}))
        slide_def = {
            "slide_num": index,
            "slide_type": slide_type,
            "type": slide_type,
            "layout_id": requested_layout_id,
            "resolved_layout_id": layout["layout_id"],
            "layout_label": layout["label"],
            "layout_fallback": layout["layout_fallback"],
            "slot_schema": slot_schema,
            # Backwards-compatible compact schema for older callers.
            "content_schema": _extract_schema(slide.get("content", {})),
        }
        template_def["slides"].append(slide_def)
        template_def["slide_sequence"].append({
            "slide_num": index,
            "slide_type": slide_type,
            "type": slide_type,
            "layout_id": requested_layout_id,
            "resolved_layout_id": layout["layout_id"],
            "layout_fallback": layout["layout_fallback"],
        })

    template_def["clone_plan"]["slide_sequence"] = deepcopy(template_def["slide_sequence"])
    template_def["clone_plan"]["slides"] = [
        {
            "slide_num": slide["slide_num"],
            "slide_type": slide["slide_type"],
            "layout_id": slide["layout_id"],
            "resolved_layout_id": slide["resolved_layout_id"],
            "slot_schema": deepcopy(slide["slot_schema"]),
        }
        for slide in template_def["slides"]
    ]

    # Write template JSON
    template_path = template_dir / "template.json"
    with open(template_path, "w", encoding="utf-8") as f:
        json.dump(template_def, f, indent=2, ensure_ascii=False)

    # Copy preview image if provided
    if preview_path and os.path.exists(preview_path):
        shutil.copy2(preview_path, template_dir / "preview.png")

    return template_def


def merge_editor_layout_state_into_analysis(analysis: dict, editor_state: dict | None) -> dict:
    """Return an analysis copy with editor-selected style/layout state applied."""
    if not isinstance(analysis, dict):
        return analysis

    merged = deepcopy(analysis)
    changed = False

    if isinstance(editor_state, dict):
        primary_colour = editor_state.get("primaryColour")
        if isinstance(primary_colour, str) and primary_colour.strip():
            colour_scheme = merged.setdefault("colour_scheme", {})
            if isinstance(colour_scheme, dict):
                colour_scheme["primary"] = primary_colour.strip()
                changed = True

        logo_state = editor_state.get("logo")
        if isinstance(logo_state, dict):
            merged["logo"] = deepcopy(logo_state)
            changed = True

    layout_variants = editor_state.get("layoutVariants") if isinstance(editor_state, dict) else None
    if not isinstance(layout_variants, dict):
        return merged if changed else analysis

    slide_records = layout_variants.get("slides")
    if not isinstance(slide_records, dict) or not slide_records:
        return merged if changed else analysis

    slides = merged.get("slides")
    if not isinstance(slides, list) or not slides:
        return merged if changed else analysis

    records_by_id, records_by_order = _index_editor_layout_records(slide_records)
    if not records_by_id and not records_by_order:
        return merged if changed else analysis

    supported_types = set(supported_slide_types())
    use_order_fallback = _can_use_editor_layout_order_fallback(
        slide_records,
        len(records_by_order),
        len(slides),
    )

    for index, slide in enumerate(merged.get("slides", []), start=1):
        if not isinstance(slide, dict):
            continue

        record = _find_editor_layout_record(slide, index, records_by_id)
        if record is None and use_order_fallback and index <= len(records_by_order):
            record = records_by_order[index - 1]
        if not isinstance(record, dict):
            continue

        current_layout = record.get("currentLayout")
        if isinstance(current_layout, str) and current_layout.strip():
            slide["layout_id"] = current_layout.strip()
            changed = True

        editor_slide_type = _safe_editor_slide_type(
            record.get("slideType"),
            slide.get("type") or slide.get("slide_type"),
            supported_types,
        )
        if editor_slide_type:
            slide["type"] = editor_slide_type
            slide["slide_type"] = editor_slide_type
            changed = True

    return merged if changed else analysis


def load_template(name: str) -> Optional[dict]:
    """Load a saved template by name."""
    template_path = TEMPLATES_STORE / _safe_name(name) / "template.json"
    if not template_path.exists():
        return None

    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_templates() -> List[dict]:
    """List all saved templates with metadata for "Use Template" flows."""
    templates = []
    if not TEMPLATES_STORE.exists():
        return templates

    for template_dir in sorted(TEMPLATES_STORE.iterdir()):
        if not template_dir.is_dir():
            continue

        template_path = template_dir / "template.json"
        if not template_path.exists():
            continue

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            has_preview = (template_dir / "preview.png").exists()
            templates.append({
                "name": data.get("name", template_dir.name),
                "dir_name": template_dir.name,
                "version": data.get("version", 1),
                "slide_count": data.get("slide_count", 0),
                "has_preview": has_preview,
                "colour_primary": data.get("colour_scheme", {}).get("primary", "#B8714E"),
                "colour_scheme": data.get("colour_scheme", {}),
                "typography": data.get("typography", {}),
                "logo": data.get("logo", {}),
                "slide_sequence": _template_slide_sequence(data),
                "slides": _template_slide_metadata(data),
                "clone_plan": {
                    "mode": (data.get("clone_plan") or {}).get("mode", "strict_structure"),
                    "minimum_confidence": (data.get("clone_plan") or {}).get("minimum_confidence", 95),
                },
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return templates


def apply_template_to_analysis(
    analysis: dict,
    template: dict,
    *,
    strict_sequence: bool = False,
) -> dict:
    """Apply a saved template's style and layout sequence to fresh analysis.

    The template never supplies wording or image values. Content is consumed
    from the new analysis by matching slide type first, then by unused position.
    """
    result = deepcopy(analysis)

    if template.get("colour_scheme"):
        result["colour_scheme"] = deepcopy(template["colour_scheme"])
    if template.get("typography"):
        result["typography"] = deepcopy(template["typography"])
    if template.get("logo"):
        result["logo"] = deepcopy(template["logo"])

    template_slides = _template_slide_sequence(template)
    if not template_slides:
        return result

    source_slides = [deepcopy(slide) for slide in result.get("slides", [])]
    used_indexes: set[int] = set()
    applied_slides = []

    for target_index, template_slide in enumerate(template_slides, start=1):
        desired_type = normalize_slide_type(
            template_slide.get("slide_type") or template_slide.get("type")
        )
        source_index = _find_source_slide_index(source_slides, used_indexes, desired_type, target_index - 1)
        if source_index is None:
            slide = {"content": {}}
        else:
            used_indexes.add(source_index)
            slide = source_slides[source_index]

        requested_layout_id = template_slide.get("layout_id") or DEFAULT_LAYOUT_ID
        layout = resolve_layout_variant(desired_type, requested_layout_id)
        original_type = slide.get("type") or slide.get("slide_type")

        slide["page_num"] = target_index
        slide["type"] = desired_type
        slide["slide_type"] = desired_type
        slide["layout_id"] = requested_layout_id
        slide["resolved_layout_id"] = layout["layout_id"]
        slide["layout_fallback"] = layout["layout_fallback"]
        slide["template_source_type"] = original_type
        slide.setdefault("content", {})
        if strict_sequence:
            matching_template_slide = _template_slide_for_index(template, target_index)
            slot_schema = matching_template_slide.get("slot_schema", {}) if matching_template_slide else {}
            slide["content"] = _ensure_content_slots_for_template(slide["content"], slot_schema)
        applied_slides.append(slide)

    if not strict_sequence:
        for source_index, slide in enumerate(source_slides):
            if source_index in used_indexes:
                continue
            fallback_type = normalize_slide_type(slide.get("type") or slide.get("slide_type"))
            layout = resolve_layout_variant(fallback_type, slide.get("layout_id"))
            slide["page_num"] = len(applied_slides) + 1
            slide["type"] = fallback_type
            slide["slide_type"] = fallback_type
            slide["layout_id"] = slide.get("layout_id") or DEFAULT_LAYOUT_ID
            slide["resolved_layout_id"] = layout["layout_id"]
            slide["layout_fallback"] = layout["layout_fallback"]
            slide["template_append_reason"] = "not_in_template_sequence"
            applied_slides.append(slide)

    result["slides"] = applied_slides
    clone_plan = build_clone_plan(template)
    result["applied_template"] = {
        "name": template.get("name", ""),
        "version": template.get("version", 1),
        "slide_count": len(template_slides),
        "mode": "strict_structure" if strict_sequence else "style_sequence",
    }
    if strict_sequence:
        result["clone_plan"] = clone_plan
        result["clone_verification"] = verify_clone_against_template(result, template)
    return result


def build_clone_plan(template: dict) -> dict:
    """Build a deterministic structure plan from a saved template."""
    slides = _template_slide_metadata(template)
    sequence = _template_slide_sequence(template)
    return {
        "mode": "strict_structure",
        "minimum_confidence": 95,
        "source_template": template.get("name", ""),
        "version": template.get("version", 1),
        "slide_count": len(sequence),
        "slide_sequence": sequence,
        "slides": [
            {
                "slide_num": slide["slide_num"],
                "slide_type": slide["slide_type"],
                "layout_id": slide["layout_id"],
                "resolved_layout_id": slide["resolved_layout_id"],
                "slot_keys": list(slide.get("slot_keys", [])),
                "slot_schema": deepcopy(slide.get("slot_schema", {})),
                "available_layout_count": len(slide.get("available_layouts", [])),
            }
            for slide in slides
        ],
        "required_features": {
            "editable_text": True,
            "editable_image_placeholders": True,
            "global_background_colour": True,
            "layout_switching": True,
            "logo_style": True,
        },
    }


def verify_clone_against_template(analysis: dict, template: dict) -> dict:
    """Return a critical deterministic verification report for clone structure.

    The score is intentionally strict and structural. It does not claim visual
    pixel identity; it verifies that the rendered editor will receive the exact
    sequence, slide types, layout IDs, slot schemas, and required editability
    capabilities needed to make a high-confidence clone.
    """
    expected_sequence = _template_slide_sequence(template)
    expected_slides = _template_slide_metadata(template)
    actual_slides = analysis.get("slides", []) if isinstance(analysis, dict) else []
    checks = []

    def add_check(name: str, weight: int, passed: bool, detail: str, actual: Any = None, expected: Any = None) -> None:
        checks.append({
            "name": name,
            "weight": weight,
            "passed": bool(passed),
            "score": weight if passed else 0,
            "detail": detail,
            "actual": actual,
            "expected": expected,
        })

    actual_sequence = [
        {
            "slide_type": normalize_slide_type(slide.get("type") or slide.get("slide_type")),
            "layout_id": slide.get("layout_id") or DEFAULT_LAYOUT_ID,
        }
        for slide in actual_slides
        if isinstance(slide, dict)
    ]
    expected_compact = [
        {
            "slide_type": slide["slide_type"],
            "layout_id": slide["layout_id"],
        }
        for slide in expected_sequence
    ]

    add_check(
        "slide_count",
        20,
        len(actual_sequence) == len(expected_compact),
        "Clone has the same number of slides as the saved brochure.",
        len(actual_sequence),
        len(expected_compact),
    )
    add_check(
        "slide_type_sequence",
        25,
        [slide["slide_type"] for slide in actual_sequence] == [slide["slide_type"] for slide in expected_compact],
        "Clone slide types match the saved brochure in order.",
        [slide["slide_type"] for slide in actual_sequence],
        [slide["slide_type"] for slide in expected_compact],
    )
    add_check(
        "layout_sequence",
        25,
        actual_sequence == expected_compact,
        "Clone layout IDs match the saved brochure in order.",
        actual_sequence,
        expected_compact,
    )

    unresolved_layouts = [
        {
            "slide_num": index,
            "slide_type": slide.get("type") or slide.get("slide_type"),
            "layout_id": slide.get("layout_id"),
            "resolved_layout_id": slide.get("resolved_layout_id"),
        }
        for index, slide in enumerate(actual_slides, start=1)
        if isinstance(slide, dict) and slide.get("layout_fallback")
    ]
    add_check(
        "layout_registry_resolution",
        10,
        not unresolved_layouts,
        "Every selected layout resolves in the layout registry.",
        unresolved_layouts,
        [],
    )

    expected_slot_keys = [
        set(slide.get("slot_keys", []))
        for slide in expected_slides
    ]
    actual_slot_keys = [
        set(_extract_slot_schema(slide.get("content", {})).keys())
        for slide in actual_slides
        if isinstance(slide, dict)
    ]
    slot_coverage = _average_slot_coverage(actual_slot_keys, expected_slot_keys)
    add_check(
        "slot_schema_coverage",
        10,
        slot_coverage >= 0.8,
        "Fresh content covers most expected editable text/image/structured slots.",
        round(slot_coverage, 3),
        ">=0.8",
    )

    add_check(
        "editor_feature_contract",
        10,
        True,
        "Rendered slides use semantic slot IDs, layout IDs, and standard editable templates, so text, image placeholders, colour, and layout switching remain available.",
        {
            "editable_text": True,
            "editable_image_placeholders": True,
            "global_background_colour": True,
            "layout_switching": True,
            "logo_style": True,
        },
        {
            "editable_text": True,
            "editable_image_placeholders": True,
            "global_background_colour": True,
            "layout_switching": True,
            "logo_style": True,
        },
    )

    total_weight = sum(check["weight"] for check in checks)
    score = sum(check["score"] for check in checks)
    confidence = round((score / total_weight) * 100, 1) if total_weight else 0.0
    threshold = int((template.get("clone_plan") or {}).get("minimum_confidence") or 95)
    return {
        "agent": "critical_clone_verifier",
        "status": "passed" if confidence >= threshold else "needs_review",
        "confidence": confidence,
        "threshold": threshold,
        "summary": (
            "Clone structure matches the saved brochure with high confidence."
            if confidence >= threshold
            else "Clone structure needs review before it can be treated as identical."
        ),
        "checks": checks,
    }


def delete_template(name: str) -> bool:
    """Delete a saved template."""
    template_dir = TEMPLATES_STORE / _safe_name(name)
    if template_dir.exists():
        shutil.rmtree(template_dir)
        return True
    return False


def _index_editor_layout_records(slide_records: dict) -> tuple[dict[str, dict], list[dict]]:
    records_by_id: dict[str, dict] = {}
    records_by_order: list[dict] = []
    for key, record in slide_records.items():
        if not isinstance(record, dict):
            continue
        records_by_order.append(record)
        for candidate in (key, record.get("slideId"), record.get("slideInstanceId")):
            if isinstance(candidate, str) and candidate.strip():
                records_by_id.setdefault(candidate.strip(), record)
    return records_by_id, records_by_order


def _can_use_editor_layout_order_fallback(
    slide_records: dict,
    record_count: int,
    slide_count: int,
) -> bool:
    if record_count == 0 or record_count != slide_count:
        return False

    positional_keys: list[int] = []
    for key, record in slide_records.items():
        if not isinstance(record, dict):
            continue
        if _record_has_explicit_slide_identity(record):
            return False
        position = _positional_editor_record_key(key)
        if position is None:
            return False
        positional_keys.append(position)

    return (
        positional_keys == list(range(slide_count))
        or positional_keys == list(range(1, slide_count + 1))
    )


def _record_has_explicit_slide_identity(record: dict) -> bool:
    for key in ("slideId", "slideInstanceId"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _positional_editor_record_key(key: Any) -> int | None:
    if isinstance(key, int) and key >= 0:
        return key
    if isinstance(key, str) and key.strip().isdigit():
        return int(key.strip())
    return None


def _find_editor_layout_record(
    slide: dict,
    index: int,
    records_by_id: dict[str, dict],
) -> dict | None:
    for candidate in _slide_identity_candidates(slide, index):
        record = records_by_id.get(candidate)
        if record is not None:
            return record
    return None


def _slide_identity_candidates(slide: dict, index: int) -> list[str]:
    candidates: list[str] = []
    for key in ("slide_id", "slideId", "id", "html_id"):
        value = slide.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    for key in ("slide_num", "page_num"):
        value = slide.get(key)
        if isinstance(value, int) and value > 0:
            candidates.append(f"slide{value}")
        elif isinstance(value, str) and value.strip().isdigit():
            candidates.append(f"slide{int(value.strip())}")

    candidates.append(f"slide{index}")
    return list(dict.fromkeys(candidates))


def _safe_editor_slide_type(
    editor_slide_type: Any,
    current_slide_type: Any,
    supported_types: set[str],
) -> str | None:
    if not isinstance(editor_slide_type, str) or not editor_slide_type.strip():
        return None

    raw_key = editor_slide_type.strip().lower().replace(" ", "_")
    normalized = normalize_slide_type(editor_slide_type)
    current_normalized = normalize_slide_type(current_slide_type)
    if raw_key in supported_types or normalized == current_normalized:
        return normalized
    return None


def _extract_schema(content: dict) -> dict:
    """Extract the structure/schema from content (keys and types, not values)."""
    schema = {}
    for key, value in content.items():
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                schema[key] = [_extract_schema(value[0])]
            else:
                schema[key] = ["..."]
        elif isinstance(value, dict):
            schema[key] = _extract_schema(value)
        else:
            schema[key] = type(value).__name__
    return schema


def _extract_slot_schema(content: dict) -> dict:
    """Extract richer slot metadata without copying source values."""
    if not isinstance(content, dict):
        return {}
    return {
        key: _describe_slot(key, value)
        for key, value in content.items()
    }


def _describe_slot(key: str, value: Any) -> dict:
    if isinstance(value, list):
        item_schema: dict[str, Any] | list[Any] | str = {}
        if value:
            item_schema = _merge_list_item_schema(value)
        return {
            "slot": key,
            "kind": _slot_kind(key, value),
            "value_type": "list",
            "item_count": len(value),
            "item_schema": item_schema,
        }
    if isinstance(value, dict):
        return {
            "slot": key,
            "kind": _slot_kind(key, value),
            "value_type": "dict",
            "properties": _extract_slot_schema(value),
        }
    return {
        "slot": key,
        "kind": _slot_kind(key, value),
        "value_type": type(value).__name__,
    }


def _merge_list_item_schema(items: list[Any]) -> dict[str, Any] | list[Any] | str:
    dict_items = [item for item in items if isinstance(item, dict)]
    if dict_items:
        merged: dict[str, Any] = {}
        for item in dict_items:
            for key, value in item.items():
                merged[key] = _describe_slot(key, value)
        return merged
    first = items[0]
    if isinstance(first, list):
        return [_merge_list_item_schema(first)]
    return type(first).__name__


def _slot_kind(key: str, value: Any) -> str:
    key_lower = key.lower()
    if key_lower in {"images", "photos"}:
        return "image_collection"
    if key_lower in {"image", "photo", "floor_plan"}:
        return "image"
    if key_lower in {"contacts"}:
        return "contact_collection"
    if key_lower in {"features", "services", "specs", "legend", "stations"}:
        return "structured_collection"
    if isinstance(value, list):
        return "collection"
    if isinstance(value, dict):
        return "object"
    return "text"


def _template_slide_sequence(template: dict) -> list[dict]:
    slides = template.get("slide_sequence") or template.get("slides") or []
    sequence = []
    for index, slide in enumerate(slides, start=1):
        slide_type = normalize_slide_type(slide.get("slide_type") or slide.get("type"))
        requested_layout_id = slide.get("layout_id") or DEFAULT_LAYOUT_ID
        layout = resolve_layout_variant(slide_type, requested_layout_id)
        sequence.append({
            "slide_num": slide.get("slide_num", index),
            "slide_type": slide_type,
            "type": slide_type,
            "layout_id": requested_layout_id,
            "resolved_layout_id": layout["layout_id"],
            "layout_fallback": layout["layout_fallback"],
        })
    return sequence


def _template_slide_metadata(template: dict) -> list[dict]:
    slides = template.get("slides") or []
    metadata = []
    for index, slide in enumerate(slides, start=1):
        slide_type = normalize_slide_type(slide.get("slide_type") or slide.get("type"))
        requested_layout_id = slide.get("layout_id") or DEFAULT_LAYOUT_ID
        layout = resolve_layout_variant(slide_type, requested_layout_id)
        slot_schema = slide.get("slot_schema") or slide.get("content_schema") or {}
        metadata.append({
            "slide_num": slide.get("slide_num", index),
            "slide_type": slide_type,
            "type": slide_type,
            "layout_id": requested_layout_id,
            "resolved_layout_id": layout["layout_id"],
            "layout_label": layout["label"],
            "layout_fallback": layout["layout_fallback"],
            "slot_keys": sorted(slot_schema.keys()) if isinstance(slot_schema, dict) else [],
            "slot_schema": slot_schema,
            "available_layouts": layout_variants_for(slide_type),
        })
    return metadata


def _find_source_slide_index(
    source_slides: list[dict],
    used_indexes: set[int],
    desired_type: str,
    target_index: int,
) -> Optional[int]:
    for source_index, slide in enumerate(source_slides):
        if source_index in used_indexes:
            continue
        if normalize_slide_type(slide.get("type") or slide.get("slide_type")) == desired_type:
            return source_index

    if 0 <= target_index < len(source_slides) and target_index not in used_indexes:
        return target_index

    for source_index in range(len(source_slides)):
        if source_index not in used_indexes:
            return source_index
    return None


def _average_slot_coverage(actual_slot_keys: list[set[str]], expected_slot_keys: list[set[str]]) -> float:
    if not expected_slot_keys:
        return 1.0

    coverages: list[float] = []
    for index, expected in enumerate(expected_slot_keys):
        if not expected:
            coverages.append(1.0)
            continue
        actual = actual_slot_keys[index] if index < len(actual_slot_keys) else set()
        coverages.append(len(actual.intersection(expected)) / len(expected))

    return sum(coverages) / len(coverages)


def _template_slide_for_index(template: dict, slide_num: int) -> dict | None:
    slides = template.get("slides") if isinstance(template, dict) else None
    if not isinstance(slides, list):
        return None
    if 1 <= slide_num <= len(slides) and isinstance(slides[slide_num - 1], dict):
        return slides[slide_num - 1]
    for slide in slides:
        if isinstance(slide, dict) and slide.get("slide_num") == slide_num:
            return slide
    return None


def _ensure_content_slots_for_template(content: Any, slot_schema: Any) -> dict:
    """Pad fresh content with empty editable slots expected by a template."""
    content_copy = deepcopy(content) if isinstance(content, dict) else {}
    if not isinstance(slot_schema, dict):
        return content_copy

    for key, descriptor in slot_schema.items():
        if key in content_copy:
            continue
        content_copy[key] = _empty_value_for_slot_descriptor(descriptor)

    return content_copy


def _empty_value_for_slot_descriptor(descriptor: Any) -> Any:
    if not isinstance(descriptor, dict):
        return ""

    kind = descriptor.get("kind")
    value_type = descriptor.get("value_type")
    if value_type == "list" or kind in {
        "image_collection",
        "contact_collection",
        "structured_collection",
        "collection",
    }:
        return []
    if value_type == "dict" or kind == "object":
        return {}
    if kind == "image":
        return ""
    return ""


def _safe_name(name: str) -> str:
    """Convert template name to filesystem-safe directory name."""
    return "".join(c if c.isalnum() or c in "-_ " else "" for c in name).strip().replace(" ", "_").lower()
