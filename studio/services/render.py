"""Page rendering through PDFium (pypdfium2).

PDFium is not thread-safe, so every call holds one module lock, and every document, page and
bitmap is closed before the lock is released. Pages are rendered one at a time, and each render is
bounded in pixels, because a tiny file can declare an enormous page.
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
INVENTORY_MAX_SIDE = 1600  # larger pages are inventoried at a lower scale, recorded per page
MAX_RENDER_PIXELS = 16_000_000  # a model image above this is refused, not rendered
_LOCK = threading.Lock()


@dataclass(frozen=True)
class PageImage:
    width_px: int
    height_px: int
    scale: float  # pixels per point
    sha256: str  # of the raw pixel rows, for this renderer and scale
    blank: bool


def _render(page, scale: float, **options) -> tuple[list[bytes], int, int, int]:
    """Render one page and copy out its pixel rows (without stride padding), width, height and
    channel count. The bitmap is closed here, while the caller still holds the lock."""
    bitmap = page.render(scale=scale, **options)
    try:
        buffer, width = bytes(bitmap.buffer), bitmap.width * bitmap.n_channels
        rows = [buffer[y * bitmap.stride : y * bitmap.stride + width] for y in range(bitmap.height)]
        return rows, bitmap.width, bitmap.height, bitmap.n_channels
    finally:
        bitmap.close()


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
                        scale = min(INVENTORY_SCALE, INVENTORY_MAX_SIDE / max(page.get_size()))
                        rows, width, height, _ = _render(page, scale, grayscale=True)
                    finally:
                        page.close()
                except Exception as error:  # one page must not stop the rest
                    results.append(f"{type(error).__name__}: {error}")
                    continue
                pixels = b"".join(rows)
                results.append(
                    PageImage(
                        width_px=width,
                        height_px=height,
                        scale=scale,
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


@dataclass(frozen=True)
class RgbImage:
    width: int
    height: int
    rows: list[bytes]  # RGB, three bytes per pixel, no padding


def render_rgb(source: bytes, page_number: int, scale: float) -> RgbImage:
    """One page (one-based) as RGB pixel rows, bounded by MAX_RENDER_PIXELS."""
    if not scale > 0:
        raise ValueError(f"scale must be positive, got {scale}")
    with _LOCK:
        pdf = pdfium.PdfDocument(source)
        try:
            page = pdf[page_number - 1]
            try:
                width_pt, height_pt = page.get_size()
                if width_pt * height_pt * scale * scale > MAX_RENDER_PIXELS:
                    raise ValueError(
                        f"page {page_number} at scale {scale} exceeds the pixel budget "
                        f"of {MAX_RENDER_PIXELS}"
                    )
                rows, width, height, channels = _render(page, scale, rev_byteorder=True)
            finally:
                page.close()
        finally:
            pdf.close()
    if channels != 3:
        raise ValueError(f"expected an RGB bitmap, got {channels} channel(s)")
    return RgbImage(width, height, rows)


def encode_png(image: RgbImage) -> bytes:
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"".join(b"\x00" + row for row in image.rows), 6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", pixels)
        + _chunk(b"IEND", b"")
    )


def render_png(source: bytes, page_number: int, scale: float) -> bytes:
    """One page (one-based) as an RGB PNG, for submission to a model."""
    return encode_png(render_rgb(source, page_number, scale))
