import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brochure_maker.template_manager import (
    merge_editor_layout_state_into_analysis,
    save_template,
)


class TestTemplateEditorStateMerge(unittest.TestCase):
    def test_sparse_keyed_editor_state_does_not_fall_back_by_order(self):
        analysis = {
            "slides": [
                {
                    "type": "cover",
                    "layout_id": "default",
                    "content": {"heading": "Cover"},
                },
                {
                    "type": "text_and_photos",
                    "layout_id": "default",
                    "content": {"body": "Body"},
                },
                {
                    "type": "photo_gallery",
                    "layout_id": "default",
                    "content": {"heading": "Gallery"},
                },
            ],
        }
        editor_state = {
            "layoutVariants": {
                "slides": {
                    "slide3": {
                        "slideId": "slide3",
                        "slideType": "photo_gallery",
                        "currentLayout": "caption-card",
                    }
                }
            }
        }

        merged = merge_editor_layout_state_into_analysis(analysis, editor_state)

        self.assertEqual(
            [(slide["type"], slide["layout_id"]) for slide in merged["slides"]],
            [
                ("cover", "default"),
                ("text_and_photos", "default"),
                ("photo_gallery", "caption-card"),
            ],
        )
        self.assertEqual(analysis["slides"][2]["layout_id"], "default")

        with tempfile.TemporaryDirectory() as tmp:
            with patch("brochure_maker.template_manager.TEMPLATES_STORE", Path(tmp)):
                template = save_template("Sparse Editor Layout", merged)

        self.assertEqual(
            [(slide["slide_type"], slide["layout_id"]) for slide in template["slides"]],
            [
                ("cover", "default"),
                ("text_and_photos", "default"),
                ("photo_gallery", "caption-card"),
            ],
        )
        self.assertEqual(template["slide_sequence"][0]["slide_type"], "cover")
        self.assertEqual(template["slide_sequence"][0]["layout_id"], "default")

    def test_save_template_uses_current_editor_layout_without_copying_content(self):
        analysis = {
            "slides": [
                {
                    "type": "cover",
                    "layout_id": "default",
                    "content": {"heading": "Do Not Clone Cover"},
                },
                {
                    "type": "text_and_photos",
                    "layout_id": "default",
                    "content": {"body": "Do Not Clone Body"},
                },
                {
                    "type": "photo_gallery",
                    "layout_id": "image-4",
                    "content": {
                        "heading": "Do Not Clone Gallery Heading",
                        "images": [
                            {
                                "url": "/private/source-gallery.jpg",
                                "caption": "Do Not Clone Gallery Caption",
                            }
                        ],
                    },
                },
            ],
        }
        editor_state = {
            "layoutVariants": {
                "slides": {
                    "slide3": {
                        "slideId": "slide3",
                        "slideType": "photo_gallery",
                        "currentLayout": "image-1",
                        "layouts": {
                            "image-1": {
                                "slideHtml": "<div>Do Not Clone Editor Snapshot</div>",
                                "capture": {"images": ["/private/editor-snapshot.jpg"]},
                            }
                        },
                    }
                }
            }
        }

        merged = merge_editor_layout_state_into_analysis(analysis, editor_state)
        with tempfile.TemporaryDirectory() as tmp:
            with patch("brochure_maker.template_manager.TEMPLATES_STORE", Path(tmp)):
                template = save_template("Editor Layout Preset", merged)

        self.assertEqual(analysis["slides"][2]["layout_id"], "image-4")
        self.assertEqual(template["slide_sequence"][2]["layout_id"], "image-1")
        self.assertEqual(template["slides"][2]["layout_id"], "image-1")
        self.assertEqual(template["slides"][2]["slide_type"], "photo_gallery")

        template_json = json.dumps(template)
        self.assertNotIn("Do Not Clone Gallery Heading", template_json)
        self.assertNotIn("Do Not Clone Gallery Caption", template_json)
        self.assertNotIn("/private/source-gallery.jpg", template_json)
        self.assertNotIn("Do Not Clone Editor Snapshot", template_json)
        self.assertNotIn("/private/editor-snapshot.jpg", template_json)

    def test_save_template_uses_current_editor_primary_colour(self):
        analysis = {
            "colour_scheme": {"primary": "#B8714E"},
            "slides": [
                {
                    "type": "cover",
                    "layout_id": "default",
                    "content": {"heading": "Do Not Clone Cover"},
                }
            ],
        }
        editor_state = {"primaryColour": "#2C3E50"}

        merged = merge_editor_layout_state_into_analysis(analysis, editor_state)
        with tempfile.TemporaryDirectory() as tmp:
            with patch("brochure_maker.template_manager.TEMPLATES_STORE", Path(tmp)):
                template = save_template("Colour Preset", merged)
                saved_template = json.loads((Path(tmp) / "colour_preset" / "template.json").read_text(encoding="utf-8"))

        self.assertEqual(analysis["colour_scheme"]["primary"], "#B8714E")
        self.assertEqual(template["colour_scheme"]["primary"], "#2C3E50")
        self.assertEqual(saved_template["colour_scheme"]["primary"], "#2C3E50")

    def test_save_template_uses_current_editor_logo_state(self):
        analysis = {
            "slides": [
                {
                    "type": "cover",
                    "layout_id": "default",
                    "content": {"heading": "Do Not Clone Cover"},
                }
            ],
        }
        editor_state = {
            "logo": {
                "type": "svg:diamond",
                "uploadedLogoDataUrl": None,
                "coverSize": 55,
                "coverPosition": "top-right",
            }
        }

        merged = merge_editor_layout_state_into_analysis(analysis, editor_state)
        with tempfile.TemporaryDirectory() as tmp:
            with patch("brochure_maker.template_manager.TEMPLATES_STORE", Path(tmp)):
                template = save_template("Logo Preset", merged)
                saved_template = json.loads((Path(tmp) / "logo_preset" / "template.json").read_text(encoding="utf-8"))

        self.assertEqual(template["logo"]["type"], "svg:diamond")
        self.assertEqual(template["logo"]["coverPosition"], "top-right")
        self.assertEqual(saved_template["logo"]["type"], "svg:diamond")


if __name__ == "__main__":
    unittest.main()
