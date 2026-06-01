from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_page_patch import apply_page_repair_patch, build_page_repair_patch


class TestExactPagePatch(unittest.TestCase):
    def test_build_page_repair_patch_generates_text_and_image_operations_from_failed_findings(self):
        patch = build_page_repair_patch(
            project_id="demo",
            page_number=2,
            accepted=False,
            graph_page={
                "page_number": 2,
                "size": {"width": 1200, "height": 800},
                "elements": [
                    {
                        "id": "p002-text-0003",
                        "type": "text",
                        "role": "body",
                        "bbox_raw": {"x": 320, "y": 140, "width": 220, "height": 12},
                    },
                    {
                        "id": "p002-image-0001",
                        "type": "image",
                        "role": "photo-region",
                        "bbox_raw": {"x": 30, "y": 40, "width": 300, "height": 420},
                    },
                ],
            },
            assessment_page={
                "findings": [
                    {
                        "severity": "major",
                        "issue": "Text and image shifted",
                        "repair_subsystem": "typography/text-layout",
                        "element_ids": ["p002-text-0003", "p002-image-0001"],
                        "evidence": "synthetic hotspot",
                    },
                    {
                        "severity": "major",
                        "issue": "Image crop shifted",
                        "repair_subsystem": "images/photo-slots",
                        "element_ids": ["p002-image-0001"],
                    },
                ]
            },
        )

        self.assertEqual(patch["status"], "auto-patch-ready")
        self.assertEqual(patch["planner"]["operation_count"], 2)
        text_op = next(op for op in patch["operations"] if op["type"] == "text-layout")
        image_op = next(op for op in patch["operations"] if op["type"] == "image-layout")
        self.assertEqual(text_op["save_id"], "exact-page2-text3")
        self.assertEqual(text_op["styles"]["left"], "320px")
        self.assertEqual(text_op["styles"]["height"], "14px")
        self.assertEqual(text_op["styles"]["white-space"], "pre-wrap")
        self.assertEqual(image_op["save_id"], "exact-page2-image1")
        self.assertEqual(image_op["fit"], "cover")
        self.assertEqual(image_op["styles"]["height"], "420px")

    def test_build_page_repair_patch_does_not_generate_operations_for_accepted_page(self):
        patch = build_page_repair_patch(
            project_id="demo",
            page_number=1,
            accepted=True,
            graph_page={"elements": [{"id": "p001-text-0001", "type": "text"}]},
            assessment_page={"findings": [{"element_ids": ["p001-text-0001"], "repair_subsystem": "typography/text-layout"}]},
        )

        self.assertEqual(patch["status"], "not-needed")
        self.assertEqual(patch["operations"], [])

    def test_build_page_repair_patch_uses_contain_fit_for_space_plan(self):
        patch = build_page_repair_patch(
            project_id="demo",
            page_number=3,
            accepted=False,
            graph_page={
                "page_number": 3,
                "size": {"width": 1000, "height": 500},
                "elements": [
                    {
                        "id": "p003-image-0002",
                        "type": "floorplan",
                        "role": "space-plan",
                        "bbox": {"x": 0.25, "y": 0.1, "width": 0.5, "height": 0.8},
                    }
                ],
            },
            assessment_page={
                "findings": [
                    {
                        "severity": "major",
                        "issue": "Space plan slot misaligned",
                        "repair_subsystem": "images/space-plan",
                        "element_ids": ["p003-image-0002"],
                    }
                ]
            },
        )

        self.assertEqual(patch["status"], "auto-patch-ready")
        self.assertEqual(len(patch["operations"]), 1)
        operation = patch["operations"][0]
        self.assertEqual(operation["type"], "image-layout")
        self.assertEqual(operation["save_id"], "exact-page3-image2")
        self.assertEqual(operation["fit"], "contain")
        self.assertEqual(operation["styles"]["left"], "250px")
        self.assertEqual(operation["styles"]["height"], "400px")

    def test_text_layout_patch_updates_only_scoped_editor_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "projects" / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "brochure.html").write_text(
                '<p class="pdf-text" data-save-id="exact-page2-text3" '
                'data-typography-role="body">Original copy</p>',
                encoding="utf-8",
            )
            patch_path = Path(temp_dir) / "page-repair-patch.json"
            patch_path.write_text(
                json.dumps(
                    {
                        "schema": "brochure-maker.page-repair-patch.v1",
                        "page_number": 2,
                        "operations": [
                            {
                                "type": "text-layout",
                                "save_id": "exact-page2-text3",
                                "styles": {
                                    "left": "320px",
                                    "top": "140px",
                                    "width": "220px",
                                    "height": "48px",
                                    "position": "fixed",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = apply_page_repair_patch(project_dir, patch_path)

            self.assertTrue(report["applied"])
            self.assertEqual(report["applied_count"], 1)
            self.assertEqual(report["rejected_count"], 0)
            state = json.loads((project_dir / "editor_state.json").read_text(encoding="utf-8"))
            item = state["editableTexts"]["exact-page2-text3"]
            self.assertEqual(item["html"], "Original copy")
            self.assertEqual(item["typography"]["role"], "body")
            self.assertEqual(item["layout"]["styles"]["left"], "320px")
            self.assertNotIn("position", item["layout"]["styles"])

    def test_patch_rejects_save_id_outside_page_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "projects" / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "brochure.html").write_text(
                '<p class="pdf-text" data-save-id="exact-page3-text1">Wrong page</p>',
                encoding="utf-8",
            )
            patch_path = Path(temp_dir) / "page-repair-patch.json"
            patch_path.write_text(
                json.dumps(
                    {
                        "page_number": 2,
                        "operations": [
                            {
                                "type": "text-layout",
                                "save_id": "exact-page3-text1",
                                "styles": {"left": "10px"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = apply_page_repair_patch(project_dir, patch_path)

            self.assertFalse(report["applied"])
            self.assertEqual(report["rejected_count"], 1)
            self.assertIn("outside page 2", report["rejected_operations"][0]["reason"])
            self.assertFalse((project_dir / "editor_state.json").exists())

    def test_image_patch_updates_fit_and_geometry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "projects" / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "brochure.html").write_text("<main></main>", encoding="utf-8")
            patch_path = Path(temp_dir) / "image-patch.json"
            patch_path.write_text(
                json.dumps(
                    {
                        "page_number": 4,
                        "operations": [
                            {
                                "type": "image-layout",
                                "save_id": "exact-page4-image2",
                                "fit": "contain",
                                "styles": {"left": "40px", "top": "88px", "width": "300px", "height": "180px"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = apply_page_repair_patch(project_dir, patch_path)

            self.assertTrue(report["applied"])
            state = json.loads((project_dir / "editor_state.json").read_text(encoding="utf-8"))
            image = state["images"]["exact-page4-image2"]
            self.assertEqual(image["fit"], "contain")
            self.assertEqual(image["left"], "40px")
            self.assertEqual(image["height"], "180px")


if __name__ == "__main__":
    unittest.main()
