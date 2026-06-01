from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from brochure_maker.exact_export_qa import REQUIRED_EXPORT_ASSERTIONS, write_export_qa


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


class TestExactExportQa(unittest.TestCase):
    def test_export_qa_checks_clean_html_and_pdf_parity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(json.dumps({"page_count": 1}), encoding="utf-8")
            (project_dir / "editor_state.json").write_text(
                json.dumps(
                    {
                        "editableTexts": {"title": {"html": "Edited Austin Headline", "edited": True}},
                        "accentColour": "#abc123",
                        "darkColour": "#102030",
                    }
                ),
                encoding="utf-8",
            )
            clean_html = (
                "<html><body class=\"export-clean\">"
                "<section class=\"exact-page\">Edited Austin Headline #abc123 #102030</section>"
                "</body></html>"
            )
            pdf_bytes = b"%PDF-1.7\n" + (b"0" * 2048)

            def fake_urlopen(request, timeout=0):
                if hasattr(request, "get_method") and request.get_method() == "POST":
                    return _FakeResponse(pdf_bytes)
                return _FakeResponse(clean_html.encode("utf-8"))

            with mock.patch("brochure_maker.exact_export_qa.urllib.request.urlopen", side_effect=fake_urlopen):
                path = write_export_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(qa["accepted"])
        self.assertTrue(all(qa["assertions"][name] for name in REQUIRED_EXPORT_ASSERTIONS))
        self.assertEqual(qa["html_export"]["contenteditableCount"], 0)
        self.assertGreaterEqual(qa["pdf_export"]["bytes"], 1024)

    def test_export_qa_rejects_editor_chrome_and_missing_edited_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(json.dumps({"page_count": 1}), encoding="utf-8")
            (project_dir / "editor_state.json").write_text(
                json.dumps({"editableTexts": {"title": {"html": "Edited marker", "edited": True}}}),
                encoding="utf-8",
            )
            dirty_html = (
                "<html><body><div class=\"exact-toolbar\"></div>"
                "<section class=\"exact-page\"><p contenteditable=\"true\">Original</p></section>"
                "<script></script></body></html>"
            )

            def fake_urlopen(request, timeout=0):
                return _FakeResponse(dirty_html.encode("utf-8") if isinstance(request, str) else b"")

            with mock.patch("brochure_maker.exact_export_qa.urllib.request.urlopen", side_effect=fake_urlopen):
                path = write_export_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))

        self.assertFalse(qa["accepted"])
        self.assertFalse(qa["assertions"]["html_export_has_no_editor_chrome"])
        self.assertFalse(qa["assertions"]["html_export_preserves_edited_text"])
        self.assertFalse(qa["assertions"]["pdf_export_nonempty"])

    def test_export_qa_stages_and_restores_temporary_edited_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "exact_metadata.json").write_text(json.dumps({"page_count": 1}), encoding="utf-8")
            (project_dir / "brochure.html").write_text(
                """
                <html><body>
                  <section class="exact-page">
                    <p data-save-id="title" data-typography-role="cover-title"
                       contenteditable="true">Original title</p>
                  </section>
                </body></html>
                """,
                encoding="utf-8",
            )

            def fake_urlopen(request, timeout=0):
                if hasattr(request, "get_method") and request.get_method() == "POST":
                    return _FakeResponse(b"%PDF-1.7\n" + (b"0" * 2048))
                state = json.loads((project_dir / "editor_state.json").read_text(encoding="utf-8"))
                text = next(iter(state["editableTexts"].values()))["html"]
                html = (
                    "<html><body class=\"export-clean\">"
                    f"<section class=\"exact-page\">{text} {state['accentColour']} {state['darkColour']}</section>"
                    "</body></html>"
                )
                return _FakeResponse(html.encode("utf-8"))

            with mock.patch("brochure_maker.exact_export_qa.urllib.request.urlopen", side_effect=fake_urlopen):
                path = write_export_qa(project_dir, base_url="http://127.0.0.1:8000", force=True)

            qa = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(qa["accepted"])
        self.assertTrue(qa["temporary_state_probe"]["used"])
        self.assertTrue(qa["temporary_state_probe"]["restored"])
        self.assertFalse((project_dir / "editor_state.json").exists())


if __name__ == "__main__":
    unittest.main()
