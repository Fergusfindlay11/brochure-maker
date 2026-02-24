"""Generate neighbourhood maps using OSM Nominatim + Leaflet + Playwright."""

import asyncio
import base64
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "BrochureMakerApp/1.0"


async def geocode_address(address: str) -> tuple[float, float]:
    """Convert an address to (latitude, longitude) via Nominatim.

    Returns:
        Tuple of (latitude, longitude).
    """
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            NOMINATIM_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()

    if not data:
        raise ValueError(f"Could not geocode address: {address}")

    return (float(data[0]["lat"]), float(data[0]["lon"]))


def _normalise_postcode(pc: str) -> str:
    """Strip spaces, uppercase, re-insert standard spacing (e.g. 'n16ta' -> 'N1 6TA')."""
    pc = pc.strip().upper().replace(" ", "")
    if len(pc) >= 5:
        return pc[:-3] + " " + pc[-3:]
    return pc


async def geocode_structured(
    street: str = "",
    postcode: str = "",
    city: str = "",
    country: str = "gb",
) -> tuple[float, float]:
    """Geocode using structured fields — more reliable than freeform for UK addresses.

    Uses Nominatim's structured search parameters (street, postalcode, city, country)
    which avoids ambiguity from building names in freeform queries.
    """
    # Normalise postcode before geocoding
    if postcode:
        postcode = _normalise_postcode(postcode)

    params: dict = {
        "street": street,
        "postalcode": postcode,
        "city": city,
        "country": country,
        "format": "json",
        "limit": 1,
    }
    # Drop empty fields — Nominatim works better with fewer, accurate fields
    params = {k: v for k, v in params.items() if v}
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-GB",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(NOMINATIM_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if not data:
        raise ValueError(
            "Could not geocode address — check street address and postcode"
        )

    return (float(data[0]["lat"]), float(data[0]["lon"]))


async def geocode_stations(
    stations: list[dict],
    base_location: str,
) -> list[dict]:
    """Geocode station names to coordinates (best-effort)."""
    results = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for station in stations:
            name = station.get("name", "")
            if not name:
                continue
            query = f"{name} station, {base_location}"
            params = {"q": query, "format": "json", "limit": 1}
            try:
                resp = await client.get(
                    NOMINATIM_URL,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                data = resp.json()
                if data:
                    results.append({
                        "name": name,
                        "time": station.get("time", ""),
                        "lat": float(data[0]["lat"]),
                        "lon": float(data[0]["lon"]),
                    })
                # Nominatim rate limit: max 1 req/sec
                await asyncio.sleep(1.1)
            except Exception:
                continue
    return results


def _hex_to_rgb(hex_colour: str) -> tuple[int, int, int]:
    """Convert #RRGGBB to (r, g, b) tuple."""
    h = hex_colour.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _compute_hue_rotate(hex_colour: str) -> str:
    """Compute a CSS hue-rotate + saturate filter to tint a greyscale map
    towards the given brand colour. Returns a CSS filter string."""
    r, g, b = _hex_to_rgb(hex_colour)
    # Convert to hue (simplified — use the dominant channel approach)
    import colorsys
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    hue_deg = int(h * 360)
    # sepia baseline is ~30deg, so offset from there
    rotate = hue_deg - 30
    sat = max(80, int(s * 200))
    return f"sepia(0.3) hue-rotate({rotate}deg) saturate({sat}%)"


def generate_map_html(
    building_coords: tuple[float, float],
    station_coords: list[dict],
    primary_colour: str,
    width: int = 940,
    height: int = 750,
) -> str:
    """Generate a self-contained HTML page with a Leaflet map."""
    lat, lon = building_coords
    r, g, b = _hex_to_rgb(primary_colour)
    css_filter = _compute_hue_rotate(primary_colour)

    # Build station markers JS
    station_markers_js = ""
    for st in station_coords:
        label = st["name"]
        time_label = st.get("time", "")
        if time_label:
            label += f" — {time_label}"
        station_markers_js += f"""
    L.circleMarker([{st['lat']}, {st['lon']}], {{
      radius: 7, fillColor: '#555', color: '#333', weight: 1.5, fillOpacity: 0.85
    }}).addTo(map).bindTooltip("{label}", {{permanent: true, direction: 'top', className: 'station-label', offset: [0, -10]}});
"""

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ width: {width}px; height: {height}px; overflow: hidden; }}
  #map {{ width: 100%; height: 100%; }}
  .leaflet-tile-pane {{ filter: {css_filter}; }}
  .station-label {{
    background: rgba(255,255,255,0.92) !important;
    border: 1px solid rgba(0,0,0,0.15) !important;
    border-radius: 4px !important;
    padding: 2px 6px !important;
    font-family: 'Segoe UI', Arial, sans-serif !important;
    font-size: 11px !important;
    font-weight: 500 !important;
    color: #333 !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.15) !important;
    white-space: nowrap !important;
  }}
  .building-label {{
    background: rgb({r},{g},{b}) !important;
    border: 2px solid rgba(255,255,255,0.9) !important;
    border-radius: 6px !important;
    padding: 4px 10px !important;
    font-family: 'Segoe UI', Arial, sans-serif !important;
    font-size: 12px !important;
    font-weight: 700 !important;
    color: white !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25) !important;
    letter-spacing: 0.04em !important;
    white-space: nowrap !important;
  }}
  .leaflet-control-attribution {{ display: none !important; }}
  .leaflet-control-zoom {{ display: none !important; }}
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
</head>
<body>
<div id="map"></div>
<script>
  var map = L.map('map', {{
    center: [{lat}, {lon}],
    zoom: 15,
    zoomControl: false,
    attributionControl: false,
    dragging: false,
    scrollWheelZoom: false,
    doubleClickZoom: false,
    touchZoom: false,
  }});

  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}@2x.png', {{
    maxZoom: 19,
    subdomains: 'abcd',
  }}).addTo(map);

  // Building marker
  L.circleMarker([{lat}, {lon}], {{
    radius: 12, fillColor: 'rgb({r},{g},{b})', color: '#fff', weight: 3, fillOpacity: 1
  }}).addTo(map).bindTooltip("\\u2302", {{permanent: true, direction: 'top', className: 'building-label', offset: [0, -14]}});

  // Station markers
  {station_markers_js}

  // Wait for tiles to load then signal ready
  map.whenReady(function() {{
    setTimeout(function() {{
      document.title = 'MAP_READY';
    }}, 2000);
  }});
</script>
</body>
</html>"""
    return html


async def generate_neighbourhood_map(
    address: str,
    location: str,
    stations: list[dict],
    primary_colour: str = "#B8714E",
    width: int = 940,
    height: int = 750,
    lat: float | None = None,
    lng: float | None = None,
    style: str = "illustrated",
    colour_scheme: dict | None = None,
    building_name: str = "",
    base_hex: str = "",
    radius_m: int = 350,
    debug: bool = False,
) -> dict:
    """Generate a neighbourhood map.

    When style="illustrated": returns {"format": "svg", "svg_content": str}
    When style="leaflet": returns {"format": "png", "image_data_url": str}
    Falls back to Leaflet PNG if illustrated rendering fails.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Use pre-stored coordinates if available, otherwise geocode
    if lat is not None and lng is not None:
        building_coords = (lat, lng)
    else:
        geocode_query = f"{address}, {location}" if location not in address else address
        building_coords = await geocode_address(geocode_query)

    # Try illustrated map first (if requested) — returns raw SVG
    if style == "illustrated":
        from brochure_maker.illustrated_map import generate_illustrated_map

        if colour_scheme is None:
            colour_scheme = {
                "primary": primary_colour,
                "primary_dark": primary_colour,
                "primary_light": primary_colour,
                "text_dark": "#3a3a3a",
                "text_light": "#ffffff",
            }

        try:
            svg_content = await generate_illustrated_map(
                building_coords=building_coords,
                building_name=building_name or address.split(",")[0],
                location=location,
                stations=stations,
                colour_scheme=colour_scheme,
                width=width,
                height=height,
                radius_m=radius_m,
                base_hex=base_hex or primary_colour,
                debug=debug,
            )
            return {"format": "svg", "svg_content": svg_content}
        except Exception as e:
            logger.warning("Illustrated map failed (%s), falling back to Leaflet", e)
            # Fall through to Leaflet pipeline below

    # Legacy Leaflet pipeline — renders to PNG via Playwright
    station_coords = await geocode_stations(stations, location)
    map_html = generate_map_html(
        building_coords, station_coords, primary_colour, width, height
    )

    import subprocess, sys, os

    render_script = str(Path(__file__).with_name("render_map.py"))
    local_browsers = Path(__file__).resolve().parent.parent / "pw-browsers"

    def _render_subprocess() -> bytes:
        env = dict(os.environ)
        if local_browsers.exists():
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
        proc = subprocess.run(
            [sys.executable, render_script, str(width), str(height)],
            input=map_html.encode("utf-8"),
            capture_output=True,
            timeout=60,
            env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))
        return proc.stdout

    screenshot_bytes = await asyncio.to_thread(_render_subprocess)

    b64 = base64.b64encode(screenshot_bytes).decode("ascii")
    return {"format": "png", "image_data_url": f"data:image/png;base64,{b64}"}
