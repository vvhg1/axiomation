"""The words in a digital page's images.

A digital page keeps its layer, and the layer holds no word a figure draws
as pixels: a plot's legend, a diagram's sentences, a cover's title.
Tesseract reads the rendered page; a word it reads inside an image's box
that the layer lacks, and that spells a word, is text only the image
holds. An image holding enough of them is cropped and read by the declared
OCR, and its text is added after the layer's under a line naming it: the
page's reader stays its layer, and the line is the record that OCR wrote
what follows. A chart the OCR gives nothing for keeps Tesseract's lines
inside its box that hold a word, its legend and axis titles, not its tick
values; so does a figure whose reading has no room in its box, as the
witness holds a page's block (`witness.Seen.overfull`).

A page so tested records it (`OCR_FIGURES_FIELD`). A stored page kept as
its layer without that record is owed the test: one that carries an image
by a read of its work (`run.Reason.FIGURES`), one that carries none by a
payload update, its text untouched.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation.sources import pdf
from axiomation.sources.ingest import layer as layer_module
from axiomation.sources.ingest import witness
from axiomation.sources.ingest.ocr import OcrProvider, Witness, Word

#: The line a figure's text stands under on its page.
FIGURE_LINE = "[text in a figure, read by OCR]"
#: The words an image must hold, its page's layer lacking them, to be read.
#: Counted per image on 45 pages of five digital works: the plots,
#: diagrams and cover whose text the layer lacks held 2 to 7; one word is
#: most often a hyphen split or a single label, and an image of the page's
#: own text held one, so reading it would store that text twice.
FIGURE_WORDS = 2
#: Tesseract's confidence from which a word counts. On the same pages,
#: misreads below it reached the floor (an axis read "loge", a pattern read
#: "WILE GLIA"); no word of a figure's text fell below it.
CONFIDENT = 60


@dataclass(frozen=True, slots=True)
class FigureText:
    """A page's figures' text, each under `FIGURE_LINE`; how many figures
    gave it; and how many readings it left out as having no room in their
    figure's box."""

    text: str = ""
    figures: int = 0
    dropped: int = 0


_WORD_RE = re.compile(r"[^\W\d_]{4,}")
_LINE_BREAK_RE = re.compile(r"-\s*\n\s*")


def words_of(text: str) -> frozenset[str]:
    """A text's words of four letters or more, casefolded, a word broken
    at a line's end joined."""
    return frozenset(
        word.casefold() for word in _WORD_RE.findall(_LINE_BREAK_RE.sub("", text))
    )


def figure_text(
    document: pdf.Pdf,
    index: int,
    layer: str,
    vocabulary: Callable[[], Set[str]],
    *,
    ocr: OcrProvider,
    tesseract: Witness,
    dpi: int,
) -> FigureText:
    """The text of a digital page's figures; empty where none holds text
    its layer lacks.

    A word spells one when it is in the system's word list or among the
    work's own words (`vocabulary`); where the list is absent, the work's
    words alone.
    """
    boxes = document.image_boxes(index, dpi=dpi)
    if not boxes:
        return FigureText()
    words = tesseract.words(document.png(index, dpi=dpi))
    known = layer_module.word_list()
    work = vocabulary()
    spelled = {
        word: found
        for word in words
        if word.confidence >= CONFIDENT
        and (
            found := [
                match
                for match in (w.casefold() for w in _WORD_RE.findall(word.text))
                if match in known or match in work
            ]
        )
    }
    held = words_of(layer)
    counted = {
        word: unheld
        for word, found in spelled.items()
        if (unheld := sum(match not in held for match in found))
    }
    figures = _figures(boxes, counted)
    if not figures:
        return FigureText()
    parts = []
    dropped = 0
    for box, crop in zip(
        figures, document.crops(index, dpi=dpi, boxes=figures), strict=True
    ):
        text = ocr.read(crop).strip()
        if witness.Seen(text, box, dpi).overfull:
            dropped += 1
            text = ""
        text = text or _lines(box, words, spelled.keys())
        if text:
            parts.append(f"{FIGURE_LINE}\n{text}")
    return FigureText("\n\n".join(parts), len(parts), dropped)


def _figures(boxes: Sequence[pdf.Box], counted: dict[Word, int]) -> list[pdf.Box]:
    """The images to read, in reading order: each holding `FIGURE_WORDS` or
    more counted words, the larger first, and one inside a box already
    taken left out, since a figure drawn as layered images holds its text
    once."""
    holding = [
        box
        for box in boxes
        if sum(n for word, n in counted.items() if _inside(word, box)) >= FIGURE_WORDS
    ]
    taken: list[pdf.Box] = []
    for box in sorted(holding, key=_area, reverse=True):
        if not any(_within(box, other) for other in taken):
            taken.append(box)
    return sorted(taken, key=lambda box: (box[1], box[0]))


def _lines(box: pdf.Box, words: Sequence[Word], spelled: Set[Word]) -> str:
    """Tesseract's lines inside a box that hold a word that spells one, read
    with confidence: a legend's or an axis's, not a line of tick values."""
    lines: dict[tuple[int, int, int], list[Word]] = {}
    for word in words:
        if _inside(word, box):
            lines.setdefault(word.line, []).append(word)
    return "\n".join(
        " ".join(word.text for word in line)
        for line in lines.values()
        if any(word in spelled for word in line)
    )


def _inside(word: Word, box: pdf.Box) -> bool:
    x, y = word.centre
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _within(box: pdf.Box, other: pdf.Box) -> bool:
    return (
        other[0] <= box[0]
        and other[1] <= box[1]
        and box[2] <= other[2]
        and box[3] <= other[3]
    )


def _area(box: pdf.Box) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


@dataclass(frozen=True, slots=True)
class FigureAudit:
    """One stored work's layer pages never tested for figures."""

    bib_key: str
    #: the pages that carry an image, tested when the work is read again
    images: tuple[int, ...] = ()
    #: the chunks of the pages that carry none, given the record (0) by a
    #: payload update
    plain: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "images": list(self.images),
            "plain": len(self.plain),
        }


def audit(
    bib_key: str, pdf_path: Path, pages: Sequence[tuple[int, int]]
) -> FigureAudit:
    """Which of a work's untested layer pages carry an image."""
    images: list[int] = []
    plain: list[int] = []
    with pdf.open_pdf(pdf_path) as document:
        for page_index, chunk_index in pages:
            if not 1 <= page_index <= document.page_count:
                continue
            if document.has_images(page_index):
                images.append(page_index)
            else:
                plain.append(chunk_index)
    return FigureAudit(bib_key, tuple(sorted(images)), tuple(sorted(plain)))
