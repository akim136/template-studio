"""Draw numbered field boxes over a rendered page, for the self-check (stage B).

The model checks its own first draft by looking at it: each stage A field is outlined in a color
for its type and labelled with its number. Everything drawn comes from stage A and the source;
no reference, baseline or evaluator material is ever drawn.
"""

from __future__ import annotations

from studio.services.render import RgbImage

COLORS = {
    "signature": (220, 0, 0),
    "initials": (230, 110, 0),
    "date_signed": (150, 0, 200),
    "text": (0, 90, 230),
    "text-merge": (0, 160, 160),
    "checkbox": (0, 150, 0),
    "checkbox-merge": (110, 150, 0),
}
DIGITS = {  # 3x5 bitmaps, one string per row
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}
PIXEL = 3  # each font pixel is drawn as a PIXEL x PIXEL square
LINE = 2


def _paint(rows: list[bytearray], x: int, y: int, w: int, h: int, color: tuple[int, int, int]):
    height, width = len(rows), len(rows[0]) // 3
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, width), min(y + h, height)
    if x0 >= x1 or y0 >= y1:
        return
    span = bytes(color) * (x1 - x0)
    for row in rows[y0:y1]:
        row[x0 * 3 : x1 * 3] = span


def _label(rows: list[bytearray], x: int, y: int, text: str, color: tuple[int, int, int]):
    """White plate with the number in `color`, its bottom-left at (x, y)."""
    width = len(text) * 4 * PIXEL + PIXEL
    top = y - 7 * PIXEL
    _paint(rows, x, top, width, 7 * PIXEL, (255, 255, 255))
    for index, char in enumerate(text):
        for dy, line in enumerate(DIGITS[char]):
            for dx, bit in enumerate(line):
                if bit == "1":
                    px = x + PIXEL + (index * 4 + dx) * PIXEL
                    _paint(rows, px, top + PIXEL + dy * PIXEL, PIXEL, PIXEL, color)


def draw_boxes(image: RgbImage, boxes: list[tuple[int, str, float, float, float, float]]):
    """`boxes` holds (number, type, x, y, width, height) in image pixels. Returns a new image."""
    rows = [bytearray(row) for row in image.rows]
    for number, kind, x, y, w, h in boxes:
        color = COLORS.get(kind, (0, 0, 0))
        x, y, w, h = round(x), round(y), max(round(w), 1), max(round(h), 1)
        _paint(rows, x, y, w, LINE, color)
        _paint(rows, x, y + h - LINE, w, LINE, color)
        _paint(rows, x, y, LINE, h, color)
        _paint(rows, x + w - LINE, y, LINE, h, color)
        _label(rows, x, y, str(number), color)
    return RgbImage(image.width, image.height, [bytes(row) for row in rows])
