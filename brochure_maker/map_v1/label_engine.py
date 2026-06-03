"""Collision-safe label placement with push-before-drop behavior."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import StyleTokens
from .text_metrics import TextMeasureInput, label_bbox_from_baseline, measure_text, resolve_label_font


@dataclass
class LabelCandidate:
    text: str
    label_class: str
    anchor_x: float
    anchor_y: float
    priority: int
    font_size: float
    critical: bool = False


@dataclass
class PlacedLabel:
    text: str
    label_class: str
    anchor_x: float
    anchor_y: float
    x: float
    y: float
    priority: int
    pushed: bool = False
    leader_to_x: float | None = None
    leader_to_y: float | None = None


class _RectIndex:
    """Lightweight grid index for rectangle collision checks."""

    def __init__(self, cell_size: float = 32.0):
        self.cell_size = cell_size
        self._cells: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}

    def _keys_for(self, rect: tuple[float, float, float, float]) -> list[tuple[int, int]]:
        x1, y1, x2, y2 = rect
        ix1 = int(math.floor(x1 / self.cell_size))
        iy1 = int(math.floor(y1 / self.cell_size))
        ix2 = int(math.floor(x2 / self.cell_size))
        iy2 = int(math.floor(y2 / self.cell_size))
        keys = []
        for ix in range(ix1, ix2 + 1):
            for iy in range(iy1, iy2 + 1):
                keys.append((ix, iy))
        return keys

    def overlaps(self, rect: tuple[float, float, float, float]) -> bool:
        x1, y1, x2, y2 = rect
        for key in self._keys_for(rect):
            for rx1, ry1, rx2, ry2 in self._cells.get(key, []):
                if not (x2 < rx1 or x1 > rx2 or y2 < ry1 or y1 > ry2):
                    return True
        return False

    def add(self, rect: tuple[float, float, float, float]) -> None:
        for key in self._keys_for(rect):
            self._cells.setdefault(key, []).append(rect)


@dataclass
class PlacementResult:
    placements: list[PlacedLabel]
    dropped_reason_counts: dict[str, int]
    placed_labels: int
    pushed_labels: int
    dropped_labels: int
    critical_label_drops: int
    ui_warning_flags: list[str]


def _label_rect(
    x: float,
    y: float,
    *,
    style_tokens: StyleTokens,
    label_class: str,
    text: str,
    font_size: float,
) -> tuple[float, float, float, float]:
    typo = style_tokens.typography
    family, weight = resolve_label_font(label_class, typo.font_family_sans, typo.font_family_serif)
    halo_pt = typo.halo_width_pt if style_tokens.rules.label_halo_mode == "bbox_rect" else 0.0
    measured = measure_text(
        TextMeasureInput(
            font_family=family,
            weight=weight,
            size_pt=font_size,
            tracking=typo.tracking,
            text=text,
        ),
        halo_width_pt=halo_pt,
    )
    return label_bbox_from_baseline(x, y, measured)


def _in_bounds(rect: tuple[float, float, float, float], width: int, height: int, margin: int = 6) -> bool:
    x1, y1, x2, y2 = rect
    return x1 >= margin and y1 >= margin and x2 <= width - margin and y2 <= height - margin


def place_labels(
    candidates: list[LabelCandidate],
    style_tokens: StyleTokens,
    width: int,
    height: int,
    occupied_rects: list[tuple[float, float, float, float]] | None = None,
) -> PlacementResult:
    """Place labels with hard non-overlap guarantees and push-before-drop.

    High-priority/critical labels attempt a wider radial search before being dropped.
    """
    index = _RectIndex(cell_size=32.0)
    if occupied_rects:
        for rect in occupied_rects:
            index.add(rect)

    label_priority = {
        name.lower(): (len(style_tokens.rules.label_priority) - idx)
        for idx, name in enumerate(style_tokens.rules.label_priority)
    }

    def _priority_key(item: LabelCandidate) -> tuple[int, int, str]:
        mapped = label_priority.get(item.label_class.lower(), 0)
        return (mapped, item.priority, item.text.lower())

    ordered = sorted(candidates, key=_priority_key, reverse=True)

    dropped_reasons: dict[str, int] = {}
    placements: list[PlacedLabel] = []
    pushed = 0
    critical_drops = 0

    base_offsets = [
        (14.0, -4.0),
        (14.0, -16.0),
        (14.0, 10.0),
        (-16.0, -4.0),
        (-16.0, -16.0),
        (-16.0, 10.0),
        (-4.0, -16.0),
        (-4.0, 12.0),
    ]

    def _record_drop(reason: str, critical: bool) -> None:
        dropped_reasons[reason] = dropped_reasons.get(reason, 0) + 1
        nonlocal critical_drops
        if critical:
            critical_drops += 1

    for candidate in ordered:
        text = candidate.text.strip()
        if not text:
            _record_drop("empty_text", candidate.critical)
            continue

        font_size = max(6.0, candidate.font_size)

        placed = None
        for dx, dy in base_offsets:
            lx = candidate.anchor_x + dx
            ly = candidate.anchor_y + dy
            rect = _label_rect(
                lx,
                ly,
                style_tokens=style_tokens,
                label_class=candidate.label_class,
                text=text,
                font_size=font_size,
            )
            if not _in_bounds(rect, width, height):
                continue
            if index.overlaps(rect):
                continue
            placed = (lx, ly, rect, False)
            break

        should_push = candidate.critical or candidate.priority >= 90
        if placed is None and should_push:
            for radius in style_tokens.rules.push_radius_steps_px:
                for angle_deg in (0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330):
                    angle = math.radians(angle_deg)
                    lx = candidate.anchor_x + math.cos(angle) * radius
                    ly = candidate.anchor_y + math.sin(angle) * radius
                    rect = _label_rect(
                        lx,
                        ly,
                        style_tokens=style_tokens,
                        label_class=candidate.label_class,
                        text=text,
                        font_size=font_size,
                    )
                    if not _in_bounds(rect, width, height):
                        continue
                    if index.overlaps(rect):
                        continue
                    dist = math.hypot(lx - candidate.anchor_x, ly - candidate.anchor_y)
                    if dist > style_tokens.rules.max_leader_line_px:
                        continue
                    placed = (lx, ly, rect, True)
                    break
                if placed is not None:
                    break

        if placed is None:
            _record_drop("collision", candidate.critical)
            continue

        lx, ly, rect, was_pushed = placed
        index.add(rect)

        leader_to_x = None
        leader_to_y = None
        if was_pushed:
            pushed += 1
            leader_to_x = candidate.anchor_x
            leader_to_y = candidate.anchor_y

        placements.append(
            PlacedLabel(
                text=text,
                label_class=candidate.label_class,
                anchor_x=candidate.anchor_x,
                anchor_y=candidate.anchor_y,
                x=lx,
                y=ly,
                priority=candidate.priority,
                pushed=was_pushed,
                leader_to_x=leader_to_x,
                leader_to_y=leader_to_y,
            )
        )

    ui_flags: list[str] = []
    if critical_drops > 0:
        ui_flags.append("critical_label_drop")
    if dropped_reasons.get("collision", 0) > 0:
        ui_flags.append("label_density_high")

    dropped_total = sum(dropped_reasons.values())
    return PlacementResult(
        placements=placements,
        dropped_reason_counts=dropped_reasons,
        placed_labels=len(placements),
        pushed_labels=pushed,
        dropped_labels=dropped_total,
        critical_label_drops=critical_drops,
        ui_warning_flags=ui_flags,
    )
