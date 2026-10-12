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
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass

from pypdf import PdfReader
from pypdf.errors import DependencyError, FileNotDecryptedError, PyPdfError

INFERENCE_NOT_RUN = "NOT_RUN: model inference is not authorized"
IMAGES_NOT_RUN = "NOT_RUN: no page rasterizer has been adopted"
NO_TEXT_REASON = "no extractable text; page images are NOT_RUN"
# A mechanics signal only. An underscore run is not a templated field.
UNDERSCORE_RUN = re.compile(r"_{3,}")


class AnalysisError(Exception):
    """The source cannot be analyzed at all. The failure record still names the source."""

    def __init__(self, message: str, source: bytes, diagnostics: list[str]) -> None:
        super().__init__(message)
        self.source_sha256 = hashlib.sha256(source).hexdigest()
        self.source_bytes = len(source)
        self.diagnostics = diagnostics

    def to_dict(self) -> dict:
        return {
            "status": "FAILED",
            "error": str(self),
            "source_sha256": self.source_sha256,
            "source_bytes": self.source_bytes,
            "diagnostics": self.diagnostics,
        }


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
    # One bad page must not drop the rest of the inventory, and a text failure keeps geometry.
    width = height = rotation = None
    try:
        box = page.mediabox
        width, height, rotation = float(box.width), float(box.height), int(page.rotation)
        text = page.extract_text() or ""
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        return PageRecord(number, "error", width, height, rotation, "", 0, reason)
    if not text.strip():
        return PageRecord(number, "no_text", width, height, rotation, text, 0, NO_TEXT_REASON)
    runs = len(UNDERSCORE_RUN.findall(text))
    return PageRecord(number, "text", width, height, rotation, text, runs)


def _page_tree(reader: PdfReader) -> list:
    """Every leaf of the page tree.

    For an encrypted file pypdf sizes ``reader.pages`` from /Count, so the inventory reads the
    flattened tree, which pypdf builds on first page access, instead.
    """
    if reader.flattened_pages is None:
        with suppress(IndexError):  # an empty tree has no page 0
            reader.get_page(0)
    if reader.flattened_pages is None:
        raise PyPdfError("the page tree could not be read")
    return list(reader.flattened_pages)


def _declared_count(reader: PdfReader) -> int | None:
    try:
        return int(reader.root_object["/Pages"]["/Count"])
    except (KeyError, TypeError, ValueError, PyPdfError):
        return None


def _read(source: bytes) -> tuple[list[PageRecord], int | None]:
    reader = PdfReader(io.BytesIO(source), strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise FileNotDecryptedError("a password is required")
    pages = [_page_record(n, page) for n, page in enumerate(_page_tree(reader), start=1)]
    return pages, _declared_count(reader)


def analyze(source: bytes) -> AnalysisResult:
    """Inventory every page of a PDF given as bytes."""
    with _parser_diagnostics() as diagnostics:
        try:
            pages, declared = _read(source)
        except FileNotDecryptedError as error:
            raise AnalysisError("the PDF needs a password", source, diagnostics) from error
        except DependencyError as error:
            message = f"unsupported encryption, needs a dependency not adopted: {error}"
            raise AnalysisError(message, source, diagnostics) from error
        except Exception as error:  # any parser failure is a FAILED record, never a traceback
            message = f"unreadable PDF: {type(error).__name__}: {error}"
            raise AnalysisError(message, source, diagnostics) from error

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
