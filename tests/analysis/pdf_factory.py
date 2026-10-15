"""Build small synthetic PDFs at test time. No PDF fixture is committed."""

import io
import zlib


def _escape(text: str) -> bytes:
    return (
        text.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    )


DRAWN_BOX = b"0 0 0 rg 72 600 200 40 re f"  # a filled rectangle and no text


def make_pdf(texts: list[str | bytes], *, size=(612, 792), bad_startxref: bool = False) -> bytes:
    """One page per entry, each showing its text in Helvetica. An empty entry is a blank page,
    and a bytes entry is used as the page's raw content stream.

    ``bad_startxref`` points the trailer at the wrong offset, so the parser recovers with
    diagnostics instead of reading cleanly.
    """
    page_ids = [4 + 2 * i for i in range(len(texts))]
    kids = b" ".join(b"%d 0 R" % page for page in page_ids)
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(texts)),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page, text in zip(page_ids, texts, strict=True):
        if isinstance(text, bytes):
            stream = text
        else:
            stream = b"BT /F1 12 Tf 72 720 Td (%s) Tj ET" % _escape(text) if text else b""
        objects[page] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (*size, page + 1)
        )
        objects[page + 1] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
    return from_objects(objects, bad_startxref=bad_startxref)


def image_pdf(
    pictures: list[tuple[bytes, int, int]],
    *,
    size=(612, 792),
    place: tuple[float, float, float, float] | None = None,
    text: bytes = b"",
    in_form: bool = False,
) -> bytes:
    """One page per picture, as a scanner writes them: 8-bit grey pixels (rows top to bottom,
    width by height) drawn over ``place`` (left, bottom, width, height in points; default: the
    whole page). ``text`` is added to the page's content, such as an invisible OCR layer, and
    ``in_form`` draws the picture from inside a form XObject."""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    left, bottom, width, height = place or (0, 0, *size)
    draw = b"q %.2f 0 0 %.2f %.2f %.2f cm /Im1 Do Q" % (width, height, left, bottom)
    kids = []
    step = 4 if in_form else 3  # page, contents, image and, in a form, the form
    for index, (pixels, columns, rows) in enumerate(pictures):
        page = 4 + step * index
        image, form = page + 2, page + 3
        kids.append(b"%d 0 R" % page)
        data = zlib.compress(pixels)
        objects[image] = (
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n%s\nendstream"
            % (columns, rows, len(data), data)
        )
        pictures_used = b"/Im1 %d 0 R" % image
        if in_form:
            objects[form] = (
                b"<< /Type /XObject /Subtype /Form /BBox [0 0 %d %d] /Resources << /XObject "
                b"<< %s >> >> /Length %d >>\nstream\n%s\nendstream"
                % (*size, pictures_used, len(draw), draw)
            )
            pictures_used, stream = b"/Fm1 %d 0 R" % form, b"/Fm1 Do\n" + text
        else:
            stream = draw + b"\n" + text
        objects[page] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font "
            b"<< /F1 3 0 R >> /XObject << %s >> >> /Contents %d 0 R >>"
            % (*size, pictures_used, page + 1)
        )
        objects[page + 1] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(kids))
    return from_objects(objects)


def shared_tree_pdf(depth: int) -> bytes:
    """A page tree whose every /Pages node lists the same child twice: 2**depth pages reached
    through depth + 1 objects."""
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    for level in range(depth):
        node, child = 2 + level, 3 + level
        count = 2 ** (depth - level)
        objects[node] = b"<< /Type /Pages /Kids [%d 0 R %d 0 R] /Count %d >>" % (
            child,
            child,
            count,
        )
    objects[2 + depth] = b"<< /Type /Page /MediaBox [0 0 612 792] >>"
    return from_objects(objects)


def from_objects(objects: dict[int, bytes], *, bad_startxref: bool = False) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, objects[number])
    xref = len(out)
    count = len(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % count
    out += b"".join(b"%010d 00000 n \n" % offsets[number] for number in range(1, count))
    startxref = xref + 5 if bad_startxref else xref
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (count, startxref)
    return bytes(out)


def encrypt(data: bytes, algorithm: str = "RC4-128") -> bytes:
    """Encrypt with an empty user password, as owner-restricted documents often are."""
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=io.BytesIO(data))
    writer.encrypt(user_password="", owner_password="owner", algorithm=algorithm)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
