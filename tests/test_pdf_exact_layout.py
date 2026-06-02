import io
import json
import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image

import brochure_maker.pdf_exact_layout as pdf_exact_layout
from brochure_maker.pdf_design_graph import build_design_graph, write_design_graph
from brochure_maker.pdf_exact_layout import extract_exact_layout, write_exact_layout_model


class TestPdfExactLayout(unittest.TestCase):
    def test_extracts_renderer_ready_layout_from_generated_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "fixture.pdf"
            output_dir = tmp_path / "layout"
            self._write_fixture_pdf(pdf_path)

            model = extract_exact_layout(pdf_path, output_dir, render_dpi=72, max_colors=5)

            self.assertEqual(model["schema"], "brochure-maker.pdf-exact-layout.v1")
            self.assertEqual(model["page_count"], 1)
            self.assertEqual(model["coordinate_system"]["unit"], "pt")
            self.assertEqual(model["coordinate_system"]["origin"], "top-left")

            page = model["pages"][0]
            self.assertEqual(page["size"], {"width": 240.0, "height": 160.0, "unit": "pt"})
            self.assertTrue(Path(page["background_path"]).exists())
            self.assertEqual(page["background"]["width_px"], 240)
            self.assertEqual(page["background"]["height_px"], 160)

            text = next(span for span in page["text_spans"] if "Austin Friars" in span["text"])
            self.assertEqual(text["color"], "#336699")
            self.assertEqual(text["font"]["family"], "Helvetica")
            self.assertAlmostEqual(text["bbox"]["x"], 24.0, places=1)
            self.assertTrue(text["editable"])

            self.assertEqual(len(page["image_boxes"]), 1)
            image_box = page["image_boxes"][0]
            self.assertTrue(Path(image_box["path"]).exists())
            self.assertEqual(image_box["bbox"], {"x": 150.0, "y": 55.0, "width": 50.0, "height": 40.0})
            self.assertEqual(image_box["source_width"], 20)
            self.assertEqual(image_box["source_height"], 16)
            self.assertTrue(image_box["slot"])

            self.assertIn("#e0e0b0", page["colors"]["background"])
            self.assertTrue(page["colors"]["dominant"])
            self.assertTrue(model["colors"]["dominant"])
            self.assertTrue(page["semantic_regions"])
            self.assertEqual(page["semantic_regions"][0]["kind"], "cover_title")
            self.assertEqual(model["inventory"]["schema"], "brochure-maker.extraction-inventory.v1")
            self.assertEqual(model["inventory"]["pages"][0]["page_purpose"], "cover")
            self.assertIn("palette", model["inventory"]["global_systems"])
            self.assertIn("typography", model["inventory"]["global_systems"])
            self.assertIn("evidence", page["colors"])
            self.assertIn("#336699", page["colors"]["evidence"]["text"])

    def test_write_exact_layout_model_persists_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "fixture.pdf"
            output_dir = tmp_path / "layout"
            self._write_fixture_pdf(pdf_path)

            model = write_exact_layout_model(pdf_path, output_dir, render_dpi=72)

            model_path = Path(model["model_path"])
            self.assertTrue(model_path.exists())
            inventory_path = Path(model["inventory_path"])
            self.assertTrue(inventory_path.exists())
            saved = json.loads(model_path.read_text(encoding="utf-8"))
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["page_count"], 1)
            self.assertEqual(saved["model_path"], str(model_path))
            self.assertEqual(saved["inventory_path"], str(inventory_path))
            self.assertEqual(inventory["schema"], "brochure-maker.extraction-inventory.v1")

    def test_design_graph_unifies_inventory_and_editor_slots(self):
        model = {
            "schema": "brochure-maker.pdf-exact-layout.v1",
            "source_pdf": "/tmp/source.pdf",
            "coordinate_system": {"unit": "pt", "origin": "top-left", "bbox": "x, y, width, height"},
            "pages": [
                {
                    "id": "page-001",
                    "page_number": 1,
                    "size": {"width": 200.0, "height": 100.0, "unit": "pt"},
                    "text_spans": [
                        {
                            "id": "p001-text-0001",
                            "text": "13 AUSTIN FRIARS",
                            "bbox": {"x": 20.0, "y": 10.0, "width": 120.0, "height": 18.0},
                            "font": {"family": "DalaMoa-Thin", "size": 18},
                            "color": "#ffffff",
                            "editable": True,
                        }
                    ],
                    "image_boxes": [
                        {
                            "id": "p001-image-0001",
                            "bbox": {"x": 30.0, "y": 40.0, "width": 80.0, "height": 40.0},
                            "path": "/tmp/image.png",
                            "source_width": 800,
                            "source_height": 400,
                            "slot": True,
                        }
                    ],
                }
            ],
            "inventory": {
                "schema": "brochure-maker.extraction-inventory.v1",
                "global_systems": {
                    "palette": {"accent_colour": "#ffea00", "dark_background_colour": "#333132"},
                    "typography": {"title": {"font_family": "DalaMoa-Thin"}},
                    "logo": {"controls": ["source facade mark replacement"]},
                    "amenity_icons": {"controls": ["icon bank"]},
                    "images": {"controls": ["image fit/crop mode"]},
                    "map": {"controls": ["regenerate/preserve map"]},
                    "agents": {"controls": ["agency logo replacement"]},
                    "feature_counts": {"source_facade_mark": 1},
                },
                "pages": [
                    {
                        "page_number": 1,
                        "page_purpose": "cover",
                        "layout_type": "dark cover with source mark",
                        "confidence": 0.82,
                        "detected_features": ["cover_title", "source_facade_mark", "photo_regions"],
                        "editable_text_blocks": [
                            {
                                "id": "p001-text-0001",
                                "typography_role": "title",
                                "bbox": {"x": 20.0, "y": 10.0, "width": 120.0, "height": 18.0},
                            }
                        ],
                        "source_logo_regions": [{"role": "cover/large mark", "editable": True}],
                        "space_plan_regions": [],
                        "amenity_icon_regions": [],
                        "map_regions": [],
                        "agent_contact_blocks": [],
                        "static_elements": ["original facade mark, hidden by replacement"],
                        "editable_elements": ["PDF text blocks with extracted typography"],
                        "recommended_extraction_methods": ["PDF text", "vector detection"],
                    }
                ],
            },
        }
        render_metadata = {
            "field_config": {
                "cover_title": {
                    "groups": [
                        {
                            "value": "13 AUSTIN FRIARS",
                            "targets": ["p001-text-0001"],
                            "orientation": "rotated-counterclockwise",
                            "left": 20.0,
                            "top": 10.0,
                            "width": 120.0,
                            "height": 18.0,
                        }
                    ]
                },
                "source_logos": [
                    {
                        "page": "1",
                        "key": "facade-p1-1",
                        "label": "Replace large facade mark page 1",
                        "left": 50.0,
                        "top": 20.0,
                        "width": 60.0,
                        "height": 40.0,
                        "mask_mode": "sample",
                    }
                ],
                "service_icons": [
                    {
                        "page": "1",
                        "key": "maintenance",
                        "label": "Maintenance",
                        "left": 12.0,
                        "top": 30.0,
                        "width": 20.0,
                        "height": 20.0,
                        "icon_id": "wrench",
                    }
                ],
                "agency_logos": {
                    "agency1": {
                        "page": "1",
                        "label": "Agency",
                        "left": 14.0,
                        "top": 70.0,
                        "width": 90.0,
                        "height": 18.0,
                        "defaultAssetUrl": "/api/projects/demo/exact_assets/images/agency-logo.png",
                    }
                },
                "typography": {
                    "fonts": [{"family": "DalaMoa-Thin", "label": "DalaMoa Thin"}],
                    "roles": {"title": {"fontFamily": "DalaMoa-Thin"}},
                },
            }
        }

        graph = build_design_graph(model, render_metadata=render_metadata)

        self.assertEqual(graph["schema"], "brochure-maker.design-graph.v1")
        self.assertEqual(graph["page_count"], 1)
        self.assertIn("global_controls", graph["theme_tokens"])
        self.assertEqual(graph["theme_tokens"]["typography"]["editor_roles"]["title"]["fontFamily"], "DalaMoa-Thin")
        page = graph["pages"][0]
        roles = {element["role"] for element in page["elements"]}
        self.assertIn("cover-title", roles)
        self.assertIn("source-facade-mark", roles)
        self.assertIn("service-icon", roles)
        self.assertIn("agency-logo", roles)

        title = next(element for element in page["elements"] if element["id"] == "p001-text-0001")
        self.assertEqual(title["type"], "text")
        self.assertEqual(title["typography_token"], "title")
        self.assertEqual(title["raw_pdf_bbox"], {"x": 20.0, "y": 10.0, "width": 120.0, "height": 18.0})
        self.assertEqual(title["bbox"], {"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.18})

        title_group = next(element for element in page["elements"] if element["id"] == "cover-title-group-1")
        self.assertEqual(title_group["node_class"], "structured-field")
        self.assertEqual(title_group["text"], "13 AUSTIN FRIARS")
        self.assertTrue(title_group["editable"])
        self.assertEqual(title_group["raw_coordinate_system"], "exact-html-px")

        source_logo = next(element for element in page["elements"] if element["id"] == "source-logo-facade-p1-1")
        self.assertEqual(source_logo["raw_coordinate_system"], "exact-html-px")
        agency_logo = next(element for element in page["elements"] if element["id"] == "agency-logo-agency1")
        self.assertEqual(agency_logo["metadata"]["default_asset_url"], "/api/projects/demo/exact_assets/images/agency-logo.png")
        self.assertIsNone(source_logo["raw_pdf_bbox"])
        self.assertTrue(source_logo["replaceable"])

    def test_design_graph_prunes_unbacked_cover_and_source_logo_features(self):
        model = {
            "source_pdf": "/tmp/source.pdf",
            "pages": [
                {
                    "id": "page-004",
                    "page_number": 4,
                    "size": {"width": 200, "height": 100, "unit": "pt"},
                    "text_spans": [
                        {
                            "id": "p004-text-0001",
                            "text": "PLUG",
                            "bbox": {"x": 10, "y": 10, "width": 40, "height": 18},
                            "font": {"family": "Heading", "size": 40},
                            "typography_role": "heading",
                        }
                    ],
                    "image_boxes": [],
                }
            ],
        }
        inventory = {
            "global_systems": {},
            "pages": [
                {
                    "page_number": 4,
                    "page_purpose": "editorial",
                    "detected_features": ["cover_title", "source_facade_mark", "global_logo", "editable_text"],
                    "editable_text_blocks": [{"id": "p004-text-0001", "typography_role": "heading"}],
                }
            ],
        }

        graph = build_design_graph(model, inventory=inventory, render_metadata={"field_config": {}})

        detected = set(graph["pages"][0]["detected_features"])
        self.assertNotIn("cover_title", detected)
        self.assertNotIn("source_facade_mark", detected)
        self.assertNotIn("global_logo", detected)
        self.assertIn("editable_text", detected)

    def test_write_design_graph_persists_brochure_design_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = {
                "source_pdf": "/tmp/source.pdf",
                "coordinate_system": {"unit": "pt"},
                "pages": [{"id": "page-001", "page_number": 1, "size": {"width": 100, "height": 100, "unit": "pt"}, "text_spans": [], "image_boxes": []}],
                "inventory": {"global_systems": {}, "pages": [{"page_number": 1, "page_purpose": "cover"}]},
            }

            output_path = write_design_graph(model, Path(tmp) / "brochure.design.json")

            self.assertTrue(output_path.exists())
            saved = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema"], "brochure-maker.design-graph.v1")
            self.assertEqual(saved["pages"][0]["page_number"], 1)

    def test_image_xref_rects_are_used_when_text_dict_misses_image_blocks(self):
        class FakeParent:
            def extract_image(self, xref):
                return {"image": b"fake-image", "ext": "png", "width": 80, "height": 60}

        class FakePage:
            parent = FakeParent()

            def get_images(self, full=True):
                return [(42,)]

            def get_image_rects(self, xref):
                return [fitz.Rect(10, 12, 90, 72)]

        with tempfile.TemporaryDirectory() as tmp:
            existing: list[dict] = []
            count = pdf_exact_layout._append_xref_image_boxes(
                page=FakePage(),
                page_number=2,
                images_dir=Path(tmp),
                min_image_area=1,
                existing=existing,
                image_counter=0,
            )

            self.assertEqual(count, 1)
            self.assertEqual(existing[0]["bbox"], {"x": 10.0, "y": 12.0, "width": 80.0, "height": 60.0})
            self.assertEqual(existing[0]["extraction_method"], "PyMuPDF image xref placement")
            self.assertTrue(Path(existing[0]["path"]).exists())

    def test_high_specification_body_copy_is_not_amenities_region(self):
        text_spans = [
            {"id": "title", "text": "Availability", "bbox": {"x": 22.0, "y": 114.0, "width": 148.0, "height": 36.0}},
            {
                "id": "copy1",
                "text": "Finished to a high specification, it provides an",
                "bbox": {"x": 22.0, "y": 221.0, "width": 256.0, "height": 14.0},
            },
            {
                "id": "copy2",
                "text": "environment that balances creativity with professionalism.",
                "bbox": {"x": 22.0, "y": 238.0, "width": 256.0, "height": 14.0},
            },
        ]

        regions = pdf_exact_layout._infer_semantic_regions(text_spans, page_number=3, page_width=840, page_height=595)

        self.assertNotIn("amenities", {region.get("kind") for region in regions})

    def test_transport_copy_without_distributed_labels_is_map_context_not_map(self):
        text_spans = [
            {"id": "h", "text": "Work within the creative heart of Clerkenwell", "bbox": {"x": 34.0, "y": 60.0, "width": 220.0, "height": 36.0}},
            {"id": "p1", "text": "Just a five minute walk from Farringdon Station,", "bbox": {"x": 34.0, "y": 172.0, "width": 290.0, "height": 14.0}},
            {"id": "p2", "text": "with Elizabeth Line, Thameslink and Underground connections,", "bbox": {"x": 34.0, "y": 186.0, "width": 290.0, "height": 14.0}},
            {"id": "p3", "text": "surrounded by independent cafes and restaurants.", "bbox": {"x": 34.0, "y": 414.0, "width": 290.0, "height": 14.0}},
        ]
        compact = pdf_exact_layout._compact_semantic_text(" ".join(span["text"] for span in text_spans))
        purpose = pdf_exact_layout._infer_page_purpose(
            compact,
            page_number=2,
            page_count=5,
            text_spans=text_spans,
            image_boxes=[{"bbox": {"x": 360, "y": 0, "width": 450, "height": 680}}],
            semantic_regions=[],
        )

        self.assertNotEqual(purpose["purpose"], "connectivity map")
        self.assertNotIn("map", purpose["features"])
        self.assertIn("map_context", purpose["features"])

    def test_report_page_with_single_service_word_is_not_service_icon_page(self):
        text_spans = [
            {
                "id": "p1",
                "text": "Other than in the case of emergency or for maintenance purposes, the external roof terrace lighting shall only be used during approved hours.",
                "bbox": {"x": 90.0, "y": 120.0, "width": 560.0, "height": 40.0},
            },
            {
                "id": "p2",
                "text": "The applicant must comply with the planning conditions and approved drawings.",
                "bbox": {"x": 90.0, "y": 170.0, "width": 520.0, "height": 20.0},
            },
        ]
        compact = pdf_exact_layout._compact_semantic_text(" ".join(span["text"] for span in text_spans))
        purpose = pdf_exact_layout._infer_page_purpose(
            compact,
            page_number=40,
            page_count=43,
            text_spans=text_spans,
            image_boxes=[],
            semantic_regions=[],
        )

        self.assertNotEqual(purpose["purpose"], "services and customisations")
        self.assertNotIn("service_icons", purpose["features"])

    def test_real_service_heading_still_marks_service_icon_page(self):
        text_spans = [
            {"id": "h", "text": "C O R E  S E R V I C E S", "bbox": {"x": 40.0, "y": 80.0, "width": 260.0, "height": 40.0}},
            {"id": "a", "text": "Daily Cleaning & Waste Management", "bbox": {"x": 40.0, "y": 200.0, "width": 180.0, "height": 20.0}},
            {"id": "b", "text": "Maintenance & Repairs", "bbox": {"x": 280.0, "y": 200.0, "width": 160.0, "height": 20.0}},
        ]
        compact = pdf_exact_layout._compact_semantic_text(" ".join(span["text"] for span in text_spans))
        purpose = pdf_exact_layout._infer_page_purpose(
            compact,
            page_number=5,
            page_count=8,
            text_spans=text_spans,
            image_boxes=[],
            semantic_regions=[],
        )

        self.assertEqual(purpose["purpose"], "services and customisations")
        self.assertIn("service_icons", purpose["features"])

    def test_location_map_with_numbered_amenities_list_is_not_icon_grid(self):
        text_spans = [
            {"id": "h", "text": "LOCATION / CONNECTIVITY", "bbox": {"x": 40.0, "y": 60.0, "width": 240.0, "height": 24.0}},
            {"id": "a", "text": "AMENITIES", "bbox": {"x": 500.0, "y": 88.0, "width": 120.0, "height": 18.0}},
            {"id": "l1", "text": "01 The Royal Exchange", "bbox": {"x": 500.0, "y": 140.0, "width": 160.0, "height": 14.0}},
            {"id": "l2", "text": "02 Rosslyn Coffee", "bbox": {"x": 500.0, "y": 160.0, "width": 150.0, "height": 14.0}},
            {"id": "l3", "text": "Pizza Pilgrims", "bbox": {"x": 650.0, "y": 330.0, "width": 130.0, "height": 14.0}},
            {"id": "frag", "text": "Q", "bbox": {"x": 610.0, "y": 350.0, "width": 8.0, "height": 14.0}},
            {"id": "s", "text": "Bank underground station", "bbox": {"x": 650.0, "y": 220.0, "width": 150.0, "height": 14.0}},
            {"id": "m", "text": "Moorgate", "bbox": {"x": 720.0, "y": 90.0, "width": 90.0, "height": 14.0}},
        ]
        regions = pdf_exact_layout._infer_semantic_regions(text_spans, page_number=6, page_width=840, page_height=595)
        compact = pdf_exact_layout._compact_semantic_text(" ".join(span["text"] for span in text_spans))
        purpose = pdf_exact_layout._infer_page_purpose(
            compact,
            page_number=6,
            page_count=7,
            text_spans=text_spans,
            image_boxes=[{"bbox": {"x": 320, "y": 0, "width": 220, "height": 140}}],
            semantic_regions=[{"kind": "map", "span_ids": ["s", "m"]}, *regions],
        )

        self.assertNotIn("amenities", {region.get("kind") for region in regions})
        self.assertEqual(purpose["purpose"], "location introduction")
        self.assertIn("map", purpose["features"])
        self.assertIn("map_context", purpose["features"])
        self.assertNotIn("amenity_icons", purpose["features"])

    def test_semantic_map_region_excludes_body_copy_and_sidebar_lists(self):
        text_spans = [
            {
                "id": "body-1",
                "text": "Hammersmith is located approximately 4.5 miles west of Central London, forming the western gateway to the West End.",
                "bbox": {"x": 70.0, "y": 72.0, "width": 318.0, "height": 42.0},
            },
            {
                "id": "body-2",
                "text": "Significant public and private sector investment has transformed the area into a true mixed-use destination.",
                "bbox": {"x": 445.0, "y": 72.0, "width": 320.0, "height": 42.0},
            },
            *[
                {
                    "id": f"list-{index}",
                    "text": label,
                    "bbox": {"x": 84.0, "y": 330.0 + index * 16.0, "width": 210.0, "height": 12.0},
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
            {"id": "map-1", "text": "RAVENSCOURT PARK", "bbox": {"x": 438.0, "y": 520.0, "width": 110.0, "height": 13.0}},
            {"id": "map-2", "text": "HAMMERSMITH STATION", "bbox": {"x": 770.0, "y": 595.0, "width": 132.0, "height": 13.0}},
            {"id": "map-3", "text": "GREAT WEST RD", "bbox": {"x": 398.0, "y": 708.0, "width": 96.0, "height": 13.0}},
            {"id": "map-4", "text": "TALGARTH RD", "bbox": {"x": 940.0, "y": 720.0, "width": 94.0, "height": 13.0}},
            {"id": "map-5", "text": "KENSINGTON OLYMPIA", "bbox": {"x": 980.0, "y": 415.0, "width": 135.0, "height": 13.0}},
            {"id": "map-6", "text": "255 HAMMERSMITH ROAD", "bbox": {"x": 770.0, "y": 475.0, "width": 170.0, "height": 13.0}},
        ]

        map_spans = pdf_exact_layout._map_label_spans_for_region(text_spans, page_width=1224, page_height=843)
        map_ids = {span["id"] for span in map_spans}
        regions = pdf_exact_layout._infer_semantic_regions(text_spans, page_number=5, page_width=1224, page_height=843)
        map_regions = [region for region in regions if region.get("kind") == "map"]

        self.assertTrue({"map-2", "map-4", "map-6"}.issubset(map_ids))
        self.assertFalse(any(span_id.startswith("body-") or span_id.startswith("list-") for span_id in map_ids))
        self.assertEqual(len(map_regions), 1)
        self.assertFalse(any(span_id.startswith("body-") or span_id.startswith("list-") for span_id in map_regions[0]["span_ids"]))

    def test_table_like_property_page_does_not_infer_semantic_map_region(self):
        text_spans = [
            {"id": "h", "text": "INVESTMENT SUMMARY", "bbox": {"x": 50.0, "y": 70.0, "width": 260.0, "height": 28.0}},
            {"id": "p", "text": "Proposal", "bbox": {"x": 50.0, "y": 150.0, "width": 100.0, "height": 16.0}},
            {"id": "s", "text": "Schedule", "bbox": {"x": 500.0, "y": 150.0, "width": 100.0, "height": 16.0}},
            {"id": "n", "text": "Status Notes", "bbox": {"x": 720.0, "y": 150.0, "width": 110.0, "height": 16.0}},
            {"id": "a", "text": "Hammersmith Road", "bbox": {"x": 50.0, "y": 260.0, "width": 140.0, "height": 14.0}},
            {"id": "b", "text": "London", "bbox": {"x": 50.0, "y": 284.0, "width": 70.0, "height": 14.0}},
            {"id": "c", "text": "Development rights application ongoing", "bbox": {"x": 720.0, "y": 284.0, "width": 210.0, "height": 14.0}},
        ]

        regions = pdf_exact_layout._infer_semantic_regions(text_spans, page_number=13, page_width=1224, page_height=843)

        self.assertNotIn("map", {region.get("kind") for region in regions})

    def test_floor_plan_images_are_not_reported_as_photo_regions(self):
        text_spans = [
            {"id": "h", "text": "4th Floor", "bbox": {"x": 36.0, "y": 90.0, "width": 130.0, "height": 42.0}},
            {"id": "a", "text": "1,801 SQ FT", "bbox": {"x": 36.0, "y": 156.0, "width": 90.0, "height": 14.0}},
            {"id": "d", "text": "32 Desks", "bbox": {"x": 36.0, "y": 210.0, "width": 60.0, "height": 14.0}},
        ]
        page = {
            "page_number": 3,
            "size": {"width": 595.0, "height": 842.0, "unit": "pt"},
            "text_spans": text_spans,
            "image_boxes": [
                {
                    "id": "p003-image-0001",
                    "bbox": {"x": 200.0, "y": 80.0, "width": 300.0, "height": 640.0},
                    "path": "plan.png",
                    "source_width": 600,
                    "source_height": 1280,
                }
            ],
            "semantic_regions": [],
        }

        inventory = pdf_exact_layout._inventory_page(page, 2, 8)

        self.assertEqual(inventory["page_purpose"], "floor plan and space plan")
        self.assertIn("space_plan", inventory["detected_features"])
        self.assertNotIn("photo_regions", inventory["detected_features"])
        self.assertEqual(inventory["photo_regions"], [])
        self.assertEqual(inventory["space_plan_regions"][0]["candidate_image_regions"], ["p003-image-0001"])
        self.assertIn("space-plan replacement and metadata text", inventory["editable_elements"])
        self.assertNotIn("photo/image slots", inventory["editable_elements"])

    def test_contact_terms_schedule_does_not_create_space_plan_inventory(self):
        text_spans = [
            {"id": "heading", "text": "FURTHER INFORMATION", "bbox": {"x": 36.0, "y": 42.0, "width": 160.0, "height": 18.0}},
            {"id": "floor", "text": "FLOOR", "bbox": {"x": 520.0, "y": 130.0, "width": 48.0, "height": 14.0}},
            {"id": "area", "text": "SQ FT", "bbox": {"x": 590.0, "y": 130.0, "width": 48.0, "height": 14.0}},
            {"id": "status", "text": "STATUS", "bbox": {"x": 660.0, "y": 130.0, "width": 58.0, "height": 14.0}},
            {"id": "row", "text": "4th Floor 1,985 Sq Ft Available", "bbox": {"x": 520.0, "y": 170.0, "width": 180.0, "height": 14.0}},
            {"id": "agent", "text": "tom.boggis@bbgreal.com", "bbox": {"x": 36.0, "y": 390.0, "width": 160.0, "height": 14.0}},
            {"id": "terms", "text": "MISREPRESENTATION ACT", "bbox": {"x": 36.0, "y": 735.0, "width": 190.0, "height": 14.0}},
        ]
        page = {
            "page_number": 8,
            "size": {"width": 842.0, "height": 595.0, "unit": "pt"},
            "text_spans": text_spans,
            "image_boxes": [],
            "image_regions": [
                {
                    "id": "space-plan-p8-detected-1",
                    "role": "space-plan",
                    "bbox": {"left": 520.0, "top": 120.0, "width": 230.0, "height": 430.0},
                    "confidence": 0.9,
                }
            ],
            "semantic_regions": [{"kind": "contacts"}, {"kind": "agency_logos"}],
        }

        inventory = pdf_exact_layout._inventory_page(page, 7, 8)

        self.assertEqual(inventory["page_purpose"], "contacts and terms")
        self.assertEqual(inventory["space_plan_regions"], [])
        self.assertNotIn("space_plan", inventory["detected_features"])
        self.assertNotIn("floor_metadata", inventory["detected_features"])
        self.assertNotIn("space-plan", {region.get("role") for region in inventory["image_regions"]})

    def test_text_only_final_legal_contacts_do_not_require_agency_logo(self):
        text_spans = [
            {
                "id": "body1",
                "text": "Please make sure that the street number and building name are clearly displayed.",
                "bbox": {"x": 103.0, "y": 130.0, "width": 460.0, "height": 12.0},
                "font": {"family": "ArialMT", "size": 10.98},
            },
            {
                "id": "body2",
                "text": "The development will result in changes to road access points and pavement levels.",
                "bbox": {"x": 103.0, "y": 220.0, "width": 460.0, "height": 12.0},
                "font": {"family": "ArialMT", "size": 10.98},
            },
            {
                "id": "contact1",
                "text": "For more advice, please email AskHighways@westminster.gov.uk.",
                "bbox": {"x": 103.0, "y": 320.0, "width": 430.0, "height": 12.0},
                "font": {"family": "ArialMT", "size": 10.98},
            },
            {
                "id": "contact2",
                "text": "Please email wasteplanning@westminster.gov.uk for advice.",
                "bbox": {"x": 103.0, "y": 356.0, "width": 430.0, "height": 12.0},
                "font": {"family": "ArialMT", "size": 10.98},
            },
            {
                "id": "terms",
                "text": "Please note: the full text for informatives can be found in the Council's policies.",
                "bbox": {"x": 93.0, "y": 633.0, "width": 440.0, "height": 12.0},
                "font": {"family": "ArialMT", "size": 10.98},
            },
        ]
        page = {
            "page_number": 43,
            "size": {"width": 612.0, "height": 792.0, "unit": "pt"},
            "text_spans": text_spans,
            "image_boxes": [],
            "image_regions": [],
            "semantic_regions": [
                {"kind": "contacts", "span_ids": ["contact1", "contact2"]},
                {"kind": "agency_logos", "span_ids": ["body1", "body2"]},
            ],
        }

        inventory = pdf_exact_layout._inventory_page(page, 42, 43)

        self.assertEqual(inventory["page_purpose"], "contacts and terms")
        self.assertIn("agent_contacts", inventory["detected_features"])
        self.assertIn("legal_copy", inventory["detected_features"])
        self.assertNotIn("agency_logos", inventory["detected_features"])
        self.assertNotIn("agency logo replacement", inventory["editable_elements"])

    def test_short_prominent_name_above_contact_is_agency_logo_evidence(self):
        text_spans = [
            {
                "id": "logo",
                "text": "WESTMINSTER",
                "bbox": {"x": 100.0, "y": 110.0, "width": 150.0, "height": 24.0},
                "font": {"family": "Arial-BoldMT", "size": 18.0, "is_bold": True},
            },
            {
                "id": "contact",
                "text": "planning@example.com",
                "bbox": {"x": 100.0, "y": 210.0, "width": 180.0, "height": 12.0},
                "font": {"family": "ArialMT", "size": 10.98},
            },
        ]
        semantic_regions = pdf_exact_layout._infer_semantic_regions(text_spans, 4, 612.0, 792.0)
        page = {
            "page_number": 4,
            "size": {"width": 612.0, "height": 792.0, "unit": "pt"},
            "text_spans": text_spans,
            "image_boxes": [],
            "image_regions": [],
            "semantic_regions": semantic_regions,
        }

        inventory = pdf_exact_layout._inventory_page(page, 3, 8)

        self.assertIn("agency_logos", {region["kind"] for region in semantic_regions})
        self.assertIn("agency_logos", inventory["detected_features"])

    def test_plan_number_decision_letter_does_not_create_space_plan_inventory(self):
        text_spans = [
            {"id": "heading", "text": "DRAFT DECISION LETTER", "bbox": {"x": 240.0, "y": 84.0, "width": 160.0, "height": 16.0}},
            {"id": "label", "text": "Plan Nos:", "bbox": {"x": 77.0, "y": 238.0, "width": 70.0, "height": 14.0}},
            {
                "id": "row1",
                "text": "SECOND FLOOR PLAN; 4468-DLG-ZZ-03-DR-A-EX_1004 A-EXISTING THIRD",
                "bbox": {"x": 160.0, "y": 296.0, "width": 394.0, "height": 12.0},
            },
            {
                "id": "row2",
                "text": "PLAN; 4468-DLG-ZZ-05-DR-A-EX_1006 B-EXISTING ROOF PLAN; 4468-DLG-ZZ-",
                "bbox": {"x": 160.0, "y": 322.0, "width": 408.0, "height": 12.0},
            },
            {
                "id": "row3",
                "text": "4468-DLG-ZZ-00-DR-A-PL_1100 D-PROPOSED LOWER GROUND FLOOR PLAN;",
                "bbox": {"x": 160.0, "y": 406.0, "width": 402.0, "height": 12.0},
            },
        ]
        page = {
            "page_number": 32,
            "size": {"width": 612.0, "height": 792.0, "unit": "pt"},
            "text_spans": text_spans,
            "image_boxes": [],
            "image_regions": [
                {
                    "id": "space-plan-p32-detected-1",
                    "role": "space-plan",
                    "bbox": {"left": 300.0, "top": 260.0, "width": 300.0, "height": 220.0},
                    "confidence": 0.9,
                }
            ],
            "semantic_regions": [],
        }

        inventory = pdf_exact_layout._inventory_page(page, 31, 43)

        self.assertEqual(inventory["space_plan_regions"], [])
        self.assertNotIn("space_plan", inventory["detected_features"])
        self.assertNotIn("floor_metadata", inventory["detected_features"])
        self.assertNotIn("space-plan", {region.get("role") for region in inventory["image_regions"]})

    def test_footer_address_labels_do_not_supply_map_distribution(self):
        text_spans = [
            {"id": "s", "text": "Central Station", "bbox": {"x": 180.0, "y": 120.0, "width": 100.0, "height": 14.0}},
            {"id": "m", "text": "North Market", "bbox": {"x": 120.0, "y": 250.0, "width": 90.0, "height": 14.0}},
            {"id": "f", "text": "120 Bridge Street", "bbox": {"x": 500.0, "y": 735.0, "width": 180.0, "height": 18.0}},
        ]

        self.assertFalse(pdf_exact_layout._has_distributed_map_label_spans(text_spans, page_width=840, page_height=793))

    def test_inventory_classifies_large_bold_lowercase_heading_spans(self):
        role = pdf_exact_layout._span_typography_role(
            2,
            {
                "text": "creative heart of",
                "font": {"family": "Garet-Bold", "is_bold": True, "size": 25.0},
            },
        )

        self.assertEqual(role, "heading")

    def test_inventory_groups_vertical_cover_title_glyphs_as_title(self):
        spans = []
        for index, (letter, x, y) in enumerate(
            [
                ("S", 142.0, 394.0),
                ("e", 142.0, 358.0),
                ("k", 142.0, 320.0),
                ("f", 142.0, 285.0),
                ("o", 142.0, 248.0),
                ("r", 142.0, 211.0),
                ("d", 142.0, 173.0),
                ("e", 142.0, 137.0),
                ("S", 173.0, 318.0),
                ("t", 173.0, 283.0),
                ("r", 173.0, 245.0),
                ("e", 173.0, 209.0),
                ("e", 173.0, 173.0),
                ("t", 173.0, 137.0),
            ],
            start=1,
        ):
            spans.append(
                {
                    "id": f"p001-text-{index:04d}",
                    "text": letter,
                    "bbox": {"x": x, "y": y, "width": 31.0, "height": 12.0},
                    "font": {"family": "ExtractedTitleFont", "is_bold": True, "size": 31.0},
                    "color": "#f1eff3",
                }
            )

        title_ids = pdf_exact_layout._cover_vertical_title_glyph_ids(1, spans)
        grouped = pdf_exact_layout._group_spans_by_typography_role(1, spans)

        self.assertEqual(len(title_ids), len(spans))
        self.assertEqual({span["id"] for span in grouped["title"]}, {span["id"] for span in spans})
        self.assertNotIn("heading", grouped)

    def test_contact_page_agency_logo_is_not_classified_as_source_facade_mark(self):
        source_regions = pdf_exact_layout._source_logo_inventory(
            page_number=5,
            purpose={"purpose": "contacts and terms", "features": ["agent_contacts", "agency_logos"]},
            page={},
            text_spans=[],
        )

        self.assertEqual(source_regions, [])

    @staticmethod
    def _write_fixture_pdf(pdf_path: Path) -> None:
        doc = fitz.open()
        page = doc.new_page(width=240, height=160)
        page.draw_rect(
            fitz.Rect(0, 0, 240, 160),
            fill=(0.93, 0.88, 0.75),
            color=(0.93, 0.88, 0.75),
            overlay=False,
        )
        page.draw_rect(
            fitz.Rect(18, 116, 222, 130),
            fill=(0.25, 0.45, 0.55),
            color=(0.25, 0.45, 0.55),
        )
        page.insert_text(
            fitz.Point(24, 38),
            "13 Austin Friars",
            fontsize=18,
            fontname="helv",
            color=(0.2, 0.4, 0.6),
        )

        image = Image.new("RGB", (20, 16), (200, 70, 60))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        page.insert_image(fitz.Rect(150, 54, 200, 96), stream=stream.getvalue())

        doc.save(pdf_path)
        doc.close()


if __name__ == "__main__":
    unittest.main()
