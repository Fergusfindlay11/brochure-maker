# AGENTS.md

## Workspace Orientation
- Parent folder: `/Users/fergusfindlay/Documents/Brochure Maker `
- Git repo root: `/Users/fergusfindlay/Documents/Brochure Maker /brochure-maker`
- Main backend entrypoint: `/Users/fergusfindlay/Documents/Brochure Maker /brochure-maker/app.py`
- Map V1 pipeline code: `/Users/fergusfindlay/Documents/Brochure Maker /brochure-maker/brochure_maker/map_v1`
- Frontend map integration: `/Users/fergusfindlay/Documents/Brochure Maker /brochure-maker/static/js/map-generator.js`
- Ingestion/extraction scripts: `/Users/fergusfindlay/Documents/Brochure Maker /brochure-maker/scripts`
- Tests: `/Users/fergusfindlay/Documents/Brochure Maker /brochure-maker/tests`

## Notes
- Do not run git commands in the parent folder; it is not a git repository.
- Prefer map-v1 APIs:
  - `POST /api/maps/v1/style/generate`
  - `POST /api/maps/v1/render`
- Legacy endpoint `/api/projects/{project_id}/generate-map` should only be used as opt-in fallback.

## Exact PDF-To-Editable-HTML Pipeline

When the user asks to test, improve, or benchmark a brochure PDF import, treat this
as exact PDF pipeline work. The target is not "render a PDF as HTML"; the target is
a fresh `/api/upload/exact` import with editable brochure features, persistent
state, clean export, and Browser-verified page fidelity.

### Canonical Flow
- Start from the repo root: `/Users/fergusfindlay/Documents/Brochure Maker /brochure-maker`.
- Use the real app pipeline: `/api/upload/exact` writes `source.pdf`, runs
  `write_exact_layout_model`, `generate_exact_pdf_layout`, `write_design_graph`,
  and stores `exact_metadata.json`.
- Generated project evidence should include:
  - `brochure.html`
  - `source.pdf`
  - `exact_metadata.json`
  - `brochure.design.json`
  - `exact_layout_model/exact-layout.json`
  - `exact_layout_model/extraction-inventory.json`
  - `browser_qa.json`
  - `browser_ui_qa.json`
  - `export_qa.json` or `evals/reports/{project_id}/export-qa.json`
  - `evals/reports/{project_id}/page-packets.json`
  - `evals/reports/{project_id}/critic-report.json`
  - `evals/reports/{project_id}/control-quality.json`
  - `evals/reports/{project_id}-visual/visual-diff.json`
- Canonical matrix manifest: `evals/exact-benchmark-manifest.json`. Do not use
  stale mirror manifests under `evals/benchmarks/`.
- Use existing projects only as QA references. Do not copy their HTML, editor
  state, fixed coordinates, manually corrected assets, or project-specific data.

### Acceptance Contract
- Browser is the acceptance gate. Always inspect the real editor and clean export
  in the in-app Browser when judging UI/editability.
- `browser_qa.json` is the fast HTTP/state Browser gate. `browser_ui_qa.json`
  is the in-app Browser UI contract and must be present in benchmark reports.
- A project is not accepted unless the critic score is 95%+ with no hard blockers.
- Each page should have source, generated, diff, Browser, design, inventory, and
  critique evidence. Failed pages enter focused repair; passing pages should not
  be rewritten without cause.
- Clean export at `/api/projects/{project_id}/export/html` must preserve edits and
  contain no editor chrome: no `contenteditable`, file inputs, toolbars, field
  panels, or editor scripts.
- PDF export at `/api/projects/{project_id}/export/pdf` must succeed with the
  same edited state applied.
- Reload persistence is required for edited text, colours, typography, icons,
  logos, images, maps, floor/space plans, contacts, and agency logos.
- Never mark work done from visual-diff JSON alone. Inspect Browser evidence and
  the generated files on disk.

### Feature Checklist
For each fresh PDF, verify that the pipeline identifies and renders:
- Page purposes, layout roles, background colours, and global palette controls.
- Embedded fonts, typography roles, editable text blocks, and font retention while
  typing or pasting.
- Photo slots, hero/gallery regions, image fit/crop modes, and static background
  textures.
- Source/facade marks, repeated header marks, logo slots, agency logos, and logo
  replacement controls.
- Amenity/service icons, icon-bank selection, transparent icon backgrounds, and
  global icon colour/size/stroke controls.
- Maps, map labels, POI/category colours, subject markers, and preserve/regenerate
  controls where available.
- Floor plans or space plans as replaceable `space-plan` regions, not generic
  photo slots.
- Contact blocks as semantic name/phone/email groups, separate from agency logos.

### Repair Workflow
- First inspect `brochure.design.json`, `extraction-inventory.json`, page packets,
  Browser QA, visual diff, and HTML assessment before changing code.
- Classify failures by subsystem: typography/text reconstruction, image regions,
  logos, icons, maps, space plans, contacts, export/state, or global controls.
- Fix reusable pipeline code, not one project output. Avoid Austin/Sekforde/Royal
  Exchange/Whittington coordinate hacks unless expressed as a generic heuristic
  with tests.
- After a code repair, rerun the affected page packet or focused benchmark first,
  then rerun a fresh upload/benchmark to prove the fix works from the PDF.
- If a page scores below 95, create or use a page-level repair plan from the page
  packet before making broader changes.

### Brochure Judgement Agents
Codex chat acts as the orchestrator. These named agents are triggered inside the
flow by reading page-packet evidence and writing JSON marks. The first
implementation is deterministic artifact generation; real subagents can inspect
the same files before final acceptance.

Each page packet must include:
- `visual-critic.json`
- `browser-interaction-qa.json`
- `editability-critic.json`
- `extraction-diagnosis.json`
- `repair-planner.json`
- `hardcoding-critic.json`

Agent contract:
- Inputs: `source.png`, `generated.png`, `diff.png`, `browser-screenshot.png`
  when available, `design-page.json`, `inventory-page.json`,
  `browser-page-qa.json`, `page-assessment.json`, `image-regions.json`,
  `pdf-text-spans.json`, `pdf-vectors.json`, and `text-reconstruction.json`.
- Output JSON: `schema`, `agent`, `purpose`, `accepted`, `status`,
  `blockers`, plus agent-specific evidence and next actions.
- Pass rule: a page that numerically passes cannot be accepted if any judgement
  agent rejects it or if any required agent artifact is missing.
- Fail rule: failed pages must name the likely subsystem and feed reusable
  repair tasks through `brochure_maker/exact_code_repair_tasks.py`.

Agent roles:
- Visual Critic: compares source, generated, diff, and Browser screenshot
  evidence; rejects sub-95 scores and major visual findings.
- Browser Interaction QA Agent: checks hover/click/edit/export evidence and
  flags giant overlays, draggable preserved backgrounds, text overlaps, and
  clean-export chrome.
- Feature Editability Agent: checks whether text, photos, artwork, maps, logos,
  amenities, space plans, contacts, and agency logos are editable or explicitly
  static.
- Extraction Diagnosis Agent: maps defects to typography, images, vectors,
  icons, maps, space plans, contacts, state, or export.
- Reusable Repair Planner Agent: converts diagnosis into page-scoped reusable
  repair tasks and rejects failed pages without tasks.
- Hardcoding Critic: rejects copied project output, manual coordinates, and
  brochure-specific patches unless expressed as generic evidence-derived
  heuristics with tests.

### Useful Commands
- Start the app if needed: `python3 app.py`.
- Fresh benchmark a PDF:
  `python3 -m brochure_maker.exact_benchmark --pdf "/path/to/brochure.pdf" --base-url http://127.0.0.1:8000`.
- Re-evaluate an existing project:
  `python3 -m brochure_maker.exact_benchmark --project-dir projects/{project_id} --base-url http://127.0.0.1:8000`.
- Write Browser evidence for an exact project:
  `python3 -m brochure_maker.exact_browser_qa projects/{project_id} --base-url http://127.0.0.1:8000 --force`.
- Write/normalize in-app Browser UI evidence:
  `python3 -m brochure_maker.exact_browser_ui_qa projects/{project_id} --base-url http://127.0.0.1:8000 --export-qa evals/reports/{project_id}/export-qa.json --force`.
- Run the canonical exact benchmark matrix:
  `python3 -m brochure_maker.exact_benchmark_matrix --manifest evals/exact-benchmark-manifest.json --base-url http://127.0.0.1:8000`.
- Run the main exact pipeline tests after reusable changes:
  `python3 -m pytest tests/test_exact_layout_fields.py tests/test_pdf_exact_layout.py tests/test_exact_export.py tests/test_exact_html_assessment.py tests/test_exact_page_packets.py tests/test_exact_benchmark.py tests/test_exact_eval.py -q`.
