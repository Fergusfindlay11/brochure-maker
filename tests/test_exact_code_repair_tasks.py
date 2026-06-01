from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_code_repair_tasks import build_code_repair_tasks, write_code_repair_tasks


class TestExactCodeRepairTasks(unittest.TestCase):
    def test_accepted_pages_create_optional_reusable_tasks_from_residual_findings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_fixture(base, accepted=True, residual=True)

            report = build_code_repair_tasks(project_dir, page_packets_path=packets_path)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["required_task_count"], 0)
            self.assertEqual(report["optional_task_count"], 1)
            task = report["tasks"][0]
            self.assertEqual(task["subsystem"], "typography/text-layout")
            self.assertFalse(task["required_for_acceptance"])
            self.assertEqual(task["pages"], [1])
            self.assertIn("brochure_maker/pdf_exact_layout.py", task["suggested_files"])
            self.assertIn("page packet(s) 1", task["worker_prompt"])
            self.assertTrue(task["hardcoding_rules"])

    def test_task_pages_are_deduped_for_page_worker_prompts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_fixture(base, accepted=True, residual=True)
            packet_dir = base / "reports" / "demo" / "pages" / "page-001"
            critique_path = packet_dir / "page-critique.json"
            critique = json.loads(critique_path.read_text(encoding="utf-8"))
            critique["residual_findings"].append(
                {
                    "severity": "minor",
                    "issue": "Another typography drift near body",
                    "repair_subsystem": "typography/text-layout",
                    "roles": ["body"],
                }
            )
            critique_path.write_text(json.dumps(critique), encoding="utf-8")

            report = build_code_repair_tasks(project_dir, page_packets_path=packets_path)

            task = report["tasks"][0]
            self.assertEqual(task["pages"], [1])
            self.assertIn("page packet(s) 1", task["worker_prompt"])
            self.assertNotIn("1, 1", task["worker_prompt"])

    def test_failed_page_creates_required_reusable_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_fixture(base, accepted=False, residual=False)

            report = build_code_repair_tasks(project_dir, page_packets_path=packets_path)

            self.assertFalse(report["accepted"])
            self.assertEqual(report["required_task_count"], 1)
            task = report["tasks"][0]
            self.assertTrue(task["required_for_acceptance"])
            self.assertEqual(task["status"], "required-reusable-code-repair")
            self.assertEqual(task["severity"], "major")
            self.assertIn("Do not copy", " ".join(task["hardcoding_rules"]))

    def test_minor_browser_overlap_residual_is_optional_on_passing_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_fixture(base, accepted=True, residual=False)
            packet_dir = base / "reports" / "demo" / "pages" / "page-001"
            critique_path = packet_dir / "page-critique.json"
            critique = json.loads(critique_path.read_text(encoding="utf-8"))
            critique["residual_findings"].append(
                {
                    "severity": "minor",
                    "issue": "Browser layout audit found possible overlapping editable text blocks",
                    "repair_subsystem": "typography/text-layout",
                    "roles": ["body"],
                }
            )
            critique_path.write_text(json.dumps(critique), encoding="utf-8")

            report = build_code_repair_tasks(project_dir, page_packets_path=packets_path)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["required_task_count"], 0)
            self.assertEqual(report["optional_task_count"], 1)
            task = report["tasks"][0]
            self.assertFalse(task["required_for_acceptance"])
            self.assertEqual(task["status"], "optional-improvement")
            self.assertEqual(task["subsystem"], "typography/text-layout")

    def test_major_browser_overlap_residual_blocks_final_acceptance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_fixture(base, accepted=True, residual=False)
            packet_dir = base / "reports" / "demo" / "pages" / "page-001"
            critique_path = packet_dir / "page-critique.json"
            critique = json.loads(critique_path.read_text(encoding="utf-8"))
            critique["residual_findings"].append(
                {
                    "severity": "major",
                    "issue": "Browser layout audit found possible overlapping editable text blocks",
                    "repair_subsystem": "typography/text-layout",
                    "roles": ["body"],
                }
            )
            critique_path.write_text(json.dumps(critique), encoding="utf-8")

            report = build_code_repair_tasks(project_dir, page_packets_path=packets_path)

            self.assertFalse(report["accepted"])
            self.assertEqual(report["required_task_count"], 1)
            task = report["tasks"][0]
            self.assertTrue(task["required_for_acceptance"])
            self.assertEqual(task["status"], "required-reusable-code-repair")
            self.assertEqual(task["subsystem"], "typography/text-layout")

    def test_agency_logo_source_fidelity_residual_blocks_final_acceptance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_fixture(base, accepted=True, residual=False)
            packet_dir = base / "reports" / "demo" / "pages" / "page-001"
            critique_path = packet_dir / "page-critique.json"
            critique = json.loads(critique_path.read_text(encoding="utf-8"))
            critique["residual_findings"].append(
                {
                    "severity": "minor",
                    "issue": "Agency logo needs visual source fidelity, not just a text fallback",
                    "repair_subsystem": "contacts/agency-logo",
                    "roles": ["agency-logo"],
                }
            )
            critique_path.write_text(json.dumps(critique), encoding="utf-8")

            report = build_code_repair_tasks(project_dir, page_packets_path=packets_path)

            self.assertFalse(report["accepted"])
            self.assertEqual(report["required_task_count"], 1)
            self.assertEqual(report["tasks"][0]["subsystem"], "contacts/agency-logo")

    def test_missing_critique_blocks_task_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo"
            project_dir.mkdir(parents=True)
            packets_path = base / "reports" / "demo" / "page-packets.json"
            packets_path.parent.mkdir(parents=True)
            packets_path.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "page_number": 1,
                                "score": 97.0,
                                "accepted": True,
                                "packet_dir": str(base / "reports" / "demo" / "pages" / "page-001"),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = build_code_repair_tasks(project_dir, page_packets_path=packets_path)

            self.assertFalse(report["accepted"])
            self.assertIn("Page 1 has no readable critique", report["blockers"][0])

    def test_write_code_repair_tasks_returns_report_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_fixture(base, accepted=True, residual=False)

            path = write_code_repair_tasks(project_dir, page_packets_path=packets_path, output_path=base / "tasks.json")

            self.assertEqual(path, (base / "tasks.json").resolve())
            self.assertTrue(path.exists())

    def _write_fixture(self, base: Path, *, accepted: bool, residual: bool) -> tuple[Path, Path]:
        project_dir = base / "projects" / "demo"
        project_dir.mkdir(parents=True)
        packet_dir = base / "reports" / "demo" / "pages" / "page-001"
        packet_dir.mkdir(parents=True)
        critique = {
            "accepted": accepted,
            "needs_repair": not accepted,
            "top_failures": [],
            "residual_findings": [],
        }
        if residual:
            critique["residual_findings"].append(
                {
                    "severity": "minor",
                    "issue": "Large visual mismatch near body",
                    "repair_subsystem": "typography/text-layout",
                    "roles": ["body"],
                }
            )
        if not accepted:
            critique["top_failures"].append(
                {
                    "severity": "major",
                    "feature": "cover-title",
                    "problem": "Title grouping mismatch",
                    "repair_task": "typography/title-grouping",
                }
            )
        critique_path = packet_dir / "page-critique.json"
        critique_path.write_text(json.dumps(critique), encoding="utf-8")
        packets_path = base / "reports" / "demo" / "page-packets.json"
        packets_path.write_text(
            json.dumps(
                {
                    "packets": [
                        {
                            "page_number": 1,
                            "score": 97.0 if accepted else 88.0,
                            "accepted": accepted,
                            "packet_dir": str(packet_dir),
                            "critique": str(critique_path),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return project_dir, packets_path


if __name__ == "__main__":
    unittest.main()
