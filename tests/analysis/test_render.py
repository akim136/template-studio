import struct
import zlib

import pypdfium2 as pdfium
import pytest

from studio.services import render
from studio.services.render import (
    INVENTORY_MAX_SIDE,
    MAX_RENDER_PIXELS,
    inventory_images,
    render_png,
)
from tests.analysis.pdf_factory import make_pdf

RED_PAGE = b"1 0 0 rg 0 0 612 792 re f"


def png_chunks(png: bytes) -> list[tuple[bytes, bytes]]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    chunks, offset = [], 8
    while offset < len(png):
        (length,) = struct.unpack(">I", png[offset : offset + 4])
        tag, data = png[offset + 4 : offset + 8], png[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", png[offset + 8 + length : offset + 12 + length])
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF, tag
        chunks.append((tag, data))
        offset += 12 + length
    return chunks


def test_every_pdfium_object_is_closed_while_the_lock_is_held(monkeypatch):
    closed = []
    for cls in (pdfium.PdfBitmap, pdfium.PdfPage, pdfium.PdfDocument):
        real = cls.close

        def close(self, *args, _real=real, _name=cls.__name__, **kwargs):
            closed.append((_name, render._LOCK.locked()))
            return _real(self, *args, **kwargs)

        monkeypatch.setattr(cls, "close", close)

    source = make_pdf(["one", "two", "three"])
    inventory_images(source)
    render_png(source, 2, scale=1.0)

    assert all(locked for _, locked in closed), closed
    counts = {name: sum(1 for n, _ in closed if n == name) for name, _ in closed}
    assert counts == {"PdfBitmap": 4, "PdfPage": 4, "PdfDocument": 2}


def test_a_huge_page_is_inventoried_downscaled_not_at_full_size():
    count, images = inventory_images(make_pdf(["x"], size=(14400, 14400)))
    image = images[0]
    assert count == 1
    assert max(image.width_px, image.height_px) <= INVENTORY_MAX_SIDE
    assert image.scale < 1.0


def test_render_png_refuses_a_page_over_the_pixel_budget():
    side = int((MAX_RENDER_PIXELS**0.5) + 100)
    with pytest.raises(ValueError, match="pixel budget"):
        render_png(make_pdf(["x"], size=(side, side)), 1, scale=1.0)


def test_render_png_is_an_rgb_png_with_valid_chunks():
    png = render_png(make_pdf([RED_PAGE]), 1, scale=2.0)
    chunks = png_chunks(png)
    assert [tag for tag, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    width, height, depth, color = struct.unpack(">IIBB", chunks[0][1][:10])
    assert (width, height, depth, color) == (1224, 1584, 8, 2)
    raw = zlib.decompress(chunks[1][1])
    assert len(raw) == height * (1 + width * 3)
    red, green, blue = raw[1:4]  # first pixel after the row's filter byte
    assert red > 200 and green < 50 and blue < 50


def test_the_inventory_asks_before_each_page_and_can_be_stopped(monkeypatch):
    asked = []

    def before_page(index, total):
        asked.append((index, total))
        if index == 1:
            raise RuntimeError("synthetic stop")

    with pytest.raises(RuntimeError, match="synthetic stop"):  # never recorded as a page failure
        inventory_images(make_pdf(["one", "two", "three"]), before_page)
    assert asked == [(0, 3), (1, 3)]
