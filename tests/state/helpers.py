from datetime import UTC, datetime, timedelta

from tests.analysis.pdf_factory import make_pdf

PAGE = b"BT /F1 12 Tf 72 720 Td (Name: ____________) Tj ET"
POLICY = {"countersigner": False, "required": [{"signer": 0, "type": "signature"}]}
PAGES = {
    (0, 1): {"width": 612.0, "height": 792.0, "rotation": 0},
    (0, 2): {"width": 612.0, "height": 792.0, "rotation": 90},
    (0, 3): {"width": None, "height": None, "rotation": 0},
}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def two_page_pdf() -> bytes:
    return make_pdf([PAGE, PAGE])


def field(**overrides: object) -> dict:
    base = {
        "id": "f1",
        "document_index": 0,
        "page": 1,
        "x": 72.0,
        "y": 600.0,
        "width": 200.0,
        "height": 24.0,
        "type": "signature",
        "signer": 0,
        "required": True,
    }
    return {**base, **overrides}
