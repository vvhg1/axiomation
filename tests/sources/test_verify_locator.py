"""The locator verifier.

Synthetic pages, so each outcome has a case it must decide and the page-count
floor is pinned to a measurement. Pages reach the verifier only through
`Sources`, which stamps each. The parser's locator shapes, the filing and
the queue are tested elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

pytest.importorskip("qdrant_client")

from axiomation.evidence import Origin, RawPage, Sources  # noqa: E402
from axiomation.sites import Site, SiteKind  # noqa: E402
from axiomation.sources.verify.locator import (  # noqa: E402
    NEGATIVE_PAGE_FLOOR,
    Outcome,
    PagesOnce,
    check,
)

KEY = "brown2010"
THEOREM = "Theorem~2.1.24"
LABEL_PAGE = "Theorem 2.1.24. The bound holds, as the following proof shows."


@dataclass
class Shelf:
    """A provider of the protocol, counting its page reads."""

    origin: Origin = Origin.LIBRARY
    texts: dict[str, dict[int, str]] = field(default_factory=dict)
    down: bool = False
    #: a key held back, and why: its file is not its entry's work
    mismatched: dict[str, str] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)

    def holds(self, key: str) -> bool:
        if self.down:
            raise ConnectionError("no qdrant")
        return key in self.texts

    def held(self, key: str) -> str:
        return self.mismatched.get(key, "")

    def pages(self, key: str, start: int, end: int | None) -> list[RawPage]:
        if self.down:
            raise ConnectionError("no qdrant")
        self.reads.append(key)
        held = self.texts.get(key, {})
        last = max(held, default=0) if end is None else end
        return [
            RawPage(key, i, held[i], file_path="/nowhere/brown2010.pdf")
            for i in sorted(held)
            if start <= i <= last
        ]

    def search(self, query: str, key: str | None, top_k: int) -> list[RawPage]:
        return []


def prose(n: int) -> str:
    return f"Page {n} of prose, with nothing to find on it but plain words."


def long_pages(count: int, *, first: int = 1) -> dict[int, str]:
    return {n: prose(n) for n in range(first, first + count)}


def reader(
    pages: dict[int, str] | None = None,
    *,
    origin: Origin = Origin.LIBRARY,
    key: str = KEY,
) -> tuple[PagesOnce, Shelf]:
    shelf = Shelf(origin, {key: pages} if pages is not None else {})
    return PagesOnce(Sources([shelf])), shelf


def site(locator: str | None) -> Site:
    return Site(
        kind=SiteKind.LOCATOR,
        block="b1",
        handle="sec:setup",
        key=KEY,
        locator=locator,
        words="\\autocite",
        text="",
        file="paper.md",
        line=5,
    )


class TestSupportedForms:
    def test_a_result_the_source_prints_is_located(self):
        found = check(reader({44: LABEL_PAGE})[0], site(THEOREM))
        assert found.outcome is Outcome.FOUND
        assert found.page_index == 44
        assert "2.1.24" in found.line

    def test_a_miss_over_a_long_source_is_not_printed(self):
        found = check(reader(long_pages(10))[0], site(THEOREM))
        assert found.outcome is Outcome.NOT_PRINTED
        assert "no page" in found.note
        assert found.pages == 10

    def test_a_match_names_the_page_that_prints_the_label(self):
        pages = {**long_pages(11), 12: LABEL_PAGE}
        found = check(reader(pages)[0], site(THEOREM))
        assert found.outcome is Outcome.FOUND
        assert found.page_index == 12

    def test_a_miss_over_a_short_source_is_below_the_floor(self):
        found = check(reader(long_pages(7))[0], site(THEOREM))
        assert found.outcome is Outcome.INCONCLUSIVE
        assert "below the 10-page floor" in found.note

    def test_a_match_in_a_short_source_is_still_located(self):
        pages = {n: LABEL_PAGE for n in range(1, 5)}
        found = check(reader(pages)[0], site(THEOREM))
        assert found.outcome is Outcome.FOUND

    def test_the_nine_ten_page_boundary(self):
        assert NEGATIVE_PAGE_FLOOR == 10
        nine = check(reader(long_pages(9))[0], site(THEOREM))
        ten = check(reader(long_pages(10))[0], site(THEOREM))
        assert nine.outcome is Outcome.INCONCLUSIVE
        assert ten.outcome is Outcome.NOT_PRINTED


class TestUnsupportedForms:
    """No read is made: a provider that raised would show as an outage."""

    def unread(self, locator: str) -> object:
        down = PagesOnce(Sources([Shelf(down=True)]))
        return check(down, site(locator))

    def test_a_page_locator_is_not_checked(self):
        found = self.unread("p.~43")
        assert found.outcome is Outcome.NOT_CHECKED
        assert "page locator" in found.note
        assert found.pages == 0

    def test_a_range_is_never_tested_by_its_first_label(self):
        found = self.unread("Theorem~2.2.3-2.2.5")
        assert found.outcome is Outcome.NOT_CHECKED
        assert "first label" in found.note

    def test_an_equation_locator_is_not_checked(self):
        found = self.unread("Equation~2.2.6")
        assert found.outcome is Outcome.NOT_CHECKED
        assert "equation" in found.note

    def test_an_unparsed_locator_is_not_checked(self):
        found = self.unread("see also the discussion")
        assert found.outcome is Outcome.NOT_CHECKED
        assert "unparsed" in found.note

    def test_no_locator_is_not_checked(self):
        found = self.unread("")
        assert found.outcome is Outcome.NOT_CHECKED
        assert found.note == "no locator"


class TestUnreadableAndOutage:
    def test_a_page_with_no_text_cannot_produce_a_miss(self):
        found = check(reader({1: ""})[0], site(THEOREM))
        assert found.outcome is Outcome.INCONCLUSIVE
        assert "no text to read" in found.note

    def test_a_short_page_with_text_does_not_block_a_miss(self):
        """Almost every book has a half-title or a dedication. A page whose
        text was read is evidence about that text, however short."""
        pages = {1: "To Daphne and Naomi", **long_pages(10, first=2)}
        found = check(reader(pages)[0], site(THEOREM))
        assert found.outcome is Outcome.NOT_PRINTED
        assert "1 page under the text floor was searched as text" in found.note
        assert "page index 1" in found.note

    def test_one_textless_page_blocks_even_when_another_was_searched(self):
        pages = {1: "To Daphne and Naomi", 2: "", **long_pages(10, first=3)}
        found = check(reader(pages)[0], site(THEOREM))
        assert found.outcome is Outcome.INCONCLUSIVE
        assert "page index 2" in found.note

    def test_a_transient_copy_is_unknown_and_names_provenance(self):
        found = check(
            reader({1: LABEL_PAGE}, origin=Origin.TRANSIENT)[0], site(THEOREM)
        )
        assert found.outcome is Outcome.INCONCLUSIVE
        assert "version of record" in found.note
        assert found.pages == 0

    def test_an_unreachable_corpus_is_an_outage(self):
        down = PagesOnce(Sources([Shelf(down=True)]))
        found = check(down, site(THEOREM))
        assert found.outcome is Outcome.INCONCLUSIVE
        assert found.note.startswith("cannot tell whether a source holds brown2010")

    def test_a_key_not_in_the_corpus_is_an_outage(self):
        found = check(reader(long_pages(12), key="other")[0], site(THEOREM))
        assert found.outcome is Outcome.INCONCLUSIVE
        assert "no source holds brown2010" in found.note


class TestReadingOnce:
    def test_a_run_reads_each_source_once(self):
        pages_once, shelf = reader({1: LABEL_PAGE})
        first = check(pages_once, site("Theorem~2.1.24"))
        second = check(pages_once, site("Theorem~9.9.9"))
        assert first.outcome is Outcome.FOUND
        assert second.outcome is Outcome.INCONCLUSIVE
        assert shelf.reads == [KEY]


def test_a_citation_s_locator_names_its_pages_at_no_cost() -> None:
    """The citation session's candidate pages: a page
    locator's own pages, read as written; a label's first places where the
    work prints it; nothing for a citation with no locator, and no search
    is made (a query would embed)."""
    from axiomation.sources.read.locate import cited_pages

    filler = "A page of the book without the label on it, long enough to read."
    shelf = Shelf(texts={KEY: {i: filler for i in range(1, 13)} | {7: LABEL_PAGE}})
    reader = Sources([shelf], placer=cited_pages)
    assert reader.places(KEY, "p.~43") == (43,)
    assert reader.places(KEY, THEOREM) == (7,)
    assert reader.places(KEY, None) == ()
    assert Sources([shelf]).places(KEY, THEOREM) == ()
