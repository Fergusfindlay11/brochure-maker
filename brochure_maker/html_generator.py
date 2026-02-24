"""Assemble interactive HTML brochure from AI analysis using Jinja2 templates."""

import os
import re
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
    text_toolbar_js = _load_static("js/text-toolbar.js")
    image_cropper_js = _load_static("js/image-cropper.js")
    slide_manager_js = _load_static("js/slide-manager.js")
    undo_redo_js = _load_static("js/undo-redo.js")
    ai_rewrite_js = _load_static("js/ai-rewrite.js")
    auto_save_js = _load_static("js/auto-save.js")
    editor_js = _load_static("js/editor.js")
    pdf_export_js = _load_static("js/pdf-export.js")
    chat_sidebar_js = _load_static("js/chat-sidebar.js")
    map_generator_js = _load_static("js/map-generator.js")

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
    address  = analysis.get("address", "")
    postcode = analysis.get("postcode", "")
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
        text_toolbar_js=text_toolbar_js,
        image_cropper_js=image_cropper_js,
        slide_manager_js=slide_manager_js,
        undo_redo_js=undo_redo_js,
        ai_rewrite_js=ai_rewrite_js,
        auto_save_js=auto_save_js,
        editor_js=editor_js,
        pdf_export_js=pdf_export_js,
        chat_sidebar_js=chat_sidebar_js,
        map_generator_js=map_generator_js,
        address=address,
        postcode=postcode,
    )

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    return html


def generate_clean_html(
    analysis: dict,
    project_id: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """Generate a clean, presentation-only HTML brochure (no editor UI)."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
    )

    css_content = _load_static("css/presentation.css")

    slides = analysis.get("slides", [])
    for slide in slides:
        slide["nav_label"] = NAV_LABELS.get(slide.get("type", ""), "Slide")

    colour_scheme = analysis.get("colour_scheme", {})
    colour_scheme.setdefault("primary", "#B8714E")
    colour_scheme.setdefault("primary_dark", _darken(colour_scheme["primary"]))
    colour_scheme.setdefault("primary_light", _lighten(colour_scheme["primary"]))
    colour_scheme.setdefault("text_light", "#FFFFFF")
    colour_scheme.setdefault("text_dark", "#2A1810")
    colour_scheme.setdefault("background", "#F5EDE5")

    brochure_name = analysis.get("brochure_name", "Building Name")
    location = analysis.get("location", "Location")
    header_name = f"{brochure_name.upper()}, {location.upper()}"
    subtitle = " ".join(location.upper()) if location else ""

    template = env.get_template("base_clean.html")
    html = template.render(
        brochure_name=brochure_name,
        location=location,
        header_name=header_name,
        subtitle=subtitle,
        page_title=f"{brochure_name} — {location}",
        colour_scheme=colour_scheme,
        slides=slides,
        project_id="",
        css_content=css_content,
    )

    # Post-process: strip editor-only attributes and elements
    html = _strip_editor_artifacts(html)

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    return html


def generate_linkedin_cards(analysis: dict) -> list:
    """Generate 4 LinkedIn carousel card HTML strings (1080x1080)."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
    )

    css_content = _load_static("css/linkedin.css")

    colour_scheme = analysis.get("colour_scheme", {})
    colour_scheme.setdefault("primary", "#B8714E")
    primary = colour_scheme["primary"]

    brochure_name = analysis.get("brochure_name", "Building Name")
    location = analysis.get("location", "Location")
    header_name = f"{brochure_name.upper()}, {location.upper()}"
    subtitle = " ".join(location.upper()) if location else ""

    slides = analysis.get("slides", [])

    # Inject primary colour into CSS
    card_css = f":root {{ --primary: {primary}; }}\n" + css_content
    card_css += f"\n.li-card {{ background: {primary}; }}"

    # Icon SVGs for highlights card
    icon_map = {
        'warehouse': '<svg viewBox="0 0 36 36" fill="none"><rect x="3" y="28" width="30" height="2" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><rect x="3" y="10" width="30" height="2.5" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><polygon points="18,3 3,10 33,10" fill="none" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linejoin="round"/><rect x="6" y="12.5" width="3.5" height="15.5" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><rect x="16.25" y="12.5" width="3.5" height="15.5" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><rect x="26.5" y="12.5" width="3.5" height="15.5" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/></svg>',
        'plug': '<svg viewBox="0 0 36 36" fill="none"><rect x="11" y="14" width="14" height="10" rx="1.5" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><line x1="14.5" y1="14" x2="14.5" y2="8" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round"/><line x1="21.5" y1="14" x2="21.5" y2="8" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round"/><line x1="18" y1="24" x2="18" y2="30" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round"/></svg>',
        'chair': '<svg viewBox="0 0 36 36" fill="none"><rect x="8" y="4" width="20" height="13" rx="2" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><rect x="8" y="18" width="20" height="7" rx="1.5" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><line x1="10" y1="25" x2="9" y2="32" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round"/><line x1="26" y1="25" x2="27" y2="32" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round"/></svg>',
        'lift': '<svg viewBox="0 0 36 36" fill="none"><rect x="5" y="4" width="26" height="28" rx="2" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><line x1="18" y1="4" x2="18" y2="32" stroke="rgba(255,255,255,0.85)" stroke-width="1.2"/><polyline points="10,16 13,12 16,16" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><polyline points="20,20 23,24 26,20" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        'shower': '<svg viewBox="0 0 36 36" fill="none"><rect x="10" y="8" width="16" height="7" rx="2" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><line x1="13" y1="18" x2="13" y2="21" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round"/><line x1="18" y1="18" x2="18" y2="21" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round"/><line x1="23" y1="18" x2="23" y2="21" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round"/></svg>',
        'bicycle': '<svg viewBox="0 0 36 36" fill="none"><circle cx="9" cy="24" r="7" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><circle cx="27" cy="24" r="7" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><polyline points="9,24 16,12 22,12" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><polyline points="22,12 27,24 18,24 16,12" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        'lightning': '<svg viewBox="0 0 36 36" fill="none"><polyline points="21,4 12,19 18,19 15,32 24,17 18,17 21,4" stroke="rgba(255,255,255,0.85)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        'breeam': '<svg viewBox="0 0 36 36" fill="none"><rect x="10" y="14" width="16" height="18" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><polyline points="7,14 18,5 29,14" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" stroke-linejoin="round"/></svg>',
        'lock': '<svg viewBox="0 0 36 36" fill="none"><rect x="8" y="17" width="20" height="14" rx="2" stroke="rgba(255,255,255,0.85)" stroke-width="1.4"/><path d="M12 17 V13 A6 6 0 0 1 24 13 V17" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" fill="none"/><circle cx="18" cy="24" r="2.5" stroke="rgba(255,255,255,0.85)" stroke-width="1.3"/></svg>',
        'leaf': '<svg viewBox="0 0 36 36" fill="none"><path d="M18 32 Q18 18 30 8 Q28 22 18 32Z" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" fill="none"/><path d="M18 32 Q18 18 6 8 Q8 22 18 32Z" stroke="rgba(255,255,255,0.85)" stroke-width="1.4" fill="none"/><line x1="18" y1="32" x2="18" y2="12" stroke="rgba(255,255,255,0.85)" stroke-width="1.2" stroke-linecap="round"/></svg>',
    }

    cards = []

    # Card 1: Cover
    cover_data = _find_slide(slides, "cover")
    tagline = cover_data.get("content", {}).get("tagline", "") if cover_data else ""
    t = env.get_template("linkedin/card_cover.html")
    cards.append(t.render(css=card_css, brochure_name=brochure_name, subtitle=subtitle, tagline=tagline))

    # Card 2: Highlights
    hl_data = _find_slide(slides, "highlights_grid")
    features = []
    if hl_data:
        for f in hl_data.get("content", {}).get("features", [])[:8]:
            icon_hint = f.get("icon_hint", "warehouse")
            features.append({
                "icon_svg": icon_map.get(icon_hint, icon_map["warehouse"]),
                "text": f.get("text", ""),
            })
    t = env.get_template("linkedin/card_highlights.html")
    cards.append(t.render(css=card_css, header_name=header_name, features=features))

    # Card 3: Photos
    photo_data = _find_slide(slides, "photo_gallery") or _find_slide(slides, "location")
    photos = []
    location_text = ""
    if photo_data:
        content = photo_data.get("content", {})
        images = content.get("images", content.get("photos", []))
        for img in images[:4]:
            photos.append({
                "description": img.get("description", "Photo"),
                "caption": img.get("caption", ""),
                "image_url": "",
            })
        if photo_data.get("type") == "location":
            location_text = content.get("body", "")
    if not photos:
        photos = [{"description": "Upload photo", "caption": "", "image_url": ""}] * 4
    t = env.get_template("linkedin/card_photos.html")
    cards.append(t.render(css=card_css, header_name=header_name, photos=photos, location_text=location_text))

    # Card 4: Contacts
    contact_data = _find_slide(slides, "contacts")
    contacts = []
    agency_name = ""
    if contact_data:
        content = contact_data.get("content", {})
        agency_name = content.get("agency_name", "")
        for c in content.get("contacts", [])[:3]:
            contacts.append({
                "name": c.get("name", ""),
                "email": c.get("email", ""),
                "phone": c.get("phone", ""),
            })
    t = env.get_template("linkedin/card_contacts.html")
    cards.append(t.render(
        css=card_css, brochure_name=brochure_name, subtitle=subtitle,
        agency_name=agency_name, contacts=contacts,
    ))

    return cards


def _find_slide(slides: list, slide_type: str):
    """Find the first slide matching a type."""
    for s in slides:
        if s.get("type") == slide_type:
            return s
    return None


def _strip_editor_artifacts(html: str) -> str:
    """Remove editor-only attributes and elements from clean HTML."""
    # Remove contenteditable attributes
    html = re.sub(r'\s+contenteditable="true"', '', html)
    # Remove slide-label divs
    html = re.sub(r'<div class="slide-label">.*?</div>\s*', '', html)
    # Remove route-delete-btn, add-line-btn, tube-add-btn buttons
    html = re.sub(r'<button[^>]*class="[^"]*route-delete-btn[^"]*"[^>]*>.*?</button>', '', html)
    html = re.sub(r'<button[^>]*class="[^"]*add-line-btn[^"]*"[^>]*>.*?</button>', '', html)
    html = re.sub(r'<button[^>]*class="[^"]*tube-add-btn[^"]*"[^>]*>.*?</button>', '', html)
    html = re.sub(r'<button[^>]*id="addStationBtn"[^>]*>.*?</button>', '', html)
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
