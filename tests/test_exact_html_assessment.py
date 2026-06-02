from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from brochure_maker.exact_html_assessment import (
    attach_html_assessment_to_browser_qa,
    build_html_assessment,
    write_html_assessment,
)
from brochure_maker.exact_text_reconstruction import build_text_reconstruction_report


class TestExactHtmlAssessment(unittest.TestCase):
    def test_assessment_maps_visual_hotspot_to_repair_subsystem(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            self.assertFalse(report["accepted"])
            self.assertIn("not a 95% pass yet", report["summary"])
            page = report["pages"][0]
            self.assertEqual(page["page_number"], 1)
            self.assertTrue(page["hotspots"])
            subsystems = {finding["repair_subsystem"] for finding in page["findings"]}
            self.assertIn("typography/title-grouping", subsystems)

    def test_assessment_attach_updates_browser_qa(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            assessment_path = write_html_assessment(
                project_dir,
                base / "html-assessment.json",
                visual_report_path=visual_path,
            )

            qa_path = attach_html_assessment_to_browser_qa(project_dir, assessment_path)

            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertIn("html_assessment", qa)
            self.assertFalse(qa["html_assessment"]["accepted"])
            self.assertTrue(qa["html_assessment"]["next_repair_tasks"])

    def test_assessment_flags_fragmented_pdf_text_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            graph = json.loads((project_dir / "brochure.design.json").read_text(encoding="utf-8"))
            graph["pages"][0]["elements"] = [
                {
                    "id": "word-1",
                    "role": "body",
                    "type": "text",
                    "text": "environment",
                    "bbox": {"x": 0.05, "y": 0.42, "width": 0.08, "height": 0.02},
                },
                {
                    "id": "word-2",
                    "role": "body",
                    "type": "text",
                    "text": "that",
                    "bbox": {"x": 0.18, "y": 0.42, "width": 0.03, "height": 0.02},
                },
                {
                    "id": "word-3",
                    "role": "body",
                    "type": "text",
                    "text": "balances",
                    "bbox": {"x": 0.25, "y": 0.42, "width": 0.06, "height": 0.02},
                },
                {
                    "id": "word-4",
                    "role": "body",
                    "type": "text",
                    "text": "creativity",
                    "bbox": {"x": 0.38, "y": 0.42, "width": 0.07, "height": 0.02},
                },
            ]
            (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            page_findings = report["pages"][0]["findings"]
            self.assertTrue(
                any(finding.get("repair_subsystem") == "typography/text-line-reconstruction" for finding in page_findings)
            )

    def test_text_reconstruction_flags_contacts_left_as_raw_pdf_fragments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "contact-demo"
            project_dir.mkdir(parents=True)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps(
                    {
                        "layout": {
                            "field_config": {
                                "contacts": [
                                    {
                                        "key": "tom",
                                        "name": "Tom Boggis",
                                        "phone": "07795 070 676",
                                        "email": "tom.boggis@bbgreal.com",
                                        "targets": ["name"],
                                        "hide_targets": ["phone", "email"],
                                        "anchor": {"page_num": 7},
                                    }
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                '<div id="page7"><p data-save-id="name">Tom Boggis</p>'
                '<p data-save-id="phone">07795 070 676</p>'
                '<p data-save-id="email">tom.boggis@bbgreal.com</p></div>',
                encoding="utf-8",
            )

            report = build_text_reconstruction_report(project_dir)

            self.assertFalse(report["accepted"])
            self.assertTrue(
                any(defect["repair_subsystem"] == "contacts/grouping" for defect in report["pages"][0]["defects"])
            )

    def test_text_reconstruction_accepts_semantic_contact_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir = base / "projects" / "contact-demo"
            project_dir.mkdir(parents=True)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps(
                    {
                        "layout": {
                            "field_config": {
                                "contacts": [
                                    {
                                        "key": "tom",
                                        "name": "Tom Boggis",
                                        "phone": "07795 070 676",
                                        "email": "tom.boggis@bbgreal.com",
                                        "targets": ["name"],
                                        "hide_targets": ["phone", "email"],
                                        "anchor": {"page_num": 7},
                                    }
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                '<div id="page7"><p data-save-id="name" data-contact-semantic-block="true">'
                'Tom Boggis<br/>07795 070 676<br/>tom.boggis@bbgreal.com</p></div>',
                encoding="utf-8",
            )

            report = build_text_reconstruction_report(project_dir)

            self.assertTrue(report["accepted"])
            self.assertEqual(report["pages"][0]["defects"], [])

    def test_assessment_flags_cover_title_single_glyph_fragments_even_when_visual_score_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            visual["score"] = 97.2
            visual["accepted"] = True
            visual["pages"][0]["score"] = 97.2
            clean_generated = base / "generated-clean.png"
            Image.new("RGB", (120, 80), "#ffffff").save(clean_generated)
            visual["pages"][0]["generated"] = str(clean_generated)
            visual_path.write_text(json.dumps(visual), encoding="utf-8")
            graph = json.loads((project_dir / "brochure.design.json").read_text(encoding="utf-8"))
            graph["pages"][0]["elements"] = [
                {
                    "id": f"title-{index}",
                    "role": "cover-title",
                    "type": "text",
                    "text": char,
                    "bbox": {"x": 0.1, "y": 0.1 + index * 0.04, "width": 0.03, "height": 0.03},
                }
                for index, char in enumerate("Sekforde")
            ]
            (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            page_findings = report["pages"][0]["findings"]
            self.assertFalse(report["accepted"])
            self.assertFalse(report["pages"][0]["accepted"])
            self.assertTrue(
                any(
                    finding.get("issue") == "Cover writing is still represented by many single-glyph title fragments"
                    and finding.get("repair_subsystem") == "typography/title-grouping"
                    and finding.get("severity") == "major"
                    for finding in page_findings
                )
            )

    def test_assessment_accepts_single_glyph_evidence_when_grouped_title_field_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            visual["score"] = 97.2
            visual["accepted"] = True
            visual["pages"][0]["score"] = 97.2
            clean_generated = base / "generated-clean.png"
            Image.new("RGB", (120, 80), "#ffffff").save(clean_generated)
            visual["pages"][0]["generated"] = str(clean_generated)
            visual_path.write_text(json.dumps(visual), encoding="utf-8")
            graph = json.loads((project_dir / "brochure.design.json").read_text(encoding="utf-8"))
            graph["pages"][0]["elements"] = [
                *[
                    {
                        "id": f"title-{index}",
                        "role": "cover-title",
                        "type": "text",
                        "text": char,
                        "bbox": {"x": 0.1, "y": 0.1 + index * 0.04, "width": 0.03, "height": 0.03},
                        "node_class": "editable-content",
                    }
                    for index, char in enumerate("Sekforde")
                ],
                {
                    "id": "cover-title-group-1",
                    "role": "cover-title",
                    "type": "text",
                    "text": "Sekforde",
                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.12, "height": 0.4},
                    "node_class": "structured-field",
                },
            ]
            (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            page_findings = report["pages"][0]["findings"]
            self.assertFalse(
                any(
                    finding.get("issue") == "Cover writing is still represented by many single-glyph title fragments"
                    for finding in page_findings
                )
            )

    def test_browser_export_and_global_control_assertions_are_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            (project_dir / "browser_qa.json").write_text(
                json.dumps(
                    {
                        "editor": {"globalSections": 7},
                        "export": {"contenteditable": 1, "inputs": 0, "scripts": 0, "toolbar": 0, "fieldsPanel": 0},
                        "assertions": {"global_controls_visible": False},
                        "visual_diff": {"score": 88.5, "accepted": False, "report": str(visual_path)},
                    }
                ),
                encoding="utf-8",
            )

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            browser_issues = {finding["issue"] for finding in report["browser_findings"]}
            self.assertIn("Clean export still contains editor chrome: contenteditable", browser_issues)
            self.assertIn("Browser did not confirm visible global controls", browser_issues)

    def test_false_media_browser_assertion_rejects_high_visual_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            visual["score"] = 99.0
            visual["accepted"] = True
            visual["pages"][0]["score"] = 99.0
            visual["pages"][0]["hotspots"] = []
            visual_path.write_text(json.dumps(visual), encoding="utf-8")
            (project_dir / "browser_qa.json").write_text(
                json.dumps(
                    {
                        "export": {"contenteditable": 0, "inputs": 0, "scripts": 0, "toolbar": 0, "fieldsPanel": 0},
                        "assertions": {
                            "global_controls_visible": True,
                            "media_slots_have_actionable_controls": False,
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            self.assertFalse(report["accepted"])
            self.assertIn("Browser did not confirm media slots have actionable controls", report["blockers"])

    def test_browser_overlap_warnings_feed_repair_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            (project_dir / "browser_qa.json").write_text(
                json.dumps(
                    {
                        "editor": {"globalSections": 7},
                        "clean_export": {},
                        "layout_audit": {
                            "textOverlaps": [],
                            "textOverlapWarnings": [
                                {
                                    "page": 1,
                                    "severity": "major",
                                    "first": {"id": "title-a", "text": "Sekforde"},
                                    "second": {"id": "title-b", "text": "Street"},
                                    "overlap_ratio": 0.72,
                                }
                            ],
                        },
                        "visual_diff": {"score": 96.5, "accepted": True, "report": str(visual_path)},
                    }
                ),
                encoding="utf-8",
            )

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            self.assertFalse(report["accepted"])
            self.assertIn(
                "Browser layout audit found possible overlapping editable text blocks",
                report["blockers"],
            )
            task_subsystems = {task["repair_subsystem"] for task in report["next_repair_tasks"]}
            self.assertIn("typography/text-layout", task_subsystems)
            self.assertFalse(report["pages"][0]["accepted"])
            self.assertTrue(
                any(
                    finding.get("issue") == "Browser layout audit found possible overlapping editable text blocks"
                    for finding in report["pages"][0]["findings"]
                )
            )

    def test_contact_page_agency_logo_with_default_asset_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            visual["score"] = 97.0
            visual["accepted"] = True
            visual["pages"][0]["score"] = 97.0
            clean_generated = base / "generated-clean.png"
            Image.new("RGB", (120, 80), "#ffffff").save(clean_generated)
            visual["pages"][0]["generated"] = str(clean_generated)
            visual_path.write_text(json.dumps(visual), encoding="utf-8")
            graph = json.loads((project_dir / "brochure.design.json").read_text(encoding="utf-8"))
            graph["pages"][0]["purpose"] = "contacts and terms"
            graph["pages"][0]["elements"] = [
                {
                    "id": "agency-logo-agency1",
                    "role": "agency-logo",
                    "type": "agency-logo",
                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.1},
                    "metadata": {"default_asset_url": "/api/projects/demo/exact_assets/images/agency-logo.png"},
                }
            ]
            (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            issues = {
                finding.get("issue")
                for finding in report["pages"][0]["findings"]
            }
            self.assertNotIn("Agency logo needs visual source fidelity, not just a text fallback", issues)

    def test_contact_page_raw_agency_logo_evidence_does_not_mask_editor_default_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            visual["score"] = 97.0
            visual["accepted"] = True
            visual["pages"][0]["score"] = 97.0
            clean_generated = base / "generated-clean.png"
            Image.new("RGB", (120, 80), "#ffffff").save(clean_generated)
            visual["pages"][0]["generated"] = str(clean_generated)
            visual_path.write_text(json.dumps(visual), encoding="utf-8")
            graph = json.loads((project_dir / "brochure.design.json").read_text(encoding="utf-8"))
            graph["pages"][0]["purpose"] = "contacts and terms"
            graph["pages"][0]["elements"] = [
                {
                    "id": "p005-image-0001",
                    "role": "agency-logo",
                    "type": "agency-logo",
                    "node_class": "extracted-evidence",
                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.12, "height": 0.12},
                    "metadata": {},
                },
                {
                    "id": "agency-logo-agency1",
                    "role": "agency-logo",
                    "type": "agency-logo",
                    "node_class": "editor-control",
                    "bbox": {"x": 0.1, "y": 0.5, "width": 0.2, "height": 0.1},
                    "metadata": {"default_asset_url": "/api/projects/demo/exact_assets/images/agency-logo.png"},
                },
            ]
            (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            issues = {finding.get("issue") for finding in report["pages"][0]["findings"]}
            self.assertNotIn("Agency logo needs visual source fidelity, not just a text fallback", issues)

    def test_contact_page_agency_logo_without_default_asset_is_flagged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_dir, visual_path = self._write_project_fixture(base)
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            visual["score"] = 97.0
            visual["accepted"] = True
            visual["pages"][0]["score"] = 97.0
            clean_generated = base / "generated-clean.png"
            Image.new("RGB", (120, 80), "#ffffff").save(clean_generated)
            visual["pages"][0]["generated"] = str(clean_generated)
            visual_path.write_text(json.dumps(visual), encoding="utf-8")
            graph = json.loads((project_dir / "brochure.design.json").read_text(encoding="utf-8"))
            graph["pages"][0]["purpose"] = "contacts and terms"
            graph["pages"][0]["elements"] = [
                {
                    "id": "agency-logo-agency1",
                    "role": "agency-logo",
                    "type": "agency-logo",
                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.1},
                    "metadata": {},
                }
            ]
            (project_dir / "brochure.design.json").write_text(json.dumps(graph), encoding="utf-8")

            report = build_html_assessment(project_dir, visual_report_path=visual_path)

            self.assertTrue(
                any(
                    finding.get("issue") == "Agency logo needs visual source fidelity, not just a text fallback"
                    for finding in report["pages"][0]["findings"]
                )
            )

    def _write_project_fixture(self, base: Path) -> tuple[Path, Path]:
        project_dir = base / "demo-assess"
        project_dir.mkdir()
        original = base / "original.png"
        generated = base / "generated.png"
        Image.new("RGB", (120, 80), "#ffffff").save(original)
        generated_image = Image.new("RGB", (120, 80), "#ffffff")
        draw = ImageDraw.Draw(generated_image)
        draw.rectangle((10, 10, 50, 70), fill="#000000")
        generated_image.save(generated)
        visual_path = base / "visual-diff.json"
        visual_path.write_text(
            json.dumps(
                {
                    "score": 88.5,
                    "accepted": False,
                    "pages": [
                        {
                            "page_number": 1,
                            "score": 88.5,
                            "mean_absolute_error": 29.3,
                            "original": str(original),
                            "generated": str(generated),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (project_dir / "browser_qa.json").write_text(
            json.dumps(
                {
                    "editor": {"globalSections": 7},
                    "clean_export": {},
                    "layout_audit": {"textOverlaps": []},
                    "visual_diff": {"score": 88.5, "accepted": False, "report": str(visual_path)},
                }
            ),
            encoding="utf-8",
        )
        (project_dir / "brochure.design.json").write_text(
            json.dumps(
                {
                    "page_count": 1,
                    "theme_tokens": {
                        "global_controls": {
                            "palette": ["accent"],
                            "typography": ["title"],
                            "logo": ["source mark"],
                            "amenity_icons": ["icons"],
                            "images": ["fit"],
                            "map": ["preserve"],
                            "agents": ["logo"],
                        }
                    },
                    "pages": [
                        {
                            "page_number": 1,
                            "purpose": "cover",
                            "elements": [
                                {
                                    "id": "title",
                                    "role": "cover-title",
                                    "type": "text",
                                    "bbox": {"x": 0.0, "y": 0.0, "width": 0.6, "height": 1.0},
                                },
                                {
                                    "id": "glyph-a",
                                    "role": "section-heading",
                                    "type": "text",
                                    "text": "A",
                                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
                                },
                                {
                                    "id": "glyph-b",
                                    "role": "section-heading",
                                    "type": "text",
                                    "text": "B",
                                    "bbox": {"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.1},
                                },
                                {
                                    "id": "glyph-c",
                                    "role": "section-heading",
                                    "type": "text",
                                    "text": "C",
                                    "bbox": {"x": 0.1, "y": 0.3, "width": 0.1, "height": 0.1},
                                },
                                {
                                    "id": "glyph-d",
                                    "role": "section-heading",
                                    "type": "text",
                                    "text": "D",
                                    "bbox": {"x": 0.1, "y": 0.4, "width": 0.1, "height": 0.1},
                                },
                                {
                                    "id": "glyph-e",
                                    "role": "section-heading",
                                    "type": "text",
                                    "text": "E",
                                    "bbox": {"x": 0.1, "y": 0.5, "width": 0.1, "height": 0.1},
                                },
                                {
                                    "id": "glyph-f",
                                    "role": "section-heading",
                                    "type": "text",
                                    "text": "F",
                                    "bbox": {"x": 0.1, "y": 0.6, "width": 0.1, "height": 0.1},
                                },
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return project_dir, visual_path
