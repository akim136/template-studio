import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from studio.services import candidates as candidates_module
from studio.services.candidates import Candidate, extract_candidates
from studio.services.fields import COORDINATE_SPACE, envelope, resolve, stage_schema
from studio.services.overlay import COLORS, draw_boxes
from studio.services.render import RgbImage
from tests.analysis.pdf_factory import make_pdf

ROLES = ["Client", "CounterSigner"]
FORM = (
    b"BT /F1 12 Tf 72 720 Td (Name: ____________) Tj ET\n"
    b"BT /F1 10 Tf 72 638 Td (Signature) Tj ET\n"
    b"0 0 0 RG 1 w 72 650 m 272 650 l S\n"
    b"0 0 0 RG 1 w 300 600 10 10 re S\n"
    b"BT /F1 10 Tf 314 601 Td (I agree) Tj ET"
)
RULE = Candidate("p1-2", 1, "rule", 71.0, 141.0, 202.0, 2.0, "", "", "Signature")
BOX = Candidate("p1-3", 1, "box", 299.0, 181.0, 12.0, 12.0, "", "I agree", "")


def raw(**overrides):
    field = {
        "candidate_id": None,
        "type": "text",
        "role": "Client",
        "merge_name": None,
        "required": False,
        "box": {"left": 100, "top": 200, "width": 300, "height": 40},
        "evidence": "label",
    }
    return {**field, **overrides}


def test_candidates_are_read_from_underscores_rules_and_boxes_in_top_left_points():
    page = extract_candidates(make_pdf([FORM]))[0]
    kinds = {c.kind: c for c in page.candidates}
    assert set(kinds) == {"underscore", "rule", "box"}
    assert kinds["underscore"].left_text == "Name:"
    assert kinds["rule"].below_text == "Signature"
    assert kinds["rule"].y == pytest.approx(792 - 651, abs=1)
    assert kinds["box"].right_text == "I agree"
    assert (kinds["box"].width, kinds["box"].height) == pytest.approx((12, 12), abs=1)


def test_a_rotated_page_says_why_it_has_no_candidates():
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([FORM]))))
    writer.pages[0].rotate(90)
    out = io.BytesIO()
    writer.write(out)
    page = extract_candidates(out.getvalue())[0]
    assert page.candidates == [] and "rotated" in page.reason


def test_a_field_on_a_line_takes_the_line_span_and_a_height_by_type():
    fields, rejected = resolve(
        1, [raw(candidate_id="p1-2", type="signature")], ROLES, [RULE], 2.0, (612, 792)
    )
    assert rejected == []
    (field,) = fields
    assert (field.x, field.width, field.height) == (71.0, 202.0, 24.0)
    assert field.y + field.height == pytest.approx(RULE.y + RULE.height)
    assert field.geometry_source == "candidate" and field.signer == 0


def test_a_checkbox_on_a_box_candidate_takes_the_box_exactly():
    (field,), _ = resolve(
        1, [raw(candidate_id="p1-3", type="checkbox")], ROLES, [BOX], 2.0, (612, 792)
    )
    assert (field.x, field.y, field.width, field.height) == (299.0, 181.0, 12.0, 12.0)


def test_a_model_box_is_converted_from_pixels_and_clipped_to_the_page():
    box = {"left": 1100, "top": 100, "width": 400, "height": 30}
    (field,), _ = resolve(1, [raw(box=box)], ROLES, [], 2.0, (612, 792))
    assert (field.x, field.y, field.height) == (550.0, 50.0, 15.0)
    assert field.width == 62.0  # clipped at the page's right edge
    assert field.geometry_source == "model_box"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"type": "text-merge", "role": "Client", "merge_name": "email"}, "merge field"),
        ({"type": "text-merge", "role": "sender", "merge_name": None}, "merge field"),
        ({"type": "text", "role": "sender"}, "only merge fields"),
        ({"candidate_id": "p9-9"}, "unknown candidate"),
        ({"box": {"left": 5000, "top": 0, "width": 10, "height": 10}}, "no area"),
    ],
)
def test_invalid_fields_are_rejected_with_a_reason_not_dropped(overrides, reason):
    fields, rejected = resolve(1, [raw(**overrides)], ROLES, [RULE], 2.0, (612, 792))
    assert fields == [] and len(rejected) == 1 and reason in rejected[0].reason


def test_the_checker_envelope_has_source_points_and_names_only_on_merge_fields():
    merge = raw(type="text-merge", role="sender", merge_name="email")
    fields, _ = resolve(1, [raw(role="CounterSigner"), merge], ROLES, [], 2.0, (612, 792))
    out = envelope(
        source_sha256="a" * 64,
        pages=[{"document_index": 0, "page": 1, "width": 612, "height": 792, "rotation": 0}],
        fields=fields,
        signer_mode="synthetic",
        business_rules_sha256="b" * 64,
        output_stage="A",
    )
    assert out["coordinate_space"] == COORDINATE_SPACE
    text, email = out["fields"]
    assert text["signer"] == 1 and "name" not in text
    assert email["signer"] == "sender" and email["name"] == "email"


def test_the_schema_is_strict_and_offers_only_the_roles_and_sender():
    schema = stage_schema(ROLES, self_check=True)
    item = schema["properties"]["fields"]["items"]
    assert schema["additionalProperties"] is False and item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])
    assert item["properties"]["role"]["enum"] == ["Client", "CounterSigner", "sender"]
    assert "changes" in schema["required"]
    assert "changes" not in stage_schema(ROLES, self_check=False)["properties"]


def test_the_overlay_outlines_each_field_in_its_type_color_and_numbers_it():
    white = RgbImage(200, 100, [b"\xff" * 600 for _ in range(100)])
    drawn = draw_boxes(white, [(7, "signature", 50, 40, 100, 40)])
    pixel = lambda x, y: tuple(drawn.rows[y][x * 3 : x * 3 + 3])  # noqa: E731
    assert pixel(100, 79) == COLORS["signature"]  # bottom edge
    assert pixel(100, 60) == (255, 255, 255)  # inside stays clear
    assert any(pixel(x, y) == COLORS["signature"] for x in range(50, 70) for y in range(19, 40))
    assert white.rows[79] == b"\xff" * 600  # the source image is not modified


def test_a_box_hanging_off_the_page_is_clipped_not_shifted():
    box = {"left": -20, "top": -10, "width": 100, "height": 30}
    (field,), _ = resolve(1, [raw(box=box)], ROLES, [], 1.0, (612, 792))
    assert (field.x, field.y, field.width, field.height) == (0.0, 0.0, 80.0, 20.0)


def test_candidates_are_measured_from_the_visible_area_and_report_its_size():
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([FORM]))))
    writer.pages[0].cropbox = RectangleObject([50, 100, 450, 700])
    out = io.BytesIO()
    writer.write(out)
    page = extract_candidates(out.getvalue())[0]
    assert (page.width_pt, page.height_pt) == (400.0, 600.0)
    rule = next(c for c in page.candidates if c.kind == "rule")
    assert rule.x == pytest.approx(71 - 50, abs=1) and rule.y == pytest.approx(700 - 651, abs=1)


def test_the_shape_scan_stops_at_its_limit_and_says_so(monkeypatch):
    monkeypatch.setattr(candidates_module, "SCAN_LIMIT", 1)
    page = extract_candidates(make_pdf([FORM]))[0]
    assert len(page.candidates) <= 3 and "scan stopped" in page.reason


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"required": "false"}, "required"),
        ({"type": "stamp"}, "unknown type"),
        ({"role": "Notary"}, "unknown role"),
        ({"box": {"left": "1", "top": 0, "width": 10, "height": 10}}, "box"),
        ({"box": {"left": float("nan"), "top": 0, "width": 10, "height": 10}}, "box"),
        ({"box": {"left": True, "top": 0, "width": 10, "height": 10}}, "box"),
        ({"evidence": None}, "evidence"),
    ],
)
def test_malformed_fields_are_rejected_not_coerced(overrides, reason):
    fields, rejected = resolve(1, [raw(**overrides)], ROLES, [], 2.0, (612, 792))
    assert fields == [] and reason in rejected[0].reason


def test_candidates_are_read_only_on_requested_pages_but_every_page_is_measured():
    first, second = extract_candidates(make_pdf([FORM, FORM]), pages=[2])
    assert first.candidates == [] and "not requested" in first.reason
    assert (first.width_pt, first.height_pt) == (612.0, 792.0)
    assert len(second.candidates) == 3


def test_a_document_pdfium_cannot_open_gives_a_reason_per_requested_page():
    pages = extract_candidates(b"%PDF-1.7 not really", pages=[1, 2])
    assert [p.page for p in pages] == [1, 2]
    assert all(p.width_pt == 0 and "could not open" in p.reason for p in pages)


def test_a_page_whose_scan_fails_keeps_its_measured_size(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic scan failure")

    monkeypatch.setattr(candidates_module, "_underscore_runs", broken)
    (page,) = extract_candidates(make_pdf([FORM]))
    assert page.candidates == [] and "RuntimeError" in page.reason
    assert (page.width_pt, page.height_pt) == (612.0, 792.0)


def test_a_page_with_more_characters_than_the_scan_reads_skips_only_the_character_scans(
    monkeypatch,
):
    monkeypatch.setattr(candidates_module, "CHAR_SCAN_LIMIT", 10)
    (page,) = extract_candidates(make_pdf([FORM]))
    assert sorted(c.kind for c in page.candidates) == ["box", "rule"]  # drawn shapes still read
    assert "character scans skipped" in page.reason and "limit is 10" in page.reason


def test_the_drawn_path_scan_counts_objects_of_every_kind(monkeypatch):
    monkeypatch.setattr(candidates_module, "PATH_SCAN_LIMIT", 2)
    runs = b" ".join(b"BT /F1 12 Tf 72 %d Td (x) Tj ET" % y for y in (720, 700, 680))
    (page,) = extract_candidates(make_pdf([runs]))  # three text objects and no path
    assert "drawn-path scan stopped after reading 2 objects" in page.reason


def test_the_drawn_path_scan_reads_at_most_its_limit_of_objects(monkeypatch):
    monkeypatch.setattr(candidates_module, "PATH_SCAN_LIMIT", 1)
    (page,) = extract_candidates(make_pdf([FORM]))
    assert [c.kind for c in page.candidates].count("box") == 0  # the second path is never read
    assert "drawn-path scan stopped after reading 1 objects" in page.reason
