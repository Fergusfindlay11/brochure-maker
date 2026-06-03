import os
import shutil
import tempfile
import unittest
from pathlib import Path

import fitz

from brochure_maker.map_v1.determinism import pdf_structural_hash, svg_hash
from brochure_maker.map_v1.duckdb_store import OvertureDuckDBStore
from brochure_maker.map_v1.errors import DeterminismViolationError, ExportError, PoiQueryTimeoutError
from brochure_maker.map_v1.models import MapStyleGenerateRequestV1
from brochure_maker.map_v1.service import map_http_exception
from brochure_maker.map_v1.style_director import generate_style_tokens
from brochure_maker.map_v1.text_metrics import TextMeasureInput, measure_text
from brochure_maker.map_v1.vector_purity import assert_vector_purity, check_svg_vector_purity


class TestMapV11Patch(unittest.TestCase):
    def test_style_defaults_include_bbox_rect_halo_mode(self):
        style = generate_style_tokens(
            MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E", vibe_text="")
        )
        self.assertEqual(style.style_tokens.rules.label_halo_mode, "bbox_rect")

    def test_text_metrics_are_stable_for_fixture_strings(self):
        if not shutil.which("fc-match"):
            self.skipTest("fontconfig not available in this environment")

        fixtures = ["St John's", "W", "MW", "ffi", "Liverpool Road Station"]
        for text in fixtures:
            left = measure_text(
                TextMeasureInput(
                    font_family="Helvetica",
                    weight="Regular",
                    size_pt=9.0,
                    tracking=0.0,
                    text=text,
                ),
                halo_width_pt=1.2,
            )
            right = measure_text(
                TextMeasureInput(
                    font_family="Helvetica",
                    weight="Regular",
                    size_pt=9.0,
                    tracking=0.0,
                    text=text,
                ),
                halo_width_pt=1.2,
            )
            self.assertEqual(left.width_px, right.width_px)
            self.assertEqual(left.height_px, right.height_px)
            self.assertEqual(left.baseline_px, right.baseline_px)

    def test_svg_hash_is_canonical_across_attribute_order(self):
        a = '<svg viewBox="0 0 10 10" xmlns="http://www.w3.org/2000/svg"><rect y="2.0000" x="1" width="3" height="4"/></svg>'
        b = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect height="4.00000" width="3.0" x="1.0" y="2"/></svg>'
        self.assertEqual(svg_hash(a), svg_hash(b))

    def test_pdf_structural_hash_stable(self):
        doc = fitz.open()
        page = doc.new_page(width=200, height=120)
        page.insert_text((20, 30), "Hello Map")
        page.draw_line((20, 50), (180, 50))
        first = doc.tobytes()
        second = doc.tobytes()
        self.assertEqual(pdf_structural_hash(first), pdf_structural_hash(second))

    def test_vector_purity_checks(self):
        good_svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect x="0" y="0" width="10" height="10"/></svg>'
        self.assertTrue(check_svg_vector_purity(good_svg))
        self.assertFalse(
            check_svg_vector_purity(
                '<svg xmlns="http://www.w3.org/2000/svg"><image href="x.png" x="0" y="0" width="10" height="10"/></svg>'
            )
        )

        doc = fitz.open()
        page = doc.new_page(width=200, height=120)
        page.insert_text((20, 30), "Vector Text")
        page.draw_rect(fitz.Rect(10, 40, 190, 80))
        pdf = doc.tobytes()
        purity = assert_vector_purity(good_svg, pdf)
        self.assertTrue(purity["svg_ok"])
        self.assertTrue(purity["pdf_ok"])

    def test_duckdb_query_has_stable_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "map.duckdb")
            store = OvertureDuckDBStore(Path(db))
            conn = store._connect()  # noqa: SLF001
            has_geom = store._table_has_column("pois", "geom")  # noqa: SLF001

            if has_geom:
                conn.execute(
                    """
                    INSERT INTO pois(id,name,category,source,license,release_id,lon,lat,minx,miny,maxx,maxy,priority,geom)
                    VALUES
                    ('a','A','station','src','lic','r1',-0.1,51.5,-0.1001,51.4999,-0.0999,51.5001,5,ST_Point(-0.1,51.5)),
                    ('b','B','cafe','src','lic','r1',-0.1002,51.5001,-0.1003,51.5,-0.1001,51.5002,7,ST_Point(-0.1002,51.5001)),
                    ('c','C','station','src','lic','r1',-0.1004,51.5002,-0.1005,51.5001,-0.1003,51.5003,4,ST_Point(-0.1004,51.5002))
                    """
                )
            else:
                conn.execute(
                    """
                    INSERT INTO pois(id,name,category,source,license,release_id,lon,lat,minx,miny,maxx,maxy,priority)
                    VALUES
                    ('a','A','station','src','lic','r1',-0.1,51.5,-0.1001,51.4999,-0.0999,51.5001,5),
                    ('b','B','cafe','src','lic','r1',-0.1002,51.5001,-0.1003,51.5,-0.1001,51.5002,7),
                    ('c','C','station','src','lic','r1',-0.1004,51.5002,-0.1005,51.5001,-0.1003,51.5003,4)
                    """
                )

            rows = store.query_pois(
                (-0.101, 51.499, -0.099, 51.501),
                ["station", "cafe"],
                10,
                category_priority={"station": 0, "cafe": 1},
            )
            self.assertEqual([r.id for r in rows], ["a", "c", "b"])

    def test_error_payload_contract(self):
        status, detail = map_http_exception(PoiQueryTimeoutError("timeout"))
        self.assertEqual(status, 504)
        self.assertEqual(detail["code"], "POI_QUERY_TIMEOUT")
        self.assertTrue(detail["retryable"])

        status2, detail2 = map_http_exception(DeterminismViolationError("internal"))
        self.assertEqual(status2, 500)
        self.assertEqual(detail2["code"], "EXPORT_FAILED")

        status3, detail3 = map_http_exception(ExportError("failed"))
        self.assertEqual(status3, 500)
        self.assertEqual(detail3["code"], "EXPORT_FAILED")


if __name__ == "__main__":
    unittest.main()
