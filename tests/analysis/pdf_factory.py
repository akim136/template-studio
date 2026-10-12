"""Build small synthetic PDFs at test time. No PDF fixture is committed."""


def _escape(text: str) -> bytes:
    return (
        text.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    )


def make_pdf(texts: list[str], *, size=(612, 792), bad_startxref: bool = False) -> bytes:
    """One page per entry, each showing its text in Helvetica. An empty entry is a blank page.

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
        stream = b"BT /F1 12 Tf 72 720 Td (%s) Tj ET" % _escape(text) if text else b""
        objects[page] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (*size, page + 1)
        )
        objects[page + 1] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)

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
