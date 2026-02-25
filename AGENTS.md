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
