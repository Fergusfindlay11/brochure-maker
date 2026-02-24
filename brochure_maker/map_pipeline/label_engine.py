"""Label Engine — zero-overlap guarantee with push-before-drop.

Collision-safe label placement using a spatial index (R-tree style grid).
High-priority labels attempt push/leader-line before being dropped.

Phases:
1. Candidate placement pass — try preferred positions.
2. Push-before-drop pass — expand search radius, allow bounded leader lines.
3. Drop only after push budget exhausted.
4. Emit warning metadata when critical labels are dropped.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from .text_metrics import TextBBox, measure_text

logger = logging.getLogger(__name__)


@dataclass
class LabelCandidate:
    """A label to be placed on the map."""

    text: str
    anchor_x: float  # anchor point in pixel space
    anchor_y: float
    font_family: str = "Jost"
    font_weight: int = 400
    font_size: float = 11.0
    letter_spacing: float = 0.0
    halo_width: float = 3.0
    priority: int = 50  # lower = higher priority
    is_critical: bool = False
    category: str = ""  # "street", "poi", "station", "building", "park", "water"
    rotation: float = 0.0  # degrees
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlacedLabel:
    """A successfully placed label with its final position."""

    candidate: LabelCandidate
    x: float
    y: float
    bbox: TextBBox
    was_pushed: bool = False
    leader_line: tuple[float, float, float, float] | None = None  # (x1,y1,x2,y2)


@dataclass
class DroppedLabel:
    """A label that could not be placed."""

    candidate: LabelCandidate
    reason: str


@dataclass
class LabelPlacementResult:
    """Complete result of label placement."""

    placed: list[PlacedLabel] = field(default_factory=list)
    pushed: list[PlacedLabel] = field(default_factory=list)
    dropped: list[DroppedLabel] = field(default_factory=list)

    @property
    def placed_count(self) -> int:
        return len(self.placed)

    @property
    def pushed_count(self) -> int:
        return len(self.pushed)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    @property
    def dropped_reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in self.dropped:
            counts[d.reason] = counts.get(d.reason, 0) + 1
        return counts

    @property
    def critical_drops(self) -> list[str]:
        return [d.candidate.text for d in self.dropped if d.candidate.is_critical]


# ──────────────────────────────────────────────
#  Spatial Index (grid-based)
# ──────────────────────────────────────────────

class _SpatialGrid:
    """Grid-based spatial index for fast overlap detection.

    Divides the map into cells. Each occupied rect is stored in all cells
    it touches. Overlap checks only test rects in the same cells.
    """

    def __init__(self, width: float, height: float, cell_size: float = 40.0):
        self.width = width
        self.height = height
        self.cell_size = cell_size
        self.cols = max(1, int(math.ceil(width / cell_size)))
        self.rows = max(1, int(math.ceil(height / cell_size)))
        self.cells: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}

    def _cell_range(
        self, x1: float, y1: float, x2: float, y2: float,
    ) -> list[tuple[int, int]]:
        c1 = max(0, int(x1 / self.cell_size))
        r1 = max(0, int(y1 / self.cell_size))
        c2 = min(self.cols - 1, int(x2 / self.cell_size))
        r2 = min(self.rows - 1, int(y2 / self.cell_size))
        return [
            (r, c)
            for r in range(r1, r2 + 1)
            for c in range(c1, c2 + 1)
        ]

    def overlaps(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        """Check if a rect overlaps any existing rect."""
        for cell_key in self._cell_range(x1, y1, x2, y2):
            for rx1, ry1, rx2, ry2 in self.cells.get(cell_key, []):
                if not (x2 < rx1 or x1 > rx2 or y2 < ry1 or y1 > ry2):
                    return True
        return False

    def insert(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Insert a rect into the spatial index."""
        rect = (x1, y1, x2, y2)
        for cell_key in self._cell_range(x1, y1, x2, y2):
            if cell_key not in self.cells:
                self.cells[cell_key] = []
            self.cells[cell_key].append(rect)


# ──────────────────────────────────────────────
#  Placement Strategies
# ──────────────────────────────────────────────

# Candidate offsets for label placement relative to anchor point.
# (dx_factor, dy_factor) applied to bbox dimensions.
_PLACEMENT_OFFSETS = [
    (1.2, 0.0),    # East
    (1.2, -1.2),   # NE
    (1.2, 1.2),    # SE
    (-1.2, 0.0),   # West (label extends left)
    (-1.2, -1.2),  # NW
    (-1.2, 1.2),   # SW
    (0.0, -1.5),   # North
    (0.0, 1.5),    # South
]

# Push search radius rings (in pixels).
_PUSH_RADII = [20, 40, 60, 80]

# Maximum leader line length (pixels).
_MAX_LEADER_LENGTH = 60


def _try_place(
    candidate: LabelCandidate,
    bbox: TextBBox,
    grid: _SpatialGrid,
    map_width: float,
    map_height: float,
    icon_gap: float = 16.0,
) -> tuple[float, float] | None:
    """Try to place a label at standard offset positions.

    Returns (x, y) of the top-left corner of the label bbox, or None.
    """
    w = bbox.padded_width + icon_gap
    h = bbox.padded_height

    for dx_f, dy_f in _PLACEMENT_OFFSETS:
        lx = candidate.anchor_x + dx_f * icon_gap
        ly = candidate.anchor_y + dy_f * icon_gap

        # Adjust for west-side placements
        if dx_f < 0:
            lx -= w

        # Check bounds
        if lx < 0 or ly - h < 0 or lx + w > map_width or ly > map_height:
            continue

        # Check overlap
        if not grid.overlaps(lx, ly - h, lx + w, ly):
            return (lx, ly)

    return None


def _try_push(
    candidate: LabelCandidate,
    bbox: TextBBox,
    grid: _SpatialGrid,
    map_width: float,
    map_height: float,
    icon_gap: float = 16.0,
) -> tuple[float, float, tuple[float, float, float, float] | None] | None:
    """Push-before-drop: try expanding search radius with optional leader line.

    Returns (x, y, leader_line) or None.
    """
    w = bbox.padded_width + icon_gap
    h = bbox.padded_height

    for radius in _PUSH_RADII:
        # Try 8 directions at this radius
        for angle_deg in range(0, 360, 45):
            angle = math.radians(angle_deg)
            lx = candidate.anchor_x + radius * math.cos(angle) + icon_gap
            ly = candidate.anchor_y + radius * math.sin(angle)

            if lx < 0 or ly - h < 0 or lx + w > map_width or ly > map_height:
                continue

            if not grid.overlaps(lx, ly - h, lx + w, ly):
                # Compute leader line from anchor to label
                leader = None
                dist = math.sqrt(
                    (lx - candidate.anchor_x) ** 2
                    + (ly - candidate.anchor_y) ** 2
                )
                if dist > icon_gap * 1.5 and dist <= _MAX_LEADER_LENGTH:
                    leader = (
                        candidate.anchor_x,
                        candidate.anchor_y,
                        lx,
                        ly - h / 2,
                    )
                return (lx, ly, leader)

    return None


# ──────────────────────────────────────────────
#  Main Engine
# ──────────────────────────────────────────────

def place_labels(
    candidates: list[LabelCandidate],
    map_width: float,
    map_height: float,
    reserved_rects: list[tuple[float, float, float, float]] | None = None,
) -> LabelPlacementResult:
    """Place all label candidates with zero-overlap guarantee.

    1. Sort by priority (lower = placed first).
    2. For each candidate, try standard placement.
    3. If standard fails and candidate is high-priority, try push.
    4. If all options exhausted, drop and record reason.
    """
    grid = _SpatialGrid(map_width, map_height)

    # Insert reserved rects (e.g. building marker zone)
    if reserved_rects:
        for rect in reserved_rects:
            grid.insert(*rect)

    # Sort candidates: lower priority number first, then alphabetically for determinism
    sorted_candidates = sorted(
        candidates,
        key=lambda c: (c.priority, c.text),
    )

    result = LabelPlacementResult()

    for candidate in sorted_candidates:
        # Measure text
        bbox = measure_text(
            text=candidate.text,
            font_family=candidate.font_family,
            weight=candidate.font_weight,
            size_pt=candidate.font_size,
            letter_spacing=candidate.letter_spacing,
            halo_width=candidate.halo_width,
        )

        if bbox.width_px == 0:
            result.dropped.append(DroppedLabel(candidate, "empty_text"))
            continue

        # Phase 1: standard placement
        pos = _try_place(candidate, bbox, grid, map_width, map_height)
        if pos is not None:
            lx, ly = pos
            w = bbox.padded_width + 16
            h = bbox.padded_height
            grid.insert(lx, ly - h, lx + w, ly)
            result.placed.append(PlacedLabel(
                candidate=candidate,
                x=lx,
                y=ly,
                bbox=bbox,
            ))
            continue

        # Phase 2: push-before-drop for high-priority labels
        if candidate.priority <= 30 or candidate.is_critical:
            push_result = _try_push(candidate, bbox, grid, map_width, map_height)
            if push_result is not None:
                lx, ly, leader = push_result
                w = bbox.padded_width + 16
                h = bbox.padded_height
                grid.insert(lx, ly - h, lx + w, ly)
                placed = PlacedLabel(
                    candidate=candidate,
                    x=lx,
                    y=ly,
                    bbox=bbox,
                    was_pushed=True,
                    leader_line=leader,
                )
                result.placed.append(placed)
                result.pushed.append(placed)
                continue

        # Phase 3: drop
        reason = "collision_all_positions_exhausted"
        if candidate.is_critical:
            reason = "critical_collision_push_exhausted"
        result.dropped.append(DroppedLabel(candidate, reason))

    logger.info(
        "Labels: %d placed (%d pushed), %d dropped",
        result.placed_count, result.pushed_count, result.dropped_count,
    )
    if result.critical_drops:
        logger.warning("Critical labels dropped: %s", result.critical_drops)

    return result
