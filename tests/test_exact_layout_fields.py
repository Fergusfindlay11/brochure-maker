from __future__ import annotations

import subprocess
import sys
import time
import re
import tempfile
import unittest
import html
import io
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

import brochure_maker.exact_pdf_layout as exact_pdf_layout
from brochure_maker.exact_pdf_layout import (
    _build_structured_field_config,
    _colour_role,
    _merge_model_image_regions,
    _refresh_source_preserved_edit_after_region_merges,
    _render_exact_html,
    _theme_colours_from_inventory,
)


class HtmlNode:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]], parent: "HtmlNode | None" = None):
        self.tag = tag
        self.attrs = {key: value or "" for key, value in attrs}
        self.parent = parent
        self.children: list[HtmlNode] = []
        self.text_parts: list[str] = []

    @property
    def text(self) -> str:
        own_text = "".join(self.text_parts)
        child_text = "".join(child.text for child in self.children)
        return own_text + child_text

    @property
    def searchable(self) -> str:
        pieces = [self.tag, self.text]
        for key, value in self.attrs.items():
            pieces.extend((key, value))
        return " ".join(pieces).lower()


class HtmlTreeParser(HTMLParser):
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document", [])
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(tag, attrs, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in self.VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(tag, attrs, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        while len(self._stack) > 1:
            node = self._stack.pop()
            if node.tag == tag:
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].text_parts.append(data)


class TestExactLayoutStructuredFields(unittest.TestCase):
    def setUp(self):
        self.html = _render_fixture_html()
        self.semantic_html = _render_semantic_fixture_html()
        parser = HtmlTreeParser()
        parser.feed(self.html)
        self.root = parser.root
        semantic_parser = HtmlTreeParser()
        semantic_parser.feed(self.semantic_html)
        self.semantic_root = semantic_parser.root

    def test_pdf_tool_timeout_kills_stuck_process_group(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            exact_pdf_layout._run_pdf_tool(
                [
                    sys.executable,
                    "-c",
                    "import signal,time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(10)",
                ],
                timeout=0.1,
            )

        self.assertLess(time.monotonic() - started, 3)

    def test_full_page_background_falls_back_when_pdftoppm_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets_dir = Path(tmp)
            doc = exact_pdf_layout.fitz.open()
            page = doc.new_page(width=120, height=80)
            try:
                with mock.patch.object(exact_pdf_layout.shutil, "which", return_value="/fake/pdftoppm"):
                    with mock.patch.object(
                        exact_pdf_layout,
                        "_run_pdf_tool",
                        side_effect=subprocess.TimeoutExpired(["pdftoppm"], 0.1),
                    ):
                        rendered = exact_pdf_layout._render_full_page_background(
                            Path("broken.pdf"),
                            page,
                            assets_dir,
                            1,
                            120,
                            80,
                        )

                self.assertEqual(rendered, "page001-full.png")
                self.assertTrue((assets_dir / rendered).exists())
            finally:
                doc.close()

    def test_full_page_background_prefers_model_background_before_pixmap_fallback(self):
        class NoPixmapPage:
            def get_pixmap(self, **_kwargs):
                raise AssertionError("PyMuPDF pixmap fallback should not run when model background exists")

        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            assets_dir = project_dir / "exact_assets"
            model_backgrounds = project_dir / "exact_layout_model" / "backgrounds"
            assets_dir.mkdir()
            model_backgrounds.mkdir(parents=True)
            Image.new("RGB", (240, 160), "#ddeeff").save(model_backgrounds / "page-032.png")

            with mock.patch.object(exact_pdf_layout.shutil, "which", return_value="/fake/pdftoppm"):
                with mock.patch.object(
                    exact_pdf_layout,
                    "_run_pdf_tool",
                    side_effect=subprocess.TimeoutExpired(["pdftoppm"], 0.1),
                ):
                    rendered = exact_pdf_layout._render_full_page_background(
                        Path("broken.pdf"),
                        NoPixmapPage(),
                        assets_dir,
                        32,
                        120,
                        80,
                    )

            self.assertEqual(rendered, "page032-full.png")
            with Image.open(assets_dir / rendered) as image:
                self.assertEqual(image.size, (120, 80))

    def test_prepare_exact_pdf_source_uses_qpdf_repaired_copy_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            source = project_dir / "source.pdf"
            source.write_bytes(b"%PDF-1.7\n% source\n")

            def fake_run(cmd, *, timeout):
                repaired_path = Path(cmd[-1])
                repaired_path.write_bytes(b"%PDF-1.7\n% repaired\n")
                return subprocess.CompletedProcess(cmd, 0, "Pages tree repaired", "")

            with mock.patch.object(exact_pdf_layout.shutil, "which", return_value="/fake/qpdf"):
                with mock.patch.object(exact_pdf_layout, "_run_pdf_tool", side_effect=fake_run):
                    processed, metadata = exact_pdf_layout.prepare_exact_pdf_source(source, project_dir)

            self.assertEqual(processed, project_dir.resolve() / "source.qpdf.pdf")
            self.assertTrue(metadata["repaired"])
            self.assertEqual(metadata["repair_tool"], "qpdf")
            self.assertIn("Pages tree repaired", metadata["stdout"])

    def test_source_preserve_pdf_page_ops_detects_plan_inventory_evidence(self):
        self.assertTrue(
            exact_pdf_layout._should_source_preserve_pdf_page_ops(
                {"page_number": 31, "image_regions": []},
                {
                    "page_number": 31,
                    "page_purpose": "location introduction",
                    "detected_features": ["location_copy", "map_context"],
                    "space_plan_regions": [{"bbox": {"x": 10, "y": 20, "width": 300, "height": 200}}],
                },
            )
        )

    def test_source_preserve_pdf_page_ops_keeps_ordinary_photo_page_editable(self):
        self.assertFalse(
            exact_pdf_layout._should_source_preserve_pdf_page_ops(
                {"page_number": 4, "image_regions": [{"role": "photo-region"}]},
                {
                    "page_number": 4,
                    "page_purpose": "editorial",
                    "detected_features": ["photo_regions"],
                },
            )
        )

    def test_model_image_slots_for_page_crops_photo_region_from_rendered_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "projectx"
            assets_dir = project_dir / "exact_assets"
            assets_dir.mkdir(parents=True)
            background_path = assets_dir / "page001-full.png"
            image = Image.new("RGB", (200, 120), "#ffffff")
            draw = ImageDraw.Draw(image)
            draw.rectangle((50, 20, 150, 80), fill="#224466")
            image.save(background_path)

            slots = exact_pdf_layout._model_image_slots_for_page(
                {
                    "page_number": 1,
                    "size": {"width": 100, "height": 60},
                    "image_regions": [
                        {
                            "id": "hero",
                            "role": "hero-photo",
                            "bbox": {"x": 25, "y": 10, "width": 50, "height": 30},
                            "confidence": 0.91,
                        }
                    ],
                },
                {"page_num": 1, "width": 200, "height": 120},
                background_path,
            )

            self.assertEqual(len(slots), 1)
            self.assertEqual(slots[0]["left"], 50)
            self.assertEqual(slots[0]["top"], 20)
            self.assertEqual(slots[0]["width"], 100)
            self.assertEqual(slots[0]["height"], 60)
            self.assertEqual(slots[0]["image_role"], "hero-photo")
            self.assertTrue((assets_dir / "images" / "page001-model-image-01.png").exists())

    def test_source_preserved_logo_detection_skips_broad_light_scans_without_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            background_path = Path(tmp) / "page001-full.png"
            Image.new("RGB", (160, 120), "#ffffff").save(background_path)
            calls: list[str] = []

            def fake_detect(_path, predicate):
                calls.append(predicate.__name__)
                return []

            with mock.patch.object(exact_pdf_layout, "_detect_accent_components", side_effect=fake_detect):
                exact_pdf_layout._build_source_logo_defs(
                    [
                        {
                            "page_num": 1,
                            "width": 160,
                            "height": 120,
                            "background_path": str(background_path),
                            "source_preserved_edit": True,
                            "source_image_marks": [],
                            "image_slots": [],
                            "text_entries": [],
                        }
                    ]
                )

            self.assertEqual(calls, ["_is_yellow_accent_pixel", "_is_coloured_source_logo_mark_pixel"])

    def test_source_preserved_logo_detection_skips_non_cover_pages_without_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            background_path = Path(tmp) / "page002-full.png"
            Image.new("RGB", (160, 120), "#ffffff").save(background_path)

            with mock.patch.object(exact_pdf_layout, "_detect_accent_components") as detect:
                slots = exact_pdf_layout._build_source_logo_defs(
                    [
                        {
                            "page_num": 2,
                            "width": 160,
                            "height": 120,
                            "background_path": str(background_path),
                            "source_preserved_edit": True,
                            "source_image_marks": [],
                            "image_slots": [],
                            "text_entries": [],
                        }
                    ]
                )

            self.assertEqual(slots, [])
            detect.assert_not_called()

    def test_exact_editor_has_a_dedicated_fields_drawer_toggle(self):
        drawer = _find_fields_drawer(self.root)
        toggle = _find_fields_toggle(self.root)

        self.assertIsNotNone(
            toggle,
            "Exact layout editor should expose a Fields toggle/button, not rely only on canvas overlays.",
        )
        self.assertIsNotNone(
            drawer,
            "Exact layout editor should render a dedicated Fields drawer/panel for structured edits.",
        )
        self.assertIn('data-global-control-panel="true"', self.html)
        self.assertIn("Global controls", self.html)
        self.assertIn('<body class="fields-open">', self.html)

        if drawer and toggle:
            controls = toggle.attrs.get("aria-controls") or toggle.attrs.get("data-target") or toggle.attrs.get("data-drawer")
            drawer_id = drawer.attrs.get("id")
            self.assertTrue(
                not controls or controls == drawer_id or "field" in controls.lower(),
                "Fields toggle should be wired to the structured fields drawer with a stable id/data target.",
            )

    def test_exact_editor_opens_with_clickable_edit_layer(self):
        self.assertIn('data-picture-layout="editable"', self.html)
        self.assertIn("page.dataset.imageLayout = kind;", self.html)

    def test_cover_title_structured_edits_fit_pdf_text_boxes(self):
        self.assertIn("function fitCoverTitleTextBox", self.html)
        self.assertIn("fitCoverTitleTextBox(target);", self.html)
        self.assertIn("['font-size', 'fontSize']", self.html)
        self.assertIn("['line-height', 'lineHeight']", self.html)

    def test_extracted_palette_from_inventory_drives_global_colour_controls(self):
        theme = _theme_colours_from_inventory(
            {
                "global_systems": {
                    "palette": {
                        "accent_colour": "#008EC2",
                        "dark_background_colour": "#f0f0f0",
                        "light_text_colour": "#ffffff",
                    }
                }
            }
        )

        self.assertEqual(theme, {"accent": "#008ec2", "dark": "#f0f0f0", "light": "#ffffff"})

    def test_renderer_uses_extracted_palette_for_toolbar_and_icon_defaults(self):
        pages = [
            {
                "page_num": 1,
                "width": 640,
                "height": 480,
                "body": "",
                "text_count": 1,
                "image_slots": [],
                "text_entries": [
                    {"page_num": 1, "save_id": "p1-title", "plain": "255 Hammersmith Road", "left": 42, "top": 42}
                ],
            }
        ]
        config = _build_structured_field_config(pages)
        config["theme_colours"] = {"accent": "#008ec2", "dark": "#f0f0f0", "light": "#ffffff"}

        rendered = _render_exact_html(
            project_id="palette-regression",
            page_width=640,
            page_height=480,
            pages=pages,
            poppler_css="",
            font_css="",
            field_config=config,
        )

        self.assertIn("--exact-accent: #008ec2;", rendered)
        self.assertIn("--exact-dark: #f0f0f0;", rendered)
        self.assertIn("--exact-ui-surface: #f0f0f0;", rendered)
        self.assertIn("--exact-accent-contrast: #ffffff;", rendered)
        self.assertIn('<option value="extracted" selected>Extracted PDF</option>', rendered)
        self.assertIn('<input id="accentColour" type="color" value="#008ec2">', rendered)
        self.assertIn('<input id="darkColour" type="color" value="#f0f0f0">', rendered)
        self.assertIn('<input id="fieldGlobalIconColour" type="color" value="#008ec2"', rendered)
        self.assertIn('window.__EXACT_THEME__ = {"accent": "#008ec2", "dark": "#f0f0f0", "light": "#ffffff"};', rendered)
        self.assertIn("var DEFAULT_ACCENT = String(EXACT_THEME.accent || '#ffea00').toLowerCase();", rendered)

    def test_renderer_merges_model_detected_space_plan_regions(self):
        pages = [
            {
                "page_num": 5,
                "width": 2000,
                "height": 1000,
                "image_regions": [],
            }
        ]
        exact_model = {
            "pages": [
                {
                    "page_number": 5,
                    "size": {"width": 1000, "height": 500},
                    "image_regions": [
                        {
                            "role": "space-plan",
                            "type": "floorplan",
                            "bbox": {"left": 120, "top": 90, "width": 360, "height": 240},
                            "editable": True,
                            "replaceable": True,
                        }
                    ],
                }
            ]
        }

        _merge_model_image_regions(pages, exact_model)

        self.assertEqual(len(pages[0]["image_regions"]), 1)
        self.assertEqual(pages[0]["image_regions"][0]["role"], "space-plan")
        self.assertEqual(pages[0]["image_regions"][0]["bbox"]["left"], 240)
        self.assertEqual(pages[0]["image_regions"][0]["bbox"]["width"], 720)
        self.assertIn("applyLayoutMode('editable');", self.html)
        self.assertIn(".exact-ocr-text:not([data-active-edit=\"true\"]):not([data-edited=\"true\"])", self.html)
        self.assertIn("el.dataset.activeEdit = 'true';", self.html)
        self.assertIn('.pdf-text[data-active-edit="true"]:not([data-edited="true"])', self.html)
        self.assertIn("page.dataset.sourcePreservedEdit === 'true'", self.html)

    def test_field_config_image_regions_only_advertise_rendered_upload_slots(self):
        pages = [
            {
                "page_num": 2,
                "image_slots": [
                    {
                        "id": "rendered-photo",
                        "image_role": "photo-region",
                        "left": 640,
                        "top": 80,
                        "width": 360,
                        "height": 420,
                        "fit": "cover",
                    }
                ],
                "image_regions": [
                    {
                        "id": "raw-model-photo-candidate",
                        "type": "image",
                        "role": "hero-photo",
                        "bbox": {"left": 478, "top": -66, "width": 606, "height": 908},
                        "editable": True,
                        "replaceable": True,
                    }
                ],
            }
        ]

        regions = exact_pdf_layout._field_config_image_regions_from_pages(pages)

        self.assertEqual(
            [region["id"] for region in regions],
            ["rendered-photo"],
            "Field config should describe real editable upload controls, not raw detector evidence.",
        )

    def test_complex_vector_pages_preserve_source_background_in_edit_mode(self):
        body = """
        <img class="pdf-bg" src="/api/projects/demo/exact_assets/page001-full.png" alt="background image"/>
        <svg class="pdf-vector-layer"><path class="pdf-vector-shape" d="M0 0H10V10Z"/></svg>
        <svg class="pdf-vector-overlay-layer"><path class="pdf-vector-shape" d="M0 0H10V10Z"/></svg>
        <p class="pdf-text" contenteditable="true" data-save-id="exact-page1-text1"
           data-typography-role="body" style="position:absolute;left:20px;top:20px">CONNECTIVITY</p>
        """
        html = _render_exact_html(
            project_id="source-preserved-test",
            page_width=640,
            page_height=480,
            pages=[
                {
                    "page_num": 1,
                    "width": 640,
                    "height": 480,
                    "body": body,
                    "text_count": 1,
                    "image_slots": [],
                    "text_entries": [
                        {"page_num": 1, "save_id": "exact-page1-text1", "plain": "CONNECTIVITY", "left": 20, "top": 20}
                    ],
                    "source_preserved_edit": True,
                    "vector_path_count": 2200,
                }
            ],
            poppler_css="",
            font_css="",
        )

        self.assertIn('data-source-preserved-edit="true"', html)
        self.assertIn('data-vector-path-count="2200"', html)
        self.assertIn('[data-source-preserved-edit="true"][data-picture-layout="editable"] .pdf-bg', html)
        self.assertIn('[data-source-preserved-edit="true"][data-picture-layout="editable"] .pdf-vector-layer', html)
        self.assertIn('[data-source-preserved-edit="true"][data-picture-layout="editable"] .pdf-text:not([data-active-edit="true"])', html)
        self.assertIn("page.dataset.sourcePreservedEdit === 'true'", html)
        self.assertIn("img.setAttribute('src', img.dataset.originalSrc);", html)
        self.assertIn("mapState.source === 'generated-v1'", html)

    def test_source_preserved_pages_suppress_extreme_transform_text_hotspots(self):
        body = """
        <img class="pdf-bg" src="/api/projects/demo/exact_assets/page001-full.png" alt="background image"/>
        <p class="ft900 pdf-text" contenteditable="true" data-save-id="map-street-fragment"
           data-pdf-transform-scale="81.357" data-typography-role="body"
           style="position:absolute;top:802px;left:992px;white-space:nowrap">TO</p>
        """
        html = _render_exact_html(
            project_id="source-preserved-hotspot-test",
            page_width=1280,
            page_height=900,
            pages=[
                {
                    "page_num": 1,
                    "width": 1280,
                    "height": 900,
                    "body": body,
                    "text_count": 1,
                    "image_slots": [],
                    "text_entries": [
                        {
                            "page_num": 1,
                            "save_id": "map-street-fragment",
                            "plain": "TO",
                            "left": 992,
                            "top": 802,
                        }
                    ],
                    "source_preserved_edit": True,
                    "vector_path_count": 2200,
                }
            ],
            poppler_css=".ft900{font-size:12px;transform:matrix(81,-80,80,81,0,0)}",
            font_css="",
        )

        self.assertIn('data-save-id="map-street-fragment"', html)
        self.assertIn('contenteditable="false"', html)
        self.assertIn('class="ft900 pdf-text exact-inert-pdf-text"', html)
        self.assertIn('data-interaction-suppressed="true"', html)
        self.assertIn('data-suppression-reason="extreme-transform-hotspot"', html)
        self.assertIn(".exact-inert-pdf-text", html)

    def test_inventory_map_region_refreshes_source_preserved_mode_after_merge(self):
        dense_paths = "".join('<path d="M0 0H10V10Z"/>' for _ in range(130))
        page = {
            "page_num": 13,
            "width": 1280,
            "height": 880,
            "body": (
                '<img class="pdf-bg" src="/api/projects/demo/exact_assets/page013-full.png" alt="background image"/>'
                f'<svg class="pdf-vector-overlay-layer">{dense_paths}</svg>'
                '<p class="pdf-text" contenteditable="true" data-save-id="map-hotspot" '
                'data-pdf-transform-scale="72.000" data-typography-role="body" '
                'style="position:absolute;top:20px;left:20px;width:400px;height:300px">KING ST</p>'
            ),
            "text_entries": [{"plain": "KING ST", "left": 20, "top": 20, "save_id": "map-hotspot"}],
            "image_regions": [
                {
                    "role": "map",
                    "type": "map",
                    "bbox": {"left": 485, "top": 0, "width": 785, "height": 874},
                    "source_evidence": {"source": "extraction-inventory map_regions"},
                }
            ],
            "source_preserved_edit": False,
        }

        _refresh_source_preserved_edit_after_region_merges([page])

        self.assertTrue(page["source_preserved_edit"])
        self.assertIn('contenteditable="false"', page["body"])
        self.assertIn('data-interaction-suppressed="true"', page["body"])

    def test_large_cover_vector_artwork_bbox_is_detected_for_image_slot(self):
        paths = [
            {
                "id": "background",
                "bbox": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "fill": "#e3e2d6",
            },
            {
                "id": "artwork",
                "bbox": {"x": 30, "y": 305, "width": 1860, "height": 745},
                "fill": "#ff685f",
            },
        ]

        bbox = exact_pdf_layout._decorative_vector_artwork_bbox_from_paths(
            paths,
            page_width=1920,
            page_height=1080,
            css_width=1920,
            css_height=1080,
            existing_slots=[],
        )

        self.assertIsNotNone(bbox)
        assert bbox is not None
        self.assertEqual(bbox["left"], 30)
        self.assertEqual(bbox["top"], 305)
        self.assertEqual(bbox["width"], 1860)
        self.assertGreater(bbox["confidence"], 0.8)

    def test_fields_drawer_contains_structured_logo_contact_and_amenity_inputs(self):
        drawer = _find_fields_drawer(self.root)
        self.assertIsNotNone(drawer, "Structured field controls must live inside the Fields drawer.")

        control_keys = _control_keys(drawer) if drawer else []
        self.assertTrue(
            _has_field_control(control_keys, "logo", "text", "upload", "clear", "reset", "source", "variant"),
            "Fields drawer should include structured logo controls.",
        )

        for field_name in ("name", "phone", "email"):
            self.assertTrue(
                _has_contact_field(control_keys, field_name),
                f"Fields drawer should expose structured agent/contact {field_name} inputs.",
            )

        self.assertTrue(
            _has_field_control(control_keys, "amenit", "title", "heading"),
            "Fields drawer should include a structured amenities title input.",
        )
        self.assertTrue(
            _has_amenity_list_control(control_keys),
            "Fields drawer should include structured amenities list inputs.",
        )
        self.assertIn(
            "data-global-logo-field",
            self.html,
            "Exact fields drawer should expose normal-style recurring brochure logo controls.",
        )
        self.assertIn(
            "data-amenity-icon-field",
            self.html,
            "Exact fields drawer should expose amenity icon-bank controls.",
        )
        self.assertIn(
            "data-map-field",
            self.html,
            "Exact fields drawer should expose a semantic map control surface.",
        )

    def test_serialized_state_bumps_version_and_includes_structured_fields(self):
        version_match = re.search(r"editableLayerVersion\s*:\s*(\d+)", self.html)
        self.assertIsNotNone(version_match, "Exact editor state should declare an editable layer version.")
        self.assertGreaterEqual(
            int(version_match.group(1)),
            4,
            "Structured field state should bump the editable layer version beyond the current overlay-only schema.",
        )

        self.assertIsNotNone(
            re.search(r"\bstructuredFields\b", self.html),
            "Serialized state should include a structuredFields object alongside text/image overlay state.",
        )
        self.assertIsNotNone(
            re.search(r"state\.structuredFields|structuredFields\s*:", self.html),
            "serialize() should write structured fields into saved project state.",
        )
        self.assertIsNotNone(
            re.search(r"applyStructuredFields\s*\([^)]*state\.structuredFields|state\.structuredFields\s*&&", self.html),
            "applyState() should restore structured field values from saved project state.",
        )
        self.assertIsNotNone(re.search(r"agencyLogos|logo", self.html), "Structured state should carry logo values.")
        self.assertIsNotNone(re.search(r"agents|contacts|contact:", self.html), "Structured state should carry agent/contact values.")
        self.assertIn("amenit", self.html.lower(), "Structured state should carry amenities values.")
        self.assertIn("colourPreset", self.html, "Global colour preset state should be saved and restored.")
        self.assertIn("COLOUR_PRESETS", self.html, "Exact editor should expose testable global colour presets.")
        self.assertIn("globalLogo", self.html, "Exact editor should persist the normal brochure logo state shape.")
        self.assertIn("amenityIconFields", self.html, "Exact editor should persist amenity icon-bank choices.")
        self.assertIn("exactMap", self.html, "Exact editor should persist the semantic map state.")

    def test_exact_semantic_overlays_include_icon_bank_and_map_contracts(self):
        self.assertIn("/static/icons/svg-library.js", self.html)
        self.assertIn("/static/js/icon-picker.js", self.html)
        self.assertIn("iconPickerOverlay", self.html)
        self.assertIn("class=\"highlight-icon exact-amenity-icon-slot\"", self.html)
        self.assertIn("data-icon-slot=\"amenity", self.html)
        self.assertIn("data-amenity-icon-field=\"amenity", self.html)
        self.assertIn("class=\"map-area exact-map-area", self.html)
        self.assertIn("data-exact-map-area=\"true\"", self.html)
        self.assertIn("data-exact-map-generate", self.html)

    def test_dark_panel_colour_is_recolourable(self):
        self.assertEqual(_colour_role("#282827"), "dark")
        self.assertEqual(_colour_role("#1d1d1b"), "dark")
        self.assertEqual(_colour_role("#454b43"), "dark")

    def test_theme_dark_colour_comes_from_weighted_pdf_fills(self):
        theme = exact_pdf_layout._theme_colours_from_pages(
            [
                {"dark_fill_stats": [{"colour": "#333132", "area": 100}]},
                {"dark_fill_stats": [{"colour": "#454b43", "area": 1000}]},
                {"dark_fill_stats": [{"colour": "#454b43", "area": 900}]},
            ]
        )

        self.assertEqual(theme["dark"], "#454b43")

    def test_embedded_pdf_font_without_unicode_cmap_is_not_browser_safe(self):
        class NoCmapFont:
            def get(self, key):
                return None if key == "cmap" else None

        with mock.patch("fontTools.ttLib.TTFont", return_value=NoCmapFont()):
            self.assertFalse(
                exact_pdf_layout._font_has_browser_usable_cmap(b"font-without-cmap", "ttf"),
                "PDF subset fonts without a usable cmap should not be registered as editable @font-face fonts.",
            )

    def test_agency_logo_slots_use_page_scoped_visual_source_regions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assets_dir = tmp_path / "project" / "exact_assets"
            assets_dir.mkdir(parents=True)
            background1 = assets_dir / "page1.png"
            background2 = assets_dir / "page2.png"
            for path in (background1, background2):
                Image.new("RGB", (900, 700), "#333132").save(path)
            image1 = Image.open(background1)
            draw1 = ImageDraw.Draw(image1)
            draw1.rectangle((410, 220, 690, 270), fill="#ffffff")
            image1.save(background1)
            image2 = Image.open(background2)
            draw2 = ImageDraw.Draw(image2)
            draw2.rectangle((40, 520, 195, 585), fill="#ffffff")
            image2.save(background2)

            entries = [
                {
                    "save_id": "page1-contact",
                    "page_num": 1,
                    "left": 420,
                    "top": 360,
                    "plain": "Tim Example 07700 000000 tim.example@agency.test",
                    "font_style": {"font_size": 16},
                },
                {
                    "save_id": "page2-contact",
                    "page_num": 2,
                    "left": 58,
                    "top": 360,
                    "plain": "Tina Example 07700 111111 tina.example@agency.test",
                    "font_style": {"font_size": 16},
                },
            ]
            contacts = [
                {
                    "key": "tim",
                    "email": "tim.example@agency.test",
                    "targets": ["page1-contact"],
                    "hide_targets": [],
                    "anchor": {"page_num": 1, "left": 420, "top": 360},
                },
                {
                    "key": "tina",
                    "email": "tina.example@agency.test",
                    "targets": ["page2-contact"],
                    "hide_targets": [],
                    "anchor": {"page_num": 2, "left": 58, "top": 360},
                },
            ]
            pages = [
                {"page_num": 1, "background_path": str(background1)},
                {"page_num": 2, "background_path": str(background2)},
            ]

            logos = exact_pdf_layout._build_agency_logo_defs(contacts, entries, pages)

        self.assertEqual(sorted(logos), ["agency1", "agency2"])
        self.assertEqual(logos["agency1"]["page"], "1")
        self.assertLess(logos["agency1"]["top"], 260)
        self.assertEqual(logos["agency1"]["sourceDetection"], "raster logo region above contact group")
        self.assertTrue(logos["agency1"]["defaultAssetUrl"].endswith(".png"))
        self.assertEqual(logos["agency2"]["page"], "2")
        self.assertGreater(logos["agency2"]["top"], 500)
        self.assertEqual(logos["agency2"]["sourceDetection"], "raster logo region below contact group")
        self.assertTrue(logos["agency2"]["defaultAssetUrl"].endswith(".png"))

    def test_agency_logo_crop_excludes_legal_footer_text_cluster(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assets_dir = tmp_path / "project" / "exact_assets"
            assets_dir.mkdir(parents=True)
            background = assets_dir / "page5.png"
            image = Image.new("RGB", (900, 760), "#454b43")
            draw = ImageDraw.Draw(image)
            draw.rectangle((48, 405, 205, 465), fill="#ffffff")
            for y in range(540, 588, 10):
                draw.rectangle((18, y, 360, y + 3), fill="#ffffff")
            image.save(background)

            entries = [
                {
                    "save_id": "contact",
                    "page_num": 5,
                    "left": 58,
                    "top": 330,
                    "plain": "Will Example 07700 000000 will.example@bbgreal.com",
                    "font_style": {"font_size": 16},
                }
            ]
            contacts = [
                {
                    "key": "will",
                    "email": "will.example@bbgreal.com",
                    "targets": ["contact"],
                    "hide_targets": [],
                    "anchor": {"page_num": 5, "left": 58, "top": 330},
                }
            ]
            pages = [{"page_num": 5, "background_path": str(background)}]

            logos = exact_pdf_layout._build_agency_logo_defs(contacts, entries, pages)

        self.assertIn("agency1", logos)
        self.assertGreater(logos["agency1"]["top"], 395)
        self.assertLess(logos["agency1"]["top"], 415)
        self.assertLess(
            logos["agency1"]["height"],
            90,
            "The source logo crop should not include separate legal/footer text below the mark.",
        )
        self.assertTrue(logos["agency1"]["defaultAssetUrl"].endswith(".png"))

    def test_agency_logo_detection_rejects_text_overlap_false_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assets_dir = tmp_path / "project" / "exact_assets"
            assets_dir.mkdir(parents=True)
            background = assets_dir / "page41.png"
            image = Image.new("RGB", (900, 760), "#ffffff")
            draw = ImageDraw.Draw(image)
            for y in (206, 224, 242):
                draw.rectangle((96, y, 390, y + 4), fill="#1a45ff")
            image.save(background)

            entries = [
                {
                    "save_id": "contact",
                    "page_num": 41,
                    "left": 104,
                    "top": 350,
                    "plain": "Environmental Sciences Team environmentalsciences2@westminster.gov.uk",
                    "font_style": {"font_size": 15},
                },
                {
                    "save_id": "link-text",
                    "page_num": 41,
                    "left": 96,
                    "top": 206,
                    "plain": "www.westminster.gov.uk/guide-temporary-structures",
                    "font_style": {"font_size": 15},
                },
            ]
            contacts = [
                {
                    "key": "environmental",
                    "email": "environmentalsciences2@westminster.gov.uk",
                    "targets": ["contact"],
                    "hide_targets": [],
                    "anchor": {"page_num": 41, "left": 104, "top": 350},
                }
            ]
            pages = [{"page_num": 41, "background_path": str(background)}]

            logos = exact_pdf_layout._build_agency_logo_defs(contacts, entries, pages)

        self.assertIn("agency1", logos)
        self.assertEqual(logos["agency1"]["defaultAssetUrl"], "")
        self.assertEqual(logos["agency1"]["sourceDetection"], "contact-anchor-fallback")

    def test_prepare_page_inner_suppresses_occluded_pdf_text_spans(self):
        with tempfile.TemporaryDirectory() as tmp:
            background = Path(tmp) / "page.png"
            image = Image.new("RGB", (500, 260), "#333132")
            draw = ImageDraw.Draw(image)
            draw.rectangle((42, 124, 210, 137), fill="#ffffff")
            image.save(background)
            inner = """
            <img width="500" height="260" src="page.png"/>
            <p style="position:absolute;top:40px;left:40px;white-space:nowrap" class="ftAccent">13 AUSTIN FRIARS EC2</p>
            <p style="position:absolute;top:120px;left:40px;white-space:nowrap" class="ftLight">4th floor meeting room space</p>
            """

            html_value, text_count, entries = exact_pdf_layout._prepare_page_inner(
                inner,
                3,
                "project1",
                {"ftAccent": "accent", "ftLight": "light"},
                "page.png",
                {
                    "ftAccent": {"font_size": "19px"},
                    "ftLight": {"font_size": "12px"},
                },
                background_path=background,
            )

        self.assertEqual(text_count, 1)
        self.assertEqual(len(entries), 1)
        self.assertNotIn("13 AUSTIN FRIARS EC2", html_value)
        self.assertIn("4th floor meeting room space", html_value)
        self.assertEqual(entries[0]["plain"], "4th floor meeting room space")

    def test_image_slots_refine_to_visible_raster_photo_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "page.png"
            image = Image.new("RGB", (1120, 792), "#454b43")
            draw = ImageDraw.Draw(image)
            draw.rectangle((772, 80, 1075, 363), fill="#d9ded9")
            draw.rectangle((438, 104, 757, 315), fill="#d8d3c4")
            image.save(image_path)

            slots = [
                {"left": 773.67, "top": -4.79, "width": 302.53, "height": 452.67, "mask_colour": "#454b43"},
                {"left": 438.5, "top": 104.09, "width": 319.02, "height": 211.87, "mask_colour": "#454b43"},
            ]
            refined = exact_pdf_layout._refine_image_slots_with_raster_components(image_path, slots)

        self.assertEqual(refined[0]["bbox_source"], "raster visible photo component")
        self.assertGreaterEqual(refined[0]["top"], 76)
        self.assertLessEqual(refined[0]["top"], 84)
        self.assertLess(refined[0]["height"], 310)
        self.assertAlmostEqual(refined[1]["left"], 438.5, delta=5)

    def test_image_slot_refinement_rejects_component_spanning_multiple_photo_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "page.png"
            Image.new("RGB", (900, 700), "#202020").save(image_path)
            slots = [
                {"left": 10.0, "top": 100.0, "width": 100.0, "height": 150.0, "mask_colour": "#202020"},
                {"left": 130.0, "top": 100.0, "width": 160.0, "height": 200.0, "mask_colour": "#202020"},
            ]
            components = [
                {"left": 10.0, "top": 100.0, "width": 280.0, "height": 200.0, "area": 47000.0, "density": 0.45},
                {"left": 10.0, "top": 100.0, "width": 100.0, "height": 150.0, "area": 14500.0, "density": 0.89},
            ]
            with mock.patch.object(exact_pdf_layout, "_detect_photo_components", return_value=components):
                refined = exact_pdf_layout._refine_image_slots_with_raster_components(image_path, slots)

        self.assertAlmostEqual(refined[0]["left"], 10.0, delta=0.01)
        self.assertAlmostEqual(refined[1]["left"], 130.0, delta=0.01)
        self.assertAlmostEqual(refined[1]["width"], 160.0, delta=0.01)
        self.assertNotEqual(refined[1].get("bbox_source"), "raster visible photo component")

    def test_photo_component_keeps_dark_adjacent_photo_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "page.png"
            image = Image.new("RGB", (1120, 792), "#454b43")
            draw = ImageDraw.Draw(image)
            draw.rectangle((712, 72, 1052, 368), fill="#d9ded9")
            draw.rectangle((712, 72, 780, 368), fill="#2f332f")
            image.save(image_path)

            components = exact_pdf_layout._detect_photo_components(image_path)

        self.assertTrue(components)
        component = components[0]
        self.assertLessEqual(component["left"], 716)
        self.assertGreaterEqual(component["width"], 330)

    def test_background_panel_sampling_recovers_light_half_behind_photo_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "spread.png"
            image = Image.new("RGB", (800, 500), "#282827")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 399, 499), fill="#ffffff")
            draw.rectangle((32, 32, 367, 467), fill="#b8c0c4")
            image.save(image_path)

            panels = exact_pdf_layout._background_panels_for_page(
                800,
                500,
                image_path,
                [{"left": 32, "top": 32, "width": 336, "height": 436}],
            )

        self.assertGreaterEqual(len(panels), 2)
        self.assertEqual(panels[0]["colour"], "#ffffff")
        self.assertEqual(panels[0]["role"], "light")
        self.assertEqual(panels[1]["role"], "dark")

    def test_background_panels_render_below_vectors_and_image_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "spread.png"
            Image.new("RGB", (400, 300), "#ffffff").save(image_path)

            rendered = exact_pdf_layout._render_background_panels(400, 300, image_path, [])

        self.assertIn('class="exact-background-panel"', rendered)
        self.assertIn('data-background-role="light"', rendered)
        self.assertIn("background:#ffffff", rendered)

    def test_vector_layer_infers_missing_translucent_photo_scrim_opacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assets_dir = tmp_path / "exact_assets"
            images_dir = assets_dir / "images"
            images_dir.mkdir(parents=True)
            source_photo = images_dir / "photo.jpg"
            full_render = assets_dir / "page001-full.png"
            Image.new("RGB", (100, 100), (120, 115, 99)).save(source_photo)
            Image.new("RGB", (100, 100), (100, 99, 86)).save(full_render)

            doc = exact_pdf_layout.fitz.open()
            page = doc.new_page(width=100, height=100)
            try:
                vector_path = {
                    "id": "p001-vector-0001",
                    "d": "M 20 0 L 40 0 L 40 100 L 20 100 Z",
                    "bbox": {"x": 20, "y": 0, "width": 20, "height": 100},
                    "fill": "#454b43",
                    "stroke": None,
                    "stroke_width": 0,
                    "fill_opacity": None,
                    "line_join": "miter",
                    "line_cap": "butt",
                    "fill_rule": "nonzero",
                }
                with mock.patch.object(exact_pdf_layout, "extract_page_svg_paths", return_value=[vector_path]):
                    svg = exact_pdf_layout._render_vector_layer(
                        page,
                        100,
                        100,
                        image_slots=[
                            {
                                "left": 0,
                                "top": 0,
                                "width": 100,
                                "height": 100,
                                "asset_url": "/api/projects/demo/exact_assets/images/photo.jpg",
                            }
                        ],
                        background_path=full_render,
                        assets_dir=assets_dir,
                    )
            finally:
                doc.close()

        self.assertIn('data-inferred-fill-opacity="0.', svg)
        self.assertIn("fill-opacity:0.", svg)
        self.assertNotIn("fill-opacity:1", svg)

    def test_agency_logo_uses_gap_between_contacts_and_legal_footer_when_available(self):
        entries = [
            {
                "save_id": "name",
                "page_num": 5,
                "plain": "Will Newton | Senior Surveyor",
                "top": 354,
                "left": 28,
                "font_style": {"font_size_px": 13},
            },
            {
                "save_id": "email",
                "page_num": 5,
                "plain": "will.newton@bbgreal.com",
                "top": 369,
                "left": 28,
                "font_style": {"font_size_px": 13},
            },
            {
                "save_id": "phone",
                "page_num": 5,
                "plain": "07880 242178",
                "top": 383,
                "left": 28,
                "font_style": {"font_size_px": 13},
            },
            {
                "save_id": "legal",
                "page_num": 5,
                "plain": "The above information contained within this brochure is subject to contract. These particulars are for general information only.",
                "top": 688,
                "left": 16,
                "font_style": {"font_size_px": 10},
            },
        ]
        contacts = [
            {
                "key": "willnewton",
                "email": "will.newton@bbgreal.com",
                "targets": ["name"],
                "hide_targets": ["email", "phone"],
                "anchor": entries[0],
            }
        ]

        logos = exact_pdf_layout._build_agency_logo_defs(contacts, entries)

        self.assertEqual(logos["agency1"]["page"], "5")
        self.assertGreaterEqual(logos["agency1"]["top"], 540)
        self.assertLessEqual(logos["agency1"]["top"], 570)

    def test_source_logo_detection_does_not_lose_yellow_facade_on_dark_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "page-full.png"
            image = Image.new("RGB", (420, 320), "#282827")
            draw = ImageDraw.Draw(image)
            for x in range(170, 251, 20):
                draw.line((x, 40, x, 285), fill="#ffea00", width=2)
            for y in range(60, 286, 28):
                draw.line((150, y, 272, y), fill="#ffea00", width=2)
            draw.arc((168, 18, 254, 100), 180, 360, fill="#ffea00", width=2)
            image.save(image_path)

            logos = exact_pdf_layout._build_source_logo_defs(
                [
                    {
                        "page_num": 1,
                        "width": 420,
                        "height": 320,
                        "background_path": str(image_path),
                        "text_entries": [],
                        "image_slots": [],
                    }
                ]
            )

        self.assertTrue(logos, "Yellow facade marks on dark backgrounds should become source-logo replacement slots.")
        self.assertEqual(logos[0]["page"], "1")

    def test_cover_brand_mark_fragments_merge_into_source_logo_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "cover-full.png"
            image = Image.new("RGB", (640, 480), "#101622")
            draw = ImageDraw.Draw(image)
            gold = "#d8a64a"
            for x in (272, 288, 304):
                draw.line((x, 150, x, 240), fill=gold, width=3)
            draw.arc((250, 124, 326, 202), 180, 360, fill=gold, width=3)
            for x in (344, 360, 376):
                draw.line((x, 142, x, 252), fill=gold, width=3)
            draw.arc((328, 132, 398, 220), 180, 360, fill=gold, width=3)
            image.save(image_path)

            logos = exact_pdf_layout._build_source_logo_defs(
                [
                    {
                        "page_num": 1,
                        "width": 640,
                        "height": 480,
                        "background_path": str(image_path),
                        "image_slots": [{"left": 248, "top": 120, "width": 160, "height": 140}],
                        "text_entries": [
                            {
                                "page_num": 1,
                                "plain": "1 - 3  R O Y A L  E X C H A N G E",
                                "left": 214,
                                "top": 300,
                                "font_style": {"font_size_px": 32},
                            }
                        ],
                    }
                ]
            )

        self.assertEqual(len(logos), 1)
        self.assertEqual(logos[0]["page"], "1")
        self.assertLess(logos[0]["top"], 180)
        self.assertGreater(logos[0]["width"], 120)
        self.assertLess(logos[0]["height"], 160)

    def test_amenity_icon_defs_use_real_icon_sections_and_skip_numbered_map_lists(self):
        entries = [
            {"page_num": 2, "save_id": "p2-heading", "plain": "KEY FEATURES", "top": 80, "left": 1000, "font_style": {"font_size_px": 30}},
            {"page_num": 2, "save_id": "p2-a1", "plain": "Premium", "top": 220, "left": 1050, "font_style": {"font_size_px": 18}},
            {"page_num": 2, "save_id": "p2-a2", "plain": "office space", "top": 244, "left": 1050, "font_style": {"font_size_px": 18}},
            {"page_num": 2, "save_id": "p2-b", "plain": "Exceptional design details", "top": 380, "left": 1050, "font_style": {"font_size_px": 18}},
            {"page_num": 3, "save_id": "p3-heading", "plain": "SUMMARY SPECIFICATION", "top": 90, "left": 1100, "font_style": {"font_size_px": 30}},
            {"page_num": 3, "save_id": "p3-a1", "plain": "BREEAM", "top": 250, "left": 1200, "font_style": {"font_size_px": 18}},
            {"page_num": 3, "save_id": "p3-a2", "plain": "‘Excellent’", "top": 274, "left": 1200, "font_style": {"font_size_px": 18}},
            {"page_num": 3, "save_id": "p3-b", "plain": "Bike racks", "top": 430, "left": 1500, "font_style": {"font_size_px": 18}},
            {"page_num": 6, "save_id": "p6-heading", "plain": "AMENITIES", "top": 90, "left": 1100, "font_style": {"font_size_px": 30}},
            {"page_num": 6, "save_id": "p6-a", "plain": "01 The Royal Exchange", "top": 150, "left": 1100, "font_style": {"font_size_px": 18}},
            {"page_num": 6, "save_id": "p6-b", "plain": "02 Rosslyn Coffee", "top": 176, "left": 1100, "font_style": {"font_size_px": 18}},
        ]

        defs = exact_pdf_layout._build_amenity_defs(entries)
        values = [value for _, _, value, _, _ in defs]

        self.assertTrue(any("Premium" in value for value in values))
        self.assertTrue(any("BREEAM" in value for value in values))
        self.assertTrue(any("Bike racks" in value for value in values))
        self.assertFalse(any("Royal Exchange" in value for value in values))
        self.assertFalse(any("Rosslyn Coffee" in value for value in values))

    def test_service_icons_require_real_service_section_headings(self):
        entries = [
            {"page_num": 3, "save_id": "floor-label", "plain": "4th floor meeting room space", "top": 620, "left": 40, "font_style": {"font_size": 12}},
            {"page_num": 5, "save_id": "core-heading", "plain": "C O R E S E R V I C E S", "top": 90, "left": 620, "font_style": {"font_size": 48}},
            {"page_num": 5, "save_id": "clean", "plain": "Daily Cleaning &\nWaste Management", "top": 230, "left": 620, "font_style": {"font_size": 13}},
            {"page_num": 5, "save_id": "repair", "plain": "Maintenance & Repairs", "top": 230, "left": 820, "font_style": {"font_size": 13}},
            {"page_num": 5, "save_id": "custom-heading", "plain": "C U S T O M I S A T I O N S", "top": 520, "left": 620, "font_style": {"font_size": 48}},
            {"page_num": 5, "save_id": "snacks", "plain": "Healthy Snacks", "top": 700, "left": 820, "font_style": {"font_size": 13}},
        ]

        icons = exact_pdf_layout._build_service_icon_defs(entries)
        labels = [icon["label"] for icon in icons]

        self.assertNotIn("4th floor meeting room space", labels)
        self.assertIn("Daily Cleaning &\nWaste Management", labels)
        self.assertIn("Maintenance & Repairs", labels)
        self.assertIn("Healthy Snacks", labels)
        self.assertEqual(next(icon for icon in icons if "Cleaning" in icon["label"])["group"], "C O R E S E R V I C E S")
        self.assertEqual(next(icon for icon in icons if "Snacks" in icon["label"])["group"], "C U S T O M I S A T I O N S")
        self.assertEqual(next(icon for icon in icons if "Cleaning" in icon["label"])["icon_id"], "broom")
        self.assertEqual(next(icon for icon in icons if "Repairs" in icon["label"])["icon_id"], "gear")
        self.assertEqual(next(icon for icon in icons if "Snacks" in icon["label"])["icon_id"], "apple")

    def test_contact_fields_merge_line_fragments_and_ignore_footer_metadata(self):
        entries = [
            {"page_num": 8, "save_id": "name1", "plain": "Abigail", "top": 652, "left": 68},
            {"page_num": 8, "save_id": "name2", "plain": "Duckworth", "top": 652, "left": 113},
            {"page_num": 8, "save_id": "phone1", "plain": "07886 170 66", "top": 674, "left": 68},
            {"page_num": 8, "save_id": "phone2", "plain": "3", "top": 673, "left": 158},
            {"page_num": 8, "save_id": "email1", "plain": "abigail.duckworth", "top": 694, "left": 68},
            {"page_num": 8, "save_id": "email2", "plain": "@bbgreal.com", "top": 694, "left": 171},
            {
                "page_num": 8,
                "save_id": "footer",
                "plain": "Design and production: www.stuartchapmandesign.co.uk 020 3983 1665",
                "top": 1048,
                "left": 68,
            },
            {
                "page_num": 8,
                "save_id": "cre8te-footer",
                "plain": "Designed and produced by Cre8te – 020 3468 5760 – cre8te.london",
                "top": 1088,
                "left": 68,
            },
        ]

        contacts = exact_pdf_layout._build_contact_fields(entries)

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Abigail Duckworth")
        self.assertEqual(contacts[0]["phone"], "07886 170 663")
        self.assertEqual(contacts[0]["email"], "abigail.duckworth@bbgreal.com")
        self.assertEqual(contacts[0]["line_prefixes"], [])

    def test_contact_fields_capture_international_uk_phone_lines(self):
        entries = [
            {"page_num": 14, "save_id": "name", "plain": "Jason Nearchou", "top": 757, "left": 76},
            {"page_num": 14, "save_id": "phone", "plain": "M: +44 (0) 770 439 7381", "top": 774, "left": 76},
            {"page_num": 14, "save_id": "email", "plain": "Jason.Nearchou@nmrk.com", "top": 790, "left": 76},
        ]

        contacts = exact_pdf_layout._build_contact_fields(entries)

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Jason Nearchou")
        self.assertEqual(contacts[0]["phone"], "+44 (0) 770 439 7381")
        self.assertEqual(contacts[0]["email"], "Jason.Nearchou@nmrk.com")
        self.assertEqual(contacts[0]["line_prefixes"], ["M"])

    def test_default_contact_groups_render_as_semantic_editable_blocks(self):
        pages = [
            {
                "page_num": 7,
                "width": 900,
                "height": 600,
                "body": """
<p class="ft1 pdf-text" style="position:absolute;top:100px;left:80px;white-space:nowrap" contenteditable="true" data-save-id="name">Tom Boggis</p>
<p class="ft2 pdf-text" style="position:absolute;top:130px;left:80px;white-space:nowrap" contenteditable="true" data-save-id="mobile-prefix">M</p>
<p class="ft2 pdf-text" style="position:absolute;top:130px;left:108px;white-space:nowrap" contenteditable="true" data-save-id="mobile">07795 070 676</p>
<p class="ft2 pdf-text" style="position:absolute;top:160px;left:80px;white-space:nowrap" contenteditable="true" data-save-id="email-prefix">E</p>
<p class="ft2 pdf-text" style="position:absolute;top:160px;left:108px;white-space:nowrap" contenteditable="true" data-save-id="email">tom.boggis@bbgreal.com</p>
""",
                "text_entries": [
                    {"page_num": 7, "save_id": "name", "plain": "Tom Boggis", "top": 100, "left": 80},
                    {"page_num": 7, "save_id": "mobile-prefix", "plain": "M", "top": 130, "left": 80},
                    {"page_num": 7, "save_id": "mobile", "plain": "07795 070 676", "top": 130, "left": 108},
                    {"page_num": 7, "save_id": "email-prefix", "plain": "E", "top": 160, "left": 80},
                    {"page_num": 7, "save_id": "email", "plain": "tom.boggis@bbgreal.com", "top": 160, "left": 108},
                ],
            }
        ]
        config = _build_structured_field_config(pages)

        exact_pdf_layout._apply_default_structured_field_layouts(pages, config)

        body = pages[0]["body"]
        self.assertIn('data-contact-semantic-block="true"', body)
        self.assertIn("07795 070 676", body)
        self.assertIn("tom.boggis@bbgreal.com", body)
        self.assertIn('<span class="exact-contact-prefix">M</span>', body)
        self.assertIn('<span class="exact-contact-prefix">E</span>', body)
        self.assertRegex(body, r'class="[^"]*exact-field-hidden[^"]*"[^>]*data-save-id="mobile"|data-save-id="mobile"[^>]*class="[^"]*exact-field-hidden')
        self.assertRegex(body, r'class="[^"]*exact-field-hidden[^"]*"[^>]*data-save-id="email"|data-save-id="email"[^>]*class="[^"]*exact-field-hidden')

    def test_contact_semantic_css_uses_browser_safe_font_for_reconstructed_text(self):
        self.assertIn('.pdf-text[data-contact-semantic-block="true"]', self.html)
        self.assertIn("font-family: Arial, sans-serif !important;", self.html)
        self.assertIn(".exact-contact-value", self.html)

    def test_map_labels_merge_spaced_road_fragments_but_keep_brand_labels(self):
        self.assertTrue(exact_pdf_layout._looks_like_map_label_entry("A U ST"))
        self.assertTrue(exact_pdf_layout._looks_like_map_label_entry("W O R MW"))
        self.assertEqual(
            [
                item["plain"]
                for item in exact_pdf_layout._merge_map_label_fragments(
                    [
                        {"save_id": "a", "plain": "L O N DO", "left": 10, "top": 10},
                        {"save_id": "b", "plain": "N   WAL", "left": 60, "top": 22},
                        {"save_id": "c", "plain": "L", "left": 100, "top": 34},
                    ]
                )
            ],
            ["LONDON WALL"],
        )
        self.assertEqual(exact_pdf_layout._merge_map_label_fragments([{"save_id": "x", "plain": "TREET", "left": 10, "top": 10}]), [])
        self.assertTrue(exact_pdf_layout._looks_like_subject_property_label("13 AUSTIN FRIARS"))
        self.assertFalse(exact_pdf_layout._looks_like_subject_property_label("1Rebel"))
        self.assertFalse(exact_pdf_layout._looks_like_subject_property_label("7 MINUTE WALK"))
        fields = exact_pdf_layout._build_map_label_fields(
            {
                "labels": [
                    {"key": "station", "text": "MOORGATE", "save_id": "station", "rank": 1, "top": 20, "left": 0},
                    {"key": "subject", "text": "13 AUSTIN FRIARS", "save_id": "subject", "rank": 0, "top": 100, "left": 0},
                ]
            }
        )
        self.assertEqual(fields[0]["value"], "13 AUSTIN FRIARS")
        self.assertTrue(exact_pdf_layout._looks_like_map_label_entry("Moorgate"))
        self.assertTrue(exact_pdf_layout._looks_like_map_label_entry("KOBOX"))

    def test_amenity_facade_field_matches_accented_pdf_text(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 4,
                    "text_entries": [
                        {
                            "page_num": 4,
                            "save_id": "exact-page4-text2",
                            "plain": "AMENITIES",
                            "left": 42,
                            "top": 80,
                        },
                        {
                            "page_num": 4,
                            "save_id": "exact-page4-text3",
                            "plain": "Victorian Façade",
                            "left": 42,
                            "top": 128,
                        }
                    ],
                }
            ]
        )

        facade_field = next(field for field in config["amenities"] if field["value"] == "Victorian Façade")

        self.assertEqual(
            facade_field["targets"],
            ["exact-page4-text3"],
            "The editable Victorian Facade amenity must target the extracted accented PDF text node.",
        )
        self.assertEqual(facade_field["value"], "Victorian Façade")

    def test_inventory_multiline_amenity_group_keeps_line_breaks_and_bounds(self):
        pages = [
            {
                "page_num": 4,
                "width": 600,
                "height": 400,
                "body": (
                    '<p style="position:absolute;top:100px;left:40px;white-space:nowrap" '
                    'class="pdf-text" contenteditable="true" data-save-id="a1" '
                    'data-plain-text="Newly Refurbished to Contemporary Style with Exposed Services">'
                    "Newly Refurbished to Contemporary Style with Exposed Services</p>"
                    '<p style="position:absolute;top:100px;left:220px;white-space:nowrap" '
                    'class="pdf-text" contenteditable="true" data-save-id="a2" '
                    'data-plain-text="In-House Gym">In-House Gym</p>'
                ),
                "inventory_amenity_label_groups": [
                    {
                        "page_num": 4,
                        "label": "Newly Refurbished to Contemporary Style with Exposed Services",
                        "value": "Newly Refurbished to\nContemporary Style\nwith Exposed Services",
                        "line_count": 3,
                        "bbox": {"left": 40, "top": 100, "width": 120, "height": 48},
                    }
                ],
                "text_entries": [
                    {"page_num": 4, "save_id": "heading", "plain": "AMENITIES", "top": 40, "left": 40, "font_style": {"font_size_px": 34}},
                    {
                        "page_num": 4,
                        "save_id": "a1",
                        "plain": "Newly Refurbished to Contemporary Style with Exposed Services",
                        "top": 100,
                        "left": 40,
                        "font_style": {"font_size_px": 13},
                    },
                    {"page_num": 4, "save_id": "a2", "plain": "In-House Gym", "top": 100, "left": 220, "font_style": {"font_size_px": 13}},
                    {"page_num": 4, "save_id": "a3", "plain": "Showers", "top": 180, "left": 40, "font_style": {"font_size_px": 13}},
                    {"page_num": 4, "save_id": "a4", "plain": "Bike Racks", "top": 180, "left": 220, "font_style": {"font_size_px": 13}},
                ],
            }
        ]

        config = _build_structured_field_config(pages)
        multiline = next(field for field in config["amenities"] if field["targets"] == ["a1"])

        self.assertEqual(multiline["kind"], "html-lines")
        self.assertEqual(multiline["value"], "Newly Refurbished to\nContemporary Style\nwith Exposed Services")
        exact_pdf_layout._apply_default_structured_field_layouts(pages, config)

        self.assertIn("Newly Refurbished to<br/>Contemporary Style<br/>with Exposed Services", pages[0]["body"])
        self.assertIn("width:128.00px", pages[0]["body"])
        self.assertIn("white-space:normal", pages[0]["body"])

    def test_structured_field_config_infers_non_austin_amenities_and_contacts(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 2,
                    "text_entries": [
                        {"page_num": 2, "save_id": "p2-title", "plain": "FEATURES", "top": 40, "left": 40},
                        {"page_num": 2, "save_id": "p2-a1", "plain": "Rooftop Terrace", "top": 100, "left": 40},
                        {"page_num": 2, "save_id": "p2-a2", "plain": "Secure Bike Store", "top": 130, "left": 40},
                        {"page_num": 2, "save_id": "p2-a3", "plain": "New Meeting Rooms", "top": 160, "left": 40},
                    ],
                },
                {
                    "page_num": 3,
                    "text_entries": [
                        {
                            "page_num": 3,
                            "save_id": "p3-contact",
                            "plain": "Nina Carter 07700 900 123 nina@example.com",
                            "top": 300,
                            "left": 60,
                        }
                    ],
                },
            ]
        )

        amenity_values = [field["value"] for field in config["amenities"]]
        self.assertIn("Rooftop Terrace", amenity_values)
        self.assertIn("Secure Bike Store", amenity_values)
        self.assertEqual(next(field for field in config["amenities"] if field["value"] == "Secure Bike Store")["icon_id"], "bicycle")
        self.assertEqual(config["contacts"][0]["name"], "Nina Carter")
        self.assertEqual(config["contacts"][0]["targets"], ["p3-contact"])
        self.assertIn("agency1", config["agency_logos"])

    def test_plan_page_labels_are_not_promoted_to_global_amenities(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 31,
                    "width": 900,
                    "height": 640,
                    "image_regions": [
                        {
                            "role": "space-plan",
                            "type": "floorplan",
                            "bbox": {"left": 80, "top": 90, "width": 720, "height": 420},
                        }
                    ],
                    "text_entries": [
                        {"page_num": 31, "save_id": "exact-page31-text20", "plain": "AMENITIES", "top": 60, "left": 80},
                        {"page_num": 31, "save_id": "exact-page31-text21", "plain": "Copy/ Print", "top": 140, "left": 120},
                        {"page_num": 31, "save_id": "exact-page31-text24", "plain": "Work lounge", "top": 180, "left": 120},
                    ],
                },
                {
                    "page_num": 2,
                    "text_entries": [
                        {"page_num": 2, "save_id": "exact-page2-text1", "plain": "FEATURES", "top": 50, "left": 50},
                        {"page_num": 2, "save_id": "exact-page2-text2", "plain": "Bike Storage", "top": 110, "left": 50},
                        {"page_num": 2, "save_id": "exact-page2-text3", "plain": "Showers", "top": 110, "left": 220},
                    ],
                },
            ]
        )

        amenity_targets = {
            target
            for amenity in config["amenities"]
            for target in amenity.get("targets", [])
        }

        self.assertNotIn("exact-page31-text21", amenity_targets)
        self.assertNotIn("exact-page31-text24", amenity_targets)
        self.assertIn("exact-page2-text2", amenity_targets)
        self.assertIn("exact-page2-text3", amenity_targets)

    def test_map_region_infers_v1_payload_and_editable_labels(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 7,
                    "width": 640,
                    "height": 480,
                    "text_entries": [
                        {"page_num": 7, "save_id": "p7-building", "plain": "25 Harbour Yard", "top": 220, "left": 300},
                        {"page_num": 7, "save_id": "p7-station", "plain": "Central Station", "top": 120, "left": 180},
                        {"page_num": 7, "save_id": "p7-street", "plain": "King Street", "top": 150, "left": 390},
                        {"page_num": 7, "save_id": "p7-market", "plain": "North Market", "top": 390, "left": 120},
                        {"page_num": 7, "save_id": "p7-cafe", "plain": "Blend Cafe", "top": 430, "left": 500},
                    ],
                }
            ]
        )

        map_region = config["map_region"]
        self.assertEqual(map_region["center"], {})
        self.assertEqual(map_region["content"]["building_name"], "25 Harbour Yard")
        self.assertGreaterEqual(len(config["map_label_fields"]), 5)
        self.assertIn("p7-building", [field["targets"][0] for field in config["map_label_fields"]])

    def test_map_region_uses_generic_map_evidence_for_non_austin_pages(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 1,
                    "width": 640,
                    "height": 480,
                    "text_entries": [
                        {"page_num": 1, "save_id": "p1-title", "plain": "Riverside Works", "top": 40, "left": 40},
                        {"page_num": 1, "save_id": "p1-copy", "plain": "Refurbished office suites", "top": 90, "left": 40},
                    ],
                },
                {
                    "page_num": 5,
                    "width": 640,
                    "height": 480,
                    "text_entries": [
                        {"page_num": 5, "save_id": "p5-central", "plain": "Central Station", "top": 120, "left": 180},
                        {"page_num": 5, "save_id": "p5-market", "plain": "Market Square", "top": 190, "left": 320},
                        {"page_num": 5, "save_id": "p5-street", "plain": "Bridge Street", "top": 250, "left": 120},
                        {"page_num": 5, "save_id": "p5-cafe", "plain": "Harbour Cafe", "top": 310, "left": 420},
                    ],
                },
            ]
        )

        self.assertEqual(config["map_region"]["page"], "5")
        self.assertIn({"name": "Central Station"}, config["map_region"]["content"]["stations"])
        self.assertEqual(config["map_region"]["content"]["building_name"], "Subject Property")
        self.assertGreaterEqual(len(config["map_label_fields"]), 4)

    def test_multiple_detected_map_pages_render_multiple_map_controls(self):
        pages = [
            {
                "page_num": 5,
                "width": 640,
                "height": 480,
                "body": "",
                "text_entries": [
                    {"page_num": 5, "save_id": "p5-central", "plain": "Central Station", "top": 120, "left": 180},
                    {"page_num": 5, "save_id": "p5-market", "plain": "Market Square", "top": 190, "left": 320},
                    {"page_num": 5, "save_id": "p5-street", "plain": "Bridge Street", "top": 250, "left": 120},
                    {"page_num": 5, "save_id": "p5-cafe", "plain": "Harbour Cafe", "top": 310, "left": 420},
                ],
                "image_slots": [],
            },
            {
                "page_num": 13,
                "width": 640,
                "height": 480,
                "body": "",
                "text_entries": [
                    {"page_num": 13, "save_id": "p13-heading", "plain": "Residential conversion comparable schemes", "top": 60, "left": 40},
                    {"page_num": 13, "save_id": "p13-table", "plain": "Scheme Status Notes", "top": 132, "left": 40},
                ],
                "inventory_page_purpose": "connectivitymap",
                "inventory_map_expected": True,
                "image_regions": [
                    {
                        "id": "p013-inventory-map-1",
                        "role": "map",
                        "type": "map",
                        "bbox": {"left": 390, "top": 20, "width": 210, "height": 220},
                        "confidence": 0.78,
                        "source_evidence": {"source": "/api/projects/demo/exact_assets/page013-full.png"},
                    }
                ],
                "image_slots": [],
            },
        ]

        config = _build_structured_field_config(pages)
        self.assertEqual([region["page"] for region in config["map_regions"]], ["5", "13"])

        html = _render_exact_html(
            project_id="multi-map-regression",
            page_width=640,
            page_height=480,
            pages=pages,
            poppler_css="",
            font_css="",
        )

        self.assertIn('data-save-id="exact-page5-map"', html)
        self.assertIn('data-save-id="exact-page13-map"', html)
        self.assertIn("window.__EXACT_MAP_REGIONS__", html)

    def test_inventory_backed_map_region_is_rendered_when_page_expects_map(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 13,
                    "width": 1280,
                    "height": 880,
                    "inventory_page_purpose": "connectivitymap",
                    "inventory_map_expected": True,
                    "text_entries": [
                        {"page_num": 13, "save_id": "p13-king", "plain": "KING ST", "top": 80, "left": 640},
                        {"page_num": 13, "save_id": "p13-hamm", "plain": "HAMMERSMITH RD", "top": 150, "left": 720},
                        {"page_num": 13, "save_id": "p13-station", "plain": "Hammersmith", "top": 220, "left": 900},
                        {"page_num": 13, "save_id": "p13-bridge", "plain": "Hammersmith Bridge", "top": 260, "left": 780},
                        {"page_num": 13, "save_id": "p13-square", "plain": "Market Square", "top": 310, "left": 1020},
                        {"page_num": 13, "save_id": "p13-rd", "plain": "Fulham Palace Road", "top": 360, "left": 860},
                        {"page_num": 13, "save_id": "p13-broadway", "plain": "Hammersmith Broadway", "top": 420, "left": 930},
                        {"page_num": 13, "save_id": "p13-court", "plain": "Barons Court", "top": 490, "left": 1060},
                        {"page_num": 13, "save_id": "p13-street", "plain": "Beavor Lane", "top": 540, "left": 980},
                    ],
                    "image_regions": [
                        {
                            "id": "p013-inventory-map-1",
                            "role": "map",
                            "type": "map",
                            "bbox": {"left": 485, "top": 0, "width": 785, "height": 874},
                            "confidence": 0.82,
                            "source_evidence": {"source": "extraction-inventory map_regions"},
                        }
                    ],
                    "image_slots": [],
                }
            ]
        )

        self.assertEqual([region["page"] for region in config["map_regions"]], ["13"])
        self.assertEqual(config["map_regions"][0]["left"], 485)
        self.assertEqual(config["map_regions"][0]["mode"], "source-pdf")

    def test_pdf_image_map_region_is_rendered_as_map_slot(self):
        page = {
            "page_num": 4,
            "width": 842,
            "height": 595,
            "inventory_page_purpose": "locationintroduction",
            "inventory_map_expected": False,
            "text_entries": [
                {"page_num": 4, "save_id": "p4-location", "plain": "Location", "top": 42, "left": 36},
                {"page_num": 4, "save_id": "p4-metropolitan", "plain": "Metropolitan", "top": 238, "left": 520},
                {"page_num": 4, "save_id": "p4-chancery", "plain": "Chancery Lane - Central", "top": 270, "left": 570},
                {"page_num": 4, "save_id": "p4-address", "plain": "44 - 46 Sekforde Street", "top": 318, "left": 610},
            ],
            "image_regions": [
                {
                    "id": "p004-image-0001",
                    "role": "map",
                    "type": "map",
                    "bbox": {"left": 277.69, "top": 84.34, "width": 610.07, "height": 393.39},
                    "confidence": 0.72,
                    "editable": True,
                    "replaceable": True,
                    "source_evidence": {
                        "source": "PyMuPDF image xref placement",
                        "reason": "map page context with low-saturation embedded region",
                        "context": {"has_map": True, "has_space_plan": False, "is_contact_page": False},
                    },
                }
            ],
            "image_slots": [],
        }

        config = _build_structured_field_config([page])

        self.assertEqual([region["page"] for region in config["map_regions"]], ["4"])
        self.assertEqual(config["map_regions"][0]["save_id"], "exact-page4-map")
        self.assertEqual(config["map_regions"][0]["mode"], "source-pdf")

    def test_inventory_map_bbox_is_trimmed_before_adjacent_table_sections(self):
        pages = [
            {
                "page_num": 13,
                "width": 1280,
                "height": 880,
                "text_entries": [
                    {
                        "page_num": 13,
                        "save_id": "intro-copy",
                        "plain": "In recent years, a substantial number of office buildings have been promoted for conversion.",
                        "top": 150,
                        "left": 120,
                        "width": 520,
                    },
                    {"page_num": 13, "save_id": "map-road", "plain": "HAMMERSMITH RD", "top": 120, "left": 760},
                    {"page_num": 13, "save_id": "table-scheme", "plain": "Scheme", "top": 344, "left": 280},
                    {"page_num": 13, "save_id": "table-proposal", "plain": "Proposal", "top": 344, "left": 520},
                    {"page_num": 13, "save_id": "table-status", "plain": "Status / Notes", "top": 344, "left": 880},
                ],
                "image_regions": [],
                "image_slots": [],
            }
        ]
        inventory = {
            "pages": [
                {
                    "page_number": 13,
                    "page_purpose": "connectivity map",
                    "detected_features": ["map"],
                    "size": {"width": 1280, "height": 880},
                    "map_regions": [
                        {
                            "bbox": {"x": 485, "y": 0, "width": 785, "height": 874},
                            "extraction_method": "PDF text labels and vector detection",
                        }
                    ],
                }
            ]
        }

        exact_pdf_layout._merge_inventory_map_regions(pages, inventory)
        config = _build_structured_field_config(pages)

        self.assertEqual([region["page"] for region in config["map_regions"]], ["13"])
        self.assertLess(config["map_regions"][0]["height"], 344)
        self.assertGreater(config["map_regions"][0]["left"], 640)

    def test_contact_page_map_like_inventory_region_is_not_rendered_as_map_slot(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 14,
                    "width": 640,
                    "height": 480,
                    "inventory_page_purpose": "contactsandterms",
                    "inventory_map_expected": False,
                    "text_entries": [
                        {"page_num": 14, "save_id": "p14-name", "plain": "VIEWINGS", "top": 120, "left": 40},
                        {"page_num": 14, "save_id": "p14-road", "plain": "Hammersmith Road", "top": 160, "left": 40},
                        {"page_num": 14, "save_id": "p14-email", "plain": "agent@example.com", "top": 220, "left": 40},
                    ],
                    "image_regions": [
                        {
                            "id": "p014-false-map",
                            "role": "map",
                            "type": "map",
                            "bbox": {"left": 0, "top": 0, "width": 300, "height": 260},
                            "confidence": 0.8,
                        }
                    ],
                }
            ]
        )

        self.assertEqual(config["map_regions"], [])

    def test_location_copy_is_not_promoted_to_fake_map_region(self):
        entries = [
            ("Location", 22, 42),
            ("Perfectly", 22, 90),
            ("positioned", 86, 90),
            ("between", 158, 90),
            ("Farringdon", 220, 90),
            ("and", 294, 90),
            ("Clerkenwell Green, 44-46 Sekforde Street enjoys", 22, 104),
            ("exceptional", 22, 118),
            ("connectivity", 100, 118),
            ("Transport connections:", 22, 274),
            ("Farringdon - Elizabeth, Circle, Hammersmith &", 43, 302),
            ("City, Metropolitan & Thameslink", 43, 316),
            ("Barbican - Circle, Hammersmith & City &", 43, 345),
            ("Metropolitan", 43, 359),
            ("Chancery Lane - Central", 43, 387),
        ]
        config = _build_structured_field_config(
            [
                {
                    "page_num": 4,
                    "width": 640,
                    "height": 480,
                    "text_entries": [
                        {"page_num": 4, "save_id": f"p4-{index}", "plain": text, "top": top, "left": left}
                        for index, (text, left, top) in enumerate(entries, start=1)
                    ],
                }
            ]
        )

        self.assertEqual(config["map_region"], {})

    def test_empty_structured_config_has_no_benchmark_specific_defaults(self):
        config = _build_structured_field_config([])
        rendered = " ".join(
            [
                config["cover_title"]["value"],
                config["cover_offer"]["value"],
                config["amenities_title"]["value"],
                " ".join(config["agency_logos"].keys()),
                " ".join(logo.get("defaultText", "") for logo in config["agency_logos"].values()),
            ]
        ).lower()

        self.assertNotIn("austin", rendered)
        self.assertNotIn("friars", rendered)
        self.assertNotIn("moorgate", rendered)
        self.assertNotIn("bbg", rendered)
        self.assertNotIn("bnp", rendered)

    def test_global_brochure_logo_controls_round_trip_cover_and_repeated_marks(self):
        drawer = _find_fields_drawer(self.semantic_root)
        self.assertIsNotNone(drawer, "Global brochure logo controls should live in the Fields drawer.")

        control_keys = _control_keys(drawer) if drawer else []
        self.assertTrue(
            _has_field_control(control_keys, "global", "logo", "upload")
            or _has_field_control(control_keys, "brochure", "logo", "upload")
            or 'data-global-logo-field="image"' in self.semantic_html,
            "Fields drawer should expose a global brochure logo upload control.",
        )
        self.assertTrue(
            _has_field_control(control_keys, "global", "logo", "reset", "clear")
            or _has_field_control(control_keys, "brochure", "logo", "reset", "clear")
            or re.search(r"data-global-logo-(?:clear|reset)", self.semantic_html),
            "Fields drawer should expose a global brochure logo reset/clear control.",
        )
        self.assertTrue(
            _has_field_control(control_keys, "global", "logo", "size")
            or _has_field_control(control_keys, "brochure", "logo", "size")
            or 'data-global-logo-field="size"' in self.semantic_html,
            "Fields drawer should expose global logo size controls, not only fixed PDF marks.",
        )
        self.assertTrue(
            _has_field_control(control_keys, "global", "logo", "position")
            or _has_field_control(control_keys, "brochure", "logo", "position", "pos")
            or 'data-global-logo-field="position"' in self.semantic_html,
            "Fields drawer should expose global logo position controls.",
        )

        self.assertRegex(
            self.semantic_html,
            r"\b(globalLogo|brochureLogo)\b",
            "Serialized exact state should use a named global brochure logo object, not an isolated toolbar value.",
        )
        self.assertRegex(
            self.semantic_html,
            r"\b(applyGlobalLogoState|applyBrochureLogoState|getGlobalLogoState|collectGlobalLogoState)\b",
            "Save/reload should have explicit global logo state helpers so one control updates cover and repeated page marks.",
        )
        self.assertRegex(
            self.semantic_html,
            r"data-global-logo-field|data-global-logo-target|data-brochure-logo-target",
            "Logo DOM should carry stable global target hooks that can bind the cover mark and repeated top-left marks together.",
        )

    def test_source_facade_logo_marks_are_detected_as_editable_slots(self):
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:  # pragma: no cover - Pillow is a project dependency.
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "page001-full.png"
            image = Image.new("RGB", (640, 480), "#333132")
            draw = ImageDraw.Draw(image)
            yellow = "#ffea00"
            for x in (360, 420, 500, 560):
                draw.line((x, 120, x, 440), fill=yellow, width=2)
            for y in (120, 180, 260, 340, 440):
                draw.line((340, y, 580, y), fill=yellow, width=2)
            for x0 in (365, 445, 525):
                draw.arc((x0, 45, x0 + 48, 130), 180, 360, fill=yellow, width=2)
            image.save(image_path)

            slots = exact_pdf_layout._build_source_logo_defs(
                [
                    {
                        "page_num": 1,
                        "width": 640,
                        "height": 480,
                        "background_path": str(image_path),
                        "image_slots": [],
                        "text_entries": [],
                    }
                ]
            )

        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["page"], "1")
        self.assertEqual(slots[0]["kind"], "facade")
        self.assertGreater(slots[0]["height"], 300)

        overlay = exact_pdf_layout._render_semantic_overlays(
            {"page_num": 1},
            {"source_logos": slots},
        )
        self.assertIn('data-source-logo-mask-role="dark"', overlay)
        self.assertIn("--source-logo-mask:var(--exact-dark);", overlay)

    def test_source_logo_detection_scales_rendered_background_pixels_to_page_coords(self):
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:  # pragma: no cover - Pillow is a project dependency.
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "page001-full.png"
            image = Image.new("RGB", (1280, 960), "#333132")
            draw = ImageDraw.Draw(image)
            yellow = "#ffea00"
            for x in range(720, 1081, 80):
                draw.line((x, 150, x, 840), fill=yellow, width=4)
            for y in range(180, 841, 80):
                draw.line((680, y, 1120, y), fill=yellow, width=4)
            image.save(image_path)

            slots = exact_pdf_layout._build_source_logo_defs(
                [
                    {
                        "page_num": 1,
                        "width": 640,
                        "height": 480,
                        "background_path": str(image_path),
                        "image_slots": [],
                        "text_entries": [],
                    }
                ]
            )

        self.assertEqual(len(slots), 1)
        self.assertLessEqual(slots[0]["left"] + slots[0]["width"], 640)
        self.assertLess(slots[0]["width"], 260)
        self.assertGreater(slots[0]["height"], 300)

    def test_white_header_mark_over_photo_is_detected_as_source_logo(self):
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:  # pragma: no cover - Pillow is a project dependency.
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "page001-full.png"
            image = Image.new("RGB", (640, 480), "#6f796d")
            draw = ImageDraw.Draw(image)
            for x in range(0, 640, 12):
                draw.line((x, 0, x + 140, 480), fill="#56634f", width=3)
            for x in (296, 322, 348):
                draw.rectangle((x, 28, x + 14, 66), fill="#ffffff")
            image.save(image_path)

            slots = exact_pdf_layout._build_source_logo_defs(
                [
                    {
                        "page_num": 1,
                        "width": 640,
                        "height": 480,
                        "background_path": str(image_path),
                        "image_slots": [{"left": 0, "top": 0, "width": 640, "height": 480}],
                        "text_entries": [],
                    }
                ]
            )

        self.assertEqual(len(slots), 1)
        self.assertLess(slots[0]["top"], 80)
        self.assertGreater(slots[0]["width"], 45)
        self.assertLess(slots[0]["height"], 70)

    def test_off_center_light_photo_detail_is_not_source_logo(self):
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:  # pragma: no cover - Pillow is a project dependency.
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "page004-full.png"
            image = Image.new("RGB", (640, 480), "#758078")
            draw = ImageDraw.Draw(image)
            draw.rectangle((500, 40, 560, 120), fill="#ffffff")
            image.save(image_path)

            slots = exact_pdf_layout._build_source_logo_defs(
                [
                    {
                        "page_num": 4,
                        "width": 640,
                        "height": 480,
                        "background_path": str(image_path),
                        "image_slots": [{"left": 0, "top": 0, "width": 640, "height": 480}],
                        "text_entries": [],
                        "source_image_marks": [],
                    }
                ]
            )

        self.assertEqual(slots, [])

    def test_non_cover_lower_yellow_plan_detail_is_not_source_logo(self):
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:  # pragma: no cover - Pillow is a project dependency.
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "page006-full.png"
            image = Image.new("RGB", (640, 480), "#fff7eb")
            draw = ImageDraw.Draw(image)
            yellow = "#ffea00"
            for x in range(220, 320, 16):
                draw.line((x, 260, x, 390), fill=yellow, width=2)
            for y in range(270, 390, 18):
                draw.line((200, y, 340, y), fill=yellow, width=2)
            image.save(image_path)

            slots = exact_pdf_layout._build_source_logo_defs(
                [
                    {
                        "page_num": 6,
                        "width": 640,
                        "height": 480,
                        "background_path": str(image_path),
                        "image_slots": [],
                        "text_entries": [],
                        "source_image_marks": [],
                    }
                ]
            )

        self.assertEqual(slots, [])

    def test_coloured_photo_detail_is_not_source_logo_without_extracted_mark(self):
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:  # pragma: no cover - Pillow is a project dependency.
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "page003-full.png"
            image = Image.new("RGB", (640, 480), "#454b43")
            draw = ImageDraw.Draw(image)
            draw.rectangle((200, 30, 520, 260), fill="#e9e9df")
            for x in range(290, 410, 12):
                draw.line((x, 34, x + 58, 82), fill="#7f4a2c", width=3)
            image.save(image_path)

            slots = exact_pdf_layout._build_source_logo_defs(
                [
                    {
                        "page_num": 3,
                        "width": 640,
                        "height": 480,
                        "background_path": str(image_path),
                        "image_slots": [{"left": 200, "top": 30, "width": 320, "height": 230}],
                        "text_entries": [],
                        "source_image_marks": [],
                    }
                ]
            )

        self.assertEqual(slots, [])

    def test_service_icon_matching_uses_word_boundaries(self):
        self.assertFalse(
            exact_pdf_layout._looks_like_service_icon_label("heritage. Originally built as a warehouse and carefully"),
            "Service icon extraction should not match the service token 'care' inside 'carefully'.",
        )
        self.assertTrue(exact_pdf_layout._looks_like_service_icon_label("Foliage Rental & Care"))

    def test_cover_glyphs_are_title_typography_not_section_headings(self):
        role = exact_pdf_layout._detect_typography_role(
            1,
            8,
            "S",
            {"font_size_px": 41, "stable_font_family": "BebasNeueBold", "source_font_family": "AAAAAA+BebasNeueBold"},
        )

        self.assertEqual(role, "cover-title")

    def test_large_bold_sentence_case_lines_are_section_headings(self):
        role = exact_pdf_layout._detect_typography_role(
            3,
            1,
            "Availability",
            {"font_size_px": 33, "stable_font_family": "Garet-Bold", "source_font_family": "AAAAAA+Garet-Bold"},
        )

        self.assertEqual(role, "section-heading")

    def test_large_bold_lowercase_heading_lines_are_section_headings(self):
        role = exact_pdf_layout._detect_typography_role(
            2,
            2,
            "creative heart of",
            {"font_size_px": 33, "stable_font_family": "Garet-Bold", "source_font_family": "AAAAAA+Garet-Bold"},
        )

        self.assertEqual(role, "section-heading")

    def test_large_body_copy_is_not_promoted_to_section_heading(self):
        role = exact_pdf_layout._detect_typography_role(
            2,
            4,
            "44-46 Sekforde Street presents a rare opportunity to",
            {
                "font_size_px": 16,
                "stable_font_family": "GlacialIndifference-Regular",
                "source_font_family": "CAAAAA+GlacialIndifference-Regular",
            },
        )

        self.assertEqual(role, "body")

    def test_ocr_cover_lines_become_editable_cover_title_targets(self):
        lines = [
            {
                "text": "ANCHOR HOUSE",
                "bbox": {"x": 160, "y": 80, "width": 320, "height": 64},
                "font_size": 59,
                "role": "cover-title",
                "confidence": 95,
            },
            {
                "text": "15-19 BRITTEN ST",
                "bbox": {"x": 42, "y": 94, "width": 180, "height": 20},
                "font_size": 18,
                "role": "body",
                "confidence": 92,
            },
        ]
        with mock.patch("brochure_maker.exact_pdf_layout.extract_ocr_text_lines", return_value=lines):
            body, count, entries = exact_pdf_layout._ocr_text_layer_for_page(
                Path("/tmp/missing.png"),
                page_num=1,
                width=640,
                height=360,
                font_styles={
                    "ft1": {
                        "stable_font_family": "P22UndergroundDemiBold",
                        "source_font_family": "KPWVND+P22UndergroundDemiBold",
                        "font_size_px": 59,
                        "color": "#ff685f",
                    }
                },
                start_index=0,
            )

        self.assertEqual(count, 2)
        self.assertIn('data-ocr-fallback="true"', body)
        self.assertIn('data-ocr-mask-colour="#ffffff"', body)
        self.assertIn("--exact-ocr-mask-colour:#ffffff", body)
        self.assertIn('data-typography-role="cover-title"', body)
        self.assertEqual(entries[0]["plain"], "ANCHOR HOUSE")
        config = _build_structured_field_config(
            [{"page_num": 1, "width": 640, "height": 360, "body": body, "text_entries": entries, "image_slots": []}]
        )
        self.assertEqual(config["cover_title"]["value"], "ANCHOR HOUSE")
        self.assertEqual(config["cover_title"]["targets"], ["exact-page1-text1"])

    def test_short_body_ocr_logo_fragment_is_not_cover_title_target(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 1,
                    "width": 1365,
                    "height": 1024,
                    "body": "",
                    "image_slots": [],
                    "text_entries": [
                        {
                            "save_id": "exact-page1-text1",
                            "page_num": 1,
                            "plain": "Hie",
                            "typography_role": "body",
                            "ocr_fallback": True,
                            "font_style": {"font_size_px": 341.32},
                            "left": 239,
                            "top": 328,
                        }
                    ],
                }
            ]
        )

        self.assertEqual(config["cover_title"]["value"], "")
        self.assertEqual(config["cover_title"]["targets"], [])
        self.assertEqual(config["cover_offer"]["value"], "")
        self.assertEqual(config["cover_offer"]["targets"], [])

    def test_headingless_feature_grid_becomes_amenity_icon_controls(self):
        entries = []
        specs = [
            ("Ability to provide", 107, 706),
            ("fully fitted office floors", 73, 740),
            ("Bicycle", 165, 999),
            ("storage", 164, 1034),
            ("High speed", 142, 1292),
            ("fibre", 178, 1327),
            ("On-site", 493, 706),
            ("gym", 512, 740),
            ("Shower", 493, 999),
            ("facilities", 489, 1034),
            ("2 x passenger", 461, 1292),
            ("lifts", 514, 1327),
            ("On-site", 823, 706),
            ("café", 843, 740),
            ("7 car parking", 794, 999),
            ("spaces", 828, 1034),
            ("VRF A/C", 821, 1292),
            ("system", 826, 1327),
        ]
        for index, (plain, left, top) in enumerate(specs, start=1):
            entries.append(
                {
                    "page_num": 3,
                    "save_id": f"exact-page3-text{index}",
                    "plain": plain,
                    "left": left,
                    "top": top,
                    "font_style": {"font_size_px": 25, "stable_font_family": "P22UndergroundBook"},
                }
            )

        config = _build_structured_field_config(
            [{"page_num": 3, "width": 2560, "height": 1440, "body": "", "text_entries": entries, "image_slots": []}]
        )

        amenity_values = [field["value"] for field in config["amenities"]]
        self.assertIn("Bicycle\nstorage", amenity_values)
        self.assertIn("On-site\ngym", amenity_values)
        self.assertIn("VRF A/C\nsystem", amenity_values)
        self.assertGreaterEqual(len(config["amenity_icons"]), 8)

    def test_vertical_cover_title_glyphs_become_readable_global_field(self):
        text_entries = []
        specs = [
            ("4", 100, 620),
            ("4", 100, 500),
            ("-", 100, 380),
            ("46", 100, 260),
            ("S", 160, 620),
            ("e", 160, 570),
            ("k", 160, 520),
            ("f", 160, 470),
            ("o", 160, 420),
            ("r", 160, 370),
            ("d", 160, 320),
            ("e", 160, 270),
            ("S", 210, 520),
            ("t", 210, 470),
            ("r", 210, 420),
            ("e", 210, 370),
            ("e", 210, 320),
            ("t", 210, 270),
        ]
        for index, (plain, left, top) in enumerate(specs, start=1):
            text_entries.append(
                {
                    "page_num": 1,
                    "save_id": f"exact-page1-text{index}",
                    "plain": plain,
                    "left": left,
                    "top": top,
                    "typography_role": "cover-title",
                    "font_style": {"font_size_px": 42, "stable_font_family": "BebasNeueBold"},
                }
            )

        config = _build_structured_field_config(
            [{"page_num": 1, "width": 640, "height": 900, "body": "", "text_entries": text_entries, "image_slots": []}]
        )

        self.assertEqual(config["cover_title"]["value"], "44-46\nSekforde\nStreet")
        self.assertEqual(len(config["cover_title"]["groups"]), 3)
        self.assertEqual(config["cover_title"]["groups"][0]["segment_lengths"], [1, 1, 1, 2])
        self.assertEqual(config["cover_title"]["groups"][0]["orientation"], "vertical-glyphs")
        self.assertGreater(config["cover_title"]["groups"][1]["height"], 280)
        self.assertGreater(config["cover_title"]["groups"][1]["width"], 20)

        rendered = _render_exact_html(
            project_id="vertical-title-regression",
            page_width=640,
            page_height=900,
            pages=[{"page_num": 1, "width": 640, "height": 900, "body": "", "text_entries": text_entries, "image_slots": []}],
            poppler_css="",
            font_css="",
        )
        self.assertIn("44-46", rendered)
        self.assertIn("data-title-groups=", rendered)
        self.assertIn("renderStackedTitleGroup", rendered)
        self.assertIn("collectTextLayout", rendered)
        self.assertIn("applyTextLayout", rendered)

    def test_vertical_cover_title_groups_can_use_pymupdf_geometry(self):
        text_entries = []
        specs = [
            ("4", 139, 633, 206),
            ("4", 139, 486, 206),
            ("-", 139, 338, 206),
            ("46", 139, 219, 206),
            ("S", 221, 510, 41),
            ("e", 221, 462, 41),
            ("k", 221, 413, 41),
            ("f", 221, 363, 41),
            ("o", 221, 316, 41),
            ("r", 221, 266, 41),
            ("d", 221, 217, 41),
            ("e", 221, 167, 41),
        ]
        for index, (plain, left, top, size) in enumerate(specs, start=1):
            text_entries.append(
                {
                    "page_num": 1,
                    "save_id": f"exact-page1-text{index}",
                    "plain": plain,
                    "left": left,
                    "top": top,
                    "typography_role": "cover-title",
                    "font_style": {"font_size_px": size, "stable_font_family": "BebasNeueBold"},
                }
            )

        config = _build_structured_field_config(
            [{"page_num": 1, "width": 1123, "height": 793, "body": "", "text_entries": text_entries, "image_slots": []}],
            cover_title_bboxes={
                "4446": {"left": -15.52, "top": 145.72, "width": 205.96, "height": 640.55, "source": "PyMuPDF text span"},
                "sekforde": {"left": 190.31, "top": 182.42, "width": 41.35, "height": 343.21, "source": "PyMuPDF grouped vertical text spans"},
            },
        )

        groups = config["cover_title"]["groups"]
        self.assertEqual(groups[0]["value"], "44-46")
        self.assertEqual(groups[0]["left"], -15.52)
        self.assertEqual(groups[0]["orientation"], "rotated-counterclockwise")
        self.assertEqual(groups[0]["bbox_source"], "PyMuPDF text span")
        self.assertEqual(groups[1]["value"], "Sekforde")
        self.assertEqual(groups[1]["left"], 190.31)
        self.assertEqual(groups[1]["bbox_source"], "PyMuPDF grouped vertical text spans")
        self.assertEqual(groups[1]["orientation"], "vertical-glyphs")

    def test_vertical_cover_title_group_recovers_missing_poppler_glyph_from_pymupdf(self):
        text_entries = []
        specs = [
            ("S", 221, 510),
            ("e", 221, 462),
            ("f", 221, 363),
            ("o", 221, 316),
            ("r", 221, 266),
            ("d", 221, 217),
            ("e", 221, 167),
            ("S", 262, 409),
            ("t", 262, 360),
            ("r", 262, 313),
            ("e", 262, 263),
            ("e", 262, 215),
            ("t", 262, 166),
        ]
        for index, (plain, left, top) in enumerate(specs, start=1):
            text_entries.append(
                {
                    "page_num": 1,
                    "save_id": f"exact-page1-text{index}",
                    "plain": plain,
                    "left": left,
                    "top": top,
                    "typography_role": "cover-title",
                    "font_style": {"font_size_px": 41, "stable_font_family": "BebasNeueBold"},
                }
            )

        config = _build_structured_field_config(
            [{"page_num": 1, "width": 1123, "height": 793, "body": "", "text_entries": text_entries, "image_slots": []}],
            cover_title_bboxes={
                "sekforde": {
                    "left": 190.31,
                    "top": 182.42,
                    "width": 41.35,
                    "height": 343.21,
                    "source": "PyMuPDF grouped vertical text spans",
                    "value": "Sekforde",
                },
                "street": {
                    "left": 231.33,
                    "top": 182.39,
                    "width": 41.35,
                    "height": 257.02,
                    "source": "PyMuPDF grouped vertical text spans",
                    "value": "Street",
                },
            },
        )

        groups = config["cover_title"]["groups"]
        self.assertEqual(groups[0]["value"], "Sekforde")
        self.assertEqual(config["cover_title"]["value"], "Sekforde\nStreet")
        self.assertEqual(groups[0]["bbox_source"], "PyMuPDF grouped vertical text spans")

        pages = [
            {
                "page_num": 1,
                "body": "".join(
                    f'<p class="ft10 pdf-text" style="position:absolute;top:{top}px;left:{left}px;white-space:nowrap" '
                    f'data-save-id="exact-page1-text{index}">{plain}</p>'
                    for index, (plain, left, top) in enumerate(specs, start=1)
                ),
            }
        ]
        exact_pdf_layout._apply_default_structured_field_layouts(pages, config)
        body = pages[0]["body"]

        self.assertIn("Sekforde", body)
        self.assertIn("<span>k</span>", body)
        self.assertIn('data-title-stack="true"', body)
        self.assertIn("exact-field-hidden", body)

    def test_default_cover_title_groups_are_baked_into_page_html(self):
        pages = [
            {
                "page_num": 1,
                "body": (
                    '<p class="ft11 pdf-text" style="position:absolute;top:633px;left:139px;white-space:nowrap" '
                    'data-save-id="exact-page1-text1">4</p>'
                    '<p class="ft11 pdf-text" style="position:absolute;top:486px;left:139px;white-space:nowrap" '
                    'data-save-id="exact-page1-text2">4</p>'
                    '<p class="ft10 pdf-text" style="position:absolute;top:510px;left:221px;white-space:nowrap" '
                    'data-save-id="exact-page1-text3">S</p>'
                    '<p class="ft10 pdf-text" style="position:absolute;top:462px;left:221px;white-space:nowrap" '
                    'data-save-id="exact-page1-text4">e</p>'
                ),
            }
        ]
        config = {
            "cover_title": {
                "groups": [
                    {
                        "value": "44",
                        "targets": ["exact-page1-text1", "exact-page1-text2"],
                        "orientation": "rotated-counterclockwise",
                        "left": -15.5,
                        "top": 145.7,
                        "width": 206.0,
                        "height": 640.6,
                    },
                    {
                        "value": "Se",
                        "targets": ["exact-page1-text3", "exact-page1-text4"],
                        "orientation": "vertical-bottom-up",
                        "left": 190.3,
                        "top": 182.4,
                        "width": 41.4,
                        "height": 95.0,
                    },
                ]
            }
        }

        exact_pdf_layout._apply_default_structured_field_layouts(pages, config)
        body = pages[0]["body"]

        self.assertIn('data-title-rotated="true"', body)
        self.assertIn('class="exact-title-rotated"', body)
        self.assertIn("top:145.70px", body)
        self.assertIn("width:206.00px", body)
        self.assertIn("height:640.60px", body)
        self.assertIn("justify-content:center", body)
        self.assertIn('data-title-stack="true"', body)
        self.assertIn('class="exact-title-stack"', body)
        self.assertIn("exact-field-hidden", body)

    def test_default_cover_title_glyph_groups_render_as_single_editable_stack(self):
        pages = [
            {
                "page_num": 1,
                "body": (
                    '<p class="ft10 pdf-text" style="position:absolute;top:510px;left:221px;white-space:nowrap" '
                    'data-save-id="exact-page1-text1">S</p>'
                    '<p class="ft10 pdf-text" style="position:absolute;top:462px;left:221px;white-space:nowrap" '
                    'data-save-id="exact-page1-text2">e</p>'
                ),
            }
        ]
        config = {
            "cover_title": {
                "groups": [
                    {
                        "value": "Se",
                        "targets": ["exact-page1-text1", "exact-page1-text2"],
                        "orientation": "vertical-glyphs",
                        "left": 190.3,
                        "top": 182.4,
                        "width": 41.4,
                        "height": 95.0,
                    },
                ]
            }
        }

        exact_pdf_layout._apply_default_structured_field_layouts(pages, config)
        body = pages[0]["body"]

        self.assertIn('data-title-glyph-group="true"', body)
        self.assertIn('data-structured-field="true"', body)
        self.assertIn('data-title-stack="true"', body)
        self.assertIn('class="exact-title-stack"', body)
        self.assertIn("<span>S</span>", body)
        self.assertIn("<span>e</span>", body)
        self.assertIn("top:182.40px", body)
        self.assertIn("height:95.00px", body)
        self.assertIn("exact-field-hidden", body)
        self.assertIn('contenteditable="false"', body)
        self.assertIn('aria-hidden="true"', body)

    def test_source_mark_asset_recovers_transparent_matte(self):
        image = Image.new("RGB", (32, 24), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 4, 12, 20), fill="white")
        draw.rectangle((18, 4, 22, 20), fill="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        output, ext = exact_pdf_layout._source_mark_asset_bytes(buffer.getvalue(), "png")
        recovered = Image.open(io.BytesIO(output)).convert("RGBA")

        self.assertEqual(ext, "png")
        self.assertEqual(recovered.getpixel((0, 0))[3], 0)
        self.assertEqual(recovered.getpixel((10, 10))[3], 255)

    def test_photo_likelihood_prefers_real_photo_over_paper_texture(self):
        texture = Image.new("RGB", (120, 80), "#202231")
        draw = ImageDraw.Draw(texture)
        for index in range(0, 120, 8):
            draw.line((index, 0, index + 20, 80), fill="#2a2d3d", width=1)
        texture_buffer = io.BytesIO()
        texture.save(texture_buffer, format="PNG")

        photo = Image.new("RGB", (120, 80), "#dde8ef")
        draw = ImageDraw.Draw(photo)
        draw.rectangle((0, 35, 120, 80), fill="#435d2f")
        draw.rectangle((20, 15, 92, 68), fill="#b8afa4")
        draw.rectangle((36, 28, 50, 48), fill="#1d2d44")
        draw.rectangle((62, 28, 78, 48), fill="#1d2d44")
        draw.ellipse((84, 6, 116, 38), fill="#59752d")
        photo_buffer = io.BytesIO()
        photo.save(photo_buffer, format="JPEG")

        self.assertGreater(
            exact_pdf_layout._image_photo_likelihood_score(photo_buffer.getvalue()),
            exact_pdf_layout._image_photo_likelihood_score(texture_buffer.getvalue()) + 10,
        )

    def test_duplicate_image_slots_keep_more_photo_like_candidate_after_refinement(self):
        slots = [
            {"left": 100, "top": 100, "width": 320, "height": 220, "asset_url": "texture.png", "photo_score": 12},
            {"left": 102, "top": 98, "width": 318, "height": 224, "asset_url": "photo.jpg", "photo_score": 56},
        ]

        deduped = exact_pdf_layout._dedupe_image_slots_by_bbox(slots)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["asset_url"], "photo.jpg")

    def test_blank_panel_image_scores_below_photo_slot_threshold(self):
        blank = Image.new("RGB", (120, 180), "#f1f7f7")
        buffer = io.BytesIO()
        blank.save(buffer, format="PNG")

        self.assertLess(exact_pdf_layout._image_photo_likelihood_score(buffer.getvalue()), 20)

    def test_photo_slot_trims_light_page_sidebar(self):
        with tempfile.TemporaryDirectory() as tmp:
            background = Image.new("RGB", (500, 300), "#233044")
            draw = ImageDraw.Draw(background)
            draw.rectangle((120, 0, 449, 299), fill="#6c8354")
            draw.rectangle((450, 0, 499, 299), fill="#f5f5f2")
            draw.text((468, 120), "02", fill="#777777")
            path = Path(tmp) / "page.png"
            background.save(path)

            trimmed = exact_pdf_layout._trim_light_sidebar_from_slot(
                path,
                {"left": 120, "top": 0, "width": 380, "height": 300, "photo_score": 80},
            )

            self.assertLess(trimmed["width"], 350)
            self.assertEqual(trimmed["bbox_source"], "light sidebar edge refinement")

    def test_exact_pdf_pages_default_to_editable_layout(self):
        page = {
            "width": 1000,
            "height": 700,
            "image_slots": [{"width": 960, "height": 660}],
        }
        ordinary_page = {
            "width": 1000,
            "height": 700,
            "image_slots": [{"width": 320, "height": 240}],
        }

        self.assertEqual(exact_pdf_layout._default_picture_layout(page), "editable")
        self.assertEqual(exact_pdf_layout._default_picture_layout(ordinary_page), "editable")

    def test_high_specification_body_copy_is_not_an_amenities_heading(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 2,
                    "width": 640,
                    "height": 480,
                    "body": "",
                    "image_slots": [],
                    "text_entries": [
                        {
                            "page_num": 2,
                            "save_id": "exact-page2-text1",
                            "plain": "The building provides contemporary, high specification workspace.",
                            "left": 40,
                            "top": 120,
                            "typography_role": "body",
                            "font_style": {"font_size_px": 14},
                        }
                    ],
                }
            ]
        )

        self.assertEqual(config["amenities_title"]["targets"], [])
        self.assertEqual(config["amenities_title"]["value"], "")

    def test_contact_name_title_line_is_recovered_for_agent_fields(self):
        config = _build_structured_field_config(
            [
                {
                    "page_num": 5,
                    "width": 640,
                    "height": 480,
                    "body": "",
                    "image_slots": [],
                    "text_entries": [
                        {"page_num": 5, "save_id": "exact-page5-text1", "plain": "Will Newton | Senior Surveyor", "left": 28, "top": 354},
                        {"page_num": 5, "save_id": "exact-page5-text2", "plain": "will.newton@bbgreal.com", "left": 28, "top": 369},
                        {"page_num": 5, "save_id": "exact-page5-text3", "plain": "07880 242178", "left": 28, "top": 384},
                        {"page_num": 5, "save_id": "exact-page5-text4", "plain": "Abigail Duckworth | Graduate Surveyor", "left": 28, "top": 418},
                        {"page_num": 5, "save_id": "exact-page5-text5", "plain": "abigail.duckworth@bbgreal.com", "left": 28, "top": 433},
                        {"page_num": 5, "save_id": "exact-page5-text6", "plain": "07886 170 663", "left": 28, "top": 448},
                    ],
                }
            ]
        )

        self.assertEqual(len(config["contacts"]), 2)
        contact = config["contacts"][0]
        self.assertEqual(contact["name"], "Will Newton")
        self.assertEqual(contact["label"], "Will Newton")
        self.assertEqual(contact["targets"], ["exact-page5-text1"])
        self.assertEqual(contact["hide_targets"], ["exact-page5-text2", "exact-page5-text3"])

    def test_amenity_icon_bank_controls_cover_all_twelve_amenities_and_persist(self):
        icon_controls = [
            node
            for node in _all_nodes(self.semantic_root)
            if "data-amenity-icon-slot" in node.attrs
        ]
        self.assertEqual(
            len(icon_controls),
            12,
            "Exact layout should expose one semantic amenity icon control for each of the 12 extracted amenities.",
        )

        icon_ids = [
            value
            for node in _all_nodes(self.semantic_root)
            for key, value in node.attrs.items()
            if key == "data-icon-id" and value
        ]
        self.assertGreaterEqual(
            len(icon_ids),
            12,
            "Amenity icon controls should be tied to the normal icon bank through persisted data-icon-id values.",
        )
        self.assertRegex(
            self.semantic_html,
            r"\bamenityIconFields\b",
            "Serialized state should persist exact amenity icon choices as amenityIconFields.",
        )
        self.assertRegex(
            self.semantic_html,
            r"\b(applyAmenityIconFields|collectAmenityIconFields|syncAmenityIconField)\b",
            "Save/reload should apply amenity icon fields back onto the exact layout.",
        )
        self.assertRegex(
            self.semantic_html,
            r"source-(?:icon|amenity).*hidden|data-(?:hide|source)-amenity-icon|export-clean .*amenity",
            "Amenity icon replacement should include source-hiding/export-clean hooks so PDF vectors do not show behind editable icons.",
        )
        self.assertEqual(
            self.semantic_html.count('data-amenity-icon-field="'),
            12,
            "Each amenity label should expose an explicit icon-bank field hook in the drawer.",
        )
        for field in ('data-global-icon-field="size"', 'data-global-icon-field="color"', 'data-global-icon-field="strokeWidth"'):
            self.assertIn(field, self.semantic_html)
        self.assertIn("globalIconStyle", self.semantic_html)
        self.assertIn("--exact-icon-scale", self.semantic_html)
        self.assertIn("--exact-icon-stroke-width", self.semantic_html)
        self.assertIn("background: transparent;", self.semantic_html)

    def test_map_page_has_semantic_editable_area_and_persisted_map_state(self):
        map_nodes = [
            node
            for node in _all_nodes(self.semantic_root)
            if any(key.startswith("data-map") or key.startswith("data-exact-map") for key in node.attrs)
            or "map-v1" in node.searchable
        ]
        self.assertTrue(
            map_nodes,
            "Exact layout should recover the PDF map as a semantic map area/control, not only vector/text fragments.",
        )
        self.assertRegex(
            self.semantic_html,
            r"data-map-v1|data-exact-map-area|data-map-area",
            "Recovered map area should use the normal map-v1/editable map hooks.",
        )
        self.assertRegex(
            self.semantic_html,
            r"\b(mapFields|mapState|maps)\b",
            "Serialized exact state should include map state for save/reload.",
        )
        self.assertRegex(
            self.semantic_html,
            r"\b(applyMapState|collectMapState|syncMapArea|restoreMapState)\b",
            "Save/reload should restore map edits onto the recovered semantic map area.",
        )
        self.assertIn(
            "data-map-center",
            self.semantic_html,
            "Recovered exact maps should carry center coordinates when they can be inferred, so map-v1 can render without analysis.json.",
        )
        self.assertIn(
            "data-map-content",
            self.semantic_html,
            "Recovered exact maps should carry map-v1 content such as building/station/category data.",
        )
        self.assertIn(
            "data-map-label-field",
            self.semantic_html,
            "Map labels should be editable fields backed by the extracted PDF text nodes.",
        )
        self.assertIn(
            "exactMapRenderPayload",
            self.semantic_html,
            "Map regeneration should send center/content payloads for exact-PDF projects, not rely only on project analysis.",
        )
        self.assertIn(
            "styleTokens",
            self.semantic_html,
            "Map state should persist map-v1 style tokens and artifacts for reload/export.",
        )
        self.assertIn(
            "buildExactLabelMapSvg",
            self.semantic_html,
            "Exact maps should have a generated SVG fallback from recovered PDF labels when map-v1 data is unavailable.",
        )
        self.assertIn(
            "generated-exact-labels",
            self.semantic_html,
            "The map fallback should save a generated source marker instead of leaving only the source PDF map.",
        )

    def test_space_plan_is_detected_as_replaceable_source_image_slot(self):
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:  # pragma: no cover - Pillow is a project dependency.
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "page003-full.png"
            image = Image.new("RGB", (640, 480), "#ffffff")
            draw = ImageDraw.Draw(image)
            yellow = "#ffea00"
            draw.rectangle((312, 58, 448, 418), fill=yellow)
            for x in range(320, 448, 20):
                draw.line((x, 58, x, 418), fill="#ffffff", width=5)
            for y in range(70, 418, 28):
                draw.line((312, y, 448, y), fill="#ffffff", width=5)
            image.save(image_path)

            page = {
                "page_num": 3,
                "width": 640,
                "height": 480,
                "body": "",
                "text_count": 4,
                "image_slots": [],
                "background_path": str(image_path),
                "text_entries": [
                    {"page_num": 3, "save_id": "p3-title", "plain": "4TH FLOOR", "left": 40, "top": 80},
                    {"page_num": 3, "save_id": "p3-area", "plain": "1,801 SQ FT", "left": 40, "top": 120},
                    {"page_num": 3, "save_id": "p3-desks", "plain": "32 Desks", "left": 40, "top": 170},
                    {"page_num": 3, "save_id": "p3-note", "plain": "*Floor plans not to scale.", "left": 40, "top": 420},
                ],
            }
            config = _build_structured_field_config([page])
            html = _render_exact_html(
                project_id="space-plan-regression",
                page_width=640,
                page_height=480,
                pages=[page],
                poppler_css=".ft1{font-size:18px}",
                font_css="",
            )

        self.assertEqual(len(config["space_plans"]), 1)
        self.assertIn('data-source-plan-slot="space-plan-p3-1"', html)
        self.assertIn('data-space-plan-field="space-plan-p3-1"', html)
        self.assertIn('data-save-id="space-plan:space-plan-p3-1"', html)
        self.assertIn("exact-space-plan-slot", html)

    def test_map_region_ignores_body_copy_and_sidebar_lists(self):
        entries = [
            {
                "page_num": 5,
                "save_id": "body-1",
                "plain": "Hammersmith is located approximately 4.5 miles west of Central London, forming the western gateway to the West End.",
                "left": 72,
                "top": 88,
                "width": 330,
                "font_style": {"font_size_px": 17},
            },
            {
                "page_num": 5,
                "save_id": "body-2",
                "plain": "Significant public and private sector investment has transformed the area into a true mixed-use destination.",
                "left": 452,
                "top": 88,
                "width": 330,
                "font_style": {"font_size_px": 17},
            },
            {"page_num": 5, "save_id": "list-h", "plain": "Amenities", "left": 72, "top": 360, "width": 120},
            *[
                {
                    "page_num": 5,
                    "save_id": f"list-{index}",
                    "plain": label,
                    "left": 108,
                    "top": 388 + index * 20,
                    "width": 260,
                    "font_style": {"font_size_px": 15},
                }
                for index, label in enumerate(
                    [
                        "Lyric Theatre",
                        "Livat Hammersmith Shopping Centre",
                        "Eventim Apollo",
                        "Hammersmith Broadway",
                        "King Street",
                        "Premier Inn London Hammersmith",
                        "Fitness First Hammersmith",
                        "F45 Training Kensington Olympia",
                    ],
                    start=1,
                )
            ],
            {"page_num": 5, "save_id": "map-1", "plain": "RAVENSCOURT PARK", "left": 455, "top": 492, "width": 130},
            {"page_num": 5, "save_id": "map-2", "plain": "HAMMERSMITH", "left": 835, "top": 598, "width": 120},
            {"page_num": 5, "save_id": "map-3", "plain": "GREAT WEST RD", "left": 430, "top": 748, "width": 130},
            {"page_num": 5, "save_id": "map-4", "plain": "TALGARTH RD", "left": 1090, "top": 752, "width": 120},
            {"page_num": 5, "save_id": "map-5", "plain": "KENSINGTON OLYMPIA", "left": 1240, "top": 455, "width": 150},
            {"page_num": 5, "save_id": "map-6", "plain": "255 HAMMERSMITH ROAD", "left": 890, "top": 510, "width": 220},
        ]
        page = {"page_num": 5, "width": 1700, "height": 1171, "text_entries": entries}

        region = exact_pdf_layout._map_region_from_text_page(page)

        self.assertTrue(region)
        self.assertGreaterEqual(region["left"], 380)
        self.assertGreaterEqual(region["top"], 440)
        self.assertLess(region["width"], 1050)
        self.assertLess(region["height"], 620)
        label_text = " ".join(label["text"] for label in region["labels"])
        self.assertIn("HAMMERSMITH", label_text)
        self.assertNotIn("Lyric Theatre", label_text)
        self.assertNotIn("Hammersmith Broadway", label_text)

    def test_false_map_image_region_is_rejected_on_table_like_pages(self):
        page = {
            "page_num": 13,
            "width": 1700,
            "height": 1171,
            "text_entries": [
                {"page_num": 13, "save_id": "h", "plain": "INVESTMENT SUMMARY", "left": 90, "top": 80},
                {"page_num": 13, "save_id": "p", "plain": "Proposal", "left": 90, "top": 160},
                {"page_num": 13, "save_id": "s", "plain": "Schedule", "left": 820, "top": 170},
                {"page_num": 13, "save_id": "n", "plain": "Status Notes", "left": 1210, "top": 170},
                {"page_num": 13, "save_id": "r", "plain": "Hammersmith Road", "left": 92, "top": 340},
            ],
        }
        giant_region = {"left": 0, "top": 0, "width": 1700, "height": 860}

        self.assertTrue(
            exact_pdf_layout._map_image_region_false_positive(giant_region, page),
            "A table/report page must not become a giant map hover or regenerate region just because it contains place words.",
        )

    def test_map_image_region_with_paragraph_text_overlap_is_rejected(self):
        page = {
            "page_num": 12,
            "width": 1700,
            "height": 1171,
            "inventory_page_purpose": "locationintroduction",
            "inventory_map_expected": True,
            "text_entries": [
                {"page_num": 12, "save_id": "hotel", "plain": "HOTEL", "left": 110, "top": 420},
                {
                    "page_num": 12,
                    "save_id": "copy",
                    "plain": "London's hotel market has demonstrated continued resilience, with occupancy rates exceeding 80%.",
                    "left": 110,
                    "top": 460,
                    "width": 420,
                },
            ],
            "image_regions": [
                {
                    "id": "false-map-photo-panel",
                    "role": "map",
                    "type": "map",
                    "bbox": {"left": 0, "top": 300, "width": 850, "height": 486},
                    "source_evidence": {
                        "source": "/api/projects/demo/exact_assets/page012-full.png",
                        "text_overlap": {
                            "entries_inside": 35,
                            "samples": [
                                "HOTEL",
                                "London's hotel market has demonstrated",
                                "continued resilience, with occupancy rates",
                                "driven by constrained supply.",
                            ],
                        },
                    },
                }
            ],
        }

        config = _build_structured_field_config([page])

        self.assertEqual(config["map_regions"], [])

    def test_model_floor_labels_create_multiple_space_plan_slots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            assets_dir = Path(temp_dir) / "project-id" / "exact_assets"
            assets_dir.mkdir(parents=True)
            background_path = assets_dir / "page011-full.png"
            Image.new("RGB", (1700, 1171), "#ffffff").save(background_path)

            page = {
                "page_num": 11,
                "width": 1700,
                "height": 1171,
                "background_path": str(background_path),
                "text_entries": [],
                "model_text_spans": [
                    {"plain": "PROPOSED GROUND FLOOR", "left": 505, "top": 622, "width": 240},
                    {"plain": "PROPOSED FIRST FLOOR", "left": 1420, "top": 622, "width": 230},
                    {"plain": "PROPOSED SECOND FLOOR", "left": 565, "top": 1080, "width": 245},
                    {"plain": "PROPOSED THIRD FLOOR", "left": 1425, "top": 1080, "width": 230},
                ],
            }

            slots = exact_pdf_layout._build_space_plan_defs([page])

        self.assertEqual(len(slots), 4)
        self.assertEqual({slot["fit"] for slot in slots}, {"contain"})
        self.assertEqual({slot["page"] for slot in slots}, {"11"})
        self.assertEqual([slot["key"] for slot in slots], [f"space-plan-p11-{index}" for index in range(1, 5)])

    def test_plan_number_schedule_labels_do_not_create_space_plan_slots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            assets_dir = Path(temp_dir) / "project-id" / "exact_assets"
            assets_dir.mkdir(parents=True)
            background_path = assets_dir / "page032-full.png"
            Image.new("RGB", (816, 1056), "#ffffff").save(background_path)

            page = {
                "page_num": 32,
                "width": 816,
                "height": 1056,
                "background_path": str(background_path),
                "text_entries": [
                    {"plain": "DRAFT DECISION LETTER", "left": 315, "top": 112, "width": 180},
                    {"plain": "Plan Nos:", "left": 103, "top": 318, "width": 80},
                ],
                "model_text_spans": [
                    {
                        "plain": "SECOND FLOOR PLAN; 4468-DLG-ZZ-03-DR-A-EX_1004 A-EXISTING THIRD",
                        "left": 214,
                        "top": 396,
                        "width": 395,
                    },
                    {
                        "plain": "PLAN; 4468-DLG-ZZ-05-DR-A-EX_1006 B-EXISTING ROOF PLAN; 4468-DLG-ZZ-",
                        "left": 214,
                        "top": 428,
                        "width": 408,
                    },
                    {
                        "plain": "4468-DLG-ZZ-00-DR-A-PL_1100 D-PROPOSED LOWER GROUND FLOOR PLAN;",
                        "left": 214,
                        "top": 512,
                        "width": 402,
                    },
                ],
            }

            slots = exact_pdf_layout._build_space_plan_defs([page])

        self.assertEqual(slots, [])
        self.assertFalse(exact_pdf_layout._has_space_plan_context(page))
        self.assertFalse(
            exact_pdf_layout._looks_like_floor_plan_label(
                "4468-DLG-ZZ-00-DR-A-PL_1100 D-PROPOSED LOWER GROUND FLOOR PLAN;"
            )
        )

    def test_availability_schedule_does_not_create_space_plan_context(self):
        page = {
            "page_num": 2,
            "width": 1587,
            "height": 1122,
            "text_entries": [
                {"plain": "FLOOR", "left": 860, "top": 571},
                {"plain": "SQ FT", "left": 1017, "top": 571},
                {"plain": "SQ M", "left": 1172, "top": 571},
                {"plain": "STATUS", "left": 1296, "top": 571},
                {"plain": "CAT B", "left": 1340, "top": 620},
                {"plain": "The 4th floor open plan Plug and Play space has been refurbished.", "left": 860, "top": 337},
            ],
        }

        self.assertFalse(exact_pdf_layout._has_space_plan_context(page))
        self.assertTrue(exact_pdf_layout._looks_like_availability_schedule_without_space_plan(page))
        self.assertFalse(exact_pdf_layout._looks_like_floor_plan_label("The 4th floor open plan Plug and Play space has been refurbished."))
        self.assertFalse(exact_pdf_layout._looks_like_floor_plan_label("The proposed extensions would provide additional office floorspace."))
        self.assertTrue(exact_pdf_layout._looks_like_floor_plan_label("4th floor meeting room space"))
        self.assertTrue(exact_pdf_layout._looks_like_floor_plan_label("Proposed Front Elevation (Bessborough Gardens)"))
        self.assertTrue(exact_pdf_layout._looks_like_floor_plan_label("Proposed Section showing Fifth Floor Pavilion Level Extension"))

    def test_planning_portal_table_becomes_replaceable_table_image_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            assets_dir = Path(temp_dir) / "project-id" / "exact_assets"
            assets_dir.mkdir(parents=True)
            background_path = assets_dir / "page010-full.png"
            Image.new("RGB", (1700, 1171), "#ffffff").save(background_path)
            page = {
                "page_num": 10,
                "width": 1700,
                "height": 1171,
                "background_path": str(background_path),
                "text_entries": [
                    {"page_num": 10, "save_id": "floor", "plain": "Floor", "left": 1140, "top": 410, "width": 82},
                    {"page_num": 10, "save_id": "units", "plain": "Units", "left": 1325, "top": 410, "width": 82},
                    {
                        "page_num": 10,
                        "save_id": "portal",
                        "plain": "CLICK FOR ACCESS TO THE PLANNING PORTAL",
                        "left": 1015,
                        "top": 1002,
                        "width": 410,
                    },
                    {"page_num": 10, "save_id": "copy", "plain": "The proposed development provides flexible office accommodation.", "left": 110, "top": 220},
                ],
            }

            slots = exact_pdf_layout._build_table_image_defs([page])

        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["key"], "table-image-p10-1")
        self.assertEqual(slots[0]["page"], "10")
        self.assertEqual(slots[0]["fit"], "contain")
        self.assertGreater(slots[0]["left"], 880)
        self.assertTrue(slots[0]["asset_url"].endswith("page010-table-image-01.png"))

    def test_service_customisation_icons_are_source_level_icon_bank_slots(self):
        labels = [
            ("Daily Cleaning & Waste Management", 841, 375),
            ("Maintenance & Repairs", 1067, 375),
            ("Health & Safety Compliance", 1293, 375),
            ("Broadband", 841, 567),
            ("Demise Electricity", 1067, 567),
            ("Business Rates", 1293, 567),
            ("Tea & Coffee", 841, 951),
            ("Healthy Snacks", 1067, 951),
            ("Foliage Rental & Care", 1293, 951),
        ]
        page = {
            "page_num": 5,
            "width": 1587,
            "height": 1122,
            "body": "",
            "text_count": len(labels),
            "image_slots": [],
            "text_entries": [
                {"page_num": 5, "save_id": f"p5-service-{index}", "plain": label, "left": left, "top": top}
                for index, (label, left, top) in enumerate(labels, start=1)
            ],
        }
        config = _build_structured_field_config([page])
        html = _render_exact_html(
            project_id="service-icons-regression",
            page_width=1587,
            page_height=1122,
            pages=[page],
            poppler_css=".ft1{font-size:18px}",
            font_css="",
        )

        self.assertEqual(len(config["service_icons"]), 9)
        self.assertIn('data-field-section="service-icons"', html)
        self.assertEqual(html.count('data-service-icon-slot="'), 9)
        self.assertIn('data-service-icon-field="dailycleaningwastemanagement"', html)
        self.assertIn('data-service-icon-field="foliagerentalcare"', html)

    def test_agent_contacts_and_agency_logos_are_round_trip_friendly(self):
        contact_keys = {
            key
            for key in re.findall(r'data-contact-field="([^"]+)"', self.semantic_html)
            if "' +" not in key and key.strip()
        }
        self.assertGreaterEqual(
            len(contact_keys),
            5,
            "Detected agent/contact blocks should remain exposed in the Fields drawer.",
        )

        self.assertIn("fields['contact:' + input.dataset.contactField]", self.semantic_html)
        self.assertIn("key.indexOf('contact:') === 0", self.semantic_html)
        self.assertIn("applyStructuredFields(state.structuredFields)", self.semantic_html)
        self.assertIn("agencyLogos: collectAgencyLogos()", self.semantic_html)
        self.assertIn("applyAgencyLogoState(state.agencyLogos)", self.semantic_html)

        logo_keys = {
            key
            for key in re.findall(r'data-logo-field="([^"]+)"', self.semantic_html)
            if "' +" not in key and key.strip()
        }
        self.assertGreaterEqual(
            len(logo_keys),
            3,
            "Detected agency logo groups should expose editable controls.",
        )
        for logo_key in sorted(logo_keys)[:3]:
            self.assertIn(
                f'data-logo-field="{logo_key}"',
                self.semantic_html,
                f"Detected agency logo {logo_key} should expose editable text/replacement controls.",
            )
            self.assertIn(
                f'data-logo-upload="{logo_key}"',
                self.semantic_html,
                f"Detected agency logo {logo_key} should expose upload replacement.",
            )
            self.assertIn(
                f'data-logo-clear="{logo_key}"',
                self.semantic_html,
                f"Detected agency logo {logo_key} should expose reset/clear.",
            )
        self.assertIn(
            "data-agency-logo-slot-upload",
            self.semantic_html,
            "Agency logos should also be directly replaceable from the logo area on the page.",
        )

    def test_contenteditable_text_forces_added_words_to_inherit_pdf_font(self):
        self.assertIn('.pdf-text[contenteditable="true"] *', self.html)
        self.assertIn(".pdf-text *", self.html)
        self.assertNotIn(
            '.pdf-text[contenteditable="true"],\n.pdf-text[contenteditable="true"] *',
            self.html,
            "Editable paragraphs must keep their extracted PDF font classes; only inserted child nodes inherit.",
        )
        self.assertIn("normaliseEditedTextMarkup(el)", self.html)
        self.assertIn("insertPlainTextAtSelection(text)", self.html)
        self.assertIn("collectTextTypography(el)", self.html)

    def test_extract_fonts_writes_raw_and_stable_font_faces(self):
        class FakePage:
            def get_fonts(self, full=True):
                return [(7, None, None, "GUBGIQ+DalaMoa-Thin")]

        class FakeDoc:
            def __iter__(self):
                return iter([FakePage()])

            def extract_font(self, xref):
                return ("GUBGIQ+DalaMoa-Thin", "otf", "Type1", b"font-bytes")

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            fonts_dir = Path(temp_dir) / "fonts"
            with mock.patch.object(exact_pdf_layout.fitz, "open", return_value=FakeDoc()):
                css = exact_pdf_layout._extract_fonts(Path("source.pdf"), fonts_dir, "font-project")

            self.assertTrue((fonts_dir / "GUBGIQ+DalaMoa-Thin.otf").exists())
            self.assertIn("font-family: 'GUBGIQ+DalaMoa-Thin'", css)
            self.assertIn("font-family: 'DalaMoa-Thin'", css)
            self.assertIn("/api/projects/font-project/exact_assets/fonts/GUBGIQ+DalaMoa-Thin.otf", css)

    def test_extract_fonts_from_austin_pdf_exposes_real_stable_aliases(self):
        pdf_path = Path("/Users/fergusfindlay/Downloads/13 Austin Friars Brochure-Oct 2025.pdf")
        if not pdf_path.exists():
            self.skipTest("Austin source brochure PDF is not available on this machine.")

        with tempfile.TemporaryDirectory() as temp_dir:
            fonts_dir = Path(temp_dir) / "fonts"
            css = exact_pdf_layout._extract_fonts(pdf_path, fonts_dir, "austin-font-test")

            self.assertIn("@font-face", css)
            self.assertIn("font-family: 'DalaMoa-Thin'", css)
            self.assertIn("font-family: 'AreaNormal-Regular'", css)
            self.assertTrue(any(fonts_dir.glob("*DalaMoa-Thin.*")))
            self.assertTrue(any(fonts_dir.glob("*AreaNormal-Regular.*")))

    def test_poppler_font_css_uses_stable_alias_and_records_typography(self):
        css, colour_roles, font_styles = exact_pdf_layout._extract_poppler_css(
            "<style>.ft10{font-size:128px;font-family:'GUBGIQ+DalaMoa-Thin';color:#ffffff;}</style>"
        )

        self.assertIn("'DalaMoa-Thin', 'GUBGIQ+DalaMoa-Thin', Arial, sans-serif", css)
        self.assertEqual(colour_roles["ft10"], "light")
        self.assertEqual(font_styles["ft10"]["source_font_family"], "GUBGIQ+DalaMoa-Thin")
        self.assertEqual(font_styles["ft10"]["stable_font_family"], "DalaMoa-Thin")
        self.assertEqual(font_styles["ft10"]["font_size"], "128px")

    def test_prepare_page_inner_tags_typography_roles_and_font_aliases(self):
        _css, colour_roles, font_styles = exact_pdf_layout._extract_poppler_css(
            """
            <style>
              .ft1{font-size:128px;font-family:'GUBGIQ+DalaMoa-Thin';color:#ffffff;}
              .ft2{font-size:64px;font-family:'GUBGIQ+DalaMoa-Thin';color:#ffea00;}
              .ft3{font-size:13px;font-family:'GUBGIQ+AreaNormal-Regular';color:#333132;}
              .ft4{font-size:7px;font-family:'GUBGIQ+AreaNormal-Regular';color:#333132;}
              .ft5{font-size:18px;font-family:'GUBGIQ+DalaMoa';color:#ffffff;}
            </style>
            """
        )

        cover, _count, _entries = exact_pdf_layout._prepare_page_inner(
            '<p class="ft1" style="position:absolute;top:10px;left:10px">FRIARS</p>',
            1,
            "typography-test",
            colour_roles,
            "page001-full.png",
            font_styles,
        )
        heading, _count, _entries = exact_pdf_layout._prepare_page_inner(
            '<p class="ft2" style="position:absolute;top:10px;left:10px">AMENITIES</p>',
            4,
            "typography-test",
            colour_roles,
            "page004-full.png",
            font_styles,
        )
        body, _count, _entries = exact_pdf_layout._prepare_page_inner(
            '<p class="ft3" style="position:absolute;top:10px;left:10px">13 Austin Friars is a listed building.</p>',
            2,
            "typography-test",
            colour_roles,
            "page002-full.png",
            font_styles,
        )
        caption, _count, _entries = exact_pdf_layout._prepare_page_inner(
            '<p class="ft4" style="position:absolute;top:10px;left:10px">*Floor plans not to scale.</p>',
            3,
            "typography-test",
            colour_roles,
            "page003-full.png",
            font_styles,
        )
        table, _count, _entries = exact_pdf_layout._prepare_page_inner(
            '<p class="ft5" style="position:absolute;top:10px;left:10px">FLOOR</p>',
            8,
            "typography-test",
            colour_roles,
            "page008-full.png",
            font_styles,
        )
        agent, _count, _entries = exact_pdf_layout._prepare_page_inner(
            '<p class="ft3" style="position:absolute;top:10px;left:10px">Tom Boggis<br/>07795 070 676<br/>tom@example.com</p>',
            8,
            "typography-test",
            colour_roles,
            "page008-full.png",
            font_styles,
        )

        self.assertIn('data-typography-role="cover-title"', cover)
        self.assertIn('data-font-alias="DalaMoa-Thin"', cover)
        self.assertIn('data-source-font-family="GUBGIQ+DalaMoa-Thin"', cover)
        self.assertIn('data-typography-role="section-heading"', heading)
        self.assertIn('data-typography-role="body"', body)
        self.assertIn('data-font-alias="AreaNormal-Regular"', body)
        self.assertIn('data-typography-role="caption"', caption)
        self.assertIn('data-typography-role="table-status"', table)
        self.assertIn('data-typography-role="agent-contact"', agent)

    def test_prepare_page_inner_normalises_noisy_schedule_labels(self):
        _css, colour_roles, font_styles = exact_pdf_layout._extract_poppler_css(
            """
            <style>
              .ft1{font-size:24px;font-family:'GUBGIQ+DalaMoa';color:#ffea00;}
            </style>
            """
        )

        rendered, _count, entries = exact_pdf_layout._prepare_page_inner(
            (
                '<p class="ft1" style="position:absolute;top:85px;left:68px">F&#160;LO&#160;O&#160;R</p>'
                '<p class="ft1" style="position:absolute;top:85px;left:187px">SQ&#160;&#160;&#160;F&#160;T</p>'
                '<p class="ft1" style="position:absolute;top:85px;left:288px">STATU&#160;S&#160;&#160;&#160;Q&#160;UOT&#160;I&#160;NG&#160;&#160;&#160;R&#160;E&#160;NT</p>'
                '<p class="ft1" style="position:absolute;top:303px;left:68px">S&#160;E&#160;RVI&#160;C&#160;E&#160;&#160;&#160;C&#160;HA&#160;RG&#160;E</p>'
            ),
            8,
            "schedule-test",
            colour_roles,
            "page008-full.png",
            font_styles,
        )

        self.assertIn('data-plain-text="FLOOR"', rendered)
        self.assertIn(">FLOOR</p>", rendered)
        self.assertIn('data-plain-text="SQ FT"', rendered)
        self.assertIn('data-plain-text="STATUS QUOTING RENT"', rendered)
        self.assertIn('data-plain-text="SERVICE CHARGE"', rendered)
        self.assertNotIn("F&#160;LO", rendered)
        self.assertEqual([entry["plain"] for entry in entries], ["FLOOR", "SQ FT", "STATUS QUOTING RENT", "SERVICE CHARGE"])

    def test_model_schedule_text_supplements_missing_editable_cells(self):
        heading_style = {
            "stable_font_family": "DalaMoa",
            "source_font_family": "GUBGIQ+DalaMoa",
            "font_size": "24px",
            "font_size_px": 24,
            "line_height": "28px",
            "line_height_px": 28,
            "color": "#ffea00",
        }
        body_style = {
            "stable_font_family": "AreaNormal-Regular",
            "source_font_family": "GUBGIQ+AreaNormal-Regular",
            "font_size": "13px",
            "font_size_px": 13,
            "line_height": "18px",
            "line_height_px": 18,
            "color": "#ffffff",
        }
        page = {
            "page_num": 2,
            "width": 1587,
            "height": 1122,
            "body": (
                '<p class="pdf-text" data-save-id="existing-floor" data-plain-text="FLOOR">FLOOR</p>'
                '<p class="pdf-text" data-save-id="existing-sqft" data-plain-text="SQ FT">SQ FT</p>'
                '<p class="pdf-text" data-save-id="existing-sqm" data-plain-text="SQ M">SQ M</p>'
                '<p class="pdf-text" data-save-id="existing-status" data-plain-text="STATUS">STATUS</p>'
                '<p class="pdf-text" data-save-id="existing-167" data-plain-text="167">167</p>'
                '<p class="pdf-text" data-save-id="existing-catb" data-plain-text="CAT B">CAT B</p>'
            ),
            "text_count": 6,
            "text_entries": [
                {"save_id": "existing-floor", "text_index": 1, "plain": "FLOOR", "left": 860, "top": 571, "width": 60, "font_style": heading_style},
                {"save_id": "existing-sqft", "text_index": 2, "plain": "SQ FT", "left": 1017, "top": 571, "width": 51, "font_style": heading_style},
                {"save_id": "existing-sqm", "text_index": 3, "plain": "SQ M", "left": 1172, "top": 571, "width": 44, "font_style": heading_style},
                {"save_id": "existing-status", "text_index": 4, "plain": "STATUS", "left": 1296, "top": 571, "width": 66, "font_style": heading_style},
                {"save_id": "existing-167", "text_index": 5, "plain": "167", "left": 1207, "top": 620, "width": 18, "font_style": body_style},
                {"save_id": "existing-catb", "text_index": 6, "plain": "CAT B", "left": 1340, "top": 620, "width": 34, "font_style": body_style},
            ],
            "model_text_spans": [
                {"model_id": "p002-text-0011", "plain": "FLOOR", "left": 860, "top": 571, "width": 60, "height": 27, "font_style": heading_style, "colour_role": "accent"},
                {"model_id": "p002-text-0012", "plain": "SQ FT", "left": 1017, "top": 571, "width": 51, "height": 27, "font_style": heading_style, "colour_role": "accent"},
                {"model_id": "p002-text-0013", "plain": "SQ M", "left": 1172, "top": 571, "width": 44, "height": 27, "font_style": heading_style, "colour_role": "accent"},
                {"model_id": "p002-text-0014", "plain": "STATUS", "left": 1296, "top": 571, "width": 66, "height": 27, "font_style": heading_style, "colour_role": "accent"},
                {"model_id": "p002-text-0015", "plain": "4", "left": 860, "top": 616, "width": 7, "height": 20, "font_style": body_style, "colour_role": "light"},
                {"model_id": "p002-text-0016", "plain": "1,801", "left": 1051, "top": 617, "width": 34, "height": 20, "font_style": body_style, "colour_role": "light"},
                {"model_id": "p002-text-0017", "plain": "167", "left": 1207, "top": 617, "width": 24, "height": 20, "font_style": body_style, "colour_role": "light"},
                {"model_id": "p002-text-0018", "plain": "CAT B", "left": 1340, "top": 617, "width": 43, "height": 20, "font_style": body_style, "colour_role": "light"},
                {"model_id": "p002-text-0019", "plain": "1", "left": 860, "top": 662, "width": 5, "height": 20, "font_style": body_style, "colour_role": "light"},
                {"model_id": "p002-text-0024", "plain": "1,872", "left": 1050, "top": 663, "width": 35, "height": 20, "font_style": body_style, "colour_role": "light"},
                {"model_id": "p002-text-0026", "plain": "LET", "left": 1333, "top": 667, "width": 25, "height": 16, "font_style": body_style, "colour_role": "light"},
            ],
        }

        exact_pdf_layout._supplement_model_schedule_text([page])

        self.assertIn('data-model-text-source="p002-text-0015"', page["body"])
        self.assertIn('data-plain-text="4"', page["body"])
        self.assertIn('data-plain-text="1,801"', page["body"])
        self.assertIn('data-plain-text="1"', page["body"])
        self.assertIn('data-plain-text="1,872"', page["body"])
        self.assertIn('data-model-text-source="p002-text-0026"', page["body"])
        self.assertNotIn('data-model-text-source="p002-text-0011"', page["body"])
        self.assertNotIn('data-model-text-source="p002-text-0017"', page["body"])
        self.assertEqual(page["text_count"], 11)

    def test_typography_controls_are_global_editable_settings(self):
        self.assertIn('data-field-section="typography"', self.html)
        self.assertIn('data-typography-role-field="cover-title"', self.html)
        self.assertIn('data-typography-role-field="section-heading"', self.html)
        self.assertIn('data-typography-role-field="body"', self.html)
        self.assertIn("window.__EXACT_TYPOGRAPHY__", self.html)
        self.assertIn("applyTypographySettings(state.typography)", self.html)

    def test_editor_skips_stale_unedited_saved_text(self):
        self.assertIn("function shouldApplySavedTextState", self.html)
        self.assertIn("item.edited === true", self.html)
        self.assertIn("item.edited !== false", self.html)
        self.assertIn("data-plain-text", self.html)
        self.assertIn("plainTextFromHtml", self.html)

    def test_body_caption_and_contact_text_keep_extracted_pdf_line_height(self):
        html = _render_exact_html(
            project_id="line-height-regression",
            page_width=640,
            page_height=480,
            pages=[
                {
                    "page_num": 1,
                    "width": 640,
                    "height": 480,
                    "body": (
                        '<p class="ft1 pdf-text" style="position:absolute;top:32px;left:42px" '
                        'contenteditable="true" data-save-id="copy" data-typography-role="body" '
                        'data-original-html="One&lt;br/&gt;Two">One<br/>Two</p>'
                    ),
                    "text_count": 1,
                    "image_slots": [],
                    "text_entries": [],
                }
            ],
            poppler_css=".ft1{font-size:13px;line-height:21px;font-family:'AreaNormal-Regular';color:#ffffff;}",
            font_css="",
        )

        self.assertIn(".ft1{font-size:13px;line-height:21px", html)
        self.assertNotIn('data-typography-role="body"] {\n  line-height: 0.8;', html)
        self.assertNotIn('data-typography-role="caption"] {\n  line-height: 0.8;', html)
        self.assertNotIn('data-typography-role="agent-contact"] {\n  line-height: 0.8;', html)

    def test_export_clean_state_hides_exact_editing_surfaces(self):
        for selector in (
            "body.export-clean .exact-toolbar",
            "body.export-clean .exact-fields-panel",
            "body.export-clean .slot-chip",
            "body.export-clean .slot-controls",
            "body.export-clean .exact-image-slot input",
        ):
            self.assertIn(selector, self.semantic_html)

        self.assertRegex(
            self.semantic_html,
            r"export-clean.*(?:global-logo|amenity|map|brand-logo|icon-picker)|(?:global-logo|amenity|map|brand-logo|icon-picker).*export-clean",
            "Clean/export mode should hide semantic logo, amenity icon, map, and agency-logo editing controls too.",
        )

    def test_extracted_image_slots_render_real_img_fallback(self):
        rendered = exact_pdf_layout._render_image_slots(
            6,
            [
                {
                    "left": 10,
                    "top": 20,
                    "width": 300,
                    "height": 180,
                    "mask_colour": "#282827",
                    "asset_url": "/api/projects/demo/exact_assets/images/photo.jpg",
                }
            ],
        )

        self.assertIn('background-image:url(&quot;/api/projects/demo/exact_assets/images/photo.jpg&quot;)', rendered)
        self.assertIn('<img class="slot-photo-img" src="/api/projects/demo/exact_assets/images/photo.jpg" alt="">', rendered)
        self.assertIn(".slot-photo-img", self.semantic_html)
        self.assertIn("imageValueToUrl", self.semantic_html)

    def test_extracted_images_render_above_vector_overlay_but_below_text(self):
        self.assertIn(".pdf-vector-overlay-layer {\n  position: absolute;", self.semantic_html)
        self.assertIn(".pdf-vector-overlay-layer {\n  position: absolute;\n  inset: 0;\n  width: 100%;\n  height: 100%;\n  z-index: 2;", self.semantic_html)
        self.assertIn(".exact-image-slot {\n  position: absolute;\n  z-index: 3;", self.semantic_html)
        self.assertIn(".pdf-text {\n  z-index: 4;", self.semantic_html)

    def test_vector_layer_splits_page_background_from_photo_overlays(self):
        class FakeRect:
            width = 100
            height = 100

        class FakePage:
            rect = FakeRect()

        paths = [
            {
                "id": "background",
                "d": "M0 0L100 0L100 100L0 100Z",
                "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
                "fill": "#ffffff",
            },
            {
                "id": "divider",
                "d": "M20 0L20 100",
                "bbox": {"x": 20, "y": 0, "width": 1, "height": 100},
                "stroke": "#f1eff3",
                "stroke_width": 1,
            },
        ]

        with mock.patch.object(exact_pdf_layout, "extract_page_svg_paths", return_value=paths):
            html_output = exact_pdf_layout._render_vector_layer(FakePage(), 100, 100)

        self.assertIn('class="pdf-vector-layer"', html_output)
        self.assertIn("pdf-vector-page-background", html_output)
        self.assertIn('class="pdf-vector-overlay-layer"', html_output)
        self.assertIn("divider", html_output)

    def test_image_tone_filter_detects_pdf_darkening(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            background_path = Path(temp_dir) / "background.png"
            source_bytes = io.BytesIO()
            Image.new("RGB", (80, 80), (200, 200, 200)).save(source_bytes, format="PNG")
            Image.new("RGB", (80, 80), (90, 90, 90)).save(background_path)

            tone = exact_pdf_layout._image_tone_filter(
                background_path,
                {"left": 0, "top": 0, "width": 80, "height": 80},
                source_bytes.getvalue(),
            )

        self.assertIn("filter:brightness", tone)

    def test_image_tone_filter_does_not_overbrighten_sampled_pdf_darkening(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            background_path = Path(temp_dir) / "background.png"
            source_bytes = io.BytesIO()
            Image.new("RGB", (80, 80), (200, 200, 200)).save(source_bytes, format="PNG")
            Image.new("RGB", (80, 80), (120, 120, 120)).save(background_path)

            tone = exact_pdf_layout._image_tone_filter(
                background_path,
                {"left": 0, "top": 0, "width": 80, "height": 80},
                source_bytes.getvalue(),
            )

        self.assertIn("brightness(0.600)", tone)

    def test_image_tone_filter_detects_pdf_contrast_scrim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            background_path = Path(temp_dir) / "background.png"
            source = Image.new("RGB", (100, 80), (0, 0, 0))
            background = Image.new("RGB", (100, 80), (0, 0, 0))
            for x in range(100):
                source_colour = int(20 + (220 * x / 99))
                background_colour = int(80 + (90 * x / 99))
                for y in range(80):
                    source.putpixel((x, y), (source_colour, source_colour, source_colour))
                    background.putpixel((x, y), (background_colour, background_colour, background_colour))
            source_bytes = io.BytesIO()
            source.save(source_bytes, format="PNG")
            background.save(background_path)

            tone = exact_pdf_layout._image_tone_filter(
                background_path,
                {"left": 0, "top": 0, "width": 100, "height": 80},
                source_bytes.getvalue(),
            )

        self.assertIn("contrast", tone)

    def test_image_tone_filter_ignores_matching_source_tone(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            background_path = Path(temp_dir) / "background.png"
            source_bytes = io.BytesIO()
            Image.new("RGB", (80, 80), (180, 180, 180)).save(source_bytes, format="PNG")
            Image.new("RGB", (80, 80), (176, 176, 176)).save(background_path)

            tone = exact_pdf_layout._image_tone_filter(
                background_path,
                {"left": 0, "top": 0, "width": 80, "height": 80},
                source_bytes.getvalue(),
            )

        self.assertEqual(tone, "")

    def test_source_logo_overlay_keeps_default_embedded_mark_until_replaced(self):
        rendered = exact_pdf_layout._render_semantic_overlays(
            {"page_num": 1, "width": 100, "height": 100},
            {
                "source_logos": [
                    {
                        "page": "1",
                        "key": "facade-p1-1",
                        "kind": "facade",
                        "label": "Header mark",
                        "left": 10,
                        "top": 12,
                        "width": 40,
                        "height": 30,
                        "mask_colour": "#f2f2f2",
                        "mask_mode": "photo",
                        "default_asset_url": "/api/projects/demo/exact_assets/images/source.png",
                    }
                ]
            },
        )

        self.assertIn("has-default-source-logo", rendered)
        self.assertIn("exact-source-logo-default", rendered)
        self.assertIn('data-default-source-logo-asset="/api/projects/demo/exact_assets/images/source.png"', rendered)

    def test_source_logo_default_image_mark_matches_detected_component(self):
        mark = exact_pdf_layout._best_source_image_mark(
            {"left": 100, "top": 50, "width": 40, "height": 30},
            [
                {"left": 400, "top": 400, "width": 20, "height": 20, "asset_url": "wrong.png"},
                {"left": 104, "top": 54, "width": 32, "height": 22, "asset_url": "right.png"},
            ],
        )

        self.assertEqual(mark["asset_url"], "right.png")


def _render_fixture_html() -> str:
    _ensure_semantic_overlay_test_shim()
    page_body = """
<p class="ft1 pdf-text" style="position:absolute;top:32px;left:42px" contenteditable="true"
   data-save-id="exact-page1-text1" data-original-html="Austin Friars">Austin Friars</p>
<p class="ft2 pdf-text" style="position:absolute;top:92px;left:42px" contenteditable="true"
   data-save-id="exact-page1-text2" data-original-html="AMENITIES">AMENITIES</p>
<p class="ft3 pdf-text" style="position:absolute;top:128px;left:42px" contenteditable="true"
   data-save-id="exact-page1-text3" data-original-html="Shower facilities&lt;br/&gt;Bike storage">
   Shower facilities<br/>Bike storage</p>
<p class="ft4 pdf-text" style="position:absolute;top:320px;left:42px" contenteditable="true"
   data-save-id="exact-page1-text4" data-original-html="Tom Boggis&lt;br/&gt;07795 070 676&lt;br/&gt;tom.boggis@example.com">
   Tom Boggis<br/>07795 070 676<br/>tom.boggis@example.com</p>
<div class="exact-image-slot has-image" data-save-id="exact-page1-image1" data-fit="cover"
     data-original-left="200" data-original-top="120" data-original-width="240" data-original-height="160">
  <div class="slot-photo"></div>
  <input type="file" accept="image/*" aria-label="Replace image"/>
</div>
"""
    return _render_exact_html(
        project_id="field-regression",
        page_width=640,
        page_height=480,
        pages=[
            {
                "page_num": 1,
                "width": 640,
                "height": 480,
                "body": page_body,
                "text_count": 4,
                "image_slots": [],
                "text_entries": [
                    {"page_num": 1, "save_id": "exact-page1-text1", "plain": "Austin Friars", "left": 42, "top": 32},
                    {"page_num": 1, "save_id": "exact-page1-text2", "plain": "AMENITIES", "left": 42, "top": 92},
                    {"page_num": 1, "save_id": "exact-page1-text3", "plain": "Shower facilities\nBike storage", "left": 42, "top": 128},
                    {"page_num": 1, "save_id": "exact-page1-text4", "plain": "Tom Boggis\n07795 070 676\ntom.boggis@example.com", "left": 42, "top": 320},
                ],
            },
            {
                "page_num": 4,
                "width": 640,
                "height": 480,
                "body": "",
                "text_count": 12,
                "image_slots": [],
                "text_entries": [
                    {"page_num": 4, "save_id": f"exact-page4-text{index}", "plain": text, "left": left, "top": top}
                    for index, text, left, top in [
                        (1, "Newly Refurbished to Contemporary Style with Exposed Services", 47, 180),
                        (2, "CAT B, Plug & Play", 273, 180),
                        (3, "In-House Gym", 500, 180),
                        (4, "8 Person Passenger Lift", 47, 300),
                        (5, "Showers", 273, 300),
                        (6, "Bike Racks", 500, 300),
                        (7, "New Kitchenette", 47, 420),
                        (8, "Air Conditioned", 273, 420),
                        (9, "Perimeter Trunking", 500, 420),
                        (10, "Victorian Façade", 47, 540),
                        (11, "Bookable Meeting Rooms", 273, 540),
                        (12, "Fibre Installed", 500, 540),
                    ]
                ],
            },
            {
                "page_num": 7,
                "width": 640,
                "height": 480,
                "body": "",
                "text_count": 3,
                "image_slots": [],
                "text_entries": [
                    {"page_num": 7, "save_id": "exact-page7-text1", "plain": "MOORGATE", "left": 120, "top": 160},
                    {"page_num": 7, "save_id": "exact-page7-text2", "plain": "LIVERPOOL ST", "left": 390, "top": 180},
                    {"page_num": 7, "save_id": "exact-page7-text3", "plain": "BANK", "left": 120, "top": 390},
                ],
            },
        ],
        poppler_css=".ft1{font-size:28px}.ft2{font-size:18px}.ft3{font-size:14px}.ft4{font-size:14px}",
        font_css="",
    )


def _render_semantic_fixture_html() -> str:
    _ensure_semantic_overlay_test_shim()
    pages = [
        _fixture_page(
            1,
            [
                ("13", 42, 30),
                ("AUSTIN", 42, 72),
                ("FRIARS", 42, 114),
                ("EC2", 42, 156),
                ("Newly refurbished plug & play office suites to let", 42, 220),
            ],
        ),
        _fixture_page(
            4,
            [
                ("AMENITIES", 42, 58),
                ("Newly Refurbished to Contemporary Style with Exposed Services", 42, 112),
                ("CAT B, Plug & Play", 42, 150),
                ("Condition", 42, 172),
                ("In-House Gym", 42, 206),
                ("8 Person Passenger Lift", 42, 238),
                ("Showers", 42, 270),
                ("Bike Racks", 42, 302),
                ("New Kitchenette", 42, 334),
                ("Air Conditioned", 42, 366),
                ("Perimeter Trunking", 42, 398),
                ("Victorian Façade", 42, 430),
                ("Bookable Meeting Rooms", 42, 462),
                ("Fibre Installed", 42, 494),
            ],
        ),
        _fixture_page(
            6,
            [
                ("LOCATION", 42, 58),
                ("Liverpool Street", 80, 112),
                ("Bank", 240, 220),
                ("Moorgate", 360, 160),
            ],
            extra_body=(
                '<svg class="pdf-vector-layer" viewBox="0 0 640 480">'
                '<path class="pdf-vector-shape" data-vector-id="map-road-1" '
                'data-bbox="70,90,470,260" d="M70 90L540 350" style="fill:none;stroke:#d8d8d8;"/>'
                '</svg>'
            ),
        ),
        _fixture_page(
            8,
            [
                ("Tom Boggis 07795 070 676 tom.boggis@bbgreal.com", 68, 300),
                ("Abigail", 68, 350),
                ("Duckworth", 68, 372),
                ("07886 170 66", 68, 394),
                ("3", 168, 394),
                ("abigail.duckworth", 68, 416),
                ("@bbgreal.com", 218, 416),
                ("Tim Williams 07717 576 894 tim.williams@realestate.bnpparibas", 240, 300),
                ("Ben Rainbow 07909 487 189 ben.rainbow@realestate.bnpparibas", 240, 350),
                ("Poppy Barker 07878 859 429 poppy@kittoffices.com", 430, 300),
            ],
        ),
    ]
    return _render_exact_html(
        project_id="semantic-field-regression",
        page_width=640,
        page_height=480,
        pages=pages,
        poppler_css=".ft1{font-size:18px}.ft2{font-size:14px}.ft3{font-size:12px}",
        font_css="",
    )


def _fixture_page(
    page_num: int,
    texts: list[tuple[str, int, int]],
    *,
    extra_body: str = "",
) -> dict[str, object]:
    text_entries = []
    body_parts = [extra_body]
    for index, (plain, left, top) in enumerate(texts, start=1):
        save_id = f"exact-page{page_num}-text{index}"
        text_entries.append(
            {
                "page_num": page_num,
                "save_id": save_id,
                "plain": plain,
                "left": left,
                "top": top,
                "width": 220,
                "height": 20,
            }
        )
        body_parts.append(
            f'<p class="ft{min(index, 3)} pdf-text" style="position:absolute;top:{top}px;left:{left}px" '
            f'contenteditable="true" data-save-id="{save_id}" '
            f'data-original-html="{html.escape(plain, quote=True)}">{html.escape(plain)}</p>'
        )
    return {
        "page_num": page_num,
        "width": 640,
        "height": 480,
        "body": "\n".join(body_parts),
        "text_count": len(texts),
        "text_entries": text_entries,
        "image_slots": [],
    }


def _all_nodes(node: HtmlNode) -> list[HtmlNode]:
    nodes = [node]
    for child in node.children:
        nodes.extend(_all_nodes(child))
    return nodes


def _find_fields_drawer(root: HtmlNode) -> HtmlNode | None:
    candidates = []
    for node in _all_nodes(root):
        blob = node.searchable
        has_field_identity = any(
            marker in blob
            for marker in (
                "data-fields-drawer",
                "data-exact-fields-drawer",
                "exact-fields-drawer",
                "fields-drawer",
                "fieldsdrawer",
            )
        )
        field_panel = "field" in blob and any(marker in blob for marker in ("drawer", "panel", "sidebar"))
        if node.tag in {"aside", "section", "form", "div"} and (has_field_identity or field_panel):
            candidates.append(node)
    return candidates[0] if candidates else None


def _find_fields_toggle(root: HtmlNode) -> HtmlNode | None:
    for node in _all_nodes(root):
        if node.tag not in {"button", "a", "input"}:
            continue
        blob = node.searchable
        if "field" in blob and any(marker in blob for marker in ("toggle", "drawer", "panel", "fields", "aria-controls")):
            return node
    return None


def _control_keys(node: HtmlNode) -> list[str]:
    controls = []
    for child in _all_nodes(node):
        if child.tag not in {"input", "select", "textarea", "button", "label"}:
            continue
        pieces = [child.tag, child.text]
        for attr in (
            "id",
            "name",
            "for",
            "type",
            "aria-label",
            "data-field",
            "data-field-key",
            "data-structured-field",
            "data-exact-field",
            "data-logo-field",
            "data-logo-prop",
            "data-logo-upload",
            "data-logo-clear",
            "data-global-logo-field",
            "data-global-logo-upload",
            "data-global-logo-clear",
            "data-global-logo-reset",
            "data-amenity-icon-field",
            "data-map-field",
            "data-contact-field",
            "data-contact-default",
            "data-global-logo-field",
            "data-amenity-icon-field",
            "data-map-field",
        ):
            value = child.attrs.get(attr)
            if value:
                pieces.extend((attr, value))
        controls.append(" ".join(pieces).lower())
    return controls


def _has_field_control(control_keys: list[str], prefix: str, *terms: str) -> bool:
    return any(prefix in key and any(term in key for term in terms) for key in control_keys)


def _has_contact_field(control_keys: list[str], field_name: str) -> bool:
    pattern = re.compile(rf"(agent|contact)[\w.\-\[\]]*{field_name}|{field_name}[\w.\-\[\]]*(agent|contact)")
    if any(pattern.search(key) for key in control_keys):
        return True
    if field_name == "name":
        return any("contact" in key and re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", key, re.IGNORECASE) for key in control_keys)
    if field_name == "phone":
        return any("contact" in key and re.search(r"\b0\d[\d\s]{8,}\b", key) for key in control_keys)
    if field_name == "email":
        return any("contact" in key and "@" in key for key in control_keys)
    return False


def _has_amenity_list_control(control_keys: list[str]) -> bool:
    return any("amenit" in key and ("item" in key or "list" in key or re.search(r"amenity\d+", key)) for key in control_keys)


def _nodes_with_attr_prefix(root: HtmlNode, prefix: str) -> list[HtmlNode]:
    return [node for node in _all_nodes(root) if any(key.startswith(prefix) for key in node.attrs)]


def _ensure_semantic_overlay_test_shim() -> None:
    if hasattr(exact_pdf_layout, "_render_semantic_overlays"):
        pass
    else:
        exact_pdf_layout._render_semantic_overlays = lambda page, field_config: ""
    if not hasattr(exact_pdf_layout, "_build_amenity_icon_defs"):
        exact_pdf_layout._build_amenity_icon_defs = lambda amenities, pages: []
    if not hasattr(exact_pdf_layout, "_build_map_region"):
        exact_pdf_layout._build_map_region = lambda pages: {}


if __name__ == "__main__":
    unittest.main()
