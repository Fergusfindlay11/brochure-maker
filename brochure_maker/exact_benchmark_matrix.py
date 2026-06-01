"""Aggregate exact PDF benchmark runs into a multi-PDF acceptance matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from brochure_maker.exact_benchmark import (
    REQUIRED_BROWSER_ASSERTIONS,
    browser_assertion_passed,
    run_exact_benchmark,
)
from brochure_maker.exact_browser_ui_qa import REQUIRED_BROWSER_UI_ASSERTIONS
from brochure_maker.exact_export_qa import REQUIRED_EXPORT_ASSERTIONS


DEFAULT_SCORE_FLOOR = 95.0
DEFAULT_PAGE_FLOOR = 95.0


def run_benchmark_matrix(
    manifest_path: str | Path,
    *,
    output_path: str | Path = "evals/reports/benchmark-matrix.json",
    base_url: str = "http://127.0.0.1:8000",
    projects_dir: str | Path = "projects",
    output_root: str | Path = "evals/reports",
    rerun: bool = True,
    page_limit: int | None = None,
) -> dict[str, Any]:
    """Run or read benchmark reports and write a matrix-level verdict."""
    manifest_file = Path(manifest_path).expanduser().resolve()
    manifest = _load_json(manifest_file)
    entries = manifest.get("benchmarks") if isinstance(manifest.get("benchmarks"), list) else []
    matrix_entries: list[dict[str, Any]] = []
    blockers: list[str] = []

    for index, raw_entry in enumerate(entries, start=1):
        if not isinstance(raw_entry, dict) or raw_entry.get("enabled", True) is False:
            continue
        entry_id = str(raw_entry.get("id") or f"benchmark-{index:03d}")
        entry_report = _run_or_load_entry(
            raw_entry,
            base_url=base_url,
            projects_dir=projects_dir,
            output_root=output_root,
            rerun=rerun,
            page_limit=page_limit,
        )
        page_packets = _load_json(_artifact_path(entry_report, "page_packets"))
        repair_loop = _load_json(_artifact_path(entry_report, "repair_loop"))
        repair_orchestration = _load_json(_artifact_path(entry_report, "repair_orchestration"))
        code_repair_tasks = _load_json(_artifact_path(entry_report, "code_repair_tasks"))
        code_repair_dispatch = _load_json(_artifact_path(entry_report, "code_repair_dispatch"))
        code_repair_execution = _load_json(_artifact_path(entry_report, "code_repair_execution"))
        matrix_entry = _score_matrix_entry(
            entry_id,
            raw_entry,
            entry_report,
            page_packets,
            repair_loop,
            repair_orchestration,
            code_repair_tasks,
            code_repair_dispatch,
            code_repair_execution,
        )
        matrix_entries.append(matrix_entry)
        blockers.extend(f"{entry_id}: {blocker}" for blocker in matrix_entry.get("blockers") or [])

    if not matrix_entries:
        blockers.append("Benchmark manifest contains no enabled benchmarks")

    accepted_entries = [entry for entry in matrix_entries if entry.get("accepted")]
    lowest_pages = [
        entry.get("lowest_page")
        for entry in matrix_entries
        if isinstance(entry.get("lowest_page"), dict)
    ]
    lowest_page_score = min(
        (float(page.get("score") or 0) for page in lowest_pages),
        default=None,
    )
    visual_scores = [
        float(entry.get("scores", {}).get("visual") or 0)
        for entry in matrix_entries
        if isinstance(entry.get("scores"), dict)
    ]
    matrix = {
        "schema": "brochure-maker.exact-benchmark-matrix.v1",
        "manifest": str(manifest_file),
        "accepted": bool(matrix_entries) and len(accepted_entries) == len(matrix_entries) and not blockers,
        "benchmark_count": len(matrix_entries),
        "accepted_count": len(accepted_entries),
        "failed_count": len(matrix_entries) - len(accepted_entries),
        "blockers": sorted(dict.fromkeys(blockers)),
        "required_browser_assertions": list(REQUIRED_BROWSER_ASSERTIONS),
        "required_browser_ui_assertions": list(REQUIRED_BROWSER_UI_ASSERTIONS),
        "required_export_assertions": list(REQUIRED_EXPORT_ASSERTIONS),
        "aggregate": {
            "lowest_page_score": round(lowest_page_score, 3) if lowest_page_score is not None else None,
            "average_visual_score": round(sum(visual_scores) / len(visual_scores), 3) if visual_scores else None,
        },
        "benchmarks": matrix_entries,
    }
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(matrix, indent=2, sort_keys=True), encoding="utf-8")
    latest = output.parent / "latest-matrix.json"
    if latest != output:
        latest.write_text(json.dumps(matrix, indent=2, sort_keys=True), encoding="utf-8")
    return matrix


def _run_or_load_entry(
    entry: dict[str, Any],
    *,
    base_url: str,
    projects_dir: str | Path,
    output_root: str | Path,
    rerun: bool,
    page_limit: int | None,
) -> dict[str, Any]:
    report_path = entry.get("report_path")
    if not rerun and report_path:
        return _load_json(Path(str(report_path)).expanduser())
    if entry.get("pdf"):
        return run_exact_benchmark(
            pdf_path=entry.get("pdf"),
            base_url=base_url,
            projects_dir=projects_dir,
            output_root=output_root,
            page_limit=page_limit,
        )
    if entry.get("project_dir"):
        return run_exact_benchmark(
            project_dir=entry.get("project_dir"),
            base_url=base_url,
            projects_dir=projects_dir,
            output_root=output_root,
            page_limit=page_limit,
        )
    if report_path:
        return _load_json(Path(str(report_path)).expanduser())
    raise ValueError(f"Benchmark entry {entry.get('id') or ''} must provide pdf, project_dir, or report_path.")


def _score_matrix_entry(
    entry_id: str,
    manifest_entry: dict[str, Any],
    report: dict[str, Any],
    page_packets: dict[str, Any],
    repair_loop: dict[str, Any],
    repair_orchestration: dict[str, Any],
    code_repair_tasks: dict[str, Any],
    code_repair_dispatch: dict[str, Any],
    code_repair_execution: dict[str, Any],
) -> dict[str, Any]:
    score_floor = float(manifest_entry.get("score_floor") or DEFAULT_SCORE_FLOOR)
    page_floor = float(manifest_entry.get("page_floor") or DEFAULT_PAGE_FLOOR)
    require_fresh_upload = bool(manifest_entry.get("require_fresh_upload"))
    blockers = [str(blocker) for blocker in report.get("blockers") or []]
    scores = report.get("scores") if isinstance(report.get("scores"), dict) else {}
    browser_assertions = report.get("browser_assertions") if isinstance(report.get("browser_assertions"), dict) else {}
    browser_ui_assertions = _report_assertions(report, "browser_ui_assertions", "browser_ui_qa")
    export_assertions = _report_assertions(report, "export_assertions", "export_qa")
    required_assertions = report.get("required_browser_assertions") or list(REQUIRED_BROWSER_ASSERTIONS)
    required_ui_assertions = report.get("required_browser_ui_assertions") or list(REQUIRED_BROWSER_UI_ASSERTIONS)
    required_export_assertions = report.get("required_export_assertions") or list(REQUIRED_EXPORT_ASSERTIONS)

    for score_name in ("visual", "html_assessment", "critic"):
        score = float(scores.get(score_name) or 0)
        if score < score_floor:
            blockers.append(f"{score_name} score {score:.3f} is below floor {score_floor:.3f}")

    for assertion in required_assertions:
        if not browser_assertion_passed(browser_assertions, assertion):
            blockers.append(f"Missing or false Browser assertion: {assertion}")
    for assertion in required_ui_assertions:
        if not browser_assertion_passed(browser_ui_assertions, assertion):
            blockers.append(f"Missing or false Browser UI assertion: {assertion}")
    for assertion in required_export_assertions:
        if not browser_assertion_passed(export_assertions, assertion):
            blockers.append(f"Missing or false export assertion: {assertion}")

    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), dict) else {}
    for artifact_name in ("browser_ui_qa", "export_qa", "control_quality"):
        if not artifacts.get(artifact_name):
            blockers.append(f"Benchmark is missing {artifact_name} artifact")
    control_quality = report.get("control_quality") if isinstance(report.get("control_quality"), dict) else {}
    if control_quality and control_quality.get("accepted") is not True:
        blockers.append("Control quality audit was not accepted")

    if require_fresh_upload and (report.get("source_mode") != "fresh-upload" or not report.get("fresh_upload")):
        blockers.append("Benchmark is required to prove fresh-upload provenance")
    if not repair_loop:
        blockers.append("Benchmark is missing repair-loop artifact")
    elif repair_loop.get("accepted") is not True:
        blockers.append("Repair loop was not accepted")
    if not repair_orchestration:
        blockers.append("Benchmark is missing repair-orchestration artifact")
    elif repair_loop.get("accepted") and repair_orchestration.get("accepted") is not True:
        blockers.append("Repair orchestration was not accepted")
    if not code_repair_tasks:
        blockers.append("Benchmark is missing code-repair-tasks artifact")
    elif code_repair_tasks.get("accepted") is not True:
        blockers.append("Code repair task artifact was not accepted")
    if not code_repair_dispatch:
        blockers.append("Benchmark is missing code-repair-dispatch artifact")
    elif code_repair_dispatch.get("accepted") is not True:
        blockers.append("Code repair dispatch artifact was not accepted")
    if not code_repair_execution:
        blockers.append("Benchmark is missing code-repair-execution artifact")
    elif code_repair_execution.get("accepted") is not True:
        blockers.append("Code repair execution artifact was not accepted")

    packet_entries = page_packets.get("packets") if isinstance(page_packets.get("packets"), list) else []
    page_scores = [
        {
            "page_number": int(packet.get("page_number") or 0),
            "score": float(packet.get("score") or 0),
            "accepted": bool(packet.get("accepted")),
        }
        for packet in packet_entries
        if isinstance(packet, dict)
    ]
    low_pages = [page for page in page_scores if page["score"] < page_floor]
    failed_packet_pages = [page for page in page_scores if page["score"] >= page_floor and not page["accepted"]]
    for page in low_pages:
        blockers.append(f"Page {page['page_number']} score {page['score']:.3f} is below floor {page_floor:.3f}")
    for page in failed_packet_pages:
        blockers.append(f"Page {page['page_number']} page packet was not accepted despite score {page['score']:.3f}")
    lowest_page = min(page_scores, key=lambda page: page["score"], default=None)

    accepted = bool(report.get("accepted")) and not blockers and bool(page_scores)
    if not report.get("accepted"):
        blockers.append("Benchmark run report was not accepted")
    if not page_scores:
        blockers.append("Benchmark has no page packet scores")

    return {
        "id": entry_id,
        "label": manifest_entry.get("label") or entry_id,
        "project_id": report.get("project_id"),
        "project_dir": report.get("project_dir"),
        "source_mode": report.get("source_mode"),
        "fresh_upload": report.get("fresh_upload"),
        "accepted": accepted and not blockers,
        "blockers": sorted(dict.fromkeys(blockers)),
        "scores": scores,
        "score_floor": score_floor,
        "page_floor": page_floor,
        "browser_assertions": browser_assertions,
        "browser_ui_assertions": browser_ui_assertions,
        "export_assertions": export_assertions,
        "required_browser_assertions": list(required_assertions),
        "required_browser_ui_assertions": list(required_ui_assertions),
        "required_export_assertions": list(required_export_assertions),
        "browser_ui_qa": report.get("browser_ui_qa") or {},
        "html_export": report.get("html_export") or {},
        "pdf_export": report.get("pdf_export") or {},
        "control_quality": control_quality,
        "failed_pages": report.get("failed_pages") or [],
        "lowest_page": lowest_page,
        "page_scores": page_scores,
        "artifacts": report.get("artifacts") or {},
        "next_repair_tasks": report.get("next_repair_tasks") or [],
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
    }


def _artifact_path(report: dict[str, Any], key: str) -> Path:
    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), dict) else {}
    return Path(str(artifacts.get(key) or ""))


def _report_assertions(report: dict[str, Any], top_key: str, nested_key: str) -> dict[str, Any]:
    top = report.get(top_key)
    if isinstance(top, dict):
        return top
    nested = report.get(nested_key)
    if isinstance(nested, dict) and isinstance(nested.get("assertions"), dict):
        return nested.get("assertions") or {}
    return {}


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or aggregate exact PDF benchmark matrix results.")
    parser.add_argument("--manifest", required=True, help="JSON manifest with benchmark entries")
    parser.add_argument("--output", default="evals/reports/benchmark-matrix.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--projects-dir", default="projects")
    parser.add_argument("--output-root", default="evals/reports")
    parser.add_argument("--page-limit", type=int, default=None)
    parser.add_argument("--no-rerun", action="store_true", help="Read report_path entries without rerunning benchmarks")
    args = parser.parse_args()
    matrix = run_benchmark_matrix(
        args.manifest,
        output_path=args.output,
        base_url=args.base_url,
        projects_dir=args.projects_dir,
        output_root=args.output_root,
        rerun=not args.no_rerun,
        page_limit=args.page_limit,
    )
    print(json.dumps(matrix, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
