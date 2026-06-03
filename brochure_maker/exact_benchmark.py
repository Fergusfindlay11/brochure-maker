"""Run the exact PDF benchmark loop as one repeatable operation."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from brochure_maker.exact_browser_qa import write_browser_qa
from brochure_maker.exact_browser_ui_qa import (
    REQUIRED_BROWSER_UI_ASSERTIONS,
    write_browser_ui_qa,
)
from brochure_maker.exact_code_repair_dispatcher import write_code_repair_dispatch
from brochure_maker.exact_code_repair_execution import write_code_repair_execution
from brochure_maker.exact_code_repair_tasks import write_code_repair_tasks
from brochure_maker.exact_control_quality import write_control_quality_report
from brochure_maker.exact_eval import write_eval_report
from brochure_maker.exact_export_qa import REQUIRED_EXPORT_ASSERTIONS, write_export_qa
from brochure_maker.exact_html_assessment import (
    attach_html_assessment_to_browser_qa,
    write_html_assessment,
)
from brochure_maker.exact_judgement_agents import AGENT_OUTPUT_FILES
from brochure_maker.exact_page_packets import write_page_packets
from brochure_maker.exact_repair_orchestrator import run_repair_orchestration
from brochure_maker.exact_repair_loop import write_repair_loop
from brochure_maker.exact_visual_diff import (
    attach_visual_diff_to_browser_qa,
    write_visual_diff_report,
)


REQUIRED_BROWSER_ASSERTIONS = (
    "editor_has_expected_pages",
    "global_controls_visible",
    "export_has_expected_pages",
    "export_has_no_editor_chrome",
    "state_roundtrip_preserved",
    "clean_export_preserves_edited_text",
    "clean_export_preserves_global_colour",
    "typed_text_font_preserved",
    "source_preserved_pages_have_no_giant_interactive_hotspots",
    "media_slots_have_actionable_controls",
    "image_replacement_roundtrip_preserved",
    "logo_replacement_roundtrip_preserved",
    "map_replacement_roundtrip_preserved",
)

BROWSER_ASSERTION_ALIASES = {
    "editor_has_expected_pages": ("editor_has_expected_pages", "editor_has_5_pages"),
    "export_has_expected_pages": ("export_has_expected_pages", "export_has_5_pages"),
}

PRODUCTION_SCAN_ROOTS = ("app.py", "brochure_maker")
EXACT_UPLOAD_TIMEOUT_SECONDS = 600
TERM_STOPWORDS = {
    "brochure",
    "pdf",
    "oct",
    "the",
    "and",
    "for",
    "street",
    "estate",
    "real",
    "anchor",
    "final",
    "house",
    "source",
}

GENERIC_HARDCODING_TOKENS = {
    # London geography and transport taxonomy words can legitimately appear in
    # reusable map/transport classifiers. Full source filenames and address
    # phrases are still scanned, but these single tokens should not turn a
    # benchmark into a false hardcoding failure.
    "london",
    "road",
    "station",
    "hammersmith",
    "circle",
    "district",
    "elizabeth",
    "metropolitan",
    "northern",
    "piccadilly",
    "thameslink",
    "victoria",
}


def run_exact_benchmark(
    *,
    pdf_path: str | Path | None = None,
    project_dir: str | Path | None = None,
    base_url: str = "http://127.0.0.1:8000",
    projects_dir: str | Path = "projects",
    output_root: str | Path = "evals/reports",
    page_limit: int | None = None,
) -> dict[str, Any]:
    """Run upload/eval/page-packet reports for an exact PDF project.

    ``pdf_path`` creates a fresh project through the real app endpoint.
    ``project_dir`` re-runs the benchmark reports for an existing generated
    project. This keeps fresh-import QA and post-repair QA on the same rails.
    """
    if bool(pdf_path) == bool(project_dir):
        raise ValueError("Provide exactly one of pdf_path or project_dir.")

    fresh_upload: dict[str, Any] | None = None
    if pdf_path:
        fresh_upload = upload_exact_pdf(pdf_path, base_url=base_url)
        project_id = str(fresh_upload["project_id"])
        project_path = Path(projects_dir).expanduser().resolve() / project_id
    else:
        project_path = Path(str(project_dir)).expanduser().resolve()
        project_id = project_path.name
        fresh_upload = _load_json(project_path / "exact_benchmark_upload.json") or None

    output_path = Path(output_root).expanduser().resolve()
    visual_dir = output_path / f"{project_id}-visual"
    run_dir = output_path / project_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if fresh_upload:
        upload_record_path = project_path / "exact_benchmark_upload.json"
        upload_record_path.write_text(json.dumps(fresh_upload, indent=2, sort_keys=True), encoding="utf-8")
        (run_dir / "upload-response.json").write_text(json.dumps(fresh_upload, indent=2, sort_keys=True), encoding="utf-8")

    visual_report_path = write_visual_diff_report(
        project_path,
        visual_dir,
        base_url=base_url,
        page_limit=page_limit,
    )
    attach_visual_diff_to_browser_qa(project_path, visual_report_path)

    write_browser_qa(project_path, base_url=base_url, force=True)
    export_qa_path = write_export_qa(
        project_path,
        base_url=base_url,
        output_path=run_dir / "export-qa.json",
        force=True,
    )
    browser_ui_qa_path = write_browser_ui_qa(
        project_path,
        base_url=base_url,
        output_path=project_path / "browser_ui_qa.json",
        export_qa_path=export_qa_path,
        force=True,
    )
    control_quality_path = write_control_quality_report(
        project_path,
        run_dir / "control-quality.json",
    )
    assessment_path = visual_dir / "html-assessment.json"
    write_html_assessment(
        project_path,
        assessment_path,
        visual_report_path=visual_report_path,
        browser_evidence_path=project_path / "browser_qa.json",
    )
    attach_html_assessment_to_browser_qa(project_path, assessment_path)

    page_packets_path = write_page_packets(
        project_path,
        output_dir=run_dir / "pages",
        visual_report_path=visual_report_path,
        assessment_path=assessment_path,
    )
    code_repair_tasks_path = write_code_repair_tasks(
        project_path,
        page_packets_path=page_packets_path,
        output_path=run_dir / "code-repair-tasks.json",
    )
    code_repair_dispatch_path = write_code_repair_dispatch(
        project_path,
        code_repair_tasks_path=code_repair_tasks_path,
        output_dir=run_dir / "code-repair-workers",
    )
    code_repair_execution_path = write_code_repair_execution(
        project_path,
        dispatch_path=code_repair_dispatch_path,
        output_path=run_dir / "code-repair-execution.json",
    )
    repair_loop_path = write_repair_loop(
        project_path,
        page_packets_path=page_packets_path,
        output_path=run_dir / "repair-loop.json",
    )
    repair_orchestration_path = run_dir / "repair-orchestration.json"
    run_repair_orchestration(
        project_path,
        repair_loop_path=repair_loop_path,
        output_path=repair_orchestration_path,
        base_url=base_url,
    )
    eval_path = write_eval_report(project_path, run_dir / "eval.json")

    visual = _load_json(visual_report_path)
    assessment = _load_json(assessment_path)
    page_packets = _load_json(page_packets_path)
    code_repair_tasks = _load_json(code_repair_tasks_path)
    code_repair_dispatch = _load_json(code_repair_dispatch_path)
    code_repair_execution = _load_json(code_repair_execution_path)
    repair_loop = _load_json(repair_loop_path)
    repair_orchestration = _load_json(repair_orchestration_path)
    eval_report = _load_json(eval_path)
    browser_qa_path = project_path / "browser_qa.json"
    browser_qa = _load_json(browser_qa_path)
    browser_ui_qa = _load_json(browser_ui_qa_path)
    export_qa = _load_json(export_qa_path)
    control_quality = _load_json(control_quality_path)
    browser_assertions = browser_qa.get("assertions") if isinstance(browser_qa.get("assertions"), dict) else {}
    browser_ui_assertions = browser_ui_qa.get("assertions") if isinstance(browser_ui_qa.get("assertions"), dict) else {}
    export_assertions = export_qa.get("assertions") if isinstance(export_qa.get("assertions"), dict) else {}
    browser_blockers = [
        f"Missing or false Browser assertion: {name}"
        for name in REQUIRED_BROWSER_ASSERTIONS
        if not browser_assertion_passed(browser_assertions, name)
    ]
    browser_ui_blockers = [
        f"Missing or false Browser UI assertion: {name}"
        for name in REQUIRED_BROWSER_UI_ASSERTIONS
        if not browser_assertion_passed(browser_ui_assertions, name)
    ]
    export_blockers = [
        f"Missing or false export assertion: {name}"
        for name in REQUIRED_EXPORT_ASSERTIONS
        if not browser_assertion_passed(export_assertions, name)
    ]
    blockers = sorted(
        {
            *[str(item) for item in visual.get("blockers") or []],
            *[str(item) for item in assessment.get("blockers") or []],
            *[str(item) for item in repair_loop.get("blockers") or []],
            *[str(item) for item in _repair_orchestration_blockers(repair_orchestration, repair_loop)],
            *[str(item) for item in eval_report.get("blockers") or []],
            *browser_blockers,
            *[str(item) for item in browser_ui_qa.get("blockers") or []],
            *browser_ui_blockers,
            *[str(item) for item in export_qa.get("blockers") or []],
            *export_blockers,
            *[str(item) for item in control_quality.get("blockers") or []],
        }
    )
    critic_report = build_critic_report(
        project_path=project_path,
        output_dir=run_dir,
        project_id=project_id,
        visual=visual,
        assessment=assessment,
        page_packets=page_packets,
        repair_loop=repair_loop,
        eval_report=eval_report,
        browser_assertions=browser_assertions,
        browser_blockers=browser_blockers,
        browser_ui_qa=browser_ui_qa,
        browser_ui_blockers=browser_ui_blockers,
        export_qa=export_qa,
        export_blockers=export_blockers,
        control_quality=control_quality,
        code_repair_tasks=code_repair_tasks,
        code_repair_dispatch=code_repair_dispatch,
        code_repair_execution=code_repair_execution,
        repair_orchestration=repair_orchestration,
        component_blockers=blockers,
        fresh_upload=fresh_upload,
    )
    critic_blockers = [str(item) for item in critic_report.get("blockers") or []]
    blockers = sorted({*blockers, *critic_blockers})
    report = {
        "schema": "brochure-maker.exact-benchmark-run.v1",
        "project_id": project_id,
        "project_dir": str(project_path),
        "fresh_upload": fresh_upload,
        "source_mode": "fresh-upload" if fresh_upload else "existing-project",
        "base_url": base_url.rstrip("/"),
        "accepted": bool(
            visual.get("accepted")
            and assessment.get("accepted")
            and page_packets.get("accepted")
            and repair_loop.get("accepted")
            and repair_orchestration.get("accepted")
            and eval_report.get("accepted")
            and not browser_blockers
            and browser_ui_qa.get("accepted")
            and not browser_ui_blockers
            and export_qa.get("accepted")
            and not export_blockers
            and control_quality.get("accepted")
            and critic_report.get("accepted")
        ),
        "scores": {
            "visual": visual.get("score"),
            "html_assessment": assessment.get("score"),
            "eval": eval_report.get("score"),
            "critic": critic_report.get("score"),
            "repair_loop": 100.0 if repair_loop.get("accepted") else 0.0,
            "browser_ui": 100.0 if browser_ui_qa.get("accepted") else 0.0,
            "export": 100.0 if export_qa.get("accepted") else 0.0,
            "control_quality": 100.0 if control_quality.get("accepted") else 0.0,
        },
        "failed_pages": page_packets.get("failed_pages") or [],
        "blockers": blockers,
        "browser_assertions": browser_assertions,
        "browser_ui_assertions": browser_ui_assertions,
        "export_assertions": export_assertions,
        "required_browser_assertions": list(REQUIRED_BROWSER_ASSERTIONS),
        "required_browser_ui_assertions": list(REQUIRED_BROWSER_UI_ASSERTIONS),
        "required_export_assertions": list(REQUIRED_EXPORT_ASSERTIONS),
        "browser_http_qa": {
            "accepted": not browser_blockers and bool(browser_qa),
            "assertions": browser_assertions,
            "blockers": browser_qa.get("blockers") or browser_blockers,
            "artifact": str(browser_qa_path),
        },
        "browser_ui_qa": {
            "accepted": browser_ui_qa.get("accepted"),
            "assertions": browser_ui_assertions,
            "blockers": browser_ui_qa.get("blockers") or browser_ui_blockers,
            "artifact": str(browser_ui_qa_path),
            "confidence": browser_ui_qa.get("confidence"),
            "browser_surface": browser_ui_qa.get("browser_surface"),
        },
        "html_export": export_qa.get("html_export") or {},
        "pdf_export": export_qa.get("pdf_export") or {},
        "control_quality": {
            "accepted": control_quality.get("accepted"),
            "blockers": control_quality.get("blockers") or [],
            "artifact": str(control_quality_path),
        },
        "artifacts": {
            "visual_diff": str(visual_report_path),
            "html_assessment": str(assessment_path),
            "page_packets": str(page_packets_path),
            "code_repair_tasks": str(code_repair_tasks_path),
            "code_repair_dispatch": str(code_repair_dispatch_path),
            "code_repair_execution": str(code_repair_execution_path),
            "repair_loop": str(repair_loop_path),
            "repair_orchestration": str(repair_orchestration_path) if repair_orchestration_path.exists() else None,
            "eval": str(eval_path),
            "browser_qa": str(browser_qa_path),
            "browser_http_qa": str(browser_qa_path),
            "browser_ui_qa": str(browser_ui_qa_path),
            "export_qa": str(export_qa_path),
            "control_quality": str(control_quality_path),
            "critic": str(run_dir / "critic-report.json"),
        },
        "next_repair_tasks": assessment.get("next_repair_tasks") or [],
        "code_repair_tasks": {
            "accepted": code_repair_tasks.get("accepted"),
            "status": code_repair_tasks.get("status"),
            "task_count": code_repair_tasks.get("task_count"),
            "required_task_count": code_repair_tasks.get("required_task_count"),
            "optional_task_count": code_repair_tasks.get("optional_task_count"),
            "blockers": code_repair_tasks.get("blockers") or [],
        },
        "code_repair_dispatch": {
            "accepted": code_repair_dispatch.get("accepted"),
            "status": code_repair_dispatch.get("status"),
            "dispatched_task_count": code_repair_dispatch.get("dispatched_task_count"),
            "required_dispatch_count": code_repair_dispatch.get("required_dispatch_count"),
            "optional_dispatch_count": code_repair_dispatch.get("optional_dispatch_count"),
            "blockers": code_repair_dispatch.get("blockers") or [],
        },
        "code_repair_execution": {
            "accepted": code_repair_execution.get("accepted"),
            "status": code_repair_execution.get("status"),
            "required_execution_count": code_repair_execution.get("required_execution_count"),
            "required_accepted_count": code_repair_execution.get("required_accepted_count"),
            "optional_execution_count": code_repair_execution.get("optional_execution_count"),
            "blockers": code_repair_execution.get("blockers") or [],
        },
        "critic": {
            "accepted": critic_report.get("accepted"),
            "score": critic_report.get("score"),
            "blockers": critic_report.get("blockers") or [],
            "hardcoding_scan": critic_report.get("hardcoding_scan") or {},
        },
        "repair_loop": {
            "accepted": repair_loop.get("accepted"),
            "status": repair_loop.get("status"),
            "failed_page_count": repair_loop.get("failed_page_count"),
            "skipped_page_count": repair_loop.get("skipped_page_count"),
            "blockers": repair_loop.get("blockers") or [],
        },
        "repair_orchestration": {
            "accepted": repair_orchestration.get("accepted"),
            "status": repair_orchestration.get("status"),
            "attempt_count": repair_orchestration.get("attempt_count"),
            "escalation_count": repair_orchestration.get("escalation_count"),
        },
    }
    run_report_path = run_dir / "benchmark-run.json"
    run_report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    latest = output_path / "latest-benchmark.json"
    latest.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    generic_latest = output_path / "latest.json"
    generic_latest.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def build_critic_report(
    *,
    project_path: Path,
    output_dir: Path,
    project_id: str,
    visual: dict[str, Any],
    assessment: dict[str, Any],
    page_packets: dict[str, Any],
    repair_loop: dict[str, Any],
    eval_report: dict[str, Any],
    browser_assertions: dict[str, Any],
    browser_blockers: list[str],
    browser_ui_qa: dict[str, Any],
    browser_ui_blockers: list[str],
    export_qa: dict[str, Any],
    export_blockers: list[str],
    control_quality: dict[str, Any],
    code_repair_tasks: dict[str, Any],
    code_repair_dispatch: dict[str, Any],
    code_repair_execution: dict[str, Any],
    repair_orchestration: dict[str, Any],
    component_blockers: list[str],
    fresh_upload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Write the final critic gate for reuse/provenance and acceptance evidence."""
    page_entries = page_packets.get("packets") if isinstance(page_packets.get("packets"), list) else []
    page_scores = [
        float(page.get("score") or 0)
        for page in page_entries
        if isinstance(page, dict)
    ]
    lowest_page_score = min(page_scores, default=0.0)
    required_browser = {
        name: browser_assertion_passed(browser_assertions, name)
        for name in REQUIRED_BROWSER_ASSERTIONS
    }
    hardcoding_scan = scan_reusable_code_for_project_literals(project_path, project_id, fresh_upload=fresh_upload)
    critique_audit = audit_page_critiques(page_packets)
    packet_artifact_audit = audit_page_packet_artifacts(page_packets)
    judgement_agent_audit = audit_judgement_agents(page_packets)
    code_repair_audit = audit_code_repair_tasks(code_repair_tasks)
    code_repair_dispatch_audit = audit_code_repair_dispatch(code_repair_dispatch, code_repair_tasks)
    code_repair_execution_audit = audit_code_repair_execution(code_repair_execution)
    repair_orchestration_audit = audit_repair_orchestration(repair_orchestration, repair_loop)
    blockers: list[str] = []
    blockers.extend(str(item) for item in component_blockers)
    blockers.extend(browser_blockers)
    blockers.extend(browser_ui_blockers)
    blockers.extend(export_blockers)
    blockers.extend(str(item) for item in browser_ui_qa.get("blockers") or [])
    blockers.extend(str(item) for item in export_qa.get("blockers") or [])
    blockers.extend(str(item) for item in control_quality.get("blockers") or [])
    blockers.extend(packet_artifact_audit.get("blockers") or [])
    blockers.extend(judgement_agent_audit.get("blockers") or [])
    blockers.extend(critique_audit.get("blockers") or [])
    blockers.extend(code_repair_audit.get("blockers") or [])
    blockers.extend(code_repair_dispatch_audit.get("blockers") or [])
    blockers.extend(code_repair_execution_audit.get("blockers") or [])
    blockers.extend(repair_orchestration_audit.get("blockers") or [])
    if page_scores and lowest_page_score < 95:
        blockers.append(f"Lowest page score {lowest_page_score:.3f} is below 95.000")
    if not page_scores:
        blockers.append("No page scores are available for critic review")
    if not repair_loop.get("accepted") and page_packets.get("accepted"):
        blockers.append("Repair loop artifact did not accept an otherwise passing page set")
    if not all(required_browser.values()):
        blockers.append("Browser assertions are incomplete")
    if hardcoding_scan.get("matches"):
        blockers.append("Reusable code contains project/source-specific literal matches")

    visual_score = float(visual.get("score") or 0)
    assessment_score = float(assessment.get("score") or 0)
    eval_score = float(eval_report.get("score") or 0)
    browser_score = 100.0 if all(required_browser.values()) else 0.0
    browser_ui_score = 100.0 if browser_ui_qa.get("accepted") and not browser_ui_blockers else 0.0
    export_score = 100.0 if export_qa.get("accepted") and not export_blockers else 0.0
    control_quality_score = 100.0 if control_quality.get("accepted") else 0.0
    hardcoding_score = 100.0 if not hardcoding_scan.get("matches") else 0.0
    packet_artifact_score = 100.0 if packet_artifact_audit.get("accepted") else 0.0
    judgement_agent_score = 100.0 if judgement_agent_audit.get("accepted") else 0.0
    code_repair_score = 100.0 if code_repair_audit.get("accepted") else 0.0
    code_repair_dispatch_score = 100.0 if code_repair_dispatch_audit.get("accepted") else 0.0
    code_repair_execution_score = 100.0 if code_repair_execution_audit.get("accepted") else 0.0
    repair_orchestration_score = 100.0 if repair_orchestration_audit.get("accepted") else 0.0
    score = round(
        min(
            value
            for value in (
                visual_score,
                assessment_score,
                eval_score,
                lowest_page_score or 0,
                browser_score,
                browser_ui_score,
                export_score,
                control_quality_score,
                hardcoding_score,
                packet_artifact_score,
                judgement_agent_score,
                code_repair_score,
                code_repair_dispatch_score,
                code_repair_execution_score,
                repair_orchestration_score,
            )
            if value is not None
        ),
        3,
    )
    accepted = bool(
        visual.get("accepted")
        and assessment.get("accepted")
        and page_packets.get("accepted")
        and eval_report.get("accepted")
        and browser_ui_qa.get("accepted")
        and export_qa.get("accepted")
        and control_quality.get("accepted")
        and score >= 95
        and not blockers
    )
    report = {
        "schema": "brochure-maker.exact-critic.v1",
        "project_id": project_id,
        "project_dir": str(project_path),
        "accepted": accepted,
        "score": score,
        "blockers": sorted(dict.fromkeys(blockers)),
        "rubric": {
            "visual_score": visual_score,
            "html_assessment_score": assessment_score,
            "eval_score": eval_score,
            "lowest_page_score": round(lowest_page_score, 3) if page_scores else None,
            "browser_score": browser_score,
            "browser_ui_score": browser_ui_score,
            "export_score": export_score,
            "control_quality_score": control_quality_score,
            "hardcoding_score": hardcoding_score,
            "page_packet_artifact_score": packet_artifact_score,
            "judgement_agent_score": judgement_agent_score,
            "code_repair_score": code_repair_score,
            "code_repair_dispatch_score": code_repair_dispatch_score,
            "code_repair_execution_score": code_repair_execution_score,
            "repair_orchestration_score": repair_orchestration_score,
        },
        "required_browser_assertions": required_browser,
        "browser_ui_qa": {
            "accepted": browser_ui_qa.get("accepted"),
            "confidence": browser_ui_qa.get("confidence"),
            "browser_surface": browser_ui_qa.get("browser_surface"),
            "required_assertions": {
                name: browser_assertion_passed(
                    browser_ui_qa.get("assertions") if isinstance(browser_ui_qa.get("assertions"), dict) else {},
                    name,
                )
                for name in REQUIRED_BROWSER_UI_ASSERTIONS
            },
        },
        "export_qa": {
            "accepted": export_qa.get("accepted"),
            "required_assertions": {
                name: browser_assertion_passed(
                    export_qa.get("assertions") if isinstance(export_qa.get("assertions"), dict) else {},
                    name,
                )
                for name in REQUIRED_EXPORT_ASSERTIONS
            },
        },
        "control_quality": {
            "accepted": control_quality.get("accepted"),
            "blockers": control_quality.get("blockers") or [],
        },
        "repair_loop": {
            "accepted": repair_loop.get("accepted"),
            "status": repair_loop.get("status"),
            "failed_page_count": repair_loop.get("failed_page_count"),
            "skipped_page_count": repair_loop.get("skipped_page_count"),
        },
        "hardcoding_scan": hardcoding_scan,
        "page_packet_artifact_audit": packet_artifact_audit,
        "judgement_agent_audit": judgement_agent_audit,
        "page_critique_audit": critique_audit,
        "code_repair_audit": code_repair_audit,
        "code_repair_dispatch_audit": code_repair_dispatch_audit,
        "code_repair_execution_audit": code_repair_execution_audit,
        "repair_orchestration_audit": repair_orchestration_audit,
        "provenance": {
            "fresh_upload_present": bool(fresh_upload),
            "fresh_upload_project_id": fresh_upload.get("project_id") if isinstance(fresh_upload, dict) else None,
        },
        "critic_prompt": (
            "Review the benchmark run, page packets, Browser QA, clean export, and reusable-code scan. "
            "Accept only if every page is >=95, required Browser assertions pass, export is clean, "
            "and reusable code has no project-specific copied output or literal coordinate hacks."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "critic-report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def audit_repair_orchestration(
    repair_orchestration: dict[str, Any],
    repair_loop: dict[str, Any],
) -> dict[str, Any]:
    """Verify page repair orchestration evidence exists and matches the repair loop."""
    blockers = _repair_orchestration_blockers(repair_orchestration, repair_loop)
    return {
        "schema": "brochure-maker.repair-orchestration-audit.v1",
        "accepted": not blockers,
        "blockers": blockers,
        "status": repair_orchestration.get("status"),
        "attempt_count": repair_orchestration.get("attempt_count"),
        "escalation_count": repair_orchestration.get("escalation_count"),
        "repair_scope": (repair_orchestration.get("scope_guard") or {}).get("repair_scope"),
    }


def _repair_orchestration_blockers(
    repair_orchestration: dict[str, Any],
    repair_loop: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if not repair_orchestration:
        return ["Missing repair-orchestration artifact"]
    if repair_loop.get("accepted") and repair_orchestration.get("accepted") is not True:
        blockers.append("Repair orchestration did not accept an accepted repair loop")
    scope_guard = repair_orchestration.get("scope_guard") if isinstance(repair_orchestration.get("scope_guard"), dict) else {}
    if scope_guard.get("repair_scope") not in {"queued failed pages only", "failed pages only"}:
        blockers.append("Repair orchestration does not declare page-scoped failed-page repair")
    if repair_loop.get("failed_page_count") and repair_orchestration.get("attempt_count") in {None, 0}:
        blockers.append("Repair orchestration did not attempt queued failed pages")
    return sorted(dict.fromkeys(blockers))


def audit_code_repair_execution(code_repair_execution: dict[str, Any]) -> dict[str, Any]:
    """Verify required code repairs have accepted worker execution receipts."""
    blockers: list[str] = []
    if not code_repair_execution:
        blockers.append("Missing code repair execution artifact")
        return {
            "schema": "brochure-maker.code-repair-execution-audit.v1",
            "accepted": False,
            "blockers": blockers,
            "required_execution_count": None,
        }
    if code_repair_execution.get("accepted") is not True:
        blockers.extend(str(blocker) for blocker in code_repair_execution.get("blockers") or [])
    return {
        "schema": "brochure-maker.code-repair-execution-audit.v1",
        "accepted": not blockers,
        "blockers": sorted(dict.fromkeys(blockers)),
        "required_execution_count": code_repair_execution.get("required_execution_count"),
        "required_accepted_count": code_repair_execution.get("required_accepted_count"),
        "optional_execution_count": code_repair_execution.get("optional_execution_count"),
    }


def audit_code_repair_dispatch(
    code_repair_dispatch: dict[str, Any],
    code_repair_tasks: dict[str, Any],
) -> dict[str, Any]:
    """Verify worker briefs exist for every generated code repair task."""
    blockers: list[str] = []
    if not code_repair_dispatch:
        blockers.append("Missing code repair dispatch artifact")
        return {
            "schema": "brochure-maker.code-repair-dispatch-audit.v1",
            "accepted": False,
            "blockers": blockers,
            "dispatched_task_count": 0,
        }
    if code_repair_dispatch.get("accepted") is not True:
        blockers.extend(str(blocker) for blocker in code_repair_dispatch.get("blockers") or [])
    expected_count = int(code_repair_tasks.get("task_count") or 0) if code_repair_tasks else 0
    dispatched_count = int(code_repair_dispatch.get("dispatched_task_count") or 0)
    if dispatched_count < expected_count:
        blockers.append(f"Code repair dispatch has {dispatched_count} worker briefs for {expected_count} tasks")
    for brief in code_repair_dispatch.get("worker_briefs") or []:
        if not isinstance(brief, dict):
            continue
        for key in ("worker_brief", "worker_prompt", "evidence_manifest"):
            path = brief.get(key)
            if not path or not Path(str(path)).expanduser().exists():
                blockers.append(f"Code repair dispatch task {brief.get('task_id') or 'unknown'} missing {key}")
    return {
        "schema": "brochure-maker.code-repair-dispatch-audit.v1",
        "accepted": not blockers,
        "blockers": sorted(dict.fromkeys(blockers)),
        "dispatched_task_count": code_repair_dispatch.get("dispatched_task_count"),
        "required_dispatch_count": code_repair_dispatch.get("required_dispatch_count"),
        "optional_dispatch_count": code_repair_dispatch.get("optional_dispatch_count"),
    }


def audit_code_repair_tasks(code_repair_tasks: dict[str, Any]) -> dict[str, Any]:
    """Verify the run has a usable reusable-code repair task artifact."""
    blockers: list[str] = []
    if not code_repair_tasks:
        blockers.append("Missing code repair task artifact")
        return {
            "schema": "brochure-maker.code-repair-audit.v1",
            "accepted": False,
            "blockers": blockers,
            "task_count": 0,
            "required_task_count": None,
        }
    if code_repair_tasks.get("accepted") is not True:
        blockers.append("Code repair task artifact has required repair tasks or blockers")
    tasks = code_repair_tasks.get("tasks") if isinstance(code_repair_tasks.get("tasks"), list) else []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if not task.get("worker_prompt"):
            blockers.append(f"Code repair task {task.get('id') or 'unknown'} has no worker prompt")
        if task.get("required_for_acceptance") and not task.get("suggested_files"):
            blockers.append(f"Required code repair task {task.get('id') or 'unknown'} has no suggested files")
    return {
        "schema": "brochure-maker.code-repair-audit.v1",
        "accepted": not blockers,
        "blockers": sorted(dict.fromkeys(blockers)),
        "task_count": code_repair_tasks.get("task_count"),
        "required_task_count": code_repair_tasks.get("required_task_count"),
        "optional_task_count": code_repair_tasks.get("optional_task_count"),
    }


REQUIRED_PAGE_PACKET_FILES = (
    "source.png",
    "generated.png",
    "diff.png",
    "design-page.json",
    "inventory-page.json",
    "browser-page-qa.json",
    "page-assessment.json",
    "page-score.json",
    "page-critique.json",
    "repair-plan.json",
    "page-repair-patch.json",
    "repair-attempts.json",
    "pdf-text-spans.json",
    "pdf-fonts.json",
    "pdf-images.json",
    "image-regions.json",
    "pdf-vectors.json",
    "raster-components.json",
    "html-text-spans.json",
    "text-reconstruction.json",
    *AGENT_OUTPUT_FILES,
)


def audit_page_packet_artifacts(page_packets: dict[str, Any]) -> dict[str, Any]:
    """Verify every page packet has the evidence files needed by page agents."""
    packet_entries = page_packets.get("packets") if isinstance(page_packets.get("packets"), list) else []
    blockers: list[str] = []
    pages: list[dict[str, Any]] = []
    for packet in packet_entries:
        if not isinstance(packet, dict):
            continue
        page_number = int(packet.get("page_number") or 0)
        raw_packet_dir = str(packet.get("packet_dir") or "")
        if not raw_packet_dir:
            blockers.append(f"Page {page_number} is missing packet_dir")
            pages.append({"page_number": page_number, "packet_dir": "", "missing": list(REQUIRED_PAGE_PACKET_FILES)})
            continue
        packet_dir = Path(raw_packet_dir).expanduser()
        missing = [
            filename
            for filename in REQUIRED_PAGE_PACKET_FILES
            if not (packet_dir / filename).exists()
        ]
        if missing:
            blockers.append(f"Page {page_number} packet is missing required artifacts: {', '.join(missing)}")
        pages.append(
            {
                "page_number": page_number,
                "packet_dir": str(packet_dir),
                "missing": missing,
                "complete": not missing,
            }
        )
    if not packet_entries:
        blockers.append("No page packets available for artifact audit")
    return {
        "schema": "brochure-maker.page-packet-artifact-audit.v1",
        "accepted": not blockers,
        "required_files": list(REQUIRED_PAGE_PACKET_FILES),
        "page_count": len(pages),
        "blockers": sorted(dict.fromkeys(blockers)),
        "pages": pages,
    }


def audit_judgement_agents(page_packets: dict[str, Any]) -> dict[str, Any]:
    """Verify named page judgement agents exist and accepted pages pass them."""
    packet_entries = page_packets.get("packets") if isinstance(page_packets.get("packets"), list) else []
    pages: list[dict[str, Any]] = []
    blockers: list[str] = []
    for packet in packet_entries:
        if not isinstance(packet, dict):
            continue
        page_number = int(packet.get("page_number") or 0)
        packet_dir = Path(str(packet.get("packet_dir") or "")).expanduser()
        summary = packet.get("judgement_agents") if isinstance(packet.get("judgement_agents"), dict) else {}
        artifact_map = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
        page_agents: list[dict[str, Any]] = []
        missing: list[str] = []
        rejected: list[str] = []
        for filename in AGENT_OUTPUT_FILES:
            raw_path = artifact_map.get(filename) or (packet_dir / filename if packet_dir else "")
            path = Path(str(raw_path)).expanduser() if raw_path else Path("")
            exists = bool(raw_path) and path.exists()
            payload = _load_json(path) if exists else {}
            accepted = bool(payload.get("accepted")) if payload else False
            agent_name = str(payload.get("agent") or filename.removesuffix(".json"))
            page_agents.append(
                {
                    "agent": agent_name,
                    "artifact": str(path) if raw_path else "",
                    "exists": exists,
                    "accepted": accepted,
                    "status": payload.get("status"),
                }
            )
            if not exists:
                missing.append(filename)
            elif packet.get("accepted") and not accepted:
                rejected.append(agent_name)
        if missing:
            blockers.append(f"Page {page_number} is missing judgement agent artifacts: {', '.join(missing)}")
        if rejected:
            blockers.append(f"Page {page_number} passed numerically but judgement agents rejected it: {', '.join(rejected)}")
        pages.append(
            {
                "page_number": page_number,
                "packet_accepted": bool(packet.get("accepted")),
                "missing": missing,
                "rejected_on_accepted_page": rejected,
                "agents": page_agents,
            }
        )
    if not packet_entries:
        blockers.append("No page packets available for judgement-agent audit")
    return {
        "schema": "brochure-maker.judgement-agent-audit.v1",
        "accepted": not blockers,
        "required_agents": list(AGENT_OUTPUT_FILES),
        "page_count": len(pages),
        "blockers": sorted(dict.fromkeys(blockers)),
        "pages": pages,
    }


def audit_page_critiques(page_packets: dict[str, Any]) -> dict[str, Any]:
    """Verify every page packet has an inspectable page critique mark-sheet."""
    packet_entries = page_packets.get("packets") if isinstance(page_packets.get("packets"), list) else []
    pages: list[dict[str, Any]] = []
    blockers: list[str] = []
    for packet in packet_entries:
        if not isinstance(packet, dict):
            continue
        page_number = int(packet.get("page_number") or 0)
        critique_path = packet.get("critique")
        page_audit = {
            "page_number": page_number,
            "critique": str(critique_path or ""),
            "exists": False,
            "has_mark_sheet": False,
            "accepted_matches_packet": False,
        }
        if not critique_path:
            blockers.append(f"Page {page_number} is missing a page critique path")
            pages.append(page_audit)
            continue
        path = Path(str(critique_path)).expanduser()
        page_audit["exists"] = path.exists()
        if not path.exists():
            blockers.append(f"Page {page_number} page critique file is missing")
            pages.append(page_audit)
            continue
        critique = _load_json(path)
        mark_sheet = critique.get("rubric") or critique.get("html_mark_sheet")
        page_audit["has_mark_sheet"] = bool(mark_sheet)
        page_audit["accepted_matches_packet"] = bool(critique.get("accepted")) == bool(packet.get("accepted"))
        if not page_audit["has_mark_sheet"]:
            blockers.append(f"Page {page_number} page critique has no mark sheet")
        if not page_audit["accepted_matches_packet"]:
            blockers.append(f"Page {page_number} page critique acceptance does not match the packet")
        pages.append(page_audit)
    if not packet_entries:
        blockers.append("No page packets available for page critique audit")
    return {
        "schema": "brochure-maker.page-critique-audit.v1",
        "accepted": not blockers,
        "page_count": len(pages),
        "blockers": sorted(dict.fromkeys(blockers)),
        "pages": pages,
    }


def scan_reusable_code_for_project_literals(
    project_path: Path,
    project_id: str,
    *,
    fresh_upload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find obvious project-specific literals in production extraction code."""
    repo_root = project_path.parent.parent if project_path.parent.name == "projects" else Path.cwd()
    exact_metadata = _load_json(project_path / "exact_metadata.json")
    source_terms = _hardcoding_terms(project_id, exact_metadata, fresh_upload)
    matches: list[dict[str, Any]] = []
    for source_file in _production_python_files(repo_root):
        try:
            text = source_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for term in source_terms:
                if _line_has_project_literal(line, term):
                    matches.append(
                        {
                            "file": str(source_file),
                            "line": line_number,
                            "term": term,
                            "snippet": line.strip()[:180],
                        }
                    )
    return {
        "schema": "brochure-maker.hardcoding-scan.v1",
        "roots": [str(repo_root / root) for root in PRODUCTION_SCAN_ROOTS],
        "terms": source_terms,
        "match_count": len(matches),
        "matches": matches,
    }


def _line_has_project_literal(line: str, term: str) -> bool:
    """Return true for real copied literals, not generic substrings.

    Source names often contain ordinary brochure words such as "final",
    "house", or "anchor". Those should not match code identifiers like
    ``finally``, ``warehouse``, or map/text anchor variables.
    """
    needle = str(term or "").strip()
    if not needle:
        return False
    if re.fullmatch(r"[A-Za-z0-9]+", needle):
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])", line, re.IGNORECASE) is not None
    return needle in line


def _production_python_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_SCAN_ROOTS:
        path = repo_root / root
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*.py")
                if "__pycache__" not in candidate.parts
            )
    return sorted(files)


def _hardcoding_terms(project_id: str, metadata: dict[str, Any], fresh_upload: dict[str, Any] | None) -> list[str]:
    raw_values = [
        project_id,
        metadata.get("filename"),
        metadata.get("brochure_name"),
    ]
    if isinstance(fresh_upload, dict):
        raw_values.extend([fresh_upload.get("project_id"), fresh_upload.get("brochure_name")])
    terms: set[str] = set()
    for value in raw_values:
        if not value:
            continue
        text = str(value)
        if _generic_hardcoding_source_name(text):
            continue
        if len(text) >= 6:
            terms.add(text)
        for token in re.split(r"[^A-Za-z0-9]+", text):
            token = token.strip()
            token_lower = token.lower()
            if len(token) < 5 or token_lower in TERM_STOPWORDS or token_lower in GENERIC_HARDCODING_TOKENS:
                continue
            terms.add(token)
    return sorted(terms, key=lambda item: (len(item), item.lower()), reverse=True)


def _generic_hardcoding_source_name(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", str(value).lower())
    return compact in {"source", "sourcepdf", "brochure", "brochurepdf"}


def upload_exact_pdf(pdf_path: str | Path, *, base_url: str = "http://127.0.0.1:8000") -> dict[str, Any]:
    """Upload a PDF through the real exact endpoint and return its JSON response."""
    source = Path(pdf_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".pdf":
        raise ValueError("Exact benchmark uploads require a PDF file.")

    boundary = f"----brochure-maker-{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(source.name)[0] or "application/pdf"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="file"; filename="{source.name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8"),
            source.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/upload/exact",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=EXACT_UPLOAD_TIMEOUT_SECONDS) as response:
            data = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Exact upload failed for {source}: {exc}") from exc
    payload = json.loads(data)
    if not isinstance(payload, dict) or not payload.get("project_id"):
        raise RuntimeError(f"Exact upload did not return a project_id: {data}")
    return payload


def browser_assertion_passed(assertions: dict[str, Any], name: str) -> bool:
    """Return true when the generic Browser gate or a legacy alias passed."""
    aliases = BROWSER_ASSERTION_ALIASES.get(name, (name,))
    return any(assertions.get(alias) is True for alias in aliases)


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the exact PDF benchmark pipeline.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", help="PDF to fresh-upload through /api/upload/exact")
    source.add_argument("--project-dir", help="Existing exact project directory to re-evaluate")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--projects-dir", default="projects")
    parser.add_argument("--output-root", default="evals/reports")
    parser.add_argument("--page-limit", type=int, default=None)
    args = parser.parse_args()

    report = run_exact_benchmark(
        pdf_path=args.pdf,
        project_dir=args.project_dir,
        base_url=args.base_url,
        projects_dir=args.projects_dir,
        output_root=args.output_root,
        page_limit=args.page_limit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
