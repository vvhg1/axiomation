"""Locate a label, grep the text, read the shape of a source.

A synthetic source whose pages carry known labels in the three publisher
spellings, with a ligature, a word broken over a line, a label that ends in
a full stop, and a cross-reference storm. Each case is a shape real
sources have, made small.

Nothing here opens a socket. Zotero is pointed at a closed port, because the
outline lives in the PDF and the adapter's whole job is to decide whether a
port answers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("qdrant_client")

from axiomation.evidence import ReadStatus  # noqa: E402
from axiomation.sources.config import ResolvedSources, SourcesError  # noqa: E402
from axiomation.sources.contract import TextSource  # noqa: E402
from axiomation.sources.library.client import SourceReader  # noqa: E402
from axiomation.sources.read.grep import LINE_CAP, PATTERN_LIMIT, grep  # noqa: E402
from axiomation.sources.read.locate import (  # noqa: E402
    CANDIDATE_CAP,
    LabelForm,
    LabelKind,
    forms,
    locate,
)
from axiomation.sources.read.structure import CONTENTS_SCAN, ContentsKind, contents  # noqa: E402
from axiomation.sources.read.text import lines_of, normalize, text_quality  # noqa: E402
from sources.fakes import FakeQdrant  # noqa: E402

NO_ZOTERO = "http://127.0.0.1:1"
FILLER = " The rest of the page is ordinary prose, enough to clear the floor." * 2

#: One source, page by page. Each page is a case real sources show.
PAGES: dict[int, str] = {
    1: "Contents\n1 Introduction 3\n2 Bounds and means 5" + FILLER,
    3: "2.1 Preliminaries\nDeﬁnition 2.1.1 (Ligature) A set is small when." + FILLER,
    5: (
        "2.2 Lovász's bound\n"
        "Theorem 2.2.3 (Lov´asz) When the weights are positive, the sum satisfies\n"
        "needed for proving Theorem 2.2.3." + FILLER
    ),
    6: "Proof of Theorem 2.2.30: by Theorem 2.2.3 and Theo-\nrem 2.2.4 it holds."
    + FILLER,
    7: "6.4.11. Corollary. Let X be a separable Hilbert space.\n(6.4.11)" + FILLER,
    8: "3.2.12 \n3.2.12. Theorem. If f is Lipschitzian and m > n then" + FILLER,
    9: "THEOREM 2. Let f be continuous. Then it is bounded." + FILLER,
    10: "1.2.3 A bare label opens this line and no word names it" + FILLER,
}
STORM = range(11, 11 + CANDIDATE_CAP + 2)
KEY = "book2001"


def stock(fake: FakeQdrant, collection: str, **payload) -> None:
    pages = dict(PAGES)
    for index in STORM:
        pages[index] = "see Theorem 1.1 for the standard argument." + FILLER
    for index, text in pages.items():
        fake.add_chunk(
            collection,
            bib_key=KEY,
            title="A Book",
            page_number=index,
            chunk_index=index,
            text=text,
            ocr_backend=TextSource.PADDLE.value,
            file_path="/nowhere/book2001.pdf",
            **payload,
        )


@pytest.fixture
def book(reader: SourceReader, fake_qdrant: FakeQdrant, settings: ResolvedSources):
    stock(fake_qdrant, settings.collection)
    return reader


def find(reader: SourceReader, word: str, number: str, kind=LabelKind.RESULT):
    return locate(reader, KEY, word, number, kind=kind, zotero_url=NO_ZOTERO)


# --- Normalization ---


class TestNormalization:
    def test_a_ligature_folds(self):
        assert normalize("Deﬁnition 2.2.2") == "Definition 2.2.2"

    def test_whitespace_collapses(self):
        assert normalize("Theorem   2.2.3 ") == "Theorem 2.2.3"

    def test_a_word_broken_over_a_line_is_rejoined(self):
        lines = lines_of("by Theo-\nrem 2.2.4 it holds")
        assert [line.text for line in lines] == ["by Theorem 2.2.4 it holds"]

    def test_a_hyphen_before_a_capital_is_a_real_hyphen(self):
        """`Fenchel-\\nLegendre` is a compound name, not a broken word."""
        lines = lines_of("the Fenchel-\nLegendre transform")
        assert len(lines) == 2

    def test_the_shown_line_is_the_source_s_own(self):
        """A reader quotes the line as evidence, ligature and all."""
        (line,) = lines_of("Deﬁnition 2.1.1 (Ligature)")
        assert line.shown.startswith("Deﬁnition")
        assert line.text.startswith("Definition")


# --- locate ---


class TestLocate:
    def test_a_result_is_found_where_it_starts_a_line(self, book):
        found = find(book, "theorem", "2.2.3")
        assert found.read_status is ReadStatus.OK
        assert found.form is LabelForm.WORD_NUMBER
        assert found.top.page_index == 5
        assert found.top.line_initial
        assert found.top.line.startswith("Theorem 2.2.3 (Lov´asz)")

    def test_a_cross_reference_ranks_after_the_statement(self, book):
        found = find(book, "theorem", "2.2.3")
        assert [c.page_index for c in found.candidates] == [5, 6]
        assert [c.line_initial for c in found.candidates] == [True, False]

    def test_a_longer_number_is_a_different_label(self, book):
        """A longer number is another label: 2.2.30 is not 2.2.3."""
        found = find(book, "theorem", "2.2.30")
        assert [c.page_index for c in found.candidates] == [6]
        assert not found.top.line_initial

    def test_a_ligature_does_not_hide_a_definition(self, book):
        found = find(book, "definition", "2.1.1")
        assert found.top.page_index == 3
        assert found.top.line_initial

    def test_a_word_broken_over_a_line_is_still_the_word(self, book):
        found = find(book, "theorem", "2.2.4")
        assert [c.page_index for c in found.candidates] == [6]

    def test_a_full_stop_after_the_label_is_the_statement(self, book):
        """`THEOREM 2.` is Theorem 2 with its full stop, and `Theorem 2.2.3`
        is not Theorem 2 at all."""
        found = find(book, "theorem", "2")
        assert [c.page_index for c in found.candidates] == [9]
        assert found.top.line_initial

    def test_the_publisher_that_prints_the_number_first(self, book):
        found = find(book, "corollary", "6.4.11")
        assert found.form is LabelForm.NUMBER_WORD
        assert found.top.page_index == 7
        assert found.top.line_initial

    def test_the_number_first_form_beats_the_bare_number(self, book):
        """Federer prints `3.2.12` alone and then `3.2.12. Theorem.`; the
        spelling with the word answers and says so."""
        found = find(book, "theorem", "3.2.12")
        assert found.form is LabelForm.NUMBER_WORD
        assert found.top.page_index == 8

    def test_the_bare_number_answers_when_no_word_does(self, book):
        found = find(book, "lemma", "1.2.3")
        assert found.form is LabelForm.NUMBER
        assert found.top.page_index == 10
        assert found.top.line_initial

    def test_the_bare_number_is_never_tried_without_a_dot(self, book):
        assert [form for form, _ in forms("theorem", "2")] == [
            LabelForm.WORD_NUMBER,
            LabelForm.NUMBER_WORD,
        ]

    def test_the_candidates_are_capped_and_the_drop_is_counted(self, book):
        found = find(book, "theorem", "1.1")
        assert len(found.candidates) == CANDIDATE_CAP
        assert found.dropped == len(STORM) - CANDIDATE_CAP
        assert not any(c.line_initial for c in found.candidates)

    def test_a_single_unnormalized_form_misses_what_it_must_miss(
        self, fake_qdrant: FakeQdrant, settings: ResolvedSources
    ):
        """One spelling over the raw
        text finds neither the ligature nor the broken word."""
        stock(fake_qdrant, settings.collection)
        raw = {
            p.payload["page_number"]: p.payload["text"]
            for p in fake_qdrant.points(settings.collection)
        }
        assert re.search(r"Definition 2\.1\.1", raw[3]) is None
        assert re.search(r"Theorem 2\.2\.4", raw[6]) is None
        assert re.search(r"Corollary 6\.4\.11", raw[7]) is None

    def test_a_miss_over_clean_text_is_an_answer(self, book):
        found = find(book, "lemma", "9.9.9")
        assert found.read_status is ReadStatus.OK
        assert found.candidates == ()
        assert found.form is None
        assert "no page of book2001 prints lemma 9.9.9" in found.detail
        assert f"over {len(PAGES) + len(STORM)} pages" in found.detail

    def test_a_miss_over_a_page_under_the_floor_is_content(
        self, book, fake_qdrant: FakeQdrant, settings: ResolvedSources
    ):
        """The label could sit on the page nobody can read."""
        fake_qdrant.add_chunk(
            settings.collection,
            bib_key=KEY,
            page_number=40,
            chunk_index=40,
            text="Fig. 2",
        )
        found = find(book, "lemma", "9.9.9")
        assert found.read_status is ReadStatus.CONTENT
        assert "page index 40" in found.detail
        assert found.quality.under_floor == (40,)

    def test_a_hit_over_the_same_source_stays_ok(
        self, book, fake_qdrant: FakeQdrant, settings: ResolvedSources
    ):
        """Many sources carry a page under the floor. A
        line-initial hit is a hit whatever the other pages are."""
        fake_qdrant.add_chunk(
            settings.collection,
            bib_key=KEY,
            page_number=40,
            chunk_index=40,
            text="Fig. 2",
        )
        found = find(book, "theorem", "2.2.3")
        assert found.read_status is ReadStatus.OK
        assert found.top.page_index == 5

    def test_a_key_that_is_not_indexed_is_infrastructure(self, book):
        found = locate(book, "nosuch1999", "theorem", "1", kind=LabelKind.RESULT)
        assert found.read_status is ReadStatus.INFRASTRUCTURE
        assert "nosuch1999" in found.detail
        assert found.quality is None

    def test_the_quality_report_says_what_the_text_is(self, book):
        found = find(book, "theorem", "2.2.3")
        assert found.quality.pages == len(PAGES) + len(STORM)
        assert found.quality.ocr == found.quality.pages
        assert found.quality.text_layer == 0
        assert "OCR" in found.quality.summary()

    def test_locate_needs_no_api_key(self, fake_qdrant, settings):
        stock(fake_qdrant, settings.collection)
        bare = SourceReader(settings)
        bare._client = fake_qdrant
        assert find(bare, "theorem", "2.2.3").top.page_index == 5


# --- Sections go to the structure first ---


@pytest.fixture
def outlined_pdf(tmp_path: Path) -> Path:
    """A twelve-page book with a four-entry nested outline, written directly."""
    from sources.pdfs import write_pdf

    path = tmp_path / "book2001.pdf"
    return write_pdf(
        path,
        ["a page of the book"] * 12,
        outline=[
            (1, "Chapter 2 Bounds and means", 5),
            (2, "2.2 Lovász's bound", 5),
            (2, "2.2.1 A subsection", 6),
            (1, "Chapter 7 The end", 11),
        ],
    )


class TestSections:
    def test_a_section_goes_to_the_outline_first(
        self, book, fake_qdrant, settings, outlined_pdf
    ):
        for point in fake_qdrant.points(settings.collection):
            point.payload["file_path"] = str(outlined_pdf)
        found = find(book, "section", "2.2", LabelKind.SECTION)
        assert found.form is LabelForm.OUTLINE
        assert [c.page_index for c in found.candidates] == [5]
        assert found.top.line == "2.2 Lovász's bound"
        assert found.outline_entries == 4

    def test_a_subsection_number_does_not_name_its_parent(
        self, book, fake_qdrant, settings, outlined_pdf
    ):
        for point in fake_qdrant.points(settings.collection):
            point.payload["file_path"] = str(outlined_pdf)
        found = find(book, "chapter", "7", LabelKind.CHAPTER)
        assert [c.page_index for c in found.candidates] == [11]
        # The paper says `\S2`; the publisher's outline says `Chapter 2`.
        assert find(book, "section", "2", LabelKind.SECTION).form is LabelForm.OUTLINE
        assert find(book, "section", "2.2.1", LabelKind.SECTION).top.page_index == 6

    def test_without_a_pdf_the_string_answers(self, book):
        """The bare number, line-initial: `2.2 Lovász's bound` heads page 5
        and `(6.4.11)` on page 7 is not it."""
        found = find(book, "section", "2.2", LabelKind.SECTION)
        assert found.form is LabelForm.NUMBER
        assert found.top.page_index == 5
        assert found.top.line_initial
        assert found.outline_entries is None

    def test_a_section_the_outline_does_not_name_falls_to_the_string(
        self, book, fake_qdrant, settings, outlined_pdf
    ):
        for point in fake_qdrant.points(settings.collection):
            point.payload["file_path"] = str(outlined_pdf)
        found = find(book, "section", "2.1", LabelKind.SECTION)
        assert found.form is LabelForm.NUMBER
        assert found.top.page_index == 3
        assert found.outline_entries == 4

    def test_a_chapter_nobody_prints_says_what_was_tried(
        self, book, fake_qdrant, settings, outlined_pdf
    ):
        for point in fake_qdrant.points(settings.collection):
            point.payload["file_path"] = str(outlined_pdf)
        found = find(book, "chapter", "9", LabelKind.CHAPTER)
        assert found.candidates == ()
        assert "outline has 4 entries" in found.detail


# --- grep ---


class TestGrep:
    def test_a_pattern_that_is_present(self, book):
        found = grep(book, KEY, "Hilbert space")
        assert found.read_status is ReadStatus.OK
        assert found.pages_matched == (7,)
        assert found.matches[0].line.startswith("6.4.11. Corollary.")

    def test_a_pattern_that_is_absent_is_an_answer_not_an_error(self, book):
        found = grep(book, KEY, "Kullback")
        assert found.read_status is ReadStatus.OK
        assert found.matches == ()
        assert "no line of book2001 matches 'Kullback'" in found.detail
        assert found.pages_searched == len(PAGES) + len(STORM)

    def test_a_negative_says_what_text_it_ran_over(self, book):
        found = grep(book, KEY, "Kullback")
        assert found.quality.ocr == found.pages_searched
        assert found.quality.under_floor == ()

    def test_a_ligature_matches_only_after_normalization(self, book):
        found = grep(book, KEY, "Definition 2\\.1\\.1")
        assert found.pages_matched == (3,)
        assert found.matches[0].line.startswith("Deﬁnition")

    def test_a_ligature_in_the_pattern_matches_too(self, book):
        assert grep(book, KEY, "Deﬁnition").pages_matched == (3,)

    def test_the_pattern_is_case_insensitive(self, book):
        """`THEOREM 2.` on page 9 answers a lowercase pattern; page 5's
        `Theorem 2.2.3` starts the same way."""
        assert grep(book, KEY, "^theorem 2\\.").pages_matched == (5, 9)

    def test_more_matches_than_the_cap_are_counted_not_served(self, book):
        found = grep(book, KEY, "the", cap=3)
        assert len(found.matches) == 3
        assert found.dropped > 0
        assert len(found.pages_matched) > 3

    def test_the_default_cap_is_forty_lines(self):
        assert LINE_CAP == 40

    def test_a_search_the_clock_stopped_is_not_a_negative(self, book):
        ticks = iter([0.0, 100.0, 200.0, 300.0])
        found = grep(book, KEY, "Kullback", budget=5.0, clock=lambda: next(ticks))
        assert found.timed_out
        assert found.read_status is ReadStatus.INFRASTRUCTURE
        assert "not a negative" in found.detail
        assert found.pages_searched == 0

    def test_a_stopped_search_with_matches_keeps_them(self, book):
        ticks = iter([0.0, 0.0, 0.0, 100.0, 200.0, 300.0, 400.0, 500.0])
        found = grep(book, KEY, "Introduction", budget=5.0, clock=lambda: next(ticks))
        assert found.timed_out
        assert found.read_status is ReadStatus.OK
        assert found.pages_matched == (1,)
        assert "the matches are from those pages" in found.detail

    def test_a_miss_over_a_page_under_the_floor_is_content(
        self, book, fake_qdrant, settings
    ):
        fake_qdrant.add_chunk(
            settings.collection,
            bib_key=KEY,
            page_number=40,
            chunk_index=40,
            text="Fig. 2",
        )
        found = grep(book, KEY, "Kullback")
        assert found.read_status is ReadStatus.CONTENT
        assert "page index 40" in found.detail

    def test_a_key_that_is_not_indexed_is_infrastructure(self, book):
        found = grep(book, "nosuch1999", "anything")
        assert found.read_status is ReadStatus.INFRASTRUCTURE
        assert found.matches == ()

    def test_a_pattern_that_is_too_long_is_refused(self, book):
        with pytest.raises(SourcesError, match="limit"):
            grep(book, KEY, "x" * (PATTERN_LIMIT + 1))

    def test_a_pattern_that_is_not_a_regex_is_refused(self, book):
        with pytest.raises(SourcesError, match="regular expression"):
            grep(book, KEY, "[")

    def test_the_quality_report_tells_a_text_layer_from_a_scan(
        self, book, fake_qdrant, settings
    ):
        for point in list(fake_qdrant.points(settings.collection))[:3]:
            point.payload["ocr_backend"] = TextSource.NONE.value
        found = grep(book, KEY, "Kullback")
        assert found.quality.text_layer == 3
        assert found.quality.ocr == found.quality.pages - 3


def test_text_quality_counts_every_origin(book):
    from axiomation.sources.library.client import Page

    pages = [
        Page("k", "t", 1, "", "x" * 60, text_source="none"),
        Page("k", "t", 2, "", "x" * 60, text_source="paddle"),
        Page("k", "t", 3, "", "x" * 60, text_source="tesseract"),
        Page("k", "t", 4, "", "", read_status=ReadStatus.CONTENT, text_source="failed"),
        Page("k", "t", 5, "", "x" * 60),
    ]
    quality = text_quality(pages)
    assert (quality.text_layer, quality.ocr, quality.failed, quality.unknown) == (
        1,
        2,
        1,
        1,
    )
    assert quality.under_floor == (4,)
    assert quality.degraded


# --- toc ---


class TestContents:
    def test_an_outline_when_the_pdf_carries_one(
        self, book, fake_qdrant, settings, outlined_pdf
    ):
        for point in fake_qdrant.points(settings.collection):
            point.payload["file_path"] = str(outlined_pdf)
        found = contents(book, KEY, zotero_url=NO_ZOTERO)
        assert found.kind is ContentsKind.OUTLINE
        assert [(e.level, e.page_index) for e in found.outline][:2] == [(1, 5), (2, 5)]
        assert found.pages == ()
        assert found.file_path == str(outlined_pdf)

    def test_the_contents_pages_when_there_is_no_outline(
        self, book, fake_qdrant, settings, fixture_pdf
    ):
        for point in fake_qdrant.points(settings.collection):
            point.payload["file_path"] = str(fixture_pdf)
        found = contents(book, KEY, zotero_url=NO_ZOTERO)
        assert found.kind is ContentsKind.CONTENTS_PAGES
        assert [page.page_index for page in found.pages] == [1]
        assert found.pages[0].text.startswith("Contents")
        assert "may run on" in found.detail

    def test_neither_is_the_honest_answer(
        self, book, fake_qdrant, settings, fixture_pdf
    ):
        for point in fake_qdrant.points(settings.collection):
            point.payload["file_path"] = str(fixture_pdf)
            if point.payload["page_number"] == 1:
                point.payload["text"] = "A title page" + FILLER
        found = contents(book, KEY, zotero_url=NO_ZOTERO)
        assert found.kind is ContentsKind.NONE
        assert f"first {CONTENTS_SCAN} pages" in found.detail

    def test_a_missing_pdf_still_finds_the_contents_pages_and_says_so(self, book):
        found = contents(book, KEY, zotero_url=NO_ZOTERO)
        assert found.kind is ContentsKind.CONTENTS_PAGES
        assert "not at /nowhere/book2001.pdf" in found.detail

    def test_a_key_that_is_not_indexed_is_infrastructure(self, book):
        found = contents(book, "nosuch1999", zotero_url=NO_ZOTERO)
        assert found.read_status is ReadStatus.INFRASTRUCTURE
        assert found.kind is ContentsKind.NONE

    def test_the_document_is_one_json_object(
        self, book, fake_qdrant, settings, outlined_pdf
    ):
        import json

        for point in fake_qdrant.points(settings.collection):
            point.payload["file_path"] = str(outlined_pdf)
        payload = json.loads(
            json.dumps(contents(book, KEY, zotero_url=NO_ZOTERO).as_dict())
        )
        assert payload["kind"] == "outline"
        assert payload["outline"][0]["title"] == "Chapter 2 Bounds and means"
