from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from brochure_maker.exact_browser_qa import write_browser_qa


class _FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestExactBrowserQA(unittest.TestCase):
    def test_write_browser_qa_counts_live_editor_and_clean_export(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=2)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <div class="exact-page" id="page1" data-page-num="1" data-picture-layout="editable">
                  <div class="pdf-text" contenteditable="true"
                       data-save-id="exact-page1-text1"
                       data-typography-role="cover-title"
                       data-font-alias="TitleFont"
                       data-font-family="TitleFont">Title</div>
                  <div class="exact-image-slot" data-save-id="exact-page1-image1"
                       data-image-role="hero-photo"
                       style="position:absolute;left:10px;top:120px;width:160px;height:90px">
                    <input type="file">
                  </div>
                  <div class="exact-source-logo-slot" data-source-logo-slot="source-logo-1"
                       style="position:absolute;left:20px;top:20px;width:70px;height:70px"></div>
                  <div class="map-area exact-map-area" data-exact-map-area="true"
                       data-save-id="exact-page1-map1"
                       style="position:absolute;left:200px;top:120px;width:200px;height:120px">
                    <div class="exact-map-controls"><button class="map-generate-btn">Regenerate</button></div>
                  </div>
                  <div class="exact-typography"></div>
                </div>
                <div class="exact-page" id="page2" data-page-num="2" data-picture-layout="editable">
                  <div class="pdf-text" contenteditable="true"
                       data-save-id="exact-page2-text1"
                       data-typography-role="body"
                       data-font-alias="BodyFont"
                       data-font-family="BodyFont">Body</div>
                  <div class="exact-typography"></div>
                </div>
              </main>
              <script></script>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main>
                <div class="exact-page" id="page1"></div>
                <div class="exact-page" id="page2"></div>
              </main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(qa["editor"]["pageCount"], 2)
            self.assertEqual(qa["editor"]["contenteditableCount"], 2)
            self.assertEqual(qa["editor"]["coverPageEditableCount"], 1)
            self.assertTrue(qa["editor"]["globalControlsVisible"])
            self.assertEqual(qa["clean_export"]["scriptCount"], 0)
            self.assertEqual(qa["clean_export"]["contenteditableCount"], 0)
            self.assertTrue(qa["assertions"]["editor_has_expected_pages"])
            self.assertTrue(qa["assertions"]["global_controls_visible"])
            self.assertTrue(qa["assertions"]["cover_page_editable"])
            self.assertTrue(qa["assertions"]["image_slots_editable_by_default"])
            self.assertTrue(qa["assertions"]["media_slots_have_actionable_controls"])
            self.assertTrue(qa["assertions"]["image_replacement_roundtrip_preserved"])
            self.assertTrue(qa["assertions"]["logo_replacement_roundtrip_preserved"])
            self.assertTrue(qa["assertions"]["map_replacement_roundtrip_preserved"])
            self.assertTrue(qa["assertions"]["ocr_fallback_text_hidden_until_edit"])
            self.assertTrue(qa["assertions"]["export_has_expected_pages"])
            self.assertTrue(qa["assertions"]["export_has_no_editor_chrome"])
            self.assertTrue(qa["assertions"]["state_roundtrip_preserved"])
            self.assertTrue(qa["assertions"]["clean_export_preserves_edited_text"])
            self.assertTrue(qa["assertions"]["clean_export_preserves_global_colour"])
            self.assertTrue(qa["assertions"]["typed_text_font_preserved"])
            self.assertTrue(qa["assertions"]["source_preserved_pages_have_no_giant_interactive_hotspots"])
            self.assertTrue(qa["interactions"]["accepted"])
            self.assertTrue(qa["interactions"]["nonDestructive"])
            self.assertEqual(qa["interactions"]["targetSaveId"], "exact-page1-text1")
            self.assertEqual(qa["interactions"]["imageProbeTargetSaveId"], "exact-page1-image1")
            self.assertEqual(qa["interactions"]["logoProbeTargetSlotId"], "source-logo-1")
            self.assertEqual(qa["interactions"]["mapProbeTargetSaveId"], "exact-page1-map1")
            self.assertFalse((project_dir / "editor_state.json").exists())
            self.assertEqual(qa["blockers"], [])

    def test_write_browser_qa_rejects_hidden_original_mode_image_slots_and_empty_cover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <div class="exact-page" id="page1" data-picture-layout="original">
                  <div class="exact-image-slot"></div>
                  <div class="exact-typography"></div>
                </div>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><div class="exact-page" id="page1"></div></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(qa["assertions"]["cover_page_editable"])
            self.assertFalse(qa["assertions"]["image_slots_editable_by_default"])
            self.assertIn("Browser did not confirm editable cover-page elements", qa["blockers"])
            self.assertIn("Browser counted image slots but the editor default hides/disables them", qa["blockers"])

    def test_write_browser_qa_rejects_missing_slots_expected_by_design_graph(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            (project_dir / "brochure.design.json").write_text(
                json.dumps(
                    {
                        "page_count": 1,
                        "pages": [
                            {
                                "page_number": 1,
                                "detected_features": ["photo_regions", "map", "source_facade_mark"],
                                "elements": [
                                    {"type": "image", "role": "photo-region"},
                                    {"type": "map", "role": "map"},
                                    {
                                        "type": "logo",
                                        "role": "source-facade-mark",
                                        "bbox": {"x": 0.2, "y": 0.2, "width": 0.15, "height": 0.1},
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <div class="exact-page" id="page1" data-page-num="1" data-picture-layout="editable">
                  <div class="pdf-text" contenteditable="true"
                       data-save-id="exact-page1-text1"
                       data-typography-role="cover-title"
                       data-font-alias="TitleFont"
                       data-font-family="TitleFont">Title</div>
                  <div class="exact-typography"></div>
                </div>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><div class="exact-page" id="page1"></div></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(qa["assertions"]["media_slots_have_actionable_controls"])
            self.assertFalse(qa["assertions"]["image_replacement_roundtrip_preserved"])
            self.assertFalse(qa["assertions"]["logo_replacement_roundtrip_preserved"])
            self.assertFalse(qa["assertions"]["map_replacement_roundtrip_preserved"])
            self.assertIn(
                "PDF/design evidence expects image/artwork/space-plan replacement slots but Browser found none",
                qa["blockers"],
            )
            self.assertIn(
                "Browser interaction probe did not confirm source logo replacement persistence/export",
                qa["blockers"],
            )

    def test_write_browser_qa_rejects_too_few_slots_expected_by_design_graph(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            (project_dir / "brochure.design.json").write_text(
                json.dumps(
                    {
                        "page_count": 1,
                        "pages": [
                            {
                                "page_number": 1,
                                "detected_features": ["photo_regions"],
                                "elements": [
                                    {
                                        "type": "image",
                                        "role": "photo-region",
                                        "bbox": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
                                    },
                                    {
                                        "type": "image",
                                        "role": "photo-region",
                                        "bbox": {"x": 0.4, "y": 0.1, "width": 0.2, "height": 0.2},
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <div class="exact-page" id="page1" data-page-num="1" data-picture-layout="editable">
                  <div class="pdf-text" contenteditable="true"
                       data-save-id="exact-page1-text1"
                       data-typography-role="cover-title"
                       data-font-alias="TitleFont"
                       data-font-family="TitleFont">Title</div>
                  <div class="exact-image-slot" data-save-id="exact-page1-image1"
                       data-image-role="photo-region"
                       style="position:absolute;left:10px;top:120px;width:160px;height:90px">
                    <input type="file">
                  </div>
                  <div class="exact-typography"></div>
                </div>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><div class="exact-page" id="page1"></div></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(qa["assertions"]["media_slots_have_actionable_controls"])
            self.assertIn(
                "PDF/design evidence expects 2 image/artwork/space-plan replacement slots but Browser found 1",
                qa["blockers"],
            )

    def test_write_browser_qa_rejects_visible_ocr_fallback_over_source_artwork(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <div class="exact-page" id="page1" data-picture-layout="editable">
                  <p class="pdf-text exact-ocr-text" contenteditable="true"
                     data-save-id="exact-page1-text1"
                     data-typography-role="cover-title">ANCHOR HOUSE</p>
                  <div class="exact-typography"></div>
                </div>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><div class="exact-page" id="page1"></div></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(qa["assertions"]["ocr_fallback_text_hidden_until_edit"])
            self.assertIn(
                "Browser counted OCR fallback text but it is visible by default and duplicates source artwork",
                qa["blockers"],
            )

    def test_existing_passing_browser_qa_is_preserved_without_force(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            qa_path = project_dir / "browser_qa.json"
            qa_path.write_text(
                json.dumps(
                    {
                        "interactions": {"titleEditFontPreserved": True},
                        "assertions": {
                            "editor_has_expected_pages": True,
                            "global_controls_visible": True,
                            "export_has_expected_pages": True,
                            "export_has_no_editor_chrome": True,
                            "source_preserved_pages_have_no_giant_interactive_hotspots": True,
                            "media_slots_have_actionable_controls": True,
                            "image_replacement_roundtrip_preserved": True,
                            "logo_replacement_roundtrip_preserved": True,
                            "map_replacement_roundtrip_preserved": True,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("urllib.request.urlopen") as urlopen:
                path = write_browser_qa(project_dir)

            self.assertEqual(path.resolve(), qa_path.resolve())
            self.assertFalse(urlopen.called)
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertTrue(qa["interactions"]["titleEditFontPreserved"])

    def test_write_browser_qa_records_text_overlap_warnings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1">
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="title-a" data-typography-role="cover-title"
                     data-font-size="72px" data-line-height="82px"
                     style="position:absolute;top:100px;left:80px;white-space:nowrap">Sekforde</p>
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="title-b" data-typography-role="cover-title"
                     data-font-size="72px" data-line-height="82px"
                     style="position:absolute;top:116px;left:100px;white-space:nowrap">Street</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            warnings = qa["layout_audit"]["textOverlapWarnings"]
            self.assertTrue(warnings)
            self.assertEqual(warnings[0]["page"], 1)
            self.assertEqual(warnings[0]["first"]["id"], "title-a")
            self.assertEqual(warnings[0]["second"]["id"], "title-b")
            self.assertIn("estimated DOM boxes", warnings[0]["evidence"])

    def test_write_browser_qa_ignores_expected_stacked_heading_line_boxes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1">
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="heading-a" data-typography-role="section-heading"
                     data-font-size="64px" data-line-height="76.8px"
                     style="position:absolute;top:180px;left:80px;white-space:nowrap">S L E E K</p>
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="heading-b" data-typography-role="section-heading"
                     data-font-size="64px" data-line-height="76.8px"
                     style="position:absolute;top:240px;left:80px;white-space:nowrap">A N D F L E X I B L E</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(qa["layout_audit"]["textOverlapWarnings"], [])
            self.assertEqual(qa["layout_audit"]["textOverlaps"], [])

    def test_write_browser_qa_ignores_source_preserved_same_line_fragments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1" data-source-preserved-edit="true">
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="place-label" data-typography-role="body"
                     data-font-size="19px" data-line-height="22.8px"
                     style="position:absolute;top:975px;left:1695px;white-space:nowrap;width:118.56px">Little Lines</p>
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="line-fragment" data-typography-role="body"
                     data-font-size="12px" data-line-height="14.4px"
                     style="position:absolute;top:987px;left:1804px;white-space:nowrap;width:6.24px">N</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(qa["layout_audit"]["textOverlapWarnings"], [])
            self.assertEqual(qa["layout_audit"]["textOverlaps"], [])

    def test_write_browser_qa_ignores_heading_edge_box_false_positive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1">
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="heading" data-typography-role="section-heading"
                     data-font-size="40px" data-line-height="48px"
                     style="position:absolute;top:148px;left:53px;white-space:nowrap">C O N N E C T I V I T Y</p>
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="poi" data-typography-role="body"
                     data-font-size="12px" data-line-height="14.4px"
                     style="position:absolute;top:187px;left:495px;white-space:nowrap">Digme Fitness</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(qa["layout_audit"]["textOverlapWarnings"], [])
            self.assertEqual(qa["layout_audit"]["textOverlaps"], [])

    def test_write_browser_qa_ignores_original_mode_transparent_pdf_overlay_overlap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1" data-picture-layout="original">
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="map-fragment" data-typography-role="body"
                     data-font-size="13px" data-line-height="15.6px"
                     style="position:absolute;top:605px;left:1587px;white-space:nowrap">een</p>
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="amenity-list" data-typography-role="body"
                     data-font-size="13px" data-line-height="15.6px"
                     style="position:absolute;top:404px;left:1457px;width:218.4px;height:420px;white-space:nowrap">08 City Social
09 Pizza Pilgrims
10 The Ivy
11 Eataly</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(qa["layout_audit"]["textOverlapWarnings"], [])
            self.assertEqual(qa["layout_audit"]["textOverlaps"], [])

    def test_write_browser_qa_ignores_adjacent_estimated_pdf_span_widths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1">
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="fragment-a" data-typography-role="body"
                     data-font-size="13px" data-line-height="15.6px"
                     style="position:absolute;top:751px;left:1085px;white-space:nowrap">The site lends itself well to a range of alternative uses including</p>
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="fragment-b" data-typography-role="body"
                     data-font-size="13px" data-line-height="15.6px"
                     style="position:absolute;top:750px;left:1499px;white-space:nowrap">residential, build</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(qa["layout_audit"]["textOverlapWarnings"], [])
            self.assertEqual(qa["layout_audit"]["textOverlaps"], [])

    def test_write_browser_qa_ignores_estimated_table_column_widths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1">
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="activity" data-typography-role="body"
                     data-font-size="15px" data-line-height="16px"
                     style="position:absolute;top:748px;left:145px;white-space:nowrap">Leaflets to Local Residents with link to Consultation Website</p>
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="date" data-typography-role="body"
                     data-font-size="15px" data-line-height="18px"
                     style="position:absolute;top:748px;left:343px;white-space:nowrap">March/ April 2023</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(qa["layout_audit"]["textOverlapWarnings"], [])
            self.assertEqual(qa["layout_audit"]["textOverlaps"], [])

    def test_write_browser_qa_ignores_map_annotation_text_overlap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1">
                  <div class="map-area exact-map-area" data-exact-map-area="true"
                       data-save-id="exact-page1-map1"
                       style="position:absolute;left:40px;top:90px;width:800px;height:600px"></div>
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="map-label" data-typography-role="caption"
                     data-font-size="8px" data-line-height="9.6px"
                     style="position:absolute;top:635px;left:209px;white-space:nowrap">STAMFORD</p>
                  <p class="pdf-text" contenteditable="true"
                     data-save-id="poi-label" data-typography-role="body"
                     data-font-size="13px" data-line-height="15.6px"
                     style="position:absolute;top:635px;left:113px;white-space:nowrap">F45 Training Kensington Olympia</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(qa["layout_audit"]["textOverlapWarnings"], [])
            self.assertEqual(qa["layout_audit"]["textOverlaps"], [])

    def test_write_browser_qa_rejects_giant_source_preserved_click_hotspots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1"
                         data-picture-layout="editable" data-source-preserved-edit="true"
                         style="width:1280px;height:900px">
                  <p class="ft627 pdf-text" contenteditable="true"
                     data-save-id="street-fragment" data-typography-role="body"
                     data-font-size="12px" data-line-height="14.4px"
                     data-pdf-transform-scale="81.357"
                     style="position:absolute;top:802px;left:992px;white-space:nowrap">TO</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(qa["interaction_audit"]["accepted"])
            self.assertEqual(qa["interaction_audit"]["hotspots"][0]["id"], "street-fragment")
            self.assertIn("Browser interaction audit found giant hover/click hotspots or draggable preserved backgrounds", qa["blockers"])

    def test_write_browser_qa_accepts_suppressed_source_preserved_click_hotspots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1"
                         data-picture-layout="editable" data-source-preserved-edit="true"
                         style="width:1280px;height:900px">
                  <p class="ft627 pdf-text exact-inert-pdf-text" contenteditable="false"
                     data-save-id="street-fragment" data-typography-role="body"
                     data-pdf-transform-scale="81.357" data-interaction-suppressed="true"
                     style="position:absolute;top:802px;left:992px;white-space:nowrap">TO</p>
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(qa["interaction_audit"]["accepted"])
            self.assertEqual(qa["interaction_audit"]["hotspots"], [])

    def test_write_browser_qa_rejects_clickable_preserved_pdf_background(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><head><style>.pdf-bg{display:block;}</style></head><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1"
                         data-picture-layout="editable" data-source-preserved-edit="true"
                         style="width:1280px;height:900px">
                  <img class="pdf-bg" src="/page.png">
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(qa["interaction_audit"]["accepted"])
            self.assertEqual(qa["interaction_audit"]["backgroundIssueCount"], 1)
            self.assertIn("Browser interaction audit found giant hover/click hotspots or draggable preserved backgrounds", qa["blockers"])

    def test_write_browser_qa_accepts_inert_preserved_pdf_background(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._write_project(Path(temp_dir), "demo1234", page_count=1)
            editor_html = """
            <html><head><style>.pdf-bg{pointer-events:none;display:block;}</style></head><body>
              <aside class="exact-fields-panel"><div class="global-controls"></div></aside>
              <main>
                <section class="exact-page" id="page1" data-page-num="1"
                         data-picture-layout="editable" data-source-preserved-edit="true"
                         style="width:1280px;height:900px">
                  <img class="pdf-bg" src="/page.png" draggable="false">
                </section>
              </main>
            </body></html>
            """
            export_html = """
            <html><body class="export-clean">
              <main><section class="exact-page" id="page1"></section></main>
            </body></html>
            """

            with mock.patch("urllib.request.urlopen", side_effect=self._urlopen(editor_html, export_html, project_dir)):
                path = write_browser_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(qa["interaction_audit"]["accepted"])
            self.assertEqual(qa["interaction_audit"]["backgroundIssueCount"], 0)

    def _write_project(self, base: Path, project_id: str, *, page_count: int) -> Path:
        project_dir = base / "projects" / project_id
        project_dir.mkdir(parents=True)
        (project_dir / "brochure.design.json").write_text(
            json.dumps({"page_count": page_count, "pages": [{"page_number": page} for page in range(1, page_count + 1)]}),
            encoding="utf-8",
        )
        return project_dir

    def _urlopen(self, editor_html: str, export_html: str, project_dir: Path):
        def fake(url: str, timeout: int = 60):
            request_url = url.full_url if hasattr(url, "full_url") else str(url)
            method = getattr(url, "get_method", lambda: "GET")()
            if request_url.endswith("/state") and method == "POST":
                (project_dir / "editor_state.json").write_bytes(url.data or b"{}")
                return _FakeResponse(b'{"message":"State saved"}')
            if request_url.endswith("/state"):
                state_path = project_dir / "editor_state.json"
                return _FakeResponse(state_path.read_bytes() if state_path.exists() else b"{}")
            if request_url.endswith("/export/html"):
                state_path = project_dir / "editor_state.json"
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    save_id, item = next(iter((state.get("editableTexts") or {}).items()), ("exact-page1-text1", {}))
                    html_value = item.get("html", "")
                    typography = item.get("typography") if isinstance(item.get("typography"), dict) else {}
                    role = typography.get("role", "cover-title")
                    family = typography.get("fontFamily") or typography.get("fontAlias") or "TitleFont"
                    accent = state.get("accentColour", "")
                    dark = state.get("darkColour", "")
                    images = state.get("images") if isinstance(state.get("images"), dict) else {}
                    image_id, image_state = next(iter(images.items()), ("", {}))
                    image_bg = image_state.get("bgImage", "") if isinstance(image_state, dict) else ""
                    global_logo = state.get("globalLogo") if isinstance(state.get("globalLogo"), dict) else {}
                    logo_data_url = global_logo.get("uploadedLogoDataUrl", "")
                    map_state = state.get("mapState") if isinstance(state.get("mapState"), dict) else {}
                    map_html = map_state.get("generatedHtml", "")
                    body = f"""
                    <html><head><style>:root{{--exact-accent:{accent};--exact-dark:{dark};}}</style></head>
                    <body class="export-clean">
                      <main><div class="exact-page" id="page1">
                        <p class="pdf-text" data-save-id="{save_id}"
                           data-typography-role="{role}"
                           data-font-alias="{family}"
                           data-font-family="{family}"
                           style="font-family:var(--exact-font-cover-title, {family})">{html_value}</p>
                        <div class="exact-image-slot" data-save-id="{image_id}" style="background-image:{image_bg}"></div>
                        <div class="exact-source-logo-slot"><img src="{logo_data_url}"></div>
                        <div class="exact-map-area">{map_html}</div>
                      </div><div class="exact-page" id="page2"></div></main>
                    </body></html>
                    """
                    return _FakeResponse(body.encode("utf-8"))
                return _FakeResponse(export_html.encode("utf-8"))
            return _FakeResponse(editor_html.encode("utf-8"))

        return fake


if __name__ == "__main__":
    unittest.main()
