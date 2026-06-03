"""Brochure Maker — FastAPI application."""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import shutil
import uuid
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import ValidationError
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from brochure_maker.pdf_extractor import extract_pdf
from brochure_maker.exact_pdf_layout import EXACT_ICON_LIBRARY, generate_exact_pdf_layout, prepare_exact_pdf_source
from brochure_maker.pdf_design_graph import write_design_graph
from brochure_maker.pdf_exact_layout import write_exact_layout_model
from brochure_maker.ai_analyser import analyse_brochure, analyse_brochure_streaming
from brochure_maker.html_generator import generate_brochure_html, generate_clean_html, generate_linkedin_cards
from brochure_maker.ai_rewriter import rewrite_text
from brochure_maker.ai_chat import chat_with_brochure
from brochure_maker.map_generator import generate_neighbourhood_map
from brochure_maker.pdf_renderer import render_pdf, HAS_PDF_RENDERER
from brochure_maker.map_v1 import (
    MapV1Service,
    MapStyleGenerateRequestV1,
    MapRenderRequestV1,
)
from brochure_maker.map_v1.config import MAP_V1_ENABLED
from brochure_maker.map_v1.pdf_export import assert_pdf_runtime_ready
from brochure_maker.map_v1.service import map_http_exception
from brochure_maker.template_manager import (
    save_template,
    load_template,
    list_templates,
    delete_template,
    apply_template_to_analysis,
    merge_editor_layout_state_into_analysis,
)
from brochure_maker.layout_variants import render_slide_html

BASE_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = BASE_DIR / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)
EVAL_REPORTS_DIR = BASE_DIR / "evals" / "reports"
map_v1_service = MapV1Service(PROJECTS_DIR) if MAP_V1_ENABLED else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fail-fast checks for map-v1 PDF typography support."""
    if MAP_V1_ENABLED:
        assert_pdf_runtime_ready()
    yield


app = FastAPI(title="Brochure Maker", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# In-memory project status tracking
project_status = {}  # type: dict


# ──────────────────────────────────────────────
#  Landing Page
# ──────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def landing_page():
    """Serve the landing page."""
    landing_path = BASE_DIR / "templates" / "landing.html"
    if landing_path.exists():
        return HTMLResponse(content=landing_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Brochure Maker</h1><p>Landing page not found.</p>")


# ──────────────────────────────────────────────
#  PDF Upload & Processing
# ──────────────────────────────────────────────
@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF brochure and start processing."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    project_id = str(uuid.uuid4())[:8]
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded PDF
    pdf_path = project_dir / "source.pdf"
    content = await file.read()
    with open(pdf_path, "wb") as f:
        f.write(content)

    # Set initial status
    project_status[project_id] = {
        "status": "uploaded",
        "message": "PDF uploaded. Starting processing...",
        "filename": file.filename,
    }

    # Start background processing
    asyncio.create_task(_process_brochure(project_id, str(pdf_path), str(project_dir)))

    return JSONResponse({
        "project_id": project_id,
        "status": "uploaded",
        "message": f"PDF '{file.filename}' uploaded. Processing started.",
    })


@app.post("/api/upload/exact")
async def upload_exact_pdf_layout(file: UploadFile = File(...)):
    """Upload a PDF and create a high-fidelity editable layout from its pages."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    project_id = str(uuid.uuid4())[:8]
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = project_dir / "source.pdf"
    content = await file.read()
    with open(pdf_path, "wb") as f:
        f.write(content)

    project_status[project_id] = {
        "status": "extracting",
        "message": "Extracting exact PDF layout, page art, fonts, and editable text...",
        "filename": file.filename,
        "mode": "exact_pdf_layout",
    }

    try:
        processing_pdf_path, pdf_preflight = prepare_exact_pdf_source(pdf_path, project_dir)
        model = write_exact_layout_model(
            processing_pdf_path,
            project_dir / "exact_layout_model",
            render_dpi=144,
        )
        layout_meta = generate_exact_pdf_layout(
            pdf_path=processing_pdf_path,
            project_dir=project_dir,
            project_id=project_id,
            output_path=project_dir / "brochure.html",
            force_source_preserve_vector_ops=bool(pdf_preflight.get("repaired")),
        )
        design_graph_path = write_design_graph(
            model,
            project_dir / "brochure.design.json",
            render_metadata=layout_meta,
            project_id=project_id,
        )
        metadata = {
            "mode": "exact_pdf_layout",
            "filename": file.filename,
            "brochure_name": Path(file.filename).stem,
            "layout": layout_meta,
            "model_path": model.get("model_path"),
            "inventory_path": model.get("inventory_path"),
            "design_graph_path": str(design_graph_path),
            "pdf_preflight": pdf_preflight,
        }
        with open(project_dir / "exact_metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        project_status[project_id] = {
            "status": "complete",
            "message": "Exact editable PDF layout ready.",
            "brochure_url": f"/api/projects/{project_id}/brochure",
            "slide_count": layout_meta.get("page_count", 0),
            "brochure_name": metadata["brochure_name"],
            "mode": "exact_pdf_layout",
        }
        return JSONResponse(project_status[project_id] | {"project_id": project_id})
    except Exception as e:
        project_status[project_id] = {
            "status": "error",
            "message": f"Exact layout extraction failed: {str(e)}",
            "mode": "exact_pdf_layout",
        }
        raise HTTPException(status_code=500, detail=project_status[project_id]["message"])


async def _process_brochure(project_id: str, pdf_path: str, project_dir: str):
    """Background task: extract PDF, analyse with AI, generate HTML."""
    try:
        # Step 1: Extract PDF content
        project_status[project_id] = {
            "status": "extracting",
            "message": "Extracting text, images, and colours from PDF...",
        }
        await asyncio.sleep(0.1)  # yield to event loop

        pdf_data = extract_pdf(pdf_path, project_dir)

        # Save page count for reference
        project_status[project_id] = {
            "status": "analysing",
            "message": f"Extracted {pdf_data['page_count']} pages. Sending to AI for analysis...",
        }

        # Step 2: AI analysis
        analysis = await analyse_brochure(pdf_data)

        template_ref_path = Path(project_dir) / "template_ref.json"
        if template_ref_path.exists():
            project_status[project_id] = {
                "status": "cloning",
                "message": "Applying saved brochure structure, style, and exact layout sequence...",
            }
            with open(template_ref_path, "r", encoding="utf-8") as f:
                template_ref = json.load(f)
            analysis = apply_template_to_analysis(analysis, template_ref, strict_sequence=True)

        # Save analysis JSON
        analysis_path = os.path.join(project_dir, "analysis.json")
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)

        clone_verification = analysis.get("clone_verification")
        if clone_verification:
            verification_path = Path(project_dir) / "verification.json"
            with open(verification_path, "w", encoding="utf-8") as f:
                json.dump(clone_verification, f, indent=2, ensure_ascii=False)
            _seed_template_editor_state(Path(project_dir), analysis)

        # Auto-geocode if address/postcode were extracted by AI
        address_str = analysis.get("address", "")
        postcode_str = analysis.get("postcode", "")
        if (address_str or postcode_str) and not analysis.get("lat"):
            try:
                import logging as _logging
                _logger = _logging.getLogger(__name__)
                from brochure_maker.map_generator import geocode_structured
                city = analysis.get("location", "")
                geo_lat, geo_lng = await geocode_structured(
                    street=address_str, postcode=postcode_str, city=city
                )
                analysis["lat"] = geo_lat
                analysis["lng"] = geo_lng
                with open(analysis_path, "w", encoding="utf-8") as f:
                    json.dump(analysis, f, indent=2, ensure_ascii=False)
                _logger.info("Auto-geocoded %s %s -> (%s, %s)", address_str, postcode_str, geo_lat, geo_lng)
            except Exception as e:
                import logging as _logging
                _logging.getLogger(__name__).warning("Auto-geocode failed: %s", e)

        if clone_verification:
            project_status[project_id] = {
                "status": "verifying",
                "message": (
                    f"Critical clone verifier is {clone_verification.get('confidence', 0)}% "
                    "confident in the saved structure/layout match..."
                ),
                "verification": clone_verification,
            }
        else:
            project_status[project_id] = {
                "status": "generating",
                "message": f"AI identified {len(analysis.get('slides', []))} slides. Generating interactive HTML...",
            }

        # Step 3: Generate HTML
        output_path = os.path.join(project_dir, "brochure.html")
        generate_brochure_html(
            analysis=analysis,
            project_id=project_id,
            output_path=output_path,
        )

        project_status[project_id] = {
            "status": "complete",
            "message": "Brochure ready! Click to view and edit.",
            "brochure_url": f"/api/projects/{project_id}/brochure",
            "slide_count": len(analysis.get("slides", [])),
            "brochure_name": analysis.get("brochure_name", "Brochure"),
            "verification": clone_verification,
        }

    except Exception as e:
        project_status[project_id] = {
            "status": "error",
            "message": f"Error: {str(e)}",
        }


# ──────────────────────────────────────────────
#  Project Status (SSE)
# ──────────────────────────────────────────────
@app.get("/api/projects/{project_id}/status")
async def get_project_status(project_id: str):
    """Get project processing status. Supports SSE for real-time updates."""
    accept = "text/event-stream"  # default to SSE

    async def event_stream():
        last_status = None
        timeout = 0
        while timeout < 300:  # 5 minute timeout
            status = project_status.get(project_id, {"status": "not_found"})
            if status != last_status:
                last_status = status
                yield f"data: {json.dumps(status)}\n\n"
                if status.get("status") in ("complete", "error", "not_found"):
                    break
            await asyncio.sleep(1)
            timeout += 1

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/projects/{project_id}/status/poll")
async def poll_project_status(project_id: str):
    """Simple polling endpoint for project status."""
    status = project_status.get(project_id)
    if not status:
        # Check if project exists on disk
        project_dir = PROJECTS_DIR / project_id
        if (project_dir / "brochure.html").exists():
            # Load analysis for metadata
            analysis_path = project_dir / "analysis.json"
            exact_metadata = _load_exact_metadata(project_dir)
            brochure_name = "Brochure"
            slide_count = 0
            mode = None
            if exact_metadata:
                brochure_name = exact_metadata.get("brochure_name", "Brochure")
                slide_count = exact_metadata.get("layout", {}).get("page_count", 0)
                mode = exact_metadata.get("mode")
            elif analysis_path.exists():
                with open(analysis_path) as f:
                    analysis = json.load(f)
                    brochure_name = analysis.get("brochure_name", "Brochure")
                    slide_count = len(analysis.get("slides", []))
            verification = _load_project_verification(project_dir)
            return JSONResponse({
                "status": "complete",
                "message": "Brochure ready.",
                "brochure_url": f"/api/projects/{project_id}/brochure",
                "brochure_name": brochure_name,
                "slide_count": slide_count,
                "mode": mode,
                "verification": verification,
            })
        raise HTTPException(status_code=404, detail="Project not found")
    return JSONResponse(status)


# ──────────────────────────────────────────────
#  Brochure Serving
# ──────────────────────────────────────────────
@app.get("/api/projects/{project_id}/brochure", response_class=HTMLResponse)
async def get_brochure(project_id: str):
    """Serve the generated interactive brochure HTML."""
    brochure_path = PROJECTS_DIR / project_id / "brochure.html"
    if not brochure_path.exists():
        raise HTTPException(status_code=404, detail="Brochure not found. Still processing?")
    return HTMLResponse(content=brochure_path.read_text(encoding="utf-8"))


@app.get("/api/projects/{project_id}/verification")
async def get_project_verification(project_id: str):
    """Return the critical clone verification report for a project."""
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    verification = _load_project_verification(project_dir)
    if not verification:
        raise HTTPException(status_code=404, detail="Clone verification not found")

    return JSONResponse({"verification": verification})


def _load_project_verification(project_dir: Path) -> dict | None:
    verification_path = project_dir / "verification.json"
    if not verification_path.exists():
        return None
    try:
        with open(verification_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _load_exact_metadata(project_dir: Path) -> dict | None:
    metadata_path = project_dir / "exact_metadata.json"
    if not metadata_path.exists():
        return None
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _prepare_exact_export_html(project_dir: Path) -> str:
    """Return exact-layout HTML as a static presentation artifact."""
    brochure_path = project_dir / "brochure.html"
    if not brochure_path.exists():
        raise HTTPException(status_code=404, detail="Exact HTML not found")

    html_content = brochure_path.read_text(encoding="utf-8")
    state = _load_project_editor_state(project_dir) or {}
    agency_defs = _extract_window_json(html_content, "__EXACT_AGENCY_LOGOS__")

    try:
        from bs4 import BeautifulSoup
    except Exception:
        return _prepare_exact_export_html_regex_fallback(html_content, state)

    soup = BeautifulSoup(html_content, "html.parser")
    if soup.body:
        classes = set(soup.body.get("class", []))
        classes.difference_update({"fields-open", "editing", "is-editing"})
        classes.add("export-clean")
        soup.body["class"] = sorted(classes)

    _apply_exact_static_state(soup, state, agency_defs if isinstance(agency_defs, dict) else {})
    for page in soup.select(".exact-page"):
        page["data-picture-layout"] = "original"
    _strip_exact_editor_markup(soup)
    _append_exact_export_style(soup)
    return str(soup)


def _prepare_exact_export_html_regex_fallback(html_content: str, state: dict) -> str:
    """Last-resort cleaner for environments without BeautifulSoup."""
    html_content = html_content.replace("<body>", '<body class="export-clean">', 1)
    html_content = re.sub(r'<img\s+class="pdf-bg"[^>]*?/?>', "", html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'\scontenteditable="[^"]*"', "", html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'\sspellcheck="[^"]*"', "", html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'<div class="exact-toolbar".*?</div>\s*', "", html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r'<aside class="exact-fields-panel".*?</aside>\s*', "", html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r'<div class="icon-picker-overlay".*?</div>\s*', "", html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r'<script\b.*?</script>\s*', "", html_content, flags=re.DOTALL | re.IGNORECASE)
    return html_content


def _apply_exact_static_state(soup: Any, state: dict, agency_defs: dict) -> None:
    _apply_exact_colours(soup, state)
    _apply_exact_typography_state(soup, state)
    _normalise_exact_source_logo_masks(soup)
    _apply_exact_text_state(soup, state)
    _remove_noisy_exact_ocr_text_targets(soup)
    _apply_exact_image_state(soup, state)
    _apply_exact_amenity_icon_state(soup, state)
    _apply_exact_logo_state(soup, state)
    _apply_exact_agency_logo_state(soup, state, agency_defs)
    _apply_exact_map_state(soup, state)


def _apply_exact_colours(soup: Any, state: dict) -> None:
    accent = state.get("accentColour") or _exact_root_css_var(soup, "--exact-accent") or "#ffea00"
    dark = state.get("darkColour") or _exact_root_css_var(soup, "--exact-dark") or "#333132"
    icon_style = state.get("globalIconStyle") if isinstance(state.get("globalIconStyle"), dict) else {}
    icon_colour = (icon_style.get("color") or accent) if icon_style.get("colorCustom") else accent
    icon_size = _clamp_float(icon_style.get("size"), 70.0, 130.0, 100.0) / 100.0
    icon_stroke = _clamp_float(icon_style.get("strokeWidth"), 1.0, 6.0, 3.0)
    style = soup.new_tag("style")
    style.string = (
        ":root{"
        f"--exact-accent:{accent};"
        f"--exact-dark:{dark};"
        f"--exact-icon-color:{icon_colour};"
        f"--exact-icon-scale:{icon_size:.3f};"
        f"--exact-icon-stroke-width:{icon_stroke:.2f};"
        "}"
    )
    (soup.head or soup).append(style)


def _exact_root_css_var(soup: Any, name: str) -> str:
    pattern = re.compile(rf"{re.escape(name)}\s*:\s*([^;}}]+)")
    for style in soup.select("style"):
        text = style.string or style.get_text() or ""
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return ""


def _clamp_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    return max(minimum, min(maximum, numeric))


def _apply_exact_typography_state(soup: Any, state: dict) -> None:
    typography = state.get("typography") if isinstance(state.get("typography"), dict) else {}
    if not typography:
        return
    mapping = {
        "cover-title": "--exact-font-cover-title",
        "section-heading": "--exact-font-section-heading",
        "body": "--exact-font-body",
        "caption": "--exact-font-caption",
        "table-status": "--exact-font-table-status",
        "agent-contact": "--exact-font-agent-contact",
    }
    lines = []
    changed_role_fonts: dict[str, tuple[str, str]] = {}
    for role, css_var in mapping.items():
        item = typography.get(role)
        family = item.get("fontFamily") if isinstance(item, dict) else item
        if isinstance(family, str) and family.strip():
            if isinstance(item, dict) and isinstance(item.get("cssVar"), str) and item["cssVar"].startswith("--"):
                css_var = item["cssVar"]
            stack = _exact_css_font_stack(family)
            lines.append(f"{css_var}:{stack};")
            if _exact_typography_setting_changed(item):
                changed_role_fonts[role] = (css_var, stack)
    if not lines:
        return
    style = soup.new_tag("style")
    style.string = ":root{" + "".join(lines) + "}"
    (soup.head or soup).append(style)
    for role, (css_var, _stack) in changed_role_fonts.items():
        for target in soup.select(f'.pdf-text[data-typography-role="{_css_attr(role)}"]'):
            fallback = target.get("data-font-alias") or target.get("data-font-family") or ""
            fallback_stack = _exact_css_font_stack(str(fallback)) if fallback else "inherit"
            _merge_style(target, {"font-family": f"var({css_var}, {fallback_stack})"})


def _exact_typography_setting_changed(item: Any) -> bool:
    if not isinstance(item, dict):
        return bool(item)
    changed = item.get("changed")
    if changed is not None:
        return bool(changed)
    family = item.get("fontFamily")
    default = item.get("defaultFontFamily")
    if isinstance(family, str) and isinstance(default, str):
        return family != default
    return bool(family)


def _exact_css_font_stack(family: str) -> str:
    escaped = family.strip().replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}", Arial, sans-serif'


def _apply_exact_text_state(soup: Any, state: dict) -> None:
    targets_by_save_id = {
        str(target.get("data-save-id")): target
        for target in soup.select("[data-save-id]")
        if target.get("data-save-id") is not None
    }
    for save_id, item in (state.get("editableTexts") or {}).items():
        if not isinstance(item, dict):
            continue
        target = targets_by_save_id.get(str(save_id))
        if target and isinstance(item.get("html"), str) and _exact_saved_text_applies(target, item):
            typography = item.get("typography") if isinstance(item.get("typography"), dict) else {}
            role = typography.get("role")
            if isinstance(role, str) and role.strip():
                target["data-typography-role"] = role.strip()
            _replace_inner_html(soup, target, _sanitize_exact_text_html(item["html"]))
            _apply_exact_text_layout_state(target, item)
            if item.get("edited"):
                target["data-edited"] = "true"
            _fit_exact_export_text_box(target)


def _exact_saved_text_applies(target: Any, item: dict) -> bool:
    saved_plain = _plain_text_from_exact_html(item.get("html"))
    current_plain = _normalise_exact_plain_text(target.get("data-plain-text") or "")
    original_plain = _plain_text_from_exact_html(target.get("data-original-html") or "")
    if (
        _is_noisy_exact_ocr_text_target(target)
        and saved_plain
        and current_plain
        and saved_plain != current_plain
        and (not original_plain or original_plain == current_plain)
    ):
        return False
    if item.get("edited") is True:
        return True
    if item.get("edited") is not False:
        return True
    return not (
        saved_plain
        and current_plain
        and saved_plain != current_plain
        and (not original_plain or original_plain == current_plain)
    )


def _is_noisy_exact_ocr_text_target(target: Any) -> bool:
    if str(target.get("data-ocr-fallback") or "").lower() != "true":
        return False
    role = str(target.get("data-typography-role") or "body")
    if role in {"cover-title", "section-heading"}:
        return False
    original_plain = _normalise_exact_plain_text(
        target.get("data-plain-text") or _plain_text_from_exact_html(target.get("data-original-html") or "")
    )
    font_size = _exact_style_px(str(target.get("style") or ""), "font-size")
    if font_size is None:
        font_size = _css_px(target.get("data-font-size"), 0.0)
    return 0 < len(original_plain) <= 4 and float(font_size or 0) >= 96


def _remove_noisy_exact_ocr_text_targets(soup: Any) -> None:
    for target in list(soup.select('.pdf-text[data-ocr-fallback="true"]')):
        if _is_noisy_exact_ocr_text_target(target):
            target.decompose()


def _normalise_exact_plain_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _plain_text_from_exact_html(html_value: Any) -> str:
    from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        fragment = BeautifulSoup(str(html_value or ""), "html.parser")
    for br in fragment.find_all("br"):
        br.replace_with(" ")
    return _normalise_exact_plain_text(fragment.get_text(" "))


def _apply_exact_text_layout_state(target: Any, item: dict) -> None:
    layout = item.get("layout")
    if not isinstance(layout, dict):
        return
    styles = layout.get("styles")
    if isinstance(styles, dict):
        allowed = {
            "left",
            "top",
            "width",
            "height",
            "font-size",
            "line-height",
            "white-space",
            "overflow-wrap",
            "word-break",
            "display",
            "align-items",
            "justify-content",
            "text-align",
        }
        updates = {
            key: str(value)
            for key, value in styles.items()
            if key in allowed and isinstance(value, (str, int, float)) and str(value).strip()
        }
        if updates:
            _merge_style(target, updates)
    if layout.get("titleStack"):
        target["data-title-stack"] = "true"
    if layout.get("hidden"):
        target["class"] = sorted(set(target.get("class", [])) | {"exact-field-hidden"})


def _fit_exact_export_text_box(target: Any) -> None:
    style = str(target.get("style") or "")
    text = _normalise_exact_plain_text(target.get_text(" ", strip=True) if hasattr(target, "get_text") else "")
    if not text:
        return
    width = _exact_style_px(style, "width")
    font_size = _exact_style_px(style, "font-size")
    if width is None or font_size is None or width <= 0 or font_size <= 0:
        return
    white_space = _exact_style_value(style, "white-space").lower()
    if white_space and white_space not in {"nowrap", "pre"}:
        return
    estimated = _estimated_exact_text_width(text, font_size)
    if estimated <= width * 1.05:
        return
    next_font = max(12.0, font_size * max(0.08, min(0.92, width / max(1.0, estimated))))
    updates = {
        "font-size": f"{next_font:.2f}px",
        "line-height": f"{max(next_font * 1.08, next_font + 2):.2f}px",
    }
    if _estimated_exact_text_width(text, next_font) > width * 1.05:
        updates.update(
            {
                "white-space": "normal",
                "overflow-wrap": "anywhere",
                "word-break": "break-word",
            }
        )
    _merge_style(target, updates)


def _estimated_exact_text_width(text: str, font_size: float) -> float:
    total = 0.0
    for char in text:
        if char.isspace():
            factor = 0.28
        elif char in ".,:;!'|":
            factor = 0.24
        elif char in "ilI[](){}":
            factor = 0.30
        elif char in "mwMW@#%&":
            factor = 0.72
        elif char.isupper():
            factor = 0.56
        elif char.isdigit():
            factor = 0.50
        else:
            factor = 0.46
        total += factor
    return total * max(1.0, font_size)


def _exact_style_px(style: str, key: str) -> float | None:
    match = re.search(rf"(?:^|;)\s*{re.escape(key)}\s*:\s*(-?\d+(?:\.\d+)?)px", style, flags=re.I)
    if not match:
        return None
    return float(match.group(1))


def _exact_style_value(style: str, key: str) -> str:
    match = re.search(rf"(?:^|;)\s*{re.escape(key)}\s*:\s*([^;]+)", style, flags=re.I)
    return match.group(1).strip() if match else ""


def _apply_exact_image_state(soup: Any, state: dict) -> None:
    targets_by_save_id = {
        str(target.get("data-save-id")): target
        for target in soup.select("[data-save-id]")
        if target.get("data-save-id") is not None
    }
    masks_by_save_id = {
        str(mask.get("data-mask-for")): mask
        for mask in soup.select("[data-mask-for]")
        if mask.get("data-mask-for") is not None
    }
    for save_id, item in (state.get("images") or {}).items():
        if not isinstance(item, dict):
            continue
        slot = targets_by_save_id.get(str(save_id))
        if not slot:
            continue
        photo = slot.select_one(".slot-photo")
        if photo and item.get("bgImage"):
            _merge_style(photo, {"background-image": str(item["bgImage"])})
            image_url = _image_value_to_url(str(item["bgImage"]))
            img = photo.select_one(".slot-photo-img")
            if image_url and not img:
                img = soup.new_tag("img")
                img["class"] = ["slot-photo-img"]
                img["alt"] = ""
                photo.append(img)
            if image_url and img:
                img["src"] = image_url
            classes = set(slot.get("class", []))
            classes.add("has-image")
            classes.add("is-replaced")
            slot["class"] = sorted(classes)
            mask = masks_by_save_id.get(str(save_id))
            if mask:
                mask_classes = set(mask.get("class", []))
                mask_classes.add("has-image")
                mask_classes.add("is-replaced")
                mask["class"] = sorted(mask_classes)
        style_updates = {
            key: str(item[key])
            for key in ("left", "top", "width", "height")
            if item.get(key)
        }
        if style_updates:
            _merge_style(slot, style_updates)
        if item.get("fit"):
            slot["data-fit"] = str(item["fit"])


def _image_value_to_url(image_value: str) -> str:
    value = str(image_value or "").strip()
    match = re.fullmatch(r"url\((['\"]?)(.*?)\1\)", value)
    return match.group(2) if match else value


def _apply_exact_amenity_icon_state(soup: Any, state: dict) -> None:
    icon_lookup = _load_exact_icon_lookup()
    changed_icons = state.get("amenityIconFields") or state.get("icons") or {}
    for slot in soup.select("[data-icon-slot]"):
        key = slot.get("data-icon-slot", "")
        icon_state = changed_icons.get(key) if isinstance(changed_icons, dict) else None
        icon_id = slot.get("data-icon-id") or slot.get("data-default-icon-id") or "office"
        active = False
        if isinstance(icon_state, dict):
            icon_id = icon_state.get("iconId") or icon_id
            active = icon_state.get("active") is not False
        elif isinstance(icon_state, str):
            icon_id = icon_state
            active = True
        slot["data-icon-id"] = icon_id
        if active:
            classes = set(slot.get("class", []))
            classes.add("is-active")
            slot["class"] = sorted(classes)
            _replace_inner_html(soup, slot, icon_lookup.get(icon_id) or EXACT_ICON_LIBRARY.get(icon_id) or "")


def _apply_exact_logo_state(soup: Any, state: dict) -> None:
    logo_state = state.get("globalLogo") if isinstance(state.get("globalLogo"), dict) else {}
    kind = logo_state.get("type") or state.get("logo") or "none"
    if kind == "none":
        return
    uploaded = logo_state.get("uploadedLogoDataUrl") or state.get("uploadedLogoDataUrl")
    size = int(logo_state.get("coverSize") or state.get("logoSize") or 56)
    position = logo_state.get("coverPosition") or state.get("logoPosition") or "source"
    source_slots = soup.select("[data-source-logo-slot]")
    if source_slots:
        scale = max(0.35, min(2.35, size / 56))
        markup = _global_logo_markup(kind, uploaded)
        if not markup:
            return
        for slot in source_slots:
            _normalise_exact_source_logo_slot_mask(slot)
            hidden_vectors = _hide_exact_source_vectors_for_slot(
                slot,
                f"source-logo:{slot.get('data-source-logo-slot') or ''}",
                threshold=0.08,
            )
            preferred_mask_mode = str(
                slot.get("data-default-source-logo-mask-mode") or slot.get("data-source-logo-mask-mode") or "sample"
            )
            slot["data-source-logo-mask-mode"] = "vector" if hidden_vectors else preferred_mask_mode
            slot["class"] = sorted(set(slot.get("class", [])) | {"is-active"})
            _replace_inner_html(
                soup,
                slot,
                '<div class="exact-source-logo-mask"></div>'
                f'<div class="exact-source-logo-art" style="transform:scale({scale:.3f})">{markup}</div>',
            )
            page = slot.find_parent(class_="exact-page")
            if page:
                page["class"] = sorted(set(page.get("class", [])) | {"show-logo"})
        return
    for page in soup.select(".exact-page"):
        existing = page.select_one(".exact-logo")
        if not existing:
            existing = soup.new_tag("div")
            existing["class"] = ["exact-logo", "logo-zone"]
            page.append(existing)
        existing["class"] = sorted(set(existing.get("class", [])) | {"is-active"})
        _replace_inner_html(soup, existing, _global_logo_markup(kind, uploaded))
        _position_static_logo(existing, page.get("data-page-num") == "1", size, position)
        page["class"] = sorted(set(page.get("class", [])) | {"show-logo"})


def _apply_exact_agency_logo_state(soup: Any, state: dict, agency_defs: dict) -> None:
    logos = state.get("agencyLogos") or {}
    if not isinstance(logos, dict):
        logos = {}
    logo_ids = set(str(key) for key in logos.keys())
    if isinstance(agency_defs, dict):
        logo_ids.update(str(key) for key in agency_defs.keys())
    for logo_id in sorted(logo_ids):
        raw_logo_state = logos.get(logo_id)
        logo_state = raw_logo_state if isinstance(raw_logo_state, dict) else {}
        definition = agency_defs.get(logo_id) if isinstance(agency_defs, dict) else None
        active = logo_state.get("active") is True or bool(logo_state.get("uploadedLogoDataUrl"))
        default_asset = str((definition or {}).get("defaultAssetUrl") or "")
        source_detection = str((definition or {}).get("sourceDetection") or "")
        materialise_default_asset = default_asset and "below contact group" in source_detection
        if not active and not materialise_default_asset:
            continue
        slot = soup.select_one(f'[data-brand-logo-slot="{_css_attr(logo_id)}"], [data-agency-logo-slot="{_css_attr(logo_id)}"]')
        if not slot and isinstance(definition, dict):
            page = soup.select_one(f'.exact-page[data-page-num="{_css_attr(str(definition.get("page") or ""))}"]')
            if not page:
                continue
            slot = soup.new_tag("div")
            slot["class"] = ["exact-brand-logo-slot"]
            slot["data-brand-logo-slot"] = logo_id
            slot["data-agency-logo-slot"] = logo_id
            _merge_style(
                slot,
                {
                    "left": f'{definition.get("left", 0)}px',
                    "top": f'{definition.get("top", 0)}px',
                    "width": f'{definition.get("width", 160)}px',
                    "height": f'{definition.get("height", 80)}px',
                },
            )
            page.append(slot)
        if not slot:
            continue
        classes = set(slot.get("class", []))
        if default_asset:
            classes.add("has-default-agency-logo")
            slot["data-default-agency-logo-asset"] = default_asset
        if source_detection:
            slot["data-source-detection"] = source_detection
        uploaded = logo_state.get("uploadedLogoDataUrl")
        if active:
            classes.add("is-active")
            slot["class"] = sorted(classes)
            text = str(logo_state.get("text") or (definition or {}).get("defaultText") or "")
        else:
            classes.discard("is-active")
            slot["class"] = sorted(classes)
            _replace_inner_html(
                soup,
                slot,
                '<div class="exact-agency-logo-default">'
                f'<img src="{html.escape(default_asset, quote=True)}" alt="">'
                "</div>",
            )
            continue
        if uploaded:
            _replace_inner_html(soup, slot, f'<div class="logo-mask"></div><img src="{html.escape(uploaded, quote=True)}" alt="">')
        else:
            class_name = str((definition or {}).get("className") or logo_id)
            output_class = f"logo-output logo-output-{class_name}"
            lines = [line.strip() for line in text.split("|||")]
            html_value = f'<div class="logo-mask"></div><div class="{output_class}">' + "<br/>".join(html.escape(line) for line in lines) + "</div>"
            _replace_inner_html(soup, slot, html_value)


def _normalise_exact_source_logo_masks(soup: Any) -> None:
    for slot in soup.select("[data-source-logo-slot]"):
        _normalise_exact_source_logo_slot_mask(slot)


def _normalise_exact_source_logo_slot_mask(slot: Any) -> None:
    mask_role = str(slot.get("data-source-logo-mask-role") or "")
    mask_colour = _style_value(slot, "--source-logo-mask")
    if mask_role == "dark" or _exact_mask_colour_role(mask_colour) == "dark":
        slot["data-source-logo-mask-role"] = "dark"
        _merge_style(slot, {"--source-logo-mask": "var(--exact-dark)"})


def _hide_exact_source_vectors_for_slot(slot: Any, owner_id: str, threshold: float = 0.08) -> int:
    page = slot.find_parent(class_="exact-page")
    if page is None:
        return 0
    vector_layers = page.select(".pdf-vector-layer, .pdf-vector-overlay-layer")
    if not vector_layers:
        return 0
    slot_rect = _exact_style_rect(slot)
    if not slot_rect:
        return 0
    hidden = 0
    for svg in vector_layers:
        view_box = _exact_view_box(svg)
        if not view_box:
            continue
        page_width = _exact_px(_style_value(page, "width")) or view_box[2]
        page_height = _exact_px(_style_value(page, "height")) or view_box[3]
        scale_x = page_width / (view_box[2] or page_width or 1)
        scale_y = page_height / (view_box[3] or page_height or 1)
        for shape in svg.select(".pdf-vector-shape[data-bbox]"):
            bbox = _exact_bbox(shape.get("data-bbox"))
            if not bbox:
                continue
            shape_rect = {
                "left": bbox[0] * scale_x,
                "top": bbox[1] * scale_y,
                "width": bbox[2] * scale_x,
                "height": bbox[3] * scale_y,
            }
            if _exact_rect_overlap_ratio(shape_rect, slot_rect) < threshold:
                continue
            shape["class"] = sorted(set(shape.get("class", [])) | {"source-logo-hidden"})
            shape["data-logo-hidden-by"] = owner_id
            hidden += 1
    return hidden


def _exact_view_box(svg: Any) -> tuple[float, float, float, float] | None:
    parts = re.split(r"[\s,]+", str(svg.get("viewBox") or svg.get("viewbox") or "").strip())
    if len(parts) != 4:
        return None
    try:
        return tuple(float(part) for part in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def _exact_bbox(value: Any) -> tuple[float, float, float, float] | None:
    parts = re.split(r"[\s,]+", str(value or "").strip())
    if len(parts) != 4:
        return None
    try:
        return tuple(float(part) for part in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def _exact_style_rect(tag: Any) -> dict[str, float] | None:
    left = _exact_px(_style_value(tag, "left"))
    top = _exact_px(_style_value(tag, "top"))
    width = _exact_px(_style_value(tag, "width"))
    height = _exact_px(_style_value(tag, "height"))
    if None in {left, top, width, height}:
        return None
    return {"left": left or 0.0, "top": top or 0.0, "width": width or 0.0, "height": height or 0.0}


def _exact_px(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _exact_rect_overlap_ratio(a: dict[str, float], b: dict[str, float]) -> float:
    left = max(a["left"], b["left"])
    top = max(a["top"], b["top"])
    right = min(a["left"] + a["width"], b["left"] + b["width"])
    bottom = min(a["top"] + a["height"], b["top"] + b["height"])
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    area = max(1.0, a["width"] * a["height"])
    return overlap / area


def _style_value(tag: Any, name: str) -> str:
    for chunk in str(tag.get("style") or "").split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        if key.strip().lower() == name.lower():
            return value.strip()
    return ""


def _exact_mask_colour_role(colour: str) -> str:
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", str(colour or "").strip())
    if not match:
        return "light"
    red = int(match.group(1)[0:2], 16)
    green = int(match.group(1)[2:4], 16)
    blue = int(match.group(1)[4:6], 16)
    return "dark" if max(red, green, blue) < 90 else "light"


def _apply_exact_map_state(soup: Any, state: dict) -> None:
    map_state = state.get("mapState") or state.get("mapFields") or state.get("exactMap") or {}
    if not isinstance(map_state, dict):
        return
    map_states = map_state.get("maps") if isinstance(map_state.get("maps"), list) else [map_state]
    used_areas: set[int] = set()
    for item in map_states:
        if not isinstance(item, dict) or not item.get("generatedHtml"):
            continue
        save_id = str(item.get("saveId") or "")
        area = None
        if save_id:
            area = soup.select_one(f'[data-exact-map-area][data-save-id="{save_id}"]')
        if area is None:
            for candidate in soup.select("[data-exact-map-area]"):
                if id(candidate) not in used_areas:
                    area = candidate
                    break
        if area is None:
            continue
        used_areas.add(id(area))
        layer = area.select_one(".exact-generated-map")
        if not layer:
            layer = soup.new_tag("div")
            layer["class"] = ["exact-generated-map"]
            area.insert(0, layer)
        _replace_inner_html(soup, layer, str(item.get("generatedHtml") or ""))
        area["class"] = sorted(set(area.get("class", [])) | {"has-generated-map", "source-map-hidden"})
        area["data-map-source"] = str(item.get("source") or "generated-v1")


def _strip_exact_editor_markup(soup: Any) -> None:
    for selector in (
        ".exact-toolbar",
        ".exact-fields-panel",
        ".icon-picker-overlay",
        ".slot-chip",
        ".slot-controls",
        ".agency-logo-chip",
        ".exact-map-controls",
        'input[type="file"]',
    ):
        for node in soup.select(selector):
            node.decompose()
    for script in soup.find_all("script"):
        script.decompose()
    for style in soup.find_all("style"):
        css = str(style.string or style.get_text() or "")
        for token in (
            "exact-toolbar",
            "exact-fields-panel",
            "slot-chip",
            "slot-controls",
            "exact-map-controls",
        ):
            css = css.replace(token, "exact-export-removed")
        css = css.replace('[contenteditable="true"]', '[data-export-removed]')
        css = css.replace("[contenteditable='true']", "[data-export-removed]")
        css = css.replace("[contenteditable]", "[data-export-removed]")
        css = css.replace('input[type="file"]', 'input[data-export-removed]')
        style.clear()
        style.string = css
    for node in soup.select("[contenteditable]"):
        node.attrs.pop("contenteditable", None)
        node.attrs.pop("spellcheck", None)
    for node in soup.select(".pdf-text [style]"):
        _remove_exact_text_style_overrides(node)
    for node in soup.select(".pdf-text font"):
        node.unwrap()
    for node in soup.select("button.exact-amenity-icon-slot"):
        node.name = "div"
        node.attrs.pop("type", None)
    for node in soup.select(".exact-source-logo-slot:not(.is-active):not(.has-default-source-logo)"):
        node.decompose()
    for node in soup.select("button, input, textarea, select"):
        if node.find_parent(class_="exact-page"):
            node.attrs["aria-hidden"] = "true"


def _append_exact_export_style(soup: Any) -> None:
    style = soup.new_tag("style")
    style.string = """
body.export-clean .exact-amenity-icon-slot { pointer-events: none; }
body.export-clean .exact-logo,
body.export-clean .exact-brand-logo-slot,
body.export-clean .exact-source-logo-slot { pointer-events: none; }
body.export-clean .exact-image-mask:not(.is-replaced),
body.export-clean .exact-space-plan-mask:not(.is-replaced),
body.export-clean .exact-amenity-icon-slot:not(.is-active),
body.export-clean .exact-brand-logo-slot:not(.is-active):not(.has-default-agency-logo),
body.export-clean .exact-logo:not(.is-active) {
  display: none !important;
}
body.export-clean .exact-source-logo-slot[data-source-logo-mask-mode="vector"] .exact-source-logo-mask,
body.export-clean .exact-source-logo-slot[data-source-logo-mask-mode="photo"] .exact-source-logo-mask { display: none !important; }
""".strip()
    (soup.head or soup).append(style)


def _remove_exact_text_style_overrides(node: Any) -> None:
    styles = {}
    for chunk in str(node.get("style") or "").split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        key = key.strip().lower()
        if key in {"font-family", "font-size", "font-weight", "font-style", "line-height", "color", "letter-spacing"}:
            continue
        styles[key] = value.strip()
    if styles:
        node["style"] = ";".join(f"{key}:{value}" for key, value in styles.items()) + ";"
    else:
        node.attrs.pop("style", None)


def _replace_inner_html(soup: Any, tag: Any, html_value: str) -> None:
    from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        fragment = BeautifulSoup(str(html_value), "html.parser")
    tag.clear()
    for child in list(fragment.contents):
        tag.append(child)


def _sanitize_exact_text_html(html_value: str) -> str:
    """Remove pasted rich-text styling so exact PDF typography remains authoritative."""
    from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
        fragment = BeautifulSoup(str(html_value), "html.parser")
    for node in fragment.select("[style]"):
        styles = {}
        for chunk in str(node.get("style") or "").split(";"):
            if ":" not in chunk:
                continue
            key, value = chunk.split(":", 1)
            key = key.strip().lower()
            if key in {"font-family", "font-size", "font-weight", "font-style", "line-height", "color", "letter-spacing"}:
                continue
            styles[key] = value.strip()
        if styles:
            node["style"] = ";".join(f"{key}:{value}" for key, value in styles.items()) + ";"
        else:
            node.attrs.pop("style", None)
    for node in fragment.find_all("font"):
        node.unwrap()
    return "".join(str(child) for child in fragment.contents)


def _merge_style(tag: Any, updates: dict[str, str]) -> None:
    styles: dict[str, str] = {}
    for chunk in str(tag.get("style") or "").split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        styles[key.strip()] = value.strip()
    styles.update({key: value for key, value in updates.items() if value is not None})
    tag["style"] = ";".join(f"{key}:{value}" for key, value in styles.items()) + ";"


def _global_logo_markup(kind: str, uploaded: str | None = None) -> str:
    if kind == "upload" and uploaded:
        return f'<img src="{html.escape(uploaded, quote=True)}" alt="">'
    if kind == "diamond":
        return '<svg viewBox="0 0 36 36" fill="none"><path d="M18 2L34 18L18 34L2 18Z" stroke="currentColor" stroke-width="1.6"/><path d="M18 9L27 18L18 27L9 18Z" stroke="currentColor" stroke-width="1.2"/></svg>'
    if kind == "grid":
        rects = []
        for index in range(20):
            x = 2 + (index % 4) * 10
            y = 2 + (index // 4) * 10
            rects.append(f'<rect x="{x}" y="{y}" width="7" height="7" stroke="currentColor" stroke-width="1.3"/>')
        return '<svg viewBox="0 0 44 54" fill="none">' + "".join(rects) + "</svg>"
    if kind == "facade":
        return '<svg viewBox="0 0 52 84" fill="none"><path d="M8 82V22h36v60M5 22h42M10 12h32M16 12V7c0-4 5-4 5 0v5M31 12V7c0-4 5-4 5 0v5M14 30h8v14h-8V30ZM30 30h8v14h-8V30ZM14 52h8v18h-8V52ZM30 52h8v18h-8V52ZM21 82V70h10v12" stroke="currentColor" stroke-width="1.2"/></svg>'
    return ""


def _position_static_logo(tag: Any, is_cover: bool, size: int, position: str) -> None:
    effective_size = size if (is_cover or position != "source") else round(size * 0.72)
    styles = {"width": f"{effective_size}px", "height": f"{round(effective_size * 1.22)}px"}
    if position == "source":
        styles.update({"top": "52px" if is_cover else "54px", "right": "64px" if is_cover else "", "left": "" if is_cover else "42px"})
    else:
        styles["top" if position.startswith("top") else "bottom"] = "42px"
        styles["left" if "left" in position else "right"] = "42px"
    _merge_style(tag, {key: value for key, value in styles.items() if value})


def _load_exact_icon_lookup() -> dict[str, str]:
    lookup = dict(EXACT_ICON_LIBRARY)
    library_path = BASE_DIR / "static" / "icons" / "svg-library.js"
    if not library_path.exists():
        return lookup
    source = library_path.read_text(encoding="utf-8", errors="ignore")
    for key, value in re.findall(r"'([^']+)':\s*`(<svg.*?</svg>)`", source, flags=re.DOTALL):
        svg = value.replace("${S}", "currentColor").replace("${SW}", "1.5")
        lookup[key] = svg
    return lookup


def _extract_window_json(source: str, name: str) -> Any:
    match = re.search(rf"window\.{re.escape(name)}\s*=\s*(.*?);\n", source, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception:
        return None


def _css_attr(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _exact_export_dimensions(project_dir: Path) -> tuple[int, int]:
    metadata = _load_exact_metadata(project_dir) or {}
    layout = metadata.get("layout") if isinstance(metadata.get("layout"), dict) else {}
    width = int(layout.get("page_width") or metadata.get("page_width") or 1200)
    height = int(layout.get("page_height") or metadata.get("page_height") or 750)
    return width, height


# ──────────────────────────────────────────────
#  Clean HTML Export (Presentation Mode)
# ──────────────────────────────────────────────
@app.get("/api/projects/{project_id}/export/html", response_class=HTMLResponse)
async def export_clean_html(project_id: str):
    """Export a clean, presentation-only HTML (no editor UI)."""
    project_dir = PROJECTS_DIR / project_id
    exact_metadata_path = project_dir / "exact_metadata.json"
    if exact_metadata_path.exists():
        return HTMLResponse(content=_prepare_exact_export_html(project_dir))

    analysis_path = PROJECTS_DIR / project_id / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="Project analysis not found")

    with open(analysis_path) as f:
        analysis = json.load(f)

    html = generate_clean_html(analysis=analysis, project_id=project_id)
    return HTMLResponse(content=html)


# ──────────────────────────────────────────────
#  LinkedIn Carousel Export
# ──────────────────────────────────────────────
@app.get("/api/projects/{project_id}/export/linkedin")
async def export_linkedin_cards(project_id: str):
    """Export 4 LinkedIn carousel card HTMLs (1080x1080)."""
    analysis_path = PROJECTS_DIR / project_id / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="Project analysis not found")

    with open(analysis_path) as f:
        analysis = json.load(f)

    cards = generate_linkedin_cards(analysis)
    return JSONResponse({"cards": cards})


# ──────────────────────────────────────────────
#  Image Upload (for placeholders)
# ──────────────────────────────────────────────
@app.post("/api/projects/{project_id}/images")
async def upload_image(project_id: str, file: UploadFile = File(...)):
    """Upload an image for a brochure placeholder."""
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    images_dir = project_dir / "user_images"
    images_dir.mkdir(exist_ok=True)

    # Save with original filename
    filename = file.filename or f"image_{uuid.uuid4()[:6]}.png"
    filepath = images_dir / filename
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    return JSONResponse({
        "url": f"/api/projects/{project_id}/images/{filename}",
        "filename": filename,
    })


@app.get("/api/projects/{project_id}/images/{filename}")
async def serve_image(project_id: str, filename: str):
    """Serve an uploaded image."""
    filepath = PROJECTS_DIR / project_id / "user_images" / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(filepath))


@app.get("/api/projects/{project_id}/exact_assets/{asset_path:path}")
async def serve_exact_layout_asset(project_id: str, asset_path: str):
    """Serve page backgrounds and embedded fonts for exact-layout projects."""
    base_dir = (PROJECTS_DIR / project_id / "exact_assets").resolve()
    filepath = (base_dir / asset_path).resolve()
    try:
        filepath.relative_to(base_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(filepath))


# ──────────────────────────────────────────────
#  Project Management
# ──────────────────────────────────────────────
@app.get("/api/projects")
async def list_projects():
    """List all projects."""
    projects = []
    if PROJECTS_DIR.exists():
        project_dirs = [path for path in PROJECTS_DIR.iterdir() if path.is_dir()]
        for project_dir in sorted(project_dirs, key=_project_updated_at, reverse=True):
            if not project_dir.is_dir():
                continue
            project_id = project_dir.name
            has_brochure = (project_dir / "brochure.html").exists()
            has_pdf = (project_dir / "source.pdf").exists()

            # Get name from analysis if available
            name = "Untitled"
            analysis_path = project_dir / "analysis.json"
            exact_metadata_path = project_dir / "exact_metadata.json"
            if analysis_path.exists():
                try:
                    with open(analysis_path) as f:
                        analysis = json.load(f)
                        name = analysis.get("brochure_name", "Untitled")
                except Exception:
                    pass
            elif exact_metadata_path.exists():
                try:
                    with open(exact_metadata_path, encoding="utf-8") as f:
                        exact_metadata = json.load(f)
                        name = exact_metadata.get("brochure_name", "Exact PDF Layout")
                except Exception:
                    name = "Exact PDF Layout"

            # Check in-memory status
            status = project_status.get(project_id, {}).get("status", "complete" if has_brochure else "unknown")
            qa = _load_project_qa_summary(project_id)

            projects.append({
                "id": project_id,
                "name": name,
                "has_brochure": has_brochure,
                "has_pdf": has_pdf,
                "status": status,
                "mode": "exact_pdf_layout" if exact_metadata_path.exists() else "ai_template",
                "updated_at": _project_updated_at(project_dir),
                **qa,
            })

    return JSONResponse({"projects": projects})


def _project_updated_at(project_dir: Path) -> float:
    candidates = [
        project_dir / "brochure.html",
        project_dir / "exact_benchmark_upload.json",
        project_dir / "exact_metadata.json",
        project_dir / "analysis.json",
        project_dir / "source.pdf",
    ]
    mtimes: list[float] = []
    for path in candidates:
        if path.exists():
            mtimes.append(path.stat().st_mtime)
    return max(mtimes) if mtimes else project_dir.stat().st_mtime


def _load_project_qa_summary(project_id: str) -> dict[str, Any]:
    report_path = EVAL_REPORTS_DIR / project_id / "benchmark-run.json"
    if not report_path.exists():
        return {
            "qa_status": "not_run",
            "qa_accepted": None,
            "qa_score": None,
        }
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "qa_status": "unknown",
            "qa_accepted": None,
            "qa_score": None,
        }
    accepted = bool(report.get("accepted"))
    scores = report.get("scores") if isinstance(report.get("scores"), dict) else {}
    score = scores.get("critic") or scores.get("visual") or scores.get("html_assessment")
    return {
        "qa_status": "passed" if accepted else "failed",
        "qa_accepted": accepted,
        "qa_score": score,
    }


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project."""
    project_dir = PROJECTS_DIR / project_id
    if project_dir.exists():
        shutil.rmtree(project_dir)
        project_status.pop(project_id, None)
        return JSONResponse({"message": f"Project {project_id} deleted."})
    raise HTTPException(status_code=404, detail="Project not found")


# ──────────────────────────────────────────────
#  Template Management
# ──────────────────────────────────────────────
@app.post("/api/templates/save")
async def save_template_route(
    name: str = Form(...),
    project_id: str = Form(...),
):
    """Save a project's brochure as a reusable template."""
    project_dir = PROJECTS_DIR / project_id
    analysis_path = project_dir / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="Project analysis not found")

    try:
        with open(analysis_path) as f:
            analysis = json.load(f)
        editor_state = _load_project_editor_state(project_dir)
        analysis = merge_editor_layout_state_into_analysis(analysis, editor_state)

        # Use first page render as preview
        render_path = project_dir / "renders" / "page1.png"
        preview = str(render_path) if render_path.exists() else None

        template_def = save_template(name, analysis, preview)
        return JSONResponse({
            "message": f"Template '{name}' saved.",
            "template": template_def,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save template: {str(e)}")


def _load_project_editor_state(project_dir: Path) -> dict | None:
    state_path = project_dir / "editor_state.json"
    if not state_path.exists():
        return None
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except json.JSONDecodeError:
        return None
    return state if isinstance(state, dict) else None


def _seed_template_editor_state(project_dir: Path, analysis: dict) -> None:
    """Seed editor state for template-only style values that live in JS state."""
    if not isinstance(analysis, dict):
        return

    state: dict[str, object] = {}
    primary = analysis.get("colour_scheme", {}).get("primary") if isinstance(analysis.get("colour_scheme"), dict) else None
    if isinstance(primary, str) and primary.strip():
        state["primaryColour"] = primary.strip()

    logo = analysis.get("logo")
    if isinstance(logo, dict) and logo:
        state["logo"] = logo

    if not state:
        return

    state_path = project_dir / "editor_state.json"
    if state_path.exists():
        try:
            existing = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        if isinstance(existing, dict):
            existing.update(state)
            state = existing

    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


@app.get("/api/templates")
async def list_templates_route():
    """List all saved templates."""
    return JSONResponse({"templates": list_templates()})


@app.delete("/api/templates/{name}")
async def delete_template_route(name: str):
    """Delete a saved template."""
    if delete_template(name):
        return JSONResponse({"message": f"Template '{name}' deleted."})
    raise HTTPException(status_code=404, detail="Template not found")


@app.post("/api/templates/{name}/apply")
async def apply_template(name: str, file: UploadFile = File(...)):
    """Create a new project using a saved template + uploaded PDF."""
    template = load_template(name)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Create project and process with template hints
    project_id = str(uuid.uuid4())[:8]
    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    # Save PDF
    pdf_path = project_dir / "source.pdf"
    content = await file.read()
    with open(pdf_path, "wb") as f:
        f.write(content)

    # Save template reference
    template_ref_path = project_dir / "template_ref.json"
    with open(template_ref_path, "w") as f:
        json.dump(template, f, indent=2)

    project_status[project_id] = {
        "status": "uploaded",
        "message": "PDF uploaded with template. Processing...",
    }

    asyncio.create_task(_process_brochure(project_id, str(pdf_path), str(project_dir)))

    return JSONResponse({
        "project_id": project_id,
        "status": "uploaded",
        "message": f"Processing with template '{name}'.",
    })


@app.post("/api/slides/render")
async def render_slide_route(request: Request):
    """Render a single slide for layout swaps."""
    payload = await _read_json_or_form(request)
    slide_type = payload.get("slide_type") or payload.get("type")
    if not slide_type:
        raise HTTPException(status_code=400, detail="slide_type is required")

    try:
        slide_num = int(payload.get("slide_num") or 1)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="slide_num must be an integer")
    slide_num = max(1, slide_num)

    content = payload.get("content") or {}
    if not isinstance(content, dict):
        raise HTTPException(status_code=400, detail="content must be an object")

    context = payload.get("context") or {}
    if not isinstance(context, dict):
        raise HTTPException(status_code=400, detail="context must be an object")
    for key in ("brochure_name", "location", "header_name", "subtitle", "clean_mode"):
        if key in payload:
            context[key] = payload[key]

    result = render_slide_html(
        slide_type=str(slide_type),
        layout_id=payload.get("layout_id"),
        slide_num=slide_num,
        content=content,
        context=context,
    )
    return JSONResponse(result)


async def _read_json_or_form(request: Request) -> dict:
    """Read route payloads from JSON or form-data bodies."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
    else:
        form = await request.form()
        payload = dict(form)

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")

    for key in ("content", "context", "colour_scheme", "typography"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            try:
                payload[key] = json.loads(value)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail=f"{key} must be valid JSON")

    return payload


# ──────────────────────────────────────────────
#  Serve rendered page images (for preview)
# ──────────────────────────────────────────────
@app.get("/api/projects/{project_id}/renders/{filename}")
async def serve_render(project_id: str, filename: str):
    """Serve a rendered page image."""
    filepath = PROJECTS_DIR / project_id / "renders" / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Render not found")
    return FileResponse(str(filepath))


# ──────────────────────────────────────────────
#  AI Content Rewrite
# ──────────────────────────────────────────────
@app.post("/api/ai/rewrite")
async def ai_rewrite(request: Request):
    """Rewrite selected text using AI."""
    body = await request.json()
    text = body.get("text", "")
    action = body.get("action", "rewrite")

    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    try:
        rewritten = await rewrite_text(text, action)
        return JSONResponse({"rewritten": rewritten})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
#  AI Chat (Conversational Brochure Editing)
# ──────────────────────────────────────────────
@app.post("/api/projects/{project_id}/chat")
async def ai_chat(project_id: str, request: Request):
    """Chat with AI to edit the brochure."""
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    body = await request.json()
    message = body.get("message", "")
    brochure_context = body.get("brochure_context", [])
    history = body.get("history", [])
    selection_context = body.get("selection_context", None)
    active_slide_id = body.get("active_slide_id", None)

    if not message:
        raise HTTPException(status_code=400, detail="No message provided")

    try:
        result = await chat_with_brochure(
            message, brochure_context, history,
            selection_context=selection_context,
            active_slide_id=active_slide_id,
        )
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_id}/chat/history")
async def get_chat_history(project_id: str):
    """Get chat history for a project."""
    history_path = PROJECTS_DIR / project_id / "chat_history.json"
    if not history_path.exists():
        return JSONResponse({"messages": []})
    with open(history_path) as f:
        return JSONResponse(json.load(f))


@app.post("/api/projects/{project_id}/chat/history")
async def save_chat_history(project_id: str, request: Request):
    """Save chat history for a project."""
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    body = await request.body()
    with open(project_dir / "chat_history.json", "wb") as f:
        f.write(body)
    return JSONResponse({"message": "Chat history saved"})


# ──────────────────────────────────────────────
#  Geocode Building Address
# ──────────────────────────────────────────────
@app.post("/api/projects/{project_id}/geocode")
async def geocode_building(project_id: str, request: Request):
    """Geocode building address using structured params; store lat/lng in analysis.json."""
    project_dir = PROJECTS_DIR / project_id
    analysis_path = project_dir / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    body = await request.json()
    street   = body.get("address", "")
    postcode = body.get("postcode", "")
    city     = body.get("city", "")

    from brochure_maker.map_generator import geocode_structured
    try:
        lat, lng = await geocode_structured(street=street, postcode=postcode, city=city)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Geocoding failed: {str(e)}")

    with open(analysis_path) as f:
        analysis = json.load(f)
    analysis["address"] = street
    analysis["postcode"] = postcode
    analysis["lat"] = lat
    analysis["lng"] = lng
    with open(analysis_path, "w") as f:
        json.dump(analysis, f, indent=2)

    display_name = ", ".join(filter(None, [street, postcode, city]))
    return JSONResponse({"lat": lat, "lng": lng, "display_name": display_name})


# ──────────────────────────────────────────────
#  Map V1 — Style + Render APIs
# ──────────────────────────────────────────────
@app.post("/api/maps/v1/style/generate")
async def map_v1_generate_style(request: Request):
    """Generate deterministic style tokens from brochure colour + optional vibe/image."""
    if not MAP_V1_ENABLED or map_v1_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DATASET_UNAVAILABLE",
                "message": "Map V1 is disabled",
                "hint": "Enable MAP_V1_ENABLED=1 to use v1 map APIs.",
                "retryable": False,
            },
        )

    try:
        payload = MapStyleGenerateRequestV1(**(await request.json()))
        result = map_v1_service.generate_style(payload)
        return JSONResponse(result.model_dump(mode="json"))
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "STYLE_VALIDATION_FAILED",
                "message": "Invalid style request payload",
                "hint": "Provide brochure_primary_hex and valid optional vibe/image fields.",
                "retryable": False,
                "errors": e.errors(),
            },
        )
    except Exception as e:
        status, detail = map_http_exception(e)
        raise HTTPException(status_code=status, detail=detail)


@app.post("/api/maps/v1/render")
async def map_v1_render(request: Request):
    """Render deterministic SVG+PDF map artifacts from resolved style_tokens."""
    if not MAP_V1_ENABLED or map_v1_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DATASET_UNAVAILABLE",
                "message": "Map V1 is disabled",
                "hint": "Enable MAP_V1_ENABLED=1 to use v1 map APIs.",
                "retryable": False,
            },
        )

    try:
        payload = MapRenderRequestV1(**(await request.json()))
        result = map_v1_service.render(payload)
        return JSONResponse(result.model_dump(mode="json"))
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "STYLE_VALIDATION_FAILED",
                "message": "Invalid render request payload",
                "hint": "Send center/content or project_id plus resolved style_tokens.",
                "retryable": False,
                "errors": e.errors(),
            },
        )
    except Exception as e:
        status, detail = map_http_exception(e)
        raise HTTPException(status_code=status, detail=detail)


@app.get("/api/projects/{project_id}/maps/v1/{map_id}/{filename}")
async def map_v1_serve_artifact(project_id: str, map_id: str, filename: str):
    """Serve map-v1 generated artifacts."""
    allowed = {"map.svg": "image/svg+xml", "map.pdf": "application/pdf", "metadata.json": "application/json", "style_tokens.json": "application/json"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="Artifact not found")

    artifact_path = PROJECTS_DIR / project_id / "maps_v1" / map_id / filename
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(str(artifact_path), media_type=allowed[filename], filename=filename)


# ──────────────────────────────────────────────
#  Neighbourhood Map Generation
# ──────────────────────────────────────────────
@app.post("/api/projects/{project_id}/generate-map")
async def generate_map(project_id: str, request: Request):
    """Generate a neighbourhood map from analysis data."""
    project_dir = PROJECTS_DIR / project_id
    analysis_path = project_dir / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="Project analysis not found")

    with open(analysis_path) as f:
        analysis = json.load(f)

    body = {}
    raw = await request.body()
    if raw:
        body = json.loads(raw)

    brochure_name = analysis.get("brochure_name", "")
    location = analysis.get("location", "")
    primary_colour = body.get(
        "primary_colour",
        analysis.get("colour_scheme", {}).get("primary", "#B8714E"),
    )
    base_hex = body.get("base_hex", primary_colour)
    map_width = max(200, min(2000, int(body.get("map_width", 940))))
    map_height = max(200, min(1500, int(body.get("map_height", 750))))

    # Find travel_map slide for station data
    stations = []
    for slide in analysis.get("slides", []):
        if slide.get("type") == "travel_map":
            stations = slide.get("content", {}).get("stations", [])
            break

    address = f"{brochure_name}, {location}"
    lat = analysis.get("lat")
    lng = analysis.get("lng")
    style = body.get("style", "illustrated")
    radius_m = max(150, min(1000, int(body.get("radius_m", 350))))
    debug = bool(body.get("debug", False))
    colour_scheme_data = analysis.get("colour_scheme", {})
    colour_scheme_data.setdefault("primary", primary_colour)

    try:
        result = await generate_neighbourhood_map(
            address=address,
            location=location,
            stations=stations,
            primary_colour=primary_colour,
            width=map_width,
            height=map_height,
            lat=lat,
            lng=lng,
            style=style,
            colour_scheme=colour_scheme_data,
            building_name=brochure_name,
            base_hex=base_hex,
            radius_m=radius_m,
            debug=debug,
        )
        response = {"message": "Map generated successfully", "format": result["format"]}
        if result["format"] == "svg":
            response["svg_content"] = result["svg_content"]
        else:
            response["image_data_url"] = result["image_data_url"]
        return JSONResponse(response)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Map generation failed: {e}")


# ──────────────────────────────────────────────
#  Auto-Save State Persistence
# ──────────────────────────────────────────────
@app.post("/api/projects/{project_id}/state")
async def save_editor_state(project_id: str, request: Request):
    """Save editor state for a project."""
    project_dir = PROJECTS_DIR / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    body = await request.body()
    state_path = project_dir / "editor_state.json"
    with open(state_path, "wb") as f:
        f.write(body)

    return JSONResponse({"message": "State saved"})


@app.get("/api/projects/{project_id}/state")
async def get_editor_state(project_id: str):
    """Get saved editor state for a project."""
    state_path = PROJECTS_DIR / project_id / "editor_state.json"
    if not state_path.exists():
        raise HTTPException(status_code=404, detail="No saved state")

    with open(state_path) as f:
        state = json.load(f)
    return JSONResponse(state)


# ──────────────────────────────────────────────
#  Server-Side PDF Export (High Quality)
# ──────────────────────────────────────────────
@app.post("/api/projects/{project_id}/export/pdf")
async def export_server_pdf(project_id: str, request: Request):
    """Export a high-quality PDF using headless Chromium (Playwright)."""
    if not HAS_PDF_RENDERER:
        raise HTTPException(
            status_code=501,
            detail="Server-side PDF not available. Install Playwright or Google Chrome/Chromium.",
        )

    project_dir = PROJECTS_DIR / project_id
    exact_metadata_path = project_dir / "exact_metadata.json"
    if exact_metadata_path.exists():
        html = _prepare_exact_export_html(project_dir)
        pdf_path = project_dir / "brochure_export.pdf"
        width_px, height_px = _exact_export_dimensions(project_dir)
        pdf_bytes = await render_pdf(
            html,
            output_path=str(pdf_path),
            width_px=width_px,
            height_px=height_px,
            base_url=str(request.base_url),
        )

        try:
            with open(exact_metadata_path, encoding="utf-8") as f:
                exact_metadata = json.load(f)
            filename = f"{exact_metadata.get('brochure_name') or project_id}.pdf"
        except Exception:
            filename = f"{project_id}.pdf"
        return FileResponse(str(pdf_path), media_type="application/pdf", filename=filename)

    analysis_path = project_dir / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="Project analysis not found")

    with open(analysis_path) as f:
        analysis = json.load(f)

    # Generate clean HTML first
    html = generate_clean_html(analysis=analysis, project_id=project_id)

    # Render to PDF
    pdf_bytes = await render_pdf(html, base_url=str(request.base_url))

    # Save PDF
    pdf_path = project_dir / "brochure_export.pdf"
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename=f"{analysis.get('brochure_name', 'brochure')}.pdf",
    )


# ──────────────────────────────────────────────
#  Run
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000)
