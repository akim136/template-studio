"""The one analysis service. The app and the evaluation wrapper both call ``analyze``.

This slice covers generic extraction and a full page inventory over the source bytes. Field
drafting (stage A) and self-check (stage B) need an authorized model route, so they report
NOT_RUN. Page images need a rasterizer that has not been adopted, so they report NOT_RUN too.

The service takes bytes and returns a record. It opens no files and makes no network calls.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PyPdfError

INFERENCE_NOT_RUN = "NOT_RUN: model inference is not authorized"
IMAGES_NOT_RUN = "NOT_RUN: no page rasterizer has been adopted"
# A mechanics signal only. An underscore run is not a templated field.
UNDERSCORE_RUN = re.compile(r"_{3,}")


class AnalysisError(Exception):
    """The source cannot be analyzed at all, for example unreadable or encrypted."""


@dataclass(frozen=True)
class PageRecord:
    page: int  # one-based
    status: str  # "text", "no_text" or "error"
    width_pt: float | None
    height_pt: float | None
    rotation: int | None
    text: str
    underscore_runs: int
    reason: str | None = None


@dataclass(frozen=True)
class AnalysisResult:
    source_sha256: str
    source_bytes: int
    page_count: int
    declared_page_count: int | None  # the page tree's /Count, checked against pages found
    inventory: str  # "COMPLETE" only when every page has a record and none failed
    pages: list[PageRecord]
    diagnostics: list[str]
    page_images: str = IMAGES_NOT_RUN
    stage_a_first_draft: str = INFERENCE_NOT_RUN
    stage_b_self_check: str = INFERENCE_NOT_RUN

    def to_dict(self) -> dict:
        return asdict(self)


class _ThreadCapture(logging.Handler):
    """Collects parser log records from the calling thread only."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.thread = threading.get_ident()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread:
            self.messages.append(f"{record.levelname} {record.name}: {record.getMessage()}")


@contextmanager
def _parser_diagnostics():
    logger = logging.getLogger("pypdf")
    capture = _ThreadCapture()
    logger.addHandler(capture)
    try:
        yield capture.messages
    finally:
        logger.removeHandler(capture)


def _page_record(number: int, page) -> PageRecord:
    try:
        box = page.mediabox
        width, height, rotation = float(box.width), float(box.height), int(page.rotation)
        text = page.extract_text() or ""
    except Exception as error:  # one bad page must not drop the rest of the inventory
        reason = f"{type(error).__name__}: {error}"
        return PageRecord(number, "error", None, None, None, "", 0, reason)
    status = "text" if text.strip() else "no_text"
    runs = len(UNDERSCORE_RUN.findall(text))
    return PageRecord(number, status, width, height, rotation, text, runs)


def _declared_count(reader: PdfReader) -> int | None:
    try:
        return int(reader.root_object["/Pages"]["/Count"])
    except (KeyError, TypeError, ValueError, PyPdfError):
        return None


def analyze(source: bytes) -> AnalysisResult:
    """Inventory every page of a PDF given as bytes."""
    with _parser_diagnostics() as diagnostics:
        try:
            reader = PdfReader(io.BytesIO(source), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise AnalysisError("the PDF is encrypted")
            pages = [_page_record(n, page) for n, page in enumerate(reader.pages, start=1)]
            declared = _declared_count(reader)
        except FileNotDecryptedError as error:
            raise AnalysisError("the PDF is encrypted") from error
        except PyPdfError as error:
            raise AnalysisError(f"unreadable PDF: {error}") from error

    findings = list(diagnostics)
    if declared != len(pages):
        findings.append(f"page tree declares {declared} page(s) but {len(pages)} were found")
    failed = [record.page for record in pages if record.status == "error"]
    if failed:
        findings.append(f"page(s) {failed} failed extraction")
    complete = bool(pages) and declared == len(pages) and not failed
    return AnalysisResult(
        source_sha256=hashlib.sha256(source).hexdigest(),
        source_bytes=len(source),
        page_count=len(pages),
        declared_page_count=declared,
        inventory="COMPLETE" if complete else "INCOMPLETE",
        pages=pages,
        diagnostics=findings,
    )
