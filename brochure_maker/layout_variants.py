"""Backend registry for slide layout variants and single-slide rendering."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

DEFAULT_LAYOUT_ID = "default"
DEFAULT_SLIDE_TYPE = "text_and_photos"

SLIDE_TYPE_ALIASES = {
    "about": "text_and_photos",
    "description": "text_and_photos",
    "generic": "text_and_photos",
    "highlights": "highlights_grid",
    "features": "highlights_grid",
    "photos": "photo_gallery",
    "gallery": "photo_gallery",
    "floorplan": "floor_plan",
    "floor-plan": "floor_plan",
    "services": "services_grid",
    "managed": "services_grid",
    "transport": "travel_map",
    "travel": "travel_map",
    "contact": "contacts",
}

HEADER_SLOTS = ["header", "header-logo", "header-name"]


def _image_slots(count: int) -> list[str]:
    return [f"image-{index}" for index in range(1, count + 1)]


def _variant(
    layout_id: str,
    label: str,
    template: str,
    description: str,
    *,
    image_count: int = 0,
    slots: list[str] | None = None,
    title_position: str | None = None,
    text_position: str | None = None,
    text_zones: list[str] | None = None,
    structure_tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": layout_id,
        "label": label,
        "description": description,
        "template": template,
        "image_count": image_count,
        "slots": slots or [],
        "title_position": title_position,
        "text_position": text_position,
        "text_zones": text_zones or [],
        "structure_tags": structure_tags or [],
    }


def _structure_metadata(variant: dict[str, Any]) -> dict[str, Any]:
    return {
        "title_position": variant.get("title_position"),
        "text_position": variant.get("text_position"),
        "text_zones": list(variant.get("text_zones", [])),
        "structure_tags": list(variant.get("structure_tags", [])),
    }


SLIDE_LAYOUT_VARIANTS = {
    "cover": {
        DEFAULT_LAYOUT_ID: _variant(
            DEFAULT_LAYOUT_ID,
            "Centered cover",
            "slides/cover.html",
            "Centered logo, title, subtitle, and optional tagline.",
            slots=["logo", "logo-mark", "heading", "subheading", "tagline"],
        ),
        "logo-left": _variant(
            "logo-left",
            "Logo left",
            "slides/cover.html",
            "Left-aligned masthead with the logo pinned to the top-left.",
            slots=["logo", "logo-mark", "heading", "subheading", "tagline"],
        ),
    },
    "text_and_photos": {
        DEFAULT_LAYOUT_ID: _variant(
            DEFAULT_LAYOUT_ID,
            "Three photos",
            "slides/text_and_photos.html",
            "Text panel with one hero image and two supporting images.",
            image_count=3,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body"] + _image_slots(3),
        ),
        "image-1": _variant(
            "image-1",
            "One image",
            "slides/text_and_photos.html",
            "Single large image beside the text panel.",
            image_count=1,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body"] + _image_slots(1),
        ),
        "image-2": _variant(
            "image-2",
            "Two images",
            "slides/text_and_photos.html",
            "Hero image with one supporting image beneath.",
            image_count=2,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body"] + _image_slots(2),
        ),
        "image-3": _variant(
            "image-3",
            "Three images",
            "slides/text_and_photos.html",
            "Hero image with two supporting images beneath.",
            image_count=3,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body"] + _image_slots(3),
        ),
        "image-4": _variant(
            "image-4",
            "Four images",
            "slides/text_and_photos.html",
            "Hero image with a three-image supporting row.",
            image_count=4,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body"] + _image_slots(4),
        ),
        "title-top": _variant(
            "title-top",
            "Title top",
            "slides/text_and_photos.html",
            "Full-width title and copy band above two balanced photo columns.",
            image_count=3,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body", "image-row"] + _image_slots(3),
            title_position="top_band",
            text_position="top_band",
            text_zones=["text-panel"],
            structure_tags=["title-top", "text-band", "photo-support"],
        ),
        "text-right": _variant(
            "text-right",
            "Text right",
            "slides/text_and_photos.html",
            "Photo-led spread with the title and body copy anchored on the right.",
            image_count=3,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body", "image-row"] + _image_slots(3),
            title_position="right_panel",
            text_position="right_panel",
            text_zones=["text-panel"],
            structure_tags=["text-right", "photo-led"],
        ),
        "text-card-overlay": _variant(
            "text-card-overlay",
            "Text card overlay",
            "slides/text_and_photos.html",
            "Hero photo spread with title and copy preserved inside an overlay text card.",
            image_count=3,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body", "image-row"] + _image_slots(3),
            title_position="overlay_card",
            text_position="overlay_card",
            text_zones=["text-panel"],
            structure_tags=["text-card", "overlay", "photo-led"],
        ),
    },
    "highlights_grid": {
        DEFAULT_LAYOUT_ID: _variant(
            DEFAULT_LAYOUT_ID,
            "Highlights grid",
            "slides/highlights_grid.html",
            "Balanced icon grid for key features.",
            slots=HEADER_SLOTS + ["heading", "features"],
        ),
        "compact": _variant(
            "compact",
            "Compact",
            "slides/highlights_grid.html",
            "Tighter spacing for dense feature sets.",
            slots=HEADER_SLOTS + ["heading", "features"],
        ),
        "two-column": _variant(
            "two-column",
            "Two column",
            "slides/highlights_grid.html",
            "Narrow two-column feature list.",
            slots=HEADER_SLOTS + ["heading", "features"],
        ),
        "title-sidebar": _variant(
            "title-sidebar",
            "Title sidebar",
            "slides/highlights_grid.html",
            "Large title column beside the feature grid.",
            slots=HEADER_SLOTS + ["heading", "features"],
            title_position="left_sidebar",
            text_position="feature_grid",
            text_zones=["features"],
            structure_tags=["title-sidebar", "feature-grid"],
        ),
    },
    "photo_gallery": {
        DEFAULT_LAYOUT_ID: _variant(
            DEFAULT_LAYOUT_ID,
            "Four photos",
            "slides/photo_gallery.html",
            "Two-by-two gallery grid.",
            image_count=4,
            slots=HEADER_SLOTS + _image_slots(4),
        ),
        "image-1": _variant(
            "image-1",
            "One image",
            "slides/photo_gallery.html",
            "Single full-gallery image.",
            image_count=1,
            slots=HEADER_SLOTS + _image_slots(1),
        ),
        "image-2": _variant(
            "image-2",
            "Two images",
            "slides/photo_gallery.html",
            "Two large images across the gallery area.",
            image_count=2,
            slots=HEADER_SLOTS + _image_slots(2),
        ),
        "image-3": _variant(
            "image-3",
            "Three images",
            "slides/photo_gallery.html",
            "Three-image gallery with one lower slot hidden.",
            image_count=3,
            slots=HEADER_SLOTS + _image_slots(3),
        ),
        "image-4": _variant(
            "image-4",
            "Four images",
            "slides/photo_gallery.html",
            "Four-image gallery with an asymmetric lower row.",
            image_count=4,
            slots=HEADER_SLOTS + _image_slots(4),
        ),
        "title-band": _variant(
            "title-band",
            "Title band",
            "slides/photo_gallery.html",
            "Gallery with a visible title and intro band above the photo grid.",
            image_count=4,
            slots=HEADER_SLOTS + ["title-band", "heading", "intro", "image-row-top", "image-row-bottom"] + _image_slots(4),
            title_position="top_band",
            text_position="top_band",
            text_zones=["title-band"],
            structure_tags=["title-band", "gallery-copy"],
        ),
        "caption-card": _variant(
            "caption-card",
            "Caption card",
            "slides/photo_gallery.html",
            "Photo grid with a visible overlay card for the gallery title and supporting copy.",
            image_count=4,
            slots=HEADER_SLOTS + ["caption-card", "caption-card-title", "caption-card-body", "image-row-top", "image-row-bottom"] + _image_slots(4),
            title_position="overlay_card",
            text_position="overlay_card",
            text_zones=["caption-card"],
            structure_tags=["caption-card", "text-card", "overlay", "gallery-copy"],
        ),
    },
    "floor_plan": {
        DEFAULT_LAYOUT_ID: _variant(
            DEFAULT_LAYOUT_ID,
            "Floor plan",
            "slides/floor_plan.html",
            "Specs panel on the left with plan image on the right.",
            image_count=1,
            slots=HEADER_SLOTS + ["spec-panel", "heading", "size-label", "spec-list", "note", "plan-area", "plan-image", "legend"],
        ),
        "plan-led": _variant(
            "plan-led",
            "Plan led",
            "slides/floor_plan.html",
            "Larger plan area with a slimmer specs panel.",
            image_count=1,
            slots=HEADER_SLOTS + ["spec-panel", "heading", "size-label", "spec-list", "note", "plan-area", "plan-image", "legend"],
        ),
        "specs-right": _variant(
            "specs-right",
            "Specs right",
            "slides/floor_plan.html",
            "Plan on the left with specs on the right.",
            image_count=1,
            slots=HEADER_SLOTS + ["spec-panel", "heading", "size-label", "spec-list", "note", "plan-area", "plan-image", "legend"],
        ),
        "title-top": _variant(
            "title-top",
            "Title top",
            "slides/floor_plan.html",
            "Floor plan with title and specs above the plan image.",
            image_count=1,
            slots=HEADER_SLOTS + ["spec-panel", "heading", "size-label", "spec-list", "note", "plan-area", "plan-image", "legend"],
            title_position="top_band",
            text_position="top_band",
            text_zones=["spec-panel"],
            structure_tags=["title-top", "spec-band", "plan-led"],
        ),
    },
    "services_grid": {
        DEFAULT_LAYOUT_ID: _variant(
            DEFAULT_LAYOUT_ID,
            "Two images",
            "slides/services_grid.html",
            "Managed services copy with a two-image panel.",
            image_count=2,
            slots=HEADER_SLOTS + ["text-panel", "heading", "intro", "services", "note", "image-panel"] + _image_slots(2),
        ),
        "image-1": _variant(
            "image-1",
            "One image",
            "slides/services_grid.html",
            "Managed services copy with one large image.",
            image_count=1,
            slots=HEADER_SLOTS + ["text-panel", "heading", "intro", "services", "note", "image-panel"] + _image_slots(1),
        ),
        "image-2": _variant(
            "image-2",
            "Two images",
            "slides/services_grid.html",
            "Managed services copy with two stacked images.",
            image_count=2,
            slots=HEADER_SLOTS + ["text-panel", "heading", "intro", "services", "note", "image-panel"] + _image_slots(2),
        ),
        "image-3": _variant(
            "image-3",
            "Three images",
            "slides/services_grid.html",
            "Managed services copy with a three-image panel.",
            image_count=3,
            slots=HEADER_SLOTS + ["text-panel", "heading", "intro", "services", "note", "image-panel"] + _image_slots(3),
        ),
        "image-4": _variant(
            "image-4",
            "Four images",
            "slides/services_grid.html",
            "Managed services copy with a four-image grid.",
            image_count=4,
            slots=HEADER_SLOTS + ["text-panel", "heading", "intro", "services", "note", "image-panel"] + _image_slots(4),
        ),
        "title-top": _variant(
            "title-top",
            "Title top",
            "slides/services_grid.html",
            "Service title, intro, and icons in a top band with supporting imagery below.",
            image_count=2,
            slots=HEADER_SLOTS + ["text-panel", "heading", "intro", "services", "note", "image-panel"] + _image_slots(2),
            title_position="top_band",
            text_position="top_band",
            text_zones=["text-panel"],
            structure_tags=["title-top", "service-copy", "image-support"],
        ),
        "text-card": _variant(
            "text-card",
            "Text card",
            "slides/services_grid.html",
            "Photo-led services spread with the managed-services copy preserved as a card.",
            image_count=2,
            slots=HEADER_SLOTS + ["text-panel", "heading", "intro", "services", "note", "image-panel"] + _image_slots(2),
            title_position="card",
            text_position="card",
            text_zones=["text-panel"],
            structure_tags=["text-card", "service-copy", "photo-led"],
        ),
    },
    "location": {
        DEFAULT_LAYOUT_ID: _variant(
            DEFAULT_LAYOUT_ID,
            "Six images",
            "slides/location.html",
            "Local area story with one hero image, two side images, and three lower images.",
            image_count=6,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body", "image-column", "image-row-bottom"] + _image_slots(6),
        ),
        "image-1": _variant(
            "image-1",
            "One image",
            "slides/location.html",
            "Single immersive local area image with copy overlaid.",
            image_count=1,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body"] + _image_slots(1),
        ),
        "image-2": _variant(
            "image-2",
            "Two images",
            "slides/location.html",
            "Hero image with one supporting local area image.",
            image_count=2,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body", "image-column"] + _image_slots(2),
        ),
        "image-3": _variant(
            "image-3",
            "Three images",
            "slides/location.html",
            "Hero image with a two-image side column.",
            image_count=3,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body", "image-column"] + _image_slots(3),
        ),
        "image-4": _variant(
            "image-4",
            "Four images",
            "slides/location.html",
            "Hero and side-column imagery with one lower supporting image.",
            image_count=4,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body", "image-column", "image-row-bottom"] + _image_slots(4),
        ),
        "text-card": _variant(
            "text-card",
            "Text card",
            "slides/location.html",
            "Local area imagery with the location title and copy preserved inside a card.",
            image_count=6,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body", "image-column", "image-row-bottom"] + _image_slots(6),
            title_position="card",
            text_position="card",
            text_zones=["text-panel"],
            structure_tags=["text-card", "location-copy", "photo-grid"],
        ),
        "title-left": _variant(
            "title-left",
            "Title left",
            "slides/location.html",
            "Location title and narrative on the left with photo grid on the right.",
            image_count=6,
            slots=HEADER_SLOTS + ["text-panel", "heading", "body", "image-column", "image-row-bottom"] + _image_slots(6),
            title_position="left_panel",
            text_position="left_panel",
            text_zones=["text-panel"],
            structure_tags=["title-left", "location-copy", "photo-support"],
        ),
    },
    "travel_map": {
        DEFAULT_LAYOUT_ID: _variant(
            DEFAULT_LAYOUT_ID,
            "Map right",
            "slides/travel_map.html",
            "Travel times on the left with map area on the right.",
            image_count=1,
            slots=HEADER_SLOTS + ["travel-panel", "heading", "station-list", "note", "map"],
        ),
        "map-left": _variant(
            "map-left",
            "Map left",
            "slides/travel_map.html",
            "Large map area on the left with travel times on the right.",
            image_count=1,
            slots=HEADER_SLOTS + ["travel-panel", "heading", "station-list", "note", "map"],
        ),
        "title-top": _variant(
            "title-top",
            "Title top",
            "slides/travel_map.html",
            "Travel title and connection list above a wide map.",
            image_count=1,
            slots=HEADER_SLOTS + ["travel-panel", "heading", "station-list", "note", "map"],
            title_position="top_band",
            text_position="top_band",
            text_zones=["travel-panel", "station-list"],
            structure_tags=["title-top", "travel-summary", "map-led"],
        ),
    },
    "contacts": {
        DEFAULT_LAYOUT_ID: _variant(
            DEFAULT_LAYOUT_ID,
            "Contacts row",
            "slides/contacts.html",
            "Back cover with contact cards in a horizontal row.",
            slots=["logo", "logo-mark", "heading", "subheading", "agency-logo", "agency-name", "contacts-row", "legal-text", "marketing-credit"],
        ),
        "stacked": _variant(
            "stacked",
            "Stacked contacts",
            "slides/contacts.html",
            "Back cover with contact cards stacked vertically.",
            slots=["logo", "logo-mark", "heading", "subheading", "agency-logo", "agency-name", "contacts-row", "legal-text", "marketing-credit"],
        ),
    },
}


def supported_slide_types() -> list[str]:
    """Return the concrete slide types backed by slide templates."""
    return list(SLIDE_LAYOUT_VARIANTS.keys())


def normalize_slide_type(slide_type: str | None) -> str:
    """Resolve loose/legacy slide type names to a supported slide type."""
    key = (slide_type or "").strip().lower().replace(" ", "_")
    key = SLIDE_TYPE_ALIASES.get(key, key)
    if key in SLIDE_LAYOUT_VARIANTS:
        return key
    return DEFAULT_SLIDE_TYPE


def layout_variants_for(slide_type: str | None) -> list[dict[str, Any]]:
    """Return available layout variants for a slide type."""
    normalized = normalize_slide_type(slide_type)
    return [
        {
            "id": layout_id,
            "label": variant["label"],
            "description": variant.get("description", ""),
            "slide_type": normalized,
            "template": variant["template"],
            "image_count": variant.get("image_count", 0),
            "slots": list(variant.get("slots", [])),
            **_structure_metadata(variant),
        }
        for layout_id, variant in SLIDE_LAYOUT_VARIANTS[normalized].items()
    ]


def layout_variants_metadata(render_endpoint: str = "/api/slides/render") -> dict[str, Any]:
    """Return frontend-consumable layout metadata grouped by slide type."""
    return {
        "renderEndpoint": render_endpoint,
        "variants": {
            slide_type: layout_variants_for(slide_type)
            for slide_type in supported_slide_types()
        },
    }


def resolve_layout_variant(slide_type: str | None, layout_id: str | None = None) -> dict[str, Any]:
    """Resolve a requested slide type/layout id to a renderable variant.

    Unknown layout IDs intentionally fall back to the default variant while
    preserving the requested id in the response metadata.
    """
    requested_slide_type = (slide_type or "").strip() or DEFAULT_SLIDE_TYPE
    normalized_slide_type = normalize_slide_type(requested_slide_type)
    requested_layout_id = (layout_id or "").strip() or DEFAULT_LAYOUT_ID

    variants = SLIDE_LAYOUT_VARIANTS[normalized_slide_type]
    variant = variants.get(requested_layout_id) or variants[DEFAULT_LAYOUT_ID]
    resolved_layout_id = variant["id"]

    return {
        "slide_type": normalized_slide_type,
        "requested_slide_type": requested_slide_type,
        "slide_type_fallback": normalized_slide_type != requested_slide_type,
        "layout_id": resolved_layout_id,
        "requested_layout_id": requested_layout_id,
        "layout_fallback": resolved_layout_id != requested_layout_id,
        "label": variant["label"],
        "description": variant.get("description", ""),
        "template": variant["template"],
        "image_count": variant.get("image_count", 0),
        "slots": list(variant.get("slots", [])),
        **_structure_metadata(variant),
    }


def render_slide_html(
    *,
    slide_type: str,
    layout_id: str | None,
    slide_num: int,
    content: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render one slide body and return HTML plus resolved layout metadata."""
    resolution = resolve_layout_variant(slide_type, layout_id)
    render_context = _build_render_context(context or {})
    render_context.update(
        {
            "slide_num": slide_num,
            "content": deepcopy(content or {}),
            "layout_id": resolution["layout_id"],
            "requested_layout_id": resolution["requested_layout_id"],
        }
    )

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
    )
    template = env.get_template(resolution["template"])
    html = template.render(**render_context)

    return {
        "html": html,
        "slide_type": resolution["slide_type"],
        "requested_slide_type": resolution["requested_slide_type"],
        "slide_type_fallback": resolution["slide_type_fallback"],
        "layout_id": resolution["layout_id"],
        "requested_layout_id": resolution["requested_layout_id"],
        "layout_fallback": resolution["layout_fallback"],
        "layout": {
            "id": resolution["layout_id"],
            "label": resolution["label"],
            "description": resolution["description"],
            "template": resolution["template"],
            "image_count": resolution["image_count"],
            "slots": resolution["slots"],
            "title_position": resolution["title_position"],
            "text_position": resolution["text_position"],
            "text_zones": resolution["text_zones"],
            "structure_tags": resolution["structure_tags"],
        },
        "available_layouts": layout_variants_for(resolution["slide_type"]),
    }


def _build_render_context(context: dict[str, Any]) -> dict[str, Any]:
    brochure_name = context.get("brochure_name") or "Building Name"
    location = context.get("location") or "Location"
    header_name = context.get("header_name") or f"{str(brochure_name).upper()}, {str(location).upper()}"
    subtitle = context.get("subtitle")
    if subtitle is None:
        subtitle = " ".join(str(location).upper()) if location else ""

    return {
        "brochure_name": brochure_name,
        "location": location,
        "header_name": header_name,
        "subtitle": subtitle,
        "clean_mode": bool(context.get("clean_mode", False)),
    }
