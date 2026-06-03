"""Aggregate page critiques into reusable exact-pipeline code repair tasks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUBSYSTEM_FILES = {
    "typography": [
        "brochure_maker/pdf_exact_layout.py",
        "brochure_maker/pdf_design_graph.py",
        "brochure_maker/exact_pdf_layout.py",
    ],
    "images": [
        "brochure_maker/pdf_exact_layout.py",
        "brochure_maker/exact_pdf_layout.py",
    ],
    "vectors": [
        "brochure_maker/pdf_vector_overlay.py",
        "brochure_maker/pdf_exact_layout.py",
    ],
    "space-plans": [
        "brochure_maker/pdf_exact_layout.py",
        "brochure_maker/exact_pdf_layout.py",
    ],
    "icons": [
        "brochure_maker/pdf_vector_overlay.py",
        "brochure_maker/exact_pdf_layout.py",
    ],
    "maps": [
        "brochure_maker/pdf_exact_layout.py",
        "brochure_maker/pdf_design_graph.py",
        "brochure_maker/exact_pdf_layout.py",
    ],
    "contacts": [
        "brochure_maker/pdf_exact_layout.py",
        "brochure_maker/pdf_design_graph.py",
        "brochure_maker/exact_pdf_layout.py",
        "app.py",
    ],
    "export": [
        "app.py",
        "brochure_maker/exact_pdf_layout.py",
    ],
    "design-graph": [
        "brochure_maker/pdf_design_graph.py",
        "brochure_maker/pdf_exact_layout.py",
    ],
    "renderer": [
        "brochure_maker/exact_pdf_layout.py",
        "app.py",
    ],
}

HARDCODING_RULES = [
    "Use PDF-derived evidence from page packets, exact-layout.json, extraction-inventory.json, and brochure.design.json only.",
    "Do not copy existing project HTML, editor state, image output, or manually fixed coordinates.",
    "Express repairs as reusable detectors, grouping rules, renderer rules, state rules, or export rules.",
    "After a code repair, rerun the affected page packet first, then rerun the fresh benchmark/matrix gate.",
]

FINAL_GATE_ISSUE_PATTERNS = (
    "Browser layout audit found possible overlapping editable text blocks",
    "Agency logo needs visual source fidelity",
    "Cover writing is still represented by many single-glyph title fragments",
)


def build_code_repair_tasks(
    project_dir: str | Path,
    *,
    page_packets_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a run-level worker backlog from page critique mark sheets."""
    project_path = Path(project_dir).expanduser().resolve()
    repo_root = project_path.parent.parent if project_path.parent.name == "projects" else Path.cwd()
    packets_path = (
        Path(page_packets_path).expanduser().resolve()
        if page_packets_path
        else repo_root / "evals" / "reports" / project_path.name / "page-packets.json"
    )
    packets = _load_json(packets_path)
    packet_entries = packets.get("packets") if isinstance(packets.get("packets"), list) else []
    task_buckets: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    page_audits: list[dict[str, Any]] = []

    for packet in packet_entries:
        if not isinstance(packet, dict):
            continue
        page_number = int(packet.get("page_number") or 0)
        packet_dir = Path(str(packet.get("packet_dir") or packets_path.parent / "pages" / f"page-{page_number:03d}")).expanduser()
        critique_path = Path(str(packet.get("critique") or packet_dir / "page-critique.json")).expanduser()
        critique = _load_json(critique_path)
        page_audits.append(
            {
                "page_number": page_number,
                "accepted": bool(packet.get("accepted")),
                "score": packet.get("score"),
                "critique": str(critique_path),
                "critique_exists": bool(critique),
            }
        )
        if not critique:
            blockers.append(f"Page {page_number} has no readable critique for code repair task generation")
            continue
        findings = _critique_findings(critique)
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            subsystem = _task_subsystem(finding)
            key = subsystem.replace("/", "-")
            bucket = task_buckets.setdefault(
                key,
                {
                    "id": key,
                    "subsystem": subsystem,
                    "root_subsystem": subsystem.split("/", 1)[0],
                    "status": "optional-improvement",
                    "required_for_acceptance": False,
                    "severity": "note",
                    "pages": [],
                    "issues": [],
                    "roles": [],
                    "finding_count": 0,
                    "suggested_files": [],
                    "evidence": [],
                    "worker_prompt": "",
                    "acceptance_checks": [],
                    "hardcoding_rules": HARDCODING_RULES,
                },
            )
            _merge_finding(bucket, page_number, packet_dir, critique_path, packet, critique, finding)

    tasks = sorted(task_buckets.values(), key=_task_sort_key)
    for task in tasks:
        task["pages"] = sorted(dict.fromkeys(int(page) for page in task["pages"]))
        task["issues"] = sorted(dict.fromkeys(task["issues"]))
        task["roles"] = sorted(dict.fromkeys(task["roles"]))
        task["suggested_files"] = sorted(dict.fromkeys(task["suggested_files"]))
        task["evidence"] = sorted(task["evidence"], key=lambda item: (item["page_number"], item["path"]))
        task["acceptance_checks"] = _acceptance_checks(task)
        task["worker_prompt"] = _worker_prompt(project_path.name, task)

    required_tasks = [task for task in tasks if task.get("required_for_acceptance")]
    if not packet_entries:
        blockers.append("No page packets available for code repair task generation")
    accepted = not blockers and not required_tasks
    report = {
        "schema": "brochure-maker.exact-code-repair-tasks.v1",
        "project_id": project_path.name,
        "project_dir": str(project_path),
        "page_packets": str(packets_path),
        "accepted": accepted,
        "status": "accepted-with-optional-repair-tasks" if accepted and tasks else "accepted-no-code-repair-tasks" if accepted else "needs-reusable-code-repair",
        "task_count": len(tasks),
        "required_task_count": len(required_tasks),
        "optional_task_count": len(tasks) - len(required_tasks),
        "blockers": sorted(dict.fromkeys(blockers)),
        "tasks": tasks,
        "page_audit": page_audits,
        "rules": {
            "required_tasks": "block acceptance because a page failed, has major/blocker findings, or needs repair",
            "optional_tasks": "do not block acceptance but explain residual sub-95-risk work for future pipeline improvements",
            "hardcoding": HARDCODING_RULES,
        },
    }
    output = Path(output_path).expanduser().resolve() if output_path else packets_path.parent / "code-repair-tasks.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def write_code_repair_tasks(
    project_dir: str | Path,
    *,
    page_packets_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> Path:
    """Write code repair tasks and return the report path."""
    report = build_code_repair_tasks(
        project_dir,
        page_packets_path=page_packets_path,
        output_path=output_path,
    )
    if output_path:
        return Path(output_path).expanduser().resolve()
    return Path(report["page_packets"]).parent / "code-repair-tasks.json"


def _critique_findings(critique: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for key in ("top_failures", "residual_findings"):
        items = critique.get(key) if isinstance(critique.get(key), list) else []
        for item in items:
            if isinstance(item, dict):
                findings.append(item)
    return findings


def _task_subsystem(finding: dict[str, Any]) -> str:
    subsystem = str(finding.get("repair_task") or finding.get("repair_subsystem") or "").strip()
    if subsystem:
        return subsystem
    feature = str(finding.get("feature") or "")
    if feature in {"cover-title", "section-heading", "body", "caption", "table-status"}:
        return "typography/text-layout"
    if feature in {"photo-region", "hero-image"}:
        return "images/photo-slots"
    if feature in {"agency-logo", "agent-contact"}:
        return "contacts/agent-text"
    if feature in {"amenity-icon", "service-icon"}:
        return "icons/icon-slots"
    if feature.startswith("map") or feature == "subject-marker":
        return "maps/map-reconstruction"
    return "unknown/page-review"


def _merge_finding(
    bucket: dict[str, Any],
    page_number: int,
    packet_dir: Path,
    critique_path: Path,
    packet: dict[str, Any],
    critique: dict[str, Any],
    finding: dict[str, Any],
) -> None:
    severity = str(finding.get("severity") or "minor")
    if _severity_rank(severity) > _severity_rank(str(bucket.get("severity") or "note")):
        bucket["severity"] = severity
    needs_repair = bool(critique.get("needs_repair")) or not packet.get("accepted") or severity in {"blocker", "major"}
    if needs_repair or _finding_blocks_final_acceptance(finding):
        bucket["required_for_acceptance"] = True
        bucket["status"] = "required-reusable-code-repair"
    bucket["finding_count"] = int(bucket.get("finding_count") or 0) + 1
    bucket["pages"].append(page_number)
    issue = finding.get("problem") or finding.get("issue")
    if issue:
        bucket["issues"].append(str(issue))
    roles = finding.get("roles") if isinstance(finding.get("roles"), list) else []
    feature = finding.get("feature")
    if feature:
        roles.append(str(feature))
    bucket["roles"].extend(str(role) for role in roles if role)
    root = str(bucket.get("root_subsystem") or "")
    bucket["suggested_files"].extend(SUBSYSTEM_FILES.get(root, []))
    bucket["evidence"].extend(
        [
            {"page_number": page_number, "kind": "packet", "path": str(packet_dir)},
            {"page_number": page_number, "kind": "critique", "path": str(critique_path)},
            {"page_number": page_number, "kind": "source", "path": str(packet_dir / "source.png")},
            {"page_number": page_number, "kind": "generated", "path": str(packet_dir / "generated.png")},
            {"page_number": page_number, "kind": "diff", "path": str(packet_dir / "diff.png")},
        ]
    )


def _acceptance_checks(task: dict[str, Any]) -> list[str]:
    root = str(task.get("root_subsystem") or "")
    checks = [
        "rerun the affected page packet repair/eval",
        "rerun the full exact benchmark matrix",
        "confirm hardcoding scan remains clean",
    ]
    if root == "typography":
        checks.append("Browser-check edited title/body text retains extracted font styling")
    elif root == "contacts":
        checks.append("Browser-check contact text and agency logo replacement persist and export")
    elif root == "icons":
        checks.append("Browser-check icon swaps keep transparent backgrounds and export")
    elif root == "maps":
        checks.append("Browser-check map preserve/regenerate controls and labels")
    elif root == "images":
        checks.append("Browser-check image replacement/crop and clean export")
    return checks


def _finding_blocks_final_acceptance(finding: dict[str, Any]) -> bool:
    """Return true for user-visible feature defects that cannot be hand-waved."""
    issue = str(finding.get("problem") or finding.get("issue") or "")
    severity = str(finding.get("severity") or "")
    if "Browser layout audit found possible overlapping editable text blocks" in issue:
        return severity in {"blocker", "major"}
    if any(pattern in issue for pattern in FINAL_GATE_ISSUE_PATTERNS):
        return True
    if "missing" in issue.lower():
        subsystem = str(finding.get("repair_task") or finding.get("repair_subsystem") or "")
        if subsystem.startswith(("logo", "contacts", "icons", "maps", "space-plans", "export")):
            return True
    return False


def _worker_prompt(project_id: str, task: dict[str, Any]) -> str:
    pages = ", ".join(str(page) for page in task.get("pages") or [])
    files = ", ".join(task.get("suggested_files") or ["the exact pipeline files"])
    return (
        f"Project {project_id}: inspect page packet(s) {pages} and fix the reusable {task.get('subsystem')} "
        f"pipeline behavior in {files}. Use only PDF-derived evidence and the page critique mark sheets; "
        "do not copy existing HTML or hardcode brochure/project coordinates."
    )


def _task_sort_key(task: dict[str, Any]) -> tuple[int, int, str]:
    required_rank = 0 if task.get("required_for_acceptance") else 1
    return (required_rank, -_severity_rank(str(task.get("severity") or "")), str(task.get("id") or ""))


def _severity_rank(severity: str) -> int:
    return {"blocker": 4, "major": 3, "minor": 2, "note": 1}.get(severity, 0)


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate exact page critiques into reusable code repair tasks.")
    parser.add_argument("project_dir")
    parser.add_argument("--page-packets", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    path = write_code_repair_tasks(
        args.project_dir,
        page_packets_path=args.page_packets,
        output_path=args.output,
    )
    print(path)


if __name__ == "__main__":
    main()
