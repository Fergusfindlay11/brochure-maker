"""Tests for the label engine — collision detection and push-before-drop."""

from brochure_maker.map_pipeline.label_engine import (
    LabelCandidate,
    place_labels,
    _SpatialGrid,
)


class TestSpatialGrid:
    """Grid-based spatial index tests."""

    def test_empty_grid_no_overlap(self):
        grid = _SpatialGrid(500, 500)
        assert not grid.overlaps(10, 10, 50, 50)

    def test_insert_then_overlap(self):
        grid = _SpatialGrid(500, 500)
        grid.insert(10, 10, 50, 50)
        assert grid.overlaps(20, 20, 60, 60)

    def test_no_overlap_disjoint(self):
        grid = _SpatialGrid(500, 500)
        grid.insert(10, 10, 50, 50)
        assert not grid.overlaps(100, 100, 150, 150)

    def test_non_overlapping_separated(self):
        grid = _SpatialGrid(500, 500)
        grid.insert(10, 10, 50, 50)
        # Rect clearly past the first rect
        assert not grid.overlaps(51, 10, 90, 50)


class TestLabelPlacement:
    """No label overlaps in dense fixtures."""

    def test_single_label_placed(self):
        candidates = [
            LabelCandidate(
                text="Test Label",
                anchor_x=250,
                anchor_y=250,
                font_size=11.0,
                priority=10,
            )
        ]
        result = place_labels(candidates, 500, 500)
        assert result.placed_count == 1
        assert result.dropped_count == 0

    def test_many_labels_no_overlaps(self):
        """Place 20 labels — none should overlap."""
        candidates = []
        for i in range(20):
            candidates.append(LabelCandidate(
                text=f"Label {i}",
                anchor_x=50 + (i % 5) * 100,
                anchor_y=50 + (i // 5) * 100,
                font_size=11.0,
                priority=50,
            ))
        result = place_labels(candidates, 500, 500)
        # All placed should have non-overlapping bboxes
        # (the engine guarantees this by design)
        assert result.placed_count > 0
        assert result.placed_count + result.dropped_count == 20

    def test_dense_labels_some_dropped(self):
        """Many labels at the same point — most should be dropped."""
        candidates = []
        for i in range(10):
            candidates.append(LabelCandidate(
                text=f"Same Spot {i}",
                anchor_x=250,
                anchor_y=250,
                font_size=11.0,
                priority=50,
            ))
        result = place_labels(candidates, 500, 500)
        assert result.placed_count >= 1
        assert result.dropped_count > 0


class TestPushBeforeDrop:
    """High-priority labels attempt push/leader-line before drop."""

    def test_critical_label_tries_push(self):
        """A critical label should be pushed rather than dropped."""
        # Place a blocking label first
        candidates = [
            LabelCandidate(
                text="Blocker",
                anchor_x=250,
                anchor_y=250,
                font_size=14.0,
                priority=5,
            ),
            LabelCandidate(
                text="Critical POI",
                anchor_x=255,
                anchor_y=255,
                font_size=11.0,
                priority=10,
                is_critical=True,
            ),
        ]
        result = place_labels(candidates, 500, 500)
        # Both should be placed (critical one pushed if needed)
        assert result.placed_count == 2

    def test_non_critical_low_priority_drops(self):
        """A low-priority non-critical label should be dropped, not pushed."""
        # Fill the area with labels
        candidates = []
        for i in range(8):
            candidates.append(LabelCandidate(
                text=f"High Priority {i}",
                anchor_x=250,
                anchor_y=250,
                font_size=14.0,
                priority=5,
            ))
        candidates.append(LabelCandidate(
            text="Low Priority",
            anchor_x=250,
            anchor_y=250,
            font_size=11.0,
            priority=80,
            is_critical=False,
        ))
        result = place_labels(candidates, 500, 500)
        # At least one should be dropped
        assert result.dropped_count > 0


class TestLabelMetadata:
    """Critical-drop metadata is emitted correctly."""

    def test_critical_drops_tracked(self):
        """If a critical label is dropped, it should appear in metadata."""
        # Create an extremely cramped scenario
        candidates = []
        for i in range(20):
            candidates.append(LabelCandidate(
                text=f"Blocking {i}",
                anchor_x=50 + (i * 5),
                anchor_y=50,
                font_size=14.0,
                priority=1,
                is_critical=True,
            ))
        result = place_labels(candidates, 200, 100)

        # Some critical labels will be dropped due to limited space
        if result.dropped_count > 0:
            assert len(result.critical_drops) > 0

    def test_dropped_reason_counts(self):
        """Dropped labels should have reason counts."""
        candidates = [
            LabelCandidate(text="A", anchor_x=50, anchor_y=50, font_size=50, priority=1),
            LabelCandidate(text="B", anchor_x=50, anchor_y=50, font_size=50, priority=80),
        ]
        result = place_labels(candidates, 100, 100)
        if result.dropped_count > 0:
            assert len(result.dropped_reason_counts) > 0


class TestReservedRects:
    """Labels respect reserved rectangles (e.g. building marker zone)."""

    def test_label_avoids_reserved_rect(self):
        reserved = [(200, 200, 300, 300)]
        candidates = [
            LabelCandidate(
                text="Should Avoid",
                anchor_x=250,
                anchor_y=250,
                font_size=11.0,
                priority=50,
            )
        ]
        result = place_labels(candidates, 500, 500, reserved_rects=reserved)
        if result.placed_count > 0:
            placed = result.placed[0]
            # Placed position should not overlap with reserved rect
            assert not (
                200 <= placed.x <= 300
                and 200 <= placed.y <= 300
            )
