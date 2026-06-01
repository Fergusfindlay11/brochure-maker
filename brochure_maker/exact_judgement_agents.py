"""Deterministic judgement-agent artifacts for exact brochure page packets.

These artifacts are the contract between Codex-as-orchestrator and the focused
subagents used during brochure repair. They do not try to replace a real visual
model; they package the current evidence into named critic outputs so a numeric
score cannot pass without an inspectable judgement trail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


AGENT_OUTPUT_FILES = (
    "visual-critic.json",
    "browser-interaction-qa.json",
    "editability-critic.json",
    "extraction-diagnosis.json",
    "repair-planner.json",
    "hardcoding-critic.json",
)

PASS_SCORE = 95.0


def build_page_judgement_agents(
    *,
    project_id: str,
    page_number: int,
    page_dir: Path,
    score: float,
    accepted: bool,
    assessment_page: dict[str, Any],
    graph_page: dict[str, Any],
    inventory_page: dict[str, Any],
    layout_page: dict[str, Any],
    browser_page: dict[str, Any],
    evidence_files: dict[str, str],
    copied_images: dict[str, str],
    page_critique: dict[str, Any],
    repair_plan: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return all page-level judgement-agent outputs."""
    context = {
        "schema": "brochure-maker.page-agent-context.v1",
        "project_id": project_id,
        "page_number": page_number,
        "packet_dir": str(page_dir),
        "evidence": {
            **{key: str(value) for key, value in copied_images.items()},
            **{key: str(value) for key, value in evidence_files.items()},
        },
    }
    visual = _visual_critic(
        context=context,
        score=score,
        accepted=accepted,
        assessment_page=assessment_page,
        graph_page=graph_page,
        copied_images=copied_images,
        browser_page=browser_page,
    )
    interaction = _browser_interaction_qa(
        context=context,
        browser_page=browser_page,
        accepted=accepted,
    )
    editability = _editability_critic(
        context=context,
        graph_page=graph_page,
        inventory_page=inventory_page,
        layout_page=layout_page,
        browser_page=browser_page,
        accepted=accepted,
    )
    diagnosis = _extraction_diagnosis(
        context=context,
        visual=visual,
        interaction=interaction,
        editability=editability,
        assessment_page=assessment_page,
        page_critique=page_critique,
    )
    planner = _repair_planner_agent(
        context=context,
        repair_plan=repair_plan,
        diagnosis=diagnosis,
        accepted=accepted,
    )
    hardcoding = _hardcoding_critic(
        context=context,
        repair_plan=repair_plan,
        page_critique=page_critique,
        diagnosis=diagnosis,
    )
    return {
        "visual-critic.json": visual,
        "browser-interaction-qa.json": interaction,
        "editability-critic.json": editability,
        "extraction-diagnosis.json": diagnosis,
        "repair-planner.json": planner,
        "hardcoding-critic.json": hardcoding,
    }


def summarise_judgement_agents(outputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compact page-packet index summary for the judgement artifacts."""
    agents: dict[str, Any] = {}
    blockers: list[str] = []
    for filename in AGENT_OUTPUT_FILES:
        payload = outputs.get(filename) if isinstance(outputs.get(filename), dict) else {}
        agent_name = str(payload.get("agent") or filename.removesuffix(".json"))
        accepted = bool(payload.get("accepted"))
        agent_blockers = [str(item) for item in payload.get("blockers") or []]
        agents[agent_name] = {
            "artifact": filename,
            "accepted": accepted,
            "status": payload.get("status"),
            "blocker_count": len(agent_blockers),
        }
        blockers.extend(f"{agent_name}: {blocker}" for blocker in agent_blockers)
    return {
        "schema": "brochure-maker.page-judgement-agents.v1",
        "accepted": all(bool(item["accepted"]) for item in agents.values()) if agents else False,
        "agent_count": len(agents),
        "agents": agents,
        "blockers": blockers,
    }


def _visual_critic(
    *,
    context: dict[str, Any],
    score: float,
    accepted: bool,
    assessment_page: dict[str, Any],
    graph_page: dict[str, Any],
    copied_images: dict[str, str],
    browser_page: dict[str, Any],
) -> dict[str, Any]:
    findings = _findings(assessment_page)
    severe = [item for item in findings if item.get("severity") in {"blocker", "major"}]
    blockers: list[str] = []
    if score < PASS_SCORE:
        blockers.append(f"Page visual score {score:.3f} is below {PASS_SCORE:.3f}")
    for finding in severe:
        blockers.append(str(finding.get("issue") or finding.get("problem") or "Major visual finding"))
    for required in ("source.png", "generated.png", "diff.png"):
        if not copied_images.get(required):
            blockers.append(f"Missing visual evidence file: {required}")
    screenshot = browser_page.get("packet_screenshot") or browser_page.get("screenshot")
    warnings = [] if screenshot else ["Browser screenshot is not attached to this page packet yet."]
    return {
        **context,
        "schema": "brochure-maker.agent.visual-critic.v1",
        "agent": "visual-critic",
        "purpose": "Compare source, generated, diff, and Browser screenshot evidence for this page.",
        "accepted": not blockers,
        "status": "accepted" if not blockers else "rejected",
        "score": round(float(score), 3),
        "page_purpose": graph_page.get("purpose") or assessment_page.get("purpose") or "",
        "blockers": blockers,
        "warnings": warnings,
        "top_failures": [
            {
                "feature": _feature_from_finding(finding),
                "problem": finding.get("issue") or finding.get("problem"),
                "severity": finding.get("severity"),
                "evidence": finding.get("evidence"),
                "likely_cause": finding.get("likely_cause"),
            }
            for finding in findings
        ],
        "pass_fail_rules": [
            "Reject if page score is below 95.",
            "Reject if any blocker or major visual finding remains.",
            "Reject if source/generated/diff evidence is missing.",
        ],
        "orchestrator_note": (
            "This is an artifact-first critic. A real visual subagent should inspect the linked images before final acceptance."
        ),
        "source_score_accepted": bool(accepted),
    }


def _browser_interaction_qa(
    *,
    context: dict[str, Any],
    browser_page: dict[str, Any],
    accepted: bool,
) -> dict[str, Any]:
    assertions = browser_page.get("assertions") if isinstance(browser_page.get("assertions"), dict) else {}
    interaction_audit = browser_page.get("interaction_audit") if isinstance(browser_page.get("interaction_audit"), dict) else {}
    hotspots = interaction_audit.get("hotspots") if isinstance(interaction_audit.get("hotspots"), list) else []
    background_issues = (
        interaction_audit.get("backgroundIssues")
        if isinstance(interaction_audit.get("backgroundIssues"), list)
        else []
    )
    blockers: list[str] = []
    if assertions.get("source_preserved_pages_have_no_giant_interactive_hotspots") is False:
        blockers.append("Browser interaction audit rejected source-preserved hover/click hotspots.")
    if hotspots:
        blockers.append(f"{len(hotspots)} giant source-preserved text hotspots remain on this page.")
    if background_issues:
        blockers.append(f"{len(background_issues)} preserved PDF background interaction issues remain on this page.")
    text_overlaps = browser_page.get("textOverlaps") if isinstance(browser_page.get("textOverlaps"), list) else []
    if text_overlaps:
        blockers.append(f"{len(text_overlaps)} Browser text overlap blockers remain on this page.")
    clean_export = browser_page.get("clean_export") if isinstance(browser_page.get("clean_export"), dict) else {}
    if assertions.get("export_has_no_editor_chrome") is False:
        blockers.append("Clean export still contains editor chrome according to Browser QA.")
    required_assertions = (
        ("state_roundtrip_preserved", "Browser QA did not prove state/reload persistence."),
        ("typed_text_font_preserved", "Browser QA did not prove typed text keeps extracted typography."),
        ("media_slots_have_actionable_controls", "Browser QA did not prove media slots have actionable controls."),
        ("image_replacement_roundtrip_preserved", "Browser QA did not prove image replacement state/export."),
        ("logo_replacement_roundtrip_preserved", "Browser QA did not prove logo replacement state/export."),
        ("map_replacement_roundtrip_preserved", "Browser QA did not prove map replacement state/export."),
    )
    for key, message in required_assertions:
        if assertions.get(key) is not True:
            blockers.append(message)
    return {
        **context,
        "schema": "brochure-maker.agent.browser-interaction-qa.v1",
        "agent": "browser-interaction-qa",
        "purpose": "Click, hover, edit, reload, and export checks for the real editor surface.",
        "accepted": not blockers,
        "status": "accepted" if not blockers else "rejected",
        "blockers": blockers,
        "checked_assertions": {
            "source_preserved_pages_have_no_giant_interactive_hotspots": assertions.get(
                "source_preserved_pages_have_no_giant_interactive_hotspots"
            ),
            "export_has_no_editor_chrome": assertions.get("export_has_no_editor_chrome"),
            "state_roundtrip_preserved": assertions.get("state_roundtrip_preserved"),
            "typed_text_font_preserved": assertions.get("typed_text_font_preserved"),
            "media_slots_have_actionable_controls": assertions.get("media_slots_have_actionable_controls"),
            "image_replacement_roundtrip_preserved": assertions.get("image_replacement_roundtrip_preserved"),
            "logo_replacement_roundtrip_preserved": assertions.get("logo_replacement_roundtrip_preserved"),
            "map_replacement_roundtrip_preserved": assertions.get("map_replacement_roundtrip_preserved"),
        },
        "interaction_audit": interaction_audit,
        "clean_export": {
            "pageCount": clean_export.get("pageCount"),
            "contenteditableCount": clean_export.get("contenteditableCount"),
            "fileInputCount": clean_export.get("fileInputCount"),
            "scriptCount": clean_export.get("scriptCount"),
        },
        "manual_browser_tasks": [
            "Hover replaceable image/map regions and screenshot before/after.",
            "Click the map/image area and confirm no giant overlay wakes up.",
            "Edit one expected text block and confirm persistence after reload.",
        ],
        "source_page_accepted": bool(accepted),
    }


def _editability_critic(
    *,
    context: dict[str, Any],
    graph_page: dict[str, Any],
    inventory_page: dict[str, Any],
    layout_page: dict[str, Any],
    browser_page: dict[str, Any],
    accepted: bool,
) -> dict[str, Any]:
    elements = [item for item in graph_page.get("elements") or [] if isinstance(item, dict)]
    roles = _role_counts(elements)
    image_regions = _image_regions(layout_page, inventory_page, graph_page)
    blockers: list[str] = []
    warnings: list[str] = []
    expected_editable_text = len(inventory_page.get("editable_text_blocks") or [])
    page_box = browser_page.get("pageBox") if isinstance(browser_page.get("pageBox"), dict) else {}
    browser_media_slots = browser_page.get("mediaSlots") if isinstance(browser_page.get("mediaSlots"), list) else []
    browser_media_roles = [str(slot.get("role") or slot.get("kind") or "") for slot in browser_media_slots if isinstance(slot, dict)]
    browser_media_kinds = [str(slot.get("kind") or "") for slot in browser_media_slots if isinstance(slot, dict)]
    if expected_editable_text and page_box and int(page_box.get("textFields") or 0) == 0:
        blockers.append("Inventory expects editable text but Browser page box reports no text fields.")
    elif expected_editable_text and not page_box:
        warnings.append("Browser page-level text-field count is unavailable; run richer in-app Browser page QA for final proof.")
    replaceable_media_roles = {
        "photo-region",
        "photo-grid",
        "hero-photo",
        "artwork-image",
        "space-plan",
        "map",
        "agency-logo",
        "source-facade-mark",
        "repeated-header-mark",
    }
    expected_browser_media_kinds: list[str] = []
    for region in image_regions:
        role = str(region.get("role") or "")
        if role in replaceable_media_roles:
            expected_browser_media_kinds.extend(_browser_kinds_for_role(role))
            if region.get("replaceable") is not True and region.get("editable") is not True:
                blockers.append(f"{role} region is expected to be editable/replaceable but lacks positive editability evidence.")
            if not browser_media_slots:
                blockers.append(f"{role} region has no Browser media/logo/map slot evidence on this page.")
            elif not _browser_media_matches_role(role, browser_media_roles, browser_media_kinds):
                blockers.append(f"{role} region has no matching Browser media/logo/map slot on this page.")
    expected_kind_counts = _string_counts(expected_browser_media_kinds)
    actual_kind_counts = _string_counts(browser_media_kinds)
    for kind, expected_count in expected_kind_counts.items():
        actual_count = int(actual_kind_counts.get(kind, 0))
        if actual_count < expected_count:
            blockers.append(
                f"Browser page has {actual_count} {kind} slots but PDF/design evidence expects {expected_count}."
            )
    text_report = browser_page.get("text_reconstruction") if isinstance(browser_page.get("text_reconstruction"), dict) else {}
    if text_report.get("accepted") is False:
        blockers.append("Text reconstruction report rejected visible text/contact recall.")
    return {
        **context,
        "schema": "brochure-maker.agent.editability-critic.v1",
        "agent": "editability-critic",
        "purpose": "Check whether brochure concepts are editable, not just visible.",
        "accepted": not blockers,
        "status": "accepted" if not blockers else "rejected",
        "blockers": blockers,
        "warnings": warnings,
        "role_counts": roles,
        "editable_text_blocks_expected": expected_editable_text,
        "browser_text_fields": page_box.get("textFields"),
        "image_region_roles": _role_counts(image_regions),
        "browser_media_slot_roles": _string_counts(browser_media_roles),
        "browser_media_slot_kinds": actual_kind_counts,
        "expected_browser_media_slot_kinds": expected_kind_counts,
        "replaceable_region_count": sum(1 for region in image_regions if region.get("replaceable")),
        "static_region_count": sum(1 for region in image_regions if region.get("locked_static")),
        "pass_fail_rules": [
            "Reject if expected editable text has no Browser-visible field.",
            "Reject if photo/artwork/map/space-plan regions are marked static.",
            "Reject if contact/text reconstruction reports missing digits, split words, or broken emails.",
        ],
        "source_page_accepted": bool(accepted),
    }


def _extraction_diagnosis(
    *,
    context: dict[str, Any],
    visual: dict[str, Any],
    interaction: dict[str, Any],
    editability: dict[str, Any],
    assessment_page: dict[str, Any],
    page_critique: dict[str, Any],
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for finding in _findings(assessment_page):
        failures.append(
            {
                "source": "html-assessment",
                "feature": _feature_from_finding(finding),
                "problem": finding.get("issue") or finding.get("problem"),
                "subsystem": _subsystem_from_finding(finding),
                "severity": finding.get("severity") or "minor",
            }
        )
    for agent in (visual, interaction, editability):
        for blocker in agent.get("blockers") or []:
            failures.append(
                {
                    "source": agent.get("agent"),
                    "feature": _feature_from_text(str(blocker)),
                    "problem": str(blocker),
                    "subsystem": _subsystem_from_text(str(blocker)),
                    "severity": "major",
                }
            )
    blockers = [failure["problem"] for failure in failures if failure.get("severity") in {"blocker", "major"}]
    subsystems = sorted(dict.fromkeys(str(item.get("subsystem") or "unknown/page-review") for item in failures))
    return {
        **context,
        "schema": "brochure-maker.agent.extraction-diagnosis.v1",
        "agent": "extraction-diagnosis",
        "purpose": "Map page failures to the reusable extraction/render/export subsystem that should be repaired.",
        "accepted": not blockers,
        "status": "accepted" if not blockers else "needs-repair",
        "blockers": blockers,
        "likely_subsystems": subsystems,
        "failures": failures,
        "page_critique_status": page_critique.get("status"),
        "diagnosis_rules": {
            "text": "split words, missing digits, overlaps, or font loss point to typography/text reconstruction.",
            "interaction": "giant hover/click overlays point to source-preserved interaction suppression.",
            "image": "missing photo/artwork/map controls point to image region classification or renderer slots.",
            "export": "editor chrome in clean export points to app.py state/export stripping.",
        },
    }


def _repair_planner_agent(
    *,
    context: dict[str, Any],
    repair_plan: dict[str, Any],
    diagnosis: dict[str, Any],
    accepted: bool,
) -> dict[str, Any]:
    tasks = repair_plan.get("tasks") if isinstance(repair_plan.get("tasks"), list) else []
    blockers: list[str] = []
    if not accepted and not tasks:
        blockers.append("Page failed but repair planner has no focused tasks.")
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if not task.get("repair_subsystem"):
            blockers.append("Repair task is missing repair_subsystem.")
        if not task.get("allowed_repair_types"):
            blockers.append(f"Repair task for {task.get('repair_subsystem') or 'unknown'} has no allowed repair types.")
    return {
        **context,
        "schema": "brochure-maker.agent.repair-planner.v1",
        "agent": "repair-planner",
        "purpose": "Turn diagnosis into page-scoped, reusable repair work.",
        "accepted": not blockers,
        "status": "accepted" if not blockers else "needs-planner-repair",
        "blockers": blockers,
        "target_score": PASS_SCORE,
        "rerender_scope": repair_plan.get("rerender_scope"),
        "tasks": tasks,
        "diagnosed_subsystems": diagnosis.get("likely_subsystems") or [],
        "feeds": "brochure_maker/exact_code_repair_tasks.py",
    }


def _hardcoding_critic(
    *,
    context: dict[str, Any],
    repair_plan: dict[str, Any],
    page_critique: dict[str, Any],
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    forbidden_terms = ("copy existing project", "hardcoded project", "project-specific", "fixed coordinates")
    for task in repair_plan.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        text = repr(task).lower()
        for term in forbidden_terms:
            if term in text and "disallowed" not in text:
                blockers.append(f"Potential hardcoding risk mentioned without a disallowed guard: {term}")
    rules = [
        "Use PDF-derived evidence only: page packet JSON, source render, Browser screenshot, and generated render.",
        "Do not copy existing project HTML, state, coordinates, or manually corrected assets.",
        "Promote repeated failures to reusable detectors/rendering rules with tests.",
    ]
    return {
        **context,
        "schema": "brochure-maker.agent.hardcoding-critic.v1",
        "agent": "hardcoding-critic",
        "purpose": "Reject project-specific patches before code repair work is accepted.",
        "accepted": not blockers,
        "status": "accepted" if not blockers else "rejected",
        "blockers": sorted(dict.fromkeys(blockers)),
        "rules": rules,
        "final_code_scan": "The run-level critic scans reusable code for project/source literals after repairs.",
    }


def _findings(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in page.get("findings") or [] if isinstance(item, dict)]


def _feature_from_finding(finding: dict[str, Any]) -> str:
    roles = finding.get("roles") if isinstance(finding.get("roles"), list) else []
    if roles:
        return str(roles[0])
    feature = finding.get("feature")
    if feature:
        return str(feature)
    subsystem = str(finding.get("repair_subsystem") or finding.get("repair_task") or "")
    return subsystem.split("/", 1)[0] if subsystem else "page"


def _feature_from_text(text: str) -> str:
    lower = text.lower()
    if "hover" in lower or "click" in lower:
        return "interaction-hotspot"
    if "text" in lower or "font" in lower or "digit" in lower or "email" in lower:
        return "text"
    if "image" in lower or "photo" in lower or "artwork" in lower:
        return "image"
    if "export" in lower:
        return "export"
    return "page"


def _subsystem_from_finding(finding: dict[str, Any]) -> str:
    subsystem = str(finding.get("repair_subsystem") or finding.get("repair_task") or "").strip()
    if subsystem:
        return subsystem
    return _subsystem_from_text(str(finding.get("issue") or finding.get("problem") or ""))


def _subsystem_from_text(text: str) -> str:
    lower = text.lower()
    if "hover" in lower or "click" in lower or "hotspot" in lower:
        return "renderer/interaction-hotspots"
    if "contact" in lower or "email" in lower or "digits" in lower or "phone" in lower:
        return "contacts/text-reconstruction"
    if "font" in lower or "text" in lower or "overlap" in lower:
        return "typography/text-reconstruction"
    if "map" in lower:
        return "maps/map-reconstruction"
    if "space" in lower or "floor" in lower:
        return "space-plans/plan-detection"
    if "image" in lower or "photo" in lower or "artwork" in lower:
        return "images/image-region-classifier"
    if "export" in lower:
        return "export/clean-html"
    return "unknown/page-review"


def _image_regions(
    layout_page: dict[str, Any],
    inventory_page: dict[str, Any],
    graph_page: dict[str, Any],
) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    primary_kind_counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()

    def add_region(region: dict[str, Any]) -> None:
        role = str(region.get("role") or region.get("semantic_role") or "")
        kind = (_browser_kinds_for_role(role) or [""])[0]
        if kind == "source-logo" and not _region_has_bbox(region):
            return
        key = (role, _region_bbox_signature(region))
        if key in seen:
            return
        seen.add(key)
        regions.append(region)
        if kind:
            primary_kind_counts[kind] = primary_kind_counts.get(kind, 0) + 1

    sources = [layout_page.get("image_regions")]
    if not layout_page.get("_metadata_image_regions_primary"):
        sources.append(inventory_page.get("image_regions"))
    for source in sources:
        if isinstance(source, list):
            for item in source:
                if isinstance(item, dict):
                    add_region(item)
    for element in graph_page.get("elements") or []:
        if isinstance(element, dict) and element.get("type") in {"image", "map", "floorplan", "logo", "agency-logo"}:
            role = str(element.get("role") or element.get("semantic_role") or "")
            kind = (_browser_kinds_for_role(role) or [""])[0]
            if kind == "source-logo" and not _region_has_bbox(element):
                continue
            if layout_page.get("_metadata_image_regions_primary") and kind in {
                "image",
                "artwork-image",
                "space-plan",
                "map",
                "agency-logo",
                "source-logo",
            }:
                continue
            # The model/exact-layout image regions are the primary evidence for
            # photos, artwork, maps and plans. The design graph re-expresses the
            # same concepts, often in normalized coordinates, so it must not
            # double-count them as separate missing editor slots.
            if kind in {"image", "artwork-image", "space-plan", "map", "agency-logo"} and primary_kind_counts.get(kind, 0) > 0:
                continue
            add_region(element)
    return regions


def _region_has_bbox(region: dict[str, Any]) -> bool:
    return isinstance(region.get("bbox"), dict) or isinstance(region.get("bbox_raw"), dict)


def _region_bbox_signature(region: dict[str, Any]) -> str:
    bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else region.get("bbox_raw")
    if not isinstance(bbox, dict):
        return str(region.get("id") or "no-bbox")
    values = []
    for key in ("left", "top", "width", "height", "x", "y"):
        value = bbox.get(key)
        if isinstance(value, (int, float)):
            values.append(f"{key}:{float(value):.4f}")
    return "|".join(values) or str(region.get("id") or "bbox")


def _browser_media_matches_role(role: str, browser_roles: list[str], browser_kinds: list[str]) -> bool:
    """Return whether Browser DOM media inventory proves a semantic role exists."""
    if role in browser_roles or role in browser_kinds:
        return True
    role_to_kinds = {
        "photo-region": {"image"},
        "photo-grid": {"image"},
        "hero-photo": {"image"},
        "artwork-image": {"artwork-image", "image"},
        "space-plan": {"space-plan"},
        "map": {"map"},
        "agency-logo": {"agency-logo"},
        "source-facade-mark": {"source-logo"},
        "repeated-header-mark": {"source-logo"},
    }
    return not role_to_kinds.get(role, set(browser_kinds)).isdisjoint(set(browser_kinds))


def _browser_kinds_for_role(role: str) -> list[str]:
    if role in {"photo-region", "photo-grid", "hero-photo"}:
        return ["image"]
    if role in {"artwork-image", "space-plan", "map", "agency-logo"}:
        return [role]
    if role in {"source-facade-mark", "repeated-header-mark"}:
        return ["source-logo"]
    return []


def _role_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        role = str(item.get("role") or item.get("semantic_role") or "unknown")
        counts[role] = counts.get(role, 0) + 1
    return dict(sorted(counts.items()))


def _string_counts(items: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = item or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
