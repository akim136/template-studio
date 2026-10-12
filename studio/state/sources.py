"""Sealed sources: PDF bytes stored once under their SHA-256, with the page inventory that fields
are checked against.

Nothing is stored for a file the analysis service cannot read. A sealed source is written only if
absent, and its bytes are re-hashed whenever they are read back, so a template's source identity
cannot drift. Page sizes are the visible area as PDFium renders it, the frame fields are placed in.
A page's rotation is recorded only when pypdf and PDFium both read it and agree; otherwise it is
unknown (None), and no field can be placed on that page; the page is flagged, without making the
inventory incomplete.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict

from studio.services.analysis import (
    MAX_SOURCE_BYTES,
    AnalysisError,
    PageFlag,
    analyze,
    oversize_message,
)
from studio.services.candidates import extract_candidates
from studio.state.store import ObjectStore, PreconditionFailed, StorageError, canonical

SHA256 = re.compile(r"[0-9a-f]{64}")
UNKNOWN_ROTATION = "the page's rotation could not be confirmed, so no field can be placed on it"


class SourceError(ValueError):
    """The upload is not a source that can be sealed."""


def _keys(sha256: str) -> tuple[str, str]:
    if not SHA256.fullmatch(sha256):
        raise SourceError("a source is named by its lowercase hex SHA-256")
    return f"sources/{sha256}.pdf", f"sources/{sha256}.json"


def seal(store: ObjectStore, data: bytes) -> dict:
    if len(data) > MAX_SOURCE_BYTES:
        raise SourceError(oversize_message(len(data)))
    try:
        result = analyze(data)
    except AnalysisError as error:
        raise SourceError(str(error)) from None
    measured = {page.page: page for page in extract_candidates(data, pages=[])}
    pages = []
    for page in result.pages:
        visible = measured.get(page.page)
        size = visible and visible.width_pt and visible.height_pt  # 0 when PDFium could not
        rendered_rotation = visible.rotation if size else None
        pages.append(
            {
                "document_index": 0,
                "page": page.page,
                "width": visible.width_pt if size else None,
                "height": visible.height_pt if size else None,
                "rotation": page.rotation if page.rotation == rendered_rotation else None,
            }
        )
    unknown = [PageFlag(p["page"], UNKNOWN_ROTATION, False) for p in pages if p["rotation"] is None]
    record = {
        "sha256": result.source_sha256,
        "size": len(data),
        "inventory": result.inventory,
        "flags": [asdict(flag) for flag in [*result.flags, *unknown]],
        "pages": pages,
    }
    pdf_key, record_key = _keys(result.source_sha256)
    try:
        store.put_new(pdf_key, data)
    except PreconditionFailed:
        source_bytes(store, result.source_sha256)  # already sealed: check it is intact
    try:
        store.put_new(record_key, canonical(record))
    except PreconditionFailed:
        return load(store, result.source_sha256)  # the first record sealed stays the record
    return record


def load(store: ObjectStore, sha256: str) -> dict | None:
    stored = store.get(_keys(sha256)[1])
    return None if stored is None else json.loads(stored.data)


def source_bytes(store: ObjectStore, sha256: str) -> bytes:
    stored = store.get(_keys(sha256)[0])
    if stored is None:
        raise StorageError(f"the sealed source {sha256[:12]} is missing")
    if hashlib.sha256(stored.data).hexdigest() != sha256:
        raise StorageError(f"the sealed source {sha256[:12]} does not match its hash")
    return stored.data


def page_index(record: dict) -> dict[tuple[int, int], dict]:
    return {(page["document_index"], page["page"]): page for page in record["pages"]}
