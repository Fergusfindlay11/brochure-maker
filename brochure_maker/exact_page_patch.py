"""Apply page-scoped repair patches through exact editor state."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


PATCH_SCHEMA = "brochure-maker.page-repair-patch.v1"
APPLICATION_SCHEMA = "brochure-maker.page-repair-patch-application.v1"

TEXT_STYLE_ALLOWLIST = {
    "left",
    "top",
    "width",
    "height",
    "white-space",
    "display",
    "align-items",
    "justify-content",
    "text-align",
}

IMAGE_STYLE_ALLOWLIST = {"left", "top", "width", "height"}
IMAGE_FITS = {"cover", "contain", "cover-left"}
MAX_AUTO_OPERATIONS = 8


def build_page_repair_patch(
    *,
    project_id: str,
    page_number: int,
    accepted: bool,
    graph_page: dict[str, Any],
    assessment_page: dict[str, Any],
) -> dict[str, Any]:
    """Build a conservative page patch from page-scoped assessment evidence.

    The planner only emits operations for explicit assessment element IDs on
    failed pages. It does not invent content or coordinates; it converts
    design-graph bboxes into editor-state overrides that the existing exact
    export path already understands.
    """
    status = "not-needed" if accepted else "pending-agent-patch"
    operations: list[dict[str, Any]] = []
    if not accepted:
        operations = _planned_operations(page_number, graph_page, assessment_page)
        status = "auto-patch-ready" if operations else "pending-agent-patch"
    return {
        "schema": PATCH_SCHEMA,
        "project_id": project_id,
        "page_number": page_number,
        "status": status,
        "planner": {
            "source": "page-assessment element_ids plus design-page bboxes",
            "strategy": "emit only allowed text/image layout operations for elements named by failed-page findings",
            "operation_count": len(operations),
        },
        "operations": operations,
        "operation_contract": _operation_contract(page_number),
    }


def apply_page_repair_patch(
    project_dir: str | Path,
    patch_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply a declarative page-only repair patch to ``editor_state.json``.

    Exact clean export already consumes ``editor_state.json`` for text layout,
    image layout, and image fit. Keeping repairs in that state path gives the
    page repair loop a real targeted render path without regenerating the whole
    project.
    """
    project_path = Path(project_dir).expanduser().resolve()
    patch_file = Path(patch_path).expanduser().resolve()
    patch = _load_json(patch_file)
    page_number = int(patch.get("page_number") or patch.get("page") or 0)
    operations = patch.get("operations") if isinstance(patch.get("operations"), list) else []
    state_path = project_path / "editor_state.json"
    state_before = _load_json(state_path)
    html = _load_html(project_path / "brochure.html")

    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    state = _normalise_state(state_before)

    for index, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict):
            rejected.append({"index": index, "reason": "operation is not an object"})
            continue
        result = _apply_operation(state, html, page_number, operation, index)
        if result.get("applied"):
            applied.append(result)
        else:
            rejected.append(result)

    state_after_fingerprint = _state_fingerprint(state)
    changed = _state_fingerprint(state_before) != state_after_fingerprint
    if applied:
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    report = {
        "schema": APPLICATION_SCHEMA,
        "project_id": project_path.name,
        "project_dir": str(project_path),
        "patch": str(patch_file),
        "page_number": page_number,
        "applied": bool(applied),
        "changed_state": changed,
        "operation_count": len(operations),
        "applied_count": len(applied),
        "rejected_count": len(rejected),
        "applied_operations": applied,
        "rejected_operations": rejected,
        "state_path": str(state_path),
        "state_before": _state_fingerprint(state_before),
        "state_after": state_after_fingerprint,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scope_guard": {
            "repair_scope": "single page editor_state patch",
            "page_number": page_number,
            "allowed_text_prefix": f"exact-page{page_number}-text",
            "allowed_image_prefix": f"exact-page{page_number}-image",
        },
    }
    output = Path(output_path).expanduser().resolve() if output_path else patch_file.with_name("page-repair-patch-application.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _planned_operations(
    page_number: int,
    graph_page: dict[str, Any],
    assessment_page: dict[str, Any],
) -> list[dict[str, Any]]:
    elements = {
        str(element.get("id")): element
        for element in graph_page.get("elements") or []
        if isinstance(element, dict) and element.get("id")
    }
    page_size = graph_page.get("size") if isinstance(graph_page.get("size"), dict) else {}
    findings = assessment_page.get("findings") if isinstance(assessment_page.get("findings"), list) else []
    operations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        subsystem = str(finding.get("repair_subsystem") or "")
        if subsystem.startswith("typography"):
            allowed_kinds = {"text"}
        elif subsystem.startswith("images"):
            allowed_kinds = {"image"}
        else:
            continue
        for element_id in finding.get("element_ids") or []:
            element = elements.get(str(element_id))
            if not element:
                continue
            operation = _operation_for_element(page_number, element, page_size, finding, allowed_kinds)
            if not operation:
                continue
            key = json.dumps(operation, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            operations.append(operation)
            if len(operations) >= MAX_AUTO_OPERATIONS:
                return operations
    return operations


def _operation_for_element(
    page_number: int,
    element: dict[str, Any],
    page_size: dict[str, Any],
    finding: dict[str, Any],
    allowed_kinds: set[str],
) -> dict[str, Any] | None:
    element_type = str(element.get("type") or "")
    role = str(element.get("role") or "")
    if "text" in allowed_kinds and (
        element_type == "text" or role in {"body", "caption", "section-heading", "cover-title", "table-status", "agent-contact"}
    ):
        save_id = _save_id_for_element(page_number, str(element.get("id") or ""), "text")
        styles = _bbox_styles(element, page_size, min_height_px=14)
        if not save_id or not styles:
            return None
        styles.setdefault("white-space", "pre-wrap")
        return {
            "type": "text-layout",
            "save_id": save_id,
            "styles": styles,
            "source_element_id": element.get("id"),
            "source_finding": _finding_summary(finding),
            "rationale": "Align editable text box to the PDF-derived design-graph bbox for this failed page finding.",
        }
    if "image" in allowed_kinds and (element_type in {"image", "floorplan"} or role in {"photo-region", "space-plan"}):
        save_id = _save_id_for_element(page_number, str(element.get("id") or ""), "image")
        styles = _bbox_styles(element, page_size, min_height_px=24)
        if not save_id or not styles:
            return None
        return {
            "type": "image-layout",
            "save_id": save_id,
            "fit": "contain" if role == "space-plan" or element_type == "floorplan" else "cover",
            "styles": styles,
            "source_element_id": element.get("id"),
            "source_finding": _finding_summary(finding),
            "rationale": "Align replaceable image slot to the PDF-derived design-graph bbox for this failed page finding.",
        }
    return None


def _save_id_for_element(page_number: int, element_id: str, kind: str) -> str | None:
    pattern = rf"p{page_number:03d}-{kind}-(\d+)"
    match = re.fullmatch(pattern, element_id)
    if not match:
        return None
    return f"exact-page{page_number}-{kind}{int(match.group(1))}"


def _bbox_styles(element: dict[str, Any], page_size: dict[str, Any], *, min_height_px: int) -> dict[str, str]:
    bbox = element.get("bbox_raw") if isinstance(element.get("bbox_raw"), dict) else None
    if not bbox:
        bbox = element.get("raw_bbox") if isinstance(element.get("raw_bbox"), dict) else None
    if not bbox:
        normalized = element.get("bbox") if isinstance(element.get("bbox"), dict) else None
        width = _float(page_size.get("width"))
        height = _float(page_size.get("height"))
        if normalized and width and height:
            bbox = {
                "x": _float(normalized.get("x"), 0) * width,
                "y": _float(normalized.get("y"), 0) * height,
                "width": _float(normalized.get("width"), 0) * width,
                "height": _float(normalized.get("height"), 0) * height,
            }
    if not bbox:
        return {}
    left = _float(bbox.get("x"), None)
    top = _float(bbox.get("y"), None)
    width = _float(bbox.get("width"), None)
    height = _float(bbox.get("height"), None)
    if left is None or top is None or width is None or height is None or width <= 0 or height <= 0:
        return {}
    return {
        "left": _px(left),
        "top": _px(top),
        "width": _px(width),
        "height": _px(max(float(min_height_px), height)),
    }


def _finding_summary(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue": finding.get("issue") or finding.get("problem"),
        "repair_subsystem": finding.get("repair_subsystem") or finding.get("repair_task"),
        "evidence": finding.get("evidence"),
        "severity": finding.get("severity"),
    }


def _operation_contract(page_number: int) -> dict[str, Any]:
    return {
        "allowed_operation_types": ["text-layout", "image-layout"],
        "text_save_id_prefix": f"exact-page{page_number}-text",
        "image_save_id_prefix": f"exact-page{page_number}-image",
        "write_target": "editor_state.json",
        "scope": "single failed page only",
        "note": "Operations are derived from failed page-packet evidence; unchanged rerenders cannot be accepted as page repairs.",
    }


def _apply_operation(
    state: dict[str, Any],
    html: BeautifulSoup,
    page_number: int,
    operation: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    op_type = str(operation.get("type") or operation.get("operation") or "").strip()
    if op_type in {"text-layout", "text-layout-style", "text-style"}:
        return _apply_text_layout_operation(state, html, page_number, operation, index)
    if op_type in {"image-layout", "image-fit", "image-style"}:
        return _apply_image_operation(state, page_number, operation, index)
    return {
        "index": index,
        "type": op_type,
        "applied": False,
        "reason": "unsupported operation type",
    }


def _apply_text_layout_operation(
    state: dict[str, Any],
    html: BeautifulSoup,
    page_number: int,
    operation: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    save_id = str(operation.get("save_id") or operation.get("target") or "").strip()
    reason = _page_save_id_rejection(save_id, page_number, "text")
    if reason:
        return {"index": index, "type": "text-layout", "save_id": save_id, "applied": False, "reason": reason}
    styles = operation.get("styles") if isinstance(operation.get("styles"), dict) else {}
    allowed_styles = _allowed_styles(styles, TEXT_STYLE_ALLOWLIST)
    if not allowed_styles:
        return {"index": index, "type": "text-layout", "save_id": save_id, "applied": False, "reason": "no allowed styles"}
    target = html.select_one(f'[data-save-id="{_css_attr(save_id)}"]')
    if target is None:
        return {"index": index, "type": "text-layout", "save_id": save_id, "applied": False, "reason": "target not found in brochure.html"}
    editable_texts = state.setdefault("editableTexts", {})
    item = editable_texts.setdefault(save_id, {})
    if not isinstance(item, dict):
        item = {}
        editable_texts[save_id] = item
    item.setdefault("html", "".join(str(child) for child in target.contents))
    item["edited"] = bool(item.get("edited"))
    typography = item.setdefault("typography", {})
    if isinstance(typography, dict):
        role = target.get("data-typography-role")
        if role and not typography.get("role"):
            typography["role"] = str(role)
    layout = item.setdefault("layout", {})
    layout_styles = layout.setdefault("styles", {})
    layout_styles.update(allowed_styles)
    if operation.get("title_stack"):
        layout["titleStack"] = True
    if operation.get("hidden"):
        layout["hidden"] = True
    return {
        "index": index,
        "type": "text-layout",
        "save_id": save_id,
        "applied": True,
        "styles": allowed_styles,
    }


def _apply_image_operation(
    state: dict[str, Any],
    page_number: int,
    operation: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    save_id = str(operation.get("save_id") or operation.get("target") or "").strip()
    reason = _page_save_id_rejection(save_id, page_number, "image")
    if reason:
        return {"index": index, "type": "image-layout", "save_id": save_id, "applied": False, "reason": reason}
    images = state.setdefault("images", {})
    item = images.setdefault(save_id, {})
    if not isinstance(item, dict):
        item = {}
        images[save_id] = item
    styles = operation.get("styles") if isinstance(operation.get("styles"), dict) else {}
    allowed_styles = _allowed_styles(styles, IMAGE_STYLE_ALLOWLIST)
    item.update(allowed_styles)
    fit = operation.get("fit")
    if isinstance(fit, str) and fit in IMAGE_FITS:
        item["fit"] = fit
    if operation.get("bgImage"):
        item["bgImage"] = str(operation["bgImage"])
    changed_keys = sorted([*allowed_styles.keys(), *(["fit"] if "fit" in item else []), *(["bgImage"] if operation.get("bgImage") else [])])
    if not changed_keys:
        return {"index": index, "type": "image-layout", "save_id": save_id, "applied": False, "reason": "no allowed image updates"}
    return {
        "index": index,
        "type": "image-layout",
        "save_id": save_id,
        "applied": True,
        "updated": changed_keys,
    }


def _page_save_id_rejection(save_id: str, page_number: int, kind: str) -> str | None:
    if page_number <= 0:
        return "patch does not declare a valid page number"
    prefix = f"exact-page{page_number}-{kind}"
    if not save_id:
        return "missing save_id"
    if not save_id.startswith(prefix):
        return f"save_id is outside page {page_number} {kind} scope"
    if not re.fullmatch(r"exact-page\d+-(text|image)\d+", save_id):
        return "save_id is not an exact PDF text/image save id"
    return None


def _allowed_styles(styles: dict[str, Any], allowlist: set[str]) -> dict[str, str]:
    allowed: dict[str, str] = {}
    for key, value in styles.items():
        key_text = str(key).strip()
        value_text = str(value).strip()
        if key_text in allowlist and value_text:
            allowed[key_text] = value_text
    return allowed


def _float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _px(value: float) -> str:
    return f"{round(float(value), 3):g}px"


def _normalise_state(state: dict[str, Any]) -> dict[str, Any]:
    normalised = json.loads(json.dumps(state)) if state else {}
    normalised.setdefault("editableTexts", {})
    normalised.setdefault("images", {})
    return normalised


def _load_html(path: Path) -> BeautifulSoup:
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        html = ""
    return BeautifulSoup(html, "html.parser")


def _state_fingerprint(state: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(state if isinstance(state, dict) else {}, sort_keys=True).encode("utf-8")
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply a page-scoped exact repair patch to editor state.")
    parser.add_argument("project_dir")
    parser.add_argument("patch")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    report = apply_page_repair_patch(args.project_dir, args.patch, output_path=args.output)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
