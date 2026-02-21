"""Brochure Maker — FastAPI application."""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

load_dotenv()

from brochure_maker.pdf_extractor import extract_pdf
from brochure_maker.ai_analyser import analyse_brochure, analyse_brochure_streaming
from brochure_maker.html_generator import generate_brochure_html
from brochure_maker.template_manager import (
    save_template,
    load_template,
    list_templates,
    delete_template,
)

BASE_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = BASE_DIR / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)

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
#  Run
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
