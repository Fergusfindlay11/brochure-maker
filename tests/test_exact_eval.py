from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_eval import evaluate_project, write_eval_report


class TestExactEval(unittest.TestCase):
    def test_eval_accepts_complete_design_graph_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "demo1234"
            (project_dir / "exact_layout_model").mkdir(parents=True)
            self._write_required_artifacts(project_dir)
            self._write_browser_evidence(project_dir, page_count=1)
            (project_dir / "brochure.design.json").write_text(
                json.dumps(
                    {
                        "schema": "brochure-maker.design-graph.v1",
                        "project_id": "demo1234",
                        "page_count": 1,
                        "theme_tokens": {
                            "palette": {"accent_colour": "#ffea00"},
                            "typography": {"editor_roles": {"title": {"fontFamily": "TitleFont"}}},
                            "global_controls": {
                                "palette": ["accent colour"],
                                "typography": ["title font"],
                                "logo": ["source facade mark replacement"],
                                "amenity_icons": ["icon bank"],
                                "images": ["image fit/crop mode"],
                                "map": ["regenerate/preserve map"],
                                "agents": ["agency logo replacement"],
                            },
                        },
                        "pages": [
                            {
                                "page_number": 1,
                                "purpose": "cover",
                                "detected_features": ["cover_title", "source_facade_mark", "photo_regions"],
                                "elements": [
                                    {"id": "title", "type": "text", "role": "cover-title", "node_class": "editable-content", "source_attribution": "PDF text span"},
                                    {
                                        "id": "logo",
                                        "type": "logo",
                                        "role": "source-facade-mark",
                                        "node_class": "editor-control",
                                        "bbox": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
                                        "replaceable": True,
                                        "source_attribution": "vector detection",
                                    },
                                    {"id": "photo", "type": "image", "role": "photo-region", "node_class": "extracted-evidence", "replaceable": True, "source_attribution": "PyMuPDF images"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = evaluate_project(project_dir)

        self.assertTrue(report["accepted"])
        self.assertEqual(report["score"], 100)
        self.assertEqual(report["blockers"], [])

    def test_eval_blocks_missing_expected_feature_roles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "demo5678"
            (project_dir / "exact_layout_model").mkdir(parents=True)
            self._write_required_artifacts(project_dir)
            (project_dir / "brochure.design.json").write_text(
                json.dumps(
                    {
                        "schema": "brochure-maker.design-graph.v1",
                        "project_id": "demo5678",
                        "page_count": 1,
                        "theme_tokens": {"palette": {}, "typography": {}, "global_controls": {}},
                        "pages": [
                            {
                                "page_number": 1,
                                "purpose": "cover",
                                "detected_features": ["cover_title", "source_facade_mark"],
                                "elements": [{"id": "body", "type": "text", "role": "body", "source_attribution": "PDF text span"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = evaluate_project(project_dir)

        self.assertFalse(report["accepted"])
        self.assertLess(report["score"], 95)
        self.assertIn("Page 1: Missing cover/title text role", report["blockers"])
        self.assertIn("Page 1: Missing source facade mark slot", report["blockers"])
        self.assertIn("Missing global palette tokens", report["blockers"])

    def test_eval_accepts_photo_grid_as_photo_region_control(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "demo-photo-grid"
            (project_dir / "exact_layout_model").mkdir(parents=True)
            self._write_required_artifacts(project_dir)
            self._write_browser_evidence(project_dir, page_count=1)
            self._write_minimal_design_graph(project_dir, project_id="demo-photo-grid")
            graph = json.loads((project_dir / "brochure.design.json").read_text(encoding="utf-8"))
            graph["pages"][0]["detected_features"] = ["photo_regions"]
            graph["pages"][0]["elements"] = [
                {
                    "id": "photo-grid-1",
                    "type": "image",
                    "role": "photo-grid",
                    "node_class": "editor-control",
                    "replaceable": True,
                    "source_attribution": "image-region classifier",
                    "bbox": {"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3},
                }
            ]
            (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")

            report = evaluate_project(project_dir)

        self.assertTrue(report["accepted"])
        self.assertNotIn("Page 1: Missing photo/image region slots", report["blockers"])

    def test_eval_accepts_actionable_semantic_map_concept(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "demo-semantic-map"
            (project_dir / "exact_layout_model").mkdir(parents=True)
            self._write_required_artifacts(project_dir)
            self._write_browser_evidence(project_dir, page_count=1)
            self._write_minimal_design_graph(project_dir, project_id="demo-semantic-map")
            graph = json.loads((project_dir / "brochure.design.json").read_text(encoding="utf-8"))
            graph["pages"][0]["detected_features"] = ["map"]
            graph["pages"][0]["elements"] = [
                {
                    "id": "map-1",
                    "type": "map",
                    "role": "map",
                    "node_class": "semantic-concept",
                    "editable": True,
                    "replaceable": True,
                    "source_attribution": "PDF text labels, vector detection, colour sampling, semantic inference",
                    "bbox": {"x": 0.05, "y": 0.35, "width": 0.8, "height": 0.5},
                }
            ]
            (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")

            report = evaluate_project(project_dir)

        self.assertTrue(report["accepted"])
        self.assertNotIn("Page 1: Missing map region", report["blockers"])

    def test_write_eval_report_writes_latest_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "demo9999"
            report_dir = Path(temp_dir) / "reports"
            (project_dir / "exact_layout_model").mkdir(parents=True)
            self._write_required_artifacts(project_dir)

            written = write_eval_report(project_dir, report_dir / "run.json")

            self.assertTrue(written.exists())
            self.assertTrue((report_dir / "latest.json").exists())

    def test_eval_blocks_browser_text_overlap_findings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "demo-overlap"
            (project_dir / "exact_layout_model").mkdir(parents=True)
            self._write_required_artifacts(project_dir)
            self._write_browser_evidence(project_dir, page_count=1)
            evidence = json.loads((project_dir / "browser_qa.json").read_text(encoding="utf-8"))
            evidence["layout_audit"] = {
                "textOverlaps": [
                    {
                        "page": 4,
                        "a": "line-1",
                        "b": "line-2",
                        "area": 173,
                    }
                ]
            }
            (project_dir / "browser_qa.json").write_text(json.dumps(evidence), encoding="utf-8")
            (project_dir / "brochure.design.json").write_text(
                json.dumps(
                    {
                        "schema": "brochure-maker.design-graph.v1",
                        "project_id": "demo-overlap",
                        "page_count": 1,
                        "theme_tokens": {
                            "palette": {"accent_colour": "#ffea00"},
                            "typography": {"editor_roles": {"title": {"fontFamily": "TitleFont"}}},
                            "global_controls": {
                                "palette": ["accent colour"],
                                "typography": ["title font"],
                                "logo": ["source facade mark replacement"],
                                "amenity_icons": ["icon bank"],
                                "images": ["image fit/crop mode"],
                                "map": ["regenerate/preserve map"],
                                "agents": ["agency logo replacement"],
                            },
                        },
                        "pages": [
                            {
                                "page_number": 1,
                                "purpose": "cover",
                                "detected_features": ["cover_title"],
                                "elements": [
                                    {"id": "title", "type": "text", "role": "cover-title", "node_class": "editable-content", "source_attribution": "PDF text span"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = evaluate_project(project_dir)

        self.assertFalse(report["accepted"])
        self.assertIn("Browser layout audit found overlapping editable text blocks", report["blockers"])

    def test_eval_accepts_current_browser_qa_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "demo-current-browser"
            (project_dir / "exact_layout_model").mkdir(parents=True)
            self._write_required_artifacts(project_dir)
            self._write_minimal_design_graph(project_dir, project_id="demo-current-browser")
            (project_dir / "browser_qa.json").write_text(
                json.dumps(
                    {
                        "editor": {
                            "exactPageCount": 1,
                            "contenteditableCount": 3,
                            "globalSectionCount": 7,
                        },
                        "export": {
                            "pageCount": 1,
                            "contenteditable": 0,
                            "inputs": 0,
                            "scripts": 0,
                            "toolbar": 0,
                            "bodyClass": "export-clean",
                        },
                        "assertions": {
                            "global_controls_visible": True,
                            "edited_text_fits_after_roundtrip": True,
                            "structured_cover_title_targets_semantic": True,
                        },
                        "interactions": {
                            "titleEditFontPreserved": True,
                            "reloadPreserved": True,
                            "stateRoundtripPreserved": True,
                            "exportPreservedEditedText": True,
                            "cleanExportAfterProbeHasNoChrome": True,
                            "globalColourExported": True,
                            "editedTextFitsBox": True,
                            "imageReplacementRoundtripPreserved": True,
                            "logoReplacementRoundtripPreserved": True,
                            "mapReplacementRoundtripPreserved": True,
                        },
                        "html_assessment": {
                            "score": 96.5,
                            "accepted": True,
                            "summary": "current Browser QA schema fixture",
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = evaluate_project(project_dir)

        self.assertTrue(report["accepted"])
        self.assertEqual(report["blockers"], [])

    def test_eval_blocks_missing_browser_interaction_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "demo-missing-interactions"
            (project_dir / "exact_layout_model").mkdir(parents=True)
            self._write_required_artifacts(project_dir)
            self._write_minimal_design_graph(project_dir, project_id="demo-missing-interactions")
            self._write_browser_evidence(project_dir, page_count=1)
            evidence = json.loads((project_dir / "browser_qa.json").read_text(encoding="utf-8"))
            evidence.pop("interactions", None)
            (project_dir / "browser_qa.json").write_text(json.dumps(evidence), encoding="utf-8")

            report = evaluate_project(project_dir)

        self.assertFalse(report["accepted"])
        self.assertIn("Missing Browser interaction evidence", report["blockers"])

    def _write_required_artifacts(self, project_dir: Path) -> None:
        (project_dir / "brochure.html").write_text("<html></html>", encoding="utf-8")
        (project_dir / "source.pdf").write_bytes(b"%PDF-1.4\n")
        (project_dir / "exact_metadata.json").write_text(json.dumps({"design_graph_path": str(project_dir / "brochure.design.json")}), encoding="utf-8")
        (project_dir / "exact_layout_model" / "exact-layout.json").write_text("{}", encoding="utf-8")
        (project_dir / "exact_layout_model" / "extraction-inventory.json").write_text(json.dumps({"page_count": 1}), encoding="utf-8")

    def _write_browser_evidence(self, project_dir: Path, *, page_count: int) -> None:
        (project_dir / "browser_qa.json").write_text(
            json.dumps(
                {
                    "editor": {
                        "pageCount": page_count,
                        "editableTextCount": 1,
                        "fieldsPanelVisible": True,
                    },
                    "clean_export": {
                        "pageCount": page_count,
                        "contenteditable": 0,
                        "inputs": 0,
                        "scripts": 0,
                        "toolbar": 0,
                        "fieldsPanel": 0,
                        "bodyClass": "export-clean",
                    },
                    "interactions": {
                        "titleEditFontPreserved": True,
                        "reloadPreserved": True,
                        "stateRoundtripPreserved": True,
                        "exportPreservedEditedText": True,
                        "cleanExportAfterProbeHasNoChrome": True,
                        "globalColourExported": True,
                        "editedTextFitsBox": True,
                        "imageReplacementRoundtripPreserved": True,
                        "logoReplacementRoundtripPreserved": True,
                        "mapReplacementRoundtripPreserved": True,
                    },
                    "assertions": {
                        "global_controls_visible": True,
                        "edited_text_fits_after_roundtrip": True,
                        "structured_cover_title_targets_semantic": True,
                    },
                    "visual_diff": {
                        "score": 97,
                        "evidence": "synthetic visual check fixture",
                    },
                    "html_assessment": {
                        "score": 97,
                        "accepted": True,
                        "summary": "synthetic HTML assessment fixture",
                    },
                }
            ),
            encoding="utf-8",
        )

    def _write_minimal_design_graph(self, project_dir: Path, *, project_id: str) -> None:
        (project_dir / "brochure.design.json").write_text(
            json.dumps(
                {
                    "schema": "brochure-maker.design-graph.v1",
                    "project_id": project_id,
                    "page_count": 1,
                    "theme_tokens": {
                        "palette": {"accent_colour": "#ffea00"},
                        "typography": {"editor_roles": {"title": {"fontFamily": "TitleFont"}}},
                        "global_controls": {
                            "palette": ["accent colour"],
                            "typography": ["title font"],
                            "logo": ["source facade mark replacement"],
                            "amenity_icons": ["icon bank"],
                            "images": ["image fit/crop mode"],
                            "map": ["regenerate/preserve map"],
                            "agents": ["agency logo replacement"],
                        },
                    },
                    "pages": [
                        {
                            "page_number": 1,
                            "purpose": "cover",
                            "detected_features": ["cover_title"],
                            "elements": [
                                {
                                    "id": "title",
                                    "type": "text",
                                    "role": "cover-title",
                                    "node_class": "editable-content",
                                    "source_attribution": "PDF text span",
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
