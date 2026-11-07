"""An OCR reading held against its witnesses: what is found, and what is not.

The texts are written for the tests: an OCR model's faults are fluent, so
each case pairs a reading with what an independent reader saw on the page.
"""

from __future__ import annotations

import pytest

from axiomation.sources.ingest import layer
from axiomation.sources.ingest.witness import (
    PASS_SCALES,
    Findings,
    Seen,
    Witnessed,
    collapse_leaders,
    find,
    find_blocks,
    held_share,
    paragraphs,
    settle,
)

PROSE = (
    "The series converges for every value of the parameter below one, "
    "and its partial sums stay bounded as the second lemma shows."
)
MORE = (
    "A bound on the remainder follows from comparing each term with the "
    "geometric series whose ratio is the parameter itself."
)
#: Tesseract reads the page with a misread letter here and there.
WITNESS = (
    "The series converges for every va1ue of the parameter below one,\n"
    "and its partial sums stay bounded as the second lemma shows.\n"
    "A bound on the remainder follows from comparing each tenn with the\n"
    "geometric series whose ratio is the parameter itself."
)
#: The witness of a page that holds `PROSE` alone.
WITNESS_OF_PROSE = WITNESS.split("\nA bound")[0]


@pytest.fixture(autouse=True)
def known(monkeypatch: pytest.MonkeyPatch) -> None:
    """A word list of the cases' words, as the system's would hold them, so
    no case rests on the machine's own list."""
    words = frozenset(
        w.casefold().strip(".,")
        for w in f"{PROSE} {MORE} {CONTENTS_TEXT}".split()
        if len(w) >= 4
    )
    monkeypatch.setattr(layer, "word_list", lambda: words)


CONTENTS = [
    ("Divergent series and their summation", 1),
    ("Abel's theorem on power series", 9),
    ("Tauberian conditions for convergence", 17),
    ("Fourier series and their sums", 25),
    ("Euler summation of slowly divergent series", 33),
    ("Asymptotic expansions of integrals", 41),
    ("Hypergeometric functions and contour integrals", 49),
    ("Bernoulli numbers and polynomials", 57),
]
CONTENTS_TEXT = "\n".join(f"{title} . . . . . . {page}" for title, page in CONTENTS)


def test_a_reading_the_witness_holds_has_no_findings() -> None:
    assert find(f"{PROSE}\n\n{MORE}", [WITNESS]) == Findings()


def test_a_made_up_paragraph_is_unheld() -> None:
    made_up = (
        "Maximum likelihood methods are the best methods for estimating "
        "the general model, as is established in the literature on testing."
    )

    found = find(f"{PROSE}\n\n{made_up}", [WITNESS])

    assert found.unheld == (
        "maximum likelihood methods are the best methods for estimating "
        "the general model",
    )
    assert not found.broken


def test_a_formula_paragraph_and_a_short_line_are_not_judged() -> None:
    formula = r"$$\sum_{n=1}^{\infty} a_n x^n = \frac{a_1 x}{1 - r x} \quad (12)$$"
    heading = "Convergence of the series"

    witness = WITNESS_OF_PROSE

    assert find(f"{heading}\n\n{formula}\n\n{PROSE}", [witness]) == Findings()


def test_formulas_are_cut_in_every_delimiter_before_words_are_counted() -> None:
    mineru = (
        r"The series converges for every value of the parameter \(r < 1\) "
        r"below one, and its partial sums \[ S_n = \sum_{k} a_k \] stay "
        "bounded as the second lemma shows."
    )

    assert find(mineru, [WITNESS_OF_PROSE]) == Findings()


def test_a_sentence_written_twice_is_a_repeat() -> None:
    found = find(f"{PROSE}\n\n{PROSE} {MORE}", [WITNESS])

    assert found.repeats == (
        "the series converges for every value of the parameter below",
    )
    assert found.broken


def test_a_page_that_repeats_itself_is_held_where_the_witness_repeats_it() -> None:
    """Two exercises open alike; the witness misreads a word in one copy."""
    exercise = (
        "Suppose that a sequence with limiting mean m is altered by "
        "changing the weight of each term in the block."
    )
    witnessed = exercise.replace("limiting", "1imiting", 1)

    found = find(f"3. {exercise}\n\n4. {exercise}", [f"3. {exercise}\n4. {witnessed}"])

    assert found.repeats == ()


def test_a_witness_s_dollar_signs_cut_none_of_its_prose() -> None:
    """The page states a condition twice; Tesseract misreads two subscripts
    as `$`, and the prose between them stays its own, the second copy
    with it."""
    condition = (
        "is a sequence of the bounded kind with its index being a "
        "monotone weight function"
    )
    reading = (
        rf"the model \( S_N \) {condition}. One checks that this holds if "
        rf"and only if \( S = S_1 \) {condition}."
    )
    witness = (
        f"the model Sy {condition}. One checks that this holds if\n"
        f"and only if S = S$, {condition}.\nfor a single term $ and"
    )

    assert find(reading, [witness]).repeats == ()


def test_a_reference_written_twice_is_a_repeat() -> None:
    """Its words of three letters are fewer than the run: initials and
    numbers count."""
    reference = (
        "[5] A. Bergen, C. Lund, R. M. Ostrow, and T. Vale, "
        "Arch. Gen. Math. 41, 207 (2011)."
    )

    found = find(f"{reference}{reference}", [reference])

    assert found.repeats


def test_sentences_alike_but_for_their_formulas_are_no_repeat() -> None:
    """A paper says the same of two quantities, each after its displayed
    formula; with the formulas cut the two sentences would be one stretch
    written twice, while a witness garbles each formula into words of its
    own and so holds that stretch twice nowhere."""
    reading = (
        r"for the gain where \[ g_a(t) = e^{2 \lambda a t} \tag{4} \] Here "
        r"\( g_a \), however, is the bound of the weighted sum "
        r"\[ S = -a \, d/dt \tag{5} \] taken at the step a."
        "\n\n"
        r"for the mean where \[ h_b(t) = \theta(t - b) \tag{8} \] Here "
        r"\( h_b \), however, is the bound of the weighted sum "
        r"\[ T = t \tag{9} \] taken at the step b."
    )
    witness = (
        "for the gain where ™~ ga(t) = E~ 2Ai At) (z-z0) v, (4) Here ¥, "
        "however, is the bound of the weighted sum S=(a/2ri)d/dt, (5) "
        "taken at the step a.\nfor the mean where hg (t) = 0(t— b) (8) "
        "Here gz, however, is the bound of the weighted sum T = t (9) "
        "taken at the step b."
    )

    assert find(reading, [witness]).repeats == ()


def test_a_passage_written_twice_with_its_formulas_is_a_repeat() -> None:
    """A model writing a heading twice writes its formulas alike, and the
    formulas count toward the run."""
    heading = r"THE FUNCTION \( \phi \) AND THE CANONICAL CHAIN. When \( n > 2 \), the"
    reading = f"{heading} mean value of the rate.\n\n{heading} largest rate."
    witness = (
        "THE FUNCTION » AND THE CANONICAL CHAIN. When n > 2, the mean value "
        "of the rate."
    )

    (repeat,) = find(reading, [witness]).repeats

    assert repeat.startswith("the function formula")
    assert repeat.endswith(" the")


def test_a_statement_the_page_prints_twice_with_its_formulas_is_held() -> None:
    """Two items of a list say the same with formulas alike; the witness
    reads both, each formula garbled its own way, so it holds the words
    twice and no formula once."""
    item = (
        r"If \( g \) is \( \lambda \) monotone, then \( g^{-1}(A) \) is "
        r"\( \lambda \) ordered whenever \( A \) is a"
    )
    reading = f"(1) {item} closed subset of Y.\n\n(3) {item} compact subset of Y."
    witness = (
        "(1) If g is & monotone, then g ~\\(A) is @ ordered whenever A is a "
        "closed subset of Y.\n(3) If g is 6 monotone, then g~1(A) is @ "
        "ordered whenever A is a compact subset of Y."
    )

    assert find(reading, [witness]).repeats == ()


def test_a_table_of_letters_printed_twice_is_no_repeat() -> None:
    """Two exercises print one transformation, its states a row of letters
    over a row of letters, which a witness reads across the page's
    columns; a stretch of so few words is no prose written twice."""
    table = "↓ a b c d e\n  b d a e c"
    reading = f"{PROSE}\n\n{table}\n\n{MORE}\n\n{table}"

    assert find(reading, [WITNESS]).repeats == ()


def test_a_loop_is_found_whatever_the_witness_holds() -> None:
    loop = "$x = (" + "0 , " * 12 + "1)$"

    found = find(f"{PROSE}\n\n{loop}", [WITNESS])

    assert found.loop == "0 ,"
    assert found.broken


def test_a_number_written_out_is_no_loop() -> None:
    assert find(f"{PROSE}\n\n1000000000000", [WITNESS_OF_PROSE]).loop == ""


def test_a_table_s_row_of_empty_cells_is_no_loop() -> None:
    """A matrix prints a dot for each zero; its cells' tags are layout."""
    row = "<tr><td>1</td>" + "<td>.</td>" * 9 + "</tr>"
    table = f"<table>{row * 3}</table>"

    assert find(f"{PROSE}\n\n{table}", [WITNESS_OF_PROSE]).loop == ""


def test_a_table_of_one_formula_written_alike_is_no_repeat() -> None:
    """Cells that each write the same formula (a row's dots) hold no word a
    witness writes: the stretch they make is not judged."""
    row = "<tr><td>1</td>" + r"<td>\( \ldots \)</td>" * 12 + "</tr>"
    table = f"<table>{row * 3}</table>"

    assert find(f"{PROSE}\n\n{table}", [WITNESS_OF_PROSE]).repeats == ()


def test_a_matrix_of_zeros_is_no_loop() -> None:
    """Each row's tags end its run of cells: nine zeros in three rows are
    no zero written nine times."""
    zeros = "<table>" + "<tr><td>0</td><td>0</td><td>0</td></tr>" * 3 + "</table>"

    assert find(f"{PROSE}\n\n{zeros}", [WITNESS_OF_PROSE]).loop == ""


def test_a_pipe_table_reads_as_an_html_table_does() -> None:
    """MinerU's server writes a table in Markdown's pipes: a row's end ends
    its run of cells, and a row of dots is marks, as the tags are."""
    zeros = "| 0 | 0 | 0 |\n| --- | --- | --- |\n" + "| 0 | 0 | 0 |\n" * 3
    dots = "| 1 |" + " . |" * 9 + "\n"

    assert find(f"{PROSE}\n\n{zeros}", [WITNESS_OF_PROSE]).loop == ""
    assert find(f"{PROSE}\n\n{dots * 3}", [WITNESS_OF_PROSE]).loop == ""


def test_leaders_are_neither_a_loop_nor_a_repeat() -> None:
    contents = "\n".join(
        f"Chapter {n}. On the series {'. ' * 40} {n * 7}" for n in range(1, 4)
    )
    bullets = (
        r"\begin{array}{c c c c c c c c c c} " + r"\bullet & " * 12 + r"\end{array}"
    )

    assert find(f"{contents}\n\n{bullets}", [contents]) == Findings()
    assert len(collapse_leaders(contents)) < len(contents) / 2


def test_a_label_with_a_changed_number_is_unheld() -> None:
    reading = f"LEMMA 2.2.2. {PROSE}"

    assert find(reading, [f"LEMMA 2.2, {WITNESS}"]).labels == ("LEMMA 2.2.2",)
    assert find(f"LEMMA 2.2. {PROSE}", [f"LEMMA 2.2, {WITNESS}"]).labels == ()


@pytest.mark.parametrize(
    ("label", "witnessed"),
    [
        ("COROLLARY 6", "Coro.iary 6"),
        ("Chapter 1. Gaussian measures", "Chapter |. Gaussian measures"),
        ("LEMMA 2.2.", "LEMMA 2. 2."),
        ("CHAPTER 1-RIEMANNIAN METRICS", "CHAPTER 1~RIEMANNIAN METRICS"),
    ],
)
def test_a_label_its_witness_misreads_is_held(label: str, witnessed: str) -> None:
    """Tesseract misreads small capitals and a label's digits; a witness
    that writes no label, or the same, is no witness against it."""
    assert find(f"{label}\n\n{PROSE}", [f"{witnessed}\n{WITNESS}"]).labels == ()


def test_a_label_is_held_against_the_labels_of_the_whole_page() -> None:
    """The witness numbers the page's other chapters too; a number the
    reading writes in another block is no number otherwise."""
    reading = f"Chapter II. Duality\n\n{PROSE}\n\nChapter III. Minimax\n\n{MORE}"
    witness = f"Chapter IJ. Duality\n{WITNESS_OF_PROSE}\nChapter III. Minimax"

    assert find(reading, [witness]).labels == ()


def test_inequalities_in_a_witness_are_words_not_a_tag() -> None:
    """Tesseract writes a formula in plain text: `<` and `>` around prose
    are not an HTML tag."""
    witness = f"Pr[y < 1] and Pr[y > 0]. {WITNESS}"

    assert find(f"{PROSE}\n\n{MORE}", [witness]) == Findings()


def test_without_a_witness_only_a_repeat_or_a_loop_is_found() -> None:
    made_up = (
        "Entirely different words about something nobody wrote on this page "
        "at all today."
    )

    assert find(made_up, []) == Findings()
    assert find(f"{PROSE} {PROSE}", []).repeats


def test_a_reading_is_ranked_by_what_its_witnesses_hold() -> None:
    made_up = "Maximum likelihood methods are the best methods for estimating models."

    assert held_share(PROSE, [WITNESS]) > held_share(made_up, [WITNESS])


# --- Text left out: a witness's words the reading does not hold ---


def test_a_reading_that_left_entries_out_is_found() -> None:
    """A block that runs to its leader and stops: the entries after it lost."""
    cut = "\n".join(CONTENTS_TEXT.splitlines()[:3])

    assert find(cut, [CONTENTS_TEXT]).left_out
    assert not find(CONTENTS_TEXT, [CONTENTS_TEXT]).left_out


def test_entries_left_out_between_a_witness_s_dollar_signs_are_found() -> None:
    lines = CONTENTS_TEXT.splitlines()
    witness = "\n".join([*lines[:3], f"$ {lines[3]}", *lines[4:], "$"])

    assert find("\n".join(lines[:3]), [witness]).left_out


def test_words_a_reading_sets_inside_its_formulas_are_not_left_out() -> None:
    """MinerU sets a sentence in a display as `\\text`; Tesseract reads it
    as prose."""
    displayed = (
        r"\[ \text{A bound on the remainder follows from comparing each term "
        r"with the geometric series} \quad \sum_k r^k \quad "
        r"\text{whose ratio is the parameter itself.} \]"
    )

    assert find(f"{PROSE}\n\n{displayed}", [WITNESS]) == Findings()


def test_operator_names_a_reading_sets_as_commands_are_not_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TeX writes `\\cosh` as a command; Tesseract reads the word "cosh"."""
    words = layer.word_list() | {"cosh", "sinh", "coth"}
    monkeypatch.setattr(layer, "word_list", lambda: words)
    displayed = r"\[ \cosh z - \sinh z \coth z + \cosh 2z \sinh 2z \coth 2z \]"
    written = "cosh z - sinh z coth z + cosh 2z sinh 2z coth 2z"

    reading = f"{PROSE}\n\n{displayed}\n\n{displayed}"
    witness = f"{WITNESS_OF_PROSE}\n{written}\n{written}"

    assert not find(reading, [witness]).left_out


def test_blocks_read_in_another_order_leave_nothing_out() -> None:
    """Two columns: the OCR reads one column after the other, the witness
    across them; every word is there."""
    assert not find(f"{MORE}\n\n{PROSE}", [f"{PROSE}\n{MORE}"]).left_out


def test_without_a_word_list_nothing_is_found_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(layer, "word_list", lambda: frozenset())
    cut = "\n".join(CONTENTS_TEXT.splitlines()[:3])

    assert not find(cut, [CONTENTS_TEXT]).left_out


# --- The passes: the page read again at another scale ---


class Passes:
    """The OCR's readings of the page at each scale it is asked for."""

    def __init__(self, *readings: str | list[Seen]) -> None:
        self.readings = list(readings)
        self.scales: list[float] = []

    def __call__(self, scale: float) -> tuple[Seen, ...]:
        self.scales.append(scale)
        reading = self.readings[len(self.scales) - 1]
        return paragraphs(reading) if isinstance(reading, str) else tuple(reading)


CLEAN = f"{PROSE}\n\n{MORE}"
LOOPED = f"{CLEAN}\n\n$(" + "0 , " * 12 + ")$"
MADE_UP = (
    f"{PROSE}\n\nMaximum likelihood methods are the best methods for "
    "estimating the general model, as is established in the literature."
)


def test_a_clean_page_is_read_once() -> None:
    passes = Passes()

    assert settle(paragraphs(CLEAN), [WITNESS], passes) == Witnessed(CLEAN, 0, 1)
    assert passes.scales == []


def test_the_second_pass_settles_a_fault_it_reads_clean() -> None:
    passes = Passes(CLEAN)

    assert settle(paragraphs(LOOPED), [WITNESS], passes) == Witnessed(CLEAN, 0, 2)
    assert passes.scales == [PASS_SCALES[0]]


def test_a_third_pass_only_for_what_the_second_leaves() -> None:
    passes = Passes(MADE_UP, CLEAN)

    assert settle(paragraphs(LOOPED), [WITNESS], passes) == Witnessed(CLEAN, 0, 3)
    assert passes.scales == list(PASS_SCALES)


def test_unsettled_the_likeliest_reading_stands_and_is_marked() -> None:
    """A looped reading is out; of the rest, the one the witness holds
    most; made-up text holds least."""
    passes = Passes(MADE_UP, MADE_UP)

    found = settle(paragraphs(LOOPED), [WITNESS], passes)

    assert (found.text, found.readings) == (MADE_UP, 3)
    assert found.disputed == 2  # the made-up paragraph, the one it replaced


def test_passes_agreeing_with_no_witness_holding_them_settle_nothing() -> None:
    """The passes are one model and may share an error."""
    found = settle(paragraphs(MADE_UP), [WITNESS], Passes(MADE_UP, MADE_UP))

    assert (found.text, found.disputed) == (MADE_UP, 2)


def test_a_pass_that_read_nothing_is_no_candidate() -> None:
    found = settle(paragraphs(MADE_UP), [WITNESS], Passes("", "  "))

    assert (found.text, found.disputed, found.readings) == (MADE_UP, 2, 1)


def test_the_kept_reading_has_its_leaders_collapsed() -> None:
    page = f"{PROSE}\n\nContents{' .' * 300} 7"

    assert settle(paragraphs(page), [WITNESS_OF_PROSE], Passes()).text == (
        collapse_leaders(page)
    )


# --- Each block against what its own box shows ---

#: A block's box on a page at 300 dpi, room for a few lines of print.
BOX = (100, 400, 2300, 600)
#: A box over a strip at the page's edge, room for part of one line.
STRIP = (0, 0, 360, 50)


def test_a_block_its_page_witness_misses_is_held_by_its_own_box() -> None:
    """Two columns: Tesseract reads the page across them, so the page's
    text holds the block out of order, and its box alone holds it."""
    across = " ".join(
        f"{a} {b}" for a, b in zip(PROSE.split(), MORE.split(), strict=False)
    )
    columns = [Seen(PROSE, BOX, witnesses=(PROSE,)), Seen(MORE, BOX, witnesses=(MORE,))]

    assert find(f"{PROSE}\n\n{MORE}", [across]).unheld
    assert find_blocks(columns, [across]) == Findings()


def test_a_short_block_no_witness_holds_any_of_is_unheld() -> None:
    made_up = Seen("Theorem on related algebraic expressions", BOX, witnesses=("",))

    found = find_blocks([Seen(PROSE), made_up], [WITNESS_OF_PROSE])

    assert found.unheld == ("theorem related algebraic expressions",)
    assert found.blocks == (1,)


def test_a_short_block_a_witness_holds_a_word_of_is_held() -> None:
    """A sideways stamp, a blackletter title: Tesseract misreads most of a
    short line but not all."""
    title = Seen("The Graduates of the University", BOX, witnesses=("She Graduates",))

    assert find_blocks([Seen(PROSE), title], [WITNESS_OF_PROSE]) == Findings()


#: A model's reply about a box it was given with no print in it.
REPLY = "The picture is too faint to make out any written content."


def test_a_block_its_box_shows_no_word_of_is_found_and_kept() -> None:
    """A margin's mark boxed as a figure, and a model's reply about it; or
    a cover's display type Tesseract cannot read. Either is found, neither
    dropped: an empty box is no ground to cut text."""
    reading = [
        Seen(PROSE, BOX, witnesses=(PROSE,)),
        Seen(REPLY, BOX, witnesses=("", "")),
    ]

    found = settle(reading, [WITNESS], Passes([], []))
    unseen = find_blocks(reading, [WITNESS]).unseen

    assert unseen == ("the picture too faint make out any written content",)
    # unheld too: no witness holds a word of it
    assert (found.text, found.disputed, found.dropped) == (f"{PROSE}\n\n{REPLY}", 2, 0)


def test_a_block_its_box_shows_no_word_of_sends_the_page_to_no_pass() -> None:
    """A cover's title in display type: a pass at another scale that leaves
    it out would settle the page without it, so its mark sends the page to
    no pass; the first reading stands, disputed."""
    title = Seen("Lectures on Measures and Series", BOX, witnesses=("", ""))
    reading = [title, Seen(PROSE, BOX, witnesses=(PROSE,))]
    passes = Passes([Seen(PROSE, BOX, witnesses=(PROSE,))])

    found = settle(
        reading, [f"Lectures on Measures and Series\n{WITNESS_OF_PROSE}"], passes
    )

    assert (found.text, found.disputed) == (f"{title.text}\n\n{PROSE}", 1)
    assert passes.scales == []


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(
            Seen(r"\[ \sum_k r^k = \frac{1}{1 - r} \]", BOX, witnesses=("",)),
            id="formula",
        ),
        pytest.param(Seen("xii", BOX, witnesses=("",)), id="page-number"),
        pytest.param(Seen(REPLY, BOX, witnesses=("Tbe pictnre", "")), id="misread"),
        pytest.param(Seen(REPLY, BOX), id="box-unread"),
        pytest.param(Seen(REPLY), id="no-box"),
    ],
)
def test_a_block_is_unseen_only_where_its_read_box_shows_no_word(block: Seen) -> None:
    """A formula and a page number Tesseract reads as nothing; a word it
    misreads is a word seen; a block whose box was not read, or that has
    none, is not judged by it."""
    assert (
        find_blocks([Seen(PROSE, BOX, witnesses=(PROSE,)), block], [WITNESS]).unseen
        == ()
    )


def test_a_block_its_box_has_no_room_for_is_dropped() -> None:
    """A strip at the page's edge boxed as a header, and a page of text
    written for it."""
    loop = Seen("of the property " * 60, STRIP)

    found = settle([loop, Seen(PROSE, BOX)], [WITNESS_OF_PROSE], Passes())

    assert found == Witnessed(PROSE, 0, 1, dropped=1)


def test_a_dropped_block_its_box_holds_words_of_is_found_and_read_again() -> None:
    """A worn scan: the model reads a block's print, then writes on past
    its box. Dropped, it would take its print with it; found, the page is
    read again, and a pass that reads the block in its room settles it."""
    looped = Seen(f"{PROSE} {'of the property ' * 400}", BOX, witnesses=(PROSE,))
    rest = Seen(MORE, BOX, witnesses=(MORE,))
    again = [Seen(PROSE, BOX, witnesses=(PROSE,)), rest]

    assert settle([looped, rest], [WITNESS], Passes(again)) == Witnessed(
        f"{PROSE}\n\n{MORE}", 0, 2
    )
    unsettled = settle(
        [looped, rest], [WITNESS], Passes([looped, rest], [looped, rest])
    )
    # its print left out too, where a witness reads the page well enough
    assert (unsettled.text, unsettled.disputed, unsettled.dropped) == (MORE, 2, 1)


def test_a_dropped_block_its_box_shares_only_a_short_word_with_is_unfound() -> None:
    """A loop of common words over a strip whose junk holds a `the`."""
    loop = Seen("the end of the " * 600, STRIP, witnesses=("irae the blatt", ""))

    found = settle(
        [loop, Seen(PROSE, BOX, witnesses=(PROSE,))], [WITNESS_OF_PROSE], Passes()
    )

    assert found == Witnessed(PROSE, 0, 1, dropped=1)


def test_a_dropped_block_its_box_holds_none_of_is_dropped_unfound() -> None:
    """A margin strip of the facing page: Tesseract reads its own junk
    there, none of the model's."""
    loop = Seen("Hikin' " + "H. " * 2000, STRIP, witnesses=("irae eta blatt", ""))

    found = settle(
        [loop, Seen(PROSE, BOX, witnesses=(PROSE,))], [WITNESS_OF_PROSE], Passes()
    )

    assert found == Witnessed(PROSE, 0, 1, dropped=1)


def test_a_block_is_dropped_only_beside_one_that_stands() -> None:
    crowded = Seen(PROSE, STRIP)

    found = settle([crowded], [WITNESS_OF_PROSE], Passes())

    assert (found.text, found.dropped) == (PROSE, 0)


def test_a_contents_line_with_its_leaders_fits_its_box() -> None:
    line = Seen(f"Means of a bounded sequence{' .' * 2000} 108", BOX)

    assert not line.overfull


class Alone:
    """The OCR's reading of one block's box cropped alone."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.asked: list[Seen] = []

    def __call__(self, block: Seen) -> Seen:
        self.asked.append(block)
        return Seen(self.text, block.box, block.dpi, block.witnesses)


MADE = (
    "Maximum likelihood methods are the best methods for estimating the "
    "general model, as is established in the literature."
)


def test_a_flagged_block_read_alone_and_held_replaces_it() -> None:
    reading = [Seen(PROSE, BOX, witnesses=(PROSE,)), Seen(MADE, BOX, witnesses=(MORE,))]
    alone = Alone(MORE)

    found = settle(reading, [WITNESS], Passes([], []), alone)

    assert found == Witnessed(f"{PROSE}\n\n{MORE}", 0, 1, alone=1)
    assert [block.text for block in alone.asked] == [MADE]


def test_a_block_read_alone_that_no_witness_holds_leaves_it_disputed() -> None:
    reading = [Seen(PROSE, BOX, witnesses=(PROSE,)), Seen(MADE, BOX, witnesses=(MORE,))]

    found = settle(reading, [WITNESS], Passes([], []), Alone(MADE))

    assert (found.text, found.disputed, found.alone) == (f"{PROSE}\n\n{MADE}", 2, 0)


def test_a_block_with_no_box_is_not_read_alone() -> None:
    alone = Alone(MORE)

    found = settle(paragraphs(MADE_UP), [WITNESS], Passes([], []), alone)

    assert (found.disputed, alone.asked) == (2, [])
