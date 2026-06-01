import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz

from brochure_maker.pdf_vector_overlay import (
    SVG_NAMESPACE,
    extract_page_svg_paths,
    page_drawings_to_svg_overlay,
)


class TestPdfVectorOverlay(unittest.TestCase):
    def test_extracts_structured_svg_paths_from_generated_pdf_drawings(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "vector-fixture.pdf"
            self._write_vector_fixture(pdf_path)

            doc = fitz.open(pdf_path)
            try:
                paths = extract_page_svg_paths(doc[0], precision=2, page_number=1)
            finally:
                doc.close()

        self.assertEqual(len(paths), 4)

        rect = paths[0]
        self.assertEqual(rect["id"], "p001-vector-0001")
        self.assertEqual(rect["d"], "M 10 20 L 70 20 L 70 80 L 10 80 Z")
        self.assertEqual(rect["bbox"], {"x": 10.0, "y": 20.0, "width": 60.0, "height": 60.0})
        self.assertEqual(rect["stroke"], "#ff0000")
        self.assertEqual(rect["fill"], "#00ff00")
        self.assertEqual(rect["stroke_width"], 2.0)
        self.assertTrue(rect["editable"])

        line = paths[1]
        self.assertEqual(line["d"], "M 90 20 L 140 80")
        self.assertEqual(line["stroke"], "#0000ff")
        self.assertIsNone(line["fill"])
        self.assertEqual(line["stroke_width"], 3.0)

        circle = paths[2]
        self.assertIn("C", circle["d"])
        self.assertEqual(circle["bbox"], {"x": 160.0, "y": 30.0, "width": 40.0, "height": 40.0})

        bezier = paths[3]
        self.assertTrue(bezier["d"].startswith("M 20 120 C "))
        self.assertEqual(bezier["source_type"], "s")

    def test_renders_positioned_recolourable_svg_overlay_without_raster_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "vector-fixture.pdf"
            self._write_vector_fixture(pdf_path)

            doc = fitz.open(pdf_path)
            try:
                svg = page_drawings_to_svg_overlay(
                    doc[0],
                    precision=2,
                    page_number=2,
                    css_width=480,
                    css_height=320,
                    left=12,
                    top=6,
                )
            finally:
                doc.close()

        root = ET.fromstring(svg)
        self.assertEqual(root.tag, f"{{{SVG_NAMESPACE}}}svg")
        self.assertEqual(root.attrib["viewBox"], "0 0 240 160")
        self.assertEqual(root.attrib["width"], "480px")
        self.assertEqual(root.attrib["height"], "320px")
        self.assertIn("position:absolute", root.attrib["style"])
        self.assertIn("left:12px", root.attrib["style"])
        self.assertIn("top:6px", root.attrib["style"])
        self.assertEqual(root.attrib["data-vector-overlay"], "pymupdf-drawings")

        path_elements = root.findall(f"{{{SVG_NAMESPACE}}}path")
        self.assertEqual(len(path_elements), 4)
        first_path = path_elements[0]
        self.assertEqual(first_path.attrib["data-editable"], "line-art")
        self.assertEqual(first_path.attrib["data-original-stroke"], "#ff0000")
        self.assertEqual(first_path.attrib["data-original-fill"], "#00ff00")
        self.assertEqual(first_path.attrib["stroke"], "var(--pdf-vector-stroke, #ff0000)")
        self.assertEqual(first_path.attrib["fill"], "var(--pdf-vector-fill, #00ff00)")

        self.assertEqual(root.findall(f".//{{{SVG_NAMESPACE}}}image"), [])
        self.assertNotIn("data:image", svg)
        self.assertNotIn("background-image", svg)

    @staticmethod
    def _write_vector_fixture(pdf_path: Path) -> None:
        doc = fitz.open()
        page = doc.new_page(width=240, height=160)
        page.draw_rect(
            fitz.Rect(10, 20, 70, 80),
            color=(1, 0, 0),
            fill=(0, 1, 0),
            width=2,
        )
        page.draw_line(
            fitz.Point(90, 20),
            fitz.Point(140, 80),
            color=(0, 0, 1),
            width=3,
        )
        page.draw_circle(
            fitz.Point(180, 50),
            20,
            color=(0, 0, 0),
            width=1,
        )
        page.draw_bezier(
            fitz.Point(20, 120),
            fitz.Point(50, 90),
            fitz.Point(80, 150),
            fitz.Point(110, 120),
            color=(0.5, 0.25, 0.75),
            width=2,
        )
        doc.save(pdf_path)
        doc.close()


if __name__ == "__main__":
    unittest.main()
