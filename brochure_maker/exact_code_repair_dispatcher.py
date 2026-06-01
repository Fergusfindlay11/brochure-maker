"""Materialize reusable exact-pipeline code repair tasks as worker briefs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_code_repair_dispatch(
    project_dir: str | Path,
    *,
    code_repair_tasks_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    include_optional: bool = True,
    max_tasks: int | None = None,
) -> dict[str, Any]:
    """Create per-task worker packets from ``code-repair-tasks.json``."""
    project_path = Path(project_dir).expanduser().resolve()
    repo_root = project_path.parent.parent if project_path.parent.name == "projects" else Path.cwd()
    tasks_path = (
        Path(code_repair_tasks_path).expanduser().resolve()
        if code_repair_tasks_path
        else repo_root / "evals" / "reports" / project_path.name / "code-repair-tasks.json"
    )
    output_root = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else tasks_path.parent / "code-repair-workers"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    task_report = _load_json(tasks_path)
    tasks = task_report.get("tasks") if isinstance(task_report.get("tasks"), list) else []
    selected_tasks = [
        task
        for task in tasks
        if isinstance(task, dict) and (include_optional or task.get("required_for_acceptance"))
    ]
    if max_tasks is not None:
        selected_tasks = selected_tasks[:max_tasks]

    worker_briefs: list[dict[str, Any]] = []
    blockers: list[str] = []
    for task in selected_tasks:
        task_id = str(task.get("id") or "unknown-task")
        task_dir = output_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        evidence_manifest = _evidence_manifest(task)
        brief = _worker_brief(project_path, tasks_path, task, task_dir, evidence_manifest)
        _write_json(task_dir / "evidence-manifest.json", evidence_manifest)
        _write_json(task_dir / "worker-brief.json", brief)
        (task_dir / "worker-prompt.md").write_text(_worker_prompt_markdown(brief), encoding="utf-8")
        worker_briefs.append(
            {
                "task_id": task_id,
                "required_for_acceptance": bool(task.get("required_for_acceptance")),
                "status": brief["status"],
                "worker_dir": str(task_dir),
                "worker_brief": str(task_dir / "worker-brief.json"),
                "worker_prompt": str(task_dir / "worker-prompt.md"),
                "evidence_manifest": str(task_dir / "evidence-manifest.json"),
            }
        )

    if not task_report:
        blockers.append("No code repair task report available for dispatch")
    required_count = sum(1 for brief in worker_briefs if brief["required_for_acceptance"])
    optional_count = len(worker_briefs) - required_count
    accepted = bool(task_report) and not blockers
    report = {
        "schema": "brochure-maker.exact-code-repair-dispatch.v1",
        "project_id": project_path.name,
        "project_dir": str(project_path),
        "code_repair_tasks": str(tasks_path),
        "dispatch_root": str(output_root),
        "accepted": accepted,
        "status": (
            "required-worker-briefs-ready"
            if required_count
            else "accepted-optional-worker-briefs" if worker_briefs else "accepted-no-worker-briefs"
        ),
        "include_optional": include_optional,
        "dispatched_task_count": len(worker_briefs),
        "required_dispatch_count": required_count,
        "optional_dispatch_count": optional_count,
        "blockers": sorted(dict.fromkeys(blockers)),
        "worker_briefs": worker_briefs,
        "contract": {
            "worker_scope": "one task packet at a time",
            "dispatch_scope": "materialize worker briefs only; required task implementation is proven by code-repair-execution.json",
            "write_scope": "only suggested reusable files unless the worker brief explains why another reusable file is required",
            "verification_order": [
                "run focused tests for the changed subsystem",
                "rerun affected page repair/eval",
                "rerun full benchmark matrix",
                "verify in Browser",
            ],
            "no_hardcoding": "do not copy existing project HTML/state or hardcode brochure/project coordinates",
        },
    }
    _write_json(output_root.parent / "code-repair-dispatch.json", report)
    return report


def write_code_repair_dispatch(
    project_dir: str | Path,
    *,
    code_repair_tasks_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    include_optional: bool = True,
    max_tasks: int | None = None,
) -> Path:
    """Write code-repair worker briefs and return the dispatch report path."""
    report = build_code_repair_dispatch(
        project_dir,
        code_repair_tasks_path=code_repair_tasks_path,
        output_dir=output_dir,
        include_optional=include_optional,
        max_tasks=max_tasks,
    )
    return Path(report["dispatch_root"]).parent / "code-repair-dispatch.json"


def _worker_brief(
    project_path: Path,
    tasks_path: Path,
    task: dict[str, Any],
    task_dir: Path,
    evidence_manifest: dict[str, Any],
) -> dict[str, Any]:
    required = bool(task.get("required_for_acceptance"))
    return {
        "schema": "brochure-maker.exact-code-repair-worker-brief.v1",
        "project_id": project_path.name,
        "project_dir": str(project_path),
        "task_id": task.get("id"),
        "subsystem": task.get("subsystem"),
        "root_subsystem": task.get("root_subsystem"),
        "status": "required-worker-ready" if required else "optional-worker-ready",
        "required_for_acceptance": required,
        "severity": task.get("severity"),
        "pages": task.get("pages") or [],
        "issues": task.get("issues") or [],
        "roles": task.get("roles") or [],
        "suggested_files": task.get("suggested_files") or [],
        "acceptance_checks": task.get("acceptance_checks") or [],
        "hardcoding_rules": task.get("hardcoding_rules") or [],
        "worker_prompt": task.get("worker_prompt"),
        "source_task_report": str(tasks_path),
        "worker_dir": str(task_dir),
        "evidence_manifest": evidence_manifest,
        "expected_worker_output": {
            "summary": "what reusable detector/renderer/export behavior changed",
            "files_changed": "list of reusable code files touched",
            "tests_run": "focused tests plus benchmark/page reruns",
            "browser_evidence": "in-app Browser editor/export checks for the affected feature",
            "hardcoding_statement": "why the repair is reusable and not project-specific",
        },
    }


def _evidence_manifest(task: dict[str, Any]) -> dict[str, Any]:
    evidence_items = task.get("evidence") if isinstance(task.get("evidence"), list) else []
    manifest_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or "")).expanduser()
        key = str(path)
        if not key or key in seen:
            continue
        seen.add(key)
        manifest_items.append(
            {
                "page_number": item.get("page_number"),
                "kind": item.get("kind"),
                "path": str(path),
                "exists": path.exists(),
            }
        )
    return {
        "schema": "brochure-maker.exact-code-repair-evidence-manifest.v1",
        "task_id": task.get("id"),
        "items": manifest_items,
        "missing": [item for item in manifest_items if not item["exists"]],
    }


def _worker_prompt_markdown(brief: dict[str, Any]) -> str:
    lines = [
        f"# Code Repair Task: {brief.get('task_id')}",
        "",
        str(brief.get("worker_prompt") or ""),
        "",
        "## Suggested Files",
        *[f"- `{path}`" for path in brief.get("suggested_files") or []],
        "",
        "## Evidence",
        *[f"- {item.get('kind')}: `{item.get('path')}`" for item in (brief.get("evidence_manifest") or {}).get("items", [])],
        "",
        "## Acceptance Checks",
        *[f"- {check}" for check in brief.get("acceptance_checks") or []],
        "",
        "## Hardcoding Rules",
        *[f"- {rule}" for rule in brief.get("hardcoding_rules") or []],
    ]
    return "\n".join(lines).strip() + "\n"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create worker briefs from exact code-repair tasks.")
    parser.add_argument("project_dir")
    parser.add_argument("--code-repair-tasks", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--required-only", action="store_true")
    parser.add_argument("--max-tasks", type=int, default=None)
    args = parser.parse_args()
    path = write_code_repair_dispatch(
        args.project_dir,
        code_repair_tasks_path=args.code_repair_tasks,
        output_dir=args.output_dir,
        include_optional=not args.required_only,
        max_tasks=args.max_tasks,
    )
    print(path)


if __name__ == "__main__":
    main()
