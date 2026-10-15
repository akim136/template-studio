"""Lines and checkbox squares found in the pixels of a rendered page.

For a page that is a picture (a scan), which has no text layer or drawn paths to read them from.
Plain Python over the grey rows PDFium renders: `bytes.translate` marks the ink, and a regular
expression finds the ink runs in each row. Runs in adjacent rows that touch are joined, so a line
scanned with a slight skew is read as one staircase; a page skewed by more than about 2 degrees
is not deskewed, and its lines are not found.

A checkbox is a top and a bottom edge joined by two straight sides. Letters such as B, D, H, O
and 8 can read the same way, so a box must also be clear of other ink just outside it, ink all
four corners, and have no solid bar of ink across its inside.

Rectangles are (left, top, right, bottom) in points with a top-left origin, measured over the
rendered area. The callers' shape thresholds are passed in, so a line or box found here is held to
the same sizes as a drawn one.
"""

from __future__ import annotations

import bisect
import math
import re
from collections import Counter
from dataclasses import dataclass, field

LINE_MAX_THICKNESS = 3.0  # points; a thicker band of ink is a bar, not a line
EDGE_MAX_THICKNESS = 2.0  # points, for a checkbox's sides
GAP_MAX = 0.5  # points: a break this short in a scanned line is bridged (a wider one could
# close up the inside of a small box with heavy sides); none is when that is under one pixel
SEGMENT_MIN = 2.5  # points: shorter runs (most glyph parts, every side or stroke) are not read
SEGMENT_INK = 0.8  # share of a bridged run that must be ink
LINE_COVERAGE = 0.9  # share of a line's span that must be inked
SLOPE_MAX = 0.035  # about 2 degrees
EDGE_MATCH = 1.5  # points between the ends of a box's top and bottom edges, and how much
# shorter than the box an edge may read
SIDE_COVERAGE = 0.8  # share of the rows between those edges that each side must ink
SIDE_WINDOW = 1.0  # points: each side inks those rows within one column this wide, as a
# straight stroke does even scanned with a slight skew; a letter's curved side does not
INTERIOR_INK_MAX = 0.6  # a filled square is a bullet, not a box; a ticked box stays a box
BAR_SPAN = 0.6  # a row inked solid across this share of a box's width, sides included, is a
# letter's bar (as in H, B or 8); a tick, a cross or a dot leaves a gap
RING = 1.0  # points: the band just outside a box, one pixel off it, that must be nearly clear
RING_INK = 0.2  # share of each side of that band that may be ink; a letter's neighbours ink more
CORNER = 0.5  # points: where each side's stroke meets the top and bottom, there is ink this close
# to the corner, as in a square and not in a rounded letter
SEGMENT_LIMIT = 400_000  # runs of ink read per page at most; past that nothing is reported.
# The busiest scanned page in the October 15, 2026 evaluation read about 118,000.
PAIR_LIMIT = 100_000  # pairs of edges tried as a box's top and bottom; past that, likewise
SAMPLE_STEP = 8  # every 8th pixel of every row makes the grey-level histogram, so no run of
# 8 pixels is missed
INK_MIN, INK_MAX = 96, 200  # the ink threshold is kept in this range of grey levels

Rect = tuple[float, float, float, float]
Segment = tuple[int, int, int, tuple[tuple[int, int], ...]]  # row, start, end, inked spans


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
    for row in rows:
        counts.update(row[::SAMPLE_STEP])
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


def _segments(ink: list[bytes], scale: float) -> tuple[list[Segment], bool]:
    """Each horizontal ink run long enough to read, with short breaks bridged, and the inked
    spans inside it; the flag is set once more than SEGMENT_LIMIT runs of ink were read: every
    run found, short or not, and each solid run inside a long one."""
    gap = round(GAP_MAX * scale)  # pixels
    least = max(2, math.ceil(SEGMENT_MIN * scale))
    solid = re.compile(rb"\x01+")
    bridged = re.compile(rb"\x01+(?:\x00{1,%d}\x01+)*" % gap) if gap else solid
    out, read = [], 0
    for y, row in enumerate(ink):
        for match in bridged.finditer(row):
            read += 1
            if read > SEGMENT_LIMIT:
                return [], True
            start, end = match.span()
            if end - start < least:
                continue
            inked = []
            for run in solid.finditer(row, start, end):
                read += 1
                if read > SEGMENT_LIMIT:
                    return [], True
                inked.append(run.span())
            if sum(e - s for s, e in inked) >= SEGMENT_INK * (end - start):
                out.append((y, start, end, tuple(inked)))
            else:  # too sparse to bridge: keep its solid runs that are long enough
                out += [(y, s, e, ((s, e),)) for s, e in inked if e - s >= least]
    return out, False


def _bands(segments: list[Segment], gap: int) -> list[_Band]:
    """Join runs in adjacent rows that overlap or touch, then measure each group."""
    parent = list(range(len(segments)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_row: dict[int, list[int]] = {}
    for i, (y, *_) in enumerate(segments):
        by_row.setdefault(y, []).append(i)  # each row's runs arrive in order of start
    for y, here in by_row.items():
        above = by_row.get(y - 1)
        if not above:
            continue
        first = 0
        for i in here:
            _, start, end, _ = segments[i]
            while first < len(above) and segments[above[first]][2] + gap < start:
                first += 1
            j = first
            while j < len(above) and segments[above[j]][1] <= end + gap:
                parent[root(i)] = root(above[j])
                j += 1
    groups: dict[int, list[Segment]] = {}
    for i, segment in enumerate(segments):
        groups.setdefault(root(i), []).append(segment)
    return [_measure(group) for group in groups.values()]


def _measure(group: list[Segment]) -> _Band:
    """Measure a band from its inked spans; the bridged breaks only joined it."""
    left = min(start for _, start, _, _ in group)
    right = max(end for _, _, end, _ in group)
    steps = [0] * (right - left + 1)
    weight = sum_x = sum_y = sum_xx = sum_xy = 0.0
    for y, _, _, inked in group:
        row = y + 0.5
        for start, end in inked:
            steps[start - left] += 1
            steps[end - left] -= 1
            n, mid = end - start, (start + end) / 2
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
        top=min(y for y, *_ in group),
        bottom=max(y for y, *_ in group) + 1,
        centre=mean_y,
        thickness=inked[len(inked) // 2],
        coverage=len(inked) / (right - left),
        slope=slope,
    )


def _straight(rows: list[bytes], start: int, end: int, window: int) -> float:
    """The largest share of rows that ink one window of columns between start and end: a straight
    side inks the same columns in every row, a curve moves across them."""
    best = 0
    for x in range(start, max(start, end - window) + 1):
        best = max(best, sum(1 for row in rows if 1 in row[x : x + window]))
    return best / len(rows)


def _outer(rows: list[bytes], columns: range) -> int:
    """The first of these columns that at least half the rows ink: where a side's stroke starts,
    seen from outside the box. A speck of noise joined onto an edge's end does not move it."""
    least = len(rows) / 2
    return next((x for x in columns if sum(row[x] for row in rows) >= least), columns[0])


def _clear(band: list[bytes]) -> bool:
    """Whether at most RING_INK of a band of pixels is ink; an empty band is clear."""
    total = sum(map(len, band))
    return not total or sum(part.count(1) for part in band) <= RING_INK * total


def _boxed(
    ink: list[bytes], left: int, right: int, top: _Band, bottom: _Band, sizes: tuple[int, ...]
) -> bool:
    """Whether both sides are straight and inked between the two edges, the inside is neither
    filled nor crossed by a solid bar, all four corners are inked, and the ink around the box is
    sparse. sizes are the side, side window, ring and corner sizes in pixels."""
    side, window, ring, corner = sizes
    rows = ink[top.bottom : bottom.top]
    if not rows or right - left <= 2 * side:
        return False
    if _straight(rows, left, left + side, window) < SIDE_COVERAGE:
        return False
    if _straight(rows, right - side, right, window) < SIDE_COVERAGE:
        return False
    inside = [row[left + side : right - side] for row in rows]
    if sum(part.count(1) for part in inside) >= INTERIOR_INK_MAX * sum(map(len, inside)):
        return False
    for row in rows:
        part = row[left:right]
        first, last = part.find(1), part.rfind(1)
        if (
            first >= 0
            and last - first + 1 >= BAR_SPAN * len(part)
            and 0 not in part[first : last + 1]
        ):
            return False
    upper, lower = top.top, bottom.bottom
    near = _outer(rows, range(left, left + side))
    far = _outer(rows, range(right - 1, right - side - 1, -1)) + 1
    for ys in (range(upper, upper + corner), range(lower - corner, lower)):
        for start in (near, far - corner):
            if not any(1 in ink[y][start : start + corner] for y in ys):
                return False
    # The ring starts a pixel off the box, so a slightly blurred edge is not counted as its ink.
    return (
        _clear([row[left:right] for row in ink[max(0, upper - 1 - ring) : max(0, upper - 1)]])
        and _clear([row[left:right] for row in ink[lower + 1 : lower + 1 + ring]])
        and _clear([row[max(0, left - 1 - ring) : max(0, left - 1)] for row in rows])
        and _clear([row[right + 1 : right + 1 + ring] for row in rows])
    )


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
    sizes = (side, *(max(2, round(points * scale)) for points in (SIDE_WINDOW, RING, CORNER)))
    least, most = box_min * scale, box_max * scale  # a box's outer size, as a drawn box's bounds
    edges = [
        band
        for band in bands
        if least - match <= band.right - band.left <= most
        and band.thickness <= side
        and band.coverage >= LINE_COVERAGE
    ]
    edges.sort(key=lambda band: (band.top, band.left))
    by_left: dict[int, list[_Band]] = {}  # each list in order of top
    for band in edges:
        by_left.setdefault(band.left, []).append(band)
    tops = {left: [band.top for band in column] for left, column in by_left.items()}
    boxes, tried = [], 0
    for top in edges:
        below = []  # edges starting under this one, within a box's height and EDGE_MATCH of it
        for left in range(math.ceil(top.left - match), math.floor(top.left + match) + 1):
            column = by_left.get(left, [])
            for j in range(bisect.bisect_right(tops.get(left, []), top.top), len(column)):
                if column[j].top - top.top > most:
                    break
                below.append(column[j])
        tried += len(below)
        if tried > PAIR_LIMIT:
            return Shapes(
                note=f"image scan stopped after {PAIR_LIMIT} box checks; nothing is reported"
            )
        for bottom in sorted(below, key=lambda band: (band.top, band.left)):
            if abs(bottom.right - top.right) > match:
                continue
            left, right = min(top.left, bottom.left), max(top.right, bottom.right)
            tall, wide = bottom.bottom - top.top, right - left
            if not (least <= tall <= most and least <= wide <= most):
                continue
            if abs(wide - tall) > box_skew * scale:
                continue
            if _boxed(ink, left, right, top, bottom, sizes):
                boxes.append(rect(left, top.top, right, bottom.bottom))
                break
    return Shapes(
        sorted(lines, key=lambda r: (r[1], r[0])), sorted(boxes, key=lambda r: (r[1], r[0]))
    )
