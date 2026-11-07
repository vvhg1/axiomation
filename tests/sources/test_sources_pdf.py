"""`pdf.py`, the module that owns the PDF library, and the writer it reads.

The writer is `tests/pdfs.py`; these tests are what make it
trustworthy as a fixture, and what pin the reader's five questions and its
1-based numbers. The one conversion that fails silently when it is wrong — a
0-based outline level or page — is asserted with exact numbers, never computed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.sources import pdf
from sources.pdfs import (
    MARGIN,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    full_page,
    image,
    prose,
    text,
    write_drawn_pdf,
    write_pdf,
)


class _Destination:
    def __init__(self, index: int) -> None:
        self._index = index

    def get_index(self) -> int:
        return self._index


class _Bookmark:
    def __init__(self, level: int, title: str, index: int | None) -> None:
        self.level = level
        self._title = title
        self._index = index

    def get_dest(self) -> _Destination | None:
        return None if self._index is None else _Destination(self._index)

    def get_title(self) -> str:
        return self._title


class _TocDocument:
    """Just enough of the library's document for the outline conversion."""

    def get_toc(self):
        return iter(
            [_Bookmark(0, "a bookmark to nothing", None), _Bookmark(1, "kept", 6)]
        )


def test_the_writer_makes_the_pages_and_text_it_claims(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "three.pdf",
        [f"page {number} body" for number in (1, 2, 3)],
    )

    with pdf.open_pdf(path) as document:
        assert document.page_count == 3
        assert document.text(2) == "page 2 body"


def test_a_layout_line_break_is_lf_not_pdfium_crlf(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "two-lines.pdf", ["first line\nsecond line"])

    with pdf.open_pdf(path) as document:
        text = document.text(1)

    assert text == "first line\nsecond line"
    assert "\r" not in text


def test_a_hyphen_at_a_line_end_keeps_its_hyphen_and_break(tmp_path: Path) -> None:
    """PDFium returns `Geo\\x02metrie`; the reader returns what the page shows,
    so `read.text` can rejoin the word and a quotation across it matches."""
    path = write_pdf(tmp_path / "hyphen.pdf", ["the book Geo-\nmetrie algebrique"])

    with pdf.open_pdf(path) as document:
        text = document.text(1)

    assert text == "the book Geo-\nmetrie algebrique"
    assert pdf.HYPHEN_BREAK not in text


def test_a_hyphen_before_a_digit_is_still_a_broken_word(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "digit.pdf", ["issues discussed in de-\n7 places"])

    with pdf.open_pdf(path) as document:
        assert document.text(1) == "issues discussed in de-\n7 places"


def test_a_glyph_with_no_unicode_is_marked_not_hidden(tmp_path: Path) -> None:
    """A font code the library could not map shows as the mark. The
    writer's font has no glyph at these codes, so PDFium passes them
    through as it does a TeX math font's; `\\x02` with no word around it is
    one of them, not a hyphen."""
    path = write_pdf(
        tmp_path / "unmapped.pdf", ["an \x0funif-alg bound, a \x02 bracket"]
    )

    with pdf.open_pdf(path) as document:
        text = document.text(1)

    assert text == f"an {pdf.UNMAPPED}unif-alg bound, a {pdf.UNMAPPED} bracket"


def test_a_page_longer_than_one_line_reads_back_whole(tmp_path: Path) -> None:
    """The fixtures write whole paragraphs; none of it may fall off the page."""
    paragraph = "Page 1 of the divergent series. " * 12
    lines = "\n".join(f"line {number}" for number in range(1, 9))
    path = write_pdf(tmp_path / "long.pdf", [paragraph, lines])

    with pdf.open_pdf(path) as document:
        assert document.text(1).split() == paragraph.split()
        assert document.text(2) == lines


def test_text_that_would_run_off_the_page_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="a page holds 28 lines"):
        write_pdf(tmp_path / "overfull.pdf", ["a line\n" * 40])


def test_the_writer_makes_an_outline_the_reader_reads_back(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "outlined.pdf",
        ["a page"] * 12,
        outline=[
            (1, "Chapter 2 Bounds and means", 5),
            (2, "2.2 Lovász's bound", 5),
            (3, "2.2.1 A subsection", 6),
            (1, "Chapter 7 The end", 11),
        ],
    )

    with pdf.open_pdf(path) as document:
        assert document.outline() == (
            pdf.Bookmark(1, "Chapter 2 Bounds and means", 5),
            pdf.Bookmark(2, "2.2 Lovász's bound", 5),
            pdf.Bookmark(3, "2.2.1 A subsection", 6),
            pdf.Bookmark(1, "Chapter 7 The end", 11),
        )


def test_the_writer_marks_the_page_that_carries_an_image(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "imaged.pdf", ["plain", "with image"], images=[2])

    with pdf.open_pdf(path) as document:
        assert document.has_images(1) is False
        assert document.has_images(2) is True


def test_a_one_pixel_rule_is_not_an_image(tmp_path: Path) -> None:
    """TeX draws rules as 1x1 inline images; a real paper's every page has one."""
    path = write_pdf(
        tmp_path / "ruled.pdf",
        ["a rule", "a rule and a picture"],
        images=[2],
        rules=[1, 2],
    )

    with pdf.open_pdf(path) as document:
        assert document.has_images(1) is False
        assert document.has_images(2) is True


def test_a_bookmark_with_no_target_is_dropped() -> None:
    document = pdf.Pdf(_TocDocument(), name="fake.pdf")

    assert document.outline() == (pdf.Bookmark(2, "kept", 7),)


def test_a_page_png_is_a_png(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "one.pdf", ["a page"])

    assert pdf.page_png(path, 1, dpi=72).startswith(b"\x89PNG")


def test_a_page_outside_the_document_names_the_count(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "two.pdf", ["a page", "another"])

    with pytest.raises(ValueError, match="2 pages"):
        pdf.page_png(path, 3, dpi=72)


def test_a_missing_file_raises(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")

    with pytest.raises(FileNotFoundError):
        pdf.outline(tmp_path / "gone.pdf")


# --- The page class: a scan is a picture of the page ---

PROSE = [
    "The series diverges for every value of the parameter.",
    "Its partial sums grow without bound, as the second lemma shows.",
]
FIGURE = image(MARGIN, 300, 300, 300)


def kinds(tmp_path: Path, *pages: list[str]) -> tuple[pdf.PageKind, ...]:
    pytest.importorskip("pypdfium2")
    path = write_drawn_pdf(tmp_path / "drawn.pdf", pages)
    with pdf.open_pdf(path) as document:
        return document.page_kinds()


SCAN, DIGITAL = pdf.PageKind.SCAN, pdf.PageKind.DIGITAL


def test_a_page_image_under_invisible_text_is_a_scan(tmp_path: Path) -> None:
    """How a scanner lays its OCR over the picture: the old layer a
    producer field or a count of characters took for digital text."""
    assert kinds(tmp_path, [full_page(), *prose(PROSE, invisible=True)]) == (SCAN,)


def test_a_page_image_with_no_text_is_a_scan(tmp_path: Path) -> None:
    assert kinds(tmp_path, [full_page()]) == (SCAN,)


def test_text_painted_before_the_image_that_covers_it_is_unseen(
    tmp_path: Path,
) -> None:
    """A visible layer under the picture is no reader's text."""
    assert kinds(tmp_path, [*prose(PROSE), full_page()]) == (SCAN,)


def test_a_page_tiled_in_strips_is_a_scan(tmp_path: Path) -> None:
    strips = [
        image(0, 0, PAGE_WIDTH, PAGE_HEIGHT / 2),
        image(0, PAGE_HEIGHT / 2, PAGE_WIDTH, PAGE_HEIGHT / 2),
    ]
    assert kinds(tmp_path, [*strips, *prose(PROSE, invisible=True)]) == (SCAN,)


def test_text_drawn_over_an_image_is_a_digital_page(tmp_path: Path) -> None:
    """A cover, a slide: the text a reader sees is the page's own."""
    assert kinds(tmp_path, [full_page(), *prose(PROSE)]) == (DIGITAL,)


def test_a_figure_with_its_caption_is_a_digital_page(tmp_path: Path) -> None:
    caption = text("Figure 2: the partial sums.", y=280)
    assert kinds(tmp_path, [*prose(PROSE), FIGURE, caption]) == (DIGITAL,)


def test_a_page_with_no_image_is_digital_text_or_blank(tmp_path: Path) -> None:
    assert kinds(tmp_path, prose(PROSE), []) == (DIGITAL, DIGITAL)


def test_an_overlay_far_larger_than_the_page_is_no_page_image(
    tmp_path: Path,
) -> None:
    """A watermark drawn past the page's edges is not a picture of it."""
    overlay = image(-2000, -2000, 5000, 5000)
    assert kinds(tmp_path, [overlay]) == (DIGITAL,)


def test_a_stamp_on_most_pages_leaves_a_scan_a_scan(tmp_path: Path) -> None:
    """An archive draws its download line over every page of a scan."""
    stamp = text("This content downloaded on 1 May 2016", y=20)
    page = [full_page(), stamp]

    assert kinds(tmp_path, page, page, page) == (SCAN, SCAN, SCAN)


def test_a_stamp_that_numbers_its_pages_is_still_a_stamp(tmp_path: Path) -> None:
    pages = [[full_page(), text(f"|000{n}||", y=20)] for n in (25, 26, 27)]

    assert kinds(tmp_path, *pages) == (SCAN, SCAN, SCAN)


def test_a_line_on_one_page_only_is_the_work_s_own_text(tmp_path: Path) -> None:
    """A stamp repeats; a heading set over one picture does not."""
    once = [full_page(), text("Chapter One", y=700)]

    assert kinds(tmp_path, once, [full_page()], [full_page()]) == (
        DIGITAL,
        SCAN,
        SCAN,
    )


def test_a_layer_outweighing_what_the_page_shows_makes_it_a_scan(
    tmp_path: Path,
) -> None:
    """A publisher's line over one scanned page with an OCR layer."""
    heading = text("E. Author, A Book", y=760)
    scanned = [full_page(), *prose(PROSE * 3, invisible=True), heading]

    assert kinds(tmp_path, scanned, [full_page()], [full_page()]) == (
        SCAN,
        SCAN,
        SCAN,
    )


def test_a_document_mixing_kinds_is_read_page_by_page(tmp_path: Path) -> None:
    """A typeset cover on a scanned article, or a scanned plate in a book."""
    cover = prose(["Journal of Examples", "Stable URL and terms of use"])
    scanned = [full_page(), *prose(PROSE, invisible=True)]

    assert kinds(tmp_path, cover, scanned, scanned) == (DIGITAL, SCAN, SCAN)
