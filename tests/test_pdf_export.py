"""Tests for PDF export — vector purity checks and structural hashing."""

import pytest
from brochure_maker.map_pipeline.pdf_export import (
    check_svg_purity,
    compute_pdf_structural_hash,
)
from brochure_maker.map_pipeline.errors import VectorPurityError


class TestSVGPurity:
    """SVG outputs must be pure vector, no large raster images."""

    def test_clean_svg_passes(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
        check_svg_purity(svg)  # Should not raise

    def test_small_image_passes(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,abc"/></svg>'
        check_svg_purity(svg)  # Small image OK

    def test_large_raster_rejected(self):
        # Generate a large base64 string (> 1KB)
        large_data = "A" * 2000
        svg = f'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,{large_data}"/></svg>'
        with pytest.raises(VectorPurityError):
            check_svg_purity(svg)

    def test_foreign_object_rejected(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div>HTML</div></foreignObject></svg>'
        with pytest.raises(VectorPurityError):
            check_svg_purity(svg)


class TestPDFStructuralHash:
    """PDF structural hash strips metadata for stable comparison."""

    def test_hash_deterministic(self):
        pdf = b"%PDF-1.4\n/CreationDate (2024-01-01)\nBT some text ET\n m 0 0 l 1 1"
        h1 = compute_pdf_structural_hash(pdf)
        h2 = compute_pdf_structural_hash(pdf)
        assert h1 == h2

    def test_different_dates_same_hash(self):
        pdf1 = b"%PDF-1.4\n/CreationDate (2024-01-01)\nBT some text ET"
        pdf2 = b"%PDF-1.4\n/CreationDate (2025-06-15)\nBT some text ET"
        h1 = compute_pdf_structural_hash(pdf1)
        h2 = compute_pdf_structural_hash(pdf2)
        assert h1 == h2

    def test_different_content_different_hash(self):
        pdf1 = b"%PDF-1.4\nBT text A ET"
        pdf2 = b"%PDF-1.4\nBT text B ET"
        h1 = compute_pdf_structural_hash(pdf1)
        h2 = compute_pdf_structural_hash(pdf2)
        assert h1 != h2
