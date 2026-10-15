import math
import random

import pytest

from studio.services import raster

PAPER, INK = 255, 0
LIMITS = {"line_min": 36.0, "box_min": 5.0, "box_max": 18.0, "box_skew": 3.0}


class Canvas:
    """A grey page drawn in points, as PDFium would render it at scale pixels per point."""

    def __init__(self, scale=3.0, size=(300.0, 200.0), paper=PAPER):
        self.scale = scale
        self.width, self.height = round(size[0] * scale), round(size[1] * scale)
        self.rows = [bytearray([paper]) * self.width for _ in range(self.height)]

    def fill(self, left, top, right, bottom, level=INK):
        x0, x1 = round(left * self.scale), round(right * self.scale)
        for y in range(round(top * self.scale), round(bottom * self.scale)):
            self.rows[y][x0:x1] = bytes([level]) * (x1 - x0)

    def frame(self, left, top, size, stroke=1.0):
        right, bottom = left + size, top + size
        self.fill(left, top, right, top + stroke)
        self.fill(left, bottom - stroke, right, bottom)
        self.fill(left, top, left + stroke, bottom)
        self.fill(right - stroke, top, right, bottom)

    def slanted(self, left, top, length, degrees, stroke=1.0):
        rise = math.tan(math.radians(degrees))
        for x in range(round(left * self.scale), round((left + length) * self.scale)):
            y0 = round(top * self.scale + (x - left * self.scale) * rise)
            for y in range(y0, y0 + round(stroke * self.scale)):
                self.rows[y][x] = INK

    def find(self):
        rows = [bytes(row) for row in self.rows]
        return raster.find(rows, self.width, self.height, self.scale, **LIMITS)


@pytest.mark.parametrize("scale", [2.0, 3.0])
def test_a_scanned_line_is_found_where_it_is_drawn(scale):
    page = Canvas(scale)
    page.fill(40, 120, 240, 121)
    found = page.find()
    assert found.boxes == []
    [(left, top, right, bottom)] = found.lines
    assert (left, right) == (pytest.approx(40, abs=0.5), pytest.approx(240, abs=0.5))
    assert (top, bottom) == (pytest.approx(120, abs=0.5), pytest.approx(121, abs=0.5))


def test_short_lines_and_thick_bars_are_not_lines():
    page = Canvas()
    page.fill(20, 20, 50, 21)  # 30 points: shorter than a drawn rule may be
    page.fill(20, 60, 220, 68)  # an 8-point bar
    assert page.find().lines == []


def test_a_line_broken_by_scanning_gaps_is_one_line():
    page = Canvas()
    for start in range(40, 240, 20):
        page.fill(start, 100, start + 19.5, 101)  # half-point breaks every 20 points
    [(left, _, right, _)] = page.find().lines
    assert (left, right) == (pytest.approx(40, abs=0.5), pytest.approx(239.5, abs=0.5))


def test_a_line_scanned_with_a_slight_skew_is_found():
    page = Canvas()
    page.slanted(40, 100, 200, 1.5)
    [(left, top, right, bottom)] = page.find().lines
    assert (left, right) == (pytest.approx(40, abs=0.5), pytest.approx(240, abs=0.5))
    middle = 100 + 100 * math.tan(math.radians(1.5)) + 0.5  # the stroke's centre halfway along
    assert (top + bottom) / 2 == pytest.approx(middle, abs=0.5)


def test_a_page_skewed_past_two_degrees_gives_no_lines():
    page = Canvas()
    page.slanted(40, 60, 200, 3.0)
    assert page.find().lines == []


def test_a_checkbox_is_found_with_its_outer_edges():
    page = Canvas()
    page.frame(100, 50, 10)
    found = page.find()
    assert found.lines == []
    [box] = found.boxes
    assert box == tuple(pytest.approx(v, abs=0.5) for v in (100, 50, 110, 60))


def test_a_ticked_checkbox_is_still_a_checkbox():
    page = Canvas()
    page.frame(100, 50, 12)
    for step in range(20):  # an X drawn inside
        page.fill(102 + step * 0.4, 52 + step * 0.4, 103 + step * 0.4, 53 + step * 0.4)
        page.fill(110 - step * 0.4, 52 + step * 0.4, 111 - step * 0.4, 53 + step * 0.4)
    assert len(page.find().boxes) == 1


def test_filled_squares_and_letter_shapes_are_not_checkboxes():
    page = Canvas()
    page.fill(20, 20, 30, 30)  # a bullet
    page.frame(20, 50, 12)  # a framed square hatched inside
    for left in range(21, 31):
        page.fill(left, 51, left + 0.67, 61)
    page.fill(60, 20, 67, 21)  # an "E": three bars and a spine
    page.fill(60, 24, 66, 25)
    page.fill(60, 28, 67, 29)
    page.fill(60, 20, 61, 29)
    page.fill(100, 20, 108, 21)  # a "D"-like shape: no straight right side
    page.fill(100, 30, 108, 31)
    page.fill(100, 20, 101, 31)
    page.fill(109, 23, 110, 28)
    assert page.find().boxes == []


def test_a_table_gives_its_rows_as_lines_and_no_boxes():
    page = Canvas()
    for top in (40, 60, 80, 100):
        page.fill(20, top, 260, top + 1)
    for left in (20, 100, 180, 259):
        page.fill(left, 40, left + 1, 101)
    found = page.find()
    assert [round(top) for _, top, _, _ in found.lines] == [40, 60, 80, 100]
    assert all(round(right - left) == 240 for left, _, right, _ in found.lines)
    assert found.boxes == []


def test_a_grey_noisy_scan_still_gives_its_line_and_box():
    page = Canvas(paper=215)
    noise = random.Random(7)
    for _ in range(4000):  # isolated dark specks
        page.rows[noise.randrange(page.height)][noise.randrange(page.width)] = 90
    page.fill(40, 120, 240, 121, level=60)
    page.frame(100, 50, 10)
    found = page.find()
    assert len(found.lines) == 1 and len(found.boxes) == 1


def test_a_blank_page_gives_nothing_and_the_result_is_repeatable():
    assert raster.find([b"\xff" * 30] * 20, 30, 20, 3.0, **LIMITS) == raster.Shapes()
    page = Canvas()
    page.fill(40, 150, 240, 151)
    page.fill(40, 30, 240, 31)
    page.frame(250, 100, 10)
    page.frame(10, 100, 10)
    first = page.find()
    assert first == page.find()
    assert [top for _, top, _, _ in first.lines] == sorted(top for _, top, _, _ in first.lines)
    assert [left for left, _, _, _ in first.boxes] == [
        pytest.approx(10, abs=0.5),
        pytest.approx(250, abs=0.5),
    ]


def test_a_page_with_too_many_ink_runs_reports_nothing_and_says_why(monkeypatch):
    monkeypatch.setattr(raster, "SEGMENT_LIMIT", 3)
    page = Canvas()
    for top in range(20, 120, 10):
        page.fill(20, top, 260, top + 1)
    found = page.find()
    assert (found.lines, found.boxes) == ([], [])
    assert found.note == "image scan stopped after 3 ink runs; nothing is reported"


def test_rows_that_do_not_match_the_page_size_are_refused():
    with pytest.raises(ValueError):
        raster.find([b"\xff" * 30] * 19, 30, 20, 3.0, **LIMITS)


def test_a_dashed_line_with_too_little_ink_is_not_a_line():
    page = Canvas()
    for dash in range(10):  # 4-point dashes 1 point apart: 40 of 49 points inked
        page.fill(20 + 5 * dash, 100, 24 + 5 * dash, 101)
    assert page.find().lines == []


def test_checkboxes_are_held_to_the_size_limits():
    page = Canvas()
    page.frame(20, 20, 5)
    page.frame(60, 20, 18)
    page.frame(140, 20, 4.67)  # just under the smallest box
    page.frame(160, 20, 18.33)  # just over the largest
    page.fill(100, 50, 118, 52)  # an 18-by-21-point frame drawn with 2-point sides
    page.fill(100, 69, 118, 71)
    page.fill(100, 50, 102, 71)
    page.fill(116, 50, 118, 71)
    found = page.find()
    assert [round(right - left) for left, _, right, _ in found.boxes] == [5, 18]


def test_a_page_of_box_edges_stops_after_a_bounded_number_of_checks(monkeypatch):
    checks = []
    boxed = raster._boxed
    monkeypatch.setattr(raster, "PAIR_LIMIT", 500)
    monkeypatch.setattr(raster, "_boxed", lambda *args: checks.append(1) or boxed(*args))
    page = Canvas(size=(200.0, 200.0))
    for top in range(0, 200, 2):  # rows of 4-point dashes: thousands of possible box edges
        for left in range(0, 196, 6):
            page.fill(left, top + 0.34, left + 4, top + 0.67)
    found = page.find()
    assert (found.lines, found.boxes) == ([], [])
    assert found.note == "image scan stopped after 500 box checks; nothing is reported"
    assert len(checks) <= 500


@pytest.mark.parametrize("scale", [1.0, 2.0, 3.0])
def test_a_small_box_with_heavy_sides_is_not_closed_up_by_gap_bridging(scale):
    page = Canvas(scale)
    page.frame(20, 20, 5, stroke=2)  # one point of paper inside
    assert page.find().boxes == [tuple(pytest.approx(v, abs=0.5) for v in (20, 20, 25, 25))]


def test_every_run_of_ink_counts_toward_the_limit_even_inside_a_bridged_run(monkeypatch):
    monkeypatch.setattr(raster, "SEGMENT_LIMIT", 50)
    dotted = b"\x00\x00\x00\x00\xff" * 40  # 40 runs of ink that bridge into one
    rows = [dotted] * 3 + [b"\xff" * 200] * 7
    found = raster.find(rows, 200, 10, 3.0, **LIMITS)
    assert found.note == "image scan stopped after 50 ink runs; nothing is reported"


def test_short_runs_of_ink_count_toward_the_limit_too(monkeypatch):
    monkeypatch.setattr(raster, "SEGMENT_LIMIT", 50)
    specks = b"\x00\xff\xff\xff\xff\xff" * 60  # 60 one-pixel specks, too far apart to bridge
    rows = [specks] + [b"\xff" * 360] * 9
    found = raster.find(rows, 360, 10, 3.0, **LIMITS)
    assert found.note == "image scan stopped after 50 ink runs; nothing is reported"


@pytest.mark.parametrize("top", [119, 119.34, 119.67, 120])
def test_a_faint_rule_is_found_whichever_rows_it_falls_on(top):
    page = Canvas()
    page.fill(40, top, 240, top + 1, level=150)
    assert len(page.find().lines) == 1


def test_heavy_box_sides_are_not_read_as_runs_at_one_pixel_per_point():
    page = Canvas(scale=1.0)
    page.frame(20, 20, 10, stroke=2)
    assert page.find().boxes == [(20, 20, 30, 30)]
