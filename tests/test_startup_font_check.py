"""Tests for startup_font_check() — fail-fast font validation."""

from unittest.mock import patch

import pytest

from brochure_maker.map_pipeline.errors import FontMissingError
from brochure_maker.map_pipeline.pdf_export import (
    startup_font_check,
    check_fonts_available,
    _check_font_available,
    REQUIRED_FONTS,
)


class TestCheckFontAvailable:
    """Unit tests for the low-level _check_font_available helper."""

    def test_returns_true_when_fc_list_finds_font(self):
        """When fc-list outputs a match, font is considered available."""
        with patch("brochure_maker.map_pipeline.pdf_export.shutil.which", return_value="/usr/bin/fc-list"):
            with patch("brochure_maker.map_pipeline.pdf_export.subprocess.run") as mock_run:
                mock_run.return_value.stdout = "/usr/share/fonts/Jost.ttf: Jost:style=Regular"
                mock_run.return_value.returncode = 0
                assert _check_font_available("Jost") is True

    def test_returns_false_when_fc_list_empty(self):
        """When fc-list produces no output, font is considered missing."""
        with patch("brochure_maker.map_pipeline.pdf_export.shutil.which", return_value="/usr/bin/fc-list"):
            with patch("brochure_maker.map_pipeline.pdf_export.subprocess.run") as mock_run:
                mock_run.return_value.stdout = ""
                mock_run.return_value.returncode = 0
                assert _check_font_available("NonexistentFont") is False

    def test_returns_true_when_fc_list_not_found(self):
        """When fc-list binary is missing, optimistically return True."""
        with patch("brochure_maker.map_pipeline.pdf_export.shutil.which", return_value=None):
            assert _check_font_available("AnyFont") is True

    def test_returns_true_on_subprocess_exception(self):
        """On subprocess error, optimistically return True."""
        with patch("brochure_maker.map_pipeline.pdf_export.shutil.which", return_value="/usr/bin/fc-list"):
            with patch(
                "brochure_maker.map_pipeline.pdf_export.subprocess.run",
                side_effect=OSError("permission denied"),
            ):
                assert _check_font_available("Jost") is True


class TestCheckFontsAvailable:
    """Tests for the batch font check helper."""

    def test_returns_empty_when_all_found(self):
        with patch(
            "brochure_maker.map_pipeline.pdf_export._check_font_available",
            return_value=True,
        ):
            assert check_fonts_available() == []

    def test_returns_missing_font_names(self):
        def _fake_check(font_name):
            return font_name != "Jost"

        with patch(
            "brochure_maker.map_pipeline.pdf_export._check_font_available",
            side_effect=_fake_check,
        ):
            missing = check_fonts_available()
            assert "Jost" in missing
            assert "Playfair Display" not in missing


class TestStartupFontCheck:
    """Tests for startup_font_check() fail-fast behavior."""

    def test_passes_when_all_fonts_available(self):
        """Should not raise when all fonts are found."""
        with patch(
            "brochure_maker.map_pipeline.pdf_export.check_fonts_available",
            return_value=[],
        ):
            startup_font_check()  # Should not raise

    def test_raises_font_missing_error(self):
        """Should raise FontMissingError listing missing fonts."""
        with patch(
            "brochure_maker.map_pipeline.pdf_export.check_fonts_available",
            return_value=["Jost", "Playfair Display"],
        ):
            with pytest.raises(FontMissingError) as exc_info:
                startup_font_check()
            assert "Jost" in exc_info.value.detail
            assert "Playfair Display" in exc_info.value.detail

    def test_error_includes_install_hint(self):
        """Error message should include actionable install instructions."""
        with patch(
            "brochure_maker.map_pipeline.pdf_export.check_fonts_available",
            return_value=["Jost"],
        ):
            with pytest.raises(FontMissingError) as exc_info:
                startup_font_check()
            assert "Install" in exc_info.value.detail or "install" in exc_info.value.detail

    def test_raises_with_single_missing_font(self):
        """Even a single missing font should trigger the error."""
        with patch(
            "brochure_maker.map_pipeline.pdf_export.check_fonts_available",
            return_value=["Playfair Display"],
        ):
            with pytest.raises(FontMissingError) as exc_info:
                startup_font_check()
            assert "Playfair Display" in exc_info.value.detail

    def test_required_fonts_constant(self):
        """REQUIRED_FONTS should include at least Jost and Playfair Display."""
        assert "Jost" in REQUIRED_FONTS
        assert "Playfair Display" in REQUIRED_FONTS
