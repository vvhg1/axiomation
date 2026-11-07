"""PDF text, each page read by what serves it.

The OCR provider is a parameter rather than a module constant, so a test paper
and the library can be built from one process. And the text source is recorded
per page, not per document: a scan whose OCR came back empty is `failed` on
that page alone, which is what lets the reader tell "this page is unusable"
from "this paper is unusable".

This module decides which pages need OCR and renders them; `ocr.py` decides
how one image is read. Which pages: a scan's (`pdf.PageKind`, read from what
the page draws, so a scan carrying an old OCR layer is read again), a
digital page that carries an image and no text, and a digital page whose
layer spells no words. A page a declared OCR service read is held against
its witnesses, Tesseract and the page's old layer, and read again where
they find a fault (`witness.py`), each block against its own box too where
the service gives boxes. Under such a service, a digital page kept as its
layer gains the text of its figures (`figures.py`).

The service reads a scan's next page while Tesseract witnesses this one:
the two run on different hardware. Tesseract's readings of one page (the
page whole, its words, each block's box) run at once, each its own process.
"""

from __future__ import annotations

import functools
import io
from collections.abc import Callable, Mapping, Set
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation.sources import pdf
from axiomation.sources.contract import TextSource
from axiomation.sources.ingest import figures, witness
from axiomation.sources.ingest import layer as layer_module
from axiomation.sources.ingest.ocr import Block, BlockReader, OcrProvider, Witness

OCR_DPI = 300
#: The margin a block's box is cropped with for Tesseract, in pixels at
#: `OCR_DPI`: a service's box may sit tight on its print.
BOX_MARGIN = 6
#: Tesseract's readings of one page run at once: one process each, on as
#: many cores.
TESSERACT_AT_ONCE = 4


@dataclass(frozen=True, slots=True)
class PageText:
    """One page's text and where it came from."""

    #: Where the page sits in the PDF, counted from 1. Not the number the
    #: source prints on it: in a book the two differ by its front matter.
    page_index: int
    text: str
    source: TextSource
    kind: pdf.PageKind = pdf.PageKind.DIGITAL
    #: the findings the witness left on the page, 0 where it settled them;
    #: None where no witness ran (a layer, Tesseract's own reading)
    disputed: int | None = None
    #: the readings the OCR gave the page, more than one where the witness
    #: found a fault in the first
    readings: int = 1
    #: the figures whose text OCR added after the layer, 0 for none; None
    #: where no test ran (a page OCR read, the Tesseract backend)
    figures: int | None = None
    #: the blocks the witness dropped as having no room in their boxes (on
    #: a layer page, the figures' readings), and those a reading of the
    #: block alone settled
    dropped: int = 0
    alone: int = 0
    #: the version of the witness that judged a stored reading kept as it
    #: stood, not witnessed again; None where this ingest's witness judged
    #: the page
    kept: int | None = None


@dataclass(frozen=True, slots=True)
class StoredReading:
    """A page's reading a backend already gave: its text, and the version
    of the witness that judged it with the findings it left, None where no
    witness did."""

    text: str
    reading: int | None = None
    disputed: int = 0


@dataclass(frozen=True, slots=True)
class _Ahead:
    """A page sent to the service before its turn: its image, and the
    reading coming back."""

    png: bytes
    reading: Future[tuple[Block, ...]]


def extract_pdf(
    pdf_path: Path,
    *,
    ocr: OcrProvider,
    tesseract: Witness | None = None,
    stored: Mapping[int, StoredReading] | None = None,
) -> list[PageText]:
    """Every page that carries text, OCR'ing the ones its layer does not serve.

    `tesseract` confirms a layer that may spell nothing before OCR replaces
    it; without one, such a layer is kept. It is also the witness of a page
    another backend reads, and finds the words in a digital page's figures.
    `stored` holds readings this backend already gave, by page index. One a
    witness judged keeps its text and its judgment; one none did is taken
    as the page's first reading, so it is witnessed without being read
    again, and keeps its text where the witness finds nothing in it.
    """
    with pdf.open_pdf(pdf_path) as document:
        kinds = document.page_kinds()

        @functools.cache
        def vocabulary() -> frozenset[str]:
            return frozenset().union(
                *(figures.words_of(document.text(i)) for i in range(1, len(kinds) + 1))
            )

        pages = []
        with ThreadPoolExecutor(max_workers=1) as service:
            ahead: dict[int, _Ahead] = {}

            def send(number: int) -> None:
                """Send a scan's page to the service before its turn; only
                the service's request leaves this thread."""
                if number > len(kinds) or kinds[number - 1] is not pdf.PageKind.SCAN:
                    return
                if (stored or {}).get(number) is not None:
                    return
                png = document.png(number, dpi=OCR_DPI)
                ahead[number] = _Ahead(png, service.submit(_blocks, ocr, png))

            send(1)
            for number, kind in enumerate(kinds, start=1):
                send(number + 1)
                page = _page_text(
                    document,
                    number,
                    kind,
                    ocr=ocr,
                    tesseract=tesseract,
                    stored=(stored or {}).get(number),
                    vocabulary=vocabulary,
                    ahead=ahead.pop(number, None),
                )
                if page.text or page.source is TextSource.FAILED:
                    pages.append(page)
        return pages


def _page_text(
    document: pdf.Pdf,
    index: int,
    kind: pdf.PageKind,
    *,
    ocr: OcrProvider,
    tesseract: Witness | None,
    stored: StoredReading | None = None,
    vocabulary: Callable[[], Set[str]] = frozenset,
    ahead: _Ahead | None = None,
) -> PageText:
    """One page: its own text layer, or OCR where the layer does not serve.

    A scanned page is OCR'd whatever its layer says, because a scan's layer
    is an old OCR's at best. A digital page is OCR'd when it carries an
    image and no text, or when its layer spells no words. A page OCR gave
    nothing for is `failed`; one that read, however short (a dedication,
    a part title), is recorded as read. A page kept as its layer gains its
    figures' text where Tesseract can find it and another backend read it;
    `vocabulary` gives the work's own words, the figures' words spelling
    one.
    """
    layer = document.text(index).strip()
    if kind is pdf.PageKind.SCAN or (not layer and document.has_images(index)):
        return _read(document, index, kind, layer, ocr, tesseract, stored, ahead)
    lost = lost_layer(document, index, layer, tesseract)
    if lost is None:
        if tesseract is None or ocr.name is tesseract.name:
            return PageText(index, layer, TextSource.NONE, kind)
        found = figures.figure_text(
            document,
            index,
            layer,
            vocabulary,
            ocr=ocr,
            tesseract=tesseract,
            dpi=OCR_DPI,
        )
        text = f"{layer}\n\n{found.text}" if found.text else layer
        return PageText(
            index,
            text,
            TextSource.NONE,
            kind,
            figures=found.figures,
            dropped=found.dropped,
        )
    if tesseract is not None and ocr.name is tesseract.name:
        source = ocr.name if lost else TextSource.FAILED
        return PageText(index, lost, source, kind)
    return _read(document, index, kind, layer, ocr, tesseract, stored)


def lost_layer(
    document: pdf.Pdf, index: int, layer: str, tesseract: OcrProvider | None
) -> str | None:
    """Tesseract's reading of a digital page whose layer spells no words,
    or None where the layer serves: its words are in the word list, or
    Tesseract reads the page as the layer says (German, an index), or
    there is no Tesseract to ask."""
    if tesseract is None or not layer_module.may_spell_nothing(layer):
        return None
    read = tesseract.read(document.png(index, dpi=OCR_DPI))
    if layer_module.similarity(read, layer) >= layer_module.TESSERACT_AGREES:
        return None
    return read


def _read(
    document: pdf.Pdf,
    index: int,
    kind: pdf.PageKind,
    layer: str,
    ocr: OcrProvider,
    tesseract: Witness | None,
    stored: StoredReading | None,
    ahead: _Ahead | None = None,
) -> PageText:
    """The OCR's reading of a page, witnessed where a witness can be: under
    any backend but Tesseract, which would be held against itself. A stored
    reading a witness judged stands as it was judged: an older witness's
    disputed page is never stored here (`upsert.outworn`), and today's would
    find in it what it found."""
    if stored is not None and stored.reading is not None:
        return PageText(
            index, stored.text, ocr.name, kind, stored.disputed, kept=stored.reading
        )
    png = ahead.png if ahead is not None else document.png(index, dpi=OCR_DPI)
    if stored is not None:
        first = tuple(Block(part.text) for part in witness.paragraphs(stored.text))
    elif ahead is not None:
        first = ahead.reading.result()
    else:
        first = _blocks(ocr, png)
    if not first:
        return PageText(index, "", TextSource.FAILED, kind)
    if tesseract is None or ocr.name is tesseract.name:
        return PageText(index, "\n\n".join(b.text for b in first), ocr.name, kind)
    with ThreadPoolExecutor(TESSERACT_AT_ONCE) as pool:
        whole = pool.submit(tesseract.read, png)
        seen = _seen(first, png, OCR_DPI, tesseract, pool)
        witnesses = [whole.result(), *([layer] if layer else [])]

        def read_at(scale: float) -> tuple[witness.Seen, ...]:
            dpi = round(OCR_DPI * scale)
            image = document.png(index, dpi=dpi)
            return _seen(_blocks(ocr, image), image, dpi, tesseract, pool)

        def read_alone(block: witness.Seen) -> witness.Seen | None:
            if block.box is None:
                return None
            part = _crop(document.png(index, dpi=block.dpi), block.box, block.dpi)
            text = "\n\n".join(b.text for b in _blocks(ocr, part))
            return witness.Seen(text, block.box, block.dpi, block.witnesses)

        found = witness.settle(seen, witnesses, read_at, read_alone)
    return PageText(
        index,
        found.text,
        ocr.name,
        kind,
        found.disputed,
        found.readings,
        dropped=found.dropped,
        alone=found.alone,
    )


def _blocks(ocr: OcrProvider, png: bytes) -> tuple[Block, ...]:
    """A page's reading as its blocks: with their boxes where the service
    gives them, else its paragraphs."""
    if isinstance(ocr, BlockReader):
        return ocr.read_blocks(png)
    return tuple(Block(part.text) for part in witness.paragraphs(ocr.read(png)))


def _seen(
    blocks: tuple[Block, ...],
    png: bytes,
    dpi: int,
    tesseract: Witness,
    pool: ThreadPoolExecutor,
) -> tuple[witness.Seen, ...]:
    """Each block with what its own box shows: Tesseract's reading of the
    box cropped alone, and Tesseract's words of the page within it, read
    at once in `pool`."""
    from PIL import Image

    if all(block.box is None for block in blocks):
        return tuple(witness.Seen(block.text) for block in blocks)
    words = pool.submit(tesseract.words, png)
    with Image.open(io.BytesIO(png)) as image:
        alone = [
            None
            if block.box is None
            else pool.submit(tesseract.read, _cropped(image, block.box, dpi))
            for block in blocks
        ]
    seen = []
    for block, reading in zip(blocks, alone, strict=True):
        if block.box is None or reading is None:
            seen.append(witness.Seen(block.text))
            continue
        left, top, right, bottom = block.box
        within = " ".join(
            word.text
            for word in words.result()
            if left <= word.centre[0] <= right and top <= word.centre[1] <= bottom
        )
        seen.append(
            witness.Seen(block.text, block.box, dpi, (reading.result(), within))
        )
    return tuple(seen)


def _crop(png: bytes, box: tuple[int, int, int, int], dpi: int) -> bytes:
    """A box of a page's image, with `BOX_MARGIN` around it, as a PNG."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        return _cropped(image, box, dpi)


def _cropped(image: Any, box: tuple[int, int, int, int], dpi: int) -> bytes:
    margin = round(BOX_MARGIN * dpi / OCR_DPI)
    left, top, right, bottom = box
    part = image.crop(
        (
            max(0, left - margin),
            max(0, top - margin),
            min(image.width, right + margin),
            min(image.height, bottom + margin),
        )
    )
    out = io.BytesIO()
    part.save(out, "PNG")
    return out.getvalue()
