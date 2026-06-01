"""Lightweight exact-PDF project evaluation for Browser-backed QA loops."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


HARD_FEATURE_ROLES = {
    "cover_title": ("cover-title", "Missing cover/title text role"),
    "source_facade_mark": ("source-facade-mark", "Missing source facade mark slot"),
    "global_logo": ("source-facade-mark", "Missing global/source logo slot"),
    "photo_regions": ("photo-region", "Missing photo/image region slots"),
    "space_plan": ("space-plan", "Missing space-plan replacement slot"),
    "amenity_icons": ("amenity-icon", "Missing amenity icon slots"),
    "service_icons": ("service-icon", "Missing service/customisation icon slots"),
    "map": ("map", "Missing map region"),
    "agent_contacts": ("agent-contact", "Missing agent/contact fields"),
    "agency_logos": ("agency-logo", "Missing agency logo replacement slot"),
}

GLOBAL_CONTROL_GROUPS = ("palette", "typography", "logo", "amenity_icons", "images", "map", "agents")
CONTROL_FEATURES = {
    "source_facade_mark",
    "global_logo",
    "space_plan",
    "amenity_icons",
    "service_icons",
    "map",
    "agency_logos",
}

FEATURE_ROLE_ALIASES = {
    "photo_regions": {"photo-region", "photo-grid", "hero-photo"},
}

ACTIONABLE_CONTROL_ROLES = {
    role
    for feature, (role, _message) in HARD_FEATURE_ROLES.items()
    if feature in CONTROL_FEATURES
}
for aliases in FEATURE_ROLE_ALIASES.values():
    ACTIONABLE_CONTROL_ROLES.update(aliases)


def evaluate_project(project_dir: str | Path, *, browser_evidence_path: str | Path | None = None) -> dict[str, Any]:
    """Score a generated exact project from its design graph and metadata."""
    base = Path(project_dir)
    graph_path = base / "brochure.design.json"
    metadata_path = base / "exact_metadata.json"
    inventory_path = base / "exact_layout_model" / "extraction-inventory.json"

    blockers: list[str] = []
    if not graph_path.exists():
        return _failed_report(base, ["Missing brochure.design.json"])
    if not metadata_path.exists():
        blockers.append("Missing exact_metadata.json")
    if not inventory_path.exists():
        blockers.append("Missing extraction-inventory.json")

    graph = _load_json(graph_path)
    metadata = _load_json(metadata_path) if metadata_path.exists() else {}
    inventory = _load_json(inventory_path) if inventory_path.exists() else {}

    page_reports = [_score_page(page) for page in graph.get("pages") or [] if isinstance(page, dict)]
    for page_report in page_reports:
        blockers.extend(page_report.get("blockers") or [])

    global_report = _score_globals(graph)
    blockers.extend(global_report.get("blockers") or [])

    artifact_report = _score_artifacts(base, graph, metadata, inventory)
    blockers.extend(artifact_report.get("blockers") or [])
    browser_report = _score_browser_evidence(base, graph, browser_evidence_path)
    blockers.extend(browser_report.get("blockers") or [])

    if page_reports:
        page_score = sum(float(page["score"]) for page in page_reports) / len(page_reports)
    else:
        page_score = 0.0
        blockers.append("Design graph contains no pages")

    score = max(0.0, min(100.0, page_score - global_report["penalty"] - artifact_report["penalty"] - browser_report["penalty"]))
    hard_blockers = sorted(dict.fromkeys(blockers))
    return {
        "schema": "brochure-maker.exact-eval.v1",
        "project_id": base.name,
        "score": round(score, 2),
        "accepted": score >= 95.0 and not hard_blockers and bool(browser_report.get("accepted")),
        "blockers": hard_blockers,
        "page_scores": page_reports,
        "global_systems": global_report,
        "artifacts": artifact_report,
        "browser_qa": browser_report,
        "feature_counts": graph.get("feature_counts") or {},
        "source_pdf": graph.get("source_pdf"),
        "notes": [
            "Automated graph/artifact scoring is a fast signal only.",
            "Browser QA remains the acceptance gate for real editor/export behavior.",
        ],
    }


def write_eval_report(
    project_dir: str | Path,
    output_path: str | Path,
    *,
    browser_evidence_path: str | Path | None = None,
) -> Path:
    """Evaluate a project and write a JSON report."""
    report = evaluate_project(project_dir, browser_evidence_path=browser_evidence_path)
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    latest = path.parent / "latest.json"
    if latest != path:
        latest.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _score_page(page: dict[str, Any]) -> dict[str, Any]:
    page_number = int(page.get("page_number") or 0)
    elements = page.get("elements") if isinstance(page.get("elements"), list) else []
    roles = {str(element.get("role") or "") for element in elements if isinstance(element, dict)}
    control_roles = {
        str(element.get("role") or "")
        for element in elements
        if isinstance(element, dict)
        and (
            element.get("node_class") in {"editor-control", "editable-content"}
            or (
                str(element.get("role") or "") in ACTIONABLE_CONTROL_ROLES
                and bool(element.get("editable") or element.get("replaceable"))
                and bool(element.get("bbox"))
                and bool(element.get("source_attribution"))
            )
        )
    }
    types = {str(element.get("type") or "") for element in elements if isinstance(element, dict)}
    detected_features = [str(feature) for feature in page.get("detected_features") or []]
    blockers: list[str] = []
    penalties = 0

    if not elements:
        blockers.append(f"Page {page_number}: no design graph elements")
        penalties += 35

    for feature in detected_features:
        expected = HARD_FEATURE_ROLES.get(feature)
        if not expected:
            continue
        role, message = expected
        roles_to_check = control_roles if feature in CONTROL_FEATURES else roles
        accepted_roles = FEATURE_ROLE_ALIASES.get(feature, {role})
        if roles_to_check.isdisjoint(accepted_roles):
            blockers.append(f"Page {page_number}: {message}")
            penalties += 20

    if "text" not in types and any(feature in detected_features for feature in ("cover_title", "editable_text", "location_copy", "legal_copy")):
        blockers.append(f"Page {page_number}: missing editable text elements")
        penalties += 20

    static_without_source = [
        element
        for element in elements
        if isinstance(element, dict)
        and element.get("replaceable")
        and not element.get("source_attribution")
    ]
    if static_without_source:
        blockers.append(f"Page {page_number}: replaceable elements missing source attribution")
        penalties += 8
    null_bbox_controls = [
        element
        for element in elements
        if isinstance(element, dict)
        and element.get("node_class") == "editor-control"
        and element.get("replaceable")
        and not element.get("bbox")
    ]
    if null_bbox_controls:
        blockers.append(f"Page {page_number}: actionable controls missing bbox")
        penalties += 18

    score = max(0, 100 - penalties)
    return {
        "page_number": page_number,
        "purpose": page.get("purpose") or "",
        "score": score,
        "blockers": blockers,
        "element_counts": page.get("element_counts") or {},
        "detected_features": detected_features,
    }


def _score_globals(graph: dict[str, Any]) -> dict[str, Any]:
    tokens = graph.get("theme_tokens") if isinstance(graph.get("theme_tokens"), dict) else {}
    controls = tokens.get("global_controls") if isinstance(tokens.get("global_controls"), dict) else {}
    blockers = []
    penalty = 0
    for group in GLOBAL_CONTROL_GROUPS:
        values = controls.get(group)
        if not isinstance(values, list) or not values:
            blockers.append(f"Missing global control group: {group}")
            penalty += 3
    typography = tokens.get("typography") if isinstance(tokens.get("typography"), dict) else {}
    if not typography.get("editor_roles") and not typography.get("inventory_roles"):
        blockers.append("Missing global typography roles")
        penalty += 10
    palette = tokens.get("palette") if isinstance(tokens.get("palette"), dict) else {}
    if not palette:
        blockers.append("Missing global palette tokens")
        penalty += 8
    return {
        "score": max(0, 100 - penalty),
        "penalty": penalty,
        "blockers": blockers,
        "control_groups": {group: controls.get(group, []) for group in GLOBAL_CONTROL_GROUPS},
    }


def _score_artifacts(base: Path, graph: dict[str, Any], metadata: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    blockers = []
    penalty = 0
    required_files = (
        "brochure.html",
        "source.pdf",
        "exact_metadata.json",
        "brochure.design.json",
        "exact_layout_model/exact-layout.json",
        "exact_layout_model/extraction-inventory.json",
    )
    files = {}
    for rel in required_files:
        path = base / rel
        files[rel] = path.exists()
        if not path.exists():
            blockers.append(f"Missing generated file: {rel}")
            penalty += 15
    if graph.get("project_id") not in (None, base.name):
        blockers.append("Design graph project_id does not match project directory")
        penalty += 10
    if metadata and not metadata.get("design_graph_path"):
        blockers.append("exact_metadata.json does not reference brochure.design.json")
        penalty += 8
    if inventory and int(inventory.get("page_count") or 0) != int(graph.get("page_count") or 0):
        blockers.append("Inventory page count does not match design graph")
        penalty += 10
    return {"score": max(0, 100 - penalty), "penalty": penalty, "blockers": blockers, "files": files}


def _score_browser_evidence(
    base: Path,
    graph: dict[str, Any],
    browser_evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(browser_evidence_path) if browser_evidence_path else base / "browser_qa.json"
    blockers = []
    penalty = 0
    if not path.exists():
        return {
            "score": 75,
            "penalty": 25,
            "accepted": False,
            "blockers": ["Missing Browser QA evidence"],
            "path": str(path),
        }
    evidence = _load_json(path)
    editor = evidence.get("editor") if isinstance(evidence.get("editor"), dict) else {}
    export = evidence.get("clean_export") if isinstance(evidence.get("clean_export"), dict) else {}
    if not export and isinstance(evidence.get("export"), dict):
        export = evidence.get("export") or {}
    interactions = evidence.get("interactions") if isinstance(evidence.get("interactions"), dict) else {}
    visual_diff = evidence.get("visual_diff") if isinstance(evidence.get("visual_diff"), dict) else {}
    html_assessment = evidence.get("html_assessment") if isinstance(evidence.get("html_assessment"), dict) else {}
    if not visual_diff:
        visual_diff = _load_related_report(base, "visual-diff.json")
    if not html_assessment:
        html_assessment = _load_related_report(base, "html-assessment.json")
    layout_audit = evidence.get("layout_audit") if isinstance(evidence.get("layout_audit"), dict) else {}
    assertions = evidence.get("assertions") if isinstance(evidence.get("assertions"), dict) else {}
    page_count = int(graph.get("page_count") or 0)

    editor_page_count = int(editor.get("pageCount") or editor.get("exactPageCount") or 0)
    editable_count = int(editor.get("editableTextCount") or editor.get("contenteditableCount") or 0)
    fields_visible = bool(editor.get("fieldsPanelVisible"))
    if not fields_visible and assertions:
        fields_visible = bool(assertions.get("global_controls_visible"))

    if editor_page_count != page_count:
        blockers.append("Browser editor page count does not match design graph")
        penalty += 15
    if not fields_visible:
        blockers.append("Browser did not confirm fields/global controls panel")
        penalty += 12
    if editable_count <= 0:
        blockers.append("Browser did not confirm editable text blocks")
        penalty += 15
    export_page_count = int(export.get("pageCount") or export.get("exactPageCount") or 0)
    if export_page_count != page_count:
        blockers.append("Browser clean export page count does not match design graph")
        penalty += 15
    export_chrome_counts = {
        "contenteditable": export.get("contenteditable", export.get("contenteditableCount", 0)),
        "inputs": export.get("inputs", 0),
        "scripts": export.get("scripts", 0),
        "toolbar": export.get("toolbar", 0),
        "fieldsPanel": export.get("fieldsPanel", export.get("fieldsPanelCount", 0)),
    }
    for key, value in export_chrome_counts.items():
        if int(value or 0) != 0:
            blockers.append(f"Browser clean export still contains {key}")
            penalty += 15
    if export.get("bodyClass") and str(export.get("bodyClass")) != "export-clean":
        blockers.append("Browser clean export body class is not export-clean")
        penalty += 5
    if interactions.get("titleEditFontPreserved") is False:
        blockers.append("Browser title edit did not preserve font")
        penalty += 20
    if interactions.get("reloadPreserved") is False:
        blockers.append("Browser reload did not preserve edited state")
        penalty += 20
    if not interactions:
        blockers.append("Missing Browser interaction evidence")
        penalty += 12
    else:
        interaction_checks = (
            ("stateRoundtripPreserved", "Browser interaction probe did not preserve state roundtrip"),
            ("exportPreservedEditedText", "Browser interaction probe did not preserve edited text in clean export"),
            ("cleanExportAfterProbeHasNoChrome", "Browser interaction probe clean export contains editor chrome"),
            ("globalColourExported", "Browser interaction probe did not preserve global colours in clean export"),
            ("imageReplacementRoundtripPreserved", "Browser interaction probe did not preserve image replacements"),
            ("logoReplacementRoundtripPreserved", "Browser interaction probe did not preserve logo replacements"),
            ("mapReplacementRoundtripPreserved", "Browser interaction probe did not preserve map replacements"),
        )
        for key, message in interaction_checks:
            if interactions.get(key) is not True:
                blockers.append(message)
                penalty += 8
    text_overlaps = layout_audit.get("textOverlaps") if isinstance(layout_audit.get("textOverlaps"), list) else []
    if text_overlaps:
        blockers.append("Browser layout audit found overlapping editable text blocks")
        penalty += 20
    visual_score = float((visual_diff or html_assessment).get("score") or 0)
    if not visual_diff and not html_assessment:
        blockers.append("Missing Browser visual-diff or screenshot evidence")
        penalty += 15
    elif visual_score < 95:
        blockers.append("Browser visual-diff score is below 95")
        penalty += 20
    if not html_assessment:
        blockers.append("Missing HTML assessment explaining visual and editability defects")
        penalty += 8
    elif html_assessment.get("accepted") is False:
        blockers.append("HTML assessment did not accept generated output")
        penalty += 8

    return {
        "score": max(0, 100 - penalty),
        "penalty": penalty,
        "accepted": not blockers,
        "blockers": blockers,
        "path": str(path),
        "editor": editor,
        "clean_export": export,
        "interactions": interactions,
        "layout_audit": layout_audit,
        "visual_diff": visual_diff,
        "html_assessment": html_assessment,
    }


def _failed_report(project_dir: Path, blockers: list[str]) -> dict[str, Any]:
    return {
        "schema": "brochure-maker.exact-eval.v1",
        "project_id": project_dir.name,
        "score": 0,
        "accepted": False,
        "blockers": blockers,
        "page_scores": [],
        "global_systems": {},
        "artifacts": {},
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_related_report(base: Path, filename: str) -> dict[str, Any]:
    repo_root = base.parent.parent if base.parent.name == "projects" else base.parent
    candidates = [
        repo_root / "evals" / "reports" / f"{base.name}-visual" / filename,
        repo_root / "evals" / "reports" / base.name / filename,
    ]
    for path in candidates:
        if path.exists():
            return _load_json(path)
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate an exact PDF brochure project.")
    parser.add_argument("project_dir", help="Path to a generated project directory")
    parser.add_argument("--output", default="evals/reports/latest.json", help="Path to write the JSON eval report")
    parser.add_argument("--browser-evidence", default=None, help="Path to Browser QA evidence JSON")
    args = parser.parse_args()
    path = write_eval_report(args.project_dir, args.output, browser_evidence_path=args.browser_evidence)
    print(path)


if __name__ == "__main__":
    main()
