"""Convert PyMuPDF page drawings into editable SVG overlay data.

This helper intentionally extracts only PDF vector drawing commands. Text,
embedded raster images, shadings, and full-page bitmap fallbacks stay outside
this module so callers can layer recolourable line art over whatever exact
layout renderer they are using.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import fitz


DEFAULT_OVERLAY_CLASS = "pdf-vector-overlay"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def extract_page_svg_paths(
    page: fitz.Page,
    *,
    precision: int = 3,
    page_number: int | None = None,
    min_area: float = 0.0,
    include_invisible: bool = False,
) -> list[dict[str, Any]]:
    """Return structured SVG path data for a PyMuPDF page's vector drawings."""
    if page_number is None:
        page_number = _page_number(page)

    return drawings_to_svg_paths(
        page.get_drawings(),
        precision=precision,
        page_number=page_number,
        min_area=min_area,
        include_invisible=include_invisible,
    )


def drawings_to_svg_paths(
    drawings: Iterable[Mapping[str, Any]],
    *,
    precision: int = 3,
    page_number: int | None = None,
    min_area: float = 0.0,
    include_invisible: bool = False,
) -> list[dict[str, Any]]:
    """Convert ``page.get_drawings()`` records into serializable SVG paths."""
    paths: list[dict[str, Any]] = []

    for drawing in drawings:
        path_data = _drawing_to_path_data(
            drawing.get("items", []),
            close_path=bool(drawing.get("closePath")),
            precision=precision,
        )
        if not path_data:
            continue

        bbox = _rect_to_bbox(drawing.get("rect"), precision)
        if min_area > 0 and bbox["width"] * bbox["height"] < min_area:
            continue

        stroke = _color_to_hex(drawing.get("color"))
        fill = _color_to_hex(drawing.get("fill"))
        stroke_width = _round(drawing.get("width") or 0.0, precision)
        if not include_invisible and not fill and (not stroke or stroke_width <= 0):
            continue

        path_index = len(paths) + 1
        path_id = _path_id(page_number, path_index)
        dash = _parse_dashes(drawing.get("dashes"), precision)
        source_type = str(drawing.get("type", ""))

        paths.append(
            {
                "id": path_id,
                "d": path_data,
                "bbox": bbox,
                "source_type": source_type,
                "seqno": drawing.get("seqno"),
                "stroke": stroke,
                "fill": fill,
                "stroke_width": stroke_width,
                "stroke_opacity": _optional_round(drawing.get("stroke_opacity"), precision),
                "fill_opacity": _optional_round(drawing.get("fill_opacity"), precision),
                "line_cap": _line_cap(drawing.get("lineCap")),
                "line_join": _line_join(drawing.get("lineJoin")),
                "dash": dash,
                "fill_rule": "evenodd" if drawing.get("even_odd") else "nonzero",
                "editable": True,
            }
        )

    return paths


def page_drawings_to_svg_overlay(
    page: fitz.Page,
    *,
    precision: int = 3,
    page_number: int | None = None,
    css_width: float | None = None,
    css_height: float | None = None,
    left: float = 0.0,
    top: float = 0.0,
    scale: float = 1.0,
    css_unit: str = "px",
    class_name: str = DEFAULT_OVERLAY_CLASS,
    min_area: float = 0.0,
    include_invisible: bool = False,
) -> str:
    """Render a positioned SVG overlay for a PyMuPDF page's vector drawings."""
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    paths = extract_page_svg_paths(
        page,
        precision=precision,
        page_number=page_number,
        min_area=min_area,
        include_invisible=include_invisible,
    )
    return svg_paths_to_overlay(
        paths,
        page_width=page_width,
        page_height=page_height,
        precision=precision,
        css_width=css_width,
        css_height=css_height,
        left=left,
        top=top,
        scale=scale,
        css_unit=css_unit,
        class_name=class_name,
    )


def svg_paths_to_overlay(
    paths: Iterable[Mapping[str, Any]],
    *,
    page_width: float,
    page_height: float,
    precision: int = 3,
    css_width: float | None = None,
    css_height: float | None = None,
    left: float = 0.0,
    top: float = 0.0,
    scale: float = 1.0,
    css_unit: str = "px",
    class_name: str = DEFAULT_OVERLAY_CLASS,
) -> str:
    """Render structured path data as a positioned SVG overlay string."""
    page_width = _round(page_width, precision)
    page_height = _round(page_height, precision)
    css_width = _round(page_width * scale if css_width is None else css_width, precision)
    css_height = _round(page_height * scale if css_height is None else css_height, precision)
    left = _round(left, precision)
    top = _round(top, precision)

    style = (
        f"position:absolute;left:{_format_number(left, precision)}{css_unit};"
        f"top:{_format_number(top, precision)}{css_unit};"
        f"width:{_format_number(css_width, precision)}{css_unit};"
        f"height:{_format_number(css_height, precision)}{css_unit};"
        "pointer-events:none;overflow:visible;"
    )
    path_markup = "\n".join(_path_to_svg(path, precision) for path in paths)

    return (
        f'<svg xmlns="{SVG_NAMESPACE}" class="{html.escape(class_name, quote=True)}" '
        f'viewBox="0 0 {_format_number(page_width, precision)} {_format_number(page_height, precision)}" '
        f'width="{_format_number(css_width, precision)}{css_unit}" '
        f'height="{_format_number(css_height, precision)}{css_unit}" '
        f'preserveAspectRatio="none" aria-hidden="true" '
        f'data-vector-overlay="pymupdf-drawings" data-coordinate-system="top-left" '
        f'style="{html.escape(style, quote=True)}">\n'
        f"{path_markup}\n"
        "</svg>"
    )


def _drawing_to_path_data(
    items: Iterable[Sequence[Any]],
    *,
    close_path: bool,
    precision: int,
) -> str:
    commands: list[str] = []
    current: tuple[float, float] | None = None

    for item in items:
        if not item:
            continue
        operator = str(item[0])

        if operator == "l" and len(item) >= 3:
            start = _point_to_tuple(item[1])
            end = _point_to_tuple(item[2])
            current = _move_to_if_needed(commands, current, start, precision)
            commands.append(f"L {_format_point(end, precision)}")
            current = end
        elif operator == "c" and len(item) >= 5:
            start = _point_to_tuple(item[1])
            control_1 = _point_to_tuple(item[2])
            control_2 = _point_to_tuple(item[3])
            end = _point_to_tuple(item[4])
            current = _move_to_if_needed(commands, current, start, precision)
            commands.append(
                "C "
                f"{_format_point(control_1, precision)} "
                f"{_format_point(control_2, precision)} "
                f"{_format_point(end, precision)}"
            )
            current = end
        elif operator == "re" and len(item) >= 2:
            commands.extend(_rect_to_path_commands(item[1], item[2] if len(item) >= 3 else 1, precision))
            current = None
        elif operator == "qu" and len(item) >= 2:
            points = _quad_points(item[1])
            if len(points) == 4:
                current = _move_to_if_needed(commands, current, points[0], precision)
                for point in points[1:]:
                    commands.append(f"L {_format_point(point, precision)}")
                commands.append("Z")
                current = None
        elif operator == "m" and len(item) >= 2:
            current = _point_to_tuple(item[1])
            commands.append(f"M {_format_point(current, precision)}")
        elif operator == "h":
            commands.append("Z")
            current = None

    if close_path and commands and commands[-1] != "Z":
        commands.append("Z")

    return " ".join(commands)


def _path_to_svg(path: Mapping[str, Any], precision: int) -> str:
    stroke = path.get("stroke")
    fill = path.get("fill")
    stroke_width = float(path.get("stroke_width", 0.0) or 0.0)
    classes = ["pdf-vector-path"]
    if fill:
        classes.append("has-fill")
    if stroke and stroke_width > 0:
        classes.append("has-stroke")

    attrs = {
        "id": str(path.get("id", "")),
        "class": " ".join(classes),
        "d": str(path.get("d", "")),
        "fill": f"var(--pdf-vector-fill, {fill})" if fill else "none",
        "stroke": f"var(--pdf-vector-stroke, {stroke})" if stroke and stroke_width > 0 else "none",
        "data-editable": "line-art",
        "data-source-type": str(path.get("source_type", "")),
        "data-original-fill": fill or "none",
        "data-original-stroke": stroke or "none",
        "data-bbox": _format_bbox(path.get("bbox", {}), precision),
    }

    if fill:
        attrs["fill-rule"] = str(path.get("fill_rule") or "nonzero")
    if stroke and stroke_width > 0:
        attrs["stroke-width"] = _format_number(stroke_width, precision)
        attrs["stroke-linecap"] = str(path.get("line_cap") or "butt")
        attrs["stroke-linejoin"] = str(path.get("line_join") or "miter")

    stroke_opacity = path.get("stroke_opacity")
    if stroke_opacity is not None and stroke and stroke_width > 0:
        attrs["stroke-opacity"] = _format_number(stroke_opacity, precision)

    fill_opacity = path.get("fill_opacity")
    if fill_opacity is not None and fill:
        attrs["fill-opacity"] = _format_number(fill_opacity, precision)

    dash = path.get("dash")
    if isinstance(dash, Mapping) and dash.get("array") and stroke and stroke_width > 0:
        attrs["stroke-dasharray"] = str(dash["svg"])
        if float(dash.get("offset", 0.0) or 0.0):
            attrs["stroke-dashoffset"] = _format_number(dash["offset"], precision)

    return "  <path " + " ".join(_xml_attr(name, value) for name, value in attrs.items()) + " />"


def _move_to_if_needed(
    commands: list[str],
    current: tuple[float, float] | None,
    point: tuple[float, float],
    precision: int,
) -> tuple[float, float]:
    if current is None or not _same_point(current, point):
        commands.append(f"M {_format_point(point, precision)}")
    return point


def _rect_to_path_commands(rect: Any, orientation: Any, precision: int) -> list[str]:
    x0, y0, x1, y1 = _rect_tuple(rect)
    clockwise = float(orientation or 1) >= 0
    if clockwise:
        points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    else:
        points = [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]
    return [
        f"M {_format_point(points[0], precision)}",
        f"L {_format_point(points[1], precision)}",
        f"L {_format_point(points[2], precision)}",
        f"L {_format_point(points[3], precision)}",
        "Z",
    ]


def _point_to_tuple(point: Any) -> tuple[float, float]:
    if hasattr(point, "x") and hasattr(point, "y"):
        return float(point.x), float(point.y)
    values = list(point)
    return float(values[0]), float(values[1])


def _rect_tuple(rect: Any) -> tuple[float, float, float, float]:
    if hasattr(rect, "x0") and hasattr(rect, "y0") and hasattr(rect, "x1") and hasattr(rect, "y1"):
        return float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    values = list(rect)
    return float(values[0]), float(values[1]), float(values[2]), float(values[3])


def _quad_points(quad: Any) -> list[tuple[float, float]]:
    if all(hasattr(quad, name) for name in ("ul", "ur", "lr", "ll")):
        return [_point_to_tuple(quad.ul), _point_to_tuple(quad.ur), _point_to_tuple(quad.lr), _point_to_tuple(quad.ll)]
    return [_point_to_tuple(point) for point in quad]


def _rect_to_bbox(rect: Any, precision: int) -> dict[str, float]:
    if rect is None:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    x0, y0, x1, y1 = _rect_tuple(rect)
    return {
        "x": _round(x0, precision),
        "y": _round(y0, precision),
        "width": _round(x1 - x0, precision),
        "height": _round(y1 - y0, precision),
    }


def _color_to_hex(color: Any) -> str | None:
    if color is None:
        return None
    values = list(color)
    if not values:
        return None
    if len(values) == 1:
        red = green = blue = values[0]
    else:
        red, green, blue = values[:3]
    return f"#{_color_channel(red):02x}{_color_channel(green):02x}{_color_channel(blue):02x}"


def _color_channel(value: Any) -> int:
    return max(0, min(255, int(round(float(value) * 255))))


def _parse_dashes(value: Any, precision: int) -> dict[str, Any] | None:
    if not value:
        return None
    text = str(value).strip()
    match = re.match(r"^\[([^\]]*)\]\s*([-+]?[0-9]*\.?[0-9]+)?$", text)
    if not match:
        return None
    array = [_round(part, precision) for part in re.split(r"[\s,]+", match.group(1).strip()) if part]
    if not array:
        return None
    offset = _round(match.group(2) or 0.0, precision)
    return {
        "array": array,
        "offset": offset,
        "svg": " ".join(_format_number(part, precision) for part in array),
    }


def _line_cap(value: Any) -> str:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        value = value[0] if value else 0
    return {0: "butt", 1: "round", 2: "square"}.get(int(float(value or 0)), "butt")


def _line_join(value: Any) -> str:
    return {0: "miter", 1: "round", 2: "bevel"}.get(int(float(value or 0)), "miter")


def _path_id(page_number: int | None, path_index: int) -> str:
    if page_number is None:
        return f"pdf-vector-{path_index:04d}"
    return f"p{int(page_number):03d}-vector-{path_index:04d}"


def _page_number(page: fitz.Page) -> int | None:
    try:
        number = int(page.number)
    except Exception:
        return None
    return number + 1 if number >= 0 else None


def _optional_round(value: Any, precision: int) -> float | None:
    if value is None:
        return None
    return _round(value, precision)


def _round(value: Any, precision: int) -> float:
    rounded = round(float(value), precision)
    return 0.0 if rounded == -0.0 else rounded


def _format_point(point: tuple[float, float], precision: int) -> str:
    return f"{_format_number(point[0], precision)} {_format_number(point[1], precision)}"


def _format_number(value: Any, precision: int) -> str:
    rounded = _round(value, precision)
    text = f"{rounded:.{precision}f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_bbox(value: Any, precision: int) -> str:
    if not isinstance(value, Mapping):
        return "0 0 0 0"
    return " ".join(
        _format_number(value.get(key, 0.0), precision)
        for key in ("x", "y", "width", "height")
    )


def _same_point(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return abs(left[0] - right[0]) < 0.00001 and abs(left[1] - right[1]) < 0.00001


def _xml_attr(name: str, value: Any) -> str:
    return f'{name}="{html.escape(str(value), quote=True)}"'
