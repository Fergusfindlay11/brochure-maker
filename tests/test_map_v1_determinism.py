import unittest

from brochure_maker.map_v1.label_engine import LabelCandidate, place_labels
from brochure_maker.map_v1.models import MapStyleGenerateRequestV1
from brochure_maker.map_v1.style_director import generate_style_tokens
from brochure_maker.map_v1.utils import contrast_ratio


class TestMapV1Determinism(unittest.TestCase):
    def test_style_generation_is_deterministic(self):
        payload = MapStyleGenerateRequestV1(
            brochure_primary_hex="#B8714E",
            vibe_text="quiet luxury warm neutrals",
            style_image_ref="",
            project_id="abc123",
        )

        a = generate_style_tokens(payload)
        b = generate_style_tokens(payload)

        self.assertEqual(a.style_hash, b.style_hash)
        self.assertEqual(a.style_tokens.model_dump(), b.style_tokens.model_dump())

    def test_style_meets_label_contrast_floor(self):
        payload = MapStyleGenerateRequestV1(
            brochure_primary_hex="#D7C5AF",
            vibe_text="minimal editorial",
        )
        style = generate_style_tokens(payload)
        ratio = contrast_ratio(
            style.style_tokens.palette.label_text,
            style.style_tokens.palette.label_halo,
        )
        self.assertGreaterEqual(ratio, 4.5)

    def test_label_engine_pushes_before_drop(self):
        style = generate_style_tokens(
            MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E", vibe_text="")
        ).style_tokens

        candidates = [
            LabelCandidate(
                text=f"STATION {idx}",
                label_class="station",
                anchor_x=150.0,
                anchor_y=120.0,
                priority=120 - idx,
                font_size=9.0,
                critical=True,
            )
            for idx in range(6)
        ]

        result = place_labels(candidates, style, width=320, height=220)

        self.assertGreater(result.placed_labels, 0)
        self.assertGreaterEqual(result.pushed_labels, 1)


if __name__ == "__main__":
    unittest.main()
