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
