"""The one module that owns the PDF libraries.

pypdfium2 (over Google's PDFium) reads every PDF this project opens: a page's
text, whether a page carries an image and where, a page rendered as a PNG, and a
document's embedded outline. The open is here alone, the questions are few,
and the library's 0-based page indices and outline levels stop at this edge:
every number this module gives out is counted from 1, as everywhere in the
project. pypdf reads what PDFium does not give out, a font's `/Encoding`, only for a
page whose text PDFium could not map (`glyphs.py`).

The libraries are imported inside the functions, so the core package stays
standard-library-only. PDFium is not thread-safe: a document is open in
one thread at a time (`open_pdf` holds `_PDFIUM`), so a session's reads
and a fetch running in another thread wait on each other.
"""

from __future__ import annotations

import contextlib
import io
import re
import statistics
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

#: Recursion depth for `get_objects`, so an image nested inside a form XObject
#: is still found. 15 is the library's own default, written down so a change
#: to it cannot quietly narrow the image verdict.
IMAGE_DEPTH = 15
#: An image this many times the page's area is an overlay drawn past the
#: page's edges (a LaTeX watermark 48 times the page), not a page image.
OVERLAY_AREA = 4
#: How far a stamp's place may drift between pages, as a share of the
#: page's width and height: an archive's download line moves by a
#: hundredth from page to page.
STAMP_DRIFT = 0.02

#: The version of `Pdf.text`'s reading of a layer, recorded on each page kept
#: as its layer (`contract.LAYER_READER_FIELD`). Raised when `Pdf.text` reads
#: a layer otherwise, so the corpus's stored layers are read against it.
#: 1: a glyph PDFium cannot map named by its font (`glyphs.py`).
#: 2: the font told by the order of drawing where its name cannot tell it;
#: the txfonts' names and a variant's suffix read.
#: 3: MathTime's italic Greek capitals (`Delta1`) read.
LAYER_READER = 3

#: A part of a rendered page: left, top, right, bottom, in pixels.
Box = tuple[int, int, int, int]


class PageKind(StrEnum):
    """What a page is, from what it draws: `SCAN` a picture of the page, the
    work's text on it drawn where nobody sees it; `DIGITAL` any other page."""

    SCAN = "scan"
    DIGITAL = "digital"


@dataclass(frozen=True, slots=True)
class Bookmark:
    """One outline entry. Level and page are counted from 1."""

    level: int
    title: str
    page_index: int


class Pdf:
    """One open PDF. Page indices are 1-based, as everywhere in this project."""

    def __init__(self, document: Any, name: str, path: Path | None = None) -> None:
        self._document = document
        self._name = name
        self._path = path
        #: pypdf's reading of the same file, opened at the first page that
        #: needs a font's `/Encoding`; None when the file cannot be read so
        self._reader: Any = _UNREAD

    @property
    def page_count(self) -> int:
        return len(self._document)

    def text(self, index: int) -> str:
        """One page's text, with its line breaks as the page shows them.

        A character PDFium could not map to Unicode takes the character
        its glyph's name stands for (`glyphs.py`): a TeX font with no
        Unicode map names its glyphs, and PDFium reads only Adobe's names.
        """
        textpage = self._page(index).get_textpage()
        try:
            text = textpage.get_text_bounded()
            named = self._named(index, textpage)
            if named:
                text = _carry(textpage, text, named)
            return _mark_unmapped(_line_breaks(text))
        finally:
            textpage.close()

    def _named(self, index: int, textpage: Any) -> dict[int, str]:
        """Each character PDFium flags as unmapped, by its index on the text
        page, as its glyph's name gives it: the name the PDF's `/Encoding`
        gives the code, else the one the embedded font program does. The
        font is told by its name where one font on the page has it, else
        by the order the page draws its text in (`_drawn`)."""
        import pypdfium2.raw as pdfium_c

        from axiomation.sources import glyphs

        named: dict[int, str] = {}
        encodings: _PageEncodings | None = None
        drawn: dict[int, dict[int, str]] | None = None
        programs: dict[int, dict[int, str]] = {}
        for char in range(textpage.count_chars()):
            if pdfium_c.FPDFText_HasUnicodeMapError(textpage.raw, char) != 1:
                continue
            code = pdfium_c.FPDFText_GetUnicode(textpage.raw, char)
            drawing = pdfium_c.FPDFText_GetTextObject(textpage.raw, char)
            font = pdfium_c.FPDFTextObj_GetFont(drawing)
            if encodings is None:
                encodings = self._page_encodings(index)
            table = encodings.table(_base_name(font))
            if table is None:
                if drawn is None:
                    drawn = self._drawn(index, textpage)
                table = drawn.get(_address(drawing), {})
            name = table.get(code, "")
            if not name:
                # per page: PDFium may reuse a closed page's font handle
                address = _address(font)
                if address not in programs:
                    programs[address] = glyphs.program_names(_font_program(font))
                name = programs[address].get(code, "")
            found = glyphs.character(name) if name else ""
            if found:
                named[char] = found
        return named

    def _page_encodings(self, index: int) -> _PageEncodings:
        """The `/Differences` of each font one page draws with, its forms'
        included, read by pypdf; none where the file cannot be read so."""
        reader = self._read()
        if reader is None:
            return _PageEncodings({}, frozenset())
        try:
            return _page_encodings(reader.pages[index - 1])
        except Exception:  # noqa: BLE001 - a page pypdf cannot read names nothing
            return _PageEncodings({}, frozenset())

    def _drawn(self, index: int, textpage: Any) -> dict[int, dict[int, str]]:
        """Each text object's font's `/Differences`, by the object's handle.

        pypdf reads the page's text-drawing commands in order, a form's
        where the form is drawn, and PDFium makes one text object of each,
        in the same order; so the two line up where they count alike, and
        each text object takes its own font's encoding, whatever its name.
        Empty where they do not, or pypdf cannot read the page."""
        import pypdfium2.raw as pdfium_c

        reader = self._read()
        if reader is None:
            return {}
        try:
            tables = _drawn_differences(reader, reader.pages[index - 1])
        except Exception:  # noqa: BLE001 - a page pypdf cannot read names nothing
            return {}
        objects = textpage.page.get_objects(max_depth=IMAGE_DEPTH)
        try:
            texts = [
                _address(obj.raw)
                for obj in objects
                if obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT
            ]
        finally:
            objects.close()
        if len(texts) != len(tables):
            return {}
        return dict(zip(texts, tables, strict=True))

    def _read(self) -> Any:
        """pypdf's reader of the file, opened at the first page that needs
        it; None where it cannot be read so."""
        if self._reader is _UNREAD:
            self._reader = _open_reader(self._path)
        return self._reader

    def has_images(self, index: int) -> bool:
        """Whether the page carries a raster image with something in it.

        A one-pixel image does not count. TeX drivers draw rules and fraction
        bars as 1x1 inline images, which PDFium reports; counting them would
        mark every page of such a paper as image-bearing.
        """
        import pypdfium2

        objects = self._page(index).get_objects(max_depth=IMAGE_DEPTH)
        try:
            return any(
                isinstance(obj, pypdfium2.PdfImage) and min(obj.get_px_size()) > 1
                for obj in objects
            )
        finally:
            objects.close()

    def image_boxes(self, index: int, *, dpi: int) -> tuple[Box, ...]:
        """Each raster image's box on the page rendered at `dpi`, in pixels
        from its top left, as `has_images` counts the images; an overlay
        drawn past the page's edges is not one."""
        import pypdfium2

        page = self._page(index)
        left, bottom, right, top = page.get_bbox()
        area = (right - left) * (top - bottom)
        scale = dpi / 72
        boxes = []
        objects = page.get_objects(max_depth=IMAGE_DEPTH)
        try:
            for obj in objects:
                if not isinstance(obj, pypdfium2.PdfImage):
                    continue
                x0, y0, x1, y1 = obj.get_bounds()
                if (
                    min(obj.get_px_size()) <= 1
                    or (x1 - x0) * (y1 - y0) > OVERLAY_AREA * area
                ):
                    continue
                boxes.append(
                    (
                        round((max(x0, left) - left) * scale),
                        round((top - min(y1, top)) * scale),
                        round((min(x1, right) - left) * scale),
                        round((top - max(y0, bottom)) * scale),
                    )
                )
        finally:
            objects.close()
        return tuple(box for box in boxes if box[2] > box[0] and box[3] > box[1])

    def crops(self, index: int, *, dpi: int, boxes: Sequence[Box]) -> list[bytes]:
        """Parts of one page rendered at `dpi`, each as PNG bytes; the page
        is rendered once."""
        bitmap = self._page(index).render(scale=dpi / 72)
        try:
            image = bitmap.to_pil()
            try:
                crops = []
                for box in boxes:
                    buffer = io.BytesIO()
                    image.crop(box).save(buffer, "PNG")
                    crops.append(buffer.getvalue())
            finally:
                image.close()
        finally:
            bitmap.close()
        return crops

    def page_kinds(self) -> tuple[PageKind, ...]:
        """Each page's kind, in page order.

        A scan is a page that carries an image and shows a reader none of
        the work's own text: what text it has is invisible (how a scanner's
        OCR lays it over the image), painted before an image that covers
        it, or a stamp. A page whose invisible text outweighs what it shows
        is a scan too: a publisher's line drawn over a scanned page with an
        OCR layer leaves it a scan. A scan carrying an old OCR layer is
        still a scan, which neither a producer field nor a count of
        characters can tell.

        A stamp is a text the document draws at one place on more than
        half its pages (an archive's download line, a running head),
        digits read alike, since an archive numbers its pages in its
        stamp. So the kind is a document's, read whole: a figure's caption
        is the work's own text and keeps its page digital, where a stamp
        does not.
        """
        draws = [self._draws(index) for index in range(1, self.page_count + 1)]
        stamped = _stamps(draws)
        kinds = []
        for number, page in enumerate(draws):
            shown = sum(
                len(text)
                for place, (text, _, _) in enumerate(page.shown)
                if (number, place) not in stamped
            )
            scan = page.image and (shown == 0 or page.invisible > shown)
            kinds.append(PageKind.SCAN if scan else PageKind.DIGITAL)
        return tuple(kinds)

    def _draws(self, index: int) -> _Draws:
        """What one page draws, as `page_kinds` reads it."""
        import pypdfium2
        import pypdfium2.raw as pdfium_c

        page = self._page(index)
        left, bottom, right, top = page.get_bbox()
        width, height = max(right - left, 1e-9), max(top - bottom, 1e-9)
        images: list[tuple[float, float, float, float]] = []
        #: each visible text: the images drawn before it, its centre, its text
        texts: list[tuple[int, float, float, str]] = []
        invisible = 0
        textpage = page.get_textpage()
        objects = page.get_objects(max_depth=IMAGE_DEPTH)
        try:
            for obj in objects:
                if isinstance(obj, pypdfium2.PdfImage):
                    x0, y0, x1, y1 = obj.get_bounds()
                    overlay = (x1 - x0) * (y1 - y0) > OVERLAY_AREA * width * height
                    if min(obj.get_px_size()) > 1 and not overlay:
                        images.append((x0, y0, x1, y1))
                elif obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT:
                    text = _text_of(obj, textpage)
                    mode = pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw)
                    if mode in _INVISIBLE_TEXT:
                        invisible += len(text)
                    elif text:
                        x0, y0, x1, y1 = obj.get_bounds()
                        texts.append((len(images), (x0 + x1) / 2, (y0 + y1) / 2, text))
        finally:
            objects.close()
            textpage.close()
        shown = tuple(
            (
                _STAMP_DIGIT_RE.sub("0", " ".join(text.split())),
                (x - left) / width,
                (y - bottom) / height,
            )
            for drawn_after, x, y, text in texts
            if not any(
                x0 <= x <= x1 and y0 <= y <= y1
                for x0, y0, x1, y1 in images[drawn_after:]
            )
        )
        return _Draws(image=bool(images), invisible=invisible, shown=shown)

    def png(self, index: int, *, dpi: int) -> bytes:
        """One page as PNG bytes, rendered at `dpi`."""
        bitmap = self._page(index).render(scale=dpi / 72)
        try:
            buffer = io.BytesIO()
            image = bitmap.to_pil()
            try:
                image.save(buffer, "PNG")
            finally:
                image.close()
        finally:
            bitmap.close()
        return buffer.getvalue()

    def outline(self) -> tuple[Bookmark, ...]:
        """The embedded outline, 1-based. A bookmark with no target is dropped."""
        entries: list[Bookmark] = []
        for bookmark in self._document.get_toc():
            destination = bookmark.get_dest()
            page = None if destination is None else destination.get_index()
            if page is None:
                continue
            entries.append(
                Bookmark(
                    level=bookmark.level + 1,
                    title=bookmark.get_title(),
                    page_index=page + 1,
                )
            )
        return tuple(entries)

    def _page(self, index: int) -> Any:
        if not 1 <= index <= self.page_count:
            raise ValueError(
                f"{self._name} has {self.page_count} pages; there is no page {index}"
            )
        return self._document[index - 1]


#: A reader not opened yet (`Pdf._reader`).
_UNREAD = object()
#: Text render modes nobody sees: 3 draws nothing, 7 only clips.
_INVISIBLE_TEXT = frozenset({3, 7})
_STAMP_DIGIT_RE = re.compile(r"\d")


@dataclass(frozen=True, slots=True)
class _Draws:
    """What one page draws: whether an image, the characters of invisible
    text, and each visible text no later image covers, its whitespace
    collapsed and its digits read as 0, with its centre as shares of the
    page's width and height."""

    image: bool
    invisible: int
    shown: tuple[tuple[str, float, float], ...]


def _stamps(pages: list[_Draws]) -> set[tuple[int, int]]:
    """Each shown text that is a stamp, as (page, its place in `shown`): a
    text at one place, within `STAMP_DRIFT` of its median place, on more
    than half the pages and two at least."""
    floor = max(2, len(pages) // 2 + 1)
    seen: dict[str, list[tuple[int, int, float, float]]] = {}
    for number, page in enumerate(pages):
        for place, (text, x, y) in enumerate(page.shown):
            seen.setdefault(text, []).append((number, place, x, y))
    stamped: set[tuple[int, int]] = set()
    for drawn in seen.values():
        if len({number for number, _, _, _ in drawn}) < floor:
            continue
        mx = statistics.median(x for _, _, x, _ in drawn)
        my = statistics.median(y for _, _, _, y in drawn)
        near = [
            (number, place)
            for number, place, x, y in drawn
            if abs(x - mx) <= STAMP_DRIFT and abs(y - my) <= STAMP_DRIFT
        ]
        if len({number for number, _ in near}) >= floor:
            stamped.update(near)
    return stamped


def _text_of(obj: Any, textpage: Any) -> str:
    """The text one text object draws, its ends trimmed."""
    import ctypes

    import pypdfium2.raw as pdfium_c

    size = pdfium_c.FPDFTextObj_GetText(obj.raw, textpage.raw, None, 0)
    buffer = ctypes.create_string_buffer(size)
    pdfium_c.FPDFTextObj_GetText(
        obj.raw,
        textpage.raw,
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort)),
        size,
    )
    return buffer.raw.decode("utf-16-le", "ignore").replace("\x00", "").strip()


@dataclass(frozen=True, slots=True)
class _PageEncodings:
    """The glyph names one page's fonts give their codes in the PDF: each
    font's `/Differences` by its base name, its subset tag dropped, and the
    names two of its fonts share with encodings that differ."""

    fonts: dict[str, dict[int, str]]
    clashed: frozenset[str]

    def table(self, base: str) -> dict[int, str] | None:
        """The `/Differences` of the font PDFium calls `base`, empty where
        the page has no such font; None where the name cannot tell which
        font it is: a Type 3 font may have none, and two fonts may share
        one. PDFium may keep a subset tag in a base name, its `+` dropped."""
        if not base:
            return None
        base = _SUBSET_RE.sub("", base)
        known = [*self.fonts, *self.clashed]
        name = base if base in known else None
        if name is None:
            name = next((n for n in known if n and base.endswith(n)), None)
        if name in self.clashed:
            return None
        return self.fonts.get(name, {}) if name is not None else {}


#: A subset font's tag before its name: `ABCDEF+CMSY10`.
_SUBSET_RE = re.compile(r"^[^+]*\+")


def _open_reader(path: Path | None) -> Any:
    """pypdf's reader of the file, or None where it cannot read it."""
    if path is None:
        return None
    import logging

    from pypdf import PdfReader

    # pypdf logs each repair it makes to a damaged file; what the ingest
    # reports is its own
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        return PdfReader(path)
    except Exception:  # noqa: BLE001 - a file pypdf cannot read names nothing
        return None


def _page_encodings(page: Any) -> _PageEncodings:
    """Each font's `/Differences` on one pypdf page and in its forms; a base
    name drawn under two that disagree is a clash."""
    fonts: dict[str, dict[int, str]] = {}
    clashed: set[str] = set()

    def walk(resources: Any, depth: int) -> None:
        if resources is None or depth > IMAGE_DEPTH:
            return
        resources = resources.get_object()
        for font in (resources.get("/Font") or {}).values():
            font = font.get_object()
            table = _differences(font.get("/Encoding"))
            name = _SUBSET_RE.sub("", str(font.get("/BaseFont", ""))[1:])
            if name in fonts and fonts[name] != table:
                clashed.add(name)
            fonts[name] = table
        for xobject in (resources.get("/XObject") or {}).values():
            xobject = xobject.get_object()
            if xobject.get("/Subtype") == "/Form":
                walk(xobject.get("/Resources"), depth + 1)

    walk(page.get("/Resources"), 0)
    named = {name: table for name, table in fonts.items() if name not in clashed}
    return _PageEncodings(named, frozenset(clashed))


#: The operators that draw text; PDFium makes one text object of each.
_SHOW_OPERATORS = frozenset({b"Tj", b"TJ", b"'", b'"'})


def _drawn_differences(reader: Any, page: Any) -> list[dict[int, str]]:
    """The `/Differences` of the font each text-drawing command of a pypdf
    page draws in, in drawing order: a form's commands where the form is
    drawn, the font set before it carried in, as the graphics state is;
    `q` and `Q` save and restore it."""
    from pypdf.generic import ContentStream

    tables: list[dict[int, str]] = []
    known: dict[int, dict[int, str]] = {}

    def table_of(font: Any) -> dict[int, str]:
        if font is None:
            return {}
        if id(font) not in known:
            known[id(font)] = _differences(font.get("/Encoding"))
        return known[id(font)]

    def walk(content: Any, resources: Any, font: Any, depth: int) -> None:
        resources = _resolved(resources) or {}
        fonts = _resolved(resources.get("/Font")) or {}
        xobjects = _resolved(resources.get("/XObject")) or {}
        saved: list[Any] = []
        for operands, operator in ContentStream(content, reader).operations:
            if operator == b"q":
                saved.append(font)
            elif operator == b"Q" and saved:
                font = saved.pop()
            elif operator == b"Tf":
                font = _resolved(fonts[operands[0]])
            elif operator in _SHOW_OPERATORS:
                tables.append(table_of(font))
            elif operator == b"Do" and depth < IMAGE_DEPTH:
                xobject = _resolved(xobjects[operands[0]])
                if xobject.get("/Subtype") == "/Form":
                    inner = xobject.get("/Resources") or resources
                    walk(xobject, inner, font, depth + 1)

    contents = page.get_contents()
    if contents is not None:
        walk(contents, page.get("/Resources"), None, 0)
    return tables


def _resolved(value: Any) -> Any:
    return value.get_object() if value is not None else None


def _differences(encoding: Any) -> dict[int, str]:
    """An `/Encoding` dictionary's `/Differences`, each code's name."""
    encoding = encoding.get_object() if encoding is not None else None
    if not hasattr(encoding, "get") or "/Differences" not in encoding:
        return {}
    table: dict[int, str] = {}
    code = 0
    for item in encoding["/Differences"]:
        item = item.get_object()
        if isinstance(item, int):
            code = item
        else:
            table[code] = str(item)[1:]
            code += 1
    return table


def _base_name(font: Any) -> str:
    import ctypes

    import pypdfium2.raw as pdfium_c

    size = pdfium_c.FPDFFont_GetBaseFontName(font, None, 0)
    buffer = ctypes.create_string_buffer(max(size, 1))
    pdfium_c.FPDFFont_GetBaseFontName(font, buffer, size)
    return buffer.value.decode("latin-1")


def _address(font: Any) -> int:
    import ctypes

    return ctypes.cast(font, ctypes.c_void_p).value or 0


def _font_program(font: Any) -> bytes:
    """The font's embedded program, empty where it has none."""
    import ctypes

    import pypdfium2.raw as pdfium_c

    size = ctypes.c_size_t()
    if not pdfium_c.FPDFFont_GetFontData(font, None, 0, ctypes.byref(size)):
        return b""
    data = (ctypes.c_uint8 * size.value)()
    pdfium_c.FPDFFont_GetFontData(font, data, size.value, ctypes.byref(size))
    return bytes(data)


def _carry(textpage: Any, bounded: str, named: dict[int, str]) -> str:
    """The page's bounded text with each named character in place
    (`_placed`), the text page's characters read one by one."""
    import pypdfium2.raw as pdfium_c

    units = [
        pdfium_c.FPDFText_GetUnicode(textpage.raw, char)
        for char in range(textpage.count_chars())
    ]
    return _placed(units, bounded, named)


def _placed(units: Sequence[int], bounded: str, named: dict[int, str]) -> str:
    """`bounded` with each named character in place.

    `units` are the text page's characters, which `bounded` writes in
    their order with some of their line breaks left out: each takes the
    next place in `bounded` that holds it. Where the line-up does not
    reach the end of `bounded`, no name is placed. A character past the
    basic plane (a math italic `𝒂`) is two units, its surrogate halves,
    and one character of `bounded`."""
    written = list(bounded)
    at = char = 0
    while char < len(units) and at < len(written):
        unit, width = units[char], 1
        if 0xD800 <= unit < 0xDC00 and char + 1 < len(units):
            low = units[char + 1]
            if 0xDC00 <= low < 0xE000:
                unit = 0x10000 + ((unit - 0xD800) << 10) + (low - 0xDC00)
                width = 2
        if chr(unit) == bounded[at]:
            if char in named:
                written[at] = named[char]
            at += 1
        char += width
    # a line-up that ends short has gone astray, and a name it placed may
    # stand on the wrong letter: PDFium's text is kept as it wrote it
    return "".join(written) if at == len(written) else bounded


#: What PDFium puts in place of a hyphen that ends a line, and of the line
#: break after it: `Geo-` then `metrie` comes back as `Geo\x02metrie`.
HYPHEN_BREAK = "\x02"
#: Letters or digits on both sides: a word broken over a line. Elsewhere the
#: same code is a glyph with no Unicode, such as a large bracket in a TeX
#: math font.
_HYPHEN_BREAK_RE = re.compile(rf"(?<=[^\W_]){HYPHEN_BREAK}(?=[^\W_])")
#: What stands in for a character the library could not map to Unicode.
UNMAPPED = "\ufffd"
#: Control characters other than tab and newline. PDFium returns a glyph's
#: font code when its font has no Unicode map (`\x0f` for a TeX `ϵ`).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _line_breaks(text: str) -> str:
    """PDFium's line breaks as the project writes them.

    A layout line ends in CRLF, and becomes LF. A word broken over a line
    ends in `HYPHEN_BREAK`, and becomes the hyphen and the break, which is
    what `read.text` rejoins a broken word from; left as it is, a quotation
    across that break never matches.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _HYPHEN_BREAK_RE.sub("-\n", text)


def _mark_unmapped(text: str) -> str:
    """Every control character left becomes `UNMAPPED`.

    A control character does not show in a terminal, so `\x0funif-alg`
    reads as `unif-alg` and could be quoted as the source's text. The mark
    shows that a character is missing, and that the page image has it.
    """
    return _CONTROL_RE.sub(UNMAPPED, text)


#: Held while a document is open: PDFium is not thread-safe. Reentrant, so
#: a thread may open a second document inside the first.
_PDFIUM = threading.RLock()


@contextlib.contextmanager
def open_pdf(path: Path) -> Iterator[Pdf]:
    """Open one PDF and close it however the block ends, no other thread
    in PDFium meanwhile."""
    import pypdfium2

    with _PDFIUM:
        document = pypdfium2.PdfDocument(str(path))
        try:
            yield Pdf(document, name=str(path), path=path)
        finally:
            document.close()


def page_png(path: Path, index: int, *, dpi: int) -> bytes:
    """One page of a PDF as PNG bytes. Page numbers are 1-based."""
    with open_pdf(path) as document:
        return document.png(index, dpi=dpi)


def outline(path: Path) -> tuple[Bookmark, ...]:
    """A PDF's embedded outline, with the library's indices converted to 1."""
    with open_pdf(path) as document:
        return document.outline()
