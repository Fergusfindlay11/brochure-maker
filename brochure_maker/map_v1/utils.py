"""Shared utility functions for map-v1 modules."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path


def canonical_json(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(data: object, length: int = 16) -> str:
    digest = hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
    return digest[:length]


def parse_hex(value: str) -> tuple[int, int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) == 6:
        raw += "FF"
    if len(raw) != 8:
        raise ValueError(f"invalid hex colour '{value}'")
    return (
        int(raw[0:2], 16),
        int(raw[2:4], 16),
        int(raw[4:6], 16),
        int(raw[6:8], 16),
    )


def to_hex(rgba: tuple[int, int, int, int], keep_alpha: bool = False) -> str:
    r, g, b, a = [max(0, min(255, int(v))) for v in rgba]
    if keep_alpha or a < 255:
        return f"#{r:02X}{g:02X}{b:02X}{a:02X}"
    return f"#{r:02X}{g:02X}{b:02X}"


def blend(hex_a: str, hex_b: str, t: float, keep_alpha: bool = False) -> str:
    t = max(0.0, min(1.0, t))
    ar, ag, ab, aa = parse_hex(hex_a)
    br, bg, bb, ba = parse_hex(hex_b)
    out = (
        round(ar + (br - ar) * t),
        round(ag + (bg - ag) * t),
        round(ab + (bb - ab) * t),
        round(aa + (ba - aa) * t),
    )
    return to_hex(out, keep_alpha=keep_alpha)


def relative_luminance(hex_colour: str) -> float:
    r, g, b, _ = parse_hex(hex_colour)

    def _channel(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    rl = _channel(r)
    gl = _channel(g)
    bl = _channel(b)
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    la = relative_luminance(hex_a)
    lb = relative_luminance(hex_b)
    hi, lo = (la, lb) if la > lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


def force_contrast(foreground: str, background: str, minimum: float = 4.5) -> str:
    if contrast_ratio(foreground, background) >= minimum:
        return foreground

    black = "#111111"
    white = "#F6F6F6"
    if contrast_ratio(black, background) >= contrast_ratio(white, background):
        candidate = black
    else:
        candidate = white
    if contrast_ratio(candidate, background) >= minimum:
        return candidate

    # As a final deterministic fallback, binary-search blend toward opposite tone.
    tone = white if candidate == black else black
    low, high = 0.0, 1.0
    best = candidate
    for _ in range(24):
        mid = (low + high) / 2.0
        probe = blend(candidate, tone, mid)
        if contrast_ratio(probe, background) >= minimum:
            best = probe
            high = mid
        else:
            low = mid
    return best


def decode_image_reference(style_image_ref: str) -> bytes | None:
    """Decode data URL or file path reference into bytes."""
    if not style_image_ref:
        return None

    text = style_image_ref.strip()
    if text.startswith("data:image/") and ";base64," in text:
        _, b64 = text.split(",", 1)
        return base64.b64decode(b64)

    path = Path(text)
    if path.exists() and path.is_file():
        return path.read_bytes()

    return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bbox_from_center(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    lat_off = radius_m / 111320.0
    lon_off = radius_m / (111320.0 * max(0.2, math.cos(math.radians(lat))))
    return (lon - lon_off, lat - lat_off, lon + lon_off, lat + lat_off)


def project_web_mercator(
    lon: float,
    lat: float,
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
    padding: int = 24,
) -> tuple[float, float]:
    """Project lon/lat into map pixel coordinates using a local bbox transform."""
    min_lon, min_lat, max_lon, max_lat = bbox
    usable_w = max(1, width - 2 * padding)
    usable_h = max(1, height - 2 * padding)
    x = padding + ((lon - min_lon) / max(1e-9, (max_lon - min_lon))) * usable_w
    y = padding + ((max_lat - lat) / max(1e-9, (max_lat - min_lat))) * usable_h
    return (round(x, 2), round(y, 2))


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v
