from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_benchmark import REQUIRED_BROWSER_ASSERTIONS
from brochure_maker.exact_browser_ui_qa import REQUIRED_BROWSER_UI_ASSERTIONS, write_browser_ui_qa
from brochure_maker.exact_export_qa import REQUIRED_EXPORT_ASSERTIONS


class TestExactBrowserUiQa(unittest.TestCase):
    def test_normalises_existing_in_app_browser_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(json.dumps({"page_count": 1}), encoding="utf-8")
            evidence = {
                "method": "manual-in-app-browser-run",
                "assertions": {name: True for name in REQUIRED_BROWSER_UI_ASSERTIONS},
                "blockers": [],
            }
            (project_dir / "browser_ui_qa.json").write_text(json.dumps(evidence), encoding="utf-8")

            path = write_browser_ui_qa(project_dir, force=True)
            qa = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(qa["accepted"])
        self.assertEqual(qa["browser_surface"], "in-app-browser")
        self.assertEqual(qa["confidence"], "full")
        self.assertTrue(all(qa["assertions"][name] for name in REQUIRED_BROWSER_UI_ASSERTIONS))

    def test_writes_provisional_ui_contract_from_http_and_export_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(json.dumps({"page_count": 1}), encoding="utf-8")
            (project_dir / "browser_qa.json").write_text(
                json.dumps(
                    {
                        "assertions": {
                            **{name: True for name in REQUIRED_BROWSER_ASSERTIONS},
                            "global_typography_controls_present": True,
                        },
                        "visual_diff": {"report": "evals/reports/demo-visual/visual-diff.json"},
                    }
                ),
                encoding="utf-8",
            )
            export_qa_path = project_dir / "run-export-qa.json"
            export_qa_path.write_text(
                json.dumps({"assertions": {name: True for name in REQUIRED_EXPORT_ASSERTIONS}}),
                encoding="utf-8",
            )

            path = write_browser_ui_qa(project_dir, export_qa_path=export_qa_path, force=True)
            qa = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(qa["accepted"])
        self.assertEqual(qa["browser_surface"], "http-fallback")
        self.assertEqual(qa["confidence"], "provisional")
        self.assertTrue(qa["assertions"]["pdf_export_checked"])
        self.assertIn("/api/projects/", qa["browser_playbook"][0])

    def test_blocks_when_provisional_evidence_is_shallow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(json.dumps({"page_count": 1}), encoding="utf-8")
            (project_dir / "browser_qa.json").write_text(
                json.dumps({"assertions": {"editor_has_expected_pages": True}}),
                encoding="utf-8",
            )

            path = write_browser_ui_qa(project_dir, force=True)
            qa = json.loads(path.read_text(encoding="utf-8"))

        self.assertFalse(qa["accepted"])
        self.assertIn(
            "Missing or false Browser UI assertion: global_controls_panel_usable",
            qa["blockers"],
        )
        self.assertIn(
            "Missing or false Browser UI assertion: pdf_export_checked",
            qa["blockers"],
        )


if __name__ == "__main__":
    unittest.main()
