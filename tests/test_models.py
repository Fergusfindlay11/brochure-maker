"""Tests for Pydantic models — API validation."""

import pytest
from brochure_maker.map_pipeline.models import (
    MapStyleGenerateRequestV1,
    MapRenderRequestV1,
    StyleTokens,
    RoadStyleTokens,
    LabelStyleTokens,
    MarkerStyleTokens,
    FeatureStyleTokens,
    MapCenter,
    MapExtent,
    MapOutput,
    MapContent,
)


class TestStyleGenerateRequest:
    """Validate style generation request model."""

    def test_valid_hex(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#FF5500")
        assert req.brochure_primary_hex == "#ff5500"

    def test_three_digit_hex_normalised(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#abc")
        assert req.brochure_primary_hex == "#aabbcc"

    def test_invalid_hex_raises(self):
        with pytest.raises(Exception):
            MapStyleGenerateRequestV1(brochure_primary_hex="not-a-colour")

    def test_optional_fields(self):
        req = MapStyleGenerateRequestV1(brochure_primary_hex="#FF5500")
        assert req.vibe_text is None
        assert req.style_image_ref is None


class TestRenderRequest:
    """Validate render request model."""

    def _make_tokens(self):
        return StyleTokens(
            base_hex="#B8714E",
            primary_hex="#B8714E",
            is_light_base=True,
            text_primary="#1a1a1a",
            text_secondary="#555555",
            text_stroke="rgba(184,113,78,0.85)",
            roads=RoadStyleTokens(
                minor_casing="#815038",
                minor_fill="white",
                major_casing="#815038",
                major_fill="white",
            ),
            labels=LabelStyleTokens(
                street_colour="#555555",
                street_halo="rgba(184,113,78,0.85)",
                poi_colour="#1a1a1a",
                poi_halo="rgba(184,113,78,0.85)",
                park_colour="#225b2b",
                water_colour="#666666",
                neighbourhood_colour="#1a1a1a",
            ),
            markers=MarkerStyleTokens(
                building_fill="#B8714E",
                building_stroke="#93593e",
                building_label_colour="#1a1a1a",
            ),
            features=FeatureStyleTokens(
                park_fill="#729e5a",
                water_stroke="#5a7e93",
                block_fill="#a96848",
            ),
        )

    def test_valid_request(self):
        tokens = self._make_tokens()
        req = MapRenderRequestV1(
            center=MapCenter(lat=51.5074, lng=-0.1278),
            style_tokens=tokens,
        )
        assert req.center.lat == 51.5074
        assert req.center.lng == -0.1278

    def test_rejects_vibe_text(self):
        tokens = self._make_tokens()
        with pytest.raises(Exception):
            MapRenderRequestV1(
                center={"lat": 51.5074, "lng": -0.1278},
                style_tokens=tokens.model_dump(),
                vibe_text="modern and sleek",
            )

    def test_rejects_style_image_ref(self):
        tokens = self._make_tokens()
        with pytest.raises(Exception):
            MapRenderRequestV1(
                center={"lat": 51.5074, "lng": -0.1278},
                style_tokens=tokens.model_dump(),
                style_image_ref="img123",
            )

    def test_default_extent(self):
        tokens = self._make_tokens()
        req = MapRenderRequestV1(
            center=MapCenter(lat=51.5074, lng=-0.1278),
            style_tokens=tokens,
        )
        assert req.extent.radius_m == 350
        assert req.extent.width_px == 940
        assert req.extent.height_px == 750

    def test_lat_bounds(self):
        with pytest.raises(Exception):
            MapCenter(lat=91.0, lng=0.0)
        with pytest.raises(Exception):
            MapCenter(lat=-91.0, lng=0.0)

    def test_lng_bounds(self):
        with pytest.raises(Exception):
            MapCenter(lat=0.0, lng=181.0)


class TestStyleTokensHash:
    """Style tokens hash computation."""

    def test_hash_deterministic(self):
        tokens = StyleTokens(
            base_hex="#B8714E",
            primary_hex="#B8714E",
            is_light_base=True,
            text_primary="#1a1a1a",
            text_secondary="#555555",
            text_stroke="rgba(184,113,78,0.85)",
            roads=RoadStyleTokens(
                minor_casing="#815038", minor_fill="white",
                major_casing="#815038", major_fill="white",
            ),
            labels=LabelStyleTokens(
                street_colour="#555555",
                street_halo="rgba(184,113,78,0.85)",
                poi_colour="#1a1a1a",
                poi_halo="rgba(184,113,78,0.85)",
                park_colour="#225b2b",
                water_colour="#666666",
                neighbourhood_colour="#1a1a1a",
            ),
            markers=MarkerStyleTokens(
                building_fill="#B8714E",
                building_stroke="#93593e",
                building_label_colour="#1a1a1a",
            ),
            features=FeatureStyleTokens(
                park_fill="#729e5a",
                water_stroke="#5a7e93",
                block_fill="#a96848",
            ),
        )
        h1 = tokens.compute_hash()
        h2 = tokens.compute_hash()
        assert h1 == h2
        assert len(h1) == 16
