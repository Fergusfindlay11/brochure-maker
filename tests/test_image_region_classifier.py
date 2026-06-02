from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from brochure_maker.image_region_classifier import classify_page_image_regions, detect_space_plan_bbox


class TestImageRegionClassifier(unittest.TestCase):
    def test_texture_side_panel_is_static_not_replaceable_image_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            background = base / "page.png"
            texture = base / "texture.png"
            Image.new("RGB", (1000, 600), "#eef3f2").save(background)
            panel = Image.new("RGB", (320, 600), "#151922")
            draw = ImageDraw.Draw(panel)
            for y in range(0, 600, 9):
                draw.line((0, y, 320, y + 2), fill="#20242d")
            panel.save(texture)

            result = classify_page_image_regions(
                page_number=5,
                width=1000,
                height=600,
                text_entries=[{"plain": "Fourth Floor 1,985 Sq Ft / 184.4 Sq M"}],
                image_slots=[
                    {
                        "id": "texture-panel",
                        "left": 690,
                        "top": 0,
                        "width": 310,
                        "height": 600,
                        "asset_path": str(texture),
                    }
                ],
                background_path=background,
            )

            self.assertEqual(result["image_slots"], [])
            self.assertEqual(result["image_regions"][0]["role"], "background-texture")
            self.assertFalse(result["image_regions"][0]["replaceable"])
            self.assertTrue(result["image_regions"][0]["locked_static"])

    def test_floor_plan_linework_becomes_space_plan_region(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            background = Path(temp_dir) / "floor-plan-page.png"
            image = Image.new("RGB", (1000, 700), "#eef3f2")
            draw = ImageDraw.Draw(image)
            draw.rectangle((720, 0, 1000, 700), fill="#111722")
            draw.rectangle((150, 155, 610, 420), outline="#7b817e", width=3)
            draw.polygon([(150, 155), (610, 155), (565, 420), (190, 385)], fill="#ead9a9", outline="#7b817e")
            for x in range(210, 570, 70):
                draw.line((x, 165, x - 20, 392), fill="#8a8d89", width=2)
            for y in range(210, 390, 45):
                draw.line((175, y, 590, y + 6), fill="#8a8d89", width=2)
            for x in range(190, 560, 90):
                draw.rectangle((x, 250, x + 36, 285), outline="#777b78", width=2)
            image.save(background)

            bbox = detect_space_plan_bbox(
                background_path=background,
                width=1000,
                height=700,
                text_entries=[{"plain": "Fourth Floor 1,985 Sq Ft / 184.4 Sq M", "top": 45}],
            )

            self.assertIsNotNone(bbox)
            assert bbox is not None
            self.assertLess(bbox["left"], 180)
            self.assertGreater(bbox["width"], 360)
            self.assertGreater(bbox["height"], 220)

            result = classify_page_image_regions(
                page_number=5,
                width=1000,
                height=700,
                text_entries=[{"plain": "Fourth Floor 1,985 Sq Ft / 184.4 Sq M", "top": 45}],
                image_slots=[],
                background_path=background,
            )
            self.assertIn("space-plan", {region["role"] for region in result["image_regions"]})

    def test_contact_schedule_linework_does_not_become_space_plan_region(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            background = Path(temp_dir) / "contact-schedule-page.png"
            image = Image.new("RGB", (1000, 700), "#eef3f2")
            draw = ImageDraw.Draw(image)
            draw.rectangle((700, 95, 980, 610), outline="#7b817e", width=3)
            for y in range(130, 590, 34):
                draw.line((710, y, 970, y), fill="#8a8d89", width=2)
            for x in range(740, 960, 55):
                draw.line((x, 105, x, 605), fill="#8a8d89", width=2)
            image.save(background)

            result = classify_page_image_regions(
                page_number=8,
                width=1000,
                height=700,
                text_entries=[
                    {"plain": "FURTHER INFORMATION", "top": 60, "left": 50},
                    {"plain": "FLOOR", "top": 130, "left": 720},
                    {"plain": "SQ FT", "top": 130, "left": 790},
                    {"plain": "STATUS", "top": 130, "left": 870},
                    {"plain": "4th Floor 1,985 Sq Ft Available", "top": 170, "left": 720},
                    {"plain": "MISREPRESENTATION ACT", "top": 620, "left": 50},
                ],
                image_slots=[],
                background_path=background,
                semantic_regions=[{"kind": "contacts"}, {"kind": "agency_logos"}],
            )

            self.assertNotIn("space-plan", {region["role"] for region in result["image_regions"]})

    def test_map_context_region_does_not_become_photo_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            background = base / "page.png"
            map_asset = base / "map.png"
            Image.new("RGB", (900, 600), "#ffffff").save(background)
            asset = Image.new("RGB", (360, 280), "#eadfb5")
            draw = ImageDraw.Draw(asset)
            for x in range(30, 340, 45):
                draw.line((x, 0, x + 20, 280), fill="#ffffff", width=5)
            for y in range(20, 260, 42):
                draw.line((0, y, 360, y + 15), fill="#ffffff", width=5)
            asset.save(map_asset)

            result = classify_page_image_regions(
                page_number=6,
                width=900,
                height=600,
                text_entries=[{"plain": "Transport links station walk time Bank Monument Liverpool Street"}],
                image_slots=[
                    {
                        "id": "map-image",
                        "left": 500,
                        "top": 80,
                        "width": 360,
                        "height": 280,
                        "asset_path": str(map_asset),
                    }
                ],
                background_path=background,
            )

            self.assertEqual(result["image_slots"], [])
            self.assertEqual(result["image_regions"][0]["role"], "map")
            self.assertTrue(result["image_regions"][0]["replaceable"])

    def test_compact_map_markers_are_static_not_logo_replacement_slots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            background = base / "page.png"
            marker = base / "marker.png"
            Image.new("RGB", (900, 600), "#f37268").save(background)
            asset = Image.new("RGB", (48, 42), "#8f2f38")
            draw = ImageDraw.Draw(asset)
            draw.ellipse((6, 6, 36, 36), fill="#ffd83b")
            draw.text((17, 14), "1", fill="#ffffff")
            asset.save(marker)

            result = classify_page_image_regions(
                page_number=13,
                width=900,
                height=600,
                text_entries=[{"plain": "Station transport underground walk time comparable schemes"}],
                image_slots=[
                    {
                        "id": "map-marker",
                        "left": 60,
                        "top": 360,
                        "width": 48,
                        "height": 42,
                        "asset_path": str(marker),
                    }
                ],
                background_path=background,
            )

            self.assertEqual(result["image_slots"], [])
            self.assertEqual(result["image_regions"][0]["role"], "static-vector-art")
            self.assertFalse(result["image_regions"][0]["replaceable"])

    def test_flat_light_panel_is_static_not_photo_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            background = base / "page.png"
            panel = base / "flat-panel.png"
            Image.new("RGB", (900, 600), "#edf4f3").save(background)
            Image.new("RGB", (420, 240), "#f8f8f6").save(panel)

            result = classify_page_image_regions(
                page_number=5,
                width=900,
                height=600,
                text_entries=[{"plain": "Space plan Fourth Floor 1,985 Sq Ft"}],
                image_slots=[
                    {
                        "id": "flat-light-panel",
                        "left": 50,
                        "top": 20,
                        "width": 420,
                        "height": 240,
                        "asset_path": str(panel),
                    }
                ],
                background_path=background,
            )

            self.assertEqual(result["image_slots"], [])
            self.assertEqual(result["image_regions"][0]["role"], "decorative-panel")
            self.assertFalse(result["image_regions"][0]["replaceable"])

    def test_compact_raster_mark_becomes_logo_not_photo_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            background = base / "page.png"
            logo = base / "logo.png"
            Image.new("RGB", (900, 600), "#edf4f3").save(background)
            asset = Image.new("RGB", (70, 40), "#f1d78a")
            draw = ImageDraw.Draw(asset)
            draw.text((8, 10), "RB", fill="#ffffff")
            asset.save(logo)

            result = classify_page_image_regions(
                page_number=5,
                width=900,
                height=600,
                text_entries=[{"plain": "Space plan Fourth Floor 1,985 Sq Ft"}],
                image_slots=[
                    {
                        "id": "small-brand-mark",
                        "left": 70,
                        "top": 520,
                        "width": 70,
                        "height": 40,
                        "asset_path": str(logo),
                    }
                ],
                background_path=background,
            )

            self.assertEqual(result["image_slots"], [])
            self.assertEqual(result["image_regions"][0]["role"], "logo")
            self.assertTrue(result["image_regions"][0]["replaceable"])

    def test_large_contact_page_image_overlapping_contacts_is_static_background(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            background = base / "page.png"
            asset = base / "contact-texture.png"
            Image.new("RGB", (1000, 600), "#111827").save(background)
            texture = Image.new("RGB", (520, 460), "#242832")
            draw = ImageDraw.Draw(texture)
            for y in range(0, 460, 11):
                draw.line((0, y, 520, y + 2), fill="#303541")
            texture.save(asset)

            result = classify_page_image_regions(
                page_number=7,
                width=1000,
                height=600,
                text_entries=[
                    {"plain": "FURTHER INFORMATION", "left": 70, "top": 44},
                    {"plain": "Viewings", "left": 70, "top": 170},
                    {"plain": "Tom Boggis", "left": 560, "top": 250},
                    {"plain": "M", "left": 560, "top": 285},
                    {"plain": "07795 070 676", "left": 590, "top": 285},
                    {"plain": "E", "left": 560, "top": 315},
                    {"plain": "tom.boggis@bbgreal.com", "left": 590, "top": 315},
                ],
                image_slots=[
                    {
                        "id": "contact-background-image",
                        "left": 520,
                        "top": 70,
                        "width": 460,
                        "height": 480,
                        "asset_path": str(asset),
                    }
                ],
                background_path=background,
            )

            self.assertEqual(result["image_slots"], [])
            self.assertEqual(result["image_regions"][0]["role"], "background-texture")
            self.assertFalse(result["image_regions"][0]["replaceable"])
            self.assertGreaterEqual(result["image_regions"][0]["source_evidence"]["text_overlap"]["contact_like_entries"], 3)

    def test_three_photo_slots_are_tagged_as_photo_grid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            background = base / "page.png"
            Image.new("RGB", (900, 600), "#111827").save(background)
            slots = []
            for index, colour in enumerate(("#b84b3c", "#3c7fb8", "#4da45d"), start=1):
                asset = base / f"photo-{index}.png"
                image = Image.new("RGB", (180, 130), colour)
                draw = ImageDraw.Draw(image)
                draw.ellipse((30, 20, 150, 110), fill="#f4d06f")
                image.save(asset)
                slots.append(
                    {
                        "id": f"photo-{index}",
                        "left": 60 + ((index - 1) * 230),
                        "top": 160,
                        "width": 190,
                        "height": 140,
                        "asset_path": str(asset),
                    }
                )

            result = classify_page_image_regions(
                page_number=6,
                width=900,
                height=600,
                text_entries=[{"plain": "Location and connectivity"}],
                image_slots=slots,
                background_path=background,
            )

            self.assertEqual(len(result["image_slots"]), 3)
            self.assertEqual({slot["image_role"] for slot in result["image_slots"]}, {"photo-grid"})
            self.assertEqual({region["role"] for region in result["image_regions"]}, {"photo-grid"})

    def test_forced_vector_artwork_becomes_replaceable_image_slot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            background = base / "page.png"
            Image.new("RGB", (1000, 600), "#e3e2d6").save(background)

            result = classify_page_image_regions(
                page_number=1,
                width=1000,
                height=600,
                text_entries=[{"plain": "ANCHOR HOUSE"}],
                image_slots=[
                    {
                        "id": "cover-artwork",
                        "left": 40,
                        "top": 180,
                        "width": 920,
                        "height": 360,
                        "asset_url": "/asset.png",
                        "candidate_role": "artwork-image",
                        "fit": "cover",
                    }
                ],
                background_path=background,
            )

            self.assertEqual(len(result["image_slots"]), 1)
            self.assertEqual(result["image_slots"][0]["image_role"], "artwork-image")
            self.assertEqual(result["image_regions"][0]["role"], "artwork-image")
            self.assertTrue(result["image_regions"][0]["replaceable"])


if __name__ == "__main__":
    unittest.main()
