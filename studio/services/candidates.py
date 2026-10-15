"""Field-position candidates with exact geometry, read from the PDF itself.

Three mechanical sources: underscore runs in the text layer, drawn horizontal rules, and small
drawn or glyph squares (checkbox shapes). A candidate is a place a field might go, not a field:
the model decides which candidates are fields and may propose fields that have no candidate.

A scanned page has no drawn rules or squares, only a picture. A page whose pictures cover at least
IMAGE_COVERAGE of its visible area, and which shows no drawn rule, drawn square or glyph square, is
rendered in grey and its lines and squares are found in the pixels (`raster`), held to the same
sizes. They become rules and boxes like the drawn ones, marked `from_image`; a line found where the
text layer already has an underscore run is left to the underscore. They are numbered after the
page's other candidates, so an id given before the image scan existed keeps its meaning.

Coordinates are source points with a top-left origin, measured from the page's visible area (the
crop box within the media box), which is the area PDFium renders. Rotated pages are not read.
Each shape reader stops after SCAN_LIMIT shapes, and the page then says so. What is read is bounded
too: a page with more than CHAR_SCAN_LIMIT characters skips the two character scans, and the
drawn-path scan stops after reading PATH_SCAN_LIMIT objects; the page says so in both cases.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from studio.services import raster
from studio.services.render import _LOCK, _render

MAX_PER_PAGE = 120  # beyond this the page is reported as truncated, never silently cut
SCAN_LIMIT = 2000  # shapes of one kind read per page at most
CHAR_SCAN_LIMIT = 100_000  # characters per page the underscore and glyph scans read at most
PATH_SCAN_LIMIT = 50_000  # drawn-path objects per page read at most
BOX_GLYPHS = frozenset("\u2610\u25a1\u25a2\u2751\u2752\uf06f\uf0a8\uf071")  # last 3: Wingdings
RULE_MIN_WIDTH, RULE_MAX_HEIGHT = 36.0, 2.0
BOX_MIN, BOX_MAX, BOX_SKEW = 5.0, 18.0, 3.0
CONTEXT_WIDTH, CONTEXT_BAND = 220.0, 12.0
IMAGE_COVERAGE = 0.5  # share of the visible area pictures must cover for the image scan
IMAGE_SCALE = 3.0  # pixels per point for the image scan
IMAGE_MAX_PIXELS = 8_000_000  # a larger page is scanned at a lower scale
IMAGE_MAX_SIDE = 20_000  # pixels on either side, so rounding each side up stays near the budget
IMAGE_MIN_SCALE = 1.5  # below this a 1-point line is under 2 pixels, and the page is not scanned
UNDERSCORE_MATCH = 3.0  # points between a line found in the image and an underscore run


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
    from_image: bool = False  # found in the pixels of a scanned page, not among its objects


@dataclass(frozen=True)
class PageCandidates:
    page: int
    candidates: list[Candidate]
    truncated_from: int | None = None  # the full count when MAX_PER_PAGE cut the list
    reason: str = ""  # why candidates could not be read, why the scan stopped, or what the
    # image scan of a scanned page found
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


def _glyph_boxes(textpage, limit: int) -> list[tuple[float, float, float, float]]:
    boxes = []
    for index in range(textpage.count_chars()):
        if len(boxes) > limit:
            break
        if textpage.get_text_range(index, 1) in BOX_GLYPHS:
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


def _area_within(bounds, view: tuple[float, float, float, float]) -> float:
    left, bottom, right, top = bounds
    view_left, view_bottom, view_right, view_top = view
    wide = min(right, view_right) - max(left, view_left)
    tall = min(top, view_top) - max(bottom, view_bottom)
    return max(wide, 0.0) * max(tall, 0.0)


def _picture_share(page, view: tuple[float, float, float, float]) -> tuple[float, bool]:
    """Share of the visible area under pictures: each top-level image, and each image a top-level
    form draws, by its own bounds on the page (overlaps are not removed; at most 1). A form's
    other content does not count. Reads at most PATH_SCAN_LIMIT objects; the flag is set if more
    remain unread."""
    view_left, view_bottom, view_right, view_top = view
    area = (view_right - view_left) * (view_top - view_bottom)
    covered, form, stopped = 0.0, None, False
    for read, obj in enumerate(page.get_objects(max_depth=2)):
        if read == PATH_SCAN_LIMIT:
            stopped = True
            break
        if obj.level == 0:
            form = obj.get_matrix() if obj.type == pdfium_raw.FPDF_PAGEOBJ_FORM else None
            if obj.type == pdfium_raw.FPDF_PAGEOBJ_IMAGE:
                covered += _area_within(obj.get_bounds(), view)
        elif obj.type == pdfium_raw.FPDF_PAGEOBJ_IMAGE and form is not None:
            # A form's objects report bounds in the form's space; its matrix places them.
            covered += _area_within(form.on_rect(*obj.get_bounds()), view)
    return (min(covered / area, 1.0) if area > 0 else 0.0), stopped


def _image_scale(width: float, height: float) -> float | None:
    """Pixels per point for the image scan, or None below IMAGE_MIN_SCALE. PDFium rounds each side
    up, so the bitmap holds at most IMAGE_MAX_PIXELS and one more row and column."""
    if width <= 0 or height <= 0:
        return None
    budget = (IMAGE_MAX_PIXELS / (width * height)) ** 0.5
    scale = min(IMAGE_SCALE, budget, IMAGE_MAX_SIDE / max(width, height))
    return scale if scale >= IMAGE_MIN_SCALE else None


def _apart_from(runs: list, lines: list) -> list:
    """The lines no underscore run already gives: a run gives a line when their bottoms are within
    UNDERSCORE_MATCH and they overlap by half the shorter. Runs are looked up by their bottom."""
    runs = sorted(runs, key=lambda run: run[1])
    bottoms = [run[1] for run in runs]
    kept = []
    for line in lines:
        left, bottom, right, _ = line
        first = bisect.bisect_left(bottoms, bottom - UNDERSCORE_MATCH)
        last = bisect.bisect_right(bottoms, bottom + UNDERSCORE_MATCH)
        if not any(
            min(right, run_right) - max(left, run_left)
            >= 0.5 * min(right - left, run_right - run_left)
            for run_left, _, run_right, _ in runs[first:last]
        ):
            kept.append(line)
    return kept


def _image_shapes(page, view, width: float, height: float, runs: list) -> tuple[list, list, str]:
    """Rules and boxes found in the page's grey pixels, in PDF coordinates like the drawn ones,
    and a note on what the scan found. Lines an underscore run already gives are dropped."""
    scale = _image_scale(width, height)
    if scale is None:
        return [], [], "the page is too large for the image scan"
    rows, wide, tall, channels = _render(page, scale, grayscale=True)
    if channels != 1:
        return [], [], f"the image scan needs grey pixels; PDFium gave {channels} channels"
    shapes = raster.find(
        rows,
        wide,
        tall,
        scale,
        line_min=RULE_MIN_WIDTH,
        box_min=BOX_MIN,
        box_max=BOX_MAX,
        box_skew=BOX_SKEW,
    )
    view_left, _, _, view_top = view

    def placed(rect):  # top-left points of the visible area to (left, bottom, right, top)
        left, top, right, bottom = rect
        return (view_left + left, view_top - bottom, view_left + right, view_top - top)

    lines = _apart_from(runs, [placed(line) for line in shapes.lines])
    boxes = [placed(box) for box in shapes.boxes]
    note = f"page image scanned: {len(lines)} lines, {len(boxes)} boxes"
    return lines, boxes, "; ".join(filter(None, [note, shapes.note]))


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
        found = [("underscore", box, False) for box in runs[:SCAN_LIMIT]]
        found += [("rule", box, False) for box in rules[:SCAN_LIMIT]]
        found += [("box", box, False) for box in (boxes[:SCAN_LIMIT] + glyphs[:SCAN_LIMIT])]
        view = (view_left, view_bottom, view_right, view_top)

        def shown(rect) -> bool:
            left, bottom, right, top = rect
            return (
                right > view_left and left < view_right and top > view_bottom and bottom < view_top
            )

        if not (unread or any(shown(rect) for rect in rules + boxes + glyphs)):
            try:  # the picture check and the scan: the page keeps what its text layer gave
                share, stopped = _picture_share(page, view)
                if share >= IMAGE_COVERAGE:
                    lines, squares, note = _image_shapes(page, view, width, height, runs)
                else:
                    lines, squares, note = [], [], ""
                    if stopped:
                        note = f"picture check stopped after reading {PATH_SCAN_LIMIT} objects"
            except Exception as error:
                lines, squares, note = [], [], f"image scan failed: {type(error).__name__}"
            found += [("rule", line, True) for line in lines]
            found += [("box", square, True) for square in squares]
            if note:
                notes.append(note)
        visible = []  # only what is visible, clipped to the visible area
        for kind, (left, bottom, right, top), from_image in found:
            if shown((left, bottom, right, top)):
                clipped = (max(left, view_left), max(bottom, view_bottom))
                clipped += (min(right, view_right), min(top, view_top))
                visible.append((kind, clipped, from_image))
        found = visible
        # Reading order (top, then left), with what the image gave after the rest.
        found.sort(key=lambda item: (item[2], -item[1][3], item[1][0]))
        out = []
        for kind, (left, bottom, right, top), from_image in found[:MAX_PER_PAGE]:
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
                    from_image=from_image,
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
