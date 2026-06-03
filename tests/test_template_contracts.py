import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

ai_chat_stub = types.ModuleType("brochure_maker.ai_chat")


async def _stub_chat_with_brochure(*args, **kwargs):
    return {}


ai_chat_stub.chat_with_brochure = _stub_chat_with_brochure
sys.modules.setdefault("brochure_maker.ai_chat", ai_chat_stub)

map_generator_stub = types.ModuleType("brochure_maker.map_generator")


async def _stub_generate_neighbourhood_map(*args, **kwargs):
    return {"format": "svg", "svg_content": "<svg></svg>"}


map_generator_stub.generate_neighbourhood_map = _stub_generate_neighbourhood_map
sys.modules.setdefault("brochure_maker.map_generator", map_generator_stub)

import app as app_module
from brochure_maker.html_generator import generate_brochure_html, generate_clean_html
from brochure_maker.layout_variants import (
    layout_variants_for,
    layout_variants_metadata,
    render_slide_html,
    resolve_layout_variant,
)
from brochure_maker.template_manager import (
    apply_template_to_analysis,
    save_template,
    verify_clone_against_template,
)


class TestLayoutVariants(unittest.TestCase):
    def test_registry_exposes_seeded_layout_ids_with_metadata(self):
        expected = {
            "cover": ["default", "logo-left"],
            "text_and_photos": ["default", "image-1", "image-2", "image-3", "image-4", "title-top", "text-right", "text-card-overlay"],
            "photo_gallery": ["default", "image-1", "image-2", "image-3", "image-4", "title-band", "caption-card"],
            "services_grid": ["default", "image-1", "image-2", "image-3", "image-4", "title-top", "text-card"],
            "location": ["default", "image-1", "image-2", "image-3", "image-4", "text-card", "title-left"],
            "highlights_grid": ["default", "compact", "two-column", "title-sidebar"],
            "floor_plan": ["default", "plan-led", "specs-right", "title-top"],
            "travel_map": ["default", "map-left", "title-top"],
            "contacts": ["default", "stacked"],
        }

        for slide_type, layout_ids in expected.items():
            variants = layout_variants_for(slide_type)
            self.assertEqual([variant["id"] for variant in variants], layout_ids)
            self.assertEqual(variants[0]["id"], "default")
            for variant in variants:
                self.assertEqual(variant["slide_type"], slide_type)
                self.assertIn("description", variant)
                self.assertIn("image_count", variant)
                self.assertIsInstance(variant["slots"], list)
                self.assertIn("title_position", variant)
                self.assertIn("text_position", variant)
                self.assertIsInstance(variant["text_zones"], list)
                self.assertIsInstance(variant["structure_tags"], list)

    def test_layout_metadata_groups_variants_for_frontend(self):
        metadata = layout_variants_metadata()

        self.assertEqual(metadata["renderEndpoint"], "/api/slides/render")
        self.assertIn("variants", metadata)
        self.assertIn("text_and_photos", metadata["variants"])
        self.assertIn(
            "image-4",
            [variant["id"] for variant in metadata["variants"]["text_and_photos"]],
        )

    def test_missing_and_unknown_layout_ids_fall_back_safely(self):
        missing = resolve_layout_variant("cover", None)
        self.assertEqual(missing["layout_id"], "default")
        self.assertEqual(missing["requested_layout_id"], "default")
        self.assertFalse(missing["layout_fallback"])

        unknown = resolve_layout_variant("cover", "future-cover-layout")
        self.assertEqual(unknown["layout_id"], "default")
        self.assertEqual(unknown["requested_layout_id"], "future-cover-layout")
        self.assertTrue(unknown["layout_fallback"])

    def test_render_slide_html_returns_fallback_metadata(self):
        result = render_slide_html(
            slide_type="cover",
            layout_id="future-cover-layout",
            slide_num=3,
            content={"heading": "Fresh Building"},
            context={"location": "Leeds"},
        )

        self.assertIn("Fresh Building", result["html"])
        self.assertEqual(result["slide_type"], "cover")
        self.assertEqual(result["layout_id"], "default")
        self.assertTrue(result["layout_fallback"])

    def test_generated_brochure_exposes_layout_metadata_before_slide_manager(self):
        html = generate_brochure_html({
            "brochure_name": "Unit House",
            "location": "Leeds",
            "slides": [
                {
                    "type": "cover",
                    "content": {"heading": "Unit House"},
                }
            ],
        })

        start_marker = '<script type="application/json" id="layoutVariants">'
        start = html.index(start_marker) + len(start_marker)
        end = html.index("</script>", start)
        metadata = json.loads(html[start:end])

        self.assertLess(html.index(start_marker), html.index("function getLayoutMetadata"))
        self.assertEqual(metadata["renderEndpoint"], "/api/slides/render")
        self.assertIn("cover", metadata["variants"])
        self.assertIn("logo-left", [variant["id"] for variant in metadata["variants"]["cover"]])

    def test_structural_layout_metadata_exposes_text_and_title_context(self):
        gallery_variants = {
            variant["id"]: variant
            for variant in layout_variants_for("photo_gallery")
        }

        self.assertEqual(gallery_variants["title-band"]["title_position"], "top_band")
        self.assertEqual(gallery_variants["title-band"]["text_position"], "top_band")
        self.assertIn("title-band", gallery_variants["title-band"]["slots"])
        self.assertIn("intro", gallery_variants["title-band"]["slots"])
        self.assertIn("gallery-copy", gallery_variants["title-band"]["structure_tags"])
        self.assertEqual(gallery_variants["caption-card"]["text_zones"], ["caption-card"])
        self.assertIn("caption-card-body", gallery_variants["caption-card"]["slots"])

        text_variants = {
            variant["id"]: variant
            for variant in layout_variants_for("text_and_photos")
        }
        self.assertEqual(text_variants["text-card-overlay"]["title_position"], "overlay_card")
        self.assertIn("text-card", text_variants["text-card-overlay"]["structure_tags"])

    def test_clean_render_preserves_structural_slots_without_editor_attributes(self):
        html = generate_clean_html({
            "brochure_name": "Unit House",
            "location": "Leeds",
            "slides": [
                {
                    "type": "photo_gallery",
                    "layout_id": "title-band",
                    "content": {
                        "heading": "Gallery Story",
                        "intro_text": "Fresh intro text",
                        "images": [
                            {"description": "Exterior", "caption": "Arrival"},
                            {"description": "Workspace", "caption": "Work"},
                            {"description": "Lounge", "caption": "Break"},
                            {"description": "Detail", "caption": "Detail"},
                        ],
                    },
                }
            ],
        })

        self.assertIn('data-slide-type="photo_gallery"', html)
        self.assertIn('data-layout-id="title-band"', html)
        self.assertIn('data-slot-id="title-band"', html)
        self.assertIn('data-slot-id="intro"', html)
        self.assertIn("Gallery Story", html)
        self.assertNotIn("contenteditable", html)


class TestTemplateContracts(unittest.TestCase):
    def test_save_template_strips_content_and_stores_layout_metadata(self):
        analysis = {
            "colour_scheme": {"primary": "#123456"},
            "typography": {"heading_style": "serif"},
            "slides": [
                {
                    "type": "cover",
                    "layout_id": "future-cover-layout",
                    "content": {
                        "heading": "Do Not Clone This Heading",
                        "images": [
                            {
                                "description": "Do Not Clone This Photo",
                                "caption": "Do Not Clone This Caption",
                            }
                        ],
                    },
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            with patch("brochure_maker.template_manager.TEMPLATES_STORE", Path(tmp)):
                template = save_template("Broker Preset", analysis)
                saved_template = json.loads((Path(tmp) / "broker_preset" / "template.json").read_text(encoding="utf-8"))

        template_json = json.dumps(template)
        self.assertNotIn("Do Not Clone This Heading", template_json)
        self.assertNotIn("Do Not Clone This Photo", template_json)
        self.assertNotIn("Do Not Clone This Caption", template_json)
        self.assertEqual(template["colour_scheme"]["primary"], "#123456")
        self.assertEqual(template["typography"]["heading_style"], "serif")
        self.assertEqual(template["slide_sequence"][0]["slide_type"], "cover")
        self.assertEqual(template["slides"][0]["layout_id"], "future-cover-layout")
        self.assertEqual(template["slides"][0]["resolved_layout_id"], "default")
        self.assertEqual(template["slides"][0]["slot_schema"]["heading"]["kind"], "text")
        self.assertEqual(template["slides"][0]["slot_schema"]["images"]["kind"], "image_collection")
        self.assertEqual(template["clone_plan"]["minimum_confidence"], 95)
        self.assertEqual(template["clone_plan"]["slides"][0]["slot_schema"]["heading"]["kind"], "text")

        self.assertEqual(saved_template["clone_plan"]["slides"][0]["slot_schema"]["images"]["kind"], "image_collection")

    def test_apply_template_reuses_style_and_sequence_with_fresh_content(self):
        template = {
            "name": "Two Slide Preset",
            "version": 2,
            "colour_scheme": {"primary": "#654321"},
            "typography": {"heading_style": "sans-serif"},
            "slide_sequence": [
                {"slide_type": "cover", "layout_id": "default"},
                {"slide_type": "contacts", "layout_id": "future-contact-layout"},
            ],
        }
        fresh_analysis = {
            "colour_scheme": {"primary": "#abcdef"},
            "typography": {"heading_style": "serif"},
            "slides": [
                {
                    "type": "contacts",
                    "content": {"contacts": [{"name": "Fresh Contact"}]},
                },
                {
                    "type": "cover",
                    "content": {"heading": "Fresh Cover"},
                },
                {
                    "type": "highlights_grid",
                    "content": {"features": [{"text": "Fresh Extra"}]},
                },
            ],
        }

        applied = apply_template_to_analysis(fresh_analysis, template)

        self.assertEqual(applied["colour_scheme"]["primary"], "#654321")
        self.assertEqual(applied["typography"]["heading_style"], "sans-serif")
        self.assertEqual([slide["type"] for slide in applied["slides"]], ["cover", "contacts", "highlights_grid"])
        self.assertEqual(applied["slides"][0]["content"]["heading"], "Fresh Cover")
        self.assertEqual(applied["slides"][1]["content"]["contacts"][0]["name"], "Fresh Contact")
        self.assertEqual(applied["slides"][1]["layout_id"], "future-contact-layout")
        self.assertEqual(applied["slides"][1]["resolved_layout_id"], "default")
        self.assertTrue(applied["slides"][1]["layout_fallback"])
        self.assertEqual(applied["slides"][2]["template_append_reason"], "not_in_template_sequence")

    def test_structural_clone_preserves_layout_id_and_slot_schema_with_fresh_content(self):
        analysis = {
            "colour_scheme": {"primary": "#123456"},
            "typography": {"heading_style": "serif"},
            "slides": [
                {
                    "type": "photo_gallery",
                    "layout_id": "caption-card",
                    "content": {
                        "heading": "Do Not Clone Gallery Heading",
                        "intro_text": "Do Not Clone Gallery Intro",
                        "caption_title": "Do Not Clone Card Title",
                        "caption_body": "Do Not Clone Card Body",
                        "images": [
                            {"description": "Do Not Clone Photo", "caption": "Do Not Clone Caption"},
                        ],
                    },
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            with patch("brochure_maker.template_manager.TEMPLATES_STORE", Path(tmp)):
                template = save_template("Structural Gallery", analysis)

        template_json = json.dumps(template)
        self.assertNotIn("Do Not Clone Gallery Heading", template_json)
        self.assertNotIn("Do Not Clone Gallery Intro", template_json)
        self.assertNotIn("Do Not Clone Photo", template_json)
        self.assertEqual(template["slides"][0]["layout_id"], "caption-card")
        self.assertEqual(template["slides"][0]["resolved_layout_id"], "caption-card")
        self.assertFalse(template["slides"][0]["layout_fallback"])
        self.assertEqual(template["slides"][0]["slot_schema"]["heading"]["kind"], "text")
        self.assertEqual(template["slides"][0]["slot_schema"]["intro_text"]["kind"], "text")
        self.assertEqual(template["slides"][0]["slot_schema"]["images"]["kind"], "image_collection")

        fresh_analysis = {
            "colour_scheme": {"primary": "#abcdef"},
            "typography": {"heading_style": "sans-serif"},
            "slides": [
                {
                    "type": "photo_gallery",
                    "content": {
                        "heading": "Fresh Gallery Heading",
                        "intro_text": "Fresh Gallery Intro",
                        "images": [{"description": "Fresh Photo", "caption": "Fresh Caption"}],
                    },
                }
            ],
        }

        applied = apply_template_to_analysis(fresh_analysis, template)

        self.assertEqual(applied["colour_scheme"]["primary"], "#123456")
        self.assertEqual(applied["typography"]["heading_style"], "serif")
        self.assertEqual(applied["slides"][0]["type"], "photo_gallery")
        self.assertEqual(applied["slides"][0]["layout_id"], "caption-card")
        self.assertEqual(applied["slides"][0]["resolved_layout_id"], "caption-card")
        self.assertFalse(applied["slides"][0]["layout_fallback"])
        self.assertEqual(applied["slides"][0]["content"]["heading"], "Fresh Gallery Heading")
        self.assertEqual(applied["slides"][0]["content"]["intro_text"], "Fresh Gallery Intro")

    def test_strict_clone_uses_exact_template_sequence_and_verifies(self):
        template = {
            "name": "Exact Clone",
            "version": 2,
            "colour_scheme": {"primary": "#654321"},
            "typography": {"heading_style": "serif"},
            "logo": {"type": "svg:diamond", "coverPosition": "top-right"},
            "slides": [
                {
                    "slide_type": "cover",
                    "layout_id": "logo-left",
                    "slot_schema": {"heading": {"kind": "text", "value_type": "str"}},
                },
                {
                    "slide_type": "photo_gallery",
                    "layout_id": "image-2",
                    "slot_schema": {
                        "heading": {"kind": "text", "value_type": "str"},
                        "images": {"kind": "image_collection", "value_type": "list"},
                    },
                },
            ],
            "clone_plan": {"minimum_confidence": 95},
        }
        fresh_analysis = {
            "colour_scheme": {"primary": "#abcdef"},
            "typography": {"heading_style": "sans-serif"},
            "slides": [
                {"type": "photo_gallery", "content": {"heading": "Fresh Gallery"}},
                {"type": "cover", "content": {"heading": "Fresh Cover"}},
                {"type": "contacts", "content": {"contacts": [{"name": "Extra"}]}},
            ],
        }

        applied = apply_template_to_analysis(fresh_analysis, template, strict_sequence=True)

        self.assertEqual([slide["type"] for slide in applied["slides"]], ["cover", "photo_gallery"])
        self.assertEqual([slide["layout_id"] for slide in applied["slides"]], ["logo-left", "image-2"])
        self.assertEqual(applied["slides"][0]["content"]["heading"], "Fresh Cover")
        self.assertEqual(applied["slides"][1]["content"]["heading"], "Fresh Gallery")
        self.assertIn("images", applied["slides"][1]["content"])
        self.assertEqual(applied["clone_verification"]["status"], "passed")
        self.assertGreaterEqual(applied["clone_verification"]["confidence"], 95)
        self.assertEqual(applied["applied_template"]["mode"], "strict_structure")
        self.assertEqual(applied["logo"]["type"], "svg:diamond")

    def test_clone_verifier_rejects_unresolved_layouts(self):
        template = {
            "name": "Impossible Layout",
            "version": 2,
            "slides": [
                {
                    "slide_type": "contacts",
                    "layout_id": "future-contact-layout",
                    "slot_schema": {"contacts": {"kind": "contact_collection", "value_type": "list"}},
                },
            ],
            "clone_plan": {"minimum_confidence": 95},
        }
        analysis = apply_template_to_analysis(
            {"slides": [{"type": "contacts", "content": {"contacts": [{"name": "Fresh"}]}}]},
            template,
            strict_sequence=True,
        )

        verification = verify_clone_against_template(analysis, template)

        self.assertEqual(verification["status"], "needs_review")
        self.assertLess(verification["confidence"], 95)
        self.assertFalse(next(check for check in verification["checks"] if check["name"] == "layout_registry_resolution")["passed"])


class TestTemplateProcessing(unittest.IsolatedAsyncioTestCase):
    async def test_process_brochure_applies_template_ref_before_writing_and_rendering(self):
        template_ref = {
            "name": "Processing Preset",
            "version": 2,
            "colour_scheme": {"primary": "#101010"},
            "typography": {"heading_style": "serif"},
            "slide_sequence": [
                {"slide_type": "cover", "layout_id": "default"},
                {"slide_type": "contacts", "layout_id": "stacked"},
            ],
            "clone_plan": {"minimum_confidence": 95},
        }
        fresh_analysis = {
            "brochure_name": "Fresh House",
            "location": "London",
            "address": "",
            "postcode": "",
            "colour_scheme": {"primary": "#ffffff"},
            "typography": {"heading_style": "sans-serif"},
            "slides": [
                {"type": "contacts", "content": {"contacts": [{"name": "Fresh Agent"}]}},
                {"type": "cover", "content": {"heading": "Fresh House"}},
            ],
        }
        captured = {}

        async def fake_analyse_brochure(pdf_data):
            self.assertEqual(pdf_data["page_count"], 2)
            return fresh_analysis

        def fake_generate_brochure_html(*, analysis, project_id, output_path):
            captured["analysis"] = analysis
            Path(output_path).write_text("<html></html>", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "template_ref.json").write_text(json.dumps(template_ref), encoding="utf-8")

            with patch.object(app_module, "extract_pdf", return_value={"page_count": 2}), \
                 patch.object(app_module, "analyse_brochure", side_effect=fake_analyse_brochure), \
                 patch.object(app_module, "generate_brochure_html", side_effect=fake_generate_brochure_html):
                await app_module._process_brochure("unit-test", "source.pdf", str(project_dir))

            saved = json.loads((project_dir / "analysis.json").read_text(encoding="utf-8"))
            verification_saved = (project_dir / "verification.json").exists()

        self.assertEqual(saved["colour_scheme"]["primary"], "#101010")
        self.assertEqual(saved["typography"]["heading_style"], "serif")
        self.assertEqual([slide["type"] for slide in saved["slides"]], ["cover", "contacts"])
        self.assertEqual(saved["slides"][0]["content"]["heading"], "Fresh House")
        self.assertEqual(saved["slides"][1]["content"]["contacts"][0]["name"], "Fresh Agent")
        self.assertEqual(saved["slides"][1]["layout_id"], "stacked")
        self.assertEqual(saved["clone_verification"]["status"], "passed")
        self.assertGreaterEqual(saved["clone_verification"]["confidence"], 95)
        self.assertTrue(verification_saved)
        self.assertEqual(captured["analysis"], saved)

    async def test_render_slide_route_accepts_json_payload(self):
        request = _json_request({
            "slide_type": "cover",
            "layout_id": "future-cover-layout",
            "slide_num": 7,
            "content": {"heading": "Route Render"},
        })

        response = await app_module.render_slide_route(request)
        data = json.loads(response.body.decode("utf-8"))

        self.assertIn("Route Render", data["html"])
        self.assertEqual(data["layout_id"], "default")
        self.assertEqual(data["requested_layout_id"], "future-cover-layout")
        self.assertTrue(data["layout_fallback"])


def _json_request(payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/slides/render",
        "headers": [(b"content-type", b"application/json")],
    }, receive)


if __name__ == "__main__":
    unittest.main()
