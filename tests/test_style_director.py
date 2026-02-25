"""Tests for the style director — deterministic style token generation."""

import pytest
from brochure_maker.map_pipeline.models import MapStyleGenerateRequestV1
from brochure_maker.map_pipeline.style_director import generate_style, STYLE_VERSION


class TestStyleDeterminism:
    """Style endpoint must return identical tokens for identical inputs and version."""

    def test_identical_inputs_produce_identical_tokens(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E")
        r1 = generate_style(req)
        r2 = generate_style(req)

        assert r1.style_hash == r2.style_hash
        assert r1.style_tokens.model_dump() == r2.style_tokens.model_dump()

    def test_different_colours_produce_different_tokens(self):
        r1 = generate_style(MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E"))
        r2 = generate_style(MapStyleGenerateRequestV1(brochure_primary_hex="#1A5276"))

        assert r1.style_hash != r2.style_hash

    def test_style_version_included(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E")
        resp = generate_style(req)
        assert resp.style_version == STYLE_VERSION

    def test_hash_is_nonempty_hex(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E")
        resp = generate_style(req)
        assert len(resp.style_hash) == 16
        int(resp.style_hash, 16)  # Should not raise

    def test_three_digit_hex_normalised(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#abc")
        resp = generate_style(req)
        assert resp.style_tokens.base_hex == "#aabbcc"


class TestStyleContrast:
    """Style tokens must have adequate contrast."""

    def test_light_base_has_dark_text(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#F0E6D8")
        resp = generate_style(req)
        assert resp.style_tokens.is_light_base is True
        # text_primary should be darker than base
        from brochure_maker.map_pipeline.style_director import _luminance
        assert _luminance(resp.style_tokens.text_primary) < _luminance(resp.style_tokens.base_hex)

    def test_dark_base_has_light_text(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#1A2530")
        resp = generate_style(req)
        assert resp.style_tokens.is_light_base is False
        from brochure_maker.map_pipeline.style_director import _luminance
        assert _luminance(resp.style_tokens.text_primary) > _luminance(resp.style_tokens.base_hex)


class TestStyleValidation:
    """Style validation and fixer rules."""

    def test_validation_report_present(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E")
        resp = generate_style(req)
        assert resp.validation_report is not None

    def test_valid_hex_passes(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#FF5500")
        resp = generate_style(req)
        assert resp.validation_report.valid or len(resp.validation_report.fixes_applied) == 0

    def test_invalid_hex_rejected(self):
        with pytest.raises(Exception):
            MapStyleGenerateRequestV1(brochure_primary_hex="not-a-colour")


class TestStyleTokenStructure:
    """Style tokens contain all required fields."""

    def test_roads_tokens(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E")
        resp = generate_style(req)
        roads = resp.style_tokens.roads
        assert roads.minor_casing
        assert roads.minor_fill
        assert roads.major_casing
        assert roads.major_fill
        assert roads.minor_casing_width > 0
        assert roads.major_casing_width > roads.minor_casing_width

    def test_label_tokens(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E")
        resp = generate_style(req)
        labels = resp.style_tokens.labels
        assert labels.street_colour
        assert labels.poi_colour
        assert labels.park_colour
        assert labels.water_colour
        assert labels.street_font_size > 0

    def test_marker_tokens(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E")
        resp = generate_style(req)
        markers = resp.style_tokens.markers
        assert markers.building_fill
        assert markers.building_stroke
        assert markers.station_roundel_colour == "#CC3333"

    def test_feature_tokens(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E")
        resp = generate_style(req)
        features = resp.style_tokens.features
        assert features.park_fill
        assert features.water_stroke
        assert 0 < features.park_opacity <= 1
        assert 0 < features.water_opacity <= 1

    def test_font_families(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#B8714E")
        resp = generate_style(req)
        assert "serif" in resp.style_tokens.font_families
        assert "sans" in resp.style_tokens.font_families
