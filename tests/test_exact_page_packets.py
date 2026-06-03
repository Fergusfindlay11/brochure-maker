from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from brochure_maker.exact_page_packets import build_page_packets, write_page_packets


class TestExactPagePackets(unittest.TestCase):
    def test_page_packets_copy_evidence_and_write_failed_page_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path, assessment_path = self._write_packet_fixture(base)

            index = build_page_packets(
                project_dir,
                output_dir=base / "reports" / "demo" / "pages",
                visual_report_path=visual_path,
                assessment_path=assessment_path,
            )

            self.assertFalse(index["accepted"])
            self.assertEqual(index["failed_pages"], [1])
            page1 = base / "reports" / "demo" / "pages" / "page-001"
            page2 = base / "reports" / "demo" / "pages" / "page-002"
            self.assertTrue((page1 / "source.png").exists())
            self.assertTrue((page1 / "generated.png").exists())
            self.assertTrue((page1 / "diff.png").exists())
            self.assertTrue((page1 / "browser-screenshot.png").exists())
            self.assertTrue((page1 / "design-page.json").exists())
            self.assertTrue((page1 / "inventory-page.json").exists())
            self.assertTrue((page1 / "pdf-text-spans.json").exists())
            self.assertTrue((page1 / "pdf-fonts.json").exists())
            self.assertTrue((page1 / "pdf-images.json").exists())
            self.assertTrue((page1 / "image-regions.json").exists())
            self.assertTrue((page1 / "pdf-vectors.json").exists())
            self.assertTrue((page1 / "raster-components.json").exists())
            self.assertTrue((page1 / "html-text-spans.json").exists())
            self.assertTrue((page1 / "text-reconstruction.json").exists())
            self.assertTrue((page1 / "visual-critic.json").exists())
            self.assertTrue((page1 / "browser-interaction-qa.json").exists())
            self.assertTrue((page1 / "editability-critic.json").exists())
            self.assertTrue((page1 / "extraction-diagnosis.json").exists())
            self.assertTrue((page1 / "repair-planner.json").exists())
            self.assertTrue((page1 / "hardcoding-critic.json").exists())
            self.assertTrue((page1 / "page-critique.json").exists())
            self.assertTrue((page1 / "repair-plan.json").exists())
            self.assertTrue((page1 / "page-repair-patch.json").exists())
            self.assertTrue((page1 / "repair-attempts.json").exists())
            evidence = json.loads((page1 / "pdf-fonts.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["fonts"][0]["family"], "TitleFont")
            self.assertEqual(evidence["fonts"][0]["count"], 1)
            self.assertTrue((page2 / "page-critique.json").exists())
            page2_critique = json.loads((page2 / "page-critique.json").read_text(encoding="utf-8"))
            self.assertTrue(page2_critique["accepted"])
            self.assertEqual(page2_critique["status"], "accepted")
            self.assertFalse(page2_critique["needs_repair"])
            self.assertEqual(page2_critique["overall_mark"], 96.5)
            self.assertTrue(page2_critique["rubric"])
            self.assertIn("95 score gate", page2_critique["pass_rationale"])
            self.assertEqual(page2_critique["top_failures"], [])
            self.assertTrue(page2_critique["html_mark_sheet"]["must_reject_if"])
            plan = json.loads((page1 / "repair-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["rerender_scope"], "page-only")
            self.assertEqual(plan["tasks"][0]["repair_subsystem"], "typography/title-grouping")
            patch = json.loads((page1 / "page-repair-patch.json").read_text(encoding="utf-8"))
            self.assertEqual(patch["status"], "auto-patch-ready")
            self.assertEqual(patch["operations"][0]["save_id"], "exact-page1-text1")
            self.assertEqual(patch["operations"][0]["styles"]["left"], "60px")
            self.assertEqual(patch["operation_contract"]["text_save_id_prefix"], "exact-page1-text")
            attempts = json.loads((page1 / "repair-attempts.json").read_text(encoding="utf-8"))
            self.assertEqual(attempts["status"], "pending")
            page2_plan = json.loads((page2 / "repair-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(page2_plan["status"], "accepted-no-repair")
            self.assertEqual(page2_plan["rerender_scope"], "none")
            page2_patch = json.loads((page2 / "page-repair-patch.json").read_text(encoding="utf-8"))
            self.assertEqual(page2_patch["status"], "not-needed")
            page2_attempts = json.loads((page2 / "repair-attempts.json").read_text(encoding="utf-8"))
            self.assertEqual(page2_attempts["status"], "not-needed")
            browser_page = json.loads((page1 / "browser-page-qa.json").read_text(encoding="utf-8"))
            self.assertEqual(browser_page["pageBox"]["page"], 1)
            self.assertTrue(browser_page["screenshot"].endswith("page-001-browser.png"))
            self.assertTrue(browser_page["packet_screenshot"].endswith("browser-screenshot.png"))
            self.assertTrue(browser_page["assertions"]["global_controls_visible"])
            self.assertEqual(browser_page["clean_export"]["pageCount"], 2)
            score = json.loads((page1 / "page-score.json").read_text(encoding="utf-8"))
            self.assertEqual(score["band"], "focused repair")
            self.assertEqual(index["lowest_page_score"], 88.25)
            self.assertEqual(index["page_scores"][0]["page_number"], 1)
            self.assertIn("judgement_agents", index["packets"][0])
            self.assertFalse(index["packets"][0]["judgement_agents"]["accepted"])
            self.assertTrue(index["packets"][1]["judgement_agents"]["accepted"])
            visual_critic = json.loads((page1 / "visual-critic.json").read_text(encoding="utf-8"))
            self.assertEqual(visual_critic["agent"], "visual-critic")
            self.assertFalse(visual_critic["accepted"])
            interaction_qa = json.loads((page1 / "browser-interaction-qa.json").read_text(encoding="utf-8"))
            self.assertEqual(interaction_qa["agent"], "browser-interaction-qa")
            self.assertTrue(interaction_qa["manual_browser_tasks"])

    def test_write_page_packets_returns_index_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path, assessment_path = self._write_packet_fixture(base)

            path = write_page_packets(
                project_dir,
                output_dir=base / "packets" / "pages",
                visual_report_path=visual_path,
                assessment_path=assessment_path,
            )

            self.assertEqual(path.resolve(), (base / "packets" / "page-packets.json").resolve())
            self.assertTrue(path.exists())

    def test_high_score_page_fails_when_judgement_agent_rejects_missing_media_slots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path, assessment_path = self._write_packet_fixture(base)
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            for page in visual["pages"]:
                page["score"] = 97.5
                page["mean_absolute_error"] = 3.0
            visual["accepted"] = True
            visual_path.write_text(json.dumps(visual), encoding="utf-8")
            assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
            for page in assessment["pages"]:
                page["score"] = 97.5
                page["accepted"] = True
                page["findings"] = []
            assessment["accepted"] = True
            assessment_path.write_text(json.dumps(assessment), encoding="utf-8")
            graph = json.loads((project_dir / "brochure.design.json").read_text(encoding="utf-8"))
            graph["pages"][0]["elements"].append(
                {
                    "id": "p001-photo-0001",
                    "type": "image",
                    "role": "photo-region",
                    "bbox": {"x": 20, "y": 20, "width": 200, "height": 100},
                    "replaceable": True,
                    "source_attribution": "synthetic PDF image region",
                }
            )
            (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")

            index = build_page_packets(
                project_dir,
                output_dir=base / "reports" / "demo" / "pages",
                visual_report_path=visual_path,
                assessment_path=assessment_path,
            )

            page1 = base / "reports" / "demo" / "pages" / "page-001"
            score = json.loads((page1 / "page-score.json").read_text(encoding="utf-8"))
            critique = json.loads((page1 / "page-critique.json").read_text(encoding="utf-8"))
            editability = json.loads((page1 / "editability-critic.json").read_text(encoding="utf-8"))
            self.assertFalse(index["accepted"])
            self.assertIn(1, index["failed_pages"])
            self.assertFalse(score["accepted"])
            self.assertFalse(critique["accepted"])
            self.assertFalse(editability["accepted"])
            self.assertTrue(
                any("no Browser media/logo/map slot evidence" in blocker for blocker in editability["blockers"])
            )

    def _write_packet_fixture(self, base: Path) -> tuple[Path, Path, Path]:
        project_dir = base / "projects" / "demo1234"
        (project_dir / "exact_layout_model").mkdir(parents=True)
        images_dir = base / "visual"
        images_dir.mkdir()
        for page in (1, 2):
            for kind in ("original", "generated", "diff"):
                Image.new("RGB", (16, 12), "#ffffff" if kind != "diff" else "#000000").save(
                    images_dir / f"page-{page:03d}-{kind}.png"
                )
        visual_path = images_dir / "visual-diff.json"
        visual_path.write_text(
            json.dumps(
                {
                    "score": 96,
                    "accepted": False,
                    "pages": [
                        {
                            "page_number": 1,
                            "score": 88.25,
                            "mean_absolute_error": 30.1,
                            "original": str(images_dir / "page-001-original.png"),
                            "generated": str(images_dir / "page-001-generated.png"),
                            "diff": str(images_dir / "page-001-diff.png"),
                        },
                        {
                            "page_number": 2,
                            "score": 96.5,
                            "mean_absolute_error": 9.2,
                            "original": str(images_dir / "page-002-original.png"),
                            "generated": str(images_dir / "page-002-generated.png"),
                            "diff": str(images_dir / "page-002-diff.png"),
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        assessment_path = images_dir / "html-assessment.json"
        assessment_path.write_text(
            json.dumps(
                {
                    "score": 96,
                    "accepted": False,
                    "pages": [
                        {
                            "page_number": 1,
                            "score": 88.25,
                            "accepted": False,
                            "purpose": "cover",
                            "findings": [
                                {
                                    "severity": "major",
                                    "issue": "Cover title is shifted",
                                    "evidence": "hotspot left rail",
                                    "likely_cause": "Title grouping mismatch",
                                    "repair_subsystem": "typography/title-grouping",
                                    "recommended_tool": "PDF text spans",
                                    "roles": ["cover-title"],
                                    "element_ids": ["p001-text-0001"],
                                }
                            ],
                        },
                        {
                            "page_number": 2,
                            "score": 96.5,
                            "accepted": True,
                            "purpose": "editorial",
                            "findings": [],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (project_dir / "brochure.design.json").write_text(
            json.dumps(
                {
                    "pages": [
                        {
                            "page_number": 1,
                            "purpose": "cover",
                            "size": {"width": 1200, "height": 800},
                            "elements": [
                                {
                                    "id": "p001-text-0001",
                                    "type": "text",
                                    "role": "cover-title",
                                    "bbox_raw": {"x": 60, "y": 80, "width": 420, "height": 44},
                                }
                            ],
                        },
                        {"page_number": 2, "purpose": "editorial", "elements": []},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (project_dir / "exact_layout_model" / "extraction-inventory.json").write_text(
            json.dumps(
                {
                    "pages": [
                        {"page_number": 1, "page_purpose": "cover", "editable_text_blocks": [{"id": "t1"}]},
                        {"page_number": 2, "page_purpose": "editorial", "editable_text_blocks": []},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (project_dir / "exact_layout_model" / "exact-layout.json").write_text(
            json.dumps(
                {
                    "pages": [
                        {
                            "page_number": 1,
                            "text_spans": [
                                {
                                    "id": "t1",
                                    "text": "Title",
                                    "font": {"family": "TitleFont", "size": 42, "is_bold": False},
                                    "color": "#ffffff",
                                }
                            ],
                            "image_boxes": [{"id": "img1", "bbox": {"x": 0, "y": 0, "width": 20, "height": 10}}],
                            "semantic_regions": [{"kind": "cover"}],
                        },
                        {"page_number": 2, "text_spans": [], "image_boxes": [], "semantic_regions": []},
                    ]
                }
            ),
            encoding="utf-8",
        )
        browser_screenshot = base / "page-001-browser.png"
        browser_screenshot.write_bytes(b"fake-png")
        (project_dir / "browser_qa.json").write_text(
            json.dumps(
                {
                    "editor": {"pageCount": 2, "pageBoxes": [{"page": 1, "textFields": 4}]},
                    "export": {"pageCount": 2},
                    "page_screenshots": {"1": str(browser_screenshot)},
                    "assertions": {
                        "global_controls_visible": True,
                        "state_roundtrip_preserved": True,
                        "typed_text_font_preserved": True,
                        "media_slots_have_actionable_controls": True,
                        "image_replacement_roundtrip_preserved": True,
                        "logo_replacement_roundtrip_preserved": True,
                        "map_replacement_roundtrip_preserved": True,
                        "export_has_no_editor_chrome": True,
                        "source_preserved_pages_have_no_giant_interactive_hotspots": True,
                    },
                    "layout_audit": {"textOverlaps": [{"page": 1, "a": "a", "b": "b"}]},
                }
            ),
            encoding="utf-8",
        )
        return project_dir, visual_path, assessment_path
