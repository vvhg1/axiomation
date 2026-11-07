"""Minimal PDF writer for the test fixtures.

The reader tests need a PDF with a text layer, and one of them needs an
embedded outline. This writes one with nothing but the standard library, so
no test's input depends on a PDF library.

It covers exactly what the fixtures ask for: page text in one of the standard
fonts, more than one layout line per page, a bookmark tree, and a symbol font
that names its glyphs (its CFF program, where a test embeds one, is built by
the test). Nothing else.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from pathlib import Path

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
FONT_SIZE = 11
MARGIN = 72
LINE_HEIGHT = 24
#: Characters per layout line. Helvetica at 11pt averages about 5.5pt a
#: character in prose, so 80 stays inside the 468pt between the margins; a
#: reader clips what falls outside the page.
LINE_WIDTH = 80

#: A bookmark is `(level, title, page)`, both numbers counted from 1.
Bookmark = tuple[int, str, int]


def write_pdf(
    path: Path,
    pages: Sequence[str],
    *,
    outline: Sequence[Bookmark] = (),
    images: Sequence[int] = (),
    rules: Sequence[int] = (),
    hidden: Sequence[int] = (),
) -> Path:
    """Write a PDF whose pages carry `pages` and whose bookmarks are `outline`.

    A page's text may hold newlines; each one is written as its own layout
    line, so a reader sees the line breaks a real page has, and a line longer
    than `LINE_WIDTH` wraps at a space. Text that would run off the page
    raises rather than being written where no reader sees it. A page index in
    `images` (counted from 1) gets a small raster image drawn on it; one in
    `rules` gets a 1x1 image stretched into a line, as TeX draws a rule; one
    in `hidden` has its text drawn invisible, as a scanner's OCR lays its
    layer over the page's image.
    """
    path.write_bytes(
        _build(list(pages), list(outline), set(images), set(rules), set(hidden))
    )
    return path


def _escape(text: str) -> str:
    for mark, replacement in (("\\", r"\\"), ("(", r"\("), (")", r"\)")):
        text = text.replace(mark, replacement)
    return text


def _lines(text: str) -> list[str]:
    lines = [
        wrapped
        for line in text.split("\n")
        for wrapped in textwrap.wrap(line, LINE_WIDTH) or [""]
    ]
    room = (PAGE_HEIGHT - 2 * MARGIN) // LINE_HEIGHT + 1
    if len(lines) > room:
        raise ValueError(f"a page holds {room} lines; this text needs {len(lines)}")
    return lines


def _content(
    text: str, *, image: bool = False, rule: bool = False, hidden: bool = False
) -> str:
    parts = ["BT", f"/F1 {FONT_SIZE} Tf", *(["3 Tr"] if hidden else [])]
    # PDF coordinates start at the bottom of the page, so the first line is
    # the highest.
    for offset, line in enumerate(_lines(text)):
        baseline = PAGE_HEIGHT - MARGIN - offset * LINE_HEIGHT
        parts.append(f"1 0 0 1 {MARGIN} {baseline} Tm")
        parts.append(f"({_escape(line)}) Tj")
    parts.append("ET")
    if image:
        parts += ["q", "20 0 0 20 72 600 cm", "/Im0 Do", "Q"]
    if rule:
        parts += [
            "q",
            "400 0 0 0.4 72 560 cm",
            "BI /W 1 /H 1 /CS /G /BPC 8 ID",
            "\x00 EI",
            "Q",
        ]
    return "\n".join(parts)


def _image_stream() -> bytes:
    payload = bytes((255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0))
    header = (
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 "
        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 12 >>"
    )
    return header + b"\nstream\n" + payload + b"\nendstream"


def _build(
    pages: list[str],
    outline: list[Bookmark],
    images: set[int],
    rules: set[int],
    hidden: set[int] = frozenset(),
) -> bytes:
    if not pages:
        raise ValueError("a PDF needs at least one page")
    if any(index < 1 or index > len(pages) for index in images | rules):
        raise ValueError("an image page index is outside the document")
    objects: dict[int, bytes] = {}
    next_number = 1

    def allocate() -> int:
        nonlocal next_number
        number = next_number
        next_number += 1
        return number

    def stream(data: str) -> bytes:
        payload = data.encode("latin-1")
        return (
            f"<< /Length {len(payload)} >>\nstream\n".encode("latin-1")
            + payload
            + b"\nendstream"
        )

    catalog = allocate()
    pages_number = allocate()
    outlines = allocate() if outline else 0
    items = [allocate() for _ in outline]
    font = allocate()
    image_numbers = {index: allocate() for index in sorted(images)}
    page_numbers = [allocate() for _ in pages]
    content_numbers = [allocate() for _ in pages]

    catalog_body = f"<< /Type /Catalog /Pages {pages_number} 0 R"
    if outlines:
        catalog_body += f" /Outlines {outlines} 0 R"
    objects[catalog] = (catalog_body + " >>").encode("latin-1")
    kids = " ".join(f"{number} 0 R" for number in page_numbers)
    objects[pages_number] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>"
    ).encode("latin-1")
    objects[font] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for number in image_numbers.values():
        objects[number] = _image_stream()

    for position, (number, content_number, text) in enumerate(
        zip(page_numbers, content_numbers, pages, strict=True), start=1
    ):
        image = position in image_numbers
        objects[content_number] = stream(
            _content(
                text, image=image, rule=position in rules, hidden=position in hidden
            )
        )
        resources = f"<< /Font << /F1 {font} 0 R >>"
        if image:
            resources += f" /XObject << /Im0 {image_numbers[position]} 0 R >>"
        objects[number] = (
            f"<< /Type /Page /Parent {pages_number} 0 R "
            f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources {resources} >> "
            f"/Contents {content_number} 0 R >>"
        ).encode("latin-1")

    if outline:
        objects.update(_outline_objects(outlines, items, outline, page_numbers))

    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in range(1, next_number):
        offsets[number] = len(body)
        body += f"{number} 0 obj\n".encode("latin-1")
        body += objects[number] + b"\nendobj\n"
    start = len(body)
    body += f"xref\n0 {next_number}\n".encode("latin-1")
    body += b"0000000000 65535 f \n"
    for number in range(1, next_number):
        body += f"{offsets[number]:010d} 00000 n \n".encode("latin-1")
    body += (
        f"trailer\n<< /Size {next_number} /Root {catalog} 0 R >>\n"
        f"startxref\n{start}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(body)


def _outline_objects(
    root: int,
    items: list[int],
    outline: list[Bookmark],
    page_numbers: list[int],
) -> dict[int, bytes]:
    """One object per bookmark, chained to its parent and its siblings."""
    parents: list[int] = []
    for index, (level, _title, _page) in enumerate(outline):
        if level <= 1:
            parents.append(root)
            continue
        parents.append(
            next(
                (
                    items[earlier]
                    for earlier in range(index - 1, -1, -1)
                    if outline[earlier][0] == level - 1
                ),
                root,
            )
        )
    objects: dict[int, bytes] = {}
    for index, (_level, title, page) in enumerate(outline):
        siblings = [
            other for other in range(len(outline)) if parents[other] == parents[index]
        ]
        place = siblings.index(index)
        body = (
            f"<< /Title ({_escape(title)}) "
            f"/Parent {parents[index]} 0 R "
            f"/Dest [{page_numbers[page - 1]} 0 R /Fit]"
        )
        if place > 0:
            body += f" /Prev {items[siblings[place - 1]]} 0 R"
        if place + 1 < len(siblings):
            body += f" /Next {items[siblings[place + 1]]} 0 R"
        children = [
            other for other in range(len(outline)) if parents[other] == items[index]
        ]
        if children:
            body += f" /First {items[children[0]]} 0 R"
            body += f" /Last {items[children[-1]]} 0 R"
            body += f" /Count {len(children)}"
        objects[items[index]] = (body + " >>").encode("latin-1")
    top = [index for index in range(len(outline)) if parents[index] == root]
    objects[root] = (
        f"<< /Type /Outlines /First {items[top[0]]} 0 R "
        f"/Last {items[top[-1]]} 0 R /Count {len(outline)} >>"
    ).encode("latin-1")
    return objects


# --- Pages drawn in shapes: what the page class reads ---


def image(x: float, y: float, width: float, height: float) -> str:
    """A raster image drawn at `x`, `y` (its lower left), `width` by
    `height` points."""
    return f"q {width} 0 0 {height} {x} {y} cm /Im0 Do Q"


def text(
    line: str, *, x: float = MARGIN, y: float = PAGE_HEIGHT - MARGIN, invisible=False
) -> str:
    """One line of text at `x`, `y`; `invisible` draws it as a scanner's
    OCR layer does (render mode 3), there for search and never seen."""
    mode = "3 Tr " if invisible else ""
    return f"BT /F1 {FONT_SIZE} Tf {mode}1 0 0 1 {x} {y} Tm ({_escape(line)}) Tj ET"


def prose(lines: Sequence[str], *, invisible: bool = False) -> list[str]:
    """Lines of text down the page from the top margin."""
    return [
        text(line, y=PAGE_HEIGHT - MARGIN - offset * LINE_HEIGHT, invisible=invisible)
        for offset, line in enumerate(lines)
    ]


def full_page() -> str:
    """An image covering the whole page, as a scan's."""
    return image(0, 0, PAGE_WIDTH, PAGE_HEIGHT)


def write_drawn_pdf(path: Path, pages: Sequence[Sequence[str]]) -> Path:
    """A PDF whose pages draw what `pages` says, in that order: `text`,
    `prose`, `image` and `full_page` calls. Every image is one 2x2 raster."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    pages_number = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    picture = add(_image_stream())
    kids = []
    for ops in pages:
        payload = "\n".join(ops).encode("latin-1")
        content = add(
            f"<< /Length {len(payload)} >>\nstream\n".encode("latin-1")
            + payload
            + b"\nendstream"
        )
        kids.append(
            add(
                (
                    f"<< /Type /Page /Parent {pages_number} 0 R "
                    f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                    f"/Resources << /Font << /F1 {font} 0 R >> "
                    f"/XObject << /Im0 {picture} 0 R >> >> "
                    f"/Contents {content} 0 R >>"
                ).encode("latin-1")
            )
        )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_number} 0 R >>".encode(
        "latin-1"
    )
    objects[pages_number - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
        f"/Count {len(kids)} >>"
    ).encode("latin-1")
    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n"
    start = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode("latin-1")
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\n"
        f"startxref\n{start}\n%%EOF\n"
    ).encode("latin-1")
    path.write_bytes(bytes(body))
    return path


# --- A font that names its glyphs: what `pdf.Pdf.text` reads a name from ---

#: A symbol font's name, as a TeX font's would be, which no reader knows.
SYMBOL_FONT = "TestSymbols"


def write_named_glyphs_pdf(
    path: Path,
    drawn: str,
    *,
    differences: dict[int, str] | None = None,
    program: bytes | None = None,
) -> Path:
    """One page drawing `drawn` in Helvetica ("plain ") and then in a symbol
    font with no Unicode map, whose glyph names are `differences` in its
    `/Encoding`, or the embedded CFF `program`'s own encoding."""
    if differences is not None:
        entries = " ".join(
            f"{code} /{name}" for code, name in sorted(differences.items())
        )
        encoding = f"/Encoding << /Type /Encoding /Differences [{entries}] >> "
    else:
        encoding = ""
    descriptor = "/FontDescriptor 7 0 R " if program is not None else ""
    payload = (
        f"BT /F1 {FONT_SIZE} Tf 1 0 0 1 {MARGIN} {PAGE_HEIGHT - MARGIN} Tm (plain ) Tj "
        f"/F2 {FONT_SIZE} Tf ({_escape(drawn)}) Tj ET"
    ).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            "/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>"
        ).encode("latin-1"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{SYMBOL_FONT} "
            f"/FirstChar 0 /LastChar 255 /Widths [{' '.join(['500'] * 256)}] "
            f"{encoding}{descriptor}>>"
        ).encode("latin-1"),
        f"<< /Length {len(payload)} >>\nstream\n".encode("latin-1")
        + payload
        + b"\nendstream",
    ]
    if program is not None:
        objects.append(
            (
                f"<< /Type /FontDescriptor /FontName /{SYMBOL_FONT} /Flags 4 "
                "/FontBBox [0 0 500 700] /ItalicAngle 0 /Ascent 700 /Descent 0 "
                "/CapHeight 700 /StemV 80 /FontFile3 8 0 R >>"
            ).encode("latin-1")
        )
        objects.append(
            f"<< /Subtype /Type1C /Length {len(program)} >>\nstream\n".encode("latin-1")
            + program
            + b"\nendstream"
        )
    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n"
    start = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode("latin-1")
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{start}\n%%EOF\n"
    ).encode("latin-1")
    path.write_bytes(bytes(body))
    return path


def write_clashing_fonts_pdf(
    path: Path,
    runs: Sequence[tuple[str, dict[int, str]]],
    *,
    type3: bool = False,
    in_form: bool = False,
) -> Path:
    """One page drawing each run in a font of its own whose glyph names are
    the run's: Type 1 fonts all called `SYMBOL_FONT`, or Type 3 fonts, which
    have no name, each glyph a blank box; `in_form` draws them in a form
    the page draws, as a plot is. Which font a code is drawn in shows by
    the order of drawing alone."""
    resources, objects = (
        [],
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"",  # the page, written once its fonts are numbered
            b"",  # its content
        ],
    )
    shows = []
    for position, (drawn, differences) in enumerate(runs, start=1):
        entries = " ".join(
            f"{code} /{name}" for code, name in sorted(differences.items())
        )
        encoding = f"/Encoding << /Type /Encoding /Differences [{entries}] >>"
        if type3:
            procs = []
            for name in sorted(set(differences.values())):
                objects.append(b"<< /Length 7 >>\nstream\n500 0 d0\nendstream")
                procs.append(f"/{name} {len(objects)} 0 R")
            low, high = min(differences), max(differences)
            font = (
                "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 500 700] "
                "/FontMatrix [0.001 0 0 0.001 0 0] "
                f"/CharProcs << {' '.join(procs)} >> {encoding} "
                f"/FirstChar {low} /LastChar {high} "
                f"/Widths [{' '.join(['500'] * (high - low + 1))}] >>"
            )
        else:
            font = (
                f"<< /Type /Font /Subtype /Type1 /BaseFont /{SYMBOL_FONT} "
                f"/FirstChar 0 /LastChar 255 /Widths [{' '.join(['500'] * 256)}] "
                f"{encoding} >>"
            )
        objects.append(font.encode("latin-1"))
        resources.append(f"/S{position} {len(objects)} 0 R")
        codes = "".join(f"\\{ord(char):03o}" for char in drawn)
        shows.append(f"/S{position} {FONT_SIZE} Tf ({codes}) Tj")
    payload = (
        f"BT 1 0 0 1 {MARGIN} {PAGE_HEIGHT - MARGIN} Tm " + " ".join(shows) + " ET"
    ).encode("latin-1")
    fonts = f"/Font << {' '.join(resources)} >>"
    if in_form:
        objects.append(
            (
                f"<< /Type /XObject /Subtype /Form /BBox [0 0 {PAGE_WIDTH} "
                f"{PAGE_HEIGHT}] /Resources << {fonts} >> /Length {len(payload)} >>"
                "\nstream\n"
            ).encode("latin-1")
            + payload
            + b"\nendstream"
        )
        payload = b"q /Fm1 Do Q"
        page_resources = f"/XObject << /Fm1 {len(objects)} 0 R >>"
    else:
        page_resources = fonts
    objects[2] = (
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
        f"/Resources << {page_resources} >> /Contents 4 0 R >>"
    ).encode("latin-1")
    objects[3] = (
        f"<< /Length {len(payload)} >>\nstream\n".encode("latin-1")
        + payload
        + b"\nendstream"
    )
    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n"
    start = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode("latin-1")
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{start}\n%%EOF\n"
    ).encode("latin-1")
    path.write_bytes(bytes(body))
    return path
