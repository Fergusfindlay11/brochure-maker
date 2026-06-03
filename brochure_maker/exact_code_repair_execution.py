"""Audit implementation receipts for exact-pipeline code repair worker briefs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RESULT_TEMPLATE = {
    "schema": "brochure-maker.exact-code-repair-worker-result.v1",
    "status": "pending",
    "accepted": False,
    "summary": "",
    "files_changed": [],
    "tests_run": [],
    "browser_evidence": [],
    "benchmark_evidence": [],
    "hardcoding_statement": "",
    "post_repair_scores": {},
}


def build_code_repair_execution(
    project_dir: str | Path,
    *,
    dispatch_path: str | Path | None = None,
    output_path: str | Path | None = None,
    write_templates: bool = True,
) -> dict[str, Any]:
    """Build the run-level audit proving required worker repairs were executed."""
    project_path = Path(project_dir).expanduser().resolve()
    repo_root = project_path.parent.parent if project_path.parent.name == "projects" else Path.cwd()
    dispatch_file = (
        Path(dispatch_path).expanduser().resolve()
        if dispatch_path
        else repo_root / "evals" / "reports" / project_path.name / "code-repair-dispatch.json"
    )
    dispatch = _load_json(dispatch_file)
    worker_briefs = dispatch.get("worker_briefs") if isinstance(dispatch.get("worker_briefs"), list) else []
    blockers: list[str] = []
    executions: list[dict[str, Any]] = []

    for worker in worker_briefs:
        if not isinstance(worker, dict):
            continue
        worker_dir = Path(str(worker.get("worker_dir") or "")).expanduser()
        result_path = worker_dir / "worker-result.json"
        template_path = worker_dir / "worker-result.template.json"
        if write_templates and worker_dir:
            _write_template(template_path, worker)
        result = _load_json(result_path)
        required = bool(worker.get("required_for_acceptance"))
        audit = _audit_worker_result(worker, result_path, result)
        executions.append(audit)
        if required and not audit["accepted"]:
            blockers.extend(audit["blockers"] or [f"Required worker task {worker.get('task_id')} has not been accepted"])

    if not dispatch:
        blockers.append("No code repair dispatch artifact available for execution audit")
    required_total = sum(1 for worker in worker_briefs if isinstance(worker, dict) and worker.get("required_for_acceptance"))
    required_accepted = sum(1 for audit in executions if audit.get("required_for_acceptance") and audit.get("accepted"))
    accepted = bool(dispatch) and not blockers
    report = {
        "schema": "brochure-maker.exact-code-repair-execution.v1",
        "project_id": project_path.name,
        "project_dir": str(project_path),
        "dispatch": str(dispatch_file),
        "accepted": accepted,
        "status": "accepted-no-required-repairs" if accepted and required_total == 0 else "accepted-required-repairs" if accepted else "required-repairs-pending",
        "required_execution_count": required_total,
        "required_accepted_count": required_accepted,
        "optional_execution_count": len(executions) - required_total,
        "blockers": sorted(dict.fromkeys(blockers)),
        "executions": executions,
        "contract": {
            "required_worker_result": "required tasks need worker-result.json with accepted=true after code changes and verification",
            "optional_worker_result": "optional tasks may remain pending without blocking acceptance",
            "verification": [
                "focused tests for changed subsystem",
                "affected page repair/eval or benchmark rerun",
                "full benchmark matrix",
                "in-app Browser editor/export evidence",
                "hardcoding scan remains clean",
            ],
        },
    }
    output = Path(output_path).expanduser().resolve() if output_path else dispatch_file.parent / "code-repair-execution.json"
    _write_json(output, report)
    return report


def write_code_repair_execution(
    project_dir: str | Path,
    *,
    dispatch_path: str | Path | None = None,
    output_path: str | Path | None = None,
    write_templates: bool = True,
) -> Path:
    """Write the code repair execution audit and return its path."""
    report = build_code_repair_execution(
        project_dir,
        dispatch_path=dispatch_path,
        output_path=output_path,
        write_templates=write_templates,
    )
    if output_path:
        return Path(output_path).expanduser().resolve()
    return Path(report["dispatch"]).parent / "code-repair-execution.json"


def _audit_worker_result(worker: dict[str, Any], result_path: Path, result: dict[str, Any]) -> dict[str, Any]:
    task_id = str(worker.get("task_id") or "unknown-task")
    required = bool(worker.get("required_for_acceptance"))
    blockers: list[str] = []
    exists = result_path.exists()
    if not exists:
        if required:
            blockers.append(f"Required worker task {task_id} is missing worker-result.json")
        return {
            "task_id": task_id,
            "required_for_acceptance": required,
            "status": "missing-result" if required else "optional-not-run",
            "accepted": False if required else True,
            "result": str(result_path),
            "exists": False,
            "blockers": blockers,
        }
    if result.get("accepted") is not True:
        blockers.append(f"Worker task {task_id} result is not accepted")
    if required:
        if not result.get("files_changed"):
            blockers.append(f"Required worker task {task_id} has no files_changed evidence")
        if not result.get("tests_run"):
            blockers.append(f"Required worker task {task_id} has no tests_run evidence")
        if not result.get("browser_evidence"):
            blockers.append(f"Required worker task {task_id} has no Browser evidence")
        if not result.get("benchmark_evidence"):
            blockers.append(f"Required worker task {task_id} has no benchmark evidence")
        if not result.get("hardcoding_statement"):
            blockers.append(f"Required worker task {task_id} has no hardcoding statement")
    return {
        "task_id": task_id,
        "required_for_acceptance": required,
        "status": str(result.get("status") or "recorded"),
        "accepted": not blockers,
        "result": str(result_path),
        "exists": True,
        "summary": result.get("summary") or "",
        "files_changed": result.get("files_changed") or [],
        "tests_run": result.get("tests_run") or [],
        "browser_evidence": result.get("browser_evidence") or [],
        "benchmark_evidence": result.get("benchmark_evidence") or [],
        "blockers": blockers,
    }


def _write_template(path: Path, worker: dict[str, Any]) -> None:
    if path.exists():
        return
    template = {
        **RESULT_TEMPLATE,
        "task_id": worker.get("task_id"),
        "required_for_acceptance": bool(worker.get("required_for_acceptance")),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "worker_brief": worker.get("worker_brief"),
    }
    _write_json(path, template)


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
    parser = argparse.ArgumentParser(description="Audit exact code-repair worker execution receipts.")
    parser.add_argument("project_dir")
    parser.add_argument("--dispatch", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--no-templates", action="store_true")
    args = parser.parse_args()
    path = write_code_repair_execution(
        args.project_dir,
        dispatch_path=args.dispatch,
        output_path=args.output,
        write_templates=not args.no_templates,
    )
    print(path)


if __name__ == "__main__":
    main()
