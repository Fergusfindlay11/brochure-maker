# Map V1.1 Setup

## Runtime env vars
- `MAP_V1_ENABLED=1`
- `MAP_V1_DUCKDB_PATH=./projects/map_v1_data.duckdb`
- `MAP_V1_DATASET_VERSION=<release id>`
- `MAP_V1_STYLE_VERSION=1.0.0`
- `MAP_V1_RENDERER_VERSION=1.1.0`
- `MAP_V1_1_ENABLE_STRICT=1` (recommended for staging/prod)
- `MAP_V1_POI_QUERY_TIMEOUT_MS=1200`
- `MAP_V1_RSVG_BIN=rsvg-convert`
- `MAP_V1_FONTCONFIG_FILE=/etc/fonts/fonts.conf` (optional)
- `MAP_V1_FONT_DIR=/app/fonts` (optional)
- `MAP_V1_REQUIRED_FONTS=Helvetica,Baskerville`

## Data ingestion
1. Ingest Overture Places parquet into DuckDB:

```bash
python3 scripts/ingest_map_v1_duckdb.py \
  --db ./projects/map_v1_data.duckdb \
  --overture-parquet "/path/to/overture/places/*.parquet" \
  --release-id 2026-02-18.0
```

2. Extract + stitch basemap features from PMTiles (or a pre-extracted GeoJSON):

```bash
python3 scripts/extract_map_v1_basemap.py \
  --pmtiles /path/to/basemap.pmtiles \
  --center-lat 51.5369 \
  --center-lon -0.1060 \
  --radius-m 900 \
  --width-px 940 \
  --height-px 750 \
  --output /tmp/basemap_features.geojson
```

3. Ingest the stitched basemap features:

```bash
python3 scripts/ingest_map_v1_duckdb.py \
  --db ./projects/map_v1_data.duckdb \
  --basemap-geojson /tmp/basemap_features.geojson \
  --release-id basemap-2026-02-18.0
```

The ingest script writes a deterministic manifest checksum at `<db>.manifest.json`.

## API flow
1. `POST /api/maps/v1/style/generate`
2. `POST /api/maps/v1/render` with resolved `style_tokens`
3. Fetch returned `artifacts.svg_url` and `artifacts.pdf_url`

## Error taxonomy
- `DATASET_UNAVAILABLE`
- `PMTILES_READ_ERROR`
- `POI_QUERY_TIMEOUT`
- `FONT_MISSING`
- `STYLE_VALIDATION_FAILED`
- `EXPORT_FAILED`

`RENDER_DETERMINISM_VIOLATION` is internal and intentionally masked from user-facing payloads.
