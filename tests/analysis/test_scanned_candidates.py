import math

import pytest
from pypdf.generic import RectangleObject

from studio.services import candidates as candidates_module
from studio.services.candidates import extract_candidates, page_candidates
from tests.analysis.fake_gateway import FakeGateway, page_of
from tests.analysis.pdf_factory import image_pdf, make_pdf
from tests.analysis.test_inference import SERIAL, edited, run
from tests.analysis.test_raster import Canvas

LETTER = (612.0, 792.0)
NAME_LINE = b"BT /F1 12 Tf 72 720 Td (Name: ____________) Tj ET"


def form(canvas):
    canvas.fill(72, 140, 272, 141)  # a signature line
    canvas.frame(300, 180, 10)  # a checkbox


def scanned(draw, **options):
    """A letter page that is one picture of what ``draw`` puts on a canvas, scanned at 2 px/pt."""
    canvas = Canvas(scale=2.0, size=LETTER)
    draw(canvas)
    pixels = b"".join(bytes(row) for row in canvas.rows)
    return image_pdf([(pixels, canvas.width, canvas.height)], **options)


def test_a_scanned_page_gets_the_lines_and_boxes_in_its_image():
    page = extract_candidates(scanned(form))[0]
    kinds = [(c.id, c.kind, c.from_image) for c in page.candidates]
    assert kinds == [("p1-1", "rule", True), ("p1-2", "box", True)]
    rule, box = page.candidates
    assert (rule.x, rule.width) == pytest.approx((72, 200), abs=0.5)
    assert rule.y + rule.height == pytest.approx(141, abs=0.5)
    assert (box.x, box.y, box.width, box.height) == pytest.approx((300, 180, 10, 10), abs=0.5)
    assert (rule.left_text, rule.right_text, rule.below_text) == ("", "", "")
    assert page.reason == "page image scanned: 1 lines, 1 boxes"


def test_a_picture_drawn_from_inside_a_form_is_scanned_too():
    page = extract_candidates(scanned(form, in_form=True))[0]
    assert [(c.kind, c.from_image) for c in page.candidates] == [("rule", True), ("box", True)]


def test_a_small_picture_does_not_make_a_page_a_scan():
    page = extract_candidates(scanned(form, place=(72, 500, 200, 260)))[0]  # about 11% of it
    assert (page.candidates, page.reason) == ([], "")


def test_a_small_picture_in_a_form_of_other_content_does_not_make_a_page_a_scan():
    frame = b"0 0 0 RG 1 w 20 20 572 752 re S"  # drawn by the same form, around the whole page
    found = extract_candidates(
        scanned(form, in_form=True, place=(72, 500, 200, 260), form_extra=frame)
    )[0]
    assert (found.candidates, found.reason) == ([], "")


def test_a_picture_drawn_by_a_moved_form_counts_where_it_lands():
    drawn, shrunk = b"/Fm1 Do\n", b"q 0.25 0 0 0.25 0 0 cm /Fm1 Do Q\n"  # into a 6% corner
    page = scanned(form, in_form=True).replace(
        b"/Length 8 >>\nstream\n" + drawn, b"/Length %d >>\nstream\n%s" % (len(shrunk), shrunk)
    )
    assert shrunk in page
    assert extract_candidates(page)[0].candidates == []


def test_a_drawn_rule_outside_the_visible_area_does_not_stop_the_image_scan():
    def crop(page):
        page.cropbox = RectangleObject([50, 100, 562, 742])

    hidden = b"0 0 0 RG 1 w 72 40 m 272 40 l S"  # under the crop box
    found = extract_candidates(edited(scanned(form, text=hidden), crop))[0]
    assert [(c.kind, c.from_image) for c in found.candidates] == [("rule", True), ("box", True)]


def test_a_page_with_a_drawn_rule_keeps_only_what_is_drawn():
    page = extract_candidates(scanned(form, text=b"0 0 0 RG 1 w 72 400 m 272 400 l S"))[0]
    assert [(c.kind, c.from_image, c.y) for c in page.candidates] == [("rule", False, 391.0)]
    assert page.reason == ""


def test_a_line_under_an_ocr_underscore_run_is_left_to_the_underscore():
    [run_] = extract_candidates(make_pdf([NAME_LINE]))[0].candidates
    bottom = run_.y + run_.height

    def page(canvas):
        canvas.fill(run_.x, bottom - 1, run_.x + run_.width, bottom)  # the scanned underscores
        form(canvas)

    invisible = NAME_LINE.replace(b"Tf ", b"Tf 3 Tr ")  # OCR text: there, but not drawn
    found = extract_candidates(scanned(page, text=invisible))[0]
    kinds = [(c.kind, c.from_image) for c in found.candidates]
    assert kinds == [("underscore", False), ("rule", True), ("box", True)]
    assert found.candidates[0].left_text == "Name:"
    assert found.reason == "page image scanned: 1 lines, 1 boxes"


def test_a_cropped_scanned_page_measures_from_its_visible_area():
    def crop(page):
        page.cropbox = RectangleObject([50, 100, 562, 742])

    found = extract_candidates(edited(scanned(form), crop))[0]
    rule, box = found.candidates
    assert (rule.x, rule.y + rule.height) == pytest.approx((22, 91), abs=0.5)
    assert (box.x, box.y) == pytest.approx((250, 130), abs=0.5)
    assert (found.width_pt, found.height_pt) == (512.0, 642.0)


def test_a_rotated_scanned_page_is_still_not_read():
    found = extract_candidates(edited(scanned(form), lambda page: page.rotate(90)))[0]
    assert (found.candidates, found.reason) == ([], "rotated page: candidates are not read")


def test_candidates_found_in_the_image_count_toward_the_page_limit(monkeypatch):
    monkeypatch.setattr(candidates_module, "MAX_PER_PAGE", 2)

    def three(canvas):
        form(canvas)
        canvas.fill(72, 400, 272, 401)

    found = extract_candidates(scanned(three))[0]
    assert (len(found.candidates), found.truncated_from) == (2, 3)


def test_a_large_page_is_scanned_at_a_lower_scale_or_not_at_all(monkeypatch):
    scales, render = [], candidates_module._render

    def counted(page, scale, **options):
        scales.append(scale)
        return render(page, scale, **options)

    monkeypatch.setattr(candidates_module, "_render", counted)
    monkeypatch.setattr(candidates_module, "IMAGE_MAX_PIXELS", 612 * 792 * 2.25)
    found = extract_candidates(scanned(form))[0]
    assert scales == [pytest.approx(1.5)] and len(found.candidates) == 2
    monkeypatch.setattr(candidates_module, "IMAGE_MAX_PIXELS", 612 * 792)
    found = extract_candidates(scanned(form))[0]
    assert (found.candidates, found.reason) == ([], "the page is too large for the image scan")
    assert len(scales) == 1


@pytest.mark.parametrize("failing", ["find", "_picture_share"])
def test_a_failed_image_scan_keeps_what_the_text_layer_gave(monkeypatch, failing):
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic")

    owner = candidates_module.raster if failing == "find" else candidates_module
    monkeypatch.setattr(owner, failing, broken)
    found = extract_candidates(scanned(form, text=NAME_LINE.replace(b"Tf ", b"Tf 3 Tr ")))[0]
    assert [c.kind for c in found.candidates] == ["underscore"]
    assert found.reason == "image scan failed: RuntimeError"


def test_one_scanned_page_reads_the_same_alone_and_with_its_document():
    source = scanned(form)
    alone = page_candidates(source, 1)
    assert alone == extract_candidates(source)[0] == page_candidates(source, 1)
    assert len(alone.candidates) == 2


def test_the_model_is_told_about_the_candidates_of_a_scanned_page(tmp_path):
    fake = FakeGateway()
    record = run(tmp_path, fake, source=scanned(form), config=SERIAL)
    [stage_a, *_] = [body for body in fake.chat_bodies() if page_of(body) == 1]
    text = stage_a["messages"][-1]["content"][0]["text"]
    assert "\np1-1 rule " in text and "\np1-2 box " in text
    assert record["evidence"][0]["candidates"] == 2


def test_candidates_found_in_the_image_are_numbered_after_the_others():
    low = NAME_LINE.replace(b"720", b"300").replace(b"Tf ", b"Tf 3 Tr ")  # OCR text low down
    found = extract_candidates(scanned(form, text=low))[0].candidates
    kinds = [(c.id, c.kind) for c in found]
    assert kinds == [("p1-1", "underscore"), ("p1-2", "rule"), ("p1-3", "box")]
    assert found[0].y > found[1].y  # numbered first, though it is lower on the page


def test_the_image_scan_scale_keeps_the_bitmap_within_its_budget():
    assert candidates_module._image_scale(612, 792) == 3.0
    assert candidates_module._image_scale(1224, 1584) == pytest.approx(2.031, abs=0.001)
    assert candidates_module._image_scale(0.01, 1e8) is None  # a sliver of millions of rows
    assert candidates_module._image_scale(0, 792) is None
    most = candidates_module.IMAGE_MAX_PIXELS + 2 * candidates_module.IMAGE_MAX_SIDE + 1
    for width, height in [(612, 792), (0.5, 9000), (3.3, 6600), (1224, 1584), (14400, 14400)]:
        scale = candidates_module._image_scale(width, height)
        if scale is not None:
            assert math.ceil(width * scale) * math.ceil(height * scale) <= most


def test_lines_are_matched_only_to_underscore_runs_near_their_bottom():
    runs = [(100.0, 500.0, 200.0, 512.0), (100.0, 300.0, 200.0, 312.0)]  # left, bottom, right, top
    lines = [
        (90.0, 503.0, 210.0, 504.0),  # 3 points above a run: left to it
        (90.0, 303.5, 210.0, 304.5),  # 3.5 points: kept
        (150.0, 500.0, 400.0, 501.0),  # over half of the run, not of itself: left to it
        (160.0, 300.0, 400.0, 301.0),  # under half of the shorter: kept
    ]
    assert candidates_module._apart_from(runs, lines) == [lines[1], lines[3]]


@pytest.mark.parametrize("in_form", [False, True])
def test_each_picture_is_a_page_of_its_own(in_form):
    blank = (b"\xff" * 4, 2, 2)
    pages = extract_candidates(image_pdf([blank, blank, blank], in_form=in_form))
    assert [page.page for page in pages] == [1, 2, 3]


def test_a_picture_check_cut_short_says_so(monkeypatch):
    monkeypatch.setattr(candidates_module, "PATH_SCAN_LIMIT", 1)  # the form, not the picture in it
    found = extract_candidates(scanned(form, in_form=True))[0]
    assert (found.candidates, found.reason) == ([], "picture check stopped after reading 1 objects")
    monkeypatch.setattr(candidates_module, "PATH_SCAN_LIMIT", 2)
    assert len(extract_candidates(scanned(form, in_form=True))[0].candidates) == 2
