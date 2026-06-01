from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_repair_loop import build_repair_loop, write_repair_loop


class TestExactRepairLoop(unittest.TestCase):
    def test_failed_pages_are_queued_and_passing_pages_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_repair_fixture(base, include_failed=True)

            loop = build_repair_loop(project_dir, page_packets_path=packets_path, output_path=base / "repair-loop.json")

            self.assertFalse(loop["accepted"])
            self.assertEqual(loop["status"], "needs-focused-repair")
            self.assertEqual(loop["failed_page_count"], 1)
            self.assertEqual(loop["skipped_page_count"], 1)
            self.assertEqual(loop["repair_queue"][0]["page_number"], 1)
            self.assertEqual(loop["repair_queue"][0]["status"], "needs-focused-repair")
            self.assertEqual(loop["repair_queue"][0]["tasks"][0]["repair_subsystem"], "typography/title-grouping")
            self.assertEqual(loop["repair_queue"][0]["top_failures"][0]["feature"], "cover-title")
            self.assertEqual(loop["skipped_pages"][0]["page_number"], 2)
            self.assertTrue(loop["skipped_pages"][0]["critique"].endswith("page-002/page-critique.json"))
            self.assertIn("repair page 1", loop["next_action"])
            self.assertEqual(loop["blockers"], [])

    def test_all_passing_pages_accept_and_do_not_enter_repair_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_repair_fixture(base, include_failed=False)

            loop_path = write_repair_loop(project_dir, page_packets_path=packets_path, output_path=base / "repair-loop.json")
            loop = json.loads(loop_path.read_text(encoding="utf-8"))

            self.assertTrue(loop["accepted"])
            self.assertEqual(loop["status"], "accepted")
            self.assertEqual(loop["repair_queue"], [])
            self.assertEqual([page["page_number"] for page in loop["skipped_pages"]], [1, 2])
            self.assertEqual(loop["next_action"], "none")

    def test_judgement_rejected_page_is_queued_even_when_numeric_packet_passed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, packets_path = self._write_repair_fixture(base, include_failed=False)
            packets = json.loads(packets_path.read_text(encoding="utf-8"))
            page1 = packets["packets"][0]
            packet_dir = Path(page1["packet_dir"])
            plan_path = packet_dir / "repair-plan.json"
            attempts_path = packet_dir / "repair-attempts.json"
            plan_path.write_text(
                json.dumps({"tasks": [{"repair_subsystem": "images/image-region-classifier"}]}),
                encoding="utf-8",
            )
            attempts_path.write_text(json.dumps({"attempts": []}), encoding="utf-8")
            page1["judgement_agents"] = {
                "accepted": False,
                "agents": {"editability-critic": {"accepted": False}},
                "blockers": ["editability-critic: missing image slot"],
            }
            page1["repair_plan"] = str(plan_path)
            packets_path.write_text(json.dumps(packets), encoding="utf-8")

            loop = build_repair_loop(project_dir, page_packets_path=packets_path, output_path=base / "repair-loop.json")

            self.assertFalse(loop["accepted"])
            self.assertEqual(loop["repair_queue"][0]["page_number"], 1)
            self.assertEqual(loop["repair_queue"][0]["tasks"][0]["repair_subsystem"], "images/image-region-classifier")
            self.assertEqual([page["page_number"] for page in loop["skipped_pages"]], [2])

    def test_missing_failed_page_artifacts_create_blockers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo"
            project_dir.mkdir(parents=True)
            packet_dir = base / "reports" / "demo" / "pages" / "page-001"
            packet_dir.mkdir(parents=True)
            packets_path = base / "reports" / "demo" / "page-packets.json"
            packets_path.write_text(
                json.dumps(
                    {
                        "packets": [
                            {
                                "page_number": 1,
                                "score": 82.0,
                                "accepted": False,
                                "band": "verifier agent required",
                                "packet_dir": str(packet_dir),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            loop = build_repair_loop(project_dir, page_packets_path=packets_path, output_path=base / "repair-loop.json")

            self.assertFalse(loop["accepted"])
            self.assertIn("Page 1 is failed but has no page-critique.json", loop["blockers"])
            self.assertIn("Page 1 is failed but has no repair-plan.json", loop["blockers"])
            self.assertEqual(loop["repair_queue"][0]["tasks"], [])
            self.assertIn("repair page 1", loop["next_action"])

    def _write_repair_fixture(self, base: Path, *, include_failed: bool) -> tuple[Path, Path]:
        project_dir = base / "projects" / "demo"
        project_dir.mkdir(parents=True)
        reports_dir = base / "reports" / "demo"
        pages_dir = reports_dir / "pages"
        packets: list[dict[str, object]] = []
        for page_number, accepted, score in (
            (1, not include_failed, 88.25 if include_failed else 97.0),
            (2, True, 96.5),
        ):
            packet_dir = pages_dir / f"page-{page_number:03d}"
            packet_dir.mkdir(parents=True)
            packet: dict[str, object] = {
                "page_number": page_number,
                "score": score,
                "accepted": accepted,
                "band": "pass" if accepted else "focused repair",
                "packet_dir": str(packet_dir),
            }
            if not accepted:
                critique_path = packet_dir / "page-critique.json"
                plan_path = packet_dir / "repair-plan.json"
                attempts_path = packet_dir / "repair-attempts.json"
                critique_path.write_text(
                    json.dumps(
                        {
                            "top_failures": [
                                {
                                    "feature": "cover-title",
                                    "problem": "Cover title is shifted",
                                    "likely_cause": "Title grouping mismatch",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                plan_path.write_text(
                    json.dumps(
                        {
                            "tasks": [
                                {
                                    "repair_subsystem": "typography/title-grouping",
                                    "issue": "Cover title is shifted",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                attempts_path.write_text(json.dumps({"attempts": []}), encoding="utf-8")
                packet["critique"] = str(critique_path)
                packet["repair_plan"] = str(plan_path)
            else:
                critique_path = packet_dir / "page-critique.json"
                critique_path.write_text(
                    json.dumps(
                        {
                            "accepted": True,
                            "status": "accepted",
                            "pass_rationale": "Page meets the 95 score gate.",
                        }
                    ),
                    encoding="utf-8",
                )
                packet["critique"] = str(critique_path)
            packets.append(packet)
        packets_path = reports_dir / "page-packets.json"
        packets_path.write_text(json.dumps({"packets": packets}), encoding="utf-8")
        return project_dir, packets_path


if __name__ == "__main__":
    unittest.main()
