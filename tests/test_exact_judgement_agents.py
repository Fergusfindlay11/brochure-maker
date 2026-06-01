from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brochure_maker.exact_judgement_agents import build_page_judgement_agents


class TestExactJudgementAgents(unittest.TestCase):
    def test_editability_critic_does_not_double_count_graph_media_duplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agents = build_page_judgement_agents(
                project_id="demo",
                page_number=2,
                page_dir=Path(temp_dir),
                score=99.0,
                accepted=True,
                assessment_page={"accepted": True, "findings": []},
                graph_page={
                    "elements": [
                        {"type": "image", "role": "hero-photo", "bbox": {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.4}},
                        {"type": "image", "role": "photo-region", "bbox": {"x": 0.5, "y": 0.1, "width": 0.2, "height": 0.2}},
                    ]
                },
                layout_page={
                    "_metadata_image_regions_primary": True,
                    "image_regions": [
                        {
                            "role": "hero-photo",
                            "bbox": {"left": 100, "top": 100, "width": 300, "height": 400},
                            "editable": True,
                            "replaceable": True,
                        },
                        {
                            "role": "photo-region",
                            "bbox": {"left": 500, "top": 100, "width": 200, "height": 200},
                            "editable": True,
                            "replaceable": True,
                        },
                    ]
                },
                inventory_page={
                    "editable_text_blocks": [],
                    "image_regions": [
                        {
                            "role": "hero-photo",
                            "bbox": {"left": 75, "top": 75, "width": 225, "height": 300},
                            "editable": True,
                            "replaceable": True,
                        },
                        {
                            "role": "photo-region",
                            "bbox": {"left": 375, "top": 75, "width": 150, "height": 150},
                            "editable": True,
                            "replaceable": True,
                        },
                    ],
                },
                browser_page={
                    "assertions": {
                        "state_roundtrip_preserved": True,
                        "typed_text_font_preserved": True,
                        "media_slots_have_actionable_controls": True,
                        "image_replacement_roundtrip_preserved": True,
                        "logo_replacement_roundtrip_preserved": True,
                        "map_replacement_roundtrip_preserved": True,
                    },
                    "mediaSlots": [
                        {"kind": "image", "role": "hero-photo"},
                        {"kind": "image", "role": "photo-region"},
                    ],
                },
                evidence_files={},
                copied_images={},
                page_critique={"accepted": True, "blockers": []},
                repair_plan={"accepted": True, "tasks": []},
            )

            editability = agents["editability-critic.json"]
            self.assertTrue(editability["accepted"], editability["blockers"])
            self.assertEqual(editability["expected_browser_media_slot_kinds"], {"image": 2})

    def test_no_bbox_source_logo_hint_is_not_a_hard_media_expectation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agents = build_page_judgement_agents(
                project_id="demo",
                page_number=1,
                page_dir=Path(temp_dir),
                score=99.0,
                accepted=True,
                assessment_page={"accepted": True, "findings": []},
                graph_page={
                    "elements": [
                        {"type": "logo", "role": "source-facade-mark", "replaceable": True},
                    ]
                },
                inventory_page={"editable_text_blocks": []},
                layout_page={"image_regions": []},
                browser_page={
                    "assertions": {
                        "state_roundtrip_preserved": True,
                        "typed_text_font_preserved": True,
                        "media_slots_have_actionable_controls": True,
                        "image_replacement_roundtrip_preserved": True,
                        "logo_replacement_roundtrip_preserved": True,
                        "map_replacement_roundtrip_preserved": True,
                    },
                    "mediaSlots": [],
                },
                evidence_files={},
                copied_images={},
                page_critique={"accepted": True, "blockers": []},
                repair_plan={"accepted": True, "tasks": []},
            )

            editability = agents["editability-critic.json"]
            self.assertTrue(editability["accepted"], editability["blockers"])
            self.assertEqual(editability["expected_browser_media_slot_kinds"], {})

    def test_plain_logo_regions_do_not_require_source_logo_controls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            agents = build_page_judgement_agents(
                project_id="demo",
                page_number=13,
                page_dir=Path(temp_dir),
                score=99.0,
                accepted=True,
                assessment_page={"accepted": True, "findings": []},
                graph_page={"elements": []},
                inventory_page={"editable_text_blocks": []},
                layout_page={
                    "_metadata_image_regions_primary": True,
                    "image_regions": [
                        {
                            "role": "logo",
                            "bbox": {"left": 56, "top": 422, "width": 85, "height": 48},
                            "editable": True,
                            "replaceable": True,
                        }
                    ],
                },
                browser_page={
                    "assertions": {
                        "state_roundtrip_preserved": True,
                        "typed_text_font_preserved": True,
                        "media_slots_have_actionable_controls": True,
                        "image_replacement_roundtrip_preserved": True,
                        "logo_replacement_roundtrip_preserved": True,
                        "map_replacement_roundtrip_preserved": True,
                    },
                    "mediaSlots": [],
                },
                evidence_files={},
                copied_images={},
                page_critique={"accepted": True, "blockers": []},
                repair_plan={"accepted": True, "tasks": []},
            )

            editability = agents["editability-critic.json"]
            self.assertTrue(editability["accepted"], editability["blockers"])
            self.assertEqual(editability["expected_browser_media_slot_kinds"], {})
