"""Field-position candidates with exact geometry, read from the PDF itself.

Three mechanical sources: underscore runs in the text layer, drawn horizontal rules, and small
drawn or glyph squares (checkbox shapes). A candidate is a place a field might go, not a field:
the model decides which candidates are fields and may propose fields that have no candidate.

Coordinates are source points with a top-left origin, measured from the page's crop box, which
is the area PDFium renders.
"""

from __future__ import annotations

from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from studio.services.render import _LOCK

MAX_PER_PAGE = 120  # beyond this the page is reported as truncated, never silently cut
BOX_GLYPHS = frozenset("\u2610\u25a1\u25a2\u2751\u2752\uf06f\uf0a8\uf071")  # last 3: Wingdings
RULE_MIN_WIDTH, RULE_MAX_HEIGHT = 36.0, 2.0
BOX_MIN, BOX_MAX, BOX_SKEW = 5.0, 18.0, 3.0
CONTEXT_WIDTH, CONTEXT_BAND = 220.0, 12.0


@dataclass(frozen=True)
class Candidate:
    id: str
    page: int
    kind: str  # "underscore", "rule" or "box"
    x: float
    y: float
    width: float
    height: float
    left_text: str  # text just left of the candidate on the same line
    right_text: str  # text just right of it (checkbox labels usually sit here)
    below_text: str  # text in a band under the candidate (labels such as "Signature")


@dataclass(frozen=True)
class PageCandidates:
    page: int
    candidates: list[Candidate]
    truncated_from: int | None = None  # the full count when MAX_PER_PAGE cut the list
    reason: str = ""  # why candidates could not be read, when they could not


def _clean(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())[:160]


def _underscore_runs(textpage) -> list[tuple[float, float, float, float]]:
    """(left, bottom, right, top) of each run of three or more underscores, in PDF space."""
    runs, current = [], []
    for index in range(textpage.count_chars()):
        if textpage.get_text_range(index, 1) == "_":
            box = textpage.get_charbox(index)
            if current and abs(box[1] - current[-1][1]) > 2:  # a new line breaks the run
                runs.append(current)
                current = []
            current.append(box)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return [
        (run[0][0], min(b[1] for b in run), run[-1][2], max(b[3] for b in run))
        for run in runs
        if len(run) >= 3
    ]


def _glyph_boxes(textpage) -> list[tuple[float, float, float, float]]:
    return [
        textpage.get_charbox(index)
        for index in range(textpage.count_chars())
        if textpage.get_text_range(index, 1) in BOX_GLYPHS
    ]


def _paths(page) -> tuple[list, list]:
    """Horizontal rules and checkbox-sized squares among top-level drawn paths."""
    rules, boxes = [], []
    for obj in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_PATH], max_depth=1):
        left, bottom, right, top = obj.get_bounds()
        width, height = right - left, top - bottom
        if width >= RULE_MIN_WIDTH and height <= RULE_MAX_HEIGHT:
            rules.append((left, bottom, right, top))
        elif BOX_MIN <= width <= BOX_MAX and BOX_MIN <= height <= BOX_MAX:
            if abs(width - height) <= BOX_SKEW:
                boxes.append((left, bottom, right, top))
    return rules, boxes


def _page_candidates(number: int, page) -> PageCandidates:
    if page.get_rotation():
        return PageCandidates(number, [], reason="rotated page: candidates are not read")
    crop_left, _, crop_right, crop_top = page.get_cropbox()
    textpage = page.get_textpage()
    try:
        found = [("underscore", box) for box in _underscore_runs(textpage)]
        rules, boxes = _paths(page)
        found += [("rule", box) for box in rules]
        found += [("box", box) for box in boxes + _glyph_boxes(textpage)]
        found.sort(key=lambda item: (-item[1][3], item[1][0]))  # reading order: top, then left
        out = []
        for kind, (left, bottom, right, top) in found[:MAX_PER_PAGE]:
            left_text = textpage.get_text_bounded(
                max(left - CONTEXT_WIDTH, crop_left), bottom - 2, left, top + CONTEXT_BAND
            )
            right_text = textpage.get_text_bounded(
                right, bottom - 2, min(right + CONTEXT_WIDTH, crop_right), top + CONTEXT_BAND
            )
            below_text = textpage.get_text_bounded(left, bottom - CONTEXT_BAND, right, bottom)
            out.append(
                Candidate(
                    id=f"p{number}-{len(out) + 1}",
                    page=number,
                    kind=kind,
                    x=round(left - crop_left, 2),
                    y=round(crop_top - top, 2),
                    width=round(right - left, 2),
                    height=round(top - bottom, 2),
                    left_text=_clean(left_text),
                    right_text=_clean(right_text),
                    below_text=_clean(below_text),
                )
            )
    finally:
        textpage.close()
    truncated = len(found) if len(found) > MAX_PER_PAGE else None
    return PageCandidates(number, out, truncated)


def extract_candidates(source: bytes) -> list[PageCandidates]:
    """Candidates for every page PDFium can open; a page that fails carries its reason."""
    with _LOCK:
        pdf = pdfium.PdfDocument(source)
        try:
            results = []
            for index in range(len(pdf)):
                try:
                    page = pdf[index]
                    try:
                        results.append(_page_candidates(index + 1, page))
                    finally:
                        page.close()
                except Exception as error:  # one page must not stop the rest
                    reason = f"{type(error).__name__}: {error}"
                    results.append(PageCandidates(index + 1, [], reason=reason))
            return results
        finally:
            pdf.close()
