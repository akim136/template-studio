"""Field-position candidates with exact geometry, read from the PDF itself.

Three mechanical sources: underscore runs in the text layer, drawn horizontal rules, and small
drawn or glyph squares (checkbox shapes). A box glyph in a symbol font is recognized by its font
and character code, since such fonts often carry no Unicode mapping. A candidate is a place a field
might go, not a field: the model decides which candidates are fields and may propose fields that
have no candidate.

Coordinates are source points with a top-left origin, measured from the page's visible area (the
crop box within the media box), which is the area PDFium renders. Rotated pages are not read.
Each shape reader stops after SCAN_LIMIT shapes, and the page then says so. What is read is bounded
too: a page with more than CHAR_SCAN_LIMIT characters skips the two character scans, and the
drawn-path scan stops after reading PATH_SCAN_LIMIT objects; the page says so in both cases.
"""

from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from studio.services.render import _LOCK

MAX_PER_PAGE = 120  # beyond this the page is reported as truncated, never silently cut
SCAN_LIMIT = 2000  # shapes of one kind read per page at most
CHAR_SCAN_LIMIT = 100_000  # characters per page the underscore and glyph scans read at most
PATH_SCAN_LIMIT = 50_000  # drawn-path objects per page read at most
BOX_GLYPHS = frozenset("\u2610\u25a1\u25a2\u2751\u2752\uf06f\uf0a8\uf071")  # last 3: Wingdings
# Box codes in symbol fonts, by font family. PDFium reads such a code c as the character c when the
# font has no Unicode map (Wingdings 2's empty box as "£"), or as U+F000 + c when it maps to the
# private use area, as Word does.
SYMBOL_BOXES = {"wingdings": frozenset({0x6F, 0x71, 0xA8}), "wingdings2": frozenset({0xA3})}
SYMBOL_CODES = frozenset().union(*SYMBOL_BOXES.values())
# Names what this module finds; the drafts' frozen hash includes it. Change it whenever what is
# found changes, so a draft started before stops instead of mixing two kinds of candidates.
FINDS = "underscore runs; drawn rules; drawn squares; box glyphs, and symbol-font boxes by font"
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
    reason: str = ""  # why candidates could not be read, or why the scan stopped
    width_pt: float = 0.0  # the visible area as PDFium renders it; 0 when unmeasured
    height_pt: float = 0.0
    rotation: int = 0


def _clean(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())[:160]


def _underscore_runs(textpage, limit: int) -> list[tuple[float, float, float, float]]:
    """(left, bottom, right, top) of each run of three or more underscores, in PDF space; at most
    limit + 1, so the caller can tell that the scan stopped."""
    runs, current = [], []

    def close(run):
        if len(run) >= 3:
            runs.append((run[0][0], min(b[1] for b in run), run[-1][2], max(b[3] for b in run)))

    for index in range(textpage.count_chars()):
        if len(runs) > limit:
            break
        if textpage.get_text_range(index, 1) == "_":
            box = textpage.get_charbox(index)
            if current and abs(box[1] - current[-1][1]) > 2:  # a new line breaks the run
                close(current)
                current = []
            current.append(box)
        elif current:
            close(current)
            current = []
    close(current)
    return runs


def _font_family(textpage, index: int, name) -> str:
    """The character's font name without a subset prefix or style, in lower case and without
    spaces: "ABCDEF+Wingdings2", "Wingdings 2" and "Wingdings2-Regular" all give "wingdings2"."""
    length = pdfium_raw.FPDFText_GetFontInfo(textpage.raw, index, name, len(name), None)
    if not 0 < length <= len(name):  # PDFium writes nothing when the name does not fit
        return ""
    text = name.raw[: length - 1].decode("latin-1").rsplit("+", 1)[-1]
    return re.sub(r"[-,].*", "", text).replace(" ", "").lower()


def _is_box_glyph(textpage, index: int, char: str, name) -> bool:
    if char in BOX_GLYPHS:
        return True
    if len(char) != 1:
        return False
    code = ord(char) - 0xF000 if 0xF000 <= ord(char) <= 0xF0FF else ord(char)
    if code not in SYMBOL_CODES:  # most characters stop here, before the font is read
        return False
    return code in SYMBOL_BOXES.get(_font_family(textpage, index, name), ())


def _glyph_boxes(textpage, limit: int) -> list[tuple[float, float, float, float]]:
    boxes = []
    name = ctypes.create_string_buffer(256)
    for index in range(textpage.count_chars()):
        if len(boxes) > limit:
            break
        if _is_box_glyph(textpage, index, textpage.get_text_range(index, 1), name):
            boxes.append(textpage.get_charbox(index))
    return boxes


def _paths(page, limit: int) -> tuple[list, list, bool]:
    """Horizontal rules and checkbox-sized squares among top-level drawn paths; at most
    limit + 1 of them together. Objects of every kind count toward PATH_SCAN_LIMIT, since
    pypdfium2 walks them all even to filter; the flag is set if the limit was read and more
    remain unread."""
    rules, boxes = [], []
    for read, obj in enumerate(page.get_objects(max_depth=1)):
        if len(rules) + len(boxes) > limit:
            break
        if read == PATH_SCAN_LIMIT:
            return rules, boxes, True
        if obj.type != pdfium_raw.FPDF_PAGEOBJ_PATH:
            continue
        left, bottom, right, top = obj.get_bounds()
        width, height = right - left, top - bottom
        if width >= RULE_MIN_WIDTH and height <= RULE_MAX_HEIGHT:
            rules.append((left, bottom, right, top))
        elif BOX_MIN <= width <= BOX_MAX and BOX_MIN <= height <= BOX_MAX:
            if abs(width - height) <= BOX_SKEW:
                boxes.append((left, bottom, right, top))
    return rules, boxes, False


def _page_candidates(number: int, page) -> PageCandidates:
    width, height = page.get_size()  # displayed: the visible area, after any rotation
    size = {"width_pt": round(width, 2), "height_pt": round(height, 2)}
    if page.get_rotation():
        reason = "rotated page: candidates are not read"
        return PageCandidates(number, [], reason=reason, rotation=page.get_rotation(), **size)
    view_left, view_bottom, view_right, view_top = page.get_bbox()  # crop box within media box
    textpage = page.get_textpage()
    try:
        notes = []
        chars = textpage.count_chars()
        if chars > CHAR_SCAN_LIMIT:
            runs, glyphs = [], []
            notes.append(
                f"character scans skipped: the page has {chars} characters; "
                f"the limit is {CHAR_SCAN_LIMIT}"
            )
        else:
            runs = _underscore_runs(textpage, SCAN_LIMIT)
            glyphs = _glyph_boxes(textpage, SCAN_LIMIT)
        rules, boxes, unread = _paths(page, SCAN_LIMIT)
        if unread:
            notes.append(f"drawn-path scan stopped after reading {PATH_SCAN_LIMIT} objects")
        if max(len(runs), len(rules) + len(boxes), len(glyphs)) > SCAN_LIMIT:
            notes.append(f"scan stopped after {SCAN_LIMIT} shapes of one kind; more exist")
        found = [("underscore", box) for box in runs[:SCAN_LIMIT]]
        found += [("rule", box) for box in rules[:SCAN_LIMIT]]
        found += [("box", box) for box in (boxes[:SCAN_LIMIT] + glyphs[:SCAN_LIMIT])]
        visible = []  # only what is visible, clipped to the visible area
        for kind, (left, bottom, right, top) in found:
            if right > view_left and left < view_right and top > view_bottom and bottom < view_top:
                clipped = (max(left, view_left), max(bottom, view_bottom))
                clipped += (min(right, view_right), min(top, view_top))
                visible.append((kind, clipped))
        found = visible
        found.sort(key=lambda item: (-item[1][3], item[1][0]))  # reading order: top, then left
        out = []
        for kind, (left, bottom, right, top) in found[:MAX_PER_PAGE]:
            left_text = textpage.get_text_bounded(
                max(left - CONTEXT_WIDTH, view_left), bottom - 2, left, top + CONTEXT_BAND
            )
            right_text = textpage.get_text_bounded(
                right, bottom - 2, min(right + CONTEXT_WIDTH, view_right), top + CONTEXT_BAND
            )
            below_text = textpage.get_text_bounded(left, bottom - CONTEXT_BAND, right, bottom)
            out.append(
                Candidate(
                    id=f"p{number}-{len(out) + 1}",
                    page=number,
                    kind=kind,
                    x=round(left - view_left, 2),
                    y=round(view_top - top, 2),
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
    return PageCandidates(number, out, truncated, "; ".join(notes), **size)


def _measured(number: int, page, reason: str) -> PageCandidates:
    width, height = page.get_size()
    size = {"width_pt": round(width, 2), "height_pt": round(height, 2)}
    return PageCandidates(number, [], reason=reason, rotation=page.get_rotation(), **size)


def _one_page(pdf, number: int, wanted: set[int] | None) -> PageCandidates:
    """One page must not stop the rest. A page whose scan fails keeps its measured size, so it
    can still be inspected from its image and text."""
    try:
        page = pdf[number - 1]
    except Exception as error:
        return PageCandidates(number, [], reason=f"{type(error).__name__}: {error}")
    try:
        if wanted is None or number in wanted:
            return _page_candidates(number, page)
        return _measured(number, page, "not requested")
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        try:
            return _measured(number, page, reason)
        except Exception:
            return PageCandidates(number, [], reason=reason)
    finally:
        page.close()


def page_candidates(source: bytes, number: int) -> PageCandidates:
    """The candidates of one page (one-based), as `extract_candidates` reads them, without
    opening the other pages."""
    with _LOCK:
        try:
            pdf = pdfium.PdfDocument(source)
        except Exception as error:
            reason = f"PDFium could not open the document: {type(error).__name__}"
            return PageCandidates(number, [], reason=reason)
        try:
            if not 0 < number <= len(pdf):
                return PageCandidates(number, [], reason="none")
            return _one_page(pdf, number, {number})
        finally:
            pdf.close()


def extract_candidates(source: bytes, pages: list[int] | None = None) -> list[PageCandidates]:
    """Every page PDFium can open is measured; candidates are read only on `pages` (default:
    all). A page that fails carries its reason, and so does every requested page of a document
    PDFium cannot open."""
    wanted = None if pages is None else set(pages)
    with _LOCK:
        try:
            pdf = pdfium.PdfDocument(source)
        except Exception as error:
            reason = f"PDFium could not open the document: {type(error).__name__}"
            return [PageCandidates(number, [], reason=reason) for number in sorted(wanted or ())]
        try:
            results = []
            for index in range(len(pdf)):
                results.append(_one_page(pdf, index + 1, wanted))
            return results
        finally:
            pdf.close()
