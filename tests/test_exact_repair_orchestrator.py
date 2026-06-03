from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from brochure_maker.exact_repair_orchestrator import run_repair_orchestration


class TestExactRepairOrchestrator(unittest.TestCase):
    def test_orchestration_runs_queued_page_and_accepts_after_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, packet_dir = self._write_fixture(base, accepted=False)

            def fake_attempt(project_dir_arg, **kwargs):
                self.assertEqual(kwargs["page_number"], 1)
                loop = json.loads(loop_path.read_text(encoding="utf-8"))
                loop["accepted"] = True
                loop["status"] = "accepted"
                loop["repair_queue"] = []
                loop["failed_page_count"] = 0
                loop["skipped_pages"] = [{"page_number": 1}, {"page_number": 2}]
                loop["skipped_page_count"] = 2
                loop_path.write_text(json.dumps(loop), encoding="utf-8")
                return {
                    "accepted": True,
                    "status": "accepted-after-page-repair",
                    "page_number": 1,
                    "score": 96.5,
                    "artifacts": {"attempt_dir": str(packet_dir / "attempts" / "attempt-001")},
                }

            with mock.patch("brochure_maker.exact_repair_orchestrator.run_page_repair_attempt", side_effect=fake_attempt) as repair:
                report = run_repair_orchestration(project_dir, repair_loop_path=loop_path)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["status"], "accepted")
            self.assertEqual(report["attempted_pages"], [1])
            self.assertEqual(report["attempt_count"], 1)
            self.assertEqual(report["escalation_count"], 0)
            self.assertTrue(repair.called)
            self.assertTrue((loop_path.parent / "repair-orchestration.json").exists())

    def test_orchestration_writes_escalation_for_still_failed_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, packet_dir = self._write_fixture(base, accepted=False)

            def fake_attempt(project_dir_arg, **kwargs):
                return {
                    "accepted": False,
                    "status": "needs-code-repair",
                    "page_number": 1,
                    "previous_score": 82.0,
                    "score": 91.0,
                    "packet_dir": str(packet_dir),
                    "artifacts": {"attempt_dir": str(packet_dir / "attempts" / "attempt-001")},
                }

            with mock.patch("brochure_maker.exact_repair_orchestrator.run_page_repair_attempt", side_effect=fake_attempt):
                report = run_repair_orchestration(project_dir, repair_loop_path=loop_path)

            self.assertFalse(report["accepted"])
            self.assertEqual(report["status"], "needs-reusable-code-repair")
            self.assertEqual(report["escalation_count"], 1)
            escalation = report["escalations"][0]
            self.assertEqual(escalation["page_number"], 1)
            self.assertEqual(escalation["subsystems"], ["typography"])
            self.assertIn("brochure_maker/pdf_exact_layout.py", escalation["suggested_files"])
            self.assertIn("Do not copy existing project HTML", " ".join(escalation["hardcoding_rules"]))
            self.assertTrue((packet_dir / "repair-escalation.json").exists())

    def test_orchestration_noops_for_accepted_loop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, _packet_dir = self._write_fixture(base, accepted=True)

            with mock.patch("brochure_maker.exact_repair_orchestrator.run_page_repair_attempt") as repair:
                report = run_repair_orchestration(project_dir, repair_loop_path=loop_path)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["status"], "accepted")
            self.assertEqual(report["attempt_count"], 0)
            self.assertFalse(repair.called)

    def test_orchestration_respects_max_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, _packet_dir = self._write_fixture(base, accepted=False, failed_pages=(1, 2))
            attempted: list[int] = []

            def fake_attempt(project_dir_arg, **kwargs):
                attempted.append(kwargs["page_number"])
                return {
                    "accepted": False,
                    "status": "needs-code-repair",
                    "page_number": kwargs["page_number"],
                    "score": 90.0,
                    "packet_dir": str(base / "reports" / "alpha123" / "pages" / f"page-{kwargs['page_number']:03d}"),
                    "artifacts": {},
                }

            with mock.patch("brochure_maker.exact_repair_orchestrator.run_page_repair_attempt", side_effect=fake_attempt):
                report = run_repair_orchestration(project_dir, repair_loop_path=loop_path, max_pages=1)

            self.assertEqual(attempted, [1])
            self.assertEqual(report["attempted_pages"], [1])

    def _write_fixture(
        self,
        base: Path,
        *,
        accepted: bool,
        failed_pages: tuple[int, ...] = (1,),
    ) -> tuple[Path, Path, Path]:
        project_dir = base / "projects" / "alpha123"
        project_dir.mkdir(parents=True)
        report_dir = base / "reports" / "alpha123"
        pages_dir = report_dir / "pages"
        queue = []
        packets = []
        for page in failed_pages:
            packet_dir = pages_dir / f"page-{page:03d}"
            packet_dir.mkdir(parents=True)
            repair_plan = packet_dir / "repair-plan.json"
            critique = packet_dir / "page-critique.json"
            attempts = packet_dir / "repair-attempts.json"
            repair_plan.write_text(
                json.dumps({"tasks": [{"repair_subsystem": "typography/title-grouping", "issue": "Title shifted"}]}),
                encoding="utf-8",
            )
            critique.write_text(json.dumps({"top_failures": [{"feature": "cover-title"}]}), encoding="utf-8")
            attempts.write_text(json.dumps({"attempts": []}), encoding="utf-8")
            packets.append({"page_number": page, "score": 82.0, "accepted": False, "packet_dir": str(packet_dir)})
            queue.append(
                {
                    "page_number": page,
                    "score": 82.0,
                    "packet_dir": str(packet_dir),
                    "critique": str(critique),
                    "repair_plan": str(repair_plan),
                    "repair_attempts": str(attempts),
                    "top_failures": [{"feature": "cover-title"}],
                    "tasks": [{"repair_subsystem": "typography/title-grouping", "issue": "Title shifted"}],
                }
            )
        page2_dir = pages_dir / "page-002"
        page2_dir.mkdir(parents=True, exist_ok=True)
        packets.append({"page_number": 2, "score": 97.0, "accepted": True, "packet_dir": str(page2_dir)})
        packets_path = report_dir / "page-packets.json"
        packets_path.parent.mkdir(parents=True, exist_ok=True)
        packets_path.write_text(
            json.dumps({"accepted": accepted, "failed_pages": [] if accepted else list(failed_pages), "packets": packets}),
            encoding="utf-8",
        )
        loop_path = report_dir / "repair-loop.json"
        loop_path.write_text(
            json.dumps(
                {
                    "accepted": accepted,
                    "status": "accepted" if accepted else "needs-focused-repair",
                    "project_id": "alpha123",
                    "project_dir": str(project_dir),
                    "page_packets": str(packets_path),
                    "repair_queue": [] if accepted else queue,
                    "skipped_pages": [{"page_number": 2, "score": 97.0}],
                    "failed_page_count": 0 if accepted else len(failed_pages),
                    "skipped_page_count": 1,
                }
            ),
            encoding="utf-8",
        )
        return project_dir, loop_path, pages_dir / "page-001"


if __name__ == "__main__":
    unittest.main()
