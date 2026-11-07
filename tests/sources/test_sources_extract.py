"""PDF extraction and chunking.

Which page OCR reads is the page class's (`pdf.PageKind`, its own tests in
`test_sources_pdf.py`) and `_page_text`'s (`test_sources_ocr.py`); here a
whole PDF is extracted and chunked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.sources.contract import TextSource
from axiomation.sources.ingest.chunk import chunk_pages
from axiomation.sources.ingest.extract import PageText, extract_pdf
from axiomation.sources.pdf import PageKind
from sources.fakes import StubOcr
from sources.pdfs import full_page, prose, write_drawn_pdf

# --- Extraction ---


def test_a_digital_pdf_extracts_every_page_from_its_text_layer(
    fixture_pdf: Path,
) -> None:
    pages = extract_pdf(fixture_pdf, ocr=StubOcr())

    assert [page.page_index for page in pages] == [1, 2, 3]
    assert all(page.source is TextSource.NONE for page in pages)
    assert "divergent series" in pages[0].text


def test_a_document_mixing_kinds_is_read_page_by_page(tmp_path: Path) -> None:
    """A typeset cover keeps its layer; the scanned pages after it are read
    by OCR, their old layer set aside, and each page records its kind."""
    pytest.importorskip("pypdfium2")
    cover = prose(["Journal of Examples", "Stable URL and terms of use"])
    scanned = [full_page(), *prose(["fory the oId layer"], invisible=True)]
    path = write_drawn_pdf(tmp_path / "mixed.pdf", [cover, scanned, scanned])

    pages = extract_pdf(path, ocr=StubOcr(text="for y the old layer"))

    assert [page.source for page in pages] == [
        TextSource.NONE,
        TextSource.TESSERACT,
        TextSource.TESSERACT,
    ]
    assert [page.kind for page in pages] == [
        PageKind.DIGITAL,
        PageKind.SCAN,
        PageKind.SCAN,
    ]
    assert pages[1].text == "for y the old layer"


def test_a_missing_pdf_raises_rather_than_returning_nothing(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")

    with pytest.raises(FileNotFoundError):
        extract_pdf(tmp_path / "gone.pdf", ocr=StubOcr())


# --- Chunking ---


def pages(*texts: str) -> list[PageText]:
    return [
        PageText(page_index=number, text=text, source=TextSource.NONE)
        for number, text in enumerate(texts, start=1)
    ]


def test_one_chunk_per_page_in_page_order() -> None:
    chunks = chunk_pages(pages("first", "second", "third"), "hardy1949", "Divergent")

    assert [chunk.page_index for chunk in chunks] == [1, 2, 3]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert all(chunk.bib_key == "hardy1949" for chunk in chunks)


def test_a_chunk_is_its_page_and_nothing_borrowed() -> None:
    """A chunk is its page's text alone, so the vectors match the text a
    page read serves."""
    chunks = chunk_pages(pages("AAA", "BBB", "CCC"), "hardy1949", "Divergent")

    assert [chunk.text for chunk in chunks] == ["AAA", "BBB", "CCC"]


def test_a_blank_page_gets_no_chunk_and_does_not_break_the_index() -> None:
    chunks = chunk_pages(pages("first", "   ", "third"), "hardy1949", "Divergent")

    assert [chunk.page_index for chunk in chunks] == [1, 3]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]


def test_a_chunk_carries_the_reader_and_kind_of_its_own_page() -> None:
    mixed = [
        PageText(1, "read by the text layer", TextSource.NONE, PageKind.DIGITAL),
        PageText(2, "read by OCR", TextSource.PADDLE, PageKind.SCAN),
    ]

    chunks = chunk_pages(mixed, "hardy1949", "Divergent")

    assert [chunk.source for chunk in chunks] == [TextSource.NONE, TextSource.PADDLE]
    assert [chunk.kind for chunk in chunks] == [PageKind.DIGITAL, PageKind.SCAN]
