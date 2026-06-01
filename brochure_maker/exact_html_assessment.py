"""Triage exact HTML output so subagent critique has concrete defects to mark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from brochure_maker.exact_text_reconstruction import build_text_reconstruction_report


ROLE_REPAIR_MAP = {
    "cover-title": ("typography/title-grouping", "PDF text spans plus transform-preserving title grouping"),
    "section-heading": ("typography/text-layout", "PDF text span grouping and font role extraction"),
    "body": ("typography/text-layout", "PDF text spans, line-height, and contenteditable sizing"),
    "caption": ("typography/text-layout", "PDF text spans and caption role extraction"),
    "table-status": ("typography/table-text", "PDF table/status text grouping"),
    "agent-contact": ("contacts/agent-text", "contact grouping and legal-footer separation"),
    "agency-logo": ("contacts/agency-logo", "visual logo region detection and export state"),
    "source-facade-mark": ("logo/source-mark", "PDF vector detection and source mark replacement slots"),
    "repeated-header-mark": ("logo/source-mark", "PDF vector detection and repeated mark grouping"),
    "photo-region": ("images/photo-slots", "PyMuPDF image extraction plus raster component refinement"),
    "space-plan": ("space-plans", "floor-plan classification and contain-fit image slots"),
    "amenity-icon": ("icons/amenities", "vector icon detection and icon bank mapping"),
    "service-icon": ("icons/services", "vector icon detection and service false-positive filtering"),
    "map": ("maps", "map region detection and semantic map reconstruction"),
    "map-label": ("maps", "map label OCR/text recovery and category colours"),
}


def build_html_assessment(
    project_dir: str | Path,
    *,
    visual_report_path: str | Path | None = None,
    browser_evidence_path: str | Path | None = None,
    hotspot_count: int = 5,
) -> dict[str, Any]:
    """Build a concrete defect assessment for a generated exact HTML project."""
    project_path = Path(project_dir).expanduser().resolve()
    graph = _load_json(project_path / "brochure.design.json")
    browser_qa = _load_json(Path(browser_evidence_path).expanduser()) if browser_evidence_path else _load_json(project_path / "browser_qa.json")
    visual = _load_visual_report(project_path, browser_qa, visual_report_path)
    text_reconstruction = build_text_reconstruction_report(project_path)
    page_lookup = {
        int(page.get("page_number") or 0): page
        for page in graph.get("pages") or []
        if isinstance(page, dict)
    }

    page_contexts: list[dict[str, Any]] = []
    for visual_page in visual.get("pages") or []:
        if not isinstance(visual_page, dict):
            continue
        page_number = int(visual_page.get("page_number") or 0)
        graph_page = page_lookup.get(page_number, {})
        hotspots = _page_hotspots(visual_page, hotspot_count=hotspot_count)
        findings = _findings_for_page(page_number, graph_page, visual_page, hotspots)
        page_contexts.append(
            {
                "page_number": page_number,
                "graph_page": graph_page,
                "visual_page": visual_page,
                "hotspots": hotspots,
                "findings": findings,
            }
        )

    browser_findings = _browser_findings(browser_qa)
    browser_findings_by_page: dict[int, list[dict[str, Any]]] = {}
    global_browser_findings: list[dict[str, Any]] = []
    for finding in browser_findings:
        try:
            page_number = int(finding.get("page") or 0)
        except (TypeError, ValueError):
            page_number = 0
        if page_number > 0:
            browser_findings_by_page.setdefault(page_number, []).append(finding)
        else:
            global_browser_findings.append(finding)
    text_findings_by_page: dict[int, list[dict[str, Any]]] = {}
    for page in text_reconstruction.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or 0)
        findings = [
            _text_reconstruction_finding(defect)
            for defect in page.get("defects") or []
            if isinstance(defect, dict)
        ]
        if findings:
            text_findings_by_page.setdefault(page_number, []).extend(findings)

    page_reports: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []
    for context in page_contexts:
        page_number = int(context["page_number"])
        graph_page = context["graph_page"]
        visual_page = context["visual_page"]
        findings = [
            *context["findings"],
            *browser_findings_by_page.get(page_number, []),
            *text_findings_by_page.get(page_number, []),
        ]
        all_findings.extend(findings)
        page_reports.append(
            {
                "page_number": page_number,
                "purpose": graph_page.get("purpose") or "",
                "score": visual_page.get("score", 0),
                "mean_absolute_error": visual_page.get("mean_absolute_error"),
                "accepted": float(visual_page.get("score") or 0) >= 95 and not _severe_findings(findings),
                "hotspots": context["hotspots"],
                "findings": findings,
            }
        )

    all_findings.extend(global_browser_findings)
    global_findings = _global_findings(graph, browser_qa)
    all_findings.extend(global_findings)
    score = float(visual.get("score") or 0)
    blockers = [
        finding["issue"]
        for finding in all_findings
        if finding.get("severity") in {"blocker", "major"}
    ]
    next_tasks = _next_repair_tasks(all_findings)
    accepted = score >= 95 and not blockers and bool(visual.get("accepted", score >= 95))
    return {
        "schema": "brochure-maker.exact-html-assessment.v1",
        "project_id": project_path.name,
        "score": round(score, 3),
        "accepted": accepted,
        "blockers": sorted(dict.fromkeys(blockers)),
        "visual_report": visual.get("report") or str(visual_report_path or ""),
        "browser_evidence": str(Path(browser_evidence_path).expanduser()) if browser_evidence_path else str(project_path / "browser_qa.json"),
        "summary": _summary(score, all_findings),
        "pages": page_reports,
        "global_findings": global_findings,
        "browser_findings": browser_findings,
        "text_reconstruction": text_reconstruction,
        "next_repair_tasks": next_tasks,
        "method": {
            "hotspots": "tile-based original-vs-generated pixel difference mapped to design graph roles",
            "browser": "Browser evidence is used for editor/export chrome, persistence, and overlap blockers",
            "text_reconstruction": "PDF-derived contact fields are checked against semantic editable HTML blocks so missing digits/emails are marked even when visual score is near pass.",
            "intended_use": "Give the critic/subagent a concrete why-not-95 mark sheet after every fresh import",
        },
    }


def write_html_assessment(
    project_dir: str | Path,
    output_path: str | Path,
    *,
    visual_report_path: str | Path | None = None,
    browser_evidence_path: str | Path | None = None,
) -> Path:
    """Write an HTML assessment JSON report."""
    report = build_html_assessment(
        project_dir,
        visual_report_path=visual_report_path,
        browser_evidence_path=browser_evidence_path,
    )
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return path


def attach_html_assessment_to_browser_qa(project_dir: str | Path, assessment_path: str | Path) -> Path:
    """Attach the HTML assessment summary to Browser QA evidence."""
    project_path = Path(project_dir).expanduser().resolve()
    qa_path = project_path / "browser_qa.json"
    qa = _load_json(qa_path)
    assessment = _load_json(Path(assessment_path).expanduser().resolve())
    qa["html_assessment"] = {
        "score": assessment.get("score", 0),
        "accepted": bool(assessment.get("accepted")),
        "report": str(Path(assessment_path).expanduser().resolve()),
        "blockers": assessment.get("blockers") or [],
        "next_repair_tasks": assessment.get("next_repair_tasks") or [],
        "summary": assessment.get("summary") or "",
        "pages": [
            {
                "page_number": page.get("page_number"),
                "score": page.get("score"),
                "accepted": page.get("accepted"),
                "findings": page.get("findings") or [],
            }
            for page in assessment.get("pages") or []
            if isinstance(page, dict)
        ],
    }
    qa_path.write_text(json.dumps(qa, indent=2, sort_keys=True), encoding="utf-8")
    return qa_path


def _load_visual_report(project_path: Path, browser_qa: dict[str, Any], visual_report_path: str | Path | None) -> dict[str, Any]:
    if visual_report_path:
        report = _load_json(Path(visual_report_path).expanduser().resolve())
        if report:
            report["report"] = str(Path(visual_report_path).expanduser().resolve())
            return report
    visual_diff = browser_qa.get("visual_diff") if isinstance(browser_qa.get("visual_diff"), dict) else {}
    report_path = visual_diff.get("report")
    if report_path:
        report = _load_json(Path(str(report_path)).expanduser())
        if report:
            report["report"] = str(report_path)
            return report
    candidates = sorted(project_path.parent.parent.glob(f"evals/reports/{project_path.name}-visual/visual-diff.json"))
    if candidates:
        report = _load_json(candidates[0])
        report["report"] = str(candidates[0])
        return report
    return visual_diff if isinstance(visual_diff, dict) else {}


def _page_hotspots(page: dict[str, Any], *, hotspot_count: int) -> list[dict[str, Any]]:
    original_path = page.get("original")
    generated_path = page.get("generated")
    if not original_path or not generated_path:
        return []
    original = Path(str(original_path))
    generated = Path(str(generated_path))
    if not original.exists() or not generated.exists():
        return []
    with Image.open(original) as original_image, Image.open(generated) as generated_image:
        source = original_image.convert("RGB")
        output = generated_image.convert("RGB")
        if output.size != source.size:
            output = output.resize(source.size, Image.Resampling.LANCZOS)
        diff = ImageChops.difference(source, output).convert("L")
        width, height = diff.size
        columns = 12
        rows = 8
        tiles: list[dict[str, Any]] = []
        for row in range(rows):
            for column in range(columns):
                left = int(column * width / columns)
                top = int(row * height / rows)
                right = int((column + 1) * width / columns)
                bottom = int((row + 1) * height / rows)
                crop = diff.crop((left, top, right, bottom))
                mean = float(ImageStat.Stat(crop).mean[0])
                if mean < 8:
                    continue
                tiles.append(
                    {
                        "bbox_px": {"x": left, "y": top, "width": right - left, "height": bottom - top},
                        "bbox": {
                            "x": round(left / width, 6),
                            "y": round(top / height, 6),
                            "width": round((right - left) / width, 6),
                            "height": round((bottom - top) / height, 6),
                        },
                        "mean_absolute_error": round(mean, 3),
                    }
                )
        return sorted(tiles, key=lambda item: item["mean_absolute_error"], reverse=True)[:hotspot_count]


def _findings_for_page(
    page_number: int,
    graph_page: dict[str, Any],
    visual_page: dict[str, Any],
    hotspots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    page_score = float(visual_page.get("score") or 0)
    if page_score < 95:
        findings.append(
            {
                "page": page_number,
                "severity": "major" if page_score < 90 else "minor",
                "issue": f"Page {page_number} visual fidelity is {page_score:.3f}, below the 95 acceptance gate",
                "evidence": f"mean_absolute_error={visual_page.get('mean_absolute_error')}",
                "likely_cause": "One or more extracted roles differs from the PDF in placement, crop, tone, typography, or missing vectors",
                "repair_subsystem": "visual-diff-guided triage",
                "recommended_tool": "Browser screenshot plus visual diff hotspots",
            }
        )
    elements = [element for element in graph_page.get("elements") or [] if isinstance(element, dict)]
    for hotspot in hotspots:
        matched = _matched_elements(elements, hotspot.get("bbox") or {})
        roles = sorted({str(element.get("role") or "unknown") for element in matched}) or ["page-background/vector"]
        subsystem, tool = _subsystem_for_roles(roles)
        findings.append(
            {
                "page": page_number,
                "severity": _severity_for_hotspot(hotspot, page_score),
                "issue": f"Large visual mismatch near {', '.join(roles[:3])}",
                "evidence": f"hotspot={hotspot.get('bbox_px')} mean_absolute_error={hotspot.get('mean_absolute_error')}",
                "likely_cause": _cause_for_roles(roles, graph_page),
                "repair_subsystem": subsystem,
                "recommended_tool": tool,
                "roles": roles,
                "element_ids": [element.get("id") for element in matched[:5]],
            }
        )
    findings.extend(_page_specific_findings(page_number, graph_page, page_score))
    return findings


def _matched_elements(elements: list[dict[str, Any]], hotspot_bbox: dict[str, Any]) -> list[dict[str, Any]]:
    matched = []
    for element in elements:
        bbox = element.get("bbox") if isinstance(element.get("bbox"), dict) else {}
        if _iou(bbox, hotspot_bbox) > 0.01 or _contains_center(bbox, hotspot_bbox):
            matched.append(element)
    return sorted(matched, key=lambda element: _role_priority(str(element.get("role") or "")))


def _page_specific_findings(page_number: int, graph_page: dict[str, Any], page_score: float) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    elements = [element for element in graph_page.get("elements") or [] if isinstance(element, dict)]
    findings.extend(_fragmented_text_line_findings(page_number, elements, page_score))
    if page_number == 1:
        single_glyph_headings = [
            element
            for element in elements
            if element.get("role") in {"cover-title", "section-heading"}
            and isinstance(element.get("text"), str)
            and len(element.get("text", "").strip()) == 1
        ]
        grouped_cover_titles = [
            element
            for element in elements
            if element.get("role") == "cover-title"
            and element.get("node_class") == "structured-field"
            and isinstance(element.get("text"), str)
            and len(element.get("text", "").strip()) >= 3
        ]
        if len(single_glyph_headings) >= 6 and not grouped_cover_titles:
            findings.append(
                {
                    "page": page_number,
                    "severity": "major",
                    "issue": "Cover writing is still represented by many single-glyph title fragments",
                    "evidence": f"{len(single_glyph_headings)} one-character cover/title spans on the cover",
                    "likely_cause": "The PDF encodes rotated cover text as glyph-level spans, so the renderer must consolidate the run before layout",
                    "repair_subsystem": "typography/title-grouping",
                    "recommended_tool": "PDF text spans with rotation/grouping analysis",
                }
            )
    if str(graph_page.get("purpose") or "").lower().startswith("contacts"):
        agency_logos = [element for element in elements if element.get("role") == "agency-logo"]
        logos_missing_default_asset = [
            element
            for element in agency_logos
            if not ((element.get("metadata") if isinstance(element.get("metadata"), dict) else {}).get("default_asset_url"))
        ]
        if logos_missing_default_asset:
            findings.append(
                {
                    "page": page_number,
                    "severity": "minor",
                    "issue": "Agency logo needs visual source fidelity, not just a text fallback",
                    "evidence": f"{len(logos_missing_default_asset)} agency-logo slot(s) lack a default visual asset in the design graph",
                    "likely_cause": "The contact logo is exposed as replaceable, but the default asset should be cropped/detected from the PDF visual logo region",
                    "repair_subsystem": "contacts/agency-logo",
                    "recommended_tool": "raster connected components near contact/legal regions plus clean export state",
                }
            )
    return findings


def _fragmented_text_line_findings(page_number: int, elements: list[dict[str, Any]], page_score: float) -> list[dict[str, Any]]:
    """Detect PDF lines that were extracted as positioned word fragments.

    These are easy to miss with a coarse visual score: a page can be close to
    95 while the writing still feels wrong because normal HTML text flow loses
    the PDF's deliberately spaced line rhythm.
    """
    if page_score >= 95:
        return []
    text_elements = [
        element
        for element in elements
        if element.get("type") == "text"
        and str(element.get("role") or "") in {"body", "caption", "table-status", "agent-contact"}
        and isinstance(element.get("bbox"), dict)
        and str(element.get("text") or "").strip()
    ]
    lines: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for element in text_elements:
        bbox = element.get("bbox") or {}
        try:
            y_key = int(round(float(bbox.get("y", 0)) * 1000))
        except (TypeError, ValueError):
            continue
        role = str(element.get("role") or "body")
        lines.setdefault((role, y_key), []).append(element)

    worst_line: list[dict[str, Any]] = []
    worst_span = 0.0
    for line in lines.values():
        if len(line) < 3:
            continue
        short_fragments = [
            element
            for element in line
            if len(str(element.get("text") or "").strip()) <= 18
        ]
        if len(short_fragments) < 3:
            continue
        xs: list[float] = []
        rights: list[float] = []
        for element in line:
            bbox = element.get("bbox") or {}
            try:
                x = float(bbox.get("x", 0))
                xs.append(x)
                rights.append(x + float(bbox.get("width", 0)))
            except (TypeError, ValueError):
                continue
        if not xs or not rights:
            continue
        span = max(rights) - min(xs)
        if span > worst_span:
            worst_span = span
            worst_line = line

    if not worst_line or worst_span < 0.16:
        return []
    sample = " | ".join(str(element.get("text") or "").strip() for element in sorted(worst_line, key=lambda item: float((item.get("bbox") or {}).get("x", 0)))[:6])
    return [
        {
            "page": page_number,
            "severity": "major" if page_score < 94.5 else "minor",
            "issue": "Body writing line was extracted as separated PDF fragments and needs typography-aware reconstruction",
            "evidence": f"{len(worst_line)} fragments across one line spanning {worst_span:.3f} page width: {sample}",
            "likely_cause": "The PDF used positioned word fragments/justified spacing; rendering it as a normal grouped HTML paragraph changes the visible writing rhythm",
            "repair_subsystem": "typography/text-line-reconstruction",
            "recommended_tool": "PDF text spans, word-position clustering, and Browser screenshot typography audit",
        }
    ]


def _browser_findings(browser_qa: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    layout_audit = browser_qa.get("layout_audit") if isinstance(browser_qa.get("layout_audit"), dict) else {}
    overlaps = layout_audit.get("textOverlaps") if isinstance(layout_audit.get("textOverlaps"), list) else []
    for overlap in overlaps:
        findings.append(
            {
                "page": overlap.get("page"),
                "severity": "blocker",
                "issue": "Browser layout audit found overlapping editable text blocks",
                "evidence": json.dumps(overlap, sort_keys=True),
                "likely_cause": "Editable text line-height or absolute box height does not match rendered PDF text",
                "repair_subsystem": "typography/text-layout",
                "recommended_tool": "Browser DOM bounding boxes and contenteditable style audit",
            }
        )
    warnings = layout_audit.get("textOverlapWarnings") if isinstance(layout_audit.get("textOverlapWarnings"), list) else []
    for warning in warnings:
        findings.append(
            {
                "page": warning.get("page"),
                "severity": warning.get("severity") if warning.get("severity") in {"major", "minor"} else "minor",
                "issue": "Browser layout audit found possible overlapping editable text blocks",
                "evidence": json.dumps(warning, sort_keys=True),
                "likely_cause": "Editable text line-height, title grouping, or absolute box sizing may not match the rendered PDF",
                "repair_subsystem": "typography/text-layout",
                "recommended_tool": "Browser DOM bounding boxes and contenteditable style audit",
            }
        )
    export = browser_qa.get("clean_export") if isinstance(browser_qa.get("clean_export"), dict) else {}
    if not export and isinstance(browser_qa.get("export"), dict):
        export = browser_qa.get("export") or {}
    for key in ("contenteditable", "inputs", "scripts", "toolbar", "fieldsPanel"):
        if int(export.get(key) or 0) != 0:
            findings.append(
                {
                    "page": None,
                    "severity": "blocker",
                    "issue": f"Clean export still contains editor chrome: {key}",
                    "evidence": f"{key}={export.get(key)}",
                    "likely_cause": "Export cleanup missed an editor-only selector or state transform",
                    "repair_subsystem": "export/state",
                    "recommended_tool": "Browser export DOM audit",
                }
            )
    assertions = browser_qa.get("assertions") if isinstance(browser_qa.get("assertions"), dict) else {}
    if assertions and assertions.get("global_controls_visible") is False:
        findings.append(
            {
                "page": None,
                "severity": "blocker",
                "issue": "Browser did not confirm visible global controls",
                "evidence": f"assertions={json.dumps(assertions, sort_keys=True)}",
                "likely_cause": "The generated editor either lacks global controls or the Browser QA harness cannot locate them reliably",
                "repair_subsystem": "renderer/editor-controls",
                "recommended_tool": "Browser fields panel DOM audit",
            }
        )
    critical_assertions = {
        "media_slots_have_actionable_controls": (
            "Browser did not confirm media slots have actionable controls",
            "image-region/slot rendering and upload-control audit",
        ),
        "image_replacement_roundtrip_preserved": (
            "Browser did not confirm image replacement persists into clean export",
            "image replacement state/export roundtrip",
        ),
        "logo_replacement_roundtrip_preserved": (
            "Browser did not confirm logo replacement persists into clean export",
            "logo replacement state/export roundtrip",
        ),
        "map_replacement_roundtrip_preserved": (
            "Browser did not confirm map replacement persists into clean export",
            "map replacement state/export roundtrip",
        ),
        "source_preserved_pages_have_no_giant_interactive_hotspots": (
            "Browser found giant source-preserved interaction hotspots",
            "interaction hotspot and z-index audit",
        ),
    }
    for key, (issue, subsystem) in critical_assertions.items():
        if assertions and assertions.get(key) is False:
            findings.append(
                {
                    "page": None,
                    "severity": "blocker",
                    "issue": issue,
                    "evidence": f"{key}=false",
                    "likely_cause": "The generated editor may look visually correct but lacks reliable editable media behavior in Browser.",
                    "repair_subsystem": subsystem,
                    "recommended_tool": "Browser interaction QA with screenshots before/after hover, click, edit, reload, and clean export",
                }
            )
    return findings


def _text_reconstruction_finding(defect: dict[str, Any]) -> dict[str, Any]:
    return {
        "page": defect.get("page"),
        "severity": defect.get("severity") if defect.get("severity") in {"blocker", "major", "minor"} else "major",
        "issue": defect.get("issue") or "Text reconstruction defect",
        "evidence": defect.get("evidence") or "",
        "likely_cause": defect.get("likely_cause") or "Semantic text block reconstruction missed a PDF-derived field",
        "repair_subsystem": defect.get("repair_subsystem") or "typography/text-reconstruction",
        "recommended_tool": defect.get("recommended_tool") or "PDF text spans and Browser screenshot",
        "failure_class": defect.get("failure_class") or "typography/text-reconstruction",
    }


def _global_findings(graph: dict[str, Any], browser_qa: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tokens = graph.get("theme_tokens") if isinstance(graph.get("theme_tokens"), dict) else {}
    controls = tokens.get("global_controls") if isinstance(tokens.get("global_controls"), dict) else {}
    expected = ("palette", "typography", "logo", "amenity_icons", "images", "map", "agents")
    missing = [group for group in expected if not controls.get(group)]
    if missing:
        findings.append(
            {
                "page": None,
                "severity": "blocker",
                "issue": f"Missing global control groups: {', '.join(missing)}",
                "evidence": f"global_controls keys={sorted(controls.keys())}",
                "likely_cause": "The design graph did not promote extracted local roles into brochure-level controls",
                "repair_subsystem": "design-graph/global-controls",
                "recommended_tool": "design graph tokenization and Browser fields panel audit",
            }
        )
    editor = browser_qa.get("editor") if isinstance(browser_qa.get("editor"), dict) else {}
    if "globalSections" in editor and int(editor.get("globalSections") or 0) < len(expected):
        findings.append(
            {
                "page": None,
                "severity": "major",
                "issue": "Browser fields panel exposes fewer global sections than expected",
                "evidence": f"globalSections={editor.get('globalSections')}",
                "likely_cause": "Renderer/editor controls are not exposing every design graph control group",
                "repair_subsystem": "renderer/editor-controls",
                "recommended_tool": "Browser fields panel DOM audit",
            }
        )
    return findings


def _summary(score: float, findings: list[dict[str, Any]]) -> str:
    blockers = [finding for finding in findings if finding.get("severity") == "blocker"]
    majors = [finding for finding in findings if finding.get("severity") == "major"]
    if score >= 95 and not blockers:
        return "HTML assessment passes the 95 visual gate with no hard Browser blockers."
    if score >= 95:
        parts = [f"HTML assessment score is {score:.3f}, but it is not accepted because critique findings remain."]
    else:
        parts = [f"HTML assessment is {score:.3f}, so it is not a 95% pass yet."]
    if blockers:
        parts.append(f"{len(blockers)} hard blockers require repair.")
    if majors:
        parts.append(f"{len(majors)} major findings explain most of the remaining visual gap.")
    return " ".join(parts)


def _next_repair_tasks(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for finding in findings:
        subsystem = str(finding.get("repair_subsystem") or "unknown")
        bucket = buckets.setdefault(
            subsystem,
            {
                "repair_subsystem": subsystem,
                "recommended_tool": finding.get("recommended_tool"),
                "severity": finding.get("severity"),
                "issues": [],
            },
        )
        issue = finding.get("issue")
        if issue and issue not in bucket["issues"]:
            bucket["issues"].append(issue)
        if _severity_rank(str(finding.get("severity"))) > _severity_rank(str(bucket.get("severity"))):
            bucket["severity"] = finding.get("severity")
            bucket["recommended_tool"] = finding.get("recommended_tool")
    return sorted(buckets.values(), key=lambda item: _severity_rank(str(item.get("severity"))), reverse=True)


def _subsystem_for_roles(roles: list[str]) -> tuple[str, str]:
    for role in roles:
        if role in ROLE_REPAIR_MAP:
            return ROLE_REPAIR_MAP[role]
    return ("vectors/backgrounds", "PDF vector overlay, colour sampling, and rendered-page component analysis")


def _cause_for_roles(roles: list[str], graph_page: dict[str, Any]) -> str:
    if any(role in {"cover-title", "section-heading", "body", "caption", "table-status"} for role in roles):
        return "Text extraction or editable box styling does not match the PDF typography and transformed placement"
    if any(role in {"photo-region", "space-plan"} for role in roles):
        return "Image slot crop, contain/cover mode, tone overlay, or raster component bbox differs from the PDF"
    if any(role in {"source-facade-mark", "repeated-header-mark", "agency-logo"} for role in roles):
        return "Logo/vector mark detection or replacement masking differs from the original source artwork"
    if any(role in {"amenity-icon", "service-icon"} for role in roles):
        return "Icon vector grouping, stroke styling, or icon bank replacement differs from the source"
    if any(role.startswith("map") or role == "map" for role in roles):
        return "Map labels, POI markers, or preserve/regenerate state differs from the PDF"
    purpose = str(graph_page.get("purpose") or "").lower()
    if "contact" in purpose:
        return "Contact-page visual grouping or logo extraction differs from the PDF"
    return "Background colour, vector overlay, or an unclassified page element differs from the PDF"


def _severity_for_hotspot(hotspot: dict[str, Any], page_score: float) -> str:
    mean = float(hotspot.get("mean_absolute_error") or 0)
    if page_score < 90 and mean >= 35:
        return "major"
    if mean >= 50:
        return "major"
    return "minor"


def _severe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [finding for finding in findings if finding.get("severity") in {"blocker", "major"}]


def _severity_rank(severity: str) -> int:
    return {"blocker": 4, "major": 3, "minor": 2, "note": 1}.get(severity, 0)


def _role_priority(role: str) -> int:
    priority = {
        "cover-title": 0,
        "source-facade-mark": 1,
        "agency-logo": 1,
        "photo-region": 2,
        "space-plan": 2,
        "section-heading": 3,
        "body": 4,
    }
    return priority.get(role, 9)


def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    try:
        ax1, ay1 = float(a["x"]), float(a["y"])
        ax2, ay2 = ax1 + float(a["width"]), ay1 + float(a["height"])
        bx1, by1 = float(b["x"]), float(b["y"])
        bx2, by2 = bx1 + float(b["width"]), by1 + float(b["height"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = inter_w * inter_h
    union = max(0.000001, (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection)
    return intersection / union


def _contains_center(a: dict[str, Any], b: dict[str, Any]) -> bool:
    try:
        ax1, ay1 = float(a["x"]), float(a["y"])
        ax2, ay2 = ax1 + float(a["width"]), ay1 + float(a["height"])
        cx = float(b["x"]) + float(b["width"]) / 2
        cy = float(b["y"]) + float(b["height"]) / 2
    except (KeyError, TypeError, ValueError):
        return False
    return ax1 <= cx <= ax2 and ay1 <= cy <= ay2


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Assess exact generated HTML and explain why it is or is not a 95% pass.")
    parser.add_argument("project_dir", help="Path to a generated exact project")
    parser.add_argument("--output", default=None, help="Path to write the assessment JSON")
    parser.add_argument("--visual-report", default=None, help="Path to visual-diff.json")
    parser.add_argument("--browser-evidence", default=None, help="Path to Browser QA evidence JSON")
    parser.add_argument("--update-browser-qa", action="store_true", help="Attach the assessment summary to browser_qa.json")
    args = parser.parse_args()
    output = args.output
    if not output:
        output = str(Path(args.project_dir).expanduser().resolve() / "html_assessment.json")
    path = write_html_assessment(
        args.project_dir,
        output,
        visual_report_path=args.visual_report,
        browser_evidence_path=args.browser_evidence,
    )
    if args.update_browser_qa:
        attach_html_assessment_to_browser_qa(args.project_dir, path)
    print(path)


if __name__ == "__main__":
    main()
