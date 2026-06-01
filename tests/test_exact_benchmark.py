from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from brochure_maker.exact_benchmark import EXACT_UPLOAD_TIMEOUT_SECONDS, run_exact_benchmark, upload_exact_pdf


class TestExactBenchmark(unittest.TestCase):
    def test_run_exact_benchmark_orchestrates_reports_for_existing_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo1234"
            project_dir.mkdir(parents=True)
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"assertions": self._passing_browser_assertions()}),
                encoding="utf-8",
            )
            output_root = base / "evals" / "reports"

            def fake_visual(project, output_dir, **kwargs):
                path = Path(output_dir) / "visual-diff.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"score": 96.5, "accepted": True, "blockers": []}), encoding="utf-8")
                return path

            def fake_assessment(project, output, **kwargs):
                Path(output).write_text(
                    json.dumps(
                        {
                            "score": 96.5,
                            "accepted": True,
                            "blockers": [],
                            "next_repair_tasks": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return Path(output)

            def fake_packets(project, **kwargs):
                return self._write_packets_with_critique(output_root, "demo1234", score=96.5)

            def fake_eval(project, output, **kwargs):
                Path(output).write_text(json.dumps({"score": 100, "accepted": True, "blockers": []}), encoding="utf-8")
                return Path(output)

            with (
                mock.patch("brochure_maker.exact_benchmark.write_visual_diff_report", side_effect=fake_visual),
                mock.patch("brochure_maker.exact_benchmark.attach_visual_diff_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_html_assessment", side_effect=fake_assessment),
                mock.patch("brochure_maker.exact_benchmark.attach_html_assessment_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_page_packets", side_effect=fake_packets),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_dispatch", side_effect=lambda project, code_repair_tasks_path, output_dir, **kwargs: self._write_dispatch(output_root, "demo1234")),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_execution", side_effect=lambda project, dispatch_path, output_path, **kwargs: self._write_execution(output_root, "demo1234")),
                mock.patch("brochure_maker.exact_benchmark.write_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_eval_report", side_effect=fake_eval),
            ):
                report = run_exact_benchmark(project_dir=project_dir, output_root=output_root)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["project_id"], "demo1234")
            self.assertEqual(report["scores"]["visual"], 96.5)
            self.assertEqual(report["scores"]["eval"], 100)
            self.assertEqual(report["scores"]["critic"], 96.5)
            self.assertEqual(report["failed_pages"], [])
            self.assertTrue((output_root / "demo1234" / "benchmark-run.json").exists())
            self.assertTrue((output_root / "demo1234" / "critic-report.json").exists())
            self.assertTrue((output_root / "demo1234" / "repair-orchestration.json").exists())
            self.assertTrue(report["repair_orchestration"]["accepted"])
            self.assertIn("browser_http_qa", report)
            self.assertIn("browser_ui_qa", report)
            self.assertIn("html_export", report)
            self.assertIn("pdf_export", report)
            self.assertTrue(report["browser_ui_qa"]["accepted"])
            self.assertTrue(report["html_export"])
            self.assertTrue(report["pdf_export"])
            self.assertIn("browser_ui_qa", report["artifacts"])
            self.assertIn("export_qa", report["artifacts"])
            self.assertIn("control_quality", report["artifacts"])
            self.assertTrue((output_root / "latest-benchmark.json").exists())
            self.assertTrue((output_root / "latest.json").exists())

    def test_run_exact_benchmark_requires_browser_gate_assertions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo-browser-gate"
            project_dir.mkdir(parents=True)
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"assertions": {"global_controls_visible": False}}),
                encoding="utf-8",
            )
            output_root = base / "reports"

            def write_json(path: Path, payload: dict):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
                return path

            with (
                mock.patch("brochure_maker.exact_benchmark.write_visual_diff_report", side_effect=lambda project, output_dir, **kwargs: write_json(Path(output_dir) / "visual-diff.json", {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_visual_diff_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_html_assessment", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_html_assessment_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_page_packets", side_effect=lambda project, **kwargs: self._write_packets_with_critique(output_root, "demo-browser-gate", score=97)),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_dispatch", side_effect=lambda project, code_repair_tasks_path, output_dir, **kwargs: self._write_dispatch(output_root, "demo-browser-gate")),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_execution", side_effect=lambda project, dispatch_path, output_path, **kwargs: self._write_execution(output_root, "demo-browser-gate")),
                mock.patch("brochure_maker.exact_benchmark.write_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_eval_report", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 100, "accepted": True, "blockers": []})),
            ):
                report = run_exact_benchmark(project_dir=project_dir, output_root=output_root)

            self.assertFalse(report["accepted"])
            self.assertIn("Missing or false Browser assertion: global_controls_visible", report["blockers"])
            self.assertIn("Missing or false Browser assertion: editor_has_expected_pages", report["blockers"])

    def test_run_exact_benchmark_fresh_upload_uses_returned_project_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_root = base / "reports"
            projects_dir = base / "projects"
            project_dir = projects_dir / "fresh999"
            project_dir.mkdir(parents=True)
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"assertions": self._passing_browser_assertions()}),
                encoding="utf-8",
            )

            def fake_write(path, payload):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(json.dumps(payload), encoding="utf-8")
                return Path(path)

            with (
                mock.patch("brochure_maker.exact_benchmark.upload_exact_pdf", return_value={"project_id": "fresh999", "status": "complete"}),
                mock.patch("brochure_maker.exact_benchmark.write_visual_diff_report", side_effect=lambda project, output_dir, **kwargs: fake_write(Path(output_dir) / "visual-diff.json", {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_visual_diff_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_html_assessment", side_effect=lambda project, output, **kwargs: fake_write(output, {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_html_assessment_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_page_packets", side_effect=lambda project, **kwargs: self._write_packets_with_critique(output_root, "fresh999", score=97)),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_dispatch", side_effect=lambda project, code_repair_tasks_path, output_dir, **kwargs: self._write_dispatch(output_root, "fresh999")),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_execution", side_effect=lambda project, dispatch_path, output_path, **kwargs: self._write_execution(output_root, "fresh999")),
                mock.patch("brochure_maker.exact_benchmark.write_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_eval_report", side_effect=lambda project, output, **kwargs: fake_write(output, {"score": 100, "accepted": True, "blockers": []})),
            ):
                report = run_exact_benchmark(
                    pdf_path=base / "source.pdf",
                    projects_dir=projects_dir,
                    output_root=output_root,
                )

            self.assertEqual(report["project_id"], "fresh999")
            self.assertEqual(report["project_dir"], str(project_dir.resolve()))
            self.assertEqual(report["fresh_upload"]["status"], "complete")
            self.assertEqual(report["source_mode"], "fresh-upload")
            self.assertTrue((project_dir / "exact_benchmark_upload.json").exists())
            self.assertTrue((output_root / "fresh999" / "upload-response.json").exists())

            with (
                mock.patch("brochure_maker.exact_benchmark.write_visual_diff_report", side_effect=lambda project, output_dir, **kwargs: fake_write(Path(output_dir) / "visual-diff.json", {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_visual_diff_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_html_assessment", side_effect=lambda project, output, **kwargs: fake_write(output, {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_html_assessment_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_page_packets", side_effect=lambda project, **kwargs: self._write_packets_with_critique(output_root, "fresh999", score=97)),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_dispatch", side_effect=lambda project, code_repair_tasks_path, output_dir, **kwargs: self._write_dispatch(output_root, "fresh999")),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_execution", side_effect=lambda project, dispatch_path, output_path, **kwargs: self._write_execution(output_root, "fresh999")),
                mock.patch("brochure_maker.exact_benchmark.write_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_eval_report", side_effect=lambda project, output, **kwargs: fake_write(output, {"score": 100, "accepted": True, "blockers": []})),
            ):
                rerun = run_exact_benchmark(project_dir=project_dir, output_root=output_root)

            self.assertEqual(rerun["fresh_upload"]["project_id"], "fresh999")
            self.assertEqual(rerun["source_mode"], "fresh-upload")

    def test_run_exact_benchmark_critic_blocks_project_specific_production_literals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo-hardcode"
            project_dir.mkdir(parents=True)
            (base / "brochure_maker").mkdir()
            (base / "brochure_maker" / "hardcoded.py").write_text(
                'PROJECT_ID = "demo-hardcode"\n',
                encoding="utf-8",
            )
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"assertions": self._passing_browser_assertions()}),
                encoding="utf-8",
            )
            output_root = base / "reports"

            def write_json(path: Path, payload: dict):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
                return path

            with (
                mock.patch("brochure_maker.exact_benchmark.write_visual_diff_report", side_effect=lambda project, output_dir, **kwargs: write_json(Path(output_dir) / "visual-diff.json", {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_visual_diff_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_html_assessment", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_html_assessment_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_page_packets", side_effect=lambda project, **kwargs: self._write_packets_with_critique(output_root, "demo-hardcode", score=97)),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_dispatch", side_effect=lambda project, code_repair_tasks_path, output_dir, **kwargs: self._write_dispatch(output_root, "demo-hardcode")),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_execution", side_effect=lambda project, dispatch_path, output_path, **kwargs: self._write_execution(output_root, "demo-hardcode")),
                mock.patch("brochure_maker.exact_benchmark.write_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_eval_report", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 100, "accepted": True, "blockers": []})),
            ):
                report = run_exact_benchmark(project_dir=project_dir, output_root=output_root)

            self.assertFalse(report["accepted"])
            self.assertIn("Reusable code contains project/source-specific literal matches", report["blockers"])
            self.assertEqual(report["scores"]["critic"], 0.0)
            critic = json.loads((output_root / "demo-hardcode" / "critic-report.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(critic["hardcoding_scan"]["match_count"], 1)

    def test_hardcoding_scan_ignores_generic_source_name_tokens(self):
        from brochure_maker.exact_benchmark import scan_reusable_code_for_project_literals

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "anchor-demo"
            project_dir.mkdir(parents=True)
            (base / "brochure_maker").mkdir()
            (base / "brochure_maker" / "generic.py").write_text(
                "def place_label(anchor):\n"
                "    try:\n"
                "        return 'warehouse'\n"
                "    finally:\n"
                "        pass\n",
                encoding="utf-8",
            )
            (project_dir / "exact_metadata.json").write_text(
                json.dumps(
                    {
                        "filename": "1-2370-anchor-house-brochure-final.pdf",
                        "brochure_name": "1-2370-anchor-house-brochure-final",
                    }
                ),
                encoding="utf-8",
            )

            scan = scan_reusable_code_for_project_literals(
                project_dir,
                "anchor-demo",
                fresh_upload={
                    "project_id": "anchor-demo",
                    "brochure_name": "1-2370-anchor-house-brochure-final",
                },
            )

        self.assertEqual(scan["matches"], [])

    def test_hardcoding_scan_ignores_exact_upload_source_pdf_name(self):
        from brochure_maker.exact_benchmark import scan_reusable_code_for_project_literals

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "fresh-demo"
            project_dir.mkdir(parents=True)
            (base / "brochure_maker").mkdir()
            (base / "brochure_maker" / "pipeline.py").write_text(
                "pdf_path = project_dir / 'source.pdf'\n"
                "def read_source(source):\n"
                "    return source.read_bytes()\n",
                encoding="utf-8",
            )
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"filename": "source.pdf", "brochure_name": "source"}),
                encoding="utf-8",
            )

            scan = scan_reusable_code_for_project_literals(
                project_dir,
                "fresh-demo",
                fresh_upload={"project_id": "fresh-demo", "brochure_name": "source"},
            )

        self.assertIn("fresh-demo", scan["terms"])
        self.assertNotIn("source", scan["terms"])
        self.assertNotIn("source.pdf", scan["terms"])
        self.assertEqual(scan["matches"], [])

    def test_hardcoding_scan_ignores_generic_transport_tokens_but_not_full_source_name(self):
        from brochure_maker.exact_benchmark import scan_reusable_code_for_project_literals

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "hammersmith-demo"
            project_dir.mkdir(parents=True)
            (base / "brochure_maker").mkdir()
            (base / "brochure_maker" / "maps.py").write_text(
                "TRANSIT_LINES = ['circle', 'hammersmith', 'metropolitan']\n"
                "COPIED = '255_Hammersmith_Road_London_-_Final.pdf'\n",
                encoding="utf-8",
            )
            (project_dir / "exact_metadata.json").write_text(
                json.dumps(
                    {
                        "filename": "255_Hammersmith_Road_London_-_Final.pdf",
                        "brochure_name": "255_Hammersmith_Road_London_-_Final",
                    }
                ),
                encoding="utf-8",
            )

            scan = scan_reusable_code_for_project_literals(
                project_dir,
                "hammersmith-demo",
                fresh_upload={
                    "project_id": "hammersmith-demo",
                    "brochure_name": "255_Hammersmith_Road_London_-_Final",
                },
            )

        self.assertNotIn("Hammersmith", scan["terms"])
        self.assertTrue(any(match["term"] == "255_Hammersmith_Road_London_-_Final.pdf" for match in scan["matches"]))
        self.assertFalse(any(match["term"].lower() == "hammersmith" for match in scan["matches"]))

    def test_run_exact_benchmark_requires_page_critique_for_every_packet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo-missing-critique"
            project_dir.mkdir(parents=True)
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"assertions": self._passing_browser_assertions()}),
                encoding="utf-8",
            )
            output_root = base / "reports"

            def write_json(path: Path, payload: dict):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
                return path

            with (
                mock.patch("brochure_maker.exact_benchmark.write_visual_diff_report", side_effect=lambda project, output_dir, **kwargs: write_json(Path(output_dir) / "visual-diff.json", {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_visual_diff_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_html_assessment", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_html_assessment_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_page_packets", side_effect=lambda project, **kwargs: write_json(output_root / "demo-missing-critique" / "page-packets.json", {"accepted": True, "failed_pages": [], "packets": [{"page_number": 1, "score": 97, "accepted": True}]})),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_dispatch", side_effect=lambda project, code_repair_tasks_path, output_dir, **kwargs: self._write_dispatch(output_root, "demo-missing-critique")),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_execution", side_effect=lambda project, dispatch_path, output_path, **kwargs: self._write_execution(output_root, "demo-missing-critique")),
                mock.patch("brochure_maker.exact_benchmark.write_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_eval_report", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 100, "accepted": True, "blockers": []})),
            ):
                report = run_exact_benchmark(project_dir=project_dir, output_root=output_root)

            self.assertFalse(report["accepted"])
            self.assertIn("Page 1 is missing a page critique path", report["blockers"])

    def test_run_exact_benchmark_requires_complete_page_packet_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo-missing-packet-files"
            project_dir.mkdir(parents=True)
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"assertions": self._passing_browser_assertions()}),
                encoding="utf-8",
            )
            output_root = base / "reports"

            def write_json(path: Path, payload: dict):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
                return path

            def fake_packets(project, **kwargs):
                page_dir = output_root / "demo-missing-packet-files" / "pages" / "page-001"
                page_dir.mkdir(parents=True, exist_ok=True)
                critique_path = page_dir / "page-critique.json"
                critique_path.write_text(json.dumps({"accepted": True, "rubric": [{"category": "typography"}]}), encoding="utf-8")
                packets = output_root / "demo-missing-packet-files" / "page-packets.json"
                packets.write_text(
                    json.dumps(
                        {
                            "accepted": True,
                            "failed_pages": [],
                            "packets": [
                                {
                                    "page_number": 1,
                                    "score": 97,
                                    "accepted": True,
                                    "packet_dir": str(page_dir),
                                    "critique": str(critique_path),
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return packets

            with (
                mock.patch("brochure_maker.exact_benchmark.write_visual_diff_report", side_effect=lambda project, output_dir, **kwargs: write_json(Path(output_dir) / "visual-diff.json", {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_visual_diff_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_html_assessment", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_html_assessment_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_page_packets", side_effect=fake_packets),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_dispatch", side_effect=lambda project, code_repair_tasks_path, output_dir, **kwargs: self._write_dispatch(output_root, "demo-missing-packet-files")),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_execution", side_effect=lambda project, dispatch_path, output_path, **kwargs: self._write_execution(output_root, "demo-missing-packet-files")),
                mock.patch("brochure_maker.exact_benchmark.write_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_eval_report", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 100, "accepted": True, "blockers": []})),
            ):
                report = run_exact_benchmark(project_dir=project_dir, output_root=output_root)

            self.assertFalse(report["accepted"])
            self.assertTrue(
                any("packet is missing required artifacts" in blocker for blocker in report["blockers"])
            )

    def test_run_exact_benchmark_requires_code_repair_dispatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo-missing-dispatch"
            project_dir.mkdir(parents=True)
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"assertions": self._passing_browser_assertions()}),
                encoding="utf-8",
            )
            output_root = base / "reports"

            def write_json(path: Path, payload: dict):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
                return path

            with (
                mock.patch("brochure_maker.exact_benchmark.write_visual_diff_report", side_effect=lambda project, output_dir, **kwargs: write_json(Path(output_dir) / "visual-diff.json", {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_visual_diff_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_html_assessment", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_html_assessment_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_page_packets", side_effect=lambda project, **kwargs: self._write_packets_with_critique(output_root, "demo-missing-dispatch", score=97)),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_dispatch", side_effect=lambda project, code_repair_tasks_path, output_dir, **kwargs: write_json(output_root / "demo-missing-dispatch" / "code-repair-dispatch.json", {"accepted": False, "blockers": ["dispatch failed"]})),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_execution", side_effect=lambda project, dispatch_path, output_path, **kwargs: self._write_execution(output_root, "demo-missing-dispatch")),
                mock.patch("brochure_maker.exact_benchmark.write_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_eval_report", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 100, "accepted": True, "blockers": []})),
            ):
                report = run_exact_benchmark(project_dir=project_dir, output_root=output_root)

            self.assertFalse(report["accepted"])
            self.assertIn("dispatch failed", report["blockers"])

    def test_run_exact_benchmark_requires_code_repair_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "demo-missing-execution"
            project_dir.mkdir(parents=True)
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"assertions": self._passing_browser_assertions()}),
                encoding="utf-8",
            )
            output_root = base / "reports"

            def write_json(path: Path, payload: dict):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
                return path

            with (
                mock.patch("brochure_maker.exact_benchmark.write_visual_diff_report", side_effect=lambda project, output_dir, **kwargs: write_json(Path(output_dir) / "visual-diff.json", {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_visual_diff_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_html_assessment", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 97, "accepted": True, "blockers": []})),
                mock.patch("brochure_maker.exact_benchmark.attach_html_assessment_to_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_page_packets", side_effect=lambda project, **kwargs: self._write_packets_with_critique(output_root, "demo-missing-execution", score=97)),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_dispatch", side_effect=lambda project, code_repair_tasks_path, output_dir, **kwargs: self._write_dispatch(output_root, "demo-missing-execution")),
                mock.patch("brochure_maker.exact_benchmark.write_code_repair_execution", side_effect=lambda project, dispatch_path, output_path, **kwargs: write_json(output_root / "demo-missing-execution" / "code-repair-execution.json", {"accepted": False, "blockers": ["execution failed"]})),
                mock.patch("brochure_maker.exact_benchmark.write_browser_qa"),
                mock.patch("brochure_maker.exact_benchmark.write_eval_report", side_effect=lambda project, output, **kwargs: write_json(Path(output), {"score": 100, "accepted": True, "blockers": []})),
            ):
                report = run_exact_benchmark(project_dir=project_dir, output_root=output_root)

            self.assertFalse(report["accepted"])
            self.assertIn("execution failed", report["blockers"])

    def test_run_exact_benchmark_requires_one_source(self):
        with self.assertRaises(ValueError):
            run_exact_benchmark()
        with self.assertRaises(ValueError):
            run_exact_benchmark(pdf_path="/tmp/a.pdf", project_dir="/tmp/project")

    def test_upload_exact_pdf_validates_pdf_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            text_path = Path(temp_dir) / "source.txt"
            text_path.write_text("not a pdf", encoding="utf-8")

            with self.assertRaises(ValueError):
                upload_exact_pdf(text_path)

    def test_upload_exact_pdf_uses_large_brochure_timeout(self):
        self.assertGreaterEqual(EXACT_UPLOAD_TIMEOUT_SECONDS, 600)

    def _passing_browser_assertions(self) -> dict[str, bool]:
        return {
            "editor_has_expected_pages": True,
            "global_controls_visible": True,
            "export_has_expected_pages": True,
            "export_has_no_editor_chrome": True,
            "state_roundtrip_preserved": True,
            "clean_export_preserves_edited_text": True,
            "clean_export_preserves_global_colour": True,
            "typed_text_font_preserved": True,
            "source_preserved_pages_have_no_giant_interactive_hotspots": True,
            "media_slots_have_actionable_controls": True,
            "image_replacement_roundtrip_preserved": True,
            "logo_replacement_roundtrip_preserved": True,
            "map_replacement_roundtrip_preserved": True,
        }

    def _write_packets_with_critique(self, output_root: Path, project_id: str, *, score: float) -> Path:
        page_dir = output_root / project_id / "pages" / "page-001"
        page_dir.mkdir(parents=True, exist_ok=True)
        required_files = (
            "source.png",
            "generated.png",
            "diff.png",
            "design-page.json",
            "inventory-page.json",
            "browser-page-qa.json",
            "page-assessment.json",
            "page-score.json",
            "repair-plan.json",
            "page-repair-patch.json",
            "repair-attempts.json",
            "pdf-text-spans.json",
            "pdf-fonts.json",
            "pdf-images.json",
            "image-regions.json",
            "pdf-vectors.json",
            "raster-components.json",
            "html-text-spans.json",
            "text-reconstruction.json",
            "visual-critic.json",
            "browser-interaction-qa.json",
            "editability-critic.json",
            "extraction-diagnosis.json",
            "repair-planner.json",
            "hardcoding-critic.json",
        )
        for filename in required_files:
            if filename.endswith("-critic.json") or filename in {
                "browser-interaction-qa.json",
                "extraction-diagnosis.json",
                "repair-planner.json",
            }:
                (page_dir / filename).write_text(
                    json.dumps(
                        {
                            "agent": filename.removesuffix(".json"),
                            "accepted": True,
                            "status": "accepted",
                            "blockers": [],
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                (page_dir / filename).write_text("{}", encoding="utf-8")
        critique_path = page_dir / "page-critique.json"
        critique_path.write_text(
            json.dumps(
                {
                    "accepted": True,
                    "status": "accepted",
                    "rubric": [{"category": "typography", "status": "pass"}],
                }
            ),
            encoding="utf-8",
        )
        path = output_root / project_id / "page-packets.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "accepted": True,
                    "failed_pages": [],
                    "packets": [
                        {
                            "page_number": 1,
                            "score": score,
                            "accepted": True,
                            "packet_dir": str(page_dir),
                            "critique": str(critique_path),
                            "judgement_agents": {
                                "accepted": True,
                                "agent_count": 6,
                                "artifacts": {
                                    "visual-critic.json": str(page_dir / "visual-critic.json"),
                                    "browser-interaction-qa.json": str(page_dir / "browser-interaction-qa.json"),
                                    "editability-critic.json": str(page_dir / "editability-critic.json"),
                                    "extraction-diagnosis.json": str(page_dir / "extraction-diagnosis.json"),
                                    "repair-planner.json": str(page_dir / "repair-planner.json"),
                                    "hardcoding-critic.json": str(page_dir / "hardcoding-critic.json"),
                                },
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_dispatch(self, output_root: Path, project_id: str) -> Path:
        dispatch_path = output_root / project_id / "code-repair-dispatch.json"
        worker_dir = output_root / project_id / "code-repair-workers" / "typography"
        worker_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("worker-brief.json", "worker-prompt.md", "evidence-manifest.json"):
            (worker_dir / filename).write_text("{}", encoding="utf-8")
        dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        dispatch_path.write_text(
            json.dumps(
                {
                    "accepted": True,
                    "status": "accepted-optional-worker-briefs",
                    "dispatched_task_count": 0,
                    "required_dispatch_count": 0,
                    "optional_dispatch_count": 0,
                    "worker_briefs": [],
                    "blockers": [],
                }
            ),
            encoding="utf-8",
        )
        return dispatch_path

    def _write_execution(self, output_root: Path, project_id: str) -> Path:
        execution_path = output_root / project_id / "code-repair-execution.json"
        execution_path.parent.mkdir(parents=True, exist_ok=True)
        execution_path.write_text(
            json.dumps(
                {
                    "accepted": True,
                    "status": "accepted-no-required-repairs",
                    "required_execution_count": 0,
                    "required_accepted_count": 0,
                    "optional_execution_count": 0,
                    "blockers": [],
                    "executions": [],
                }
            ),
            encoding="utf-8",
        )
        return execution_path


if __name__ == "__main__":
    unittest.main()
