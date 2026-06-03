from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_code_repair_dispatcher import (
    build_code_repair_dispatch,
    write_code_repair_dispatch,
)


class TestExactCodeRepairDispatcher(unittest.TestCase):
    def test_dispatch_writes_worker_briefs_for_optional_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, tasks_path = self._write_tasks_fixture(base, required=False)

            report = build_code_repair_dispatch(project_dir, code_repair_tasks_path=tasks_path)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["status"], "accepted-optional-worker-briefs")
            self.assertEqual(report["dispatched_task_count"], 1)
            brief = report["worker_briefs"][0]
            self.assertEqual(brief["status"], "optional-worker-ready")
            self.assertTrue(Path(brief["worker_brief"]).exists())
            self.assertTrue(Path(brief["worker_prompt"]).exists())
            self.assertTrue(Path(brief["evidence_manifest"]).exists())
            prompt = Path(brief["worker_prompt"]).read_text(encoding="utf-8")
            self.assertIn("Suggested Files", prompt)
            self.assertIn("Hardcoding Rules", prompt)

    def test_dispatch_blocks_required_tasks_until_worker_implementation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, tasks_path = self._write_tasks_fixture(base, required=True)

            report = build_code_repair_dispatch(project_dir, code_repair_tasks_path=tasks_path)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["status"], "required-worker-briefs-ready")
            self.assertEqual(report["required_dispatch_count"], 1)
            self.assertEqual(report["blockers"], [])

    def test_dispatch_can_select_required_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, tasks_path = self._write_tasks_fixture(base, required=True, include_optional=True)

            report = build_code_repair_dispatch(
                project_dir,
                code_repair_tasks_path=tasks_path,
                include_optional=False,
            )

            self.assertEqual(report["dispatched_task_count"], 1)
            self.assertEqual(report["worker_briefs"][0]["task_id"], "typography-title-grouping")

    def test_write_dispatch_returns_report_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, tasks_path = self._write_tasks_fixture(base, required=False)

            path = write_code_repair_dispatch(
                project_dir,
                code_repair_tasks_path=tasks_path,
                output_dir=base / "workers",
            )

            self.assertEqual(path, (base / "code-repair-dispatch.json").resolve())
            self.assertTrue(path.exists())

    def _write_tasks_fixture(
        self,
        base: Path,
        *,
        required: bool,
        include_optional: bool = False,
    ) -> tuple[Path, Path]:
        project_dir = base / "projects" / "demo"
        project_dir.mkdir(parents=True)
        packet_dir = base / "reports" / "demo" / "pages" / "page-001"
        packet_dir.mkdir(parents=True)
        for filename in ("source.png", "generated.png", "diff.png", "page-critique.json"):
            (packet_dir / filename).write_text("evidence", encoding="utf-8")
        tasks = [
            {
                "id": "typography-title-grouping",
                "subsystem": "typography/title-grouping",
                "root_subsystem": "typography",
                "status": "required-reusable-code-repair" if required else "optional-improvement",
                "required_for_acceptance": required,
                "severity": "major" if required else "minor",
                "pages": [1],
                "issues": ["Title grouping mismatch"],
                "roles": ["cover-title"],
                "suggested_files": ["brochure_maker/pdf_exact_layout.py"],
                "evidence": [
                    {"page_number": 1, "kind": "packet", "path": str(packet_dir)},
                    {"page_number": 1, "kind": "source", "path": str(packet_dir / "source.png")},
                    {"page_number": 1, "kind": "generated", "path": str(packet_dir / "generated.png")},
                    {"page_number": 1, "kind": "diff", "path": str(packet_dir / "diff.png")},
                    {"page_number": 1, "kind": "critique", "path": str(packet_dir / "page-critique.json")},
                ],
                "worker_prompt": "Fix the reusable title grouping rule.",
                "acceptance_checks": ["rerun full matrix"],
                "hardcoding_rules": ["Do not copy existing project HTML"],
            }
        ]
        if include_optional:
            tasks.append(
                {
                    "id": "images-photo-slots",
                    "subsystem": "images/photo-slots",
                    "root_subsystem": "images",
                    "status": "optional-improvement",
                    "required_for_acceptance": False,
                    "severity": "minor",
                    "pages": [2],
                    "issues": ["Photo crop mismatch"],
                    "suggested_files": ["brochure_maker/exact_pdf_layout.py"],
                    "evidence": [],
                    "worker_prompt": "Improve reusable photo crop detection.",
                    "acceptance_checks": [],
                    "hardcoding_rules": ["Do not copy existing project HTML"],
                }
            )
        tasks_path = base / "reports" / "demo" / "code-repair-tasks.json"
        tasks_path.parent.mkdir(parents=True, exist_ok=True)
        tasks_path.write_text(
            json.dumps(
                {
                    "accepted": not required,
                    "task_count": len(tasks),
                    "required_task_count": 1 if required else 0,
                    "optional_task_count": len(tasks) - (1 if required else 0),
                    "tasks": tasks,
                }
            ),
            encoding="utf-8",
        )
        return project_dir, tasks_path


if __name__ == "__main__":
    unittest.main()
