"""A file against its entry (`sources.match`): the title on a title page,
the authors on it or just after, the year a note. Synthetic entries and
first pages, in words of our own: a book with the same title by another
author, a sibling paper by the same authors, OCR's glued and split words.
"""

from __future__ import annotations

import pytest

pytest.importorskip("qdrant_client")

from axiomation.sources.bib import BibRecord  # noqa: E402
from axiomation.sources.match import (  # noqa: E402
    FOUND_TITLE_RUN,
    check,
    expected,
    expected_found,
    surnames,
)

BOOK = BibRecord(
    "halvorsen2011",
    title="Shaken Flows of Smooth Maps",
    author="Halvorsen, Ines K. and Quist, Tobias",
    year="2011",
    kind="book",
)

#: The entry's own book: a half-title, a series page, then the title page.
RIGHT_BOOK = {
    1: "",
    2: "Lecture Notes on Moving Systems 41\nSeries editors\nA. Brand C. Oduya",
    3: "Ines K. Halvorsen  Tobias Quist\nShaken\nFlows of\nSmooth Maps\nSecond Edition",
    4: "Department of Mathematics\nNorthfield University\n© Northfield Press 2011",
    5: "Preface\nThis book collects what is known about maps under small noise.",
}

#: Another author's book with the same title, whose introduction names the
#: entry's authors: their names stand in the first pages, never on its
#: title page or the page after it.
OTHER_BOOK = {
    1: "",
    2: "Studies in Stochastic Motion\nVolume 9",
    3: "Ivo Marten\nShaken Flows of\nSmooth Maps\n1994 Kestrel Books\nLeiden",
    4: "Ivo Marten\nInstitute of Mathematics\nLibrary record: Marten, Ivo.\n"
    "Shaken flows of smooth maps / Ivo Marten. 1994",
    5: "Contents\n1 Small noise 1\n2 Exits 40",
    6: "Introduction\nThere is Halvorsen and Quist's book with the same title "
    "as this one; ours has no connection with it.",
}


def test_the_entrys_own_book_matches_on_its_title_page() -> None:
    found = check(expected(BOOK), RIGHT_BOOK)
    assert found.matches and found.title_page == 3 and not found.why
    assert found.says.startswith("Ines K. Halvorsen")
    assert found.year_note == ""


def test_a_book_with_the_same_title_by_another_author_is_held() -> None:
    found = check(expected(BOOK), OTHER_BOOK)
    assert not found.matches
    assert found.title_page == 3
    assert found.why == (
        "its title page (p. 3) names none of its entry's authors (Halvorsen, Quist)"
    )
    assert found.says.startswith("Ivo Marten Shaken Flows")
    assert found.year_note == "its first pages print 1994, not 2011"


def test_another_years_printing_of_the_right_book_is_a_note_never_a_hold() -> None:
    reprint = {**RIGHT_BOOK, 4: "© Northfield Press 2003, 2004"}
    found = check(expected(BOOK), reprint)
    assert found.matches
    assert found.year_note == "its first pages print 2003, 2004, not 2011"


PAPER = BibRecord(
    "oduya2000",
    title="A Nonparametric Rank Method of Accounting and Correcting for Missing "
    "Studies in Pooled Trials",
    author="Oduya, Clara and Brand, Anton",
    year="2000",
    kind="article",
)

#: The same authors' other paper, the same year, sharing a phrase of the
#: title (6 of 14 words), and citing the entry in its reference list.
SIBLING = {
    1: "Fill the Gap: A Plot Method of Testing and Adjusting "
    "for Missing Studies in Pooled Trials\nClara Oduya and Anton Brand\n"
    "Abstract. We test for studies left out of a pooled analysis.",
    2: "1. Introduction\nA pooled trial omits the studies nobody published.",
    3: "2. The method\nWe plot each study's effect against its size.",
    4: "3. Results\nThe plot shows the gap.",
    9: "References\nOduya, C. and Brand, A. (2000). A nonparametric rank "
    "method of accounting and correcting for missing studies in pooled trials.",
}


def test_a_sibling_paper_by_the_same_authors_is_held() -> None:
    found = check(expected(PAPER), SIBLING)
    assert not found.matches
    assert found.why.startswith("its first 4 pages with text print no title page")
    assert found.says_page == 1 and found.says.startswith("Fill the Gap")


def test_a_shared_phrase_over_half_a_title_passes_a_library_file_not_a_found_one() -> (
    None
):
    """A person chose a library file; a web candidate is a stranger, its
    title held to a higher bar."""
    near = {
        1: "Plot Based Correcting for Missing Studies in Pooled Trials\n"
        "Clara Oduya and Anton Brand"
    }
    want = expected(
        BibRecord(
            "oduya2000b",
            title="Rank Correcting for Missing Studies in Pooled Trials by Imputation",
            author="Oduya, Clara",
            kind="article",
        )
    )
    assert check(want, near).matches
    assert not check(want, near, FOUND_TITLE_RUN).matches


def test_a_doi_printed_on_the_first_pages_matches_outright() -> None:
    with_doi = BibRecord(
        PAPER.key,
        PAPER.title,
        PAPER.author,
        "10.5555/rank.2000.17",
        "",
        "2000",
        "article",
    )
    pages = {
        1: "Journal of Pooled Evidence\nhttps://doi.org/10.5555/rank.2000.17\n"
        "Some other words entirely"
    }
    assert check(expected(with_doi), pages).matches


def test_ocr_glued_and_split_words_still_read() -> None:
    entry = BibRecord(
        "wendt1987",
        title="A Universal Master Equation for Slow Decoherence",
        author="W{\\'e}ndt, L.",
        year="1987",
        kind="article",
    )
    glued = {
        1: "Volume 12, number 3\nA UNIVERSALMASTEREQUATION\nFOR SLOW "
        "DECOHERENCE\nL. WENDT1\nDepartment of Physics"
    }
    split = {1: "A Univ ersal Mas ter Equa tion for Slo w Decoherence\nL. W´endt"}
    assert check(expected(entry), glued).matches
    assert check(expected(entry), split).matches


def test_a_name_misread_by_one_letter_still_names_its_author() -> None:
    entry = BibRecord(
        "crumbles1992",
        title="Readings of Dark Matter Brownies",
        author="Crumbl{\\`e}s, Ottoline",
        kind="book",
    )
    pages = {
        1: "READINGS OF DARK MATTER BROWNIES\nOTTOLINE CRUMBLHS\n"
        "Laboratoire de pâtisserie"
    }
    assert check(expected(entry), pages).matches


def test_a_broken_text_layer_holds_no_readable_words() -> None:
    garbled = {i: "\x01\x02 Q% ;: ^^ 3$ \x7f" * 20 for i in range(1, 5)}
    found = check(expected(PAPER), garbled)
    assert not found.matches
    assert "hold no readable words" in found.why


def test_surnames_read_the_bibs_forms() -> None:
    assert surnames("Halvorsen, Ines K. and Quist, Tobias and others") == (
        "halvorsen",
        "quist",
    )
    assert surnames("Clara Oduya and {\\.Z}yczak, Ewa") == ("oduya", "zyczak")
    assert surnames("Gell-Mann, Murray") == ("gell mann",)


def test_a_resolved_work_brings_its_own_creators_and_year() -> None:
    bare = BibRecord("doi:10.5555/x")
    want = expected_found(bare, BOOK.title, ["Ines K. Halvorsen"], "2011-03", "")
    assert want.surnames == ("halvorsen",) and want.year == "2011"
    assert check(want, {1: "Shaken Flows of Smooth Maps\nInes K. Halvorsen"}).matches


def test_a_preface_after_the_cataloguing_page_never_names_the_authors() -> None:
    """A copyright page prints the title in its catalogue record; the page
    after it is often the preface, which may name the entry's authors."""
    early = {**OTHER_BOOK, 5: OTHER_BOOK[6], 6: "Chapter 1"}
    found = check(expected(BOOK), early)
    assert not found.matches and found.title_page == 3


def test_a_common_word_never_passes_for_a_short_name() -> None:
    entry = BibRecord(
        "hill1990",
        title="Shaken Flows of Smooth Maps",
        author="Hill, Ada",
        kind="article",
    )
    hall = {1: "Shaken Flows of Smooth Maps\nPrentice Hall, Englewood"}
    assert not check(expected(entry), hall).matches


def test_a_name_is_never_read_across_two_words() -> None:
    entry = BibRecord(
        "baker1984",
        title="Shaken Flows of Smooth Maps",
        author="Hedges, Lars",
        kind="article",
    )
    across = {1: "Shaken Flows of Smooth Maps\nthe hedge sat by the road"}
    split = {1: "Shaken Flows of Smooth Maps\nL. He dg es"}
    assert not check(expected(entry), across).matches
    assert check(expected(entry), split).matches


def test_a_longer_doi_is_another_work() -> None:
    with_doi = BibRecord(
        PAPER.key,
        PAPER.title,
        PAPER.author,
        "10.5555/rank.2000.1",
        "",
        "2000",
        "article",
    )
    longer = {1: "https://doi.org/10.5555/rank.2000.17\nSome other words entirely"}
    assert not check(expected(with_doi), longer).matches


#: A library's scan of the entry's book: binding and blank leaves, a
#: bookplate, a half-title printing the title alone, a blank, a series
#: page, a blank, then the title page as the PDF's eleventh.
SCANNED_BOOK = {
    2: "EX LIBRIS",
    7: "Northfield Centenary Series\nSHAKEN FLOWS OF SMOOTH MAPS",
    9: "Northfield Centenary Series\nPublished with the approval of the Senate",
    11: "SHAKEN FLOWS OF\nSMOOTH MAPS\nBY\nINES K. HALVORSEN\nAND TOBIAS QUIST",
    12: "Copyright 2011 by Northfield Press",
    **{i: f"Chapter text, page {i}." for i in range(13, 31)},
}


def test_a_scans_blank_leaves_are_not_counted_toward_its_title_page() -> None:
    """Pages 1-6, 8 and 10 print nothing; counted, they would push the title
    page past a book's first ten and the half-title's authors past reach."""
    blanks = {i: "" for i in (1, 3, 4, 5, 6, 8, 10)}
    found = check(expected(BOOK), {**blanks, **SCANNED_BOOK})
    assert found.matches and found.title_page == 7
    assert check(expected(BOOK), SCANNED_BOOK).matches


def test_a_window_still_ends_at_its_tenth_page_with_text() -> None:
    late = {i: f"Front matter, leaf {i}." for i in range(1, 11)}
    late[11] = SCANNED_BOOK[11]
    found = check(expected(BOOK), late)
    assert not found.matches
    assert found.why.startswith("its first 10 pages with text print no title page")
