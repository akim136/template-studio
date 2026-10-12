"""Page rendering through PDFium (pypdfium2).

PDFium is not thread-safe, so every call holds one module lock. Pages are rendered one at a time
and released, so a large document is never rasterized all at once.
"""

from __future__ import annotations

import hashlib
import struct
import threading
import zlib
from dataclasses import dataclass

import pypdfium2 as pdfium

RENDERER = f"pdfium {pdfium.PDFIUM_INFO}"
INVENTORY_SCALE = 1.0  # 72 DPI grayscale: enough to prove the page renders and is not blank
_LOCK = threading.Lock()


@dataclass(frozen=True)
class PageImage:
    width_px: int
    height_px: int
    sha256: str  # of the raw pixel rows, for this renderer and scale
    blank: bool


def _rows(bitmap) -> list[bytes]:
    """Pixel rows without the stride padding PDFium may add."""
    buffer, width = bytes(bitmap.buffer), bitmap.width * bitmap.n_channels
    return [buffer[y * bitmap.stride : y * bitmap.stride + width] for y in range(bitmap.height)]


def inventory_images(source: bytes) -> tuple[int, list[PageImage | str]]:
    """Render every page at inventory scale. Returns PDFium's page count and, per page, the image
    evidence or the reason it could not be rendered."""
    with _LOCK:
        pdf = pdfium.PdfDocument(source)
        try:
            results: list[PageImage | str] = []
            for index in range(len(pdf)):
                try:
                    page = pdf[index]
                    try:
                        bitmap = page.render(scale=INVENTORY_SCALE, grayscale=True)
                        rows = _rows(bitmap)
                    finally:
                        page.close()
                except Exception as error:  # one page must not stop the rest
                    results.append(f"{type(error).__name__}: {error}")
                    continue
                pixels = b"".join(rows)
                results.append(
                    PageImage(
                        width_px=bitmap.width,
                        height_px=bitmap.height,
                        sha256=hashlib.sha256(pixels).hexdigest(),
                        blank=not pixels.strip(b"\xff"),
                    )
                )
            return len(pdf), results
        finally:
            pdf.close()


def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def render_png(source: bytes, page_number: int, scale: float) -> bytes:
    """One page (one-based) as an RGB PNG, for submission to a model."""
    with _LOCK:
        pdf = pdfium.PdfDocument(source)
        try:
            page = pdf[page_number - 1]
            try:
                bitmap = page.render(scale=scale, rev_byteorder=True)
                rows = _rows(bitmap)
            finally:
                page.close()
        finally:
            pdf.close()
    if bitmap.n_channels != 3:
        raise ValueError(f"expected an RGB bitmap, got {bitmap.n_channels} channel(s)")
    header = struct.pack(">IIBBBBB", bitmap.width, bitmap.height, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"".join(b"\x00" + row for row in rows), 6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", pixels)
        + _chunk(b"IEND", b"")
    )
