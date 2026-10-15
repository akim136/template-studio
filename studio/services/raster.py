"""Lines and checkbox squares found in the pixels of a rendered page.

For a page that is a picture (a scan), which has no text layer or drawn paths to read them from.
Plain Python over the grey rows PDFium renders: `bytes.translate` marks the ink, and a regular
expression finds the ink runs in each row. Runs in adjacent rows that touch are joined, so a line
scanned with a slight skew is read as one staircase; a page skewed by more than about 2 degrees
is not deskewed, and its lines are not found.

Rectangles are (left, top, right, bottom) in points with a top-left origin, measured over the
rendered area. The callers' shape thresholds are passed in, so a line or box found here is held to
the same sizes as a drawn one.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

LINE_MAX_THICKNESS = 3.0  # points; a thicker band of ink is a bar, not a line
EDGE_MAX_THICKNESS = 2.0  # points, for a checkbox's sides
GAP_MAX = 1.0  # points: a break this short in a scanned line is bridged
SEGMENT_MIN = 2.5  # points: shorter runs (most glyph parts, every side or stroke) are not read
SEGMENT_INK = 0.8  # share of a bridged run that must be ink
LINE_COVERAGE = 0.9  # share of a line's span that must be inked
SLOPE_MAX = 0.035  # about 2 degrees
EDGE_MATCH = 1.5  # points between the ends of a box's top and bottom edges
SIDE_COVERAGE = 0.8  # share of the rows between those edges that each side must ink
INTERIOR_INK_MAX = 0.6  # a filled square is a bullet, not a box; a ticked box stays a box
SEGMENT_LIMIT = 200_000  # ink runs read per page at most; past that nothing is reported
SAMPLE_STEP = 4  # every 4th row, and every 2nd pixel in it, make the grey-level histogram
INK_MIN, INK_MAX = 96, 200  # the ink threshold is kept in this range of grey levels

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Shapes:
    lines: list[Rect] = field(default_factory=list)
    boxes: list[Rect] = field(default_factory=list)
    note: str = ""  # why the scan stopped, if it did


@dataclass(frozen=True)
class _Band:
    """One joined group of ink runs, in pixels: its span, rows, thickness and slope."""

    left: int
    right: int  # exclusive
    top: int
    bottom: int  # exclusive
    centre: float  # mean row of its ink, weighted by run length
    thickness: int  # median count of inked rows per column
    coverage: float  # share of its columns with any ink
    slope: float


def ink_threshold(rows: list[bytes]) -> int:
    """Otsu's threshold on a sample of grey levels: a pixel darker than it is ink."""
    counts: Counter[int] = Counter()
    for row in rows[::SAMPLE_STEP]:
        counts.update(row[::2])
    total = sum(counts.values())
    level_sum = sum(level * n for level, n in counts.items())
    best, threshold = -1.0, 128
    weight = below = 0
    for level in range(256):
        n = counts.get(level, 0)
        weight += n
        if not weight:
            continue
        if weight == total:
            break
        below += level * n
        dark, light = below / weight, (level_sum - below) / (total - weight)
        spread = weight * (total - weight) * (dark - light) ** 2
        if spread > best:
            best, threshold = spread, level + 1
    return min(max(threshold, INK_MIN), INK_MAX)


def _segments(ink: list[bytes], scale: float) -> tuple[list[tuple[int, int, int]], bool]:
    """(row, start, end) of each horizontal ink run long enough to read, with short breaks
    bridged; the flag is set if SEGMENT_LIMIT was passed."""
    gap = max(1, round(GAP_MAX * scale))
    least = max(2, round(SEGMENT_MIN * scale))
    bridged = re.compile(rb"\x01+(?:\x00{1,%d}\x01+)*" % gap)
    solid = re.compile(rb"\x01+")
    out = []
    for y, row in enumerate(ink):
        for match in bridged.finditer(row):
            start, end = match.span()
            if end - start < least:
                continue
            if match.group().count(1) >= SEGMENT_INK * (end - start):
                out.append((y, start, end))
            else:  # too sparse to bridge: keep its solid runs that are long enough
                out += [(y, start + s, start + e) for s, e in _spans(solid, match.group(), least)]
            if len(out) > SEGMENT_LIMIT:
                return [], True
    return out, False


def _spans(pattern: re.Pattern[bytes], data: bytes, least: int) -> list[tuple[int, int]]:
    return [m.span() for m in pattern.finditer(data) if m.end() - m.start() >= least]


def _bands(segments: list[tuple[int, int, int]], gap: int) -> list[_Band]:
    """Join runs in adjacent rows that overlap or touch, then measure each group."""
    parent = list(range(len(segments)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_row: dict[int, list[int]] = {}
    for i, (y, _, _) in enumerate(segments):
        by_row.setdefault(y, []).append(i)  # each row's runs arrive in order of start
    for y, here in by_row.items():
        above = by_row.get(y - 1)
        if not above:
            continue
        first = 0
        for i in here:
            _, start, end = segments[i]
            while first < len(above) and segments[above[first]][2] + gap < start:
                first += 1
            j = first
            while j < len(above) and segments[above[j]][1] <= end + gap:
                parent[root(i)] = root(above[j])
                j += 1
    groups: dict[int, list[tuple[int, int, int]]] = {}
    for i, segment in enumerate(segments):
        groups.setdefault(root(i), []).append(segment)
    return [_measure(group) for group in groups.values()]


def _measure(group: list[tuple[int, int, int]]) -> _Band:
    left = min(start for _, start, _ in group)
    right = max(end for _, _, end in group)
    steps = [0] * (right - left + 1)
    weight = sum_x = sum_y = sum_xx = sum_xy = 0.0
    for y, start, end in group:
        steps[start - left] += 1
        steps[end - left] -= 1
        n, mid, row = end - start, (start + end) / 2, y + 0.5
        weight += n
        sum_x += n * mid
        sum_y += n * row
        sum_xx += n * (mid * mid + n * n / 12)  # a run's own spread along x
        sum_xy += n * mid * row
    depth, inked = 0, []
    for step in steps[:-1]:
        depth += step
        if depth:
            inked.append(depth)
    inked.sort()
    mean_x, mean_y = sum_x / weight, sum_y / weight
    spread = sum_xx / weight - mean_x * mean_x
    slope = (sum_xy / weight - mean_x * mean_y) / spread if spread > 1 else 0.0
    return _Band(
        left=left,
        right=right,
        top=min(y for y, _, _ in group),
        bottom=max(y for y, _, _ in group) + 1,
        centre=mean_y,
        thickness=inked[len(inked) // 2],
        coverage=len(inked) / (right - left),
        slope=slope,
    )


def _boxed(ink: list[bytes], left: int, right: int, top: _Band, bottom: _Band, side: int) -> bool:
    """Whether both sides are inked between the two edges, and the inside is not filled."""
    rows = ink[top.bottom : bottom.top]
    if not rows or right - left <= 2 * side:
        return False
    least = SIDE_COVERAGE * len(rows)
    if sum(1 for row in rows if 1 in row[left : left + side]) < least:
        return False
    if sum(1 for row in rows if 1 in row[right - side : right]) < least:
        return False
    inside = [row[left + side : right - side] for row in rows]
    return sum(part.count(1) for part in inside) < INTERIOR_INK_MAX * sum(map(len, inside))


def find(
    rows: list[bytes],
    width: int,
    height: int,
    scale: float,
    *,
    line_min: float,
    box_min: float,
    box_max: float,
    box_skew: float,
) -> Shapes:
    """Lines at least line_min points long, and squares box_min to box_max points on a side whose
    width and height differ by at most box_skew, in one page of grey rows rendered at scale
    pixels per point."""
    if len(rows) != height or any(len(row) != width for row in rows):
        raise ValueError("the rows do not match the page size")
    threshold = ink_threshold(rows)
    marks = bytes(1 if level < threshold else 0 for level in range(256))
    ink = [row.translate(marks) for row in rows]
    segments, stopped = _segments(ink, scale)
    if stopped:
        return Shapes(
            note=f"image scan stopped after {SEGMENT_LIMIT} ink runs; nothing is reported"
        )
    bands = _bands(segments, max(1, round(GAP_MAX * scale)))

    def rect(left: float, top: float, right: float, bottom: float) -> Rect:
        return (
            round(left / scale, 2),
            round(top / scale, 2),
            round(right / scale, 2),
            round(bottom / scale, 2),
        )

    lines = []
    for band in bands:
        if (
            band.right - band.left >= line_min * scale
            and band.thickness <= LINE_MAX_THICKNESS * scale
            and band.coverage >= LINE_COVERAGE
            and abs(band.slope) <= SLOPE_MAX
        ):
            half = band.thickness / 2
            lines.append(rect(band.left, band.centre - half, band.right, band.centre + half))

    match, side = EDGE_MATCH * scale, max(2, round(EDGE_MAX_THICKNESS * scale))
    edges = [
        band
        for band in bands
        if (box_min - EDGE_MATCH) * scale
        <= band.right - band.left
        <= (box_max + EDGE_MATCH) * scale
        and band.thickness <= side
        and band.coverage >= LINE_COVERAGE
    ]
    edges.sort(key=lambda band: (band.top, band.left))
    boxes = []
    for i, top in enumerate(edges):
        for bottom in edges[i + 1 :]:
            if bottom.top - top.top > (box_max + EDGE_MATCH) * scale:
                break
            if abs(bottom.left - top.left) > match or abs(bottom.right - top.right) > match:
                continue
            left, right = min(top.left, bottom.left), max(top.right, bottom.right)
            tall, wide = bottom.bottom - top.top, right - left
            if tall < (box_min - EDGE_MATCH) * scale or abs(wide - tall) > box_skew * scale:
                continue
            if _boxed(ink, left, right, top, bottom, side):
                boxes.append(rect(left, top.top, right, bottom.bottom))
                break
    return Shapes(
        sorted(lines, key=lambda r: (r[1], r[0])), sorted(boxes, key=lambda r: (r[1], r[0]))
    )
