import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from studio.services import candidates as candidates_module
from studio.services.candidates import extract_candidates, page_candidates
from tests.analysis.fake_gateway import FakeGateway, page_of
from tests.analysis.pdf_factory import image_pdf, make_pdf
from tests.analysis.test_inference import SERIAL, run
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


def edited(source, change):
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(source)))
    change(writer.pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


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


def test_a_failed_image_scan_keeps_what_the_text_layer_gave(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(candidates_module.raster, "find", broken)
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
