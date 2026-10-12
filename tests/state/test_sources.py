import hashlib
import io

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from studio.state import sources
from studio.state.store import StorageError
from tests.state.helpers import two_page_pdf


def test_a_sealed_source_records_every_page_under_its_hash(services):
    data = two_page_pdf()
    record = sources.seal(services.store, data)
    assert record["sha256"] == hashlib.sha256(data).hexdigest()
    assert record["inventory"] == "COMPLETE" and record["size"] == len(data)
    assert [(p["page"], p["width"], p["height"]) for p in record["pages"]] == [
        (1, 612.0, 792.0),
        (2, 612.0, 792.0),
    ]
    assert sources.source_bytes(services.store, record["sha256"]) == data
    assert sources.load(services.store, record["sha256"]) == record


def test_sealing_the_same_bytes_again_keeps_the_first_record(services):
    first = sources.seal(services.store, two_page_pdf())
    assert sources.seal(services.store, two_page_pdf()) == first


def test_page_sizes_are_the_visible_area_fields_are_placed_in(services):
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(two_page_pdf())))
    writer.pages[0].cropbox = RectangleObject([50, 100, 450, 700])
    out = io.BytesIO()
    writer.write(out)
    record = sources.seal(services.store, out.getvalue())
    assert (record["pages"][0]["width"], record["pages"][0]["height"]) == (400.0, 600.0)


def test_nothing_is_stored_for_a_file_that_cannot_be_read(services, tmp_path):
    with pytest.raises(sources.SourceError, match="unreadable PDF"):
        sources.seal(services.store, b"%PDF-1.7 not really")
    assert not (tmp_path / "state" / "sources").exists()


def test_an_oversize_source_is_refused_before_it_is_read(services, monkeypatch):
    monkeypatch.setattr(sources, "MAX_SOURCE_BYTES", 10)
    with pytest.raises(sources.SourceError, match="the limit is"):
        sources.seal(services.store, two_page_pdf())


def test_altered_source_bytes_are_detected_on_every_read(services, tmp_path):
    record = sources.seal(services.store, two_page_pdf())
    (tmp_path / "state" / "sources" / f"{record['sha256']}.pdf").write_bytes(b"changed")
    with pytest.raises(StorageError, match="does not match"):
        sources.source_bytes(services.store, record["sha256"])
    with pytest.raises(StorageError, match="does not match"):
        sources.seal(services.store, two_page_pdf())


@pytest.mark.parametrize("name", ["../x", "ABC", "0" * 63])
def test_a_source_is_named_only_by_a_hash(services, name):
    with pytest.raises(sources.SourceError):
        sources.load(services.store, name)
