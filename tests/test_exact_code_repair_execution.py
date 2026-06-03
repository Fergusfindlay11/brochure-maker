from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_code_repair_execution import (
    build_code_repair_execution,
    write_code_repair_execution,
)


class TestExactCodeRepairExecution(unittest.TestCase):
    def test_optional_worker_without_result_accepts_and_writes_template(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, dispatch_path, worker_dir = self._write_dispatch_fixture(base, required=False)

            report = build_code_repair_execution(project_dir, dispatch_path=dispatch_path)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["status"], "accepted-no-required-repairs")
            self.assertEqual(report["required_execution_count"], 0)
            self.assertTrue((worker_dir / "worker-result.template.json").exists())
            self.assertFalse((worker_dir / "worker-result.json").exists())

    def test_required_worker_without_result_blocks_acceptance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, dispatch_path, _worker_dir = self._write_dispatch_fixture(base, required=True)

            report = build_code_repair_execution(project_dir, dispatch_path=dispatch_path)

            self.assertFalse(report["accepted"])
            self.assertEqual(report["status"], "required-repairs-pending")
            self.assertIn("missing worker-result.json", report["blockers"][0])

    def test_required_worker_with_complete_result_accepts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, dispatch_path, worker_dir = self._write_dispatch_fixture(base, required=True)
            (worker_dir / "worker-result.json").write_text(
                json.dumps(
                    {
                        "accepted": True,
                        "status": "implemented",
                        "summary": "Improved reusable typography grouping.",
                        "files_changed": ["brochure_maker/pdf_exact_layout.py"],
                        "tests_run": ["python3 -m pytest tests/test_pdf_exact_layout.py -q"],
                        "browser_evidence": ["/tmp/browser.png"],
                        "benchmark_evidence": ["evals/reports/benchmark-matrix.json"],
                        "hardcoding_statement": "No project literals or fixed brochure coordinates were added.",
                    }
                ),
                encoding="utf-8",
            )

            report = build_code_repair_execution(project_dir, dispatch_path=dispatch_path)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["status"], "accepted-required-repairs")
            self.assertEqual(report["required_accepted_count"], 1)
            self.assertEqual(report["executions"][0]["files_changed"], ["brochure_maker/pdf_exact_layout.py"])

    def test_write_execution_returns_output_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, dispatch_path, _worker_dir = self._write_dispatch_fixture(base, required=False)

            path = write_code_repair_execution(
                project_dir,
                dispatch_path=dispatch_path,
                output_path=base / "execution.json",
            )

            self.assertEqual(path, (base / "execution.json").resolve())
            self.assertTrue(path.exists())

    def _write_dispatch_fixture(self, base: Path, *, required: bool) -> tuple[Path, Path, Path]:
        project_dir = base / "projects" / "demo"
        project_dir.mkdir(parents=True)
        worker_dir = base / "reports" / "demo" / "code-repair-workers" / "typography-title"
        worker_dir.mkdir(parents=True)
        (worker_dir / "worker-brief.json").write_text("{}", encoding="utf-8")
        dispatch_path = base / "reports" / "demo" / "code-repair-dispatch.json"
        dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        dispatch_path.write_text(
            json.dumps(
                {
                    "accepted": True,
                    "worker_briefs": [
                        {
                            "task_id": "typography-title",
                            "required_for_acceptance": required,
                            "worker_dir": str(worker_dir),
                            "worker_brief": str(worker_dir / "worker-brief.json"),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return project_dir, dispatch_path, worker_dir


if __name__ == "__main__":
    unittest.main()
