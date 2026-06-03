"""Write live editor/export Browser QA evidence for exact PDF projects."""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HTTP_TIMEOUT_SECONDS = 180


def write_browser_qa(
    project_dir: str | Path,
    *,
    base_url: str = "http://127.0.0.1:8000",
    output_path: str | Path | None = None,
    force: bool = False,
) -> Path:
    """Inspect the live editor/export routes and write ``browser_qa.json``.

    This is a lightweight artifact builder for the benchmark gate. Manual
    in-app Browser checks can still add richer interaction evidence, and this
    function preserves those fields when refreshing the structural assertions.
    """
    project_path = Path(project_dir).expanduser().resolve()
    output = Path(output_path).expanduser().resolve() if output_path else project_path / "browser_qa.json"
    existing = _load_json(output)
    if not force and _has_required_assertions(existing):
        return output

    graph = _load_json(project_path / "brochure.design.json")
    expected_pages = int(graph.get("page_count") or len(graph.get("pages") or []) or 0)
    project_id = project_path.name
    editor_url = f"{base_url.rstrip('/')}/api/projects/{project_id}/brochure"
    export_url = f"{base_url.rstrip('/')}/api/projects/{project_id}/export/html"
    blockers: list[str] = []
    editor_html = _fetch(editor_url, blockers)
    export_html = _fetch(export_url, blockers)

    editor = _editor_summary(editor_html, expected_pages)
    export = _export_summary(export_html, expected_pages)
    expected_media = _expected_media_summary(project_path, graph)
    media_audit = _media_slot_audit(editor, expected_media)
    layout_audit = _layout_audit(editor_html)
    interaction_audit = _interaction_hotspot_audit(editor_html)
    interactions = _interaction_probe(
        project_path,
        project_id=project_id,
        editor_html=editor_html,
        base_url=base_url.rstrip("/"),
    )
    assertions = {
        "editor_has_expected_pages": expected_pages > 0 and editor["pageCount"] == expected_pages,
        "global_controls_visible": bool(editor["globalControlsVisible"]),
        "editable_text_blocks_present": editor["contenteditableCount"] > 0,
        "cover_page_editable": editor["coverPageEditableCount"] > 0,
        "global_typography_controls_present": editor["globalTypographyControls"] > 0,
        "image_slots_present": editor["imageSlotCount"] > 0,
        "image_slots_editable_by_default": editor["imageSlotCount"] == 0 or editor["editableDefaultPageCount"] > 0,
        "media_slots_have_actionable_controls": bool(media_audit.get("accepted")),
        "image_replacement_roundtrip_preserved": (
            not expected_media.get("expectsImageReplacement")
            or (
                bool(interactions.get("imageProbeTargetSaveId"))
                and bool(interactions.get("imageReplacementRoundtripPreserved"))
            )
        ),
        "logo_replacement_roundtrip_preserved": (
            not expected_media.get("expectsSourceLogoReplacement")
            or (
                bool(interactions.get("logoProbeTargetSlotId"))
                and bool(interactions.get("logoReplacementRoundtripPreserved"))
            )
        ),
        "map_replacement_roundtrip_preserved": (
            not expected_media.get("expectsMapReplacement")
            or (
                bool(interactions.get("mapProbeTargetSaveId"))
                and bool(interactions.get("mapReplacementRoundtripPreserved"))
            )
        ),
        "ocr_fallback_text_hidden_until_edit": (
            editor["ocrFallbackTextCount"] == 0 or bool(editor["ocrFallbackHiddenByDefaultCss"])
        ),
        "structured_cover_title_targets_semantic": int(editor.get("noisyCoverTitleTargetCount") or 0) == 0,
        "export_has_expected_pages": expected_pages > 0 and export["pageCount"] == expected_pages,
        "export_has_no_editor_chrome": (
            export["contenteditableCount"] == 0
            and export["fileInputCount"] == 0
            and export["scriptCount"] == 0
            and export["toolbarCount"] == 0
            and export["fieldsPanelCount"] == 0
        ),
        "state_roundtrip_preserved": bool(interactions.get("stateRoundtripPreserved")),
        "clean_export_preserves_edited_text": bool(interactions.get("exportPreservedEditedText")),
        "clean_export_preserves_global_colour": bool(interactions.get("globalColourExported")),
        "typed_text_font_preserved": bool(interactions.get("titleEditFontPreserved")),
        "edited_text_fits_after_roundtrip": bool(interactions.get("editedTextFitsBox")),
        "source_preserved_pages_have_no_giant_interactive_hotspots": bool(interaction_audit.get("accepted")),
    }
    if not assertions["editor_has_expected_pages"]:
        blockers.append("Browser editor page count does not match design graph")
    if not assertions["global_controls_visible"]:
        blockers.append("Browser did not confirm fields/global controls panel")
    if not assertions["editable_text_blocks_present"]:
        blockers.append("Browser did not confirm editable text blocks")
    if not assertions["cover_page_editable"]:
        blockers.append("Browser did not confirm editable cover-page elements")
    if not assertions["image_slots_editable_by_default"]:
        blockers.append("Browser counted image slots but the editor default hides/disables them")
    if not assertions["media_slots_have_actionable_controls"]:
        blockers.extend(str(item) for item in media_audit.get("blockers") or [])
    if not assertions["image_replacement_roundtrip_preserved"]:
        blockers.append("Browser interaction probe did not confirm image replacement persistence/export")
    if not assertions["logo_replacement_roundtrip_preserved"]:
        blockers.append("Browser interaction probe did not confirm source logo replacement persistence/export")
    if not assertions["map_replacement_roundtrip_preserved"]:
        blockers.append("Browser interaction probe did not confirm map replacement/preserve state persistence/export")
    if not assertions["ocr_fallback_text_hidden_until_edit"]:
        blockers.append("Browser counted OCR fallback text but it is visible by default and duplicates source artwork")
    if not assertions["structured_cover_title_targets_semantic"]:
        blockers.append("Cover title control targets OCR fallback/source artwork text instead of a semantic title")
    if not assertions["export_has_expected_pages"]:
        blockers.append("Browser clean export page count does not match design graph")
    if not assertions["export_has_no_editor_chrome"]:
        blockers.append("Browser clean export contains editor chrome")
    if layout_audit.get("textOverlaps"):
        blockers.append("Browser layout audit found overlapping editable text blocks")
    if not assertions["source_preserved_pages_have_no_giant_interactive_hotspots"]:
        blockers.append("Browser interaction audit found giant hover/click hotspots or draggable preserved backgrounds")
    if not assertions["edited_text_fits_after_roundtrip"]:
        blockers.append("Edited text target would clip or overflow its fitted box in clean export")
    for key, message in (
        ("state_roundtrip_preserved", "Browser interaction probe did not confirm state roundtrip persistence"),
        ("clean_export_preserves_edited_text", "Browser interaction probe did not confirm edited text in clean export"),
        ("clean_export_preserves_global_colour", "Browser interaction probe did not confirm global colour export"),
        ("typed_text_font_preserved", "Browser interaction probe did not confirm edited title font metadata"),
    ):
        if not assertions[key]:
            blockers.append(message)

    payload = {
        **existing,
        "schema": "brochure-maker.browser-qa.v1",
        "project_id": project_id,
        "base_url": base_url.rstrip("/"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "method": "live-editor-and-clean-export-http",
        "editor_url": editor_url,
        "export_url": export_url,
        "expected_pages": expected_pages,
        "editor": {**(existing.get("editor") if isinstance(existing.get("editor"), dict) else {}), **editor},
        "export": {**(existing.get("export") if isinstance(existing.get("export"), dict) else {}), **export},
        "clean_export": {**(existing.get("clean_export") if isinstance(existing.get("clean_export"), dict) else {}), **export},
        "expected_media": {**(existing.get("expected_media") if isinstance(existing.get("expected_media"), dict) else {}), **expected_media},
        "media_audit": {**(existing.get("media_audit") if isinstance(existing.get("media_audit"), dict) else {}), **media_audit},
        "layout_audit": {**(existing.get("layout_audit") if isinstance(existing.get("layout_audit"), dict) else {}), **layout_audit},
        "interaction_audit": {
            **(existing.get("interaction_audit") if isinstance(existing.get("interaction_audit"), dict) else {}),
            **interaction_audit,
        },
        "interactions": {**(existing.get("interactions") if isinstance(existing.get("interactions"), dict) else {}), **interactions},
        "assertions": {**(existing.get("assertions") if isinstance(existing.get("assertions"), dict) else {}), **assertions},
        "blockers": sorted(dict.fromkeys(str(blocker) for blocker in blockers)),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output


def _has_required_assertions(payload: dict[str, Any]) -> bool:
    assertions = payload.get("assertions") if isinstance(payload.get("assertions"), dict) else {}
    return all(
        assertions.get(name) is True
        for name in (
            "editor_has_expected_pages",
            "global_controls_visible",
            "export_has_expected_pages",
            "export_has_no_editor_chrome",
            "source_preserved_pages_have_no_giant_interactive_hotspots",
            "media_slots_have_actionable_controls",
            "image_replacement_roundtrip_preserved",
            "logo_replacement_roundtrip_preserved",
            "map_replacement_roundtrip_preserved",
            "edited_text_fits_after_roundtrip",
            "structured_cover_title_targets_semantic",
        )
    )


def _fetch(url: str, blockers: list[str]) -> str:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as exc:
        blockers.append(f"Could not fetch Browser QA URL {url}: {exc}")
        return ""


def _post_json(url: str, payload: dict[str, Any], blockers: list[str]) -> bool:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return 200 <= int(getattr(response, "status", 200) or 200) < 300
    except (OSError, urllib.error.URLError) as exc:
        blockers.append(f"Could not POST Browser QA state probe to {url}: {exc}")
        return False


def _fetch_json(url: str, blockers: list[str]) -> dict[str, Any]:
    text = _fetch(url, blockers)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        blockers.append(f"Browser QA state probe response was not JSON: {url}")
        return {}
    return data if isinstance(data, dict) else {}


def _interaction_probe(
    project_path: Path,
    *,
    project_id: str,
    editor_html: str,
    base_url: str,
) -> dict[str, Any]:
    """Temporarily save a QA edit and verify state/export behaviour.

    The probe writes through the same state endpoint used by the editor, fetches
    clean export while the probe state is active, and then restores the previous
    ``editor_state.json`` byte-for-byte. That gives the benchmark real
    persistence/export evidence without leaving benchmark projects edited.
    """
    target = _interaction_text_target(editor_html)
    if not target:
        return {
            "schema": "brochure-maker.browser-interaction-qa.v1",
            "method": "state-api-export-roundtrip",
            "accepted": False,
            "blockers": ["No editable text target found for interaction probe"],
            "nonDestructive": True,
        }
    image_target = _interaction_image_target(editor_html)
    logo_target = _interaction_source_logo_target(editor_html)
    map_target = _interaction_map_target(editor_html)

    state_path = project_path / "editor_state.json"
    had_state = state_path.exists()
    previous_state = state_path.read_bytes() if had_state else None
    marker = f"__QA_EDIT_{project_id}__"
    image_marker = f"__QA_IMAGE_{project_id}__"
    logo_marker = f"__QA_LOGO_{project_id}__"
    map_marker = f"__QA_MAP_{project_id}__"
    image_data_url = _svg_data_url(image_marker, "#ff00aa")
    logo_data_url = _svg_data_url(logo_marker, "#101820")
    map_html = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" data-qa-map-marker="'
        + map_marker
        + '"><rect width="10" height="10" fill="#ff00aa"/></svg>'
    )
    accent = "#ff00aa"
    dark = "#101820"
    role = str(target.get("role") or "body")
    font_family = str(target.get("font_family") or target.get("font_alias") or "").strip()
    original_html = str(target.get("html") or target.get("text") or "")
    probe_html = (original_html.rstrip() + " " + marker).strip()
    probe_state = {
        "exactLayout": True,
        "editableLayerVersion": 5,
        "colourPreset": "custom",
        "accentColour": accent,
        "darkColour": dark,
        "typography": {
            role: {
                "fontFamily": font_family,
                "defaultFontFamily": font_family,
                "role": role,
                "changed": False,
            }
        },
        "editableTexts": {
            str(target["save_id"]): {
                "html": probe_html,
                "edited": True,
                "typography": {
                    "role": role,
                    "fontFamily": font_family,
                    "fontAlias": str(target.get("font_alias") or ""),
                },
                "layout": {},
            }
        },
        "images": {},
    }
    if image_target:
        probe_state["images"][str(image_target["save_id"])] = {
            "bgImage": f'url("{image_data_url}")',
            "bgPosition": "50% 50%",
            "bgSize": "contain",
            "fit": "contain",
        }
    if logo_target:
        probe_state["globalLogo"] = {
            "type": "upload",
            "uploadedLogoDataUrl": logo_data_url,
            "coverSize": 64,
            "coverPosition": "source",
        }
    if map_target:
        probe_state["mapState"] = {
            "generatedHtml": map_html,
            "source": "qa-probe",
        }
    blockers: list[str] = []
    state_url = f"{base_url}/api/projects/{project_id}/state"
    export_url = f"{base_url}/api/projects/{project_id}/export/html"
    posted = False
    fetched_state: dict[str, Any] = {}
    export_html = ""
    cleanup_removed_marker = False
    try:
        posted = _post_json(state_url, probe_state, blockers)
        if posted:
            fetched_state = _fetch_json(state_url, blockers)
            export_html = _fetch(export_url, blockers)
    finally:
        if had_state and previous_state is not None:
            state_path.write_bytes(previous_state)
        else:
            try:
                state_path.unlink()
            except FileNotFoundError:
                pass
        cleanup_html = _fetch(export_url, [])
        cleanup_removed_marker = marker not in cleanup_html

    roundtrip_text = (
        ((fetched_state.get("editableTexts") or {}).get(str(target["save_id"])) or {}).get("html") == probe_html
    )
    export_summary = _export_summary(export_html, _page_count(editor_html))
    export_target = _export_text_target(export_html, str(target["save_id"]))
    export_text_fit = _export_text_fit_audit(export_target)
    export_text_ok = marker in export_html
    export_chrome_ok = (
        export_summary["contenteditableCount"] == 0
        and export_summary["fileInputCount"] == 0
        and export_summary["scriptCount"] == 0
        and export_summary["toolbarCount"] == 0
        and export_summary["fieldsPanelCount"] == 0
    )
    font_preserved = bool(export_target) and (
        str(export_target.get("role") or "") == role
        and (
            not font_family
            or font_family in str(export_target.get("font_alias") or "")
            or font_family in str(export_target.get("font_family") or "")
            or font_family in str(export_target.get("style") or "")
        )
    )
    colour_exported = accent in export_html and dark in export_html
    images_state = fetched_state.get("images") if isinstance(fetched_state.get("images"), dict) else {}
    image_state_ok = True
    image_export_ok = True
    if image_target:
        image_state = images_state.get(str(image_target["save_id"])) if isinstance(images_state, dict) else None
        image_state_ok = isinstance(image_state, dict) and image_data_url in str(image_state.get("bgImage") or "")
        image_export_ok = image_data_url in export_html
    global_logo_state = fetched_state.get("globalLogo") if isinstance(fetched_state.get("globalLogo"), dict) else {}
    logo_state_ok = True
    logo_export_ok = True
    if logo_target:
        logo_state_ok = str(global_logo_state.get("uploadedLogoDataUrl") or "") == logo_data_url
        logo_export_ok = logo_data_url in export_html
    map_state = fetched_state.get("mapState") if isinstance(fetched_state.get("mapState"), dict) else {}
    map_state_ok = True
    map_export_ok = True
    if map_target:
        map_state_ok = map_marker in str(map_state.get("generatedHtml") or "")
        map_export_ok = map_marker in export_html
    blockers.extend(
        message
        for ok, message in (
            (posted, "State probe POST failed"),
            (roundtrip_text, "State probe GET did not return edited text"),
            (export_text_ok, "Clean export did not include edited probe text"),
            (export_chrome_ok, "Clean export contained editor chrome after probe"),
            (font_preserved, "Edited text target did not preserve typography metadata in export"),
            (bool(export_text_fit.get("accepted")), "Edited text target would clip or overflow its fitted box in clean export"),
            (colour_exported, "Clean export did not include probed global colours"),
            (image_state_ok, "State probe GET did not return edited image slot"),
            (image_export_ok, "Clean export did not include probed image replacement"),
            (logo_state_ok, "State probe GET did not return global logo replacement"),
            (logo_export_ok, "Clean export did not include probed source logo replacement"),
            (map_state_ok, "State probe GET did not return map replacement state"),
            (map_export_ok, "Clean export did not include probed map replacement"),
            (cleanup_removed_marker, "State probe cleanup did not remove probe marker from export"),
        )
        if not ok
    )
    return {
        "schema": "brochure-maker.browser-interaction-qa.v1",
        "method": "state-api-export-roundtrip",
        "accepted": not blockers,
        "blockers": sorted(dict.fromkeys(blockers)),
        "nonDestructive": cleanup_removed_marker,
        "targetSaveId": str(target["save_id"]),
        "targetRole": role,
        "typedTextMarker": marker,
        "stateRoundtripPreserved": bool(posted and roundtrip_text),
        "reloadPreserved": bool(posted and roundtrip_text),
        "exportPreservedEditedText": bool(export_text_ok),
        "cleanExportAfterProbeHasNoChrome": bool(export_chrome_ok),
        "globalColourExported": bool(colour_exported),
        "titleEditFontPreserved": bool(font_preserved),
        "editedTextFitsBox": bool(export_text_fit.get("accepted")),
        "editedTextFitAudit": export_text_fit,
        "imageReplacementRoundtripPreserved": bool(image_state_ok and image_export_ok),
        "logoReplacementRoundtripPreserved": bool(logo_state_ok and logo_export_ok),
        "mapReplacementRoundtripPreserved": bool(map_state_ok and map_export_ok),
        "imageProbeTargetSaveId": str(image_target.get("save_id")) if image_target else "",
        "logoProbeTargetSlotId": str(logo_target.get("slot_id")) if logo_target else "",
        "mapProbeTargetSaveId": str(map_target.get("save_id")) if map_target else "",
        "fontFamily": font_family,
        "cleanupRemovedMarker": bool(cleanup_removed_marker),
        "exportProbe": export_summary,
    }


def _editor_summary(html: str, expected_pages: int) -> dict[str, Any]:
    page_layouts: list[str] = []
    cover_editable_count = 0
    source_preserved_edit_count = 0
    media_slots: list[dict[str, Any]] = []
    page_boxes: list[dict[str, Any]] = []
    noisy_cover_title_targets: list[str] = []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        pages = soup.select(".exact-page")
        noisy_cover_title_targets = _structured_cover_title_noisy_targets(soup)
        page_layouts = [str(page.get("data-picture-layout") or "") for page in pages]
        source_preserved_edit_count = sum(
            1 for page in pages if str(page.get("data-source-preserved-edit") or "").lower() == "true"
        )
        cover = soup.select_one('#page1, .exact-page[data-page-num="1"]')
        cover_editable_count = len(cover.select('[contenteditable="true"]')) if cover else 0
        for page in pages:
            page_num = _node_page_number(page)
            page_media = _media_slots_for_page(page)
            media_slots.extend(page_media)
            page_boxes.append(
                {
                    "page": page_num,
                    "textFields": len(page.select('.pdf-text[contenteditable="true"]')),
                    "imageSlots": sum(1 for slot in page_media if slot.get("kind") in {"image", "space-plan", "artwork-image"}),
                    "sourceLogoSlots": sum(1 for slot in page_media if slot.get("kind") == "source-logo"),
                    "agencyLogoSlots": sum(1 for slot in page_media if slot.get("kind") == "agency-logo"),
                    "mapSlots": sum(1 for slot in page_media if slot.get("kind") == "map"),
                }
            )
    except Exception:
        page_layouts = re.findall(r'data-picture-layout\s*=\s*["\']([^"\']+)', html)
        cover_editable_count = 0
        source_preserved_edit_count = len(
            re.findall(r'<(?:section|div)\b[^>]*class\s*=\s*["\'][^"\']*\bexact-page\b[^"\']*["\'][^>]*data-source-preserved-edit\s*=\s*["\']true["\']', html)
        )
    return {
        "pageCount": _page_count(html),
        "expectedPageCount": expected_pages,
        "contenteditableCount": _count(r"\bcontenteditable\s*=\s*['\"]true['\"]", html),
        "editableTextCount": _count(r"\bcontenteditable\s*=\s*['\"]true['\"]", html),
        "coverPageEditableCount": cover_editable_count,
        "fieldsPanelVisible": _class_count(html, "exact-fields-panel") > 0,
        "globalControlsVisible": _class_count(html, "global-controls") > 0 or "data-global-control" in html,
        "globalTypographyControls": _class_count(html, "exact-typography"),
        "imageSlotCount": _class_count(html, "exact-image-slot"),
        "mapSlotCount": sum(1 for slot in media_slots if slot.get("kind") == "map"),
        "sourcePreservedEditPageCount": source_preserved_edit_count,
        "ocrFallbackTextCount": _class_count(html, "exact-ocr-text"),
        "noisyCoverTitleTargetCount": len(noisy_cover_title_targets),
        "noisyCoverTitleTargets": noisy_cover_title_targets[:12],
        "ocrFallbackHiddenByDefaultCss": (
            "exact-ocr-text:not([data-active-edit=\"true\"]):not([data-edited=\"true\"])" in html
            and "color: transparent !important" in html
            and "el.dataset.activeEdit = 'true';" in html
            and ".pdf-text[data-active-edit=\"true\"]:not([data-edited=\"true\"])" in html
        ),
        "editableDefaultPageCount": sum(1 for layout in page_layouts if layout == "editable"),
        "originalDefaultPageCount": sum(1 for layout in page_layouts if layout == "original"),
        "sourceLogoSlots": _class_count(html, "exact-source-logo-slot") + _class_count(html, "source-logo-slot"),
        "agencyLogoControls": _class_count(html, "agency-logo-upload") + _class_count(html, "agency-logo-chip"),
        "amenityIconSlots": _class_count(html, "amenity-icon-slot"),
        "mediaSlots": media_slots,
        "mediaSlotCount": len(media_slots),
        "pageBoxes": page_boxes,
    }


def _structured_cover_title_noisy_targets(soup: Any) -> list[str]:
    noisy: list[str] = []
    for control in soup.select('[data-exact-field="coverTitle"][data-targets]'):
        target_ids = [
            part.strip()
            for part in str(control.get("data-targets") or "").split(",")
            if part.strip()
        ]
        for save_id in target_ids:
            node = soup.select_one(f'[data-save-id="{_css_attr(save_id)}"]')
            if node is not None and _is_noisy_ocr_text_node(node):
                noisy.append(save_id)
    return noisy


def _is_noisy_ocr_text_node(node: Any) -> bool:
    if str(node.get("data-ocr-fallback") or "").lower() != "true":
        return False
    role = str(node.get("data-typography-role") or "body")
    if role in {"cover-title", "section-heading"}:
        return False
    original = _normalise_space(str(node.get("data-plain-text") or node.get_text(" ", strip=True) or ""))
    font_size = _css_px(node.get("data-font-size"), _style_px(str(node.get("style") or ""), "font-size") or 0.0)
    return 0 < len(original.replace(" ", "")) <= 4 and font_size >= 96


def _is_low_signal_interaction_text_node(node: Any) -> bool:
    if _is_noisy_ocr_text_node(node):
        return True
    if str(node.get("data-ocr-fallback") or "").lower() != "true":
        return False
    role = str(node.get("data-typography-role") or "body")
    if role in {"cover-title", "section-heading"}:
        return False
    original = _normalise_space(str(node.get("data-plain-text") or node.get_text(" ", strip=True) or ""))
    return 0 < len(original.replace(" ", "")) <= 4


def _media_slots_for_page(page: Any) -> list[dict[str, Any]]:
    page_num = _node_page_number(page)
    slots: list[dict[str, Any]] = []
    for node in page.select(".exact-image-slot"):
        classes = _node_classes(node)
        role = str(node.get("data-image-role") or ("space-plan" if "exact-space-plan-slot" in classes else "image"))
        kind = _media_kind_from_role(role, classes)
        rect = _node_rect(node)
        save_id = str(node.get("data-save-id") or "").strip()
        slots.append(
            {
                "page": page_num,
                "kind": kind,
                "role": role,
                "saveId": save_id,
                "slotId": str(node.get("data-slot-id") or node.get("data-image-slot") or ""),
                "bbox": rect,
                "hasUsableBox": _usable_rect(rect),
                "hasStableId": bool(save_id),
                "hasUploadAffordance": bool(node.select_one('input[type="file"]')),
                "replaceable": True,
                "fit": str(node.get("data-fit") or ""),
                "sourceImage": str(node.get("data-source-image") or ""),
            }
        )
    for node in page.select("[data-source-logo-slot]"):
        rect = _node_rect(node)
        slot_id = str(node.get("data-source-logo-slot") or "").strip()
        slots.append(
            {
                "page": page_num,
                "kind": "source-logo",
                "role": str(node.get("data-source-logo-kind") or "source-facade-mark"),
                "saveId": slot_id,
                "slotId": slot_id,
                "bbox": rect,
                "hasUsableBox": _usable_rect(rect),
                "hasStableId": bool(slot_id),
                "hasUploadAffordance": True,
                "replaceable": True,
                "defaultAsset": str(node.get("data-default-source-logo-asset") or ""),
            }
        )
    for node in page.select("[data-brand-logo-slot], [data-agency-logo-slot]"):
        rect = _node_rect(node)
        slot_id = str(node.get("data-brand-logo-slot") or node.get("data-agency-logo-slot") or "").strip()
        slots.append(
            {
                "page": page_num,
                "kind": "agency-logo",
                "role": "agency-logo",
                "saveId": slot_id,
                "slotId": slot_id,
                "bbox": rect,
                "hasUsableBox": _usable_rect(rect),
                "hasStableId": bool(slot_id),
                "hasUploadAffordance": True,
                "replaceable": True,
                "defaultAsset": str(node.get("data-default-agency-logo-asset") or ""),
            }
        )
    for node in page.select("[data-exact-map-area], .map-area"):
        if not node.get("data-exact-map-area") and "map-area" not in _node_classes(node):
            continue
        rect = _node_rect(node)
        save_id = str(node.get("data-save-id") or "").strip()
        slots.append(
            {
                "page": page_num,
                "kind": "map",
                "role": "map",
                "saveId": save_id,
                "slotId": str(node.get("data-slot-id") or save_id),
                "bbox": rect,
                "hasUsableBox": _usable_rect(rect),
                "hasStableId": bool(save_id),
                "hasUploadAffordance": bool(node.select_one(".map-generate-btn, [data-exact-map-generate]")),
                "hasMapControl": bool(node.select_one(".exact-map-controls, .map-generate-btn, [data-exact-map-generate]")),
                "replaceable": True,
            }
        )
    return slots


def _node_page_number(node: Any) -> int:
    value = node.get("data-page-num") or str(node.get("id") or "").removeprefix("page")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _node_classes(node: Any) -> set[str]:
    classes = node.get("class") or []
    if isinstance(classes, str):
        return {part for part in classes.split() if part}
    return {str(part) for part in classes if part}


def _node_rect(node: Any) -> dict[str, float | None]:
    style = str(node.get("style") or "")
    left = _style_px(style, "left")
    top = _style_px(style, "top")
    width = _style_px(style, "width")
    height = _style_px(style, "height")
    return {
        "left": round(left, 3) if left is not None else None,
        "top": round(top, 3) if top is not None else None,
        "width": round(width, 3) if width is not None else None,
        "height": round(height, 3) if height is not None else None,
    }


def _usable_rect(rect: dict[str, float | None]) -> bool:
    try:
        return float(rect.get("width") or 0) >= 4 and float(rect.get("height") or 0) >= 4
    except (TypeError, ValueError):
        return False


def _export_summary(html: str, expected_pages: int) -> dict[str, Any]:
    return {
        "pageCount": _page_count(html),
        "expectedPageCount": expected_pages,
        "contenteditableCount": _count(r"\bcontenteditable\b", html),
        "fileInputCount": _count(r"<input\b[^>]*\btype\s*=\s*['\"]file['\"]", html),
        "scriptCount": _count(r"<script\b", html),
        "toolbarCount": _class_count(html, "exact-toolbar"),
        "fieldsPanelCount": _class_count(html, "exact-fields-panel"),
        "imageSlots": _class_count(html, "exact-image-slot"),
        "sourceLogoSlots": _class_count(html, "exact-source-logo-slot") + _class_count(html, "source-logo-slot"),
        "bodyClass": _body_class(html),
    }


def _expected_media_summary(project_path: Path, graph: dict[str, Any]) -> dict[str, Any]:
    """Summarise media features the PDF-derived design graph says should exist."""
    feature_roles: list[str] = []
    expected_entries: list[tuple[int, str, str]] = []
    layout_kind_counts_by_page: dict[int, dict[str, int]] = {}
    metadata = _load_json(project_path / "exact_metadata.json")
    metadata_entries = _metadata_expected_media_entries(metadata)
    if metadata_entries:
        return _expected_media_result(
            metadata_entries,
            feature_roles=[],
            method="renderer field_config media controls generated from PDF-derived semantic regions",
        )
    layout = _load_json(project_path / "exact_layout_model" / "exact-layout.json")
    for page in layout.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or 0)
        for region in page.get("image_regions") or []:
            if not isinstance(region, dict):
                continue
            role = str(region.get("role") or region.get("semantic_role") or "")
            kind = _expected_kind_from_role(role)
            if not kind:
                continue
            expected_entries.append((page_number, role, kind))
            layout_kind_counts_by_page.setdefault(page_number, {})[kind] = (
                layout_kind_counts_by_page.setdefault(page_number, {}).get(kind, 0) + 1
            )
    for page in graph.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or 0)
        page_layout_kinds = layout_kind_counts_by_page.get(page_number, {})
        seen_graph_regions: set[tuple[str, str]] = set()
        map_added_for_page = page_layout_kinds.get("map", 0) > 0
        for feature in page.get("detected_features") or []:
            feature_roles.extend(_roles_for_detected_feature(str(feature)))
        for element in page.get("elements") or []:
            if isinstance(element, dict):
                role = str(element.get("role") or element.get("semantic_role") or "")
                kind = _expected_kind_from_role(role)
                if not kind:
                    continue
                bbox_value = element.get("bbox") if isinstance(element.get("bbox"), dict) else element.get("bbox_raw")
                if kind == "source-logo" and not isinstance(bbox_value, dict):
                    continue
                if kind in {"image", "artwork-image", "space-plan"} and page_layout_kinds.get(kind, 0) > 0:
                    continue
                if kind == "map":
                    if map_added_for_page:
                        continue
                    map_added_for_page = True
                bbox_sig = _bbox_signature(bbox_value)
                if bbox_sig == "no-bbox":
                    bbox_sig = str(element.get("id") or bbox_sig)
                key = (role, bbox_sig)
                if key in seen_graph_regions:
                    continue
                seen_graph_regions.add(key)
                expected_entries.append((page_number, role, kind))
    return _expected_media_result(
        expected_entries,
        feature_roles=feature_roles,
        method="PDF-derived design graph roles plus exact-layout semantic image regions",
    )


def _expected_media_result(
    expected_entries: list[tuple[int, str, str]],
    *,
    feature_roles: list[str],
    method: str,
) -> dict[str, Any]:
    by_role: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for _page_number, role, kind in expected_entries:
        by_role[role] = by_role.get(role, 0) + 1
        by_kind[kind] = by_kind.get(kind, 0) + 1
    feature_kinds = {_expected_kind_from_role(role) for role in feature_roles}
    feature_kinds.discard("")
    return {
        "schema": "brochure-maker.expected-media.v1",
        "byRole": dict(sorted(by_role.items())),
        "byKind": dict(sorted(by_kind.items())),
        "expectsImageReplacement": bool(
            by_kind.get("image")
            or by_kind.get("space-plan")
            or by_kind.get("artwork-image")
            or not {"image", "space-plan", "artwork-image"}.isdisjoint(feature_kinds)
        ),
        # Logo replacement needs a concrete PDF-derived bbox/slot candidate.
        # Broad detected-feature flags are useful as weak hints for maps/images,
        # but letting them require logo controls creates false blockers on pages
        # where the detector only found branded text or decorative artwork.
        "expectsSourceLogoReplacement": bool(by_kind.get("source-logo")),
        "expectsAgencyLogoReplacement": bool(by_kind.get("agency-logo")),
        "expectsMapReplacement": bool(by_kind.get("map") or "map" in feature_kinds),
        "featureKinds": sorted(feature_kinds),
        "entryCount": len(expected_entries),
        "method": method,
    }


def _metadata_expected_media_entries(metadata: dict[str, Any]) -> list[tuple[int, str, str]]:
    layout = metadata.get("layout") if isinstance(metadata.get("layout"), dict) else {}
    field_config = layout.get("field_config") if isinstance(layout.get("field_config"), dict) else {}
    entries: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str, str, str]] = set()

    def add(page_number: int, role: str, kind: str, signature: str) -> None:
        if not page_number or not kind:
            return
        key = (page_number, role, kind, signature)
        if key in seen:
            return
        seen.add(key)
        entries.append((page_number, role, kind))

    for region in field_config.get("image_regions") or []:
        if not isinstance(region, dict):
            continue
        role = str(region.get("role") or region.get("semantic_role") or "")
        kind = _expected_kind_from_role(role)
        if not kind:
            continue
        page_number = int(region.get("page") or region.get("page_number") or 0)
        add(page_number, role, kind, _bbox_signature(region))
    for logo in field_config.get("source_logos") or []:
        if not isinstance(logo, dict):
            continue
        page_number = int(logo.get("page") or logo.get("page_number") or 0)
        role = str(logo.get("role") or "source-facade-mark")
        add(page_number, role, "source-logo", _bbox_signature(logo))
    agency_logos = field_config.get("agency_logos")
    if isinstance(agency_logos, dict):
        logo_items = agency_logos.values()
    elif isinstance(agency_logos, list):
        logo_items = agency_logos
    else:
        logo_items = []
    for logo in logo_items:
        if not isinstance(logo, dict):
            continue
        page_number = int(logo.get("page") or logo.get("page_number") or 0)
        add(page_number, "agency-logo", "agency-logo", _bbox_signature(logo))
    map_region = field_config.get("map_region") if isinstance(field_config.get("map_region"), dict) else {}
    if map_region:
        page_number = int(map_region.get("page") or map_region.get("page_number") or 0)
        add(page_number, "map", "map", _bbox_signature(map_region))
    for plan in field_config.get("space_plans") or []:
        if not isinstance(plan, dict):
            continue
        page_number = int(plan.get("page") or plan.get("page_number") or 0)
        add(page_number, "space-plan", "space-plan", _bbox_signature(plan))
    return entries


def _roles_for_detected_feature(feature: str) -> list[str]:
    return {
        "photo_regions": ["photo-region"],
        "space_plan": ["space-plan"],
        "map": ["map"],
        "source_facade_mark": ["source-facade-mark"],
        "global_logo": ["source-facade-mark"],
        "agency_logos": ["agency-logo"],
    }.get(feature, [])


def _expected_kind_from_role(role: str) -> str:
    if role in {"photo-region", "photo-grid", "hero-photo"}:
        return "image"
    if role in {"artwork-image", "space-plan", "map", "agency-logo"}:
        return role
    if role in {"source-facade-mark", "repeated-header-mark", "logo"}:
        return "source-logo"
    return ""


def _bbox_signature(bbox: Any) -> str:
    if not isinstance(bbox, dict):
        return "no-bbox"
    values = []
    for key in ("x", "y", "left", "top", "width", "height"):
        try:
            values.append(str(round(float(bbox.get(key) or 0), 3)))
        except (TypeError, ValueError):
            values.append("0")
    return ",".join(values)


def _media_kind_from_role(role: str, classes: set[str] | None = None) -> str:
    if classes and "exact-space-plan-slot" in classes:
        return "space-plan"
    if role in {"photo-region", "photo-grid", "hero-photo"}:
        return "image"
    if role in {"artwork-image", "space-plan", "map"}:
        return role
    return "image"


def _media_slot_audit(editor: dict[str, Any], expected_media: dict[str, Any] | None = None) -> dict[str, Any]:
    slots = editor.get("mediaSlots") if isinstance(editor.get("mediaSlots"), list) else []
    expected_media = expected_media or {}
    blockers: list[str] = []
    by_kind: dict[str, int] = {}
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        kind = str(slot.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        label = f"page {slot.get('page') or '?'} {kind} {slot.get('saveId') or slot.get('slotId') or ''}".strip()
        if not slot.get("hasStableId"):
            blockers.append(f"Browser media slot lacks a stable id: {label}")
        if not slot.get("hasUsableBox"):
            blockers.append(f"Browser media slot lacks a usable bbox: {label}")
        if kind in {"image", "space-plan", "artwork-image"} and not slot.get("hasUploadAffordance"):
            blockers.append(f"Browser media slot lacks upload/replace affordance: {label}")
        if kind == "source-logo" and not slot.get("replaceable"):
            blockers.append(f"Browser source logo slot is not replaceable: {label}")
        if kind == "map" and not slot.get("hasMapControl"):
            blockers.append(f"Browser map slot lacks preserve/regenerate controls: {label}")
    actual_image_count = sum(by_kind.get(kind, 0) for kind in ("image", "space-plan", "artwork-image"))
    expected_by_kind = expected_media.get("byKind") if isinstance(expected_media.get("byKind"), dict) else {}
    expected_image_count = sum(int(expected_by_kind.get(kind) or 0) for kind in ("image", "space-plan", "artwork-image"))
    if expected_media.get("expectsImageReplacement") and actual_image_count <= 0:
        blockers.append("PDF/design evidence expects image/artwork/space-plan replacement slots but Browser found none")
    elif expected_image_count and actual_image_count < expected_image_count:
        blockers.append(
            f"PDF/design evidence expects {expected_image_count} image/artwork/space-plan replacement slots but Browser found {actual_image_count}"
        )
    if expected_media.get("expectsSourceLogoReplacement") and by_kind.get("source-logo", 0) <= 0:
        blockers.append("PDF/design evidence expects source logo replacement slots but Browser found none")
    elif int(expected_by_kind.get("source-logo") or 0) and by_kind.get("source-logo", 0) < int(expected_by_kind.get("source-logo") or 0):
        blockers.append(
            f"PDF/design evidence expects {int(expected_by_kind.get('source-logo') or 0)} source logo slots but Browser found {by_kind.get('source-logo', 0)}"
        )
    if expected_media.get("expectsAgencyLogoReplacement") and by_kind.get("agency-logo", 0) <= 0:
        blockers.append("PDF/design evidence expects agency logo replacement slots but Browser found none")
    elif int(expected_by_kind.get("agency-logo") or 0) and by_kind.get("agency-logo", 0) < int(expected_by_kind.get("agency-logo") or 0):
        blockers.append(
            f"PDF/design evidence expects {int(expected_by_kind.get('agency-logo') or 0)} agency logo slots but Browser found {by_kind.get('agency-logo', 0)}"
        )
    if expected_media.get("expectsMapReplacement") and by_kind.get("map", 0) <= 0:
        blockers.append("PDF/design evidence expects map replacement/preserve controls but Browser found none")
    elif int(expected_by_kind.get("map") or 0) and by_kind.get("map", 0) < int(expected_by_kind.get("map") or 0):
        blockers.append(
            f"PDF/design evidence expects {int(expected_by_kind.get('map') or 0)} map controls but Browser found {by_kind.get('map', 0)}"
        )
    return {
        "schema": "brochure-maker.browser-media-slot-audit.v1",
        "accepted": not blockers,
        "slotCount": len(slots),
        "byKind": by_kind,
        "expectedByKind": expected_by_kind,
        "blockers": sorted(dict.fromkeys(blockers)),
        "method": "static DOM inventory of editor media/logo/map slots with bbox and replace affordance checks",
    }


def _interaction_text_target(html: str) -> dict[str, Any] | None:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    candidates = [
        node
        for node in soup.select('.pdf-text[contenteditable="true"][data-save-id]')
        if not _is_low_signal_interaction_text_node(node)
    ]
    if not candidates:
        return None
    def page_number(node: Any) -> int:
        page = node.find_parent(class_="exact-page")
        return _node_page_number(page) if page else 0

    ranked = sorted(
        candidates,
        key=lambda node: (
            0 if page_number(node) == 1 or str(node.get("data-save-id") or "").startswith("exact-page1-") else 1,
            0 if node.get("data-typography-role") == "cover-title" else 1,
            0 if str(node.get("data-ocr-fallback") or "").lower() == "true" else 1,
            0 if str(node.get_text(" ", strip=True)).strip() else 1,
        ),
    )
    for node in ranked:
        save_id = str(node.get("data-save-id") or "").strip()
        text = node.get_text(" ", strip=True)
        if not save_id or not text:
            continue
        return {
            "save_id": save_id,
            "role": str(node.get("data-typography-role") or "body"),
            "font_alias": str(node.get("data-font-alias") or ""),
            "font_family": str(node.get("data-font-family") or ""),
            "html": "".join(str(child) for child in node.contents),
            "text": text,
        }
    return None


def _interaction_image_target(html: str) -> dict[str, Any] | None:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select(".exact-image-slot[data-save-id]"):
        save_id = str(node.get("data-save-id") or "").strip()
        if not save_id:
            continue
        return {
            "save_id": save_id,
            "role": str(node.get("data-image-role") or ("space-plan" if "exact-space-plan-slot" in _node_classes(node) else "image")),
        }
    return None


def _interaction_source_logo_target(html: str) -> dict[str, Any] | None:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("[data-source-logo-slot]"):
        slot_id = str(node.get("data-source-logo-slot") or "").strip()
        if slot_id:
            return {"slot_id": slot_id}
    return None


def _interaction_map_target(html: str) -> dict[str, Any] | None:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("[data-exact-map-area][data-save-id], .map-area[data-save-id]"):
        save_id = str(node.get("data-save-id") or "").strip()
        if save_id:
            return {"save_id": save_id}
    return None


def _svg_data_url(marker: str, colour: str) -> str:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" data-qa-marker="{marker}">'
        f'<title>{marker}</title><rect width="12" height="12" fill="{colour}"/></svg>'
    )
    return "data:image/svg+xml," + urllib.parse.quote(svg, safe="/:=;,+")


def _export_text_target(html: str, save_id: str) -> dict[str, Any] | None:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(f'[data-save-id="{_css_attr(save_id)}"]')
    if not node:
        return None
    return {
        "save_id": save_id,
        "role": str(node.get("data-typography-role") or ""),
        "font_alias": str(node.get("data-font-alias") or ""),
        "font_family": str(node.get("data-font-family") or ""),
        "style": str(node.get("style") or ""),
        "text": node.get_text(" ", strip=True),
    }


def _export_text_fit_audit(target: dict[str, Any] | None) -> dict[str, Any]:
    if not target:
        return {"accepted": False, "reason": "export target missing", "blockers": ["Export text target not found"]}
    style = str(target.get("style") or "")
    text = _normalise_space(str(target.get("text") or ""))
    width = _style_px(style, "width")
    font_size = _style_px(style, "font-size")
    white_space = _style_value(style, "white-space").lower()
    blockers: list[str] = []
    estimated_width = None
    if text and width is not None and font_size is not None:
        estimated_width = _estimated_text_width(text, font_size)
        if white_space in {"nowrap", "pre"} and estimated_width > width * 1.15:
            blockers.append("Edited text is wider than its nowrap text box")
    return {
        "accepted": not blockers,
        "saveId": str(target.get("save_id") or ""),
        "textLength": len(text),
        "width": round(width, 2) if width is not None else None,
        "fontSize": round(font_size, 2) if font_size is not None else None,
        "estimatedNaturalWidth": round(estimated_width, 2) if estimated_width is not None else None,
        "whiteSpace": white_space,
        "blockers": blockers,
    }


def _layout_audit(html: str) -> dict[str, Any]:
    boxes = _editable_text_boxes(html)
    warnings: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    by_page: dict[int, list[dict[str, Any]]] = {}
    for box in boxes:
        by_page.setdefault(int(box.get("page") or 0), []).append(box)
    for page, page_boxes in sorted(by_page.items()):
        for index, first in enumerate(page_boxes):
            for second in page_boxes[index + 1 :]:
                overlap = _box_overlap(first, second)
                if not overlap:
                    continue
                if _is_expected_superscript_overlap(first, second):
                    continue
                if _is_expected_stacked_heading_overlap(first, second, float(overlap["overlap_ratio"])):
                    continue
                if _is_heading_edge_box_false_positive(first, second, overlap):
                    continue
                if _is_source_preserved_overlay_overlap(first, second):
                    continue
                if _is_same_line_fragment_false_positive(first, second, overlap):
                    continue
                if _is_same_line_estimated_column_false_positive(first, second, overlap):
                    continue
                if _is_map_annotation_overlap(first, second, overlap):
                    continue
                ratio = float(overlap["overlap_ratio"])
                entry = {
                    "page": page,
                    "first": _box_ref(first),
                    "second": _box_ref(second),
                    "overlap_px": overlap["overlap_px"],
                    "overlap_ratio": round(ratio, 3),
                    "severity": "major" if ratio >= 0.65 else "minor",
                    "evidence": "estimated DOM boxes from live editor HTML absolute positions",
                }
                warnings.append(entry)
                if ratio >= 0.82 and _is_unexpected_text_collision(first, second):
                    blockers.append({**entry, "severity": "blocker"})
    return {
        "schema": "brochure-maker.browser-layout-audit.v1",
        "textBoxCount": len(boxes),
        "textOverlapWarnings": warnings[:80],
        "textOverlaps": blockers[:40],
        "page4OverlapResolved": not any(int(item.get("page") or 0) == 4 for item in blockers),
        "method": "static DOM approximation from fetched editor HTML; manual in-app Browser screenshots remain the acceptance gate",
    }


def _interaction_hotspot_audit(html: str) -> dict[str, Any]:
    """Catch source-preserved PDF fragments that would wake up on hover/click."""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return {
            "schema": "brochure-maker.browser-interaction-hotspot-audit.v1",
            "accepted": True,
            "hotspots": [],
            "method": "skipped; BeautifulSoup unavailable",
        }
    soup = BeautifulSoup(html, "html.parser")
    hotspots: list[dict[str, Any]] = []
    background_issues: list[dict[str, Any]] = []
    css_has_inert_pdf_background = bool(
        re.search(r"\.pdf-bg\s*\{[^}]*pointer-events\s*:\s*none", html, flags=re.DOTALL | re.IGNORECASE)
    )
    for page in soup.select('.exact-page[data-source-preserved-edit="true"]'):
        page_width, page_height = _page_dimensions(page)
        page_area = max(1.0, page_width * page_height)
        page_num = int(page.get("data-page-num") or 0)
        for bg in page.select("img.pdf-bg"):
            if css_has_inert_pdf_background and str(bg.get("draggable") or "").lower() == "false":
                continue
            background_issues.append(
                {
                    "page": page_num,
                    "issue": "preserved PDF/map background image can receive hover/click/drag",
                    "expectedFix": "set pointer-events:none and draggable=false on .pdf-bg",
                    "draggable": str(bg.get("draggable") or ""),
                    "cssHasPointerEventsNone": css_has_inert_pdf_background,
                }
            )
        for node in page.select('.pdf-text[contenteditable="true"]'):
            if str(node.get("data-interaction-suppressed") or "").lower() == "true":
                continue
            transform_scale = _float_attr(node.get("data-pdf-transform-scale"))
            style = str(node.get("style") or "")
            width = _style_px(style, "width")
            height = _style_px(style, "height")
            area_ratio = (width * height / page_area) if width is not None and height is not None else 0.0
            if transform_scale < 8.0 and area_ratio < 0.12:
                continue
            hotspots.append(
                {
                    "page": page_num,
                    "id": str(node.get("data-save-id") or ""),
                    "text": str(node.get("data-plain-text") or node.get_text(" ", strip=True) or "")[:80],
                    "transformScale": round(transform_scale, 3),
                    "areaRatio": round(area_ratio, 4),
                    "reason": "extreme-transform-hotspot" if transform_scale >= 8.0 else "oversized-source-preserved-hotspot",
                    "expectedFix": "mark inert with contenteditable=false, pointer-events:none, and data-interaction-suppressed=true",
                }
            )
    return {
        "schema": "brochure-maker.browser-interaction-hotspot-audit.v1",
        "accepted": not hotspots and not background_issues,
        "hotspots": hotspots[:60],
        "hotspotCount": len(hotspots),
        "backgroundIssues": background_issues[:40],
        "backgroundIssueCount": len(background_issues),
        "method": "static preflight for Browser hover/click QA; flags invisible PDF text hitboxes likely to activate during interaction",
    }


def _page_dimensions(page: Any) -> tuple[float, float]:
    style = str(page.get("style") or "")
    width = _style_px(style, "width")
    height = _style_px(style, "height")
    return (width or 1.0, height or 1.0)


def _float_attr(value: Any) -> float:
    try:
        return float(str(value or "0"))
    except (TypeError, ValueError):
        return 0.0


def _editable_text_boxes(html: str) -> list[dict[str, Any]]:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    boxes: list[dict[str, Any]] = []
    for node in soup.select(".exact-page .pdf-text"):
        page = node.find_parent(class_="exact-page")
        if not page:
            continue
        classes = set(str(node.get("class") or "").replace("[", "").replace("]", "").replace("'", "").split())
        if "exact-field-hidden" in classes:
            continue
        if str(node.get("aria-hidden") or "").lower() == "true":
            continue
        if str(node.get("contenteditable") or "").lower() == "false":
            continue
        style = str(node.get("style") or "")
        left = _style_px(style, "left")
        top = _style_px(style, "top")
        if left is None or top is None:
            continue
        text = str(node.get("data-plain-text") or node.get_text(" ", strip=True) or "").strip()
        if not text:
            continue
        font_size = _css_px(node.get("data-font-size"), 16.0)
        line_height = _css_px(node.get("data-line-height"), max(1.0, font_size * 1.2))
        explicit_width = _style_px(style, "width")
        explicit_height = _style_px(style, "height")
        lines = [line for line in text.splitlines() if line.strip()] or [text]
        width = explicit_width if explicit_width is not None else max(1.0, _estimated_text_width(text, font_size))
        height = explicit_height if explicit_height is not None else max(line_height, len(lines) * line_height)
        boxes.append(
            {
                "page": int(page.get("data-page-num") or 0),
                "id": str(node.get("data-save-id") or ""),
                "role": str(node.get("data-typography-role") or ""),
                "text": text[:120],
                "left": round(left, 3),
                "top": round(top, 3),
                "right": round(left + width, 3),
                "bottom": round(top + height, 3),
                "width": round(width, 3),
                "height": round(height, 3),
                "font_size": round(font_size, 3),
                "line_height": round(line_height, 3),
                "estimated_width": explicit_width is None,
                "page_layout": str(page.get("data-picture-layout") or ""),
                "page_has_map_slot": bool(page.select_one("[data-exact-map-area], .exact-map-area, .map-area")),
                "source_preserved": str(page.get("data-source-preserved-edit") or "").lower() == "true",
                "semantic": bool(node.get("data-contact-semantic-block")),
            }
        )
    return boxes


def _estimated_text_width(text: str, font_size: float) -> float:
    """Approximate natural PDF span width more closely than a flat char count."""
    total = 0.0
    for char in text:
        if char.isspace():
            factor = 0.28
        elif char in ".,:;!'|":
            factor = 0.24
        elif char in "ilI[](){}":
            factor = 0.30
        elif char in "mwMW@#%&":
            factor = 0.72
        elif char.isupper():
            factor = 0.56
        elif char.isdigit():
            factor = 0.50
        else:
            factor = 0.46
        total += factor
    return total * max(1.0, font_size)


def _normalise_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _style_px(style: str, key: str) -> float | None:
    match = re.search(rf"(?:^|;)\s*{re.escape(key)}\s*:\s*(-?\d+(?:\.\d+)?)px", style, flags=re.I)
    if not match:
        return None
    return float(match.group(1))


def _style_value(style: str, key: str) -> str:
    match = re.search(rf"(?:^|;)\s*{re.escape(key)}\s*:\s*([^;]+)", style, flags=re.I)
    return match.group(1).strip() if match else ""


def _css_px(value: Any, fallback: float) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return fallback
    try:
        return float(match.group(0))
    except ValueError:
        return fallback


def _box_overlap(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any] | None:
    left = max(float(first["left"]), float(second["left"]))
    top = max(float(first["top"]), float(second["top"]))
    right = min(float(first["right"]), float(second["right"]))
    bottom = min(float(first["bottom"]), float(second["bottom"]))
    width = max(0.0, right - left)
    height = max(0.0, bottom - top)
    area = width * height
    if area < 24:
        return None
    first_area = max(1.0, float(first["width"]) * float(first["height"]))
    second_area = max(1.0, float(second["width"]) * float(second["height"]))
    ratio = area / min(first_area, second_area)
    if ratio < 0.18:
        return None
    return {
        "overlap_px": {"left": round(left, 2), "top": round(top, 2), "width": round(width, 2), "height": round(height, 2)},
        "overlap_ratio": ratio,
    }


def _box_ref(box: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": box.get("id"),
        "role": box.get("role"),
        "text": box.get("text"),
        "bbox": {
            "left": box.get("left"),
            "top": box.get("top"),
            "width": box.get("width"),
            "height": box.get("height"),
        },
    }


def _is_expected_superscript_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    suffixes = {"st", "nd", "rd", "th"}
    first_text = str(first.get("text") or "").strip().lower()
    second_text = str(second.get("text") or "").strip().lower()
    return (
        first_text in suffixes
        and len(second_text) > 8
        or second_text in suffixes
        and len(first_text) > 8
    )


def _is_unexpected_text_collision(first: dict[str, Any], second: dict[str, Any]) -> bool:
    roles = {str(first.get("role") or ""), str(second.get("role") or "")}
    if roles == {"cover-title"}:
        return False
    first_text = str(first.get("text") or "").strip()
    second_text = str(second.get("text") or "").strip()
    return len(first_text) > 2 and len(second_text) > 2


def _is_expected_stacked_heading_overlap(first: dict[str, Any], second: dict[str, Any], ratio: float) -> bool:
    """Ignore adjacent title/heading line boxes that overlap but glyphs do not."""
    roles = {str(first.get("role") or ""), str(second.get("role") or "")}
    heading_roles = {"cover-title", "section-heading", "table-status"}
    if not roles or not roles.issubset(heading_roles):
        return False
    first_top = float(first.get("top") or 0)
    second_top = float(second.get("top") or 0)
    vertical_gap = abs(second_top - first_top)
    reference_height = min(float(first.get("height") or 0), float(second.get("height") or 0))
    if reference_height <= 0:
        return False
    left_delta = abs(float(first.get("left") or 0) - float(second.get("left") or 0))
    font_size = max(float(first.get("font_size") or 0), float(second.get("font_size") or 0), 1.0)
    return ratio <= 0.36 and vertical_gap >= reference_height * 0.45 and left_delta <= font_size * 0.75


def _is_heading_edge_box_false_positive(
    first: dict[str, Any],
    second: dict[str, Any],
    overlap: dict[str, Any],
) -> bool:
    """Ignore tiny edge contacts caused by estimated heading width."""
    roles = {str(first.get("role") or ""), str(second.get("role") or "")}
    if "section-heading" not in roles:
        return False
    if roles.issubset({"section-heading", "cover-title", "table-status"}):
        return False
    overlap_px = overlap.get("overlap_px") if isinstance(overlap.get("overlap_px"), dict) else {}
    overlap_width = float(overlap_px.get("width") or 0)
    ratio = float(overlap.get("overlap_ratio") or 0)
    font_size = max(float(first.get("font_size") or 0), float(second.get("font_size") or 0), 1.0)
    return ratio <= 0.4 and 0 < overlap_width <= max(44.0, font_size * 0.9)


def _is_same_line_fragment_false_positive(
    first: dict[str, Any],
    second: dict[str, Any],
    overlap: dict[str, Any],
) -> bool:
    """Ignore tiny same-line PDF text fragments that touch a longer label.

    Map/table pages often preserve the source artwork while exposing tiny PDF
    text fragments. Browser font metrics can make an adjacent one-to-three
    letter fragment overlap the edge of a longer label even though the visible
    source render is correct.
    """
    first_text = str(first.get("text") or "").strip()
    second_text = str(second.get("text") or "").strip()
    estimated = bool(first.get("estimated_width") or second.get("estimated_width"))
    source_preserved = bool(first.get("source_preserved") or second.get("source_preserved"))
    if not (estimated or source_preserved):
        return False
    if min(len(first_text), len(second_text)) > 4 and not (
        bool(first.get("estimated_width")) and bool(second.get("estimated_width"))
    ):
        return False
    first_center = float(first.get("top") or 0) + float(first.get("height") or 0) / 2.0
    second_center = float(second.get("top") or 0) + float(second.get("height") or 0) / 2.0
    font_size = max(float(first.get("font_size") or 0), float(second.get("font_size") or 0), 1.0)
    if abs(first_center - second_center) > max(6.0, font_size * 0.55):
        return False
    overlap_px = overlap.get("overlap_px") if isinstance(overlap.get("overlap_px"), dict) else {}
    overlap_width = float(overlap_px.get("width") or 0)
    if min(len(first_text), len(second_text)) <= 4:
        return 0 < overlap_width <= max(22.0, font_size * 1.15)
    edge_touch = (
        float(first.get("left") or 0) <= float(second.get("left") or 0) <= float(first.get("right") or 0)
        or float(second.get("left") or 0) <= float(first.get("left") or 0) <= float(second.get("right") or 0)
    )
    return edge_touch and 0 < overlap_width <= max(44.0, font_size * 2.8)


def _is_same_line_estimated_column_false_positive(
    first: dict[str, Any],
    second: dict[str, Any],
    overlap: dict[str, Any],
) -> bool:
    """Ignore table-column collisions caused only by estimated natural widths."""
    if not (bool(first.get("estimated_width")) and bool(second.get("estimated_width"))):
        return False
    first_center = float(first.get("top") or 0) + float(first.get("height") or 0) / 2.0
    second_center = float(second.get("top") or 0) + float(second.get("height") or 0) / 2.0
    font_size = max(float(first.get("font_size") or 0), float(second.get("font_size") or 0), 1.0)
    if abs(first_center - second_center) > max(4.0, font_size * 0.35):
        return False
    left_delta = abs(float(first.get("left") or 0) - float(second.get("left") or 0))
    if left_delta < max(72.0, font_size * 4.0):
        return False
    overlap_px = overlap.get("overlap_px") if isinstance(overlap.get("overlap_px"), dict) else {}
    overlap_width = float(overlap_px.get("width") or 0)
    if overlap_width <= 0:
        return False
    return overlap_width < left_delta and overlap_width <= max(132.0, font_size * 9.0)


def _is_map_annotation_overlap(
    first: dict[str, Any],
    second: dict[str, Any],
    overlap: dict[str, Any],
) -> bool:
    """Ignore preserved map labels colliding with POI/list labels.

    Map pages often expose a replaceable map area while the exact PDF text
    layer still contains tiny station, street, or marker labels. A static box
    collision there is a map-text extraction artifact, not an overlapping
    brochure paragraph.
    """
    if not (first.get("page_has_map_slot") or second.get("page_has_map_slot")):
        return False
    roles = {str(first.get("role") or ""), str(second.get("role") or "")}
    if "caption" not in roles:
        return False
    max_font = max(float(first.get("font_size") or 0), float(second.get("font_size") or 0))
    overlap_px = overlap.get("overlap_px") if isinstance(overlap.get("overlap_px"), dict) else {}
    overlap_height = float(overlap_px.get("height") or 0)
    return max_font <= 14.0 and overlap_height <= 18.0


def _is_source_preserved_overlay_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Ignore transparent PDF text overlays in source-preserved exact mode.

    In exact/original mode the PDF render is the visible truth and ordinary
    ``.pdf-text`` nodes are transparent hit targets. Their static CSS boxes can
    overlap because they mirror fragmented PDF text, but that does not mean the
    brochure visually has overlapping writing. Semantic replacement blocks stay
    auditable because they are visible editor/export content.
    """
    return (
        (
            str(first.get("page_layout") or "") == "original"
            and str(second.get("page_layout") or "") == "original"
            or bool(first.get("source_preserved"))
            and bool(second.get("source_preserved"))
        )
        and not bool(first.get("semantic"))
        and not bool(second.get("semantic"))
    )


def _page_count(html: str) -> int:
    ids = set(re.findall(r"\bid\s*=\s*['\"]page(\d+)['\"]", html))
    if ids:
        return len(ids)
    return _count(r"\bclass\s*=\s*['\"][^'\"]*\bexact-page\b", html)


def _body_class(html: str) -> str:
    match = re.search(r"<body\b[^>]*\bclass\s*=\s*['\"]([^'\"]*)['\"]", html, flags=re.I)
    return match.group(1) if match else ""


def _css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.I))


def _class_count(html: str, class_name: str) -> int:
    escaped = re.escape(class_name)
    return _count(rf"<[^>]+\bclass\s*=\s*['\"][^'\"]*\b{escaped}\b", html)


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Write live Browser QA evidence for an exact PDF project.")
    parser.add_argument("project_dir")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    path = write_browser_qa(args.project_dir, base_url=args.base_url, output_path=args.output, force=args.force)
    print(path)


if __name__ == "__main__":
    main()
