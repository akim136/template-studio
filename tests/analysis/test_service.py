import builtins
import hashlib
import io
import os
import pathlib
from dataclasses import replace

import pytest
from pypdf import PageObject
from pypdf.errors import DependencyError

from studio.services import analysis as analysis_module
from studio.services import render
from studio.services.analysis import (
    INFERENCE_NOT_RUN,
    NO_TEXT_REASON,
    AnalysisError,
    PageFlag,
    analyze,
)
from studio.services.render import PageImage
from tests.analysis.pdf_factory import DRAWN_BOX, encrypt, make_pdf, shared_tree_pdf


def test_inventory_records_every_page_from_the_source_bytes():
    source = make_pdf(["Investor name: ______", "", "Subscription amount (USD)"])
    result = analyze(source)

    assert result.source_sha256 == hashlib.sha256(source).hexdigest()
    assert (result.page_count, result.declared_page_count) == (3, 3)
    assert result.inventory == "COMPLETE"
    assert [p.page for p in result.pages] == [1, 2, 3]
    assert [p.status for p in result.pages] == ["text", "no_text", "text"]
    assert [p.underscore_runs for p in result.pages] == [1, 0, 0]
    assert result.pages[2].text == "Subscription amount (USD)"
    assert {(p.width_pt, p.height_pt, p.rotation) for p in result.pages} == {(612.0, 792.0, 0)}
    assert result.diagnostics == []


def test_model_stages_are_reported_not_run_and_no_fields_are_substituted():
    record = analyze(make_pdf(["Name: ____"])).to_dict()
    assert record["stage_a_first_draft"] == INFERENCE_NOT_RUN
    assert record["stage_b_self_check"] == INFERENCE_NOT_RUN
    assert record["renderer"].startswith("pdfium ")
    assert "fields" not in record


def test_a_page_missing_from_the_page_tree_makes_the_inventory_incomplete():
    source = make_pdf(["a", "b", "c"]).replace(b"/Count 3", b"/Count 5")
    result = analyze(source)
    assert (result.page_count, result.declared_page_count) == (3, 5)
    assert result.inventory == "INCOMPLETE"
    assert PageFlag(None, "the page tree declares 5 page(s) but 3 were found", True) in result.flags


def test_a_page_tree_without_a_count_is_flagged_in_words():
    result = analyze(make_pdf(["a"]).replace(b" /Count 1", b""))
    assert result.declared_page_count is None
    assert PageFlag(None, "the page tree does not declare a page count", True) in result.flags
    assert not any("None" in flag.reason for flag in result.flags)


def test_a_failed_page_is_recorded_not_dropped(monkeypatch):
    real = PageObject.extract_text

    def extract_text(self, *args, **kwargs):
        text = real(self, *args, **kwargs)
        if text == "two":
            raise ValueError("synthetic page failure")
        return text

    monkeypatch.setattr(PageObject, "extract_text", extract_text)
    result = analyze(make_pdf(["one", "two", "three"]))

    assert [p.status for p in result.pages] == ["text", "error", "text"]
    assert result.pages[1].reason == "ValueError: synthetic page failure"
    failed = result.pages[1]
    assert (failed.width_pt, failed.height_pt, failed.rotation) == (612.0, 792.0, 0)
    assert result.inventory == "INCOMPLETE"
    blocking = PageFlag(2, "text extraction failed: ValueError: synthetic page failure", True)
    assert blocking in result.flags


def test_parser_diagnostics_are_captured_and_never_printed(capsys):
    result = analyze(make_pdf(["recovered"], bad_startxref=True))
    assert result.inventory == "COMPLETE"
    assert any("startxref" in line for line in result.diagnostics)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("source", [b"", b"not a pdf", make_pdf(["x"])[:40]])
def test_unreadable_input_raises_analysis_error(source):
    with pytest.raises(AnalysisError, match="unreadable PDF"):
        analyze(source)


def test_the_service_opens_no_files(monkeypatch):
    """Generator isolation: the service works from the bytes it is handed and nothing else."""

    def refuse(*args, **kwargs):
        raise AssertionError(f"analysis tried to open {args[:1]}")

    for target, name in [(builtins, "open"), (io, "open"), (os, "open")]:
        monkeypatch.setattr(target, name, refuse)
    monkeypatch.setattr(pathlib.Path, "open", refuse)
    assert analyze(make_pdf(["isolated"])).inventory == "COMPLETE"


@pytest.mark.parametrize(("real", "declared"), [(5, 3), (3, 5)])
def test_an_encrypted_pdf_is_inventoried_from_its_page_tree(real, declared):
    """pypdf sizes an encrypted file's pages from /Count, so the inventory must not trust it."""
    texts = [f"page {n}" for n in range(1, real + 1)]
    source = encrypt(make_pdf(texts).replace(b"/Count %d" % real, b"/Count %d" % declared))
    result = analyze(source)
    assert (result.page_count, result.declared_page_count) == (real, declared)
    assert [p.text for p in result.pages] == texts
    assert result.inventory == "INCOMPLETE"


@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-128", "AES-256"])
def test_an_encrypted_pdf_with_an_empty_user_password_is_read_and_rendered(algorithm):
    result = analyze(encrypt(make_pdf(["one", "two"]), algorithm))
    assert [p.text for p in result.pages] == ["one", "two"]
    assert [p.image.blank for p in result.pages] == [False, False]
    assert (result.page_count, result.inventory) == (2, "COMPLETE")


@pytest.mark.parametrize(
    ("raised", "message"),
    [
        (DependencyError("cryptography>=3.1 is required for AES algorithm"), "unsupported"),
        (KeyError("/Root"), "unreadable PDF: KeyError"),
        (NotImplementedError("crypt filter"), "unreadable PDF: NotImplementedError"),
    ],
)
def test_any_parser_failure_is_an_analysis_error_that_names_the_source(
    monkeypatch, raised, message
):
    def reader(*args, **kwargs):
        raise raised

    monkeypatch.setattr(analysis_module, "PdfReader", reader)
    source = make_pdf(["x"])
    with pytest.raises(AnalysisError, match=message) as caught:
        analyze(source)
    record = caught.value.to_dict()
    assert record["status"] == "FAILED"
    assert record["source_sha256"] == hashlib.sha256(source).hexdigest()


def test_a_page_without_text_records_why():
    page = analyze(make_pdf([""])).pages[0]
    assert (page.status, page.reason) == ("no_text", NO_TEXT_REASON)


def test_every_page_is_rendered_and_pages_without_text_are_flagged():
    result = analyze(make_pdf(["Signature: ______", "", DRAWN_BOX]))
    assert result.renderer_page_count == 3
    assert all(isinstance(p.image, PageImage) for p in result.pages)
    assert {(p.image.width_px, p.image.height_px) for p in result.pages} == {(612, 792)}
    assert [p.image.blank for p in result.pages] == [False, True, False]
    assert result.flags == [
        PageFlag(2, "the page renders blank", False),
        PageFlag(3, "no text layer; the page needs visual inspection", False),
    ]
    assert result.inventory == "COMPLETE"


def test_a_page_that_cannot_be_rendered_is_flagged_and_blocks_complete(monkeypatch):
    real = analysis_module.inventory_images

    def one_page_fails(source, before_page=None):
        count, images = real(source, before_page)
        return count, [images[0], "PdfiumError: synthetic render failure"]

    monkeypatch.setattr(analysis_module, "inventory_images", one_page_fails)
    result = analyze(make_pdf(["one", "two"]))
    reason = "page image not rendered: PdfiumError: synthetic render failure"
    assert result.flags == [PageFlag(2, reason, True)]
    assert result.inventory == "INCOMPLETE"


def test_a_document_pdfium_cannot_open_flags_every_page(monkeypatch):
    def cannot_open(source, before_page=None):
        raise RuntimeError("synthetic open failure")

    monkeypatch.setattr(analysis_module, "inventory_images", cannot_open)
    result = analyze(make_pdf(["one", "two"]))
    assert result.renderer_page_count is None
    assert [flag.page for flag in result.flags if flag.blocks_complete] == [1, 2]
    assert result.inventory == "INCOMPLETE"


def test_a_source_over_the_size_limit_is_rejected_before_parsing(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("parsed or rendered a source over the limit")

    monkeypatch.setattr(analysis_module, "PdfReader", must_not_run)
    monkeypatch.setattr(analysis_module, "inventory_images", must_not_run)
    source = make_pdf(["too big"])
    monkeypatch.setattr(analysis_module, "MAX_SOURCE_BYTES", len(source) - 1)
    with pytest.raises(AnalysisError, match=f"the limit is {len(source) - 1} bytes") as caught:
        analyze(source)
    assert caught.value.to_dict()["source_sha256"] == hashlib.sha256(source).hexdigest()


def test_a_source_exactly_at_the_size_limit_is_analyzed(monkeypatch):
    source = make_pdf(["just fits"])
    monkeypatch.setattr(analysis_module, "MAX_SOURCE_BYTES", len(source))
    assert analyze(source).inventory == "COMPLETE"


def test_the_limits_are_the_agreed_ones_and_the_size_message_says_25_mib():
    assert analysis_module.MAX_SOURCE_BYTES == 25 * 1024 * 1024
    assert analysis_module.MAX_PAGES == 400
    assert analysis_module.MAX_TEXT_CHARS == 4_000_000
    assert analysis_module.TIME_LIMIT_S == 180
    with pytest.raises(AnalysisError, match=r"\(25 MiB\)"):
        analyze(b"0" * (analysis_module.MAX_SOURCE_BYTES + 1))


def test_a_source_over_the_page_limit_fails_before_any_page_is_read(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("read or rendered a page of a source over the limit")

    monkeypatch.setattr(analysis_module, "MAX_PAGES", 2)
    monkeypatch.setattr(analysis_module, "_page_record", must_not_run)
    monkeypatch.setattr(analysis_module, "inventory_images", must_not_run)
    with pytest.raises(AnalysisError, match="has 3 pages; the limit is 2") as caught:
        analyze(make_pdf(["a", "b", "c"]))
    assert caught.value.to_dict()["status"] == "FAILED"


def test_a_page_tree_is_expanded_only_to_a_bound_before_its_pages_are_counted(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("read or rendered a page of a source over the limit")

    monkeypatch.setattr(analysis_module, "_page_record", must_not_run)
    monkeypatch.setattr(analysis_module, "inventory_images", must_not_run)
    with pytest.raises(AnalysisError, match=r"page tree .*1601 > 1600.*the page limit is 400"):
        analyze(shared_tree_pdf(depth=20))  # 2**20 pages through 22 objects


def test_a_source_at_the_page_limit_is_analyzed(monkeypatch):
    monkeypatch.setattr(analysis_module, "MAX_PAGES", 2)
    assert analyze(make_pdf(["a", "b"])).inventory == "COMPLETE"


def test_pdfium_is_held_to_the_page_limit_before_it_renders(monkeypatch):
    def must_not_render(*args, **kwargs):
        raise AssertionError("rendered a page of a source over the limit")

    source = make_pdf(["a", "b"])
    pages, _ = analysis_module._read(source, deadline=float("inf"))
    monkeypatch.setattr(analysis_module, "MAX_PAGES", 1)
    monkeypatch.setattr(render, "_render", must_not_render)
    with pytest.raises(analysis_module.LimitExceeded, match="PDFium found 2 pages; the limit"):
        analysis_module._with_images(source, pages, deadline=float("inf"))


def test_text_past_the_limit_fails_the_analysis(monkeypatch):
    monkeypatch.setattr(analysis_module, "MAX_TEXT_CHARS", 5)
    analyze(make_pdf(["abcde"]))  # exactly at the limit
    with pytest.raises(AnalysisError, match="reaches 8 characters by page 2; the limit is 5"):
        analyze(make_pdf(["abc", "defgh"]))


def clock(step):
    ticks = iter(range(0, 10_000, step))
    return lambda: next(ticks)


def test_an_analysis_past_its_time_limit_fails_between_pages(monkeypatch):
    monkeypatch.setattr(analysis_module, "_clock", clock(100))  # start 0, pages at 100 and 200
    with pytest.raises(AnalysisError, match="time limit of 180 seconds .*reading page 2"):
        analyze(make_pdf(["a", "b"]))


def test_the_time_limit_also_covers_rendering(monkeypatch):
    monkeypatch.setattr(analysis_module, "_clock", clock(50))  # read ends at 150, render at 200
    with pytest.raises(AnalysisError, match="time limit of 180 seconds .*rendering page 1"):
        analyze(make_pdf(["a", "b", "c"]))


def test_the_time_limit_is_checked_once_more_after_the_last_page(monkeypatch):
    monkeypatch.setattr(analysis_module, "_clock", clock(70))  # read 70, render 140, end 210
    with pytest.raises(AnalysisError, match="time limit of 180 seconds after the last page"):
        analyze(make_pdf(["a"]))


def test_the_time_limit_holds_when_pdfium_cannot_open_the_document(monkeypatch):
    def unopenable(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(analysis_module, "inventory_images", unopenable)
    monkeypatch.setattr(analysis_module, "_clock", clock(100))  # read 100, end 200
    with pytest.raises(AnalysisError, match="time limit of 180 seconds after the last page"):
        analyze(make_pdf(["a"]))


def test_pages_pdfium_did_not_find_are_flagged(monkeypatch):
    real = analysis_module.inventory_images

    def finds_one(source, before_page=None):
        count, images = real(source, before_page)
        return 1, images[:1]

    monkeypatch.setattr(analysis_module, "inventory_images", finds_one)
    result = analyze(make_pdf(["one", "two"]))
    assert PageFlag(2, "page image not rendered: PDFium found only 1 page(s)", True) in result.flags
    assert PageFlag(None, "PDFium found 1 page(s) but the page tree has 2", True) in result.flags
    assert result.inventory == "INCOMPLETE"


def test_extra_pages_pdfium_found_are_flagged_not_dropped(monkeypatch):
    real = analysis_module.inventory_images

    def finds_three(source, before_page=None):
        count, images = real(source, before_page)
        return 3, images + images[:1]

    monkeypatch.setattr(analysis_module, "inventory_images", finds_three)
    result = analyze(make_pdf(["one", "two"]))
    assert result.flags == [PageFlag(None, "PDFium found 3 page(s) but the page tree has 2", True)]
    assert result.inventory == "INCOMPLETE"


def test_a_blank_render_of_a_page_with_text_blocks_complete(monkeypatch):
    """PDFium does not report content it cannot draw; a blank page that has text is suspect."""
    real = analysis_module.inventory_images

    def first_page_blank(source, before_page=None):
        count, images = real(source, before_page)
        return count, [replace(images[0], blank=True), *images[1:]]

    monkeypatch.setattr(analysis_module, "inventory_images", first_page_blank)
    result = analyze(make_pdf(["has text", "also text"]))
    reason = "the page renders blank although it has a text layer"
    assert result.flags == [PageFlag(1, reason, True)]
    assert result.inventory == "INCOMPLETE"
