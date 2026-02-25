"""Deterministic Amenity Map Pipeline v1.

Two decoupled APIs:
- POST /api/maps/v1/style/generate  -- vibe/image to style tokens
- POST /api/maps/v1/render          -- style_tokens to SVG+PDF
"""

__version__ = "1.0.0"
