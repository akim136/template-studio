"""Checkbox glyphs in the text layer, including symbol fonts that carry no Unicode mapping."""

import ctypes

import pypdfium2 as pdfium
import pytest

from studio.services.candidates import _font_family, extract_candidates
from tests.analysis.pdf_factory import from_objects

WIDTHS = b"[" + b" ".join(b"1000" for _ in range(256)) + b"]"


def glyph_page(font: bytes, code: int, unicode: int | None = None) -> bytes:
    """One page showing character `code` at 12 pt in a non-embedded symbolic font named `font`,
    with a ToUnicode map to `unicode` when given, and a Helvetica line below."""
    content = b"BT /F1 12 Tf 100 700 Td <%02x> Tj ET BT /F2 12 Tf 100 650 Td (A label) Tj ET" % code
    to_unicode = b""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R /F2 7 0 R >> >> /Contents 5 0 R >>",
        5: b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        6: b"<< /Type /FontDescriptor /FontName /%s /Flags 4 /FontBBox [0 -200 1000 800] "
        b"/ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>" % font,
        7: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    if unicode is not None:
        cmap = (
            b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
            b"/CMapName /Adobe-Identity-UCS def /CMapType 2 def "
            b"1 begincodespacerange <00> <FF> endcodespacerange "
            b"1 beginbfchar <%02x> <%04x> endbfchar endcmap "
            b"CMapName currentdict /CMap defineresource pop end end" % (code, unicode)
        )
        objects[8] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap), cmap)
        to_unicode = b" /ToUnicode 8 0 R"
    objects[3] = (
        b"<< /Type /Font /Subtype /TrueType /BaseFont /%s /FirstChar 0 /LastChar 255 "
        b"/Widths %s /FontDescriptor 6 0 R%s >>" % (font, WIDTHS, to_unicode)
    )
    return from_objects(objects)


def boxes(data: bytes) -> list:
    [page] = extract_candidates(data)
    return [c for c in page.candidates if c.kind == "box"]


@pytest.mark.parametrize(
    ("font", "code"),
    [
        (b"Wingdings2", 0xA3),  # read as "£" without a Unicode map
        (b"ABCDEF+Wingdings2", 0xA3),  # an embedded subset
        (b"Wingdings#202", 0xA3),  # "Wingdings 2"
        (b"Wingdings-Regular", 0x6F),  # read as "o"
        (b"Wingdings", 0xA8),
        (b"Wingdings", 0x71),
    ],
)
def test_a_symbol_font_box_without_a_unicode_map_is_a_box(font, code):
    [box] = boxes(glyph_page(font, code))
    assert box.y == pytest.approx(83, abs=1) and 5 <= box.width <= 18


def test_a_symbol_font_box_mapped_to_the_private_use_area_is_a_box():
    # Word maps a symbol font's code c to U+F000 + c.
    assert len(boxes(glyph_page(b"Wingdings2", 0xA3, unicode=0xF0A3))) == 1


@pytest.mark.parametrize(
    ("font", "code"),
    [
        (b"Helvetica", 0xA3),  # a pound sign
        (b"Arial", 0x6F),  # the letter o
        (b"Wingdings2", 0x6F),  # a Wingdings code, in the other font
        (b"Wingdings", 0xA3),  # a Wingdings 2 code, in the other font
        (b"Wingdings3", 0xA3),
    ],
)
def test_the_same_codes_in_other_fonts_are_not_boxes(font, code):
    assert boxes(glyph_page(font, code)) == []


def test_a_font_name_longer_than_the_buffer_names_no_family():
    # PDFium writes nothing when a name does not fit, so what the buffer held before is not read.
    textpage = pdfium.PdfDocument(glyph_page(b"Wingdings2", 0xA3))[0].get_textpage()
    assert _font_family(textpage, 0, ctypes.create_string_buffer(256)) == "wingdings2"
    assert _font_family(textpage, 0, ctypes.create_string_buffer(b"Symbol", 8)) == ""
