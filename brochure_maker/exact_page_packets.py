"""Build per-page evidence packets for exact PDF repair loops."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from brochure_maker.exact_judgement_agents import (
    AGENT_OUTPUT_FILES,
    build_page_judgement_agents,
    summarise_judgement_agents,
)
from brochure_maker.exact_page_patch import build_page_repair_patch
from brochure_maker.exact_text_reconstruction import build_text_reconstruction_report


PASS_SCORE = 95.0


def build_page_packets(
    project_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    visual_report_path: str | Path | None = None,
    assessment_path: str | Path | None = None,
    browser_evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create page-scoped packets from visual/design/inventory/browser evidence."""
    project_path = Path(project_dir).expanduser().resolve()
    repo_root = project_path.parent.parent if project_path.parent.name == "projects" else Path.cwd()
    packet_root = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else repo_root / "evals" / "reports" / project_path.name / "pages"
    )
    packet_root.mkdir(parents=True, exist_ok=True)

    graph = _load_json(project_path / "brochure.design.json")
    inventory = _load_json(project_path / "exact_layout_model" / "extraction-inventory.json")
    exact_layout = _load_json(project_path / "exact_layout_model" / "exact-layout.json")
    exact_metadata = _load_json(project_path / "exact_metadata.json")
    browser = _load_json(Path(browser_evidence_path).expanduser()) if browser_evidence_path else _load_json(project_path / "browser_qa.json")
    visual = _load_visual(project_path, repo_root, browser, visual_report_path)
    assessment = _load_assessment(project_path, repo_root, browser, assessment_path)
    text_reconstruction = build_text_reconstruction_report(project_path)

    graph_pages = _page_lookup(graph.get("pages"))
    inventory_pages = _page_lookup(inventory.get("pages") if isinstance(inventory, dict) else None)
    layout_pages = _page_lookup(exact_layout.get("pages") if isinstance(exact_layout, dict) else None)
    metadata_image_region_pages = _metadata_image_region_lookup(exact_metadata)
    assessment_pages = _page_lookup(assessment.get("pages") if isinstance(assessment, dict) else None)
    visual_pages = _page_lookup(visual.get("pages") if isinstance(visual, dict) else None)
    text_reconstruction_pages = _page_lookup(text_reconstruction.get("pages") if isinstance(text_reconstruction, dict) else None)
    page_numbers = sorted(
        {
            *graph_pages.keys(),
            *inventory_pages.keys(),
            *layout_pages.keys(),
            *assessment_pages.keys(),
            *visual_pages.keys(),
            *text_reconstruction_pages.keys(),
        }
    )

    packets: list[dict[str, Any]] = []
    for page_number in page_numbers:
        page_dir = packet_root / f"page-{page_number:03d}"
        page_dir.mkdir(parents=True, exist_ok=True)
        visual_page = visual_pages.get(page_number, {})
        graph_page = graph_pages.get(page_number, {})
        inventory_page = inventory_pages.get(page_number, {})
        layout_page = layout_pages.get(page_number, {})
        metadata_image_regions = metadata_image_region_pages.get(page_number, [])
        layout_page_for_agents = dict(layout_page)
        if metadata_image_regions:
            # Render metadata is already in the Browser/HTML coordinate system
            # and includes model-stage regions that survived renderer de-duping.
            # Use it as the page-agent media truth to avoid double-counting raw
            # model bboxes and rendered slots as separate expected controls.
            layout_page_for_agents["image_regions"] = metadata_image_regions
            layout_page_for_agents["_metadata_image_regions_primary"] = True
        assessment_page = assessment_pages.get(page_number, {})
        text_reconstruction_page = text_reconstruction_pages.get(page_number, {})
        copied = _copy_page_images(visual_page, page_dir)
        browser_page = _browser_page_packet(browser, page_number)
        browser_page["text_reconstruction"] = {
            "accepted": bool(text_reconstruction_page.get("accepted", True)),
            "defects": text_reconstruction_page.get("defects") or [],
            "expected_contacts": text_reconstruction_page.get("expected_contacts") or [],
            "rendered_contact_blocks": text_reconstruction_page.get("rendered_contact_blocks") or [],
        }
        browser_screenshot = _copy_browser_screenshot(browser_page, page_dir)
        if browser_screenshot:
            browser_page["packet_screenshot"] = browser_screenshot
            copied["browser-screenshot.png"] = browser_screenshot
        evidence_files = _write_page_evidence_files(
            page_dir,
            layout_page=layout_page_for_agents,
            inventory_page=inventory_page,
            graph_page=graph_page,
            visual_page=visual_page,
            assessment_page=assessment_page,
            text_reconstruction_page=text_reconstruction_page,
            metadata_image_regions=metadata_image_regions,
        )
        score = float(visual_page.get("score") or assessment_page.get("score") or 0)
        accepted = score >= PASS_SCORE and bool(assessment_page.get("accepted", score >= PASS_SCORE))
        page_score = {
            "schema": "brochure-maker.page-score.v1",
            "project_id": project_path.name,
            "page_number": page_number,
            "score": round(score, 3),
            "accepted": accepted,
            "band": _score_band(score),
            "threshold": PASS_SCORE,
            "visual": {
                "mean_absolute_error": visual_page.get("mean_absolute_error"),
                "source": copied.get("source.png"),
                "generated": copied.get("generated.png"),
                "diff": copied.get("diff.png"),
            },
            "evidence_files": evidence_files,
        }
        _write_json(page_dir / "design-page.json", graph_page)
        _write_json(page_dir / "inventory-page.json", inventory_page)
        _write_json(page_dir / "browser-page-qa.json", browser_page)
        _write_json(page_dir / "page-score.json", page_score)
        _write_json(page_dir / "page-assessment.json", assessment_page)
        critique = _page_critique(project_path.name, page_number, score, accepted, assessment_page, graph_page)
        _write_json(page_dir / "page-critique.json", critique)
        repair_plan = _repair_plan(project_path.name, page_number, score, assessment_page, accepted=accepted)
        repair_attempts = _repair_attempts_seed(project_path.name, page_number, accepted)
        _write_json(page_dir / "repair-plan.json", repair_plan)
        judgement_outputs = build_page_judgement_agents(
            project_id=project_path.name,
            page_number=page_number,
            page_dir=page_dir,
            score=score,
            accepted=accepted,
            assessment_page=assessment_page,
            graph_page=graph_page,
            inventory_page=inventory_page,
            layout_page=layout_page_for_agents,
            browser_page=browser_page,
            evidence_files=evidence_files,
            copied_images=copied,
            page_critique=critique,
            repair_plan=repair_plan,
        )
        for filename, payload in judgement_outputs.items():
            _write_json(page_dir / filename, payload)
        judgement_summary = summarise_judgement_agents(judgement_outputs)
        final_assessment_page = assessment_page
        if accepted and not bool(judgement_summary.get("accepted")):
            accepted = False
            final_assessment_page = _assessment_with_agent_blockers(assessment_page, judgement_summary)
            page_score["accepted"] = False
            page_score["band"] = _score_band(min(score, PASS_SCORE - 0.001))
            critique = _page_critique(project_path.name, page_number, score, accepted, final_assessment_page, graph_page)
            repair_plan = _repair_plan(project_path.name, page_number, score, final_assessment_page, accepted=accepted)
            repair_attempts = _repair_attempts_seed(project_path.name, page_number, accepted)
            _write_json(page_dir / "page-score.json", page_score)
            _write_json(page_dir / "page-assessment.json", final_assessment_page)
            _write_json(page_dir / "page-critique.json", critique)
            _write_json(page_dir / "repair-plan.json", repair_plan)
            judgement_outputs = build_page_judgement_agents(
                project_id=project_path.name,
                page_number=page_number,
                page_dir=page_dir,
                score=score,
                accepted=accepted,
                assessment_page=final_assessment_page,
                graph_page=graph_page,
                inventory_page=inventory_page,
                layout_page=layout_page_for_agents,
                browser_page=browser_page,
                evidence_files=evidence_files,
                copied_images=copied,
                page_critique=critique,
                repair_plan=repair_plan,
            )
            for filename, payload in judgement_outputs.items():
                _write_json(page_dir / filename, payload)
            judgement_summary = summarise_judgement_agents(judgement_outputs)
        _write_json(
            page_dir / "page-repair-patch.json",
            build_page_repair_patch(
                project_id=project_path.name,
                page_number=page_number,
                accepted=accepted,
                graph_page=graph_page,
                assessment_page=final_assessment_page,
            ),
        )
        _write_json(page_dir / "repair-attempts.json", repair_attempts)
        if not accepted:
            repair_plan_path = str(page_dir / "repair-plan.json")
        else:
            repair_plan_path = None
        packets.append(
            {
                "page_number": page_number,
                "score": page_score["score"],
                "accepted": accepted,
                "band": page_score["band"],
                "packet_dir": str(page_dir),
                "copied": copied,
                "evidence_files": evidence_files,
                "critique": str(page_dir / "page-critique.json"),
                "judgement_agents": {
                    **judgement_summary,
                    "artifacts": {filename: str(page_dir / filename) for filename in AGENT_OUTPUT_FILES},
                },
                "repair_plan": repair_plan_path,
                "repair_patch": str(page_dir / "page-repair-patch.json"),
                "repair_attempts": str(page_dir / "repair-attempts.json"),
            }
        )

    page_scores = [
        {"page_number": packet["page_number"], "score": packet["score"], "accepted": packet["accepted"]}
        for packet in packets
    ]
    index = {
        "schema": "brochure-maker.page-packets.v1",
        "project_id": project_path.name,
        "packet_root": str(packet_root),
        "page_count": len(packets),
        "accepted": bool(packets) and all(packet["accepted"] for packet in packets),
        "failed_pages": [packet["page_number"] for packet in packets if not packet["accepted"]],
        "lowest_page_score": min((float(packet["score"]) for packet in packets), default=None),
        "page_scores": page_scores,
        "packets": packets,
        "source_reports": {
            "visual": visual.get("report") or str(visual_report_path or ""),
            "assessment": assessment.get("report") or str(assessment_path or ""),
            "browser": str(Path(browser_evidence_path).expanduser()) if browser_evidence_path else str(project_path / "browser_qa.json"),
        },
        "judgement_agents": _summarise_packet_judgements(packets),
    }
    _write_json(packet_root.parent / "page-packets.json", index)
    return index


def write_page_packets(
    project_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    visual_report_path: str | Path | None = None,
    assessment_path: str | Path | None = None,
    browser_evidence_path: str | Path | None = None,
) -> Path:
    """Write page packets and return the packet index path."""
    index = build_page_packets(
        project_dir,
        output_dir=output_dir,
        visual_report_path=visual_report_path,
        assessment_path=assessment_path,
        browser_evidence_path=browser_evidence_path,
    )
    return Path(index["packet_root"]).parent / "page-packets.json"


def _copy_page_images(visual_page: dict[str, Any], page_dir: Path) -> dict[str, str]:
    copied: dict[str, str] = {}
    for key, target_name in (("original", "source.png"), ("generated", "generated.png"), ("diff", "diff.png")):
        source = visual_page.get(key)
        if not source:
            continue
        source_path = Path(str(source)).expanduser()
        if not source_path.exists():
            continue
        target = page_dir / target_name
        shutil.copy2(source_path, target)
        copied[target_name] = str(target)
    return copied


def _copy_browser_screenshot(browser_page: dict[str, Any], page_dir: Path) -> str | None:
    source = browser_page.get("screenshot")
    if not source:
        return None
    source_path = Path(str(source)).expanduser()
    if not source_path.exists() or not source_path.is_file():
        return None
    target = page_dir / "browser-screenshot.png"
    shutil.copy2(source_path, target)
    return str(target)


def _write_page_evidence_files(
    page_dir: Path,
    *,
    layout_page: dict[str, Any],
    inventory_page: dict[str, Any],
    graph_page: dict[str, Any],
    visual_page: dict[str, Any],
    assessment_page: dict[str, Any],
    text_reconstruction_page: dict[str, Any] | None = None,
    metadata_image_regions: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Write small page-scoped extraction files for critic/repair agents."""
    page_number = int(
        layout_page.get("page_number")
        or inventory_page.get("page_number")
        or graph_page.get("page_number")
        or assessment_page.get("page_number")
        or visual_page.get("page_number")
        or 0
    )
    text_spans = layout_page.get("text_spans") if isinstance(layout_page.get("text_spans"), list) else []
    image_boxes = layout_page.get("image_boxes") if isinstance(layout_page.get("image_boxes"), list) else []
    image_regions = _image_region_evidence(layout_page, inventory_page, graph_page, metadata_image_regions or [])
    semantic_regions = layout_page.get("semantic_regions") if isinstance(layout_page.get("semantic_regions"), list) else []
    hotspots = assessment_page.get("hotspots") if isinstance(assessment_page.get("hotspots"), list) else []
    text_reconstruction_page = text_reconstruction_page or {}
    files = {
        "pdf-text-spans.json": {
            "schema": "brochure-maker.page-pdf-text-spans.v1",
            "page_number": page_number,
            "count": len(text_spans),
            "text_spans": text_spans,
        },
        "pdf-fonts.json": {
            "schema": "brochure-maker.page-pdf-fonts.v1",
            "page_number": page_number,
            "fonts": _font_inventory(text_spans),
            "typography_roles": inventory_page.get("typography_roles") or [],
        },
        "pdf-images.json": {
            "schema": "brochure-maker.page-pdf-images.v1",
            "page_number": page_number,
            "count": len(image_boxes),
            "image_boxes": image_boxes,
            "image_regions": image_regions,
            "photo_regions": inventory_page.get("photo_regions") or [],
            "space_plan_regions": inventory_page.get("space_plan_regions") or [],
        },
        "image-regions.json": {
            "schema": "brochure-maker.page-image-regions.v1",
            "page_number": page_number,
            "count": len(image_regions),
            "regions": image_regions,
            "roles": sorted({str(region.get("role") or "unknown") for region in image_regions if isinstance(region, dict)}),
            "note": "Semantic classification decides whether a visual region is a replaceable photo/logo/map/space-plan or a static background/decorative region.",
        },
        "pdf-vectors.json": {
            "schema": "brochure-maker.page-pdf-vectors.v1",
            "page_number": page_number,
            "semantic_regions": semantic_regions,
            "vector_elements": layout_page.get("vector_elements") if isinstance(layout_page.get("vector_elements"), list) else [],
            "note": "Vector extraction is currently represented by semantic regions and overlay-derived editor controls when raw vectors are unavailable.",
        },
        "raster-components.json": {
            "schema": "brochure-maker.page-raster-components.v1",
            "page_number": page_number,
            "hotspots": hotspots,
            "visual": {
                "score": visual_page.get("score"),
                "mean_absolute_error": visual_page.get("mean_absolute_error"),
                "original": visual_page.get("original"),
                "generated": visual_page.get("generated"),
                "diff": visual_page.get("diff"),
            },
            "note": "Until connected-component extraction is formalised, visual-diff hotspots are the page-scoped raster repair evidence.",
        },
        "html-text-spans.json": {
            "schema": "brochure-maker.page-html-text-spans.v1",
            "page_number": page_number,
            "expected_contacts": text_reconstruction_page.get("expected_contacts") or [],
            "rendered_contact_blocks": text_reconstruction_page.get("rendered_contact_blocks") or [],
            "note": "HTML-visible semantic text blocks extracted from the generated editable HTML.",
        },
        "text-reconstruction.json": {
            "schema": "brochure-maker.page-text-reconstruction.v1",
            "page_number": page_number,
            "accepted": bool(text_reconstruction_page.get("accepted", True)),
            "defects": text_reconstruction_page.get("defects") or [],
            "expected_contacts": text_reconstruction_page.get("expected_contacts") or [],
            "rendered_contact_blocks": text_reconstruction_page.get("rendered_contact_blocks") or [],
        },
    }
    written: dict[str, str] = {}
    for filename, payload in files.items():
        path = page_dir / filename
        _write_json(path, payload)
        written[filename] = str(path)
    return written


def _image_region_evidence(
    layout_page: dict[str, Any],
    inventory_page: dict[str, Any],
    graph_page: dict[str, Any],
    metadata_image_regions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collect semantic image regions from layout, inventory, and design graph."""
    regions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(region: dict[str, Any], source: str) -> None:
        if not isinstance(region, dict):
            return
        role = str(region.get("role") or "")
        bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else region.get("bbox_raw")
        if not role and not bbox:
            return
        key = json.dumps({"id": region.get("id"), "role": role, "bbox": bbox}, sort_keys=True, default=str)
        if key in seen:
            return
        seen.add(key)
        regions.append(
            {
                "id": region.get("id"),
                "type": region.get("type"),
                "role": role or region.get("semantic_role"),
                "bbox": bbox,
                "editable": region.get("editable"),
                "replaceable": region.get("replaceable"),
                "locked_static": region.get("locked_static"),
                "fit_mode": region.get("fit_mode") or (region.get("metadata") or {}).get("fit"),
                "mask_mode": region.get("mask_mode") or (region.get("metadata") or {}).get("mask_mode"),
                "confidence": region.get("confidence"),
                "source": source,
                "source_evidence": region.get("source_evidence") or (region.get("metadata") or {}).get("source_evidence"),
            }
        )

    for region in layout_page.get("image_regions") or []:
        add(region, "exact-layout image_regions")
    for region in metadata_image_regions or []:
        add(region, "exact metadata field_config image_regions")
    for region in inventory_page.get("image_regions") or []:
        add(region, "extraction-inventory image_regions")
    for element in graph_page.get("elements") or []:
        if not isinstance(element, dict):
            continue
        if str(element.get("role") or "") in {
            "photo-region",
            "photo-grid",
            "hero-photo",
            "background-texture",
            "decorative-panel",
            "space-plan",
            "map",
            "agency-logo",
            "logo",
            "table-image",
            "static-vector-art",
        }:
            add(element, "brochure.design element")
    return regions


def _font_inventory(text_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fonts: dict[str, dict[str, Any]] = {}
    for span in text_spans:
        if not isinstance(span, dict):
            continue
        font = span.get("font") if isinstance(span.get("font"), dict) else {}
        family = str(font.get("family") or span.get("font_family") or "unknown")
        size = font.get("size") or span.get("font_size")
        color = span.get("color")
        key = json.dumps({"family": family, "size": size, "color": color}, sort_keys=True)
        entry = fonts.setdefault(
            key,
            {
                "family": family,
                "size": size,
                "color": color,
                "is_bold": bool(font.get("is_bold")),
                "is_italic": bool(font.get("is_italic")),
                "count": 0,
                "sample_text": "",
            },
        )
        entry["count"] += 1
        if not entry["sample_text"] and span.get("text"):
            entry["sample_text"] = str(span.get("text"))[:80]
    return sorted(fonts.values(), key=lambda item: (-int(item["count"]), str(item["family"])))


def _browser_page_packet(browser: dict[str, Any], page_number: int) -> dict[str, Any]:
    layout_audit = browser.get("layout_audit") if isinstance(browser.get("layout_audit"), dict) else {}
    text_overlaps = [
        item
        for item in layout_audit.get("textOverlaps") or []
        if isinstance(item, dict) and int(item.get("page") or 0) == page_number
    ]
    text_overlap_warnings = [
        item
        for item in layout_audit.get("textOverlapWarnings") or []
        if isinstance(item, dict) and int(item.get("page") or 0) == page_number
    ]
    browser_evidence = browser.get("browser_evidence") if isinstance(browser.get("browser_evidence"), dict) else {}
    page_key = f"page{page_number}Text"
    editor = browser.get("editor") if isinstance(browser.get("editor"), dict) else {}
    export = browser.get("clean_export") if isinstance(browser.get("clean_export"), dict) else {}
    if not export and isinstance(browser.get("export"), dict):
        export = browser.get("export") or {}
    interaction_audit = browser.get("interaction_audit") if isinstance(browser.get("interaction_audit"), dict) else {}
    interaction_hotspots = [
        item
        for item in interaction_audit.get("hotspots") or []
        if isinstance(item, dict) and int(item.get("page") or 0) == page_number
    ]
    interaction_background_issues = [
        item
        for item in interaction_audit.get("backgroundIssues") or []
        if isinstance(item, dict) and int(item.get("page") or 0) == page_number
    ]
    page_screenshots = browser.get("page_screenshots") if isinstance(browser.get("page_screenshots"), dict) else {}
    page_boxes = [
        item
        for item in editor.get("pageBoxes", []) or []
        if isinstance(item, dict) and int(item.get("page") or 0) == page_number
    ]
    media_slots = [
        item
        for item in editor.get("mediaSlots", []) or []
        if isinstance(item, dict) and int(item.get("page") or 0) == page_number
    ]
    return {
        "schema": "brochure-maker.browser-page-qa.v1",
        "page_number": page_number,
        "textOverlaps": text_overlaps,
        "textOverlapWarnings": text_overlap_warnings,
        "page4OverlapResolved": layout_audit.get("page4OverlapResolved") if page_number == 4 else None,
        "pageText": browser_evidence.get(page_key),
        "editor": editor,
        "pageBox": page_boxes[0] if page_boxes else {},
        "mediaSlots": media_slots,
        "screenshot": page_screenshots.get(str(page_number)) or page_screenshots.get(page_number),
        "global_controls": browser.get("global_controls") if isinstance(browser.get("global_controls"), dict) else {},
        "assertions": browser.get("assertions") if isinstance(browser.get("assertions"), dict) else {},
        "interaction_audit": {
            "schema": interaction_audit.get("schema") or "brochure-maker.browser-interaction-hotspot-audit.v1",
            "accepted": bool(interaction_audit.get("accepted", True)) and not interaction_hotspots and not interaction_background_issues,
            "hotspots": interaction_hotspots,
            "hotspotCount": len(interaction_hotspots),
            "backgroundIssues": interaction_background_issues,
            "backgroundIssueCount": len(interaction_background_issues),
            "globalAccepted": interaction_audit.get("accepted"),
            "method": interaction_audit.get("method"),
        },
        "interactions": browser.get("interactions") if isinstance(browser.get("interactions"), dict) else {},
        "clean_export": export,
        "notes": [] if browser else ["Browser QA has not been captured for this page yet."],
    }


def _summarise_packet_judgements(packets: list[dict[str, Any]]) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    blockers: list[str] = []
    for packet in packets:
        page_number = int(packet.get("page_number") or 0)
        summary = packet.get("judgement_agents") if isinstance(packet.get("judgement_agents"), dict) else {}
        page_blockers = [str(item) for item in summary.get("blockers") or []]
        pages.append(
            {
                "page_number": page_number,
                "accepted": bool(summary.get("accepted")),
                "agent_count": summary.get("agent_count"),
                "blocker_count": len(page_blockers),
            }
        )
        blockers.extend(f"Page {page_number}: {blocker}" for blocker in page_blockers)
    return {
        "schema": "brochure-maker.packet-judgement-agents.v1",
        "accepted": bool(packets) and all(page.get("accepted") for page in pages),
        "page_count": len(pages),
        "pages": pages,
        "blockers": blockers,
    }


def _assessment_with_agent_blockers(assessment_page: dict[str, Any], judgement_summary: dict[str, Any]) -> dict[str, Any]:
    """Promote judgement-agent blockers into normal page findings."""
    merged = dict(assessment_page)
    findings = [item for item in assessment_page.get("findings") or [] if isinstance(item, dict)]
    for blocker in judgement_summary.get("blockers") or []:
        blocker_text = str(blocker)
        findings.append(
            {
                "severity": "major",
                "issue": blocker_text,
                "evidence": "page judgement agent rejected the Browser/editability evidence",
                "likely_cause": "Automated fidelity score passed without sufficient editability or interaction proof",
                "repair_subsystem": _agent_repair_subsystem(blocker_text),
                "recommended_tool": "Browser page packet plus image/text/layout evidence",
                "roles": [_agent_role_from_blocker(blocker_text)],
            }
        )
    merged["accepted"] = False
    merged["findings"] = findings
    return merged


def _agent_repair_subsystem(text: str) -> str:
    lower = text.lower()
    if "image" in lower or "photo" in lower or "media" in lower or "slot" in lower:
        return "images/image-region-classifier"
    if "logo" in lower:
        return "logos/source-mark-detection"
    if "map" in lower:
        return "maps/map-reconstruction"
    if "hover" in lower or "click" in lower or "hotspot" in lower:
        return "renderer/interaction-hotspots"
    if "font" in lower or "text" in lower or "contact" in lower or "digit" in lower or "email" in lower:
        return "typography/text-reconstruction"
    if "export" in lower or "chrome" in lower:
        return "export/clean-html"
    return "page/agent-judgement"


def _agent_role_from_blocker(text: str) -> str:
    lower = text.lower()
    if "logo" in lower:
        return "logo"
    if "map" in lower:
        return "map"
    if "image" in lower or "photo" in lower or "media" in lower:
        return "photo-region"
    if "contact" in lower:
        return "agent-contact"
    if "font" in lower or "text" in lower:
        return "body"
    return "page"


def _page_critique(
    project_id: str,
    page_number: int,
    score: float,
    accepted: bool,
    assessment_page: dict[str, Any],
    graph_page: dict[str, Any],
) -> dict[str, Any]:
    findings = assessment_page.get("findings") if isinstance(assessment_page.get("findings"), list) else []
    severe_findings = [
        finding
        for finding in findings
        if isinstance(finding, dict) and finding.get("severity") in {"blocker", "major"}
    ]
    residual_findings = [finding for finding in findings if isinstance(finding, dict)]
    mark_sheet = _page_mark_sheet(graph_page, residual_findings)
    return {
        "schema": "brochure-maker.page-critique.v1",
        "project_id": project_id,
        "page": page_number,
        "score": round(score, 3),
        "accepted": accepted,
        "needs_repair": not accepted,
        "overall_mark": round(score, 3),
        "confidence": _critique_confidence(score, accepted, residual_findings, severe_findings),
        "status": "accepted" if accepted else "needs-focused-repair",
        "purpose": graph_page.get("purpose") or assessment_page.get("purpose") or "",
        "acceptance_gate": {
            "threshold": PASS_SCORE,
            "score_passed": score >= PASS_SCORE,
            "no_blocker_or_major_findings": not severe_findings,
            "browser_page_evidence_required": True,
        },
        "pass_rationale": (
            "Page meets the 95 score gate with no blocker or major page findings."
            if accepted
            else "Page is below the acceptance gate or has severe findings that require focused repair."
        ),
        "no_repair_reason": (
            "No repair plan was generated because the page passed the 95 score gate and has no blocker or major findings."
            if accepted
            else None
        ),
        "top_failures": [
            {
                "feature": _feature_from_finding(finding),
                "problem": finding.get("issue"),
                "evidence": finding.get("evidence"),
                "likely_cause": finding.get("likely_cause"),
                "repair_task": finding.get("repair_subsystem"),
                "recommended_tool": finding.get("recommended_tool"),
                "severity": finding.get("severity"),
            }
            for finding in findings
            if isinstance(finding, dict) and (not accepted or finding.get("severity") in {"blocker", "major"})
        ],
        "residual_findings": residual_findings,
        "rubric": mark_sheet,
        "residual_risks": _residual_risks(mark_sheet, accepted),
        "html_mark_sheet": {
            "source": "page-assessment.json plus design-page.json",
            "why_this_score_matters": "A page can score near 95 while still having local text, logo, icon, map, or crop defects; this mark sheet makes those defects inspectable by a page critic.",
            "critic_instruction": "Compare source.png, generated.png, diff.png, browser-page-qa.json, and this critique before accepting the page.",
            "must_reject_if": [
                "editable text visibly overlaps",
                "major source mark, map, space plan, contact, image, or icon control is missing",
                "clean export evidence leaks editor chrome",
                "repair suggestion depends on copied HTML or brochure-specific fixed coordinates",
            ],
        },
        "agent_prompt": (
            "Inspect only this page packet: source.png, generated.png, diff.png, "
            "design-page.json, inventory-page.json, browser-page-qa.json, and page-assessment.json. "
            "Mark why the page passed or failed. Identify reusable repairs only; reject project-specific coordinate hacks."
        ),
    }


def _page_mark_sheet(graph_page: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    elements = [element for element in graph_page.get("elements") or [] if isinstance(element, dict)]
    roles = {str(element.get("role") or "") for element in elements}
    subsystems = {str(finding.get("repair_subsystem") or "") for finding in findings if isinstance(finding, dict)}
    categories = [
        ("typography", {"cover-title", "section-heading", "body", "caption", "table-status"}, ("typography",)),
        ("image/photo slots", {"hero-image", "photo-region"}, ("images",)),
        ("logo/source marks", {"source-facade-mark", "repeated-header-mark", "agency-logo"}, ("logo", "contacts/agency-logo")),
        ("amenity/service icons", {"amenity-icon", "service-icon"}, ("icons",)),
        ("map", {"map", "map-label", "subject-marker"}, ("maps",)),
        ("space plans", {"space-plan"}, ("space-plans",)),
        ("agents/contacts", {"agent-contact", "agency-logo", "legal-copy"}, ("contacts",)),
        ("browser/export", set(), ("export", "renderer/editor-controls")),
    ]
    marks: list[dict[str, Any]] = []
    for label, role_markers, subsystem_prefixes in categories:
        relevant = bool(roles.intersection(role_markers)) or any(
            subsystem.startswith(prefix)
            for subsystem in subsystems
            for prefix in subsystem_prefixes
        )
        category_findings = [
            finding
            for finding in findings
            if _finding_in_category(finding, role_markers, subsystem_prefixes)
        ]
        severe = [
            finding
            for finding in category_findings
            if finding.get("severity") in {"blocker", "major"}
        ]
        if severe:
            status = "fail"
        elif category_findings:
            status = "review"
        elif relevant:
            status = "pass"
        else:
            status = "not-present"
        marks.append(
            {
                "category": label,
                "status": status,
                "relevant": relevant,
                "finding_count": len(category_findings),
                "evidence": "design-page roles and page-assessment findings",
            }
        )
    return marks


def _finding_in_category(
    finding: dict[str, Any],
    role_markers: set[str],
    subsystem_prefixes: tuple[str, ...],
) -> bool:
    roles = finding.get("roles") if isinstance(finding.get("roles"), list) else []
    if any(str(role) in role_markers for role in roles):
        return True
    subsystem = str(finding.get("repair_subsystem") or "")
    return any(subsystem.startswith(prefix) for prefix in subsystem_prefixes)


def _critique_confidence(
    score: float,
    accepted: bool,
    findings: list[dict[str, Any]],
    severe_findings: list[dict[str, Any]],
) -> str:
    if not accepted or severe_findings:
        return "low"
    if score < 96 or findings:
        return "medium"
    return "high"


def _residual_risks(mark_sheet: list[dict[str, Any]], accepted: bool) -> list[str]:
    risks = [
        f"{mark['category']} has residual findings and should be checked by a page critic"
        for mark in mark_sheet
        if mark.get("status") == "review"
    ]
    if not accepted:
        risks.append("Page is not accepted and needs focused repair before final brochure acceptance")
    return risks


def _repair_plan(
    project_id: str,
    page_number: int,
    score: float,
    assessment_page: dict[str, Any],
    *,
    accepted: bool,
) -> dict[str, Any]:
    findings = assessment_page.get("findings") if isinstance(assessment_page.get("findings"), list) else []
    tasks: list[dict[str, Any]] = []
    if not accepted:
        seen: set[str] = set()
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            subsystem = str(finding.get("repair_subsystem") or "unknown")
            issue = str(finding.get("issue") or "")
            key = f"{subsystem}:{issue}"
            if key in seen:
                continue
            seen.add(key)
            tasks.append(
                {
                    "repair_subsystem": subsystem,
                    "issue": issue,
                    "recommended_tool": finding.get("recommended_tool"),
                    "allowed_repair_types": _allowed_repairs(subsystem),
                    "disallowed": [
                        "copying existing project HTML",
                        "hardcoded project/page coordinates without a reusable detector",
                        "deleting difficult features",
                        "inventing missing content",
                    ],
                }
            )
    return {
        "schema": "brochure-maker.page-repair-plan.v1",
        "project_id": project_id,
        "page": page_number,
        "score": round(score, 3),
        "target_score": PASS_SCORE,
        "status": "accepted-no-repair" if accepted else "needs-focused-repair",
        "tasks": tasks,
        "rerender_scope": "none" if accepted else "page-only",
        "no_repair_reason": "Page already meets the 95 page score gate." if accepted else None,
        "acceptance": (
            "No page repair should run while this page remains accepted."
            if accepted
            else "Re-render and re-score this page packet; page passes only at >= 95 with no hard Browser blockers."
        ),
    }


def _repair_attempts_seed(project_id: str, page_number: int, accepted: bool) -> dict[str, Any]:
    return {
        "schema": "brochure-maker.repair-attempts.v1",
        "project_id": project_id,
        "page_number": page_number,
        "status": "not-needed" if accepted else "pending",
        "attempts": [],
        "page_repair_contract": {
            "passing_pages_are_not_touched": True,
            "failed_pages_are_repaired_individually": True,
        },
    }


def _feature_from_finding(finding: dict[str, Any]) -> str:
    roles = finding.get("roles")
    if isinstance(roles, list) and roles:
        return str(roles[0])
    subsystem = str(finding.get("repair_subsystem") or "")
    return subsystem.split("/", 1)[0] if subsystem else "page"


def _allowed_repairs(subsystem: str) -> list[str]:
    if subsystem.startswith("typography"):
        return ["font size/line-height fixes", "text grouping correction", "bbox nudges", "z-index/layering fixes"]
    if subsystem.startswith("images"):
        return ["image fit/crop fixes", "photo tone/scrim fixes", "bbox nudges", "z-index/layering fixes"]
    if subsystem.startswith("contacts"):
        return ["contact/logo grouping", "logo slot correction", "bbox nudges"]
    if subsystem.startswith("icons"):
        return ["icon slot correction", "bbox nudges", "z-index/layering fixes"]
    if subsystem.startswith("maps"):
        return ["map label alignment", "bbox nudges", "z-index/layering fixes"]
    return ["bbox nudges", "z-index/layering fixes"]


def _score_band(score: float) -> str:
    if score >= 95:
        return "pass"
    if score >= 85:
        return "focused repair"
    if score >= 70:
        return "verifier agent required"
    if score >= 50:
        return "deeper extraction diagnosis"
    return "page extraction failure"


def _load_visual(project_path: Path, repo_root: Path, browser: dict[str, Any], visual_report_path: str | Path | None) -> dict[str, Any]:
    if visual_report_path:
        visual = _load_json(Path(visual_report_path).expanduser())
        visual["report"] = str(Path(visual_report_path).expanduser())
        return visual
    visual_summary = browser.get("visual_diff") if isinstance(browser.get("visual_diff"), dict) else {}
    report = visual_summary.get("report")
    if report:
        visual = _load_json(Path(str(report)).expanduser())
        visual["report"] = str(report)
        return visual
    candidate = repo_root / "evals" / "reports" / f"{project_path.name}-visual" / "visual-diff.json"
    visual = _load_json(candidate)
    if visual:
        visual["report"] = str(candidate)
    return visual


def _load_assessment(project_path: Path, repo_root: Path, browser: dict[str, Any], assessment_path: str | Path | None) -> dict[str, Any]:
    if assessment_path:
        assessment = _load_json(Path(assessment_path).expanduser())
        assessment["report"] = str(Path(assessment_path).expanduser())
        return assessment
    assessment_summary = browser.get("html_assessment") if isinstance(browser.get("html_assessment"), dict) else {}
    report = assessment_summary.get("report")
    if report:
        assessment = _load_json(Path(str(report)).expanduser())
        assessment["report"] = str(report)
        return assessment
    candidate = repo_root / "evals" / "reports" / f"{project_path.name}-visual" / "html-assessment.json"
    assessment = _load_json(candidate)
    if assessment:
        assessment["report"] = str(candidate)
    return assessment


def _page_lookup(pages: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(pages, list):
        return {}
    lookup: dict[int, dict[str, Any]] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page_number") or page.get("page_num") or 0)
        if page_number > 0:
            lookup[page_number] = page
    return lookup


def _metadata_image_region_lookup(metadata: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    layout = metadata.get("layout") if isinstance(metadata.get("layout"), dict) else {}
    field_config = layout.get("field_config") if isinstance(layout.get("field_config"), dict) else {}
    regions = field_config.get("image_regions") if isinstance(field_config.get("image_regions"), list) else []
    lookup: dict[int, list[dict[str, Any]]] = {}
    for region in regions:
        if not isinstance(region, dict):
            continue
        try:
            page_number = int(region.get("page_number") or region.get("page") or 0)
        except (TypeError, ValueError):
            continue
        if page_number <= 0:
            continue
        lookup.setdefault(page_number, []).append(region)
    return lookup


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build per-page evidence packets for exact PDF repair loops.")
    parser.add_argument("project_dir", help="Path to a generated exact project")
    parser.add_argument("--output-dir", default=None, help="Packet pages directory. Default: evals/reports/{project_id}/pages")
    parser.add_argument("--visual-report", default=None, help="Path to visual-diff.json")
    parser.add_argument("--assessment", default=None, help="Path to html-assessment.json")
    parser.add_argument("--browser-evidence", default=None, help="Path to browser_qa.json")
    args = parser.parse_args()
    path = write_page_packets(
        args.project_dir,
        output_dir=args.output_dir,
        visual_report_path=args.visual_report,
        assessment_path=args.assessment,
        browser_evidence_path=args.browser_evidence,
    )
    print(path)


if __name__ == "__main__":
    main()
