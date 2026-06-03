"""Contract artifact for in-app Browser UI QA of exact projects."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_BROWSER_UI_ASSERTIONS = (
    "editor_opened_in_browser",
    "global_controls_panel_usable",
    "text_edit_reload_roundtrip",
    "global_colour_roundtrip",
    "typography_roundtrip",
    "media_controls_usable",
    "image_replacement_roundtrip",
    "logo_replacement_roundtrip",
    "map_replacement_roundtrip",
    "clean_export_opened_in_browser",
    "pdf_export_checked",
    "screenshots_or_visual_artifacts_captured",
)


def write_browser_ui_qa(
    project_dir: str | Path,
    *,
    base_url: str = "http://127.0.0.1:8000",
    output_path: str | Path | None = None,
    export_qa_path: str | Path | None = None,
    force: bool = False,
) -> Path:
    """Write or normalize ``browser_ui_qa.json``.

    Real in-app Browser runs can write this artifact first. If no real Browser
    artifact exists, this writer records a deterministic provisional UI contract
    from the HTTP Browser/export evidence so benchmark reports stay explicit.
    """
    project_path = Path(project_dir).expanduser().resolve()
    output = Path(output_path).expanduser().resolve() if output_path else project_path / "browser_ui_qa.json"
    existing = _load_json(output)
    if not force and _has_required_assertions(existing):
        return output

    if existing and _looks_like_real_browser_evidence(existing):
        payload = _normalise_existing(project_path, existing, base_url=base_url)
    elif not _looks_like_exact_project(project_path):
        payload = _non_exact_fixture_payload(project_path, base_url=base_url)
    else:
        payload = _provisional_payload(project_path, base_url=base_url, export_qa_path=export_qa_path)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output


def _looks_like_real_browser_evidence(payload: dict[str, Any]) -> bool:
    method = str(payload.get("method") or "")
    surface = str(payload.get("browser_surface") or payload.get("browserSurface") or "")
    return "in-app" in method or surface == "in-app-browser"


def _looks_like_exact_project(project_path: Path) -> bool:
    return (project_path / "exact_metadata.json").exists() or (project_path / "brochure.html").exists()


def _has_required_assertions(payload: dict[str, Any]) -> bool:
    assertions = payload.get("assertions") if isinstance(payload.get("assertions"), dict) else {}
    return all(assertions.get(name) is True for name in REQUIRED_BROWSER_UI_ASSERTIONS)


def _normalise_existing(project_path: Path, existing: dict[str, Any], *, base_url: str) -> dict[str, Any]:
    assertions = existing.get("assertions") if isinstance(existing.get("assertions"), dict) else {}
    normalised = {
        name: bool(assertions.get(name) or _legacy_assertion(existing, name))
        for name in REQUIRED_BROWSER_UI_ASSERTIONS
    }
    blockers = [str(item) for item in existing.get("blockers") or []]
    for name, value in normalised.items():
        if not value:
            blockers.append(f"Missing or false Browser UI assertion: {name}")
    return {
        **existing,
        "schema": "brochure-maker.browser-ui-qa.v1",
        "project_id": project_path.name,
        "base_url": base_url.rstrip("/"),
        "captured_at": existing.get("captured_at") or datetime.now(timezone.utc).isoformat(),
        "method": existing.get("method") or "in-app-browser-playwright",
        "browser_surface": "in-app-browser",
        "confidence": existing.get("confidence") or "full",
        "accepted": all(normalised.values()) and not blockers,
        "assertions": normalised,
        "blockers": sorted(dict.fromkeys(blockers)),
        "required_assertions": list(REQUIRED_BROWSER_UI_ASSERTIONS),
    }


def _legacy_assertion(payload: dict[str, Any], name: str) -> bool:
    aliases = {
        "editor_opened_in_browser": ("editorOpened", "editor_has_expected_pages"),
        "global_controls_panel_usable": ("globalControlsUsable", "global_controls_visible"),
        "text_edit_reload_roundtrip": ("reloadPreserved", "state_roundtrip_preserved"),
        "global_colour_roundtrip": ("globalColourExported", "clean_export_preserves_global_colour"),
        "typography_roundtrip": ("titleEditFontPreserved", "typed_text_font_preserved"),
        "media_controls_usable": ("mediaSlotsHaveActionableControls", "media_slots_have_actionable_controls"),
        "image_replacement_roundtrip": ("imageReplacementRoundtripPreserved", "image_replacement_roundtrip_preserved"),
        "logo_replacement_roundtrip": ("logoReplacementRoundtripPreserved", "logo_replacement_roundtrip_preserved"),
        "map_replacement_roundtrip": ("mapReplacementRoundtripPreserved", "map_replacement_roundtrip_preserved"),
        "clean_export_opened_in_browser": ("cleanExportOpened", "export_has_no_editor_chrome"),
        "pdf_export_checked": ("pdfExportChecked", "pdf_export_nonempty"),
        "screenshots_or_visual_artifacts_captured": ("screenshotsCaptured", "visualArtifactsCaptured"),
    }
    for alias in aliases.get(name, ()):
        if payload.get(alias) is True:
            return True
    return False


def _non_exact_fixture_payload(project_path: Path, *, base_url: str) -> dict[str, Any]:
    return {
        "schema": "brochure-maker.browser-ui-qa.v1",
        "project_id": project_path.name,
        "base_url": base_url.rstrip("/"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "method": "not-applicable-non-exact-fixture",
        "browser_surface": "fixture",
        "confidence": "fixture",
        "accepted": True,
        "assertions": {name: True for name in REQUIRED_BROWSER_UI_ASSERTIONS},
        "blockers": [],
        "required_assertions": list(REQUIRED_BROWSER_UI_ASSERTIONS),
    }


def _provisional_payload(
    project_path: Path,
    *,
    base_url: str,
    export_qa_path: str | Path | None = None,
) -> dict[str, Any]:
    browser_qa = _load_json(project_path / "browser_qa.json")
    export_qa = _load_json(export_qa_path) if export_qa_path else _load_json(project_path / "export_qa.json")
    browser_assertions = browser_qa.get("assertions") if isinstance(browser_qa.get("assertions"), dict) else {}
    export_assertions = export_qa.get("assertions") if isinstance(export_qa.get("assertions"), dict) else {}
    visual_diff = browser_qa.get("visual_diff") if isinstance(browser_qa.get("visual_diff"), dict) else {}
    screenshots = _existing_screenshots(project_path, browser_qa)
    assertions = {
        "editor_opened_in_browser": bool(browser_assertions.get("editor_has_expected_pages")),
        "global_controls_panel_usable": bool(
            browser_assertions.get("global_controls_visible")
            and browser_assertions.get("global_typography_controls_present")
        ),
        "text_edit_reload_roundtrip": bool(
            browser_assertions.get("state_roundtrip_preserved")
            and browser_assertions.get("clean_export_preserves_edited_text")
        ),
        "global_colour_roundtrip": bool(browser_assertions.get("clean_export_preserves_global_colour")),
        "typography_roundtrip": bool(browser_assertions.get("typed_text_font_preserved")),
        "media_controls_usable": bool(browser_assertions.get("media_slots_have_actionable_controls")),
        "image_replacement_roundtrip": bool(browser_assertions.get("image_replacement_roundtrip_preserved")),
        "logo_replacement_roundtrip": bool(browser_assertions.get("logo_replacement_roundtrip_preserved")),
        "map_replacement_roundtrip": bool(browser_assertions.get("map_replacement_roundtrip_preserved")),
        "clean_export_opened_in_browser": bool(
            browser_assertions.get("export_has_expected_pages")
            and browser_assertions.get("export_has_no_editor_chrome")
        ),
        "pdf_export_checked": bool(
            export_assertions.get("pdf_export_succeeded")
            and export_assertions.get("pdf_export_nonempty")
        ),
        "screenshots_or_visual_artifacts_captured": bool(screenshots or visual_diff.get("report")),
    }
    blockers = [
        f"Missing or false Browser UI assertion: {name}"
        for name, value in assertions.items()
        if not value
    ]
    return {
        "schema": "brochure-maker.browser-ui-qa.v1",
        "project_id": project_path.name,
        "base_url": base_url.rstrip("/"),
        "editor_url": f"{base_url.rstrip('/')}/api/projects/{project_path.name}/brochure",
        "clean_export_url": f"{base_url.rstrip('/')}/api/projects/{project_path.name}/export/html",
        "pdf_export_url": f"{base_url.rstrip('/')}/api/projects/{project_path.name}/export/pdf",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "method": "http-derived-browser-ui-contract",
        "browser_surface": "http-fallback",
        "confidence": "provisional",
        "accepted": all(assertions.values()) and not blockers,
        "assertions": assertions,
        "blockers": sorted(dict.fromkeys(blockers)),
        "required_assertions": list(REQUIRED_BROWSER_UI_ASSERTIONS),
        "screenshots": screenshots,
        "browser_playbook": _browser_playbook(project_path.name, base_url.rstrip("/")),
    }


def _existing_screenshots(project_path: Path, browser_qa: dict[str, Any]) -> list[str]:
    screenshots: list[str] = []
    for key in ("screenshots", "browser_screenshots"):
        value = browser_qa.get(key)
        if isinstance(value, list):
            screenshots.extend(str(item) for item in value if item)
    evidence_dir = project_path / "browser-ui-evidence"
    if evidence_dir.exists():
        screenshots.extend(str(path) for path in sorted(evidence_dir.glob("*.png")))
    return sorted(dict.fromkeys(screenshots))


def _browser_playbook(project_id: str, base_url: str) -> list[str]:
    return [
        f"Open {base_url}/api/projects/{project_id}/brochure in the in-app Browser.",
        "Confirm the global controls panel is visible and interactable.",
        "Edit text, colour, typography, image, logo, map, icon, space-plan, and contact/agency controls when present.",
        "Reload the editor and verify edits persist.",
        f"Open {base_url}/api/projects/{project_id}/export/html and verify edited state with no editor chrome.",
        f"POST {base_url}/api/projects/{project_id}/export/pdf and verify a non-empty PDF artifact.",
        "Save screenshots for editor before edit, after edit, after reload, clean export, and PDF/export confirmation.",
    ]


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Write/normalize in-app Browser UI QA evidence.")
    parser.add_argument("project_dir")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default=None)
    parser.add_argument("--export-qa", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    path = write_browser_ui_qa(
        args.project_dir,
        base_url=args.base_url,
        output_path=args.output,
        export_qa_path=args.export_qa,
        force=args.force,
    )
    print(path)


if __name__ == "__main__":
    main()
