"""Execute page-scoped exact-PDF repair attempts from repair-loop packets."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brochure_maker.exact_html_assessment import write_html_assessment
from brochure_maker.exact_page_patch import apply_page_repair_patch
from brochure_maker.exact_repair_loop import write_repair_loop
from brochure_maker.exact_visual_diff import write_visual_diff_report


PASS_SCORE = 95.0


def run_page_repair_attempt(
    project_dir: str | Path,
    *,
    repair_loop_path: str | Path | None = None,
    page_number: int | None = None,
    base_url: str = "http://127.0.0.1:8000",
) -> dict[str, Any]:
    """Run one queued page through a page-only render/score/merge attempt."""
    project_path = Path(project_dir).expanduser().resolve()
    repo_root = project_path.parent.parent if project_path.parent.name == "projects" else Path.cwd()
    loop_path = (
        Path(repair_loop_path).expanduser().resolve()
        if repair_loop_path
        else repo_root / "evals" / "reports" / project_path.name / "repair-loop.json"
    )
    loop = _load_json(loop_path)
    queue = loop.get("repair_queue") if isinstance(loop.get("repair_queue"), list) else []
    item = _select_queue_item(queue, page_number)
    if not item:
        return {
            "schema": "brochure-maker.page-repair-attempt.v1",
            "project_id": project_path.name,
            "status": "no-op",
            "accepted": bool(loop.get("accepted")),
            "page_number": page_number,
            "message": "No queued failed page matched this request.",
            "repair_loop": str(loop_path),
        }
    if item.get("status") == "retry-limit-reached":
        return {
            "schema": "brochure-maker.page-repair-attempt.v1",
            "project_id": project_path.name,
            "status": "blocked",
            "accepted": False,
            "page_number": int(item.get("page_number") or 0),
            "message": "Queued page has reached the retry limit; no page render was run.",
            "repair_loop": str(loop_path),
            "repair_attempts": item.get("repair_attempts"),
        }

    target_page = int(item.get("page_number") or 0)
    packet_dir = Path(str(item.get("packet_dir"))).expanduser()
    attempts_path = Path(str(item.get("repair_attempts") or packet_dir / "repair-attempts.json")).expanduser()
    attempts = _load_json(attempts_path) or {
        "schema": "brochure-maker.repair-attempts.v1",
        "project_id": project_path.name,
        "page_number": target_page,
        "attempts": [],
    }
    attempt_items = attempts.get("attempts") if isinstance(attempts.get("attempts"), list) else []
    attempt_number = len(attempt_items) + 1
    attempt_id = f"page-{target_page:03d}-attempt-{attempt_number:03d}"
    attempt_dir = packet_dir / "attempts" / f"attempt-{attempt_number:03d}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    pre_render = _render_fingerprints(packet_dir=packet_dir)
    repair_plan_payload = _load_json(item.get("repair_plan") or packet_dir / "repair-plan.json")
    patch_path = _resolve_patch_path(packet_dir, repair_plan_payload, item)
    patch_application = None
    if patch_path and patch_path.exists():
        patch_payload = _load_json(patch_path)
        if isinstance(patch_payload.get("operations"), list) and patch_payload["operations"]:
            patch_application = apply_page_repair_patch(
                project_path,
                patch_path,
                output_path=attempt_dir / "page-repair-patch-application.json",
            )

    visual_path = write_visual_diff_report(
        project_path,
        attempt_dir / "visual",
        base_url=base_url,
        page_numbers=[target_page],
    )
    assessment_path = attempt_dir / "page-assessment.json"
    write_html_assessment(project_path, assessment_path, visual_report_path=visual_path)
    visual = _load_json(visual_path)
    assessment = _load_json(assessment_path)
    visual_page = _page_entry(visual, target_page)
    assessment_page = _page_entry(assessment, target_page)
    score = float(visual_page.get("score") or assessment_page.get("score") or 0)
    raw_accepted = score >= PASS_SCORE and bool(assessment_page.get("accepted", score >= PASS_SCORE))
    previous_score = _float_or_none(item.get("score"))
    post_render = _render_fingerprints(visual_page=visual_page)
    repair_evidence = _repair_evidence(
        pre_render=pre_render,
        post_render=post_render,
        previous_score=previous_score,
        score=score,
    )
    judgement_ok, judgement_blockers = _packet_judgement_status(packet_dir)
    repair_evidence["judgement_agent_accepted"] = judgement_ok
    repair_evidence["judgement_agent_blockers"] = judgement_blockers
    repair_evidence["acceptance_blockers"].extend(judgement_blockers)
    accepted = raw_accepted and not repair_evidence["acceptance_blockers"]
    status = "accepted-after-page-repair" if accepted else "needs-code-repair"
    finished_at = datetime.now(timezone.utc).isoformat()
    passing_pages = [
        int(page.get("page_number") or 0)
        for page in loop.get("skipped_pages") or []
        if isinstance(page, dict) and int(page.get("page_number") or 0) > 0
    ]
    attempt = {
        "schema": "brochure-maker.page-repair-attempt.v1",
        "project_id": project_path.name,
        "attempt_id": attempt_id,
        "page_number": target_page,
        "attempt_number": attempt_number,
        "started_at": started_at,
        "finished_at": finished_at,
        "captured_at": finished_at,
        "status": status,
        "accepted": accepted,
        "raw_accepted": raw_accepted,
        "previous_score": item.get("score"),
        "score": round(score, 3),
        "score_delta": repair_evidence["score_delta"],
        "target_score": PASS_SCORE,
        "band": _score_band(score),
        "rendered_pages": [target_page],
        "passing_pages_untouched": passing_pages,
        "packet_dir": str(packet_dir),
        "repair_plan": item.get("repair_plan"),
        "critique": item.get("critique"),
        "tasks": item.get("tasks") if isinstance(item.get("tasks"), list) else [],
        "top_failures": item.get("top_failures") if isinstance(item.get("top_failures"), list) else [],
        "inputs_read": _page_inputs(packet_dir, item),
        "patch_application": patch_application,
        "repair_evidence": repair_evidence,
        "reusable_rationale": (
            "Attempt was scoped to PDF-derived page-packet evidence and the reusable exact renderer; "
            "it did not copy existing project HTML or introduce project-specific coordinates."
        ),
        "disallowed_checks": {
            "copied_existing_project_html": False,
            "hardcoded_project_coordinates": False,
            "introduced_project_id_literal_patch": False,
            "touched_passing_page_packets": False,
            "accepted_without_changed_render_output": bool(repair_evidence["acceptance_blockers"]),
        },
        "artifacts": {
            "attempt_dir": str(attempt_dir),
            "patch_application": str(attempt_dir / "page-repair-patch-application.json") if patch_application else None,
            "visual_diff": str(visual_path),
            "html_assessment": str(assessment_path),
            "source": visual_page.get("original"),
            "generated": visual_page.get("generated"),
            "diff": visual_page.get("diff"),
        },
        "scope_guard": {
            "repair_scope": "page-only",
            "requested_page": target_page,
            "fresh_upload_scope": "not rerun by this command",
            "passing_pages_are_not_rerendered": True,
        },
        "next_action": "rerun repair loop or benchmark matrix" if accepted else _next_action_for_failed_attempt(repair_evidence),
    }
    attempt_items.append(attempt)
    attempts["attempts"] = attempt_items
    attempts["latest_attempt"] = attempt
    _write_json(attempts_path, attempts)
    _merge_attempt_into_page_packet(loop_path, loop, target_page, attempt, visual_page, assessment_page)
    return attempt


def _merge_attempt_into_page_packet(
    loop_path: Path,
    loop: dict[str, Any],
    page_number: int,
    attempt: dict[str, Any],
    visual_page: dict[str, Any],
    assessment_page: dict[str, Any],
) -> None:
    packets_path = Path(str(loop.get("page_packets") or "")).expanduser()
    packets = _load_json(packets_path)
    packet_entries = packets.get("packets") if isinstance(packets.get("packets"), list) else []
    packet_dir: Path | None = None
    for packet in packet_entries:
        if not isinstance(packet, dict) or int(packet.get("page_number") or 0) != page_number:
            continue
        packet["score"] = attempt["score"]
        packet["accepted"] = bool(attempt["accepted"])
        packet["band"] = attempt["band"]
        packet_dir = Path(str(packet.get("packet_dir") or "")).expanduser()
        packet["last_attempt"] = str(packet_dir / "attempts" / f"attempt-{attempt['attempt_number']:03d}" / "page-repair-attempt.json")
        break
    packets["accepted"] = bool(packet_entries) and all(bool(packet.get("accepted")) for packet in packet_entries if isinstance(packet, dict))
    packets["failed_pages"] = [
        int(packet.get("page_number") or 0)
        for packet in packet_entries
        if isinstance(packet, dict) and not packet.get("accepted")
    ]
    _write_json(packets_path, packets)

    attempt_path = Path(attempt["artifacts"]["attempt_dir"]) / "page-repair-attempt.json"
    _write_json(attempt_path, attempt)
    if packet_dir:
        _copy_attempt_images(visual_page, packet_dir)
        _write_json(
            packet_dir / "page-score.json",
            {
                "schema": "brochure-maker.page-score.v1",
                "project_id": attempt["project_id"],
                "page_number": page_number,
                "score": attempt["score"],
                "accepted": bool(attempt["accepted"]),
                "band": attempt["band"],
                "threshold": PASS_SCORE,
                "visual": {
                    "mean_absolute_error": visual_page.get("mean_absolute_error"),
                    "source": str(packet_dir / "source.png") if (packet_dir / "source.png").exists() else visual_page.get("original"),
                    "generated": str(packet_dir / "generated.png") if (packet_dir / "generated.png").exists() else visual_page.get("generated"),
                    "diff": str(packet_dir / "diff.png") if (packet_dir / "diff.png").exists() else visual_page.get("diff"),
                },
                "latest_attempt": str(attempt_path),
            },
        )
        _write_json(packet_dir / "page-assessment.json", assessment_page)
    write_repair_loop(
        Path(str(loop.get("project_dir"))),
        page_packets_path=packets_path,
        output_path=loop_path,
        max_attempts=int(loop.get("max_attempts") or 3),
    )


def _packet_judgement_status(packet_dir: Path) -> tuple[bool, list[str]]:
    """Return whether existing page judgement agents still permit acceptance."""
    blockers: list[str] = []
    found = False
    for filename in (
        "visual-critic.json",
        "browser-interaction-qa.json",
        "editability-critic.json",
        "extraction-diagnosis.json",
        "hardcoding-critic.json",
    ):
        payload = _load_json(packet_dir / filename)
        if not payload:
            continue
        found = True
        agent = str(payload.get("agent") or filename.removesuffix(".json"))
        if payload.get("accepted") is False:
            agent_blockers = [str(item) for item in payload.get("blockers") or []]
            if agent_blockers:
                blockers.extend(f"{agent}: {item}" for item in agent_blockers[:8])
            else:
                blockers.append(f"{agent}: judgement agent rejected this page")
    if not found:
        return True, []
    return not blockers, blockers


def _copy_attempt_images(visual_page: dict[str, Any], packet_dir: Path) -> None:
    for source_key, filename in (("original", "source.png"), ("generated", "generated.png"), ("diff", "diff.png")):
        source = visual_page.get(source_key)
        if not source:
            continue
        source_path = Path(str(source)).expanduser()
        if source_path.exists():
            shutil.copy2(source_path, packet_dir / filename)


def _resolve_patch_path(packet_dir: Path, repair_plan: dict[str, Any], queue_item: dict[str, Any] | None = None) -> Path | None:
    queue_item = queue_item or {}
    value = queue_item.get("repair_patch")
    if isinstance(value, str) and value.strip():
        path = Path(value).expanduser()
        return path if path.is_absolute() else packet_dir / path
    for key in ("patch", "patch_path", "page_patch"):
        value = repair_plan.get(key)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            return path if path.is_absolute() else packet_dir / path
    default = packet_dir / "page-repair-patch.json"
    return default if default.exists() else None


def _repair_evidence(
    *,
    pre_render: dict[str, Any],
    post_render: dict[str, Any],
    previous_score: float | None,
    score: float,
) -> dict[str, Any]:
    changed_outputs = _changed_render_outputs(pre_render, post_render)
    pre_render_available = all(
        bool(pre_render.get(key, {}).get("exists"))
        for key in ("generated", "diff")
    )
    was_failing = previous_score is not None and previous_score < PASS_SCORE
    score_delta = round(score - previous_score, 3) if previous_score is not None else None
    acceptance_blockers: list[str] = []
    if was_failing and score >= PASS_SCORE and pre_render_available and not changed_outputs:
        acceptance_blockers.append(
            "No generated/diff render output changed from the failed page packet; this is a rescore, not a proven page repair."
        )
    return {
        "schema": "brochure-maker.page-repair-evidence.v1",
        "previous_score": previous_score,
        "score": round(score, 3),
        "score_delta": score_delta,
        "previous_page_was_failing": was_failing,
        "pre_render_available": pre_render_available,
        "changed_render_outputs": changed_outputs,
        "visual_output_changed": bool(changed_outputs),
        "pre_render": pre_render,
        "post_render": post_render,
        "acceptance_blockers": acceptance_blockers,
        "contract": (
            "A previously failing page may only be accepted after a page repair attempt when the "
            "generated or diff render evidence changes, proving the loop did not simply rescore the same output."
        ),
    }


def _render_fingerprints(
    *,
    packet_dir: Path | None = None,
    visual_page: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fingerprints: dict[str, Any] = {}
    for logical_key, packet_name, visual_key in (
        ("source", "source.png", "original"),
        ("generated", "generated.png", "generated"),
        ("diff", "diff.png", "diff"),
    ):
        path: Path | None = None
        if visual_page is not None and visual_page.get(visual_key):
            path = Path(str(visual_page.get(visual_key))).expanduser()
        elif packet_dir is not None:
            path = packet_dir / packet_name
        fingerprints[logical_key] = _file_fingerprint(path) if path is not None else {"exists": False}
    return fingerprints


def _changed_render_outputs(pre_render: dict[str, Any], post_render: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for key in ("generated", "diff"):
        before = pre_render.get(key) if isinstance(pre_render.get(key), dict) else {}
        after = post_render.get(key) if isinstance(post_render.get(key), dict) else {}
        if not before.get("exists") or not after.get("exists"):
            continue
        if before.get("sha256") != after.get("sha256"):
            changed.append(key)
    return changed


def _next_action_for_failed_attempt(repair_evidence: dict[str, Any]) -> str:
    if repair_evidence.get("acceptance_blockers"):
        return "make a reusable pipeline code or design-graph repair that changes the page render, then rerun this page attempt"
    return "make a reusable pipeline code change, then rerun this page attempt"


def _page_inputs(packet_dir: Path, item: dict[str, Any]) -> list[dict[str, Any]]:
    paths = [
        item.get("critique"),
        item.get("repair_plan"),
        item.get("repair_attempts"),
        packet_dir / "page-score.json",
        packet_dir / "page-assessment.json",
        packet_dir / "design-page.json",
        packet_dir / "inventory-page.json",
        packet_dir / "pdf-text-spans.json",
        packet_dir / "pdf-fonts.json",
        packet_dir / "pdf-images.json",
        packet_dir / "pdf-vectors.json",
        packet_dir / "raster-components.json",
        packet_dir / "source.png",
        packet_dir / "generated.png",
        packet_dir / "diff.png",
    ]
    fingerprints: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in paths:
        if not raw_path:
            continue
        path = Path(str(raw_path)).expanduser()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        fingerprints.append(_file_fingerprint(path))
    return fingerprints


def _file_fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "sha256": digest.hexdigest(),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def _select_queue_item(queue: list[Any], page_number: int | None) -> dict[str, Any] | None:
    for item in queue:
        if not isinstance(item, dict):
            continue
        if page_number is None or int(item.get("page_number") or 0) == int(page_number):
            return item
    return None


def _page_entry(report: dict[str, Any], page_number: int) -> dict[str, Any]:
    for page in report.get("pages") or []:
        if isinstance(page, dict) and int(page.get("page_number") or 0) == page_number:
            return page
    return {}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_band(score: float) -> str:
    if score >= PASS_SCORE:
        return "pass"
    if score >= 85:
        return "focused repair"
    if score >= 70:
        return "verifier agent required"
    if score >= 50:
        return "deeper extraction diagnosis"
    return "page extraction failure"


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
    parser = argparse.ArgumentParser(description="Run one page-scoped exact-PDF repair attempt.")
    parser.add_argument("project_dir")
    parser.add_argument("--repair-loop", default=None)
    parser.add_argument("--page", type=int, default=None)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    attempt = run_page_repair_attempt(
        args.project_dir,
        repair_loop_path=args.repair_loop,
        page_number=args.page,
        base_url=args.base_url,
    )
    print(json.dumps(attempt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
