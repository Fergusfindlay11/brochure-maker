"""Orchestrate failed-page exact-PDF repair attempts and escalation briefs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brochure_maker.exact_code_repair_tasks import HARDCODING_RULES, SUBSYSTEM_FILES
from brochure_maker.exact_page_repair import run_page_repair_attempt


def run_repair_orchestration(
    project_dir: str | Path,
    *,
    repair_loop_path: str | Path | None = None,
    output_path: str | Path | None = None,
    max_pages: int | None = None,
    base_url: str = "http://127.0.0.1:8000",
) -> dict[str, Any]:
    """Run page repair attempts for queued failures and write escalation briefs."""
    project_path = Path(project_dir).expanduser().resolve()
    repo_root = project_path.parent.parent if project_path.parent.name == "projects" else Path.cwd()
    loop_path = (
        Path(repair_loop_path).expanduser().resolve()
        if repair_loop_path
        else repo_root / "evals" / "reports" / project_path.name / "repair-loop.json"
    )
    output = Path(output_path).expanduser().resolve() if output_path else loop_path.parent / "repair-orchestration.json"
    started_at = datetime.now(timezone.utc).isoformat()
    starting_loop = _load_json(loop_path)
    initial_queue = _queue(starting_loop)
    attempted_pages: list[int] = []
    attempts: list[dict[str, Any]] = []
    escalations: list[dict[str, Any]] = []
    limit = max_pages if max_pages is not None else len(initial_queue)

    if starting_loop.get("accepted") or not initial_queue or limit == 0:
        report = _report(
            project_path,
            loop_path=loop_path,
            output=output,
            started_at=started_at,
            attempts=attempts,
            escalations=escalations,
            accepted=bool(starting_loop.get("accepted")),
            status="accepted" if starting_loop.get("accepted") else "no-queued-pages",
            final_loop=starting_loop,
        )
        _write_json(output, report)
        return report

    for item in initial_queue[:limit]:
        page_number = int(item.get("page_number") or 0)
        if page_number <= 0:
            continue
        current_loop = _load_json(loop_path)
        if _page_is_skipped(current_loop, page_number):
            continue
        attempt = run_page_repair_attempt(
            project_path,
            repair_loop_path=loop_path,
            page_number=page_number,
            base_url=base_url,
        )
        attempts.append(attempt)
        attempted_pages.append(page_number)
        if not attempt.get("accepted"):
            refreshed = _load_json(loop_path)
            refreshed_item = _queue_item(refreshed, page_number) or item
            escalation = build_escalation_brief(project_path, refreshed_item, attempt)
            escalations.append(escalation)
            packet_dir = Path(str(refreshed_item.get("packet_dir") or attempt.get("packet_dir") or "")).expanduser()
            if str(packet_dir):
                _write_json(packet_dir / "repair-escalation.json", escalation)

    final_loop = _load_json(loop_path)
    accepted = bool(final_loop.get("accepted"))
    status = "accepted" if accepted else "needs-reusable-code-repair"
    report = _report(
        project_path,
        loop_path=loop_path,
        output=output,
        started_at=started_at,
        attempts=attempts,
        escalations=escalations,
        accepted=accepted,
        status=status,
        final_loop=final_loop,
        attempted_pages=attempted_pages,
    )
    _write_json(output, report)
    return report


def build_escalation_brief(project_path: Path, queue_item: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]:
    """Create a reusable-code repair brief from a failed page attempt."""
    page_number = int(queue_item.get("page_number") or attempt.get("page_number") or 0)
    tasks = queue_item.get("tasks") if isinstance(queue_item.get("tasks"), list) else []
    subsystems = sorted(
        {
            str(task.get("repair_subsystem") or "").split("/", 1)[0]
            for task in tasks
            if isinstance(task, dict) and task.get("repair_subsystem")
        }
    )
    suggested_files = sorted({file for subsystem in subsystems for file in SUBSYSTEM_FILES.get(subsystem, [])})
    packet_dir = str(queue_item.get("packet_dir") or attempt.get("packet_dir") or "")
    prompt = (
        f"Inspect only page {page_number}'s packet and the suggested reusable pipeline files. "
        "Fix the extraction/rendering heuristic that caused the failed score; do not copy existing HTML, "
        "do not hardcode project ids or brochure-specific coordinates, and rerun the page repair attempt."
    )
    return {
        "schema": "brochure-maker.page-repair-escalation.v1",
        "project_id": project_path.name,
        "page_number": page_number,
        "status": "needs-reusable-code-repair",
        "score": attempt.get("score"),
        "previous_score": attempt.get("previous_score"),
        "packet_dir": packet_dir,
        "latest_attempt": attempt.get("artifacts", {}).get("attempt_dir"),
        "top_failures": queue_item.get("top_failures") if isinstance(queue_item.get("top_failures"), list) else [],
        "tasks": tasks,
        "subsystems": subsystems,
        "suggested_files": suggested_files,
        "agent_prompt": prompt,
        "hardcoding_rules": HARDCODING_RULES,
    }


def _report(
    project_path: Path,
    *,
    loop_path: Path,
    output: Path,
    started_at: str,
    attempts: list[dict[str, Any]],
    escalations: list[dict[str, Any]],
    accepted: bool,
    status: str,
    final_loop: dict[str, Any],
    attempted_pages: list[int] | None = None,
) -> dict[str, Any]:
    finished_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema": "brochure-maker.exact-repair-orchestration.v1",
        "project_id": project_path.name,
        "project_dir": str(project_path),
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "accepted": accepted,
        "repair_loop": str(loop_path),
        "output": str(output),
        "attempted_pages": attempted_pages or [],
        "attempt_count": len(attempts),
        "attempts": attempts,
        "escalation_count": len(escalations),
        "escalations": escalations,
        "final_repair_loop": {
            "accepted": final_loop.get("accepted"),
            "status": final_loop.get("status"),
            "failed_page_count": final_loop.get("failed_page_count"),
            "skipped_page_count": final_loop.get("skipped_page_count"),
            "blockers": final_loop.get("blockers") or [],
        },
        "scope_guard": {
            "fresh_upload_scope": "not rerun by orchestration",
            "repair_scope": "queued failed pages only",
            "passing_pages": "skipped unless the benchmark is rerun after a reusable code change",
        },
    }


def _queue(loop: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in loop.get("repair_queue") or [] if isinstance(item, dict)]


def _queue_item(loop: dict[str, Any], page_number: int) -> dict[str, Any] | None:
    for item in _queue(loop):
        if int(item.get("page_number") or 0) == page_number:
            return item
    return None


def _page_is_skipped(loop: dict[str, Any], page_number: int) -> bool:
    return any(
        isinstance(page, dict) and int(page.get("page_number") or 0) == page_number
        for page in loop.get("skipped_pages") or []
    )


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
    parser = argparse.ArgumentParser(description="Run exact-PDF queued page repair orchestration.")
    parser.add_argument("project_dir")
    parser.add_argument("--repair-loop", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    report = run_repair_orchestration(
        args.project_dir,
        repair_loop_path=args.repair_loop,
        output_path=args.output,
        max_pages=args.max_pages,
        base_url=args.base_url,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
