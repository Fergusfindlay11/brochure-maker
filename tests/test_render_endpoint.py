"""Integration tests for POST /api/maps/v1/render with mocked Overpass.

Stubs out heavy non-pipeline imports (PyMuPDF, Anthropic, Playwright, etc.)
before loading the FastAPI app so the test runs in a minimal environment.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
#  Stub heavy modules that app.py imports at top level but the map pipeline
#  doesn't need. We inject them into sys.modules *before* importing app.
# ---------------------------------------------------------------------------

def _stub_module(name, attrs=None):
    mod = types.ModuleType(name)
    if attrs:
        for k, v in attrs.items():
            setattr(mod, k, v)
    return mod


_STUBS = {
    "fitz": _stub_module("fitz"),
    "anthropic": _stub_module("anthropic", {"Anthropic": MagicMock}),
    "playwright": _stub_module("playwright"),
    "playwright.async_api": _stub_module("playwright.async_api", {"async_playwright": MagicMock}),
}

# Stub brochure_maker modules that require heavy deps
for mod_name in (
    "brochure_maker.pdf_extractor",
    "brochure_maker.ai_analyser",
    "brochure_maker.html_generator",
    "brochure_maker.ai_rewriter",
    "brochure_maker.ai_chat",
    "brochure_maker.map_generator",
    "brochure_maker.pdf_renderer",
    "brochure_maker.template_manager",
):
    _STUBS[mod_name] = _stub_module(mod_name, {
        # Common names that app.py imports:
        "extract_pdf": MagicMock(),
        "analyse_brochure": MagicMock(),
        "analyse_brochure_streaming": MagicMock(),
        "generate_brochure_html": MagicMock(),
        "generate_clean_html": MagicMock(),
        "generate_linkedin_cards": MagicMock(),
        "rewrite_text": MagicMock(),
        "chat_with_brochure": MagicMock(),
        "generate_neighbourhood_map": MagicMock(),
        "render_pdf": MagicMock(),
        "HAS_PLAYWRIGHT": False,
        "save_template": MagicMock(),
        "load_template": MagicMock(),
        "list_templates": MagicMock(return_value=[]),
        "delete_template": MagicMock(),
    })

# Inject stubs before anything tries to import them
for name, mod in _STUBS.items():
    if name not in sys.modules:
        sys.modules[name] = mod

from fastapi.testclient import TestClient  # noqa: E402

from brochure_maker.map_pipeline.basemap import BasemapExtraction, BasemapFeature  # noqa: E402


# ---------------------------------------------------------------------------
#  Fixtures: minimal Overpass-like data and a valid style_tokens payload
# ---------------------------------------------------------------------------

def _fake_basemap_extraction() -> BasemapExtraction:
    """Pre-built basemap extraction with roads and a park."""
    return BasemapExtraction(
        roads=[
            BasemapFeature(
                feature_type="road",
                geometry_type="LineString",
                coords=[(51.4998, -0.1202), (51.5000, -0.1200), (51.5002, -0.1198)],
                properties={"highway": "residential", "name": "Elm Street"},
            ),
            BasemapFeature(
                feature_type="road",
                geometry_type="LineString",
                coords=[(51.4995, -0.1225), (51.5000, -0.1200), (51.5005, -0.1175)],
                properties={"highway": "primary", "name": "High Road"},
            ),
        ],
        parks=[
            BasemapFeature(
                feature_type="park",
                geometry_type="Polygon",
                coords=[
                    (51.5010, -0.1220), (51.5015, -0.1210),
                    (51.5010, -0.1200), (51.5005, -0.1210),
                ],
                properties={"name": "Central Park"},
            ),
        ],
        water=[],
        buildings=[],
        source="overpass",
    )


def _valid_style_tokens() -> dict:
    """Minimal valid style_tokens dict for the render endpoint."""
    return {
        "base_hex": "#b8714e",
        "primary_hex": "#b8714e",
        "is_light_base": False,
        "text_primary": "#2c2320",
        "text_secondary": "#6b5a50",
        "text_stroke": "#f5efe9",
        "roads": {
            "minor_casing": "#b8714e",
            "minor_fill": "#f5efe9",
            "major_casing": "#2c2320",
            "major_fill": "#b8714e",
        },
        "labels": {
            "street_colour": "#6b5a50",
            "street_halo": "#f5efe9",
            "poi_colour": "#2c2320",
            "poi_halo": "#f5efe9",
            "park_colour": "#3a6b35",
            "water_colour": "#2563a0",
            "neighbourhood_colour": "#2c2320",
        },
        "markers": {
            "building_fill": "#b8714e",
            "building_stroke": "#7a4c33",
            "building_label_colour": "#2c2320",
        },
        "features": {
            "park_fill": "#aed581",
            "water_stroke": "#64b5f6",
            "block_fill": "#d7cfc5",
        },
    }


def _render_request_body() -> dict:
    return {
        "center": {"lat": 51.5, "lng": -0.12},
        "extent": {"radius_m": 350, "width_px": 940, "height_px": 750},
        "output": {"format": "svg"},
        "content": {"building_name": "Test Building"},
        "style_tokens": _valid_style_tokens(),
    }


# ---------------------------------------------------------------------------
#  Tests
# ---------------------------------------------------------------------------

class TestRenderEndpointIntegration:
    """Full-stack tests for POST /api/maps/v1/render with Overpass mocked."""

    @pytest.fixture(autouse=True)
    def _client(self):
        """Create a TestClient with Overpass calls mocked."""
        from app import app
        self.client = TestClient(app)

    def _mock_overpass(self):
        """Return patch context managers for the Overpass fetcher."""
        basemap = _fake_basemap_extraction()

        return (
            patch(
                "brochure_maker.map_pipeline.basemap.extract_from_overpass",
                new_callable=AsyncMock,
                return_value=basemap,
            ),
            patch(
                "brochure_maker.map_pipeline.orchestrator._get_overpass_extra_features",
                new_callable=AsyncMock,
                return_value={
                    "pois": [],
                    "rail_stations": [],
                    "neighbourhoods": [
                        {"name": "Mayfair", "lat": 51.5020, "lon": -0.1190},
                    ],
                },
            ),
            patch(
                "brochure_maker.map_pipeline.orchestrator.is_dataset_available",
                return_value=False,
            ),
        )

    def test_render_returns_200_with_svg(self):
        """Render endpoint returns 200 with SVG content."""
        m1, m2, m3 = self._mock_overpass()
        with m1, m2, m3:
            resp = self.client.post("/api/maps/v1/render", json=_render_request_body())
        assert resp.status_code == 200
        body = resp.json()
        assert body["svg_content"] is not None
        assert body["svg_content"].startswith("<svg")

    def test_render_svg_contains_building_marker(self):
        """Building name ends up in the SVG output."""
        m1, m2, m3 = self._mock_overpass()
        with m1, m2, m3:
            resp = self.client.post("/api/maps/v1/render", json=_render_request_body())
        svg = resp.json()["svg_content"]
        assert "TEST BUILDING" in svg

    def test_render_metadata_present(self):
        """Response includes label metrics and renderer version."""
        m1, m2, m3 = self._mock_overpass()
        with m1, m2, m3:
            resp = self.client.post("/api/maps/v1/render", json=_render_request_body())
        meta = resp.json()["metadata"]
        assert "label_metrics" in meta
        assert "renderer_version" in meta
        assert meta["dataset_version"] == "overpass-live"

    def test_render_determinism(self):
        """Same request produces byte-identical SVG."""
        m1a, m2a, m3a = self._mock_overpass()
        with m1a, m2a, m3a:
            resp1 = self.client.post("/api/maps/v1/render", json=_render_request_body())

        m1b, m2b, m3b = self._mock_overpass()
        with m1b, m2b, m3b:
            resp2 = self.client.post("/api/maps/v1/render", json=_render_request_body())

        assert resp1.json()["svg_content"] == resp2.json()["svg_content"]

    def test_render_rejects_vibe_text(self):
        """vibe_text is explicitly disallowed on the render endpoint."""
        body = _render_request_body()
        body["vibe_text"] = "something"
        resp = self.client.post("/api/maps/v1/render", json=body)
        assert resp.status_code == 422

    def test_render_rejects_missing_style_tokens(self):
        """Request without style_tokens should fail validation."""
        body = _render_request_body()
        del body["style_tokens"]
        resp = self.client.post("/api/maps/v1/render", json=body)
        assert resp.status_code == 422

    def test_render_label_metrics_counts(self):
        """Label metrics include placed/dropped counts."""
        m1, m2, m3 = self._mock_overpass()
        with m1, m2, m3:
            resp = self.client.post("/api/maps/v1/render", json=_render_request_body())
        lm = resp.json()["metadata"]["label_metrics"]
        assert lm["placed_labels"] >= 0
        assert isinstance(lm["dropped_labels"], int)

    def test_render_attribution_present(self):
        """Response includes attribution text."""
        m1, m2, m3 = self._mock_overpass()
        with m1, m2, m3:
            resp = self.client.post("/api/maps/v1/render", json=_render_request_body())
        assert "attribution" in resp.json()
        assert "OpenStreetMap" in resp.json()["attribution"]
