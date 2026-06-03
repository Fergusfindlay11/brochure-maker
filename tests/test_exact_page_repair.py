from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from brochure_maker.exact_page_repair import run_page_repair_attempt


class TestExactPageRepair(unittest.TestCase):
    def test_page_repair_attempt_renders_only_failed_page_and_merges_acceptance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, packet_dir = self._write_fixture(base, failed_score=88.25)
            calls: list[dict] = []

            with (
                mock.patch(
                    "brochure_maker.exact_page_repair.write_visual_diff_report",
                    side_effect=self._fake_visual_writer(base, score=96.25, calls=calls),
                ),
                mock.patch(
                    "brochure_maker.exact_page_repair.write_html_assessment",
                    side_effect=self._fake_assessment_writer(score=96.25, accepted=True),
                ),
            ):
                attempt = run_page_repair_attempt(project_dir, repair_loop_path=loop_path, page_number=1)

            self.assertTrue(attempt["accepted"])
            self.assertEqual(attempt["attempt_id"], "page-001-attempt-001")
            self.assertEqual(attempt["status"], "accepted-after-page-repair")
            self.assertTrue(attempt["repair_evidence"]["visual_output_changed"])
            self.assertEqual(set(attempt["repair_evidence"]["changed_render_outputs"]), {"generated", "diff"})
            self.assertTrue(attempt["inputs_read"])
            self.assertFalse(attempt["disallowed_checks"]["hardcoded_project_coordinates"])
            self.assertEqual(attempt["rendered_pages"], [1])
            self.assertEqual(attempt["passing_pages_untouched"], [2])
            self.assertEqual(calls[0]["page_numbers"], [1])
            attempts = json.loads((packet_dir / "repair-attempts.json").read_text(encoding="utf-8"))
            self.assertEqual(len(attempts["attempts"]), 1)
            packets = json.loads((base / "reports" / "demo" / "page-packets.json").read_text(encoding="utf-8"))
            self.assertTrue(packets["accepted"])
            self.assertEqual(packets["failed_pages"], [])
            self.assertEqual(packets["packets"][0]["score"], 96.25)
            self.assertTrue(packets["packets"][0]["accepted"])
            self.assertEqual(packets["packets"][1]["score"], 97.0)
            loop = json.loads(loop_path.read_text(encoding="utf-8"))
            self.assertTrue(loop["accepted"])
            self.assertEqual(loop["repair_queue"], [])
            self.assertEqual(loop["skipped_page_count"], 2)
            score = json.loads((packet_dir / "page-score.json").read_text(encoding="utf-8"))
            self.assertEqual(score["latest_attempt"], str(packet_dir / "attempts" / "attempt-001" / "page-repair-attempt.json"))
            self.assertTrue((packet_dir / "generated.png").exists())

    def test_page_repair_attempt_cannot_accept_unchanged_page_render(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, packet_dir = self._write_fixture(
                base,
                failed_score=88.25,
                initial_render_matches_attempt=True,
            )

            with (
                mock.patch(
                    "brochure_maker.exact_page_repair.write_visual_diff_report",
                    side_effect=self._fake_visual_writer(base, score=96.25, calls=[]),
                ),
                mock.patch(
                    "brochure_maker.exact_page_repair.write_html_assessment",
                    side_effect=self._fake_assessment_writer(score=96.25, accepted=True),
                ),
            ):
                attempt = run_page_repair_attempt(project_dir, repair_loop_path=loop_path, page_number=1)

            self.assertFalse(attempt["accepted"])
            self.assertTrue(attempt["raw_accepted"])
            self.assertEqual(attempt["status"], "needs-code-repair")
            self.assertFalse(attempt["repair_evidence"]["visual_output_changed"])
            self.assertTrue(attempt["repair_evidence"]["acceptance_blockers"])
            self.assertTrue(attempt["disallowed_checks"]["accepted_without_changed_render_output"])
            packets = json.loads((base / "reports" / "demo" / "page-packets.json").read_text(encoding="utf-8"))
            self.assertFalse(packets["accepted"])
            self.assertEqual(packets["failed_pages"], [1])
            attempts = json.loads((packet_dir / "repair-attempts.json").read_text(encoding="utf-8"))
            self.assertEqual(attempts["latest_attempt"]["score"], 96.25)
            self.assertFalse(attempts["latest_attempt"]["accepted"])

    def test_page_repair_attempt_cannot_accept_when_judgement_agents_still_reject(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, packet_dir = self._write_fixture(base, failed_score=88.25)
            (packet_dir / "editability-critic.json").write_text(
                json.dumps(
                    {
                        "agent": "editability-critic",
                        "accepted": False,
                        "blockers": ["Browser page has 0 image slots but PDF/design evidence expects 2."],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch(
                    "brochure_maker.exact_page_repair.write_visual_diff_report",
                    side_effect=self._fake_visual_writer(base, score=96.25, calls=[]),
                ),
                mock.patch(
                    "brochure_maker.exact_page_repair.write_html_assessment",
                    side_effect=self._fake_assessment_writer(score=96.25, accepted=True),
                ),
            ):
                attempt = run_page_repair_attempt(project_dir, repair_loop_path=loop_path, page_number=1)

            self.assertFalse(attempt["accepted"])
            self.assertTrue(attempt["raw_accepted"])
            self.assertFalse(attempt["repair_evidence"]["judgement_agent_accepted"])
            self.assertTrue(
                any("editability-critic" in blocker for blocker in attempt["repair_evidence"]["acceptance_blockers"])
            )
            packets = json.loads((base / "reports" / "demo" / "page-packets.json").read_text(encoding="utf-8"))
            self.assertFalse(packets["accepted"])
            self.assertEqual(packets["failed_pages"], [1])

    def test_page_repair_attempt_below_threshold_stays_queued(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, packet_dir = self._write_fixture(base, failed_score=82.0)

            with (
                mock.patch(
                    "brochure_maker.exact_page_repair.write_visual_diff_report",
                    side_effect=self._fake_visual_writer(base, score=91.0, calls=[]),
                ),
                mock.patch(
                    "brochure_maker.exact_page_repair.write_html_assessment",
                    side_effect=self._fake_assessment_writer(score=91.0, accepted=False),
                ),
            ):
                attempt = run_page_repair_attempt(project_dir, repair_loop_path=loop_path)

            self.assertFalse(attempt["accepted"])
            self.assertEqual(attempt["status"], "needs-code-repair")
            packets = json.loads((base / "reports" / "demo" / "page-packets.json").read_text(encoding="utf-8"))
            self.assertFalse(packets["accepted"])
            self.assertEqual(packets["failed_pages"], [1])
            self.assertEqual(packets["packets"][0]["band"], "focused repair")
            loop = json.loads(loop_path.read_text(encoding="utf-8"))
            self.assertFalse(loop["accepted"])
            self.assertEqual(loop["repair_queue"][0]["page_number"], 1)
            self.assertEqual(loop["repair_queue"][0]["attempt_count"], 1)
            attempts = json.loads((packet_dir / "repair-attempts.json").read_text(encoding="utf-8"))
            self.assertEqual(attempts["latest_attempt"]["score"], 91.0)

    def test_page_repair_attempt_applies_declared_page_patch_before_render(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, _packet_dir = self._write_fixture(
                base,
                failed_score=82.0,
                include_patch=True,
            )

            with (
                mock.patch(
                    "brochure_maker.exact_page_repair.write_visual_diff_report",
                    side_effect=self._fake_visual_writer(base, score=96.0, calls=[]),
                ),
                mock.patch(
                    "brochure_maker.exact_page_repair.write_html_assessment",
                    side_effect=self._fake_assessment_writer(score=96.0, accepted=True),
                ),
            ):
                attempt = run_page_repair_attempt(project_dir, repair_loop_path=loop_path, page_number=1)

            self.assertTrue(attempt["accepted"])
            self.assertTrue(attempt["patch_application"]["applied"])
            self.assertTrue(Path(attempt["artifacts"]["patch_application"]).exists())
            state = json.loads((project_dir / "editor_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["editableTexts"]["exact-page1-text1"]["layout"]["styles"]["left"], "42px")

    def test_page_repair_attempt_noops_when_queue_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo"
            project_dir.mkdir(parents=True)
            loop_path = base / "reports" / "demo" / "repair-loop.json"
            loop_path.parent.mkdir(parents=True)
            loop_path.write_text(json.dumps({"accepted": True, "repair_queue": []}), encoding="utf-8")

            attempt = run_page_repair_attempt(project_dir, repair_loop_path=loop_path, page_number=1)

            self.assertEqual(attempt["status"], "no-op")
            self.assertTrue(attempt["accepted"])

    def test_page_repair_attempt_respects_retry_limit_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, loop_path, _packet_dir = self._write_fixture(base, failed_score=70.0)
            loop = json.loads(loop_path.read_text(encoding="utf-8"))
            loop["repair_queue"][0]["status"] = "retry-limit-reached"
            loop_path.write_text(json.dumps(loop), encoding="utf-8")

            with mock.patch("brochure_maker.exact_page_repair.write_visual_diff_report") as visual:
                attempt = run_page_repair_attempt(project_dir, repair_loop_path=loop_path, page_number=1)

            self.assertEqual(attempt["status"], "blocked")
            self.assertFalse(attempt["accepted"])
            self.assertFalse(visual.called)

    def _write_fixture(
        self,
        base: Path,
        *,
        failed_score: float,
        initial_render_matches_attempt: bool = False,
        include_patch: bool = False,
    ) -> tuple[Path, Path, Path]:
        project_dir = base / "projects" / "demo"
        project_dir.mkdir(parents=True)
        (project_dir / "brochure.html").write_text(
            '<p class="pdf-text" data-save-id="exact-page1-text1" data-typography-role="body">Original</p>',
            encoding="utf-8",
        )
        report_dir = base / "reports" / "demo"
        packet_dir = report_dir / "pages" / "page-001"
        page2_dir = report_dir / "pages" / "page-002"
        packet_dir.mkdir(parents=True)
        page2_dir.mkdir(parents=True)
        repair_plan = packet_dir / "repair-plan.json"
        critique = packet_dir / "page-critique.json"
        attempts = packet_dir / "repair-attempts.json"
        repair_plan.write_text(
            json.dumps({"tasks": [{"repair_subsystem": "typography/title-grouping", "issue": "Title shifted"}]}),
            encoding="utf-8",
        )
        critique.write_text(
            json.dumps({"top_failures": [{"feature": "cover-title", "problem": "Title shifted"}]}),
            encoding="utf-8",
        )
        attempts.write_text(json.dumps({"attempts": []}), encoding="utf-8")
        if include_patch:
            (packet_dir / "page-repair-patch.json").write_text(
                json.dumps(
                    {
                        "schema": "brochure-maker.page-repair-patch.v1",
                        "page_number": 1,
                        "operations": [
                            {
                                "type": "text-layout",
                                "save_id": "exact-page1-text1",
                                "styles": {"left": "42px", "top": "24px", "width": "180px"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        generated_colour = "#eeeeee" if initial_render_matches_attempt else "#222222"
        diff_colour = "#111111" if initial_render_matches_attempt else "#333333"
        for filename, colour in (
            ("source.png", "#ffffff"),
            ("generated.png", generated_colour),
            ("diff.png", diff_colour),
        ):
            Image.new("RGB", (10, 10), colour).save(packet_dir / filename)
        packets_path = report_dir / "page-packets.json"
        packets_path.write_text(
            json.dumps(
                {
                    "accepted": False,
                    "failed_pages": [1],
                    "packets": [
                        {
                            "page_number": 1,
                            "score": failed_score,
                            "accepted": False,
                            "band": "focused repair",
                            "packet_dir": str(packet_dir),
                            "critique": str(critique),
                            "repair_plan": str(repair_plan),
                        },
                        {
                            "page_number": 2,
                            "score": 97.0,
                            "accepted": True,
                            "band": "pass",
                            "packet_dir": str(page2_dir),
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        loop_path = report_dir / "repair-loop.json"
        loop_path.write_text(
            json.dumps(
                {
                    "accepted": False,
                    "status": "needs-focused-repair",
                    "project_id": "demo",
                    "project_dir": str(project_dir),
                    "page_packets": str(packets_path),
                    "max_attempts": 3,
                    "skipped_pages": [{"page_number": 2, "score": 97.0}],
                    "repair_queue": [
                        {
                            "page_number": 1,
                            "score": failed_score,
                            "band": "focused repair",
                            "packet_dir": str(packet_dir),
                            "status": "needs-focused-repair",
                            "critique": str(critique),
                            "repair_plan": str(repair_plan),
                            "repair_attempts": str(attempts),
                            "attempt_count": 0,
                            "top_failures": [{"feature": "cover-title", "problem": "Title shifted"}],
                            "tasks": [{"repair_subsystem": "typography/title-grouping", "issue": "Title shifted"}],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return project_dir, loop_path, packet_dir

    def _fake_visual_writer(self, base: Path, *, score: float, calls: list[dict]):
        def fake(project_dir: Path, output_dir: Path, **kwargs):
            calls.append(dict(kwargs))
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            image_paths = {}
            for kind, colour in (("original", "#ffffff"), ("generated", "#eeeeee"), ("diff", "#111111")):
                path = output / f"page-001-{kind}.png"
                Image.new("RGB", (10, 10), colour).save(path)
                image_paths[kind] = str(path)
            report = {
                "score": score,
                "accepted": score >= 95,
                "pages": [
                    {
                        "page_number": 1,
                        "score": score,
                        "mean_absolute_error": 1.2,
                        **image_paths,
                    }
                ],
            }
            path = output / "visual-diff.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            return path

        return fake

    def _fake_assessment_writer(self, *, score: float, accepted: bool):
        def fake(project_dir: Path, output_path: Path, **kwargs):
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "score": score,
                        "accepted": accepted,
                        "pages": [{"page_number": 1, "score": score, "accepted": accepted, "findings": []}],
                    }
                ),
                encoding="utf-8",
            )
            return path

        return fake


if __name__ == "__main__":
    unittest.main()
