"""One chunk per page, and nothing borrowed.

Page-level chunks are what makes a citation checkable: a verdict can name the
page it read, and the human can open that page. A chunk that also carried
its neighbours' text would make "page 44" three pages, its vectors built over
all three, so a read could serve text its vectors never matched or cite a
neighbour as the page. A chunk is a page and the payload says so
(`contract.ChunkScheme.PAGE`); how a sentence across a page break stays
findable is the embedding's job."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from axiomation.sources.contract import TextSource
from axiomation.sources.ingest.extract import PageText
from axiomation.sources.pdf import PageKind


@dataclass(frozen=True, slots=True)
class Chunk:
    """One page's retrievable text, and where that text came from."""

    bib_key: str
    title: str
    page_index: int
    chunk_index: int
    text: str
    source: TextSource
    kind: PageKind = PageKind.DIGITAL
    #: the witness's findings left on the page; None where none ran
    disputed: int | None = None
    #: the version of the witness that judged a stored reading kept as it
    #: stood; None where this ingest's witness judged it
    kept: int | None = None
    #: the figures whose text OCR added to the layer; None where no test ran
    figures: int | None = None


def chunk_pages(pages: Sequence[PageText], bib_key: str, title: str) -> list[Chunk]:
    """One chunk per page that carries text, in page order.

    A page whose text is blank is not a chunk, so `chunk_index` counts the
    pages that are and `page_index` still says where each one sits in the PDF.
    """
    usable = [page for page in pages if page.text.strip()]
    return [
        Chunk(
            bib_key=bib_key,
            title=title,
            page_index=page.page_index,
            chunk_index=index,
            text=page.text,
            source=page.source,
            kind=page.kind,
            disputed=page.disputed,
            kept=page.kept,
            figures=page.figures,
        )
        for index, page in enumerate(usable)
    ]
