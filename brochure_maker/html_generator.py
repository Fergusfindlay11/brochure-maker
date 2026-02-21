"""Assemble interactive HTML brochure from AI analysis using Jinja2 templates."""

import os
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader


# Resolve template and static directories relative to this file
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Slide type to nav label mapping
NAV_LABELS = {
    "cover": "Cover",
    "text_and_photos": "About",
    "highlights_grid": "Highlights",
    "photo_gallery": "Photos",
    "floor_plan": "Floor Plan",
    "services_grid": "Services",
    "location": "Location",
    "travel_map": "Travel",
    "contacts": "Contacts",
}


def generate_brochure_html(
    analysis: dict,
    project_id: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """Generate a self-contained interactive HTML brochure.

    Args:
        analysis: Structured brochure data from AI analyser.
        project_id: Optional project ID for API integration.
        output_path: Optional file path to write the HTML to.

    Returns:
        The rendered HTML string.
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,  # We need raw HTML in SVGs
    )

    # Load static assets for inlining
    css_content = _load_static("css/editor.css")
    svg_library_js = _load_static("icons/svg-library.js")
    icon_picker_js = _load_static("js/icon-picker.js")
    logo_picker_js = _load_static("js/logo-picker.js")
    colour_picker_js = _load_static("js/colour-picker.js")
    transport_picker_js = _load_static("js/transport-picker.js")
    settings_sync_js = _load_static("js/settings-sync.js")
    editor_js = _load_static("js/editor.js")
    pdf_export_js = _load_static("js/pdf-export.js")

    # Prepare slides with nav labels
    slides = analysis.get("slides", [])
    for slide in slides:
        slide["nav_label"] = NAV_LABELS.get(slide.get("type", ""), "Slide")

    # Build colour scheme with defaults
    colour_scheme = analysis.get("colour_scheme", {})
    colour_scheme.setdefault("primary", "#B8714E")
    colour_scheme.setdefault("primary_dark", _darken(colour_scheme["primary"]))
    colour_scheme.setdefault("primary_light", _lighten(colour_scheme["primary"]))
    colour_scheme.setdefault("text_light", "#FFFFFF")
    colour_scheme.setdefault("text_dark", "#2A1810")
    colour_scheme.setdefault("background", "#F5EDE5")

    # Build header name for slide headers
    brochure_name = analysis.get("brochure_name", "Building Name")
    location = analysis.get("location", "Location")
    header_name = f"{brochure_name.upper()}, {location.upper()}"

    # Build subtitle (spaced-out location)
    subtitle = " ".join(location.upper()) if location else ""

    template = env.get_template("base_brochure.html")
    html = template.render(
        brochure_name=brochure_name,
        location=location,
        header_name=header_name,
        subtitle=subtitle,
        page_title=f"{brochure_name} — {location}",
        colour_scheme=colour_scheme,
        slides=slides,
        project_id=project_id or "",
        # Inlined static assets
        css_content=css_content,
        svg_library_js=svg_library_js,
        icon_picker_js=icon_picker_js,
        logo_picker_js=logo_picker_js,
        colour_picker_js=colour_picker_js,
        transport_picker_js=transport_picker_js,
        settings_sync_js=settings_sync_js,
        editor_js=editor_js,
        pdf_export_js=pdf_export_js,
    )

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    return html


def _load_static(relative_path: str) -> str:
    """Load a static file's contents."""
    filepath = STATIC_DIR / relative_path
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return f"/* File not found: {relative_path} */"


def _darken(hex_colour: str, factor: float = 0.8) -> str:
    """Darken a hex colour."""
    hex_colour = hex_colour.lstrip("#")
    r = int(int(hex_colour[0:2], 16) * factor)
    g = int(int(hex_colour[2:4], 16) * factor)
    b = int(int(hex_colour[4:6], 16) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _lighten(hex_colour: str, factor: float = 0.3) -> str:
    """Lighten a hex colour."""
    hex_colour = hex_colour.lstrip("#")
    r = int(hex_colour[0:2], 16)
    g = int(hex_colour[2:4], 16)
    b = int(hex_colour[4:6], 16)
    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))
    return f"#{r:02x}{g:02x}{b:02x}"
