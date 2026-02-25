"""Brochure Maker — FastAPI application."""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import ValidationError
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from brochure_maker.pdf_extractor import extract_pdf
from brochure_maker.ai_analyser import analyse_brochure, analyse_brochure_streaming
from brochure_maker.html_generator import generate_brochure_html, generate_clean_html, generate_linkedin_cards
from brochure_maker.ai_rewriter import rewrite_text
from brochure_maker.ai_chat import chat_with_brochure
from brochure_maker.map_generator import generate_neighbourhood_map
from brochure_maker.pdf_renderer import render_pdf, HAS_PLAYWRIGHT
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
)

BASE_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = BASE_DIR / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)
map_v1_service = MapV1Service(PROJECTS_DIR) if MAP_V1_ENABLED else None

app = FastAPI(title="Brochure Maker", version="1.0.0")

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


@app.on_event("startup")
async def _map_v1_startup_checks() -> None:
    """Fail-fast checks for map-v1 PDF typography support."""
    if MAP_V1_ENABLED:
        assert_pdf_runtime_ready()


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

        # Save analysis JSON
        analysis_path = os.path.join(project_dir, "analysis.json")
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)

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
            brochure_name = "Brochure"
            slide_count = 0
            if analysis_path.exists():
                with open(analysis_path) as f:
                    analysis = json.load(f)
                    brochure_name = analysis.get("brochure_name", "Brochure")
                    slide_count = len(analysis.get("slides", []))
            return JSONResponse({
                "status": "complete",
                "message": "Brochure ready.",
                "brochure_url": f"/api/projects/{project_id}/brochure",
                "brochure_name": brochure_name,
                "slide_count": slide_count,
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


# ──────────────────────────────────────────────
#  Clean HTML Export (Presentation Mode)
# ──────────────────────────────────────────────
@app.get("/api/projects/{project_id}/export/html", response_class=HTMLResponse)
async def export_clean_html(project_id: str):
    """Export a clean, presentation-only HTML (no editor UI)."""
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


# ──────────────────────────────────────────────
#  Project Management
# ──────────────────────────────────────────────
@app.get("/api/projects")
async def list_projects():
    """List all projects."""
    projects = []
    if PROJECTS_DIR.exists():
        for project_dir in sorted(PROJECTS_DIR.iterdir(), reverse=True):
            if not project_dir.is_dir():
                continue
            project_id = project_dir.name
            has_brochure = (project_dir / "brochure.html").exists()
            has_pdf = (project_dir / "source.pdf").exists()

            # Get name from analysis if available
            name = "Untitled"
            analysis_path = project_dir / "analysis.json"
            if analysis_path.exists():
                try:
                    with open(analysis_path) as f:
                        analysis = json.load(f)
                        name = analysis.get("brochure_name", "Untitled")
                except Exception:
                    pass

            # Check in-memory status
            status = project_status.get(project_id, {}).get("status", "complete" if has_brochure else "unknown")

            projects.append({
                "id": project_id,
                "name": name,
                "has_brochure": has_brochure,
                "has_pdf": has_pdf,
                "status": status,
            })

    return JSONResponse({"projects": projects})


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
    analysis_path = PROJECTS_DIR / project_id / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="Project analysis not found")

    try:
        with open(analysis_path) as f:
            analysis = json.load(f)

        # Use first page render as preview
        render_path = PROJECTS_DIR / project_id / "renders" / "page1.png"
        preview = str(render_path) if render_path.exists() else None

        template_def = save_template(name, analysis, preview)
        return JSONResponse({
            "message": f"Template '{name}' saved.",
            "template": template_def,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save template: {str(e)}")


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
async def export_server_pdf(project_id: str):
    """Export a high-quality PDF using headless Chromium (Playwright)."""
    if not HAS_PLAYWRIGHT:
        raise HTTPException(
            status_code=501,
            detail="Server-side PDF not available. Install Playwright: pip install playwright && python -m playwright install chromium",
        )

    analysis_path = PROJECTS_DIR / project_id / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="Project analysis not found")

    with open(analysis_path) as f:
        analysis = json.load(f)

    # Generate clean HTML first
    html = generate_clean_html(analysis=analysis, project_id=project_id)

    # Render to PDF
    pdf_bytes = await render_pdf(html)

    # Save PDF
    pdf_path = PROJECTS_DIR / project_id / "brochure_export.pdf"
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
