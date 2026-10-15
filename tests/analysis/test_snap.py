import dataclasses

import pytest

from studio.services.candidates import Candidate, extract_candidates
from studio.services.fields import resolve
from tests.analysis.test_fields import ROLES, raw
from tests.analysis.test_scanned_candidates import form, scanned

SCALE = 2.0  # image pixels per point
LETTER = (612.0, 792.0)
LINE = Candidate("p1-1", 1, "rule", 72.0, 140.0, 200.0, 1.0, "", "", "", from_image=True)
SQUARE = Candidate("p1-2", 1, "box", 300.0, 180.0, 10.0, 10.0, "", "", "", from_image=True)


def drawn(left, top, width, height, **overrides):
    """A field the model placed with its own box, given here in points."""
    box = {"left": left * SCALE, "top": top * SCALE, "width": width * SCALE}
    return raw(box={**box, "height": height * SCALE}, **overrides)


def placed(field, candidates):
    fields, rejected = resolve(1, [field], ROLES, candidates, SCALE, LETTER)
    assert rejected == []
    [out] = fields
    return (out.x, out.y, out.width, out.height, out.geometry_source, out.candidate_id)


def test_a_box_drawn_on_a_scanned_line_sits_on_the_line_within_its_own_span():
    field = drawn(60, 124, 150, 18)  # starts left of the line, ends inside it
    assert placed(field, [LINE]) == (72.0, 127.0, 138.0, 14.0, "snapped", None)
    signature = drawn(80, 120, 180, 20, type="signature")
    assert placed(signature, [LINE]) == (80.0, 117.0, 180.0, 24.0, "snapped", None)


def test_a_checkbox_drawn_near_a_scanned_square_takes_the_square():
    field = drawn(301, 182, 9, 9, type="checkbox")
    assert placed(field, [SQUARE]) == (300.0, 180.0, 10.0, 10.0, "snapped", None)


def test_a_checkbox_anchored_to_a_line_is_snapped_and_keeps_its_anchor():
    field = drawn(301, 182, 9, 9, type="checkbox", candidate_id="p1-1")
    assert placed(field, [LINE, SQUARE]) == (300.0, 180.0, 10.0, 10.0, "snapped", "p1-1")


@pytest.mark.parametrize(
    "field",
    [
        drawn(60, 115, 150, 18),  # its bottom 8 points above the line
        drawn(180, 124, 200, 18),  # more than half of it past the line's end
        drawn(250, 124, 100, 18),  # beside the line, not on it
    ],
)
def test_a_box_not_clearly_on_a_line_keeps_its_own_geometry(field):
    assert placed(field, [LINE])[4] == "model_box"


@pytest.mark.parametrize(
    "field",
    [
        drawn(304, 184, 9, 9, type="checkbox"),  # its centre 5 points from the square's
        drawn(302, 182, 4, 4, type="checkbox"),  # less than half the square's size
        drawn(295, 175, 21, 21, type="checkbox"),  # more than twice it
    ],
)
def test_a_checkbox_not_clearly_on_a_square_keeps_its_own_geometry(field):
    assert placed(field, [SQUARE])[4] == "model_box"


def test_two_lines_about_as_near_snap_to_neither():
    second = dataclasses.replace(LINE, id="p1-3", y=143.5)  # a double underline
    field = drawn(80, 124, 150, 18)
    assert placed(field, [LINE])[4] == "snapped"
    assert placed(field, [LINE, second])[4] == "model_box"
    farther = dataclasses.replace(second, y=146.0)
    assert placed(field, [LINE, farther])[:2] == (80.0, 127.0)


def test_shapes_read_from_a_digital_page_are_never_snapped_to():
    digital = [dataclasses.replace(LINE, from_image=False)]
    assert placed(drawn(60, 124, 150, 18), digital)[4] == "model_box"
    square = [dataclasses.replace(SQUARE, from_image=False)]
    assert placed(drawn(301, 182, 9, 9, type="checkbox"), square)[4] == "model_box"


def test_a_field_anchored_to_a_candidate_keeps_the_candidate_geometry():
    field = drawn(60, 124, 150, 18, candidate_id="p1-1")
    assert placed(field, [LINE]) == (72.0, 127.0, 200.0, 14.0, "candidate", "p1-1")


def test_a_field_drawn_on_a_scanned_page_snaps_to_what_its_image_shows():
    found = extract_candidates(scanned(form))[0].candidates
    line, square = found
    assert placed(drawn(70, 124, 160, 16), found)[:5] == (
        pytest.approx(line.x, abs=0.01),
        pytest.approx(line.y + line.height - 14, abs=0.01),
        pytest.approx(158, abs=0.5),
        14.0,
        "snapped",
    )
    box = placed(drawn(301, 181, 9, 9, type="checkbox"), found)
    assert box[:4] == (square.x, square.y, square.width, square.height)


def test_a_snap_that_would_leave_the_page_keeps_the_model_box():
    near_top = dataclasses.replace(LINE, y=9.0)  # no room above it for a 14-point field
    assert placed(drawn(72, 0, 150, 10), [near_top]) == (72.0, 0.0, 150.0, 10.0, "model_box", None)


@pytest.mark.parametrize(
    "box",
    [
        {"left": 1223.998, "top": 360.0, "width": 20.0, "height": 20.0},  # 0.001 points wide
        {"left": 600.0, "top": 1583.998, "width": 20.0, "height": 20.0},  # 0.001 points tall
    ],
)
def test_a_sliver_at_the_page_edge_is_not_snapped(box):
    edge = [dataclasses.replace(SQUARE, x=600.0, y=180.0), dataclasses.replace(SQUARE, y=781.0)]
    assert placed(raw(box=box, type="checkbox"), edge)[4] == "model_box"
