"""Runtime configuration for Map V1."""

from __future__ import annotations

import os
from pathlib import Path

MAP_V1_ENABLED = os.getenv("MAP_V1_ENABLED", "1") == "1"
MAP_V1_STYLE_VERSION = os.getenv("MAP_V1_STYLE_VERSION", "1.0.0")
MAP_V1_RENDERER_VERSION = os.getenv("MAP_V1_RENDERER_VERSION", "1.1.0")
MAP_V1_DATASET_VERSION = os.getenv("MAP_V1_DATASET_VERSION", "unversioned")
MAP_V1_DEFAULT_RADIUS_M = int(os.getenv("MAP_V1_DEFAULT_RADIUS_M", "900"))
MAP_V1_1_ENABLE_STRICT = os.getenv("MAP_V1_1_ENABLE_STRICT", "0") == "1"
MAP_V1_POI_QUERY_TIMEOUT_MS = int(os.getenv("MAP_V1_POI_QUERY_TIMEOUT_MS", "1200"))

MAP_V1_DUCKDB_PATH = Path(os.getenv("MAP_V1_DUCKDB_PATH", "./projects/map_v1_data.duckdb")).expanduser()
MAP_V1_REQUIRED_FONTS = [
    part.strip() for part in os.getenv("MAP_V1_REQUIRED_FONTS", "Helvetica,Baskerville").split(",") if part.strip()
]
MAP_V1_RSVG_BIN = os.getenv("MAP_V1_RSVG_BIN", "rsvg-convert")
MAP_V1_FONTCONFIG_FILE = os.getenv("MAP_V1_FONTCONFIG_FILE", "")
MAP_V1_FONT_DIR = os.getenv("MAP_V1_FONT_DIR", "")

# Label classes that should trigger UI warnings when dropped.
MAP_V1_CRITICAL_LABEL_CLASSES = {
    part.strip().lower()
    for part in os.getenv("MAP_V1_CRITICAL_LABEL_CLASSES", "transit,station,building").split(",")
    if part.strip()
}
