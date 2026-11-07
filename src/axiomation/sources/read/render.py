"""A PDF page as an image.

What a reader reads where a page's text is unusable or reads wrong: a scan
with no text layer or whose OCR gave up, a table nobody can read as a line
of characters, a formula. Vision tokens are expensive: a reader asks for it
where `read_status` says `content`, or the text it has does not read.

The PDF is wherever the payload said it was. When that has gone stale —
Zotero moved the storage, the library was rebuilt — the adapter resolves the
key again, and only then does the read fail as `infrastructure`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axiomation.evidence import Page, ReadStatus, Sources
from axiomation.sources import pdf
from axiomation.sources.read import zotero

DEFAULT_DPI = 120


@dataclass(frozen=True, slots=True)
class Rendered:
    """One rendered page, or the reason there is none."""

    bib_key: str
    page_index: int
    png: bytes = b""
    file_path: str = ""
    read_status: ReadStatus = ReadStatus.OK
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "bib_key": self.bib_key,
            "page_index": self.page_index,
            "bytes": len(self.png),
            "file_path": self.file_path,
            "read_status": self.read_status.value,
            "detail": self.detail,
        }


def render(
    reader: Sources,
    bib_key: str,
    page_index: int,
    *,
    dpi: int = DEFAULT_DPI,
    zotero_url: str = zotero.DEFAULT_ZOTERO_URL,
) -> Rendered:
    """Render one page of one source. Never raises."""
    results = reader.page(bib_key, page_index)
    if not results.pages:
        return _failed(bib_key, page_index, results.detail, results.read_status)
    page = results.pages[0]
    pdf_path = pdf_path_for(page, zotero_url)
    if pdf_path is None:
        return _failed(
            bib_key,
            page_index,
            f"the PDF for {bib_key} is not at {page.file_path or '(no path stored)'} "
            "and Zotero offers no one other file of it. Re-ingest the collection "
            "or fix the bibliography's file field.",
        )
    try:
        png = render_pdf_page(pdf_path, page_index, dpi=dpi)
    except Exception as error:  # noqa: BLE001 - a broken PDF is not our bug
        return _failed(
            bib_key,
            page_index,
            f"cannot render page {page_index} of {pdf_path}: {error}",
        )
    return Rendered(
        bib_key=bib_key,
        page_index=page_index,
        png=png,
        file_path=str(pdf_path),
        read_status=page.read_status,
        detail=page.detail,
    )


def pdf_path_for(
    page: Page, zotero_url: str = zotero.DEFAULT_ZOTERO_URL
) -> Path | None:
    """The PDF the payload names, or the one Zotero names instead.

    Shared with `structure.py`, which reads the PDF's outline: the file is
    wherever the payload said, and when that has gone stale the adapter is
    asked again.
    """
    stored = Path(page.file_path) if page.file_path else None
    if stored is not None and stored.is_file():
        return stored
    if not zotero.available(zotero_url):
        return None
    return zotero.pdf_path(page.bib_key, base_url=zotero_url)


def render_pdf_page(
    pdf_path: Path, page_index: int, *, dpi: int = DEFAULT_DPI
) -> bytes:
    """One page of a PDF as PNG bytes. Page numbers are 1-indexed."""
    return pdf.page_png(pdf_path, page_index, dpi=dpi)


def _failed(
    bib_key: str,
    page_index: int,
    detail: str,
    status: ReadStatus = ReadStatus.INFRASTRUCTURE,
) -> Rendered:
    """A page not rendered: its read's own failure (a work held back is
    `mismatch`, a miss `absent`), else the tool's."""
    return Rendered(
        bib_key=bib_key,
        page_index=page_index,
        read_status=status,
        detail=detail,
    )
