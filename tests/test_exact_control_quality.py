from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_control_quality import build_control_quality_report


class TestExactControlQuality(unittest.TestCase):
    def test_accepts_complete_semantic_global_controls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            self._write_graph(project_dir, typography_complete=True, source_attribution=True)
            (project_dir / "brochure.html").write_text(
                """
                <section data-field-section="amenities">
                  <label>Roof terrace</label>
                  <input data-exact-field="amenity-roof" data-targets="icon-1" value="Roof terrace">
                </section>
                """,
                encoding="utf-8",
            )

            report = build_control_quality_report(project_dir)

        self.assertTrue(report["accepted"])
        self.assertEqual(report["blockers"], [])

    def test_rejects_noisy_fields_duplicate_targets_and_incomplete_typography(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            self._write_graph(project_dir, typography_complete=False, source_attribution=False)
            (project_dir / "brochure.html").write_text(
                """
                <section data-field-section="amenities">
                  <label>Map fragment</label>
                  <input data-exact-field="amenity-fragment" data-targets="slot-1" value="N R">
                  <label>Duplicate target</label>
                  <input data-exact-field="amenity-duplicate" data-targets="slot-1" value="Bike storage">
                </section>
                """,
                encoding="utf-8",
            )

            report = build_control_quality_report(project_dir)

        self.assertFalse(report["accepted"])
        self.assertIn("Noisy amenity/global field label: N R", report["blockers"])
        self.assertIn("Duplicate global control target: slot-1", report["blockers"])
        self.assertIn("Typography role cover-title is missing lineHeight", report["blockers"])
        self.assertIn("Replaceable element lacks source attribution on page 1", report["blockers"])

    def _write_graph(self, project_dir: Path, *, typography_complete: bool, source_attribution: bool) -> None:
        typography = {
            "cssVar": "--exact-font-cover-title",
            "fontFamily": "DalaMoa-Thin",
            "fontSize": "128px",
            "fontWeight": "300",
            "letterSpacing": "0.04em",
            "sourceStyleEvidence": {
                "fontSize": True,
                "lineHeight": True,
                "fontWeight": True,
                "letterSpacing": True,
            },
        }
        if typography_complete:
            typography["lineHeight"] = "0.9"
        graph = {
            "theme_tokens": {
                "global_controls": {
                    "palette": {"accent": "#abc"},
                    "typography": {"roles": ["cover-title"]},
                    "logo": {"slots": ["cover"]},
                    "amenity_icons": {"slots": ["slot-1"]},
                    "images": {"slots": ["image-1"]},
                    "map": {"slots": ["map-1"]},
                    "agents": {"groups": ["contact-1"]},
                },
                "typography": {"editor_roles": {"cover-title": typography}},
            },
            "pages": [
                {
                    "page_number": 1,
                    "elements": [
                        {
                            "id": "photo-1",
                            "role": "photo-region",
                            "replaceable": True,
                            **({"source_attribution": "PyMuPDF image block"} if source_attribution else {}),
                        }
                    ],
                }
            ],
        }
        (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
