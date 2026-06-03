from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_benchmark import REQUIRED_BROWSER_ASSERTIONS
from brochure_maker.exact_benchmark_matrix import run_benchmark_matrix
from brochure_maker.exact_browser_ui_qa import REQUIRED_BROWSER_UI_ASSERTIONS
from brochure_maker.exact_export_qa import REQUIRED_EXPORT_ASSERTIONS


class TestExactBenchmarkMatrix(unittest.TestCase):
    def test_matrix_accepts_multiple_reports_and_tracks_lowest_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report_a = self._write_benchmark_report(base, "a", page_scores=[99.0, 97.25], visual=98.0)
            report_b = self._write_benchmark_report(base, "b", page_scores=[96.5, 95.5], visual=96.0)
            manifest = self._write_manifest(
                base,
                [
                    {"id": "austin", "report_path": str(report_a), "require_fresh_upload": True},
                    {"id": "sekforde", "report_path": str(report_b), "require_fresh_upload": True},
                ],
            )

            matrix = run_benchmark_matrix(
                manifest,
                output_path=base / "reports" / "matrix.json",
                rerun=False,
            )

            self.assertTrue(matrix["accepted"])
            self.assertEqual(matrix["benchmark_count"], 2)
            self.assertEqual(matrix["accepted_count"], 2)
            self.assertEqual(matrix["aggregate"]["lowest_page_score"], 95.5)
            self.assertEqual(matrix["blockers"], [])
            self.assertTrue(matrix["benchmarks"][0]["browser_assertions"]["editor_has_expected_pages"])
            self.assertIn("global_controls_visible", matrix["benchmarks"][0]["required_browser_assertions"])
            self.assertIn("text_edit_reload_roundtrip", matrix["benchmarks"][0]["required_browser_ui_assertions"])
            self.assertIn("pdf_export_nonempty", matrix["benchmarks"][0]["required_export_assertions"])
            self.assertTrue(matrix["benchmarks"][0]["repair_loop"]["accepted"])
            self.assertTrue(matrix["benchmarks"][0]["code_repair_tasks"]["accepted"])
            self.assertTrue(matrix["benchmarks"][0]["code_repair_dispatch"]["accepted"])
            self.assertTrue(matrix["benchmarks"][0]["code_repair_execution"]["accepted"])
            self.assertTrue((base / "reports" / "latest-matrix.json").exists())

    def test_matrix_blocks_page_scores_below_floor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report = self._write_benchmark_report(base, "low-page", page_scores=[96.0, 94.9])
            manifest = self._write_manifest(base, [{"id": "low-page", "report_path": str(report)}])

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn("low-page: Page 2 score 94.900 is below floor 95.000", matrix["blockers"])

    def test_matrix_distinguishes_packet_rejection_from_score_floor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report = self._write_benchmark_report(base, "packet-fail", page_scores=[97.5])
            packets_path = base / "reports" / "packet-fail" / "page-packets.json"
            packets = json.loads(packets_path.read_text(encoding="utf-8"))
            packets["packets"][0]["accepted"] = False
            packets_path.write_text(json.dumps(packets), encoding="utf-8")
            manifest = self._write_manifest(base, [{"id": "packet-fail", "report_path": str(report)}])

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn(
                "packet-fail: Page 1 page packet was not accepted despite score 97.500",
                matrix["blockers"],
            )

    def test_matrix_blocks_missing_browser_assertions_even_when_report_claims_acceptance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report = self._write_benchmark_report(
                base,
                "weak-browser",
                browser_assertions={"global_controls_visible": False},
            )
            manifest = self._write_manifest(base, [{"id": "weak-browser", "report_path": str(report)}])

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn(
                "weak-browser: Missing or false Browser assertion: editor_has_expected_pages",
                matrix["blockers"],
            )
            self.assertIn(
                "weak-browser: Missing or false Browser assertion: global_controls_visible",
                matrix["blockers"],
            )

    def test_matrix_blocks_missing_browser_ui_and_export_assertions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report = self._write_benchmark_report(
                base,
                "weak-ui-export",
                browser_ui_assertions={"editor_opened_in_browser": True},
                export_assertions={"pdf_export_succeeded": True},
            )
            manifest = self._write_manifest(base, [{"id": "weak-ui-export", "report_path": str(report)}])

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn(
                "weak-ui-export: Missing or false Browser UI assertion: global_controls_panel_usable",
                matrix["blockers"],
            )
            self.assertIn(
                "weak-ui-export: Missing or false export assertion: html_export_has_expected_pages",
                matrix["blockers"],
            )

    def test_matrix_blocks_missing_quality_artifacts_even_for_claimed_acceptance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report = self._write_benchmark_report(base, "artifact-gap", write_quality_artifacts=False)
            manifest = self._write_manifest(base, [{"id": "artifact-gap", "report_path": str(report)}])

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn("artifact-gap: Benchmark is missing browser_ui_qa artifact", matrix["blockers"])
            self.assertIn("artifact-gap: Benchmark is missing export_qa artifact", matrix["blockers"])
            self.assertIn("artifact-gap: Benchmark is missing control_quality artifact", matrix["blockers"])

    def test_matrix_blocks_low_critic_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report = self._write_benchmark_report(base, "weak-critic", visual=97.0, critic=93.0)
            manifest = self._write_manifest(base, [{"id": "weak-critic", "report_path": str(report)}])

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn(
                "weak-critic: critic score 93.000 is below floor 95.000",
                matrix["blockers"],
            )

    def test_matrix_blocks_missing_or_failed_repair_loop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            missing = self._write_benchmark_report(base, "missing-loop", write_repair_loop=False)
            failed = self._write_benchmark_report(base, "failed-loop", repair_loop_accepted=False)
            manifest = self._write_manifest(
                base,
                [
                    {"id": "missing-loop", "report_path": str(missing)},
                    {"id": "failed-loop", "report_path": str(failed)},
                ],
            )

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn("missing-loop: Benchmark is missing repair-loop artifact", matrix["blockers"])
            self.assertIn("failed-loop: Repair loop was not accepted", matrix["blockers"])

    def test_matrix_blocks_missing_or_failed_repair_orchestration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            missing = self._write_benchmark_report(base, "missing-orchestration", write_repair_orchestration=False)
            failed = self._write_benchmark_report(base, "failed-orchestration", repair_orchestration_accepted=False)
            manifest = self._write_manifest(
                base,
                [
                    {"id": "missing-orchestration", "report_path": str(missing)},
                    {"id": "failed-orchestration", "report_path": str(failed)},
                ],
            )

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn("missing-orchestration: Benchmark is missing repair-orchestration artifact", matrix["blockers"])
            self.assertIn("failed-orchestration: Repair orchestration was not accepted", matrix["blockers"])

    def test_matrix_blocks_missing_or_failed_code_repair_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            missing = self._write_benchmark_report(base, "missing-code-tasks", write_code_tasks=False)
            failed = self._write_benchmark_report(base, "failed-code-tasks", code_tasks_accepted=False)
            manifest = self._write_manifest(
                base,
                [
                    {"id": "missing-code-tasks", "report_path": str(missing)},
                    {"id": "failed-code-tasks", "report_path": str(failed)},
                ],
            )

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn("missing-code-tasks: Benchmark is missing code-repair-tasks artifact", matrix["blockers"])
            self.assertIn("failed-code-tasks: Code repair task artifact was not accepted", matrix["blockers"])

    def test_matrix_blocks_missing_or_failed_code_repair_dispatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            missing = self._write_benchmark_report(base, "missing-dispatch", write_dispatch=False)
            failed = self._write_benchmark_report(base, "failed-dispatch", dispatch_accepted=False)
            manifest = self._write_manifest(
                base,
                [
                    {"id": "missing-dispatch", "report_path": str(missing)},
                    {"id": "failed-dispatch", "report_path": str(failed)},
                ],
            )

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn("missing-dispatch: Benchmark is missing code-repair-dispatch artifact", matrix["blockers"])
            self.assertIn("failed-dispatch: Code repair dispatch artifact was not accepted", matrix["blockers"])

    def test_matrix_blocks_missing_or_failed_code_repair_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            missing = self._write_benchmark_report(base, "missing-execution", write_execution=False)
            failed = self._write_benchmark_report(base, "failed-execution", execution_accepted=False)
            manifest = self._write_manifest(
                base,
                [
                    {"id": "missing-execution", "report_path": str(missing)},
                    {"id": "failed-execution", "report_path": str(failed)},
                ],
            )

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn("missing-execution: Benchmark is missing code-repair-execution artifact", matrix["blockers"])
            self.assertIn("failed-execution: Code repair execution artifact was not accepted", matrix["blockers"])

    def test_matrix_blocks_when_fresh_upload_provenance_is_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            report = self._write_benchmark_report(base, "existing", source_mode="existing-project", fresh_upload=None)
            manifest = self._write_manifest(
                base,
                [{"id": "existing", "report_path": str(report), "require_fresh_upload": True}],
            )

            matrix = run_benchmark_matrix(manifest, output_path=base / "matrix.json", rerun=False)

            self.assertFalse(matrix["accepted"])
            self.assertIn(
                "existing: Benchmark is required to prove fresh-upload provenance",
                matrix["blockers"],
            )

    def _write_manifest(self, base: Path, entries: list[dict]) -> Path:
        path = base / "manifest.json"
        path.write_text(json.dumps({"benchmarks": entries}), encoding="utf-8")
        return path

    def _write_benchmark_report(
        self,
        base: Path,
        project_id: str,
        *,
        page_scores: list[float] | None = None,
        visual: float = 97.0,
        critic: float | None = None,
        browser_assertions: dict | None = None,
        browser_ui_assertions: dict | None = None,
        export_assertions: dict | None = None,
        source_mode: str = "fresh-upload",
        fresh_upload: dict | None | bool = True,
        write_quality_artifacts: bool = True,
        control_quality_accepted: bool = True,
        write_repair_loop: bool = True,
        repair_loop_accepted: bool = True,
        write_repair_orchestration: bool = True,
        repair_orchestration_accepted: bool = True,
        write_code_tasks: bool = True,
        code_tasks_accepted: bool = True,
        write_dispatch: bool = True,
        dispatch_accepted: bool = True,
        write_execution: bool = True,
        execution_accepted: bool = True,
    ) -> Path:
        page_scores = page_scores or [97.0]
        browser_assertions = browser_assertions or {
            name: True for name in REQUIRED_BROWSER_ASSERTIONS
        }
        browser_ui_assertions = browser_ui_assertions or {
            name: True for name in REQUIRED_BROWSER_UI_ASSERTIONS
        }
        export_assertions = export_assertions or {
            name: True for name in REQUIRED_EXPORT_ASSERTIONS
        }
        fresh_upload_payload = (
            {"project_id": project_id, "status": "complete"}
            if fresh_upload is True
            else fresh_upload
        )
        run_dir = base / "reports" / project_id
        run_dir.mkdir(parents=True, exist_ok=True)
        packets_path = run_dir / "page-packets.json"
        packets_path.write_text(
            json.dumps(
                {
                    "accepted": all(score >= 95 for score in page_scores),
                    "failed_pages": [index for index, score in enumerate(page_scores, start=1) if score < 95],
                    "packets": [
                        {
                            "page_number": index,
                            "score": score,
                            "accepted": score >= 95,
                        }
                        for index, score in enumerate(page_scores, start=1)
                    ],
                }
            ),
            encoding="utf-8",
        )
        repair_loop_path = run_dir / "repair-loop.json"
        if write_repair_loop:
            repair_loop_path.write_text(
                json.dumps(
                    {
                        "accepted": repair_loop_accepted,
                        "status": "accepted" if repair_loop_accepted else "needs-focused-repair",
                        "failed_page_count": 0 if repair_loop_accepted else 1,
                        "skipped_page_count": len(page_scores) if repair_loop_accepted else max(0, len(page_scores) - 1),
                        "blockers": [] if repair_loop_accepted else ["Page 1 requires focused repair"],
                    }
                ),
                encoding="utf-8",
            )
        repair_orchestration_path = run_dir / "repair-orchestration.json"
        if write_repair_orchestration:
            repair_orchestration_path.write_text(
                json.dumps(
                    {
                        "accepted": repair_orchestration_accepted,
                        "status": "accepted" if repair_orchestration_accepted else "needs-reusable-code-repair",
                        "attempt_count": 0 if repair_orchestration_accepted else 1,
                        "escalation_count": 0 if repair_orchestration_accepted else 1,
                    }
                ),
                encoding="utf-8",
            )
        code_tasks_path = run_dir / "code-repair-tasks.json"
        if write_code_tasks:
            code_tasks_path.write_text(
                json.dumps(
                    {
                        "accepted": code_tasks_accepted,
                        "status": "accepted-no-code-repair-tasks" if code_tasks_accepted else "needs-reusable-code-repair",
                        "task_count": 0 if code_tasks_accepted else 1,
                        "required_task_count": 0 if code_tasks_accepted else 1,
                        "optional_task_count": 0,
                        "blockers": [] if code_tasks_accepted else ["Page 1 requires reusable code repair"],
                    }
                ),
                encoding="utf-8",
            )
        dispatch_path = run_dir / "code-repair-dispatch.json"
        if write_dispatch:
            dispatch_path.write_text(
                json.dumps(
                    {
                        "accepted": dispatch_accepted,
                        "status": "accepted-no-worker-briefs" if dispatch_accepted else "required-worker-briefs-ready",
                        "dispatched_task_count": 0,
                        "required_dispatch_count": 0 if dispatch_accepted else 1,
                        "optional_dispatch_count": 0,
                        "blockers": [] if dispatch_accepted else ["Required task needs worker implementation"],
                    }
                ),
                encoding="utf-8",
            )
        execution_path = run_dir / "code-repair-execution.json"
        if write_execution:
            execution_path.write_text(
                json.dumps(
                    {
                        "accepted": execution_accepted,
                        "status": "accepted-no-required-repairs" if execution_accepted else "required-repairs-pending",
                        "required_execution_count": 0 if execution_accepted else 1,
                        "required_accepted_count": 0,
                        "optional_execution_count": 0,
                        "blockers": [] if execution_accepted else ["Required worker task needs accepted result"],
                    }
                ),
                encoding="utf-8",
            )
        browser_ui_path = run_dir / "browser-ui-qa.json"
        export_qa_path = run_dir / "export-qa.json"
        control_quality_path = run_dir / "control-quality.json"
        if write_quality_artifacts:
            browser_ui_path.write_text(
                json.dumps(
                    {
                        "accepted": all(browser_ui_assertions.values()),
                        "assertions": browser_ui_assertions,
                        "blockers": [],
                        "browser_surface": "in-app-browser",
                        "confidence": "full",
                    }
                ),
                encoding="utf-8",
            )
            export_qa_path.write_text(
                json.dumps(
                    {
                        "accepted": all(export_assertions.values()),
                        "assertions": export_assertions,
                        "blockers": [],
                        "html_export": {"pageCount": len(page_scores)},
                        "pdf_export": {"bytes": 4096, "succeeded": True},
                    }
                ),
                encoding="utf-8",
            )
            control_quality_path.write_text(
                json.dumps(
                    {
                        "accepted": control_quality_accepted,
                        "blockers": [] if control_quality_accepted else ["Noisy global control labels"],
                    }
                ),
                encoding="utf-8",
            )
        report_path = run_dir / "benchmark-run.json"
        report_path.write_text(
            json.dumps(
                {
                    "accepted": True,
                    "project_id": project_id,
                    "project_dir": str(base / "projects" / project_id),
                    "source_mode": source_mode,
                    "fresh_upload": fresh_upload_payload,
                    "scores": {
                        "visual": visual,
                        "html_assessment": visual,
                        "eval": 100,
                        "critic": visual if critic is None else critic,
                        "browser_ui": 100,
                        "export": 100,
                        "control_quality": 100 if control_quality_accepted else 0,
                    },
                    "failed_pages": [],
                    "blockers": [],
                    "browser_assertions": browser_assertions,
                    "browser_ui_assertions": browser_ui_assertions,
                    "export_assertions": export_assertions,
                    "required_browser_assertions": list(REQUIRED_BROWSER_ASSERTIONS),
                    "required_browser_ui_assertions": list(REQUIRED_BROWSER_UI_ASSERTIONS),
                    "required_export_assertions": list(REQUIRED_EXPORT_ASSERTIONS),
                    "browser_http_qa": {"accepted": True, "assertions": browser_assertions},
                    "browser_ui_qa": {"accepted": all(browser_ui_assertions.values()), "assertions": browser_ui_assertions},
                    "html_export": {"pageCount": len(page_scores)},
                    "pdf_export": {"bytes": 4096, "succeeded": True},
                    "control_quality": {
                        "accepted": control_quality_accepted,
                        "blockers": [] if control_quality_accepted else ["Noisy global control labels"],
                    },
                    "artifacts": {
                        "page_packets": str(packets_path),
                        **(
                            {
                                "browser_ui_qa": str(browser_ui_path),
                                "export_qa": str(export_qa_path),
                                "control_quality": str(control_quality_path),
                            }
                            if write_quality_artifacts
                            else {}
                        ),
                        **({"repair_loop": str(repair_loop_path)} if write_repair_loop else {}),
                        **({"repair_orchestration": str(repair_orchestration_path)} if write_repair_orchestration else {}),
                        **({"code_repair_tasks": str(code_tasks_path)} if write_code_tasks else {}),
                        **({"code_repair_dispatch": str(dispatch_path)} if write_dispatch else {}),
                        **({"code_repair_execution": str(execution_path)} if write_execution else {}),
                    },
                }
            ),
            encoding="utf-8",
        )
        return report_path


if __name__ == "__main__":
    unittest.main()
