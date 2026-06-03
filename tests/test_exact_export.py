from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path

from app import _exact_export_dimensions, _prepare_exact_export_html
from brochure_maker.pdf_renderer import render_pdf


class TestExactExport(unittest.TestCase):
    def test_exact_export_is_static_presentation_html(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1587, "page_height": 1122}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps(
                    {
                        "exactLayout": True,
                        "globalLogo": {"type": "facade", "coverSize": 70, "coverPosition": "top-left"},
                        "editableTexts": {"copy": {"html": "Edited export copy", "edited": True}},
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head><style>.pdf-text[contenteditable="true"] *{font-family:inherit;}</style></head><body class="fields-open editing">
                  <div class="exact-toolbar">Toolbar</div>
                  <aside class="exact-fields-panel"><input type="file"></aside>
                  <main>
                    <section class="exact-page" data-page-num="1">
                      <img class="pdf-bg" src="/api/projects/demo/exact_assets/page001-full.png"/>
                      <p data-save-id="copy" contenteditable="true" spellcheck="false">Source copy</p>
                    </section>
                  </main>
                  <script>window.__PROJECT_ID__ = "demo";</script>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("export-clean", html)
        self.assertNotIn("fields-open", html)
        self.assertNotIn("editing", html)
        self.assertIn("Edited export copy", html)
        self.assertIn("exact-logo", html)
        self.assertNotIn("window.__EXACT_EMBEDDED_STATE__", html)
        self.assertNotIn("window.__PROJECT_ID__", html)
        self.assertIn("page001-full.png", html)
        self.assertIn('class="pdf-bg"', html)
        self.assertNotIn("contenteditable", html)
        self.assertNotIn("spellcheck", html)
        self.assertNotIn("exact-toolbar", html)
        self.assertNotIn("exact-fields-panel", html)
        self.assertNotIn('type="file"', html)
        self.assertNotIn("<script", html)

    def test_exact_export_keeps_pdf_background_without_dropping_page_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 600}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps(
                    {
                        "exactLayout": True,
                        "editableTexts": {"copy": {"html": "Edited page copy", "edited": True}},
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="2">
                      <img class="pdf-bg" src="/api/projects/demo/exact_assets/page002-full.png">
                      <p data-save-id="copy" class="pdf-text" contenteditable="true">Original page copy</p>
                      <div class="exact-image-slot" data-save-id="image1"></div>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("Edited page copy", html)
        self.assertIn("exact-image-slot", html)
        self.assertIn("page002-full.png", html)
        self.assertIn('class="pdf-bg"', html)

    def test_exact_export_removes_noisy_ocr_source_mark_fragments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1365, "page_height": 1024}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1">
                      <img class="pdf-bg" src="/api/projects/demo/exact_assets/page001-full.png">
                      <p class="pdf-text exact-ocr-text" data-save-id="exact-page1-text1"
                         data-ocr-fallback="true" data-typography-role="body"
                         data-font-size="341.32px" data-plain-text="Hie"
                         style="font-size:341.32px;width:441px;white-space:nowrap">Hie</p>
                      <p class="pdf-text exact-ocr-text" data-save-id="exact-page1-text2"
                         data-ocr-fallback="true" data-typography-role="section-heading"
                         data-font-size="37px" data-plain-text="SCHEDULE">SCHEDULE</p>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertNotIn(">Hie<", html)
        self.assertIn(">SCHEDULE<", html)

    def test_exact_export_ignores_stale_unedited_text_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 600}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps(
                    {
                        "exactLayout": True,
                        "editableTexts": {
                            "schedule-cell": {"html": "F&nbsp;LO&nbsp;O&nbsp;R", "edited": False},
                            "custom-copy": {"html": "Edited marker", "edited": True},
                        },
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="8">
                      <p data-save-id="schedule-cell" class="pdf-text" contenteditable="true"
                         data-plain-text="SQ FT" data-original-html="SQ FT">SQ FT</p>
                      <p data-save-id="custom-copy" class="pdf-text" contenteditable="true"
                         data-plain-text="FLOOR" data-original-html="FLOOR">FLOOR</p>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("SQ FT", html)
        self.assertIn("Edited marker", html)
        self.assertNotIn("F&nbsp;LO", html)
        self.assertNotIn("F LO O R", html)

    def test_exact_export_replaces_mapped_source_facade_logo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1587, "page_height": 1122}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps({"exactLayout": True, "globalLogo": {"type": "facade", "coverSize": 56, "coverPosition": "source"}}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1">
                      <svg class="pdf-vector-layer" viewBox="0 0 1587 1122"><path class="pdf-vector-shape" data-bbox="900,10,500,900"/></svg>
                      <div class="exact-source-logo-slot logo-zone"
                           data-source-logo-slot="facade-p1-1"
                           style="left:900px;top:10px;width:500px;height:900px;--source-logo-mask:#282827;"></div>
                    </section>
                  </main>
                  <script>window.__PROJECT_ID__ = "demo";</script>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("exact-source-logo-slot", html)
        self.assertIn("is-active", html)
        self.assertIn("exact-source-logo-mask", html)
        self.assertIn("exact-source-logo-art", html)
        self.assertIn('data-source-logo-mask-role="dark"', html)
        self.assertIn('data-source-logo-mask-mode="vector"', html)
        self.assertIn("source-logo-hidden", html)
        self.assertIn('data-logo-hidden-by="source-logo:facade-p1-1"', html)
        self.assertIn('data-source-logo-mask-mode="vector"] .exact-source-logo-mask', html)
        self.assertIn('data-source-logo-mask-mode="photo"] .exact-source-logo-mask { display: none', html)
        self.assertIn("--source-logo-mask:var(--exact-dark);", html)
        self.assertNotIn("--source-logo-mask:#282827", html)
        self.assertNotIn("exact-logo logo-zone", html)

    def test_exact_export_preserves_photo_source_logo_mask_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1587, "page_height": 1122}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps({"exactLayout": True, "globalLogo": {"type": "facade", "coverSize": 56, "coverPosition": "source"}}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1">
                      <div class="exact-source-logo-slot logo-zone"
                           data-source-logo-slot="facade-p1-1"
                           data-source-logo-mask-mode="photo"
                           data-default-source-logo-mask-mode="photo"
                           style="left:900px;top:10px;width:120px;height:80px;--source-logo-mask:#9b9d9e;"></div>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn('data-source-logo-mask-mode="photo"', html)
        self.assertIn("exact-source-logo-mask", html)
        self.assertIn('data-source-logo-mask-mode="photo"] .exact-source-logo-mask { display: none', html)

    def test_exact_export_keeps_unreplaced_default_source_logo_mark(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1587, "page_height": 1122}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(json.dumps({"exactLayout": True}), encoding="utf-8")
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1">
                      <div class="exact-source-logo-slot logo-zone has-default-source-logo"
                           data-source-logo-slot="facade-p1-1"
                           data-default-source-logo-asset="/api/projects/demo/exact_assets/images/source.png">
                        <div class="exact-source-logo-default"><img src="/api/projects/demo/exact_assets/images/source.png" alt=""></div>
                      </div>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("exact-source-logo-default", html)
        self.assertIn("/api/projects/demo/exact_assets/images/source.png", html)

    def test_exact_export_hides_unedited_editor_overlays(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1587, "page_height": 1122}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1" data-picture-layout="editable">
                      <p class="pdf-text" data-save-id="copy" contenteditable="true">Original copy</p>
                      <div class="exact-image-mask" data-mask-for="image1"></div>
                      <div class="exact-image-slot" data-save-id="image1"></div>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("body.export-clean .exact-image-mask:not(.is-replaced)", html)
        self.assertNotIn("contenteditable", html)

    def test_exact_export_forces_source_preserved_layout_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 600}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1" data-picture-layout="editable">
                      <img class="pdf-bg" src="/api/projects/demo/exact_assets/page001-full.png">
                      <div class="exact-image-slot" data-save-id="image1"></div>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn('data-picture-layout="original"', html)

    def test_exact_export_uses_extracted_theme_colour_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 600}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head>
                  <style>:root{--exact-accent:#c8d35b;--exact-dark:#454b43;}</style>
                </head><body>
                  <main><section class="exact-page" data-page-num="1"></section></main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("--exact-accent:#c8d35b;", html)
        self.assertIn("--exact-dark:#454b43;", html)
        self.assertNotIn("--exact-dark:#333132;", html)

    def test_exact_export_does_not_activate_text_only_default_agency_logo_slots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 600}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main><section class="exact-page" data-page-num="5"></section></main>
                  <script>
                    window.__EXACT_AGENCY_LOGOS__ = {
                      "agency1": {
                        "label": "BBGREAL",
                        "page": "5",
                        "left": 24,
                        "top": 420,
                        "width": 170,
                        "height": 96,
                        "className": "agency",
                        "defaultText": "BBGREAL"
                      }
                    };
                  </script>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertNotIn('data-agency-logo-slot="agency1"', html)
        self.assertNotIn("logo-output-agency", html)
        self.assertNotIn("BBGREAL", html)
        self.assertNotIn("<script", html)

    def test_exact_export_materialises_detected_default_agency_logo_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 600}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main><section class="exact-page" data-page-num="5"></section></main>
                  <script>
                    window.__EXACT_AGENCY_LOGOS__ = {
                      "agency1": {
                        "label": "BBGREAL",
                        "page": "5",
                        "left": 24,
                        "top": 420,
                        "width": 170,
                        "height": 96,
                        "className": "agency",
                        "defaultText": "BBGREAL",
                        "defaultAssetUrl": "/api/projects/demo/exact_assets/images/agency-logo-p5-bbg-1.png",
                        "sourceDetection": "raster logo region below contact group"
                      }
                    };
                  </script>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn('data-agency-logo-slot="agency1"', html)
        self.assertIn("has-default-agency-logo", html)
        self.assertIn("exact-agency-logo-default", html)
        self.assertIn("/api/projects/demo/exact_assets/images/agency-logo-p5-bbg-1.png", html)
        self.assertIn("raster logo region below contact group", html)
        self.assertIn(
            "body.export-clean .exact-brand-logo-slot:not(.is-active):not(.has-default-agency-logo)",
            html,
        )
        self.assertNotIn("body.export-clean .exact-brand-logo-slot:not(.is-active),", html)
        self.assertNotIn("logo-output-agency", html)
        self.assertNotIn("<script", html)

    def test_exact_export_does_not_duplicate_above_contact_default_agency_logo_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 600}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main><section class="exact-page" data-page-num="8"></section></main>
                  <script>
                    window.__EXACT_AGENCY_LOGOS__ = {
                      "agency1": {
                        "label": "BNPPARIBAS",
                        "page": "8",
                        "left": 420,
                        "top": 250,
                        "width": 240,
                        "height": 80,
                        "className": "agency",
                        "defaultText": "BNPPARIBAS",
                        "defaultAssetUrl": "/api/projects/demo/exact_assets/images/agency-logo-p8-bnp-1.png",
                        "sourceDetection": "raster logo region above contact group"
                      }
                    };
                  </script>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertNotIn('data-agency-logo-slot="agency1"', html)
        self.assertNotIn("agency-logo-p8-bnp-1.png", html)
        self.assertNotIn("<script", html)

    def test_exact_export_creates_saved_active_agency_logo_slot_without_editor_javascript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 600}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps(
                    {
                        "exactLayout": True,
                        "agencyLogos": {
                            "agency1": {
                                "active": True,
                                "text": "BBGREAL",
                                "uploadedLogoDataUrl": "",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main><section class="exact-page" data-page-num="5"></section></main>
                  <script>
                    window.__EXACT_AGENCY_LOGOS__ = {
                      "agency1": {
                        "label": "BBGREAL",
                        "page": "5",
                        "left": 24,
                        "top": 420,
                        "width": 170,
                        "height": 96,
                        "className": "agency",
                        "defaultText": "Default"
                      }
                    };
                  </script>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn('data-agency-logo-slot="agency1"', html)
        self.assertIn("exact-brand-logo-slot", html)
        self.assertIn("is-active", html)
        self.assertIn("logo-output-agency", html)
        self.assertIn("BBGREAL", html)
        self.assertNotIn("Default", html)
        self.assertNotIn("<script", html)

    def test_exact_export_marks_user_replaced_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1587, "page_height": 1122}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps(
                    {
                        "exactLayout": True,
                        "images": {
                            "image1": {
                                "bgImage": "url(\"data:image/png;base64,abc\")",
                                "fit": "cover",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1" data-picture-layout="editable">
                      <div class="exact-image-mask has-image" data-mask-for="image1"></div>
                      <div class="exact-image-slot has-image" data-save-id="image1">
                        <div class="slot-photo"></div>
                      </div>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("exact-image-slot has-image is-replaced", html)
        self.assertIn("exact-image-mask has-image is-replaced", html)
        self.assertIn("data:image/png;base64,abc", html)
        self.assertIn('<img alt="" class="slot-photo-img" src="data:image/png;base64,abc"/>', html)

    def test_exact_export_strips_pasted_font_overrides_but_keeps_typography_role(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1587, "page_height": 1122}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps(
                    {
                        "exactLayout": True,
                        "typography": {
                            "cover-title": {
                                "fontFamily": "AreaNormal-Regular",
                                "defaultFontFamily": "DalaMoa-Thin",
                                "cssVar": "--exact-font-cover-title",
                                "changed": True,
                            }
                        },
                        "editableTexts": {
                            "title": {
                                "html": '<span style="font-family: Arial; font-size: 16px; color: red;">FRIARS PLUS</span>',
                                "edited": True,
                                "typography": {"role": "cover-title", "fontAlias": "DalaMoa-Thin"},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head>
                  <style>.ft10{font-family:'DalaMoa-Thin', Arial, sans-serif;font-size:128px;}</style>
                </head><body>
                  <main>
                    <section class="exact-page" data-page-num="1">
                      <p class="ft10 pdf-text" data-save-id="title"
                         data-typography-role="cover-title" data-font-alias="DalaMoa-Thin"
                         contenteditable="true" spellcheck="false">FRIARS</p>
                    </section>
                  </main>
                  <script>window.__PROJECT_ID__ = "demo";</script>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("FRIARS PLUS", html)
        self.assertIn('class="ft10 pdf-text"', html)
        self.assertIn('data-typography-role="cover-title"', html)
        self.assertIn('data-font-alias="DalaMoa-Thin"', html)
        self.assertIn('--exact-font-cover-title:"AreaNormal-Regular", Arial, sans-serif;', html)
        self.assertIn("font-family:var(--exact-font-cover-title", html)
        self.assertNotIn("contenteditable", html)
        self.assertNotIn("spellcheck", html)
        self.assertNotIn("font-family: Arial", html)
        self.assertNotIn("font-size: 16px", html)
        self.assertNotIn("color: red", html)

    def test_exact_export_preserves_stacked_title_layout_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 1200}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps(
                    {
                        "exactLayout": True,
                        "editableTexts": {
                            "title-primary": {
                                "html": '<span class="exact-title-stack"><span>H</span><span>O</span><span>U</span><span>S</span><span>E</span></span>',
                                "edited": True,
                                "typography": {"role": "cover-title"},
                                "layout": {
                                    "styles": {
                                        "left": "160px",
                                        "top": "220px",
                                        "width": "64px",
                                        "height": "360px",
                                        "white-space": "normal",
                                        "display": "flex",
                                        "align-items": "center",
                                        "justify-content": "center",
                                        "text-align": "center",
                                    },
                                    "titleStack": True,
                                },
                            },
                            "title-fragment": {
                                "html": "",
                                "edited": True,
                                "typography": {"role": "cover-title"},
                                "layout": {"hidden": True},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1">
                      <p class="pdf-text" data-save-id="title-primary" data-typography-role="cover-title"
                         contenteditable="true" spellcheck="false">S</p>
                      <p class="pdf-text" data-save-id="title-fragment" data-typography-role="cover-title"
                         contenteditable="true" spellcheck="false">e</p>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn('data-title-stack="true"', html)
        self.assertIn("exact-title-stack", html)
        self.assertIn("left:160px", html)
        self.assertIn("height:360px", html)
        self.assertIn("exact-field-hidden", html)
        self.assertNotIn("contenteditable", html)
        self.assertNotIn("spellcheck", html)

    def test_exact_export_keeps_default_source_logo_slots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 1200}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1">
                      <div class="exact-source-logo-slot has-default-source-logo"
                           data-source-logo-slot="facade-p1-1">
                        <div class="exact-source-logo-default">
                          <img src="/api/projects/test/exact_assets/images/page001-source-mark-01.png" alt="">
                        </div>
                      </div>
                      <div class="exact-source-logo-slot" data-source-logo-slot="empty-p1"></div>
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("facade-p1-1", html)
        self.assertIn("page001-source-mark-01.png", html)
        self.assertNotIn("empty-p1", html)

    def test_exact_export_keeps_pdf_background_for_source_preserved_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 900, "page_height": 1200}),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head><style>
                .pdf-bg { display:none; }
                .exact-page[data-picture-layout="original"] .pdf-bg { display:block; }
                </style></head><body>
                  <main>
                    <section class="exact-page" data-page-num="1" data-picture-layout="original">
                      <img class="pdf-bg" src="/api/projects/test/exact_assets/page001-full.png" alt="">
                    </section>
                  </main>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("pdf-bg", html)
        self.assertIn("page001-full.png", html)
        self.assertIn('data-picture-layout="original"', html)

    def test_exact_export_preserves_global_icon_style(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1587, "page_height": 1122}),
                encoding="utf-8",
            )
            (project_dir / "editor_state.json").write_text(
                json.dumps(
                    {
                        "exactLayout": True,
                        "accentColour": "#ffea00",
                        "globalIconStyle": {
                            "size": "125",
                            "strokeWidth": "4.5",
                            "color": "#00aaff",
                            "colorCustom": True,
                        },
                        "amenityIconFields": {"amenity1": {"iconId": "bike", "active": True}},
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "brochure.html").write_text(
                """
                <html><head></head><body>
                  <main>
                    <section class="exact-page" data-page-num="4">
                      <button class="highlight-icon exact-amenity-icon-slot"
                              data-icon-slot="amenity1" data-icon-id="office"
                              data-default-icon-id="office"></button>
                    </section>
                  </main>
                  <script>window.__PROJECT_ID__ = "demo";</script>
                </body></html>
                """,
                encoding="utf-8",
            )

            html = _prepare_exact_export_html(project_dir)

        self.assertIn("--exact-icon-color:#00aaff;", html)
        self.assertIn("--exact-icon-scale:1.250;", html)
        self.assertIn("--exact-icon-stroke-width:4.50;", html)
        self.assertIn('data-icon-id="bike"', html)
        self.assertIn("is-active", html)
        self.assertNotIn("<script", html)

    def test_exact_pdf_export_uses_metadata_dimensions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(
                json.dumps({"page_width": 1587, "page_height": 1122}),
                encoding="utf-8",
            )

            self.assertEqual(_exact_export_dimensions(project_dir), (1587, 1122))

    def test_pdf_renderer_accepts_explicit_dimensions(self):
        signature = inspect.signature(render_pdf)

        self.assertIn("width_px", signature.parameters)
        self.assertIn("height_px", signature.parameters)
        self.assertIn("base_url", signature.parameters)


if __name__ == "__main__":
    unittest.main()
