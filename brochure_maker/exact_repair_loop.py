"""Create the failed-page repair loop artifact for exact PDF benchmarks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_MAX_ATTEMPTS = 3


def build_repair_loop(
    project_dir: str | Path,
    *,
    page_packets_path: str | Path | None = None,
    output_path: str | Path | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Build the page-focused repair queue from page packet evidence."""
    project_path = Path(project_dir).expanduser().resolve()
    repo_root = project_path.parent.parent if project_path.parent.name == "projects" else Path.cwd()
    packets_path = (
        Path(page_packets_path).expanduser().resolve()
        if page_packets_path
        else repo_root / "evals" / "reports" / project_path.name / "page-packets.json"
    )
    packets = _load_json(packets_path)
    packet_entries = packets.get("packets") if isinstance(packets.get("packets"), list) else []
    repair_queue: list[dict[str, Any]] = []
    skipped_pages: list[dict[str, Any]] = []
    blockers: list[str] = []

    for packet in packet_entries:
        if not isinstance(packet, dict):
            continue
        page_number = int(packet.get("page_number") or 0)
        if page_number <= 0:
            continue
        packet_dir = Path(str(packet.get("packet_dir") or packets_path.parent / "pages" / f"page-{page_number:03d}")).expanduser()
        judgement_accepted = _packet_judgement_accepted(packet)
        critique_path = Path(str(packet.get("critique") or packet_dir / "page-critique.json")).expanduser()
        critique = _load_json(critique_path)
        critique_accepted = critique.get("accepted")
        effective_accepted = (
            packet.get("accepted") is True
            and judgement_accepted
            and critique_accepted is not False
        )
        if effective_accepted:
            skipped_pages.append(
                {
                    "page_number": page_number,
                    "score": packet.get("score"),
                    "reason": "page already passed the 95 score gate",
                    "critique": str(critique_path),
                }
            )
            continue

        repair_plan_path = Path(str(packet.get("repair_plan") or packet_dir / "repair-plan.json")).expanduser()
        repair_patch_path = Path(str(packet.get("repair_patch") or packet_dir / "page-repair-patch.json")).expanduser()
        attempts_path = packet_dir / "repair-attempts.json"
        repair_plan = _load_json(repair_plan_path)
        attempts = _load_json(attempts_path)
        attempt_items = attempts.get("attempts") if isinstance(attempts.get("attempts"), list) else []
        if not critique:
            blockers.append(f"Page {page_number} is failed but has no page-critique.json")
        if not repair_plan:
            blockers.append(f"Page {page_number} is failed but has no repair-plan.json")
        if len(attempt_items) >= max_attempts:
            blockers.append(f"Page {page_number} has reached the repair attempt limit")

        repair_queue.append(
            {
                "page_number": page_number,
                "score": packet.get("score"),
                "band": packet.get("band"),
                "packet_dir": str(packet_dir),
                "status": "retry-limit-reached" if len(attempt_items) >= max_attempts else "needs-focused-repair",
                "critique": str(critique_path),
                "repair_plan": str(repair_plan_path),
                "repair_patch": str(repair_patch_path),
                "repair_attempts": str(attempts_path),
                "attempt_count": len(attempt_items),
                "top_failures": critique.get("top_failures") if isinstance(critique.get("top_failures"), list) else [],
                "tasks": repair_plan.get("tasks") if isinstance(repair_plan.get("tasks"), list) else [],
                "judgement_agents": packet.get("judgement_agents") if isinstance(packet.get("judgement_agents"), dict) else {},
            }
        )

    if not packet_entries:
        blockers.append("No page packets available for repair loop")
    status = "accepted" if not repair_queue and not blockers and bool(packet_entries) else "needs-focused-repair"
    next_action = "none"
    if repair_queue:
        next_page = repair_queue[0]["page_number"]
        next_action = f"repair page {next_page} using its page packet, then rerender and rescore that page only"
    elif blockers:
        next_action = "resolve repair-loop blockers before accepting the brochure"

    loop = {
        "schema": "brochure-maker.exact-repair-loop.v1",
        "project_id": project_path.name,
        "project_dir": str(project_path),
        "page_packets": str(packets_path),
        "status": status,
        "accepted": status == "accepted" and not blockers,
        "max_attempts": max_attempts,
        "failed_page_count": len(repair_queue),
        "skipped_page_count": len(skipped_pages),
        "repair_queue": repair_queue,
        "skipped_pages": skipped_pages,
        "blockers": sorted(dict.fromkeys(blockers)),
        "next_action": next_action,
        "loop_contract": {
            "fresh_upload_scope": "whole brochure once",
            "repair_scope": "failed pages only",
            "passing_pages": "do not rerender unless a reusable pipeline change requires a fresh import",
            "acceptance": "all pages score at least 95 and Browser/export gates pass",
        },
    }
    output = (
        Path(output_path).expanduser().resolve()
        if output_path
        else packets_path.parent / "repair-loop.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(loop, indent=2, sort_keys=True), encoding="utf-8")
    return loop


def write_repair_loop(
    project_dir: str | Path,
    *,
    page_packets_path: str | Path | None = None,
    output_path: str | Path | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Path:
    """Write the repair-loop artifact and return its path."""
    loop = build_repair_loop(
        project_dir,
        page_packets_path=page_packets_path,
        output_path=output_path,
        max_attempts=max_attempts,
    )
    return Path(output_path).expanduser().resolve() if output_path else Path(loop["page_packets"]).parent / "repair-loop.json"


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _packet_judgement_accepted(packet: dict[str, Any]) -> bool:
    summary = packet.get("judgement_agents") if isinstance(packet.get("judgement_agents"), dict) else {}
    if not summary:
        return True
    if summary.get("accepted") is False:
        return False
    agents = summary.get("agents") if isinstance(summary.get("agents"), dict) else {}
    return not any(isinstance(agent, dict) and agent.get("accepted") is False for agent in agents.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an exact-PDF failed-page repair loop artifact.")
    parser.add_argument("project_dir")
    parser.add_argument("--page-packets", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    args = parser.parse_args()
    path = write_repair_loop(
        args.project_dir,
        page_packets_path=args.page_packets,
        output_path=args.output,
        max_attempts=args.max_attempts,
    )
    print(path)


if __name__ == "__main__":
    main()
