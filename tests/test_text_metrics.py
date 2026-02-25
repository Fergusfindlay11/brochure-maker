"""Tests for the text metrics subsystem."""

from brochure_maker.map_pipeline.text_metrics import measure_text, TextBBox


class TestTextMetricsBasic:
    """Basic text measurement tests (work with heuristic backend)."""

    def test_empty_text_returns_zero(self):
        bbox = measure_text("")
        assert bbox.width_px == 0
        assert bbox.height_px == 0

    def test_single_char_has_nonzero_size(self):
        bbox = measure_text("A", size_pt=12.0)
        assert bbox.width_px > 0
        assert bbox.height_px > 0

    def test_longer_text_is_wider(self):
        short = measure_text("Hi", size_pt=12.0)
        long = measure_text("Hello World", size_pt=12.0)
        assert long.width_px > short.width_px

    def test_larger_font_is_taller(self):
        small = measure_text("Test", size_pt=10.0)
        large = measure_text("Test", size_pt=20.0)
        assert large.height_px > small.height_px

    def test_bold_slightly_wider(self):
        normal = measure_text("Test", weight=400, size_pt=12.0)
        bold = measure_text("Test", weight=700, size_pt=12.0)
        assert bold.width_px >= normal.width_px


class TestTextMetricsDeterminism:
    """Metrics must be identical across runs in the same container."""

    def test_repeated_calls_identical(self):
        bbox1 = measure_text("St John's", font_family="Jost", weight=500, size_pt=11.0)
        bbox2 = measure_text("St John's", font_family="Jost", weight=500, size_pt=11.0)
        assert bbox1.width_px == bbox2.width_px
        assert bbox1.height_px == bbox2.height_px
        assert bbox1.baseline_px == bbox2.baseline_px

    def test_golden_fixture_strings(self):
        """Fixed set of strings must produce stable widths."""
        fixtures = ["St John's", "W", "MW", "ffi", "Neighbourhood Centre"]
        results = []
        for text in fixtures:
            bbox = measure_text(text, font_family="Jost", weight=500, size_pt=11.0)
            results.append(bbox.width_px)

        # Re-measure and compare
        for i, text in enumerate(fixtures):
            bbox = measure_text(text, font_family="Jost", weight=500, size_pt=11.0)
            assert bbox.width_px == results[i], f"Mismatch for {text!r}"


class TestTextMetricsHalo:
    """Halo padding adds to bounding box."""

    def test_halo_adds_padding(self):
        bbox = measure_text("Test", size_pt=12.0, halo_width=3.0)
        assert bbox.halo_padding == 3.0
        assert bbox.padded_width > bbox.width_px
        assert bbox.padded_height > bbox.height_px

    def test_no_halo_no_padding(self):
        bbox = measure_text("Test", size_pt=12.0, halo_width=0.0)
        assert bbox.halo_padding == 0.0
        assert bbox.padded_width == bbox.width_px


class TestTextMetricsLetterSpacing:
    """Letter spacing increases width."""

    def test_spacing_increases_width(self):
        tight = measure_text("HELLO", size_pt=12.0, letter_spacing=0.0)
        spaced = measure_text("HELLO", size_pt=12.0, letter_spacing=3.0)
        assert spaced.width_px > tight.width_px
