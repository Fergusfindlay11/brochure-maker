"""Save, load, and list brochure templates."""

import json
import os
import shutil
from pathlib import Path
from typing import Optional, List, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_STORE = BASE_DIR / "saved_templates"
TEMPLATES_STORE.mkdir(exist_ok=True)


def save_template(name: str, analysis: dict, preview_path: Optional[str] = None) -> dict:
    """Save a brochure analysis as a reusable template.

    Strips specific content details but preserves structure, slide types,
    colour scheme, and layout.
    """
    template_dir = TEMPLATES_STORE / _safe_name(name)
    template_dir.mkdir(parents=True, exist_ok=True)

    # Build template definition from analysis
    template_def = {
        "name": name,
        "colour_scheme": analysis.get("colour_scheme", {}),
        "typography": analysis.get("typography", {}),
        "slide_count": len(analysis.get("slides", [])),
        "slides": [],
    }

    for slide in analysis.get("slides", []):
        slide_def = {
            "type": slide.get("type", "cover"),
            "content_schema": _extract_schema(slide.get("content", {})),
        }
        template_def["slides"].append(slide_def)

    # Write template JSON
    template_path = template_dir / "template.json"
    with open(template_path, "w", encoding="utf-8") as f:
        json.dump(template_def, f, indent=2, ensure_ascii=False)

    # Copy preview image if provided
    if preview_path and os.path.exists(preview_path):
        shutil.copy2(preview_path, template_dir / "preview.png")

    return template_def


def load_template(name: str) -> Optional[dict]:
    """Load a saved template by name."""
    template_path = TEMPLATES_STORE / _safe_name(name) / "template.json"
    if not template_path.exists():
        return None

    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_templates() -> List[dict]:
    """List all saved templates with basic metadata."""
    templates = []
    if not TEMPLATES_STORE.exists():
        return templates

    for template_dir in sorted(TEMPLATES_STORE.iterdir()):
        if not template_dir.is_dir():
            continue

        template_path = template_dir / "template.json"
        if not template_path.exists():
            continue

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            has_preview = (template_dir / "preview.png").exists()
            templates.append({
                "name": data.get("name", template_dir.name),
                "dir_name": template_dir.name,
                "slide_count": data.get("slide_count", 0),
                "has_preview": has_preview,
                "colour_primary": data.get("colour_scheme", {}).get("primary", "#B8714E"),
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return templates


def delete_template(name: str) -> bool:
    """Delete a saved template."""
    template_dir = TEMPLATES_STORE / _safe_name(name)
    if template_dir.exists():
        shutil.rmtree(template_dir)
        return True
    return False


def _extract_schema(content: dict) -> dict:
    """Extract the structure/schema from content (keys and types, not values)."""
    schema = {}
    for key, value in content.items():
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                schema[key] = [_extract_schema(value[0])]
            else:
                schema[key] = ["..."]
        elif isinstance(value, dict):
            schema[key] = _extract_schema(value)
        else:
            schema[key] = type(value).__name__
    return schema


def _safe_name(name: str) -> str:
    """Convert template name to filesystem-safe directory name."""
    return "".join(c if c.isalnum() or c in "-_ " else "" for c in name).strip().replace(" ", "_").lower()
