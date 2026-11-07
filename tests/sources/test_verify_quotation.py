"""The quotation verifier: the exact tier and the alignment.

Synthetic pages, so each tier has a case it must decide and the cost rules are
pinned to a measurement. Pages reach the verifier only through `Sources`,
which stamps each. The parser's quotation shapes, the filing and the queue
are tested elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

pytest.importorskip("qdrant_client")

from axiomation.evidence import Origin, Page, RawPage, Sources  # noqa: E402
from axiomation.records import Verdict  # noqa: E402
from axiomation.sites import Site, SiteKind  # noqa: E402
from axiomation.sources.verify import quotation  # noqa: E402

QUOTE = "the rate is sharp and bounded above by the constant"

#: Where a page's text came from, as the repair policy reads it.
OCR = "tesseract"
TEXT = "none"
UNKNOWN = ""

#: Appended to a page so its text clears the 50-character floor; the
#: alignment may start and end anywhere, so it costs nothing.
FILLER = " Further prose follows here so that the page has enough text."


@dataclass
class Shelf:
    """A provider of the protocol: each key's pages by index as `(text,
    text_source)`; `search` returns every page of the key (or of all)."""

    origin: Origin = Origin.LIBRARY
    texts: dict[str, dict[int, tuple[str, str]]] = field(default_factory=dict)
    down: bool = False
    #: it searches, as the library does
    searches = True
    #: a key held back, and why: its file is not its entry's work
    mismatched: dict[str, str] = field(default_factory=dict)
    #: each key's pages its OCR's witness left disputed, and how many findings
    disputed: dict[str, dict[int, int]] = field(default_factory=dict)

    def holds(self, key: str) -> bool:
        if self.down:
            raise ConnectionError("the shelf is down")
        return key in self.texts

    def held(self, key: str) -> str:
        return self.mismatched.get(key, "")

    def pages(self, key: str, start: int, end: int | None) -> list[RawPage]:
        if self.down:
            raise ConnectionError("the shelf is down")
        held = self.texts.get(key, {})
        last = max(held, default=0) if end is None else end
        marks = self.disputed.get(key, {})
        return [
            RawPage(
                key, i, held[i][0], text_source=held[i][1], disputed=marks.get(i, 0)
            )
            for i in sorted(held)
            if start <= i <= last
        ]

    def search(self, query: str, key: str | None, top_k: int) -> list[RawPage]:
        if self.down:
            raise ConnectionError("the shelf is down")
        return [
            RawPage(
                k,
                i,
                text,
                text_source=source,
                score=1.0 / i,
                disputed=self.disputed.get(k, {}).get(i, 0),
            )
            for k, held in self.texts.items()
            if key in (None, k)
            for i, (text, source) in sorted(held.items())
        ][:top_k]


def page(
    text: str, *, index: int = 1, source: str = UNKNOWN, bare: bool = False
) -> tuple[int, tuple[str, str]]:
    return index, (text if bare else text + FILLER, source)


def sources_for(*pages: tuple[int, tuple[str, str]], key: str = "brown2010") -> Sources:
    return Sources([Shelf(texts={key: dict(pages)})])


def site(
    text: str,
    *,
    key: str = "brown2010",
    locator: str | None = None,
    line: int = 10,
) -> Site:
    return Site(
        kind=SiteKind.QUOTATION,
        block="b1",
        handle="sec:setup",
        key=key,
        locator=locator,
        words=text,
        text=text,
        file="paper.md",
        line=line,
    )


class TestExactTier:
    def test_an_exact_quotation_is_verified(self):
        check = quotation.check(
            sources_for(page(f"Prose before. {QUOTE}. Prose after.")), site(QUOTE)
        )
        assert check.verdict is Verdict.VERIFIED
        assert check.note == "exact"
        assert check.page_index == 1
        assert "page index 1" in check.evidence
        assert check.dropped == 0


class TestAlignment:
    def test_a_substituted_word_is_not_shown_with_the_diff(self):
        changed = "the rate is flat and bounded above by the constant"
        check = quotation.check(sources_for(page(changed)), site(QUOTE))
        assert check.verdict is Verdict.NOT_SHOWN
        assert check.note == 'word 4: "sharp" in the paper, "flat" on the page'

    def test_a_difference_on_a_disputed_page_is_left_to_a_reader(self):
        """The OCR may have left words out: the page is read as an image."""
        changed = "the rate is flat and bounded above by the constant"
        shelf = Shelf(
            texts={"brown2010": dict([page(changed, source="paddle")])},
            disputed={"brown2010": {1: 1}},
        )
        check = quotation.check(Sources([shelf]), site(QUOTE))
        assert check.verdict is None
        assert "read the page as an image" in check.note

    def test_a_quotation_a_disputed_page_carries_is_verified(self):
        shelf = Shelf(
            texts={"brown2010": dict([page(QUOTE, source="paddle")])},
            disputed={"brown2010": {1: 1}},
        )
        check = quotation.check(Sources([shelf]), site(QUOTE))
        assert check.verdict is Verdict.VERIFIED

    def test_a_dropped_qualifier_is_not_shown_and_names_it(self):
        source = "the rate is not sharp and bounded above by the constant"
        check = quotation.check(sources_for(page(source)), site(QUOTE))
        assert check.verdict is Verdict.NOT_SHOWN
        assert check.note == 'after word 3: "not" on the page, nothing in the paper'

    def test_scattered_words_are_not_verified(self):
        scattered = (
            "the rate is here and there sharp and also bounded above by the "
            "constant with extra words everywhere around them"
        )
        check = quotation.check(sources_for(page(scattered)), site(QUOTE))
        assert check.verdict is not Verdict.VERIFIED

    def test_a_paraphrase_is_left_for_a_reader(self):
        other = "a completely different sentence about something unrelated here"
        check = quotation.check(sources_for(page(other)), site(QUOTE))
        assert check.verdict is None
        assert check.note

    def test_inline_mathematics_is_dropped_and_counted(self):
        quoted = "The value $x^2$ is bounded above by the constant"
        rendered = "The value x2 is bounded above by the constant"
        check = quotation.check(sources_for(page(rendered)), site(quoted))
        assert check.verdict is Verdict.VERIFIED
        assert "1 spans dropped" in check.note
        assert check.dropped == 1

    def test_exact_is_tried_before_tolerant(self):
        check = quotation.check(sources_for(page(QUOTE)), site(QUOTE))
        assert check.note == "exact"

    def test_a_footnote_mark_and_a_reference_are_skipped(self):
        rendered = "the rate † is sharp and [12] bounded above by the constant"
        check = quotation.check(sources_for(page(rendered)), site(QUOTE))
        assert check.verdict is Verdict.VERIFIED
        assert "0 words repaired" in check.note


class TestMarksAndBreaks:
    """What the paper and the page print differently without a word
    changing: the quotation marks, the punctuation at a word's ends, a word
    broken over a line."""

    def test_typographic_quotes_and_apostrophes_are_the_plain_ones(self):
        quoted = 'Euler\'s "law of small gaps" holds for every modulus'
        printed = "Euler\u2019s \u201claw of small gaps\u201d holds for every modulus"
        check = quotation.check(sources_for(page(printed, source=TEXT)), site(quoted))
        assert check.verdict is Verdict.VERIFIED
        assert check.note == "exact"

    def test_latex_quotes_are_the_plain_ones(self):
        quoted = "Euler's ``law of small gaps'' holds for every modulus"
        printed = "Euler\u2019s \u201claw of small gaps\u201d holds for every modulus"
        check = quotation.check(sources_for(page(printed, source=TEXT)), site(quoted))
        assert check.verdict is Verdict.VERIFIED
        assert check.note == "exact"

    def test_a_full_stop_at_a_word_s_end_is_no_changed_word(self):
        """An elided quotation is aligned, and the page's `constant.` ends
        its sentence: the word is the paper's `constant`."""
        quoted = "the rate is sharp ... bounded above by the constant"
        check = quotation.check(
            sources_for(page(QUOTE + ".", source=TEXT)), site(quoted)
        )
        assert check.verdict is Verdict.VERIFIED
        assert check.note.startswith("tolerant")

    def test_punctuation_at_both_ends_is_stripped_on_either_side(self):
        quoted = "the rate is (sharp) and bounded above by the constant,"
        check = quotation.check(sources_for(page(QUOTE, source=TEXT)), site(quoted))
        assert check.verdict is Verdict.VERIFIED

    def test_a_changed_word_with_a_full_stop_is_still_changed(self):
        changed = "the rate is sharp and bounded above by the limit."
        check = quotation.check(sources_for(page(changed, source=TEXT)), site(QUOTE))
        assert check.verdict is Verdict.NOT_SHOWN
        assert check.note == 'word 10: "constant" in the paper, "limit." on the page'

    def test_a_word_broken_over_a_line_is_joined(self):
        printed = "the rate is sharp and boun-\nded above by the con-\n  stant"
        check = quotation.check(sources_for(page(printed, source=TEXT)), site(QUOTE))
        assert check.verdict is Verdict.VERIFIED
        assert check.note == "exact"

    def test_a_compound_broken_inside_a_part_keeps_its_hyphen(self):
        quoted = "the most-likely rate is bounded above by the constant"
        printed = "the mo-\nst-likely rate is bounded above by the constant"
        check = quotation.check(sources_for(page(printed, source=TEXT)), site(quoted))
        assert check.verdict is Verdict.VERIFIED

    def test_a_compound_broken_at_its_own_hyphen_reads_either_way(self):
        """The page cannot tell `well-` then `posed` from a word broken in
        two: the quotation's `well-posed` and `wellposed` both match it."""
        printed = "the problem is well-\nposed and bounded above by the constant"
        for quoted in (
            "the problem is well-posed and bounded above by the constant",
            "the problem is wellposed and bounded above by the constant",
        ):
            check = quotation.check(
                sources_for(page(printed, source=TEXT)), site(quoted)
            )
            assert check.verdict is Verdict.VERIFIED, quoted
            assert check.note == "exact"
        aligned = quotation.check(
            sources_for(page(printed, source=TEXT)),
            site("the problem is well-posed ... bounded above by the constant"),
        )
        assert aligned.verdict is Verdict.VERIFIED
        assert "wellposed" in aligned.evidence and "\x02" not in aligned.evidence

    def test_the_paper_s_hyphen_at_a_line_s_end_is_its_own(self):
        """The paper's source wraps a line where its author likes, never
        inside a word: `well-` at a line's end is the author's hyphen."""
        quoted = "the problem is well-\nposed and bounded above by the constant"
        printed = "the problem is well-posed and bounded above by the constant"
        check = quotation.check(sources_for(page(printed, source=TEXT)), site(quoted))
        assert check.verdict is Verdict.VERIFIED
        assert check.note == "exact"
        elided = "the problem is well-\nposed ... bounded above by the constant"
        check = quotation.check(sources_for(page(printed, source=TEXT)), site(elided))
        assert check.verdict is Verdict.VERIFIED

    @pytest.mark.parametrize(
        ("paper", "printed"),
        [
            ("-1", "1"),
            ("+1", "1"),
            ("50%", "50"),
            ("§3", "3"),
            ("bounded?", "bounded."),
            ("bounded!", "bounded"),
        ],
    )
    def test_a_mark_that_carries_meaning_is_a_changed_word(self, paper, printed):
        quoted = f"the rate is sharp and {paper} above by the constant"
        source = f"the rate is sharp and {printed} above by the constant"
        check = quotation.check(sources_for(page(source, source=TEXT)), site(quoted))
        assert check.verdict is Verdict.NOT_SHOWN
        assert check.note.startswith(f'word 6: "{paper}" in the paper')

    def test_nested_quotation_marks_and_brackets_are_quoting(self):
        quoted = "the rate is 'sharp' and [bounded] above by the constant"
        printed = 'the rate is "sharp" and (bounded) above by the constant'
        check = quotation.check(sources_for(page(printed, source=TEXT)), site(quoted))
        assert check.verdict is None  # an editorial bracket is the reader's
        quoted = "the rate is 'sharp' and (bounded) above by the constant;"
        check = quotation.check(sources_for(page(printed, source=TEXT)), site(quoted))
        assert check.verdict is Verdict.VERIFIED

    def test_a_hyphen_inside_a_line_stays(self):
        """Only a break at a line's end joins: `sharp-and` on one line is
        not `sharpand`."""
        printed = "the rate is sharp-and bounded above by the constant"
        check = quotation.check(sources_for(page(printed, source=TEXT)), site(QUOTE))
        assert check.verdict is not Verdict.VERIFIED


class TestRepairs:
    def test_a_mark_read_inside_a_word_is_a_repair(self):
        rendered = "the rate is sharp and b0unded above by the constant"
        check = quotation.check(sources_for(page(rendered, source=OCR)), site(QUOTE))
        assert check.verdict is Verdict.VERIFIED
        assert "1 words repaired" in check.note

    def test_a_letter_swap_on_an_ocr_page_is_left_to_a_reader(self):
        """`rn` read as `m` is OCR's commonest slip, and `modern`/`modem` is
        why it is not free: the swap can make one real word of another."""
        quoted = "the corner is bounded above by the constant"
        rendered = "the comer is bounded above by the constant"
        check = quotation.check(sources_for(page(rendered, source=OCR)), site(quoted))
        assert check.verdict is None
        assert "changed word" in check.note

    def test_not_read_as_no_is_never_a_repair(self):
        quoted = "the rate is not sharp and bounded above by the constant"
        rendered = "the rate is no. sharp and bounded above by the constant"
        on_ocr = quotation.check(sources_for(page(rendered, source=OCR)), site(quoted))
        assert on_ocr.verdict is None
        on_text = quotation.check(
            sources_for(page(rendered, source=TEXT)), site(quoted)
        )
        assert on_text.verdict is Verdict.NOT_SHOWN

    def test_a_diacritic_is_a_different_word_on_a_text_layer_page(self):
        quoted = "the Holder bound is sharp above by the constant"
        rendered = "the Hölder bound is sharp above by the constant"
        on_text = quotation.check(
            sources_for(page(rendered, source=TEXT)), site(quoted)
        )
        assert on_text.verdict is Verdict.NOT_SHOWN
        assert '"Holder" in the paper, "Hölder" on the page' in on_text.note
        on_ocr = quotation.check(sources_for(page(rendered, source=OCR)), site(quoted))
        assert on_ocr.verdict is None

    def test_real_word_changes_do_not_verify_on_an_ocr_page(self):
        for quoted, rendered in (
            ("bounded", "rounded"),
            ("not", "now"),
            ("increasing", "decreasing"),
        ):
            check = quotation.check(
                sources_for(page(f"the value is {rendered} here", source=OCR)),
                site(f"the value is {quoted} here"),
            )
            assert check.verdict is not Verdict.VERIFIED, (quoted, rendered)

    def test_the_same_difference_is_not_shown_on_a_text_layer_page(self):
        quoted = "the corner is bounded above by the constant"
        rendered = "the comer is bounded above by the constant"
        check = quotation.check(sources_for(page(rendered, source=TEXT)), site(quoted))
        assert check.verdict is Verdict.NOT_SHOWN

    def test_the_same_difference_is_unsettled_without_a_recorded_source(self):
        quoted = "the corner is bounded above by the constant"
        rendered = "the comer is bounded above by the constant"
        check = quotation.check(
            sources_for(page(rendered, source=UNKNOWN)), site(quoted)
        )
        assert check.verdict is None
        assert "unknown origin" in check.note

    def test_the_confusion_table(self):
        assert quotation._ocr_shape("corner", "comer")  # rn -> m
        assert quotation._ocr_shape("d", "cl")  # cl -> d
        assert quotation._ocr_shape("w", "vv")  # vv -> w
        assert quotation._ocr_shape("bounded", "b0unded")  # a mark between letters
        assert quotation._ocr_shape("cafe", "café")  # a diacritic
        assert quotation._ocr_shape("l", "I")
        assert not quotation._ocr_shape("bounded", "rounded")
        assert not quotation._ocr_shape("not", "now")
        assert not quotation._ocr_shape("increasing", "decreasing")

    def test_only_an_unreadable_letter_is_free(self):
        assert quotation._is_repair("bounded", "b0unded", "ocr")
        assert quotation._is_repair("flow", "f1ow", "ocr")
        assert not quotation._is_repair("bounded", "b0unded", "text")
        assert not quotation._is_repair("bounded", "b0unded", "unknown")
        for paper, on_page in (
            ("corner", "comer"),  # rn -> m, and modern/modem
            ("be", "he"),  # h/b
            ("cat", "eat"),  # e/c
            ("flat", "fiat"),  # l/i
            ("schön", "schon"),  # a diacritic
            ("not", "no."),  # a mark at the edge of a word
            ("abound", "0bound"),  # a mark at the start
            ("b0und3d", "bounded"),  # the paper's own digits
        ):
            assert not quotation._is_repair(paper, on_page, "ocr"), (paper, on_page)


class TestMathSpan:
    def test_a_span_absorbs_formula_tokens(self):
        quoted = "The value $\\alpha+\\beta$ is bounded above"
        rendered = "The value α + β is bounded above"
        check = quotation.check(sources_for(page(rendered)), site(quoted))
        assert check.verdict is Verdict.VERIFIED
        assert "1 spans dropped" in check.note

    def test_a_span_does_not_absorb_a_word(self):
        assert quotation._absorbable("log")
        assert quotation._absorbable("x2")
        assert not quotation._absorbable("not")

    def test_the_absorption_bound_is_pinned(self):
        assert quotation.ABSORB_BOUND == 3
        quoted = "the $x$ end"
        three = quotation.check(sources_for(page("the a b c end")), site(quoted))
        assert three.verdict is Verdict.VERIFIED
        four = quotation.check(sources_for(page("the a b c d end")), site(quoted))
        assert four.verdict is not Verdict.VERIFIED


class TestElision:
    def test_an_honest_ellipsis_is_verified_and_counted(self):
        quoted = "the rate is ... and bounded above"
        rendered = "the rate is sharp and then some and bounded above"
        check = quotation.check(sources_for(page(rendered)), site(quoted))
        assert check.verdict is Verdict.VERIFIED
        assert "1 elisions" in check.note

    def test_a_short_ellipsis_segment_is_unsettled(self):
        quoted = "the ... and bounded above"
        rendered = "the rate is sharp and then some and bounded above"
        check = quotation.check(sources_for(page(rendered)), site(quoted))
        assert check.verdict is None
        assert "elision" in check.note

    def test_an_editorial_bracket_is_unsettled_unless_exact(self):
        quoted = "the rate is [the] sharp and bounded above"
        rendered = "the rate is the sharp and bounded above"
        check = quotation.check(sources_for(page(rendered)), site(quoted))
        assert check.verdict is None
        assert "bracket" in check.note
        exact = quotation.check(sources_for(page(quoted)), site(quoted))
        assert exact.verdict is Verdict.VERIFIED
        assert exact.note == "exact"


class TestUnreadablePages:
    def test_a_page_under_the_floor_is_unsettled_not_a_miss(self):
        """An unreadable page is not "the work does not say it";
        the verifier files nothing for it, so the verdict is None with a note."""
        check = quotation.check(sources_for(page("", bare=True)), site(QUOTE))
        assert check.verdict is None
        assert "no page above the text floor" in check.note

    def test_a_failed_ocr_page_is_unsettled_not_a_miss(self):
        check = quotation.check(
            sources_for(page(f"{QUOTE}.", source="failed", bare=True)), site(QUOTE)
        )
        assert check.verdict is None

    def test_an_unreachable_corpus_files_nothing(self):
        check = quotation.check(Sources([Shelf(down=True)]), site(QUOTE))
        assert check.verdict is None

    def test_a_key_no_source_holds_is_unsettled(self):
        check = quotation.check(sources_for(page(QUOTE), key="other"), site(QUOTE))
        assert check.verdict is None
        assert check.note == "no page the work holds"

    def test_a_key_no_source_holds_is_named_when_read_by_page(self):
        check = quotation.check(
            sources_for(page(QUOTE), key="other"), site(QUOTE, locator="p.~3")
        )
        assert check.verdict is None
        assert "no source holds brown2010" in check.note


class TestPageLocator:
    def test_a_page_locator_is_read_directly(self):
        check = quotation.check(
            sources_for(page(f"{QUOTE}.", index=43)),
            site(QUOTE, locator="p.~43"),
        )
        assert check.verdict is Verdict.VERIFIED
        assert check.how == "locator"
        assert check.page_index == 43


class TestWordAlignment:
    def test_a_missing_quotation_word_is_reported(self):
        align = quotation._align(
            ["the", "rate", "is", "sharp"], ["the", "rate", "is", "flat"], OCR
        )
        assert align.cost == 1
        assert align.edits == (quotation._Edit(False, 4, "sharp", "flat"),)

    def test_an_omitted_page_word_is_reported(self):
        align = quotation._align(
            ["the", "rate", "is", "sharp"],
            ["the", "rate", "is", "not", "sharp"],
            OCR,
        )
        assert align.cost == 1
        assert align.edits == (quotation._Edit(True, 3, None, "not"),)

    def test_ties_go_to_the_earlier_position(self):
        align = quotation._align(["alpha"], ["alpha", "beta", "alpha"], OCR)
        assert align.cost == 0
        assert align.start == 0
        assert align.span == "alpha"


class TestStandsOn:
    """A reader's quote held against the page it names, under the tiers a
    quotation site's is verified by."""

    @staticmethod
    def on(text: str, source: str) -> Page:
        return Page("brown2010", "", 1, "", text + FILLER, text_source=source)

    def test_exact_words_stand(self):
        assert quotation.stands_on(QUOTE, self.on(QUOTE, TEXT))

    def test_an_ocr_repair_stands_on_a_page_a_machine_read(self):
        page = self.on("the rate is sharp and b0unded above by the constant", OCR)
        assert quotation.stands_on(QUOTE, page)

    def test_the_same_difference_on_a_text_layer_does_not(self):
        page = self.on("the rate is sharp and b0unded above by the constant", TEXT)
        assert not quotation.stands_on(QUOTE, page)

    def test_a_different_word_does_not(self):
        page = self.on("the rate is flat and bounded above by the constant", OCR)
        assert not quotation.stands_on(QUOTE, page)

    def test_a_line_end_hyphen_copied_with_a_space_stands(self):
        page = self.on(
            "noise can lead to bounds that are too wide in the long-\nrun.", TEXT
        )
        assert quotation.stands_on("bounds that are too wide in the long- run", page)
        assert quotation.stands_on("bounds that are too wide in the long-run", page)
