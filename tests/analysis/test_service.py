import builtins
import hashlib
import io
import os
import pathlib
import zlib

import pytest
from pypdf import PageObject
from pypdf.errors import DependencyError

from studio.services import analysis as analysis_module
from studio.services.analysis import (
    INFERENCE_NOT_RUN,
    NO_TEXT_REASON,
    AnalysisError,
    PageFlag,
    analyze,
)
from studio.services.render import PageImage, render_png
from tests.analysis.pdf_factory import DRAWN_BOX, encrypt, make_pdf


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
    assert "page tree declares 5 page(s) but 3 were found" in result.diagnostics


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


def test_an_encrypted_pdf_with_an_empty_user_password_is_inventoried():
    result = analyze(encrypt(make_pdf(["one", "two"])))
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

    def one_page_fails(source):
        count, images = real(source)
        return count, [images[0], "PdfiumError: synthetic render failure"]

    monkeypatch.setattr(analysis_module, "inventory_images", one_page_fails)
    result = analyze(make_pdf(["one", "two"]))
    reason = "page image not rendered: PdfiumError: synthetic render failure"
    assert result.flags == [PageFlag(2, reason, True)]
    assert result.inventory == "INCOMPLETE"


def test_a_document_pdfium_cannot_open_flags_every_page(monkeypatch):
    def cannot_open(source):
        raise RuntimeError("synthetic open failure")

    monkeypatch.setattr(analysis_module, "inventory_images", cannot_open)
    result = analyze(make_pdf(["one", "two"]))
    assert result.renderer_page_count is None
    assert [flag.page for flag in result.flags if flag.blocks_complete] == [1, 2]
    assert result.inventory == "INCOMPLETE"


def test_a_source_over_the_size_limit_is_rejected_before_parsing(monkeypatch):
    monkeypatch.setattr(analysis_module, "MAX_SOURCE_BYTES", 100)
    with pytest.raises(AnalysisError, match="the limit is 100"):
        analyze(make_pdf(["too big"]))
    assert analysis_module.MAX_SOURCE_BYTES == 100
    monkeypatch.undo()
    assert analysis_module.MAX_SOURCE_BYTES == 50 * 1024 * 1024


def test_render_png_returns_a_valid_png_of_the_page():
    png = render_png(make_pdf(["x"]), 1, scale=2.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(png[16:20]), int.from_bytes(png[20:24])
    assert (width, height) == (1224, 1584)
    idat_length = int.from_bytes(png[33:37])
    assert png[37:41] == b"IDAT"
    raw = zlib.decompress(png[41 : 41 + idat_length])
    assert len(raw) == height * (1 + width * 3)
