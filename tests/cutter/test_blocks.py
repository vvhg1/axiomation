"""The block rules and their doubts.

One test per rule and per doubt, each on a synthetic paper, in both formats
where the shape exists in both; a sentence broken by a blank line
at a display with them. The `paper` fixture checks coverage on every
one.
"""

from __future__ import annotations

import pytest

from axiomation.cutter import DisplayRule, Doubt, Form, Role, Segment
from paper_shapes import FOOTNOTE, INLINE_DISPLAY


def doc(body: str, preamble: str = "") -> str:
    return preamble + "\\begin{document}\n" + body + "\\end{document}\n"


def md(body: str) -> str:
    return "---\ntitle: A paper\n---\n\n" + body


STATEMENT = (
    "\\begin{theorem}\\label{thm:t}\n"
    "Assume:\n"
    "\\begin{enumerate}\n"
    "\\item first;\n"
    "\\item second.\n"
    "\\end{enumerate}\n"
    "Then\n"
    "\\begin{equation}\\label{eq:e}\n"
    "x = y.\n"
    "\\end{equation}\n"
    "\\end{theorem}\n"
)
PROOF = "\\begin{proof}\nThe first paragraph.\n\nThe second paragraph.\n\\end{proof}\n"


@pytest.fixture(params=["md", "latex"])
def spell(request):
    """A body in one of the two formats: `(text, root name)`."""

    def spelled(body: str) -> tuple[str, str]:
        if request.param == "md":
            return md(body), "paper.md"
        return doc(body), "paper.tex"

    return spelled


def cut_of(paper, spell, body: str, **options):
    text, name = spell(body)
    return paper(text, name=name, **options)


# --- Rule 3: a statement is one block ---


def test_a_statement_with_a_list_and_a_display_is_one_block(paper, spell) -> None:
    cutting = cut_of(paper, spell, STATEMENT)

    (block,) = cutting.blocks(Form.STATEMENT)
    assert cutting.text(block).startswith("\\begin{theorem}")
    assert cutting.text(block).endswith("\\end{theorem}")
    assert "\\item second." in cutting.text(block)
    assert "x = y." in cutting.text(block)
    assert (block.environment, block.meaning, block.roles) == (
        "theorem",
        "theorem",
        (Role.CLAIM,),
    )
    assert block.segments == (Segment(starts="", role=Role.CLAIM),)
    assert not cutting.blocks(Form.ITEM) and not cutting.blocks(Form.DISPLAY)


@pytest.mark.parametrize(
    ("environment", "role"),
    [
        ("definition", Role.DEFINITION),
        ("assumption", Role.ASSUMPTION),
        ("condition", Role.ASSUMPTION),
        ("conjecture", Role.CLAIM),
        ("remark", None),
    ],
)
def test_a_statement_s_role_is_its_environment_s(paper, environment, role) -> None:
    cutting = paper(doc(f"\\begin{{{environment}}}\nX.\n\\end{{{environment}}}\n"))

    (block,) = cutting.blocks(Form.STATEMENT)
    assert block.roles == (() if role is None else (role,))


def test_a_declared_name_takes_the_role_its_meaning_has(paper) -> None:
    preamble = "\\newtheorem{thm}{Theorem}\n\\newtheorem{game}{Game}\n"
    cutting = paper(
        doc(
            "\\begin{thm}\nA.\n\\end{thm}\n\n\\begin{game}\nB.\n\\end{game}\n",
            preamble,
        )
    )

    thm, game = cutting.blocks(Form.STATEMENT)
    assert (thm.environment, thm.meaning, thm.roles) == (
        "thm",
        "theorem",
        (Role.CLAIM,),
    )
    assert (game.environment, game.meaning, game.roles) == ("game", None, ())


# --- Rule 4: a proof's paragraphs are blocks ---


def test_a_proof_s_paragraphs_are_blocks(paper, spell) -> None:
    cutting = cut_of(paper, spell, PROOF)

    assert cutting.texts(Form.PROSE) == [
        "\\begin{proof} The first paragraph.",
        "The second paragraph. \\end{proof}",
    ]


def test_a_proof_title_goes_to_its_first_block(paper) -> None:
    cutting = paper(
        doc("\\begin{proof}[Proof of the claim]\nIt holds.\n\\end{proof}\n")
    )

    assert cutting.texts() == [
        "\\begin{proof}[Proof of the claim] It holds. \\end{proof}"
    ]


# --- Rule 6: displays, under both settings ---


SENTENCE = "We have\n\\[ x = 1 \\]\nwhich implies $y$.\n"


def test_own_block_cuts_a_display_out_of_its_sentence(paper, spell) -> None:
    cutting = cut_of(paper, spell, SENTENCE)

    first, display, rest = cutting.blocks()[-3:]
    assert cutting.text(first) == "We have"
    assert (display.form, display.environment) == (Form.DISPLAY, "\\[")
    assert cutting.text(rest) == "which implies $y$."
    assert rest.doubts == (Doubt.DISPLAY_IN_SENTENCE,)


def test_in_paragraph_keeps_the_sentence_whole(paper, spell) -> None:
    cutting = cut_of(paper, spell, SENTENCE, displays=DisplayRule.IN_PARAGRAPH)

    assert cutting.texts(Form.PROSE)[-1] == "We have \\[ x = 1 \\] which implies $y$."
    assert not cutting.blocks(Form.DISPLAY)
    assert not any(block.doubts for block in cutting.blocks())


def test_in_paragraph_a_display_between_blank_lines_is_its_own(paper, spell) -> None:
    body = (
        "We have it.\n\n\\begin{equation}\nx = 1\n\\end{equation}\n\nA new sentence.\n"
    )
    cutting = cut_of(paper, spell, body, displays=DisplayRule.IN_PARAGRAPH)

    (display,) = cutting.blocks(Form.DISPLAY)
    assert display.environment == "equation"
    assert cutting.texts(Form.PROSE)[-2:] == ["We have it.", "A new sentence."]


# --- A sentence broken by a blank line at a display ---


def test_in_paragraph_a_display_after_a_broken_sentence_joins_it(paper, spell) -> None:
    """A sentence broken off before its display by a blank line: the display
    ends the sentence, and the next one starts a block."""
    body = (
        "We conclude that\n\n\\begin{equation}\nx = 1\n\\end{equation}\n"
        "Combining these, we get it.\n"
    )
    cutting = cut_of(paper, spell, body, displays=DisplayRule.IN_PARAGRAPH)

    assert cutting.texts()[-2:] == [
        "We conclude that \\begin{equation} x = 1 \\end{equation}",
        "Combining these, we get it.",
    ]


def test_in_paragraph_lower_case_after_a_display_continues_it(paper, spell) -> None:
    body = (
        "We have\n\n\\begin{equation}\nx = 1\n\\end{equation}\n\nwhich implies it.\n"
        "\nThen it holds that\n\\[ y = 2, \\]\n\nwhere y is two.\n"
    )
    cutting = cut_of(paper, spell, body, displays=DisplayRule.IN_PARAGRAPH)

    assert cutting.texts()[-2:] == [
        "We have \\begin{equation} x = 1 \\end{equation} which implies it.",
        "Then it holds that \\[ y = 2, \\] where y is two.",
    ]


def test_in_paragraph_a_comment_between_does_not_end_the_sentence(paper) -> None:
    """A display, a blank line, a comment line, a blank line, then
    "where"."""
    body = "Then it holds that\n\\[ y = 2, \\]\n\n % aside\n\nwhere y is two.\n"
    cutting = paper(doc(body), displays=DisplayRule.IN_PARAGRAPH)

    assert cutting.texts() == ["Then it holds that \\[ y = 2, \\] where y is two."]


def test_a_broken_sentence_joins_only_a_display(paper, spell) -> None:
    body = (
        "A title-like line\n\nText after it.\n\n"
        "It ends here.\n\nlower case after a sentence.\n"
    )
    for displays in DisplayRule:
        cutting = cut_of(paper, spell, body, displays=displays)

        assert len(cutting.blocks(Form.PROSE)) == 4


def test_own_block_leaves_a_broken_sentence_broken(paper, spell) -> None:
    body = (
        "We conclude that\n\n\\begin{equation}\nx = 1\n\\end{equation}\n\n"
        "where x is one.\n"
    )
    cutting = cut_of(paper, spell, body)

    assert cutting.texts(Form.PROSE)[-2:] == ["We conclude that", "where x is one."]
    assert len(cutting.blocks(Form.DISPLAY)) == 1


def test_a_display_after_a_paragraph_break_is_not_doubted(paper, spell) -> None:
    cutting = cut_of(paper, spell, "We have\n\\[ x = 1 \\]\n\nA new paragraph.\n")

    assert cutting.holding("A new paragraph.").doubts == ()


def test_an_inline_dollar_display(paper) -> None:
    """The display opens and closes mid-line, inside a sentence, as a
    conversion to Markdown writes a LaTeX display."""
    cutting = paper(INLINE_DISPLAY, name="paper.md")

    display = cutting.holding("\\sum_{j\\in J'}")
    assert display.form is Form.DISPLAY
    assert cutting.text(display).startswith("$$") and cutting.text(display).endswith(
        "$$"
    )
    before = cutting.holding("settles on the limit")
    after = cutting.holding("That $a^{\\star}")
    assert cutting.text(before).endswith("on the limit")
    assert cutting.text(display).endswith("=a^{\\star}.$$")
    assert display.doubts == (Doubt.MID_LINE_BOUNDARY,)
    assert after.doubts == (Doubt.DISPLAY_IN_SENTENCE,)

    joined = paper(INLINE_DISPLAY, name="paper.md", displays=DisplayRule.IN_PARAGRAPH)
    whole = joined.holding("settles on the limit")
    assert "$$\\sum_{j\\in J'}" in joined.text(whole)
    assert "we get that" in joined.text(whole)


# --- Rules 9 and 10: prose and `\paragraph` ---


def test_prose_splits_at_blank_lines(paper, spell) -> None:
    cutting = cut_of(paper, spell, "One line\nand the next.\n\n  \nAnother.\n")

    assert cutting.texts(Form.PROSE)[-2:] == ["One line and the next.", "Another."]


@pytest.mark.parametrize(
    "line",
    ["\\FloatBarrier", "\\medskip", "\\enlargethispage{\\baselineskip}", "\\small"],
)
def test_a_layout_line_between_paragraphs_makes_no_block(paper, spell, line) -> None:
    """A line that lays out the page prints nothing (`LAYOUT_COMMANDS`):
    the paragraphs either side are the only blocks there."""
    cutting = cut_of(paper, spell, f"Before.\n\n{line}\n\nAfter.\n")

    assert cutting.texts(Form.PROSE)[-2:] == ["Before.", "After."]
    assert not any(line in text for text in cutting.texts())


def test_a_comment_only_line_is_not_blank(paper) -> None:
    cutting = paper(doc("Line one\n% a comment\nline two.\n\nNext.\n"))

    assert cutting.texts() == ["Line one line two.", "Next."]


def test_a_paragraph_heading_joins_the_paragraph_after_it(paper) -> None:
    cutting = paper(
        doc(
            "Before.\n\\paragraph{Notation.}\n\nWe write $x$.\n\n"
            "\\subparagraph{Aside.} Run in.\n"
        )
    )

    assert cutting.texts() == [
        "Before.",
        "\\paragraph{Notation.} We write $x$.",
        "\\subparagraph{Aside.} Run in.",
    ]


# --- Rules 1 and 2: the title and headings ---


def test_the_title_in_yaml(paper) -> None:
    cutting = paper(md("# One\n\nText.\n"), name="paper.md")

    title, heading, prose = cutting.blocks()
    assert (title.form, cutting.text(title)) == (Form.TITLE, "A paper")
    assert (heading.form, cutting.text(heading)) == (Form.HEADING, "# One")
    assert prose.form is Form.PROSE


def test_the_title_in_latex(paper) -> None:
    cutting = paper(
        doc(
            "\\maketitle\n\\section{One}\\label{sec:one}\nText.\n",
            "\\title{A \\emph{paper}}\n\\author{Someone}\n",
        )
    )

    title, heading, prose = cutting.blocks()
    assert (title.form, cutting.text(title)) == (Form.TITLE, "A \\emph{paper}")
    assert (heading.form, cutting.text(heading)) == (Form.HEADING, "\\section{One}")
    assert cutting.text(prose) == "Text."


def test_a_heading_is_its_own_block(paper, spell) -> None:
    body = (
        "# Setting {#sec:set}\nText right under it.\n"
        if spell("")[1] == "paper.md"
        else "\\section{Setting}\\label{sec:set}\nText right under it.\n"
    )
    cutting = cut_of(paper, spell, body)

    heading, prose = cutting.blocks()[-2:]
    assert heading.form is Form.HEADING
    assert "Setting" in cutting.text(heading) and "sec:set" not in cutting.text(heading)
    assert cutting.text(prose) == "Text right under it."


# --- Rule 5: lists ---


def test_list_items_outside_a_statement_are_blocks(paper) -> None:
    cutting = paper(
        doc(
            "\\begin{enumerate}[(i)]\n"
            "\\item First,\n\nstill first.\n"
            "\\item[(b)] Second.\n"
            "\\end{enumerate}\n"
        )
    )

    first, second = cutting.blocks(Form.ITEM)
    assert cutting.text(first) == "\\begin{enumerate}[(i)] \\item First, still first."
    assert cutting.text(second) == "\\item[(b)] Second. \\end{enumerate}"
    assert first.environment == "enumerate"


def test_latex_s_own_list_is_a_list_and_a_statement_in_an_item_is_a_block(
    paper,
) -> None:
    """LaTeX's own `\\begin{list}{\\labelitemi}{…}`: its items are
    blocks, and a lemma inside an item is one block, the item's text after
    it another."""
    cutting = paper(
        doc(
            "\\begin{list}{\\labelitemi}{\\leftmargin=1em}\n"
            "\\item One.\n"
            "\\item Two leads to\n"
            "\\begin{lemma}\\label{lem:x}\nA claim.\n\\end{lemma}\n"
            "A comment on it.\n"
            "\\end{list}\n"
        )
    )

    assert [(block.form, cutting.text(block)) for block in cutting.blocks()] == [
        (Form.ITEM, "\\begin{list}{\\labelitemi}{\\leftmargin=1em} \\item One."),
        (Form.ITEM, "\\item Two leads to"),
        (Form.STATEMENT, "\\begin{lemma} A claim. \\end{lemma}"),
        (Form.ITEM, "A comment on it. \\end{list}"),
    ]
    assert cutting.holding("A claim.").roles == (Role.CLAIM,)


def test_a_nested_list_s_items_are_blocks_of_their_own(paper) -> None:
    cutting = paper(
        doc(
            "\\begin{itemize}\n"
            "\\item Outer, before.\n"
            "  \\begin{enumerate}\n"
            "  \\item Inner one.\n"
            "  \\item Inner two.\n"
            "  \\end{enumerate}\n"
            "  Outer, after.\n"
            "\\item Outer two.\n"
            "\\end{itemize}\n"
        )
    )

    assert cutting.texts(Form.ITEM) == [
        "\\begin{itemize} \\item Outer, before.",
        "\\begin{enumerate} \\item Inner one.",
        "\\item Inner two. \\end{enumerate}",
        "Outer, after.",
        "\\item Outer two. \\end{itemize}",
    ]


def test_markdown_list_items_are_blocks(paper) -> None:
    cutting = paper(
        md(
            "Some prose.\n\n"
            "- first item\n  wrapped\n"
            "- second item\n\n"
            "  continued after a blank line\n"
            "  - nested\n\n"
            "After the list.\n"
        ),
        name="paper.md",
    )

    assert cutting.texts(Form.ITEM) == [
        "- first item wrapped",
        "- second item continued after a blank line",
        "- nested",
    ]
    assert cutting.texts(Form.PROSE) == ["Some prose.", "After the list."]


# --- Rule 7: floats, code, containers, unknown environments ---


def test_a_float_is_one_block_caption_included(paper) -> None:
    cutting = paper(
        doc(
            "\\begin{figure}\n\\centering\n\\includegraphics{a.png}\n"
            "\\caption{A picture.}\\label{fig:a}\n\\end{figure}\n"
        )
    )

    (block,) = cutting.blocks()
    assert (block.form, block.environment) == (Form.FLOAT, "figure")
    assert "\\caption{A picture.}" in cutting.text(block)


@pytest.mark.parametrize(
    ("text", "name", "environment"),
    [
        pytest.param(
            md("```python\nx = 1\n\n\ny = 2\n```\n"), "paper.md", "```", id="md"
        ),
        (
            doc("\\begin{verbatim}\nx = 1\n\n\ny = 2\n\\end{verbatim}\n"),
            "paper.tex",
            "verbatim",
        ),
    ],
)
def test_a_code_block_is_one_block(paper, text, name, environment) -> None:
    cutting = paper(text, name=name)

    (block,) = cutting.blocks(Form.CODE)
    assert block.environment == environment


def test_a_container_is_transparent(paper) -> None:
    cutting = paper(doc("\\begin{center}\nOne.\n\nTwo.\n\\end{center}\n"))

    assert cutting.texts() == ["\\begin{center} One.", "Two. \\end{center}"]


def test_an_unknown_environment_is_one_doubted_block(paper) -> None:
    cutting = paper(doc("\\begin{quote}\nOne.\n\nTwo.\n\\end{quote}\n"))

    (block,) = cutting.blocks()
    assert (block.form, block.environment) == (Form.ENVIRONMENT, "quote")
    assert block.doubts == (Doubt.UNKNOWN_ENVIRONMENT,)


# --- Rule 8: footnotes ---


def test_a_footnote_is_nested_and_owns_its_text(paper) -> None:
    """The footnote's argument holds a `\\citep{…}` with two keys, and the
    sentence goes on after it."""
    cutting = paper(FOOTNOTE)

    (footnote,) = cutting.blocks(Form.FOOTNOTE)
    paragraph = cutting.holding("We rate each forecast by a")
    assert footnote.within == paragraph.id
    assert cutting.text(footnote).startswith("\\footnote{Strictly")
    assert "jones2012proper}" in cutting.text(footnote)
    assert "Strictly" not in cutting.text(paragraph)
    assert "$s : \\calP \\times \\calY" in cutting.text(paragraph)
    assert len(paragraph.owned) == 2
    assert footnote.doubts == () and paragraph.doubts == ()


def test_a_footnote_in_a_statement_nests_in_it(paper) -> None:
    cutting = paper(doc("\\begin{lemma}\nA\\footnote{Note.} claim.\n\\end{lemma}\n"))

    (lemma,) = cutting.blocks(Form.STATEMENT)
    (footnote,) = cutting.blocks(Form.FOOTNOTE)
    assert footnote.within == lemma.id


# --- Files ---


def test_a_block_ends_at_a_file_boundary(paper) -> None:
    cutting = paper(
        doc("Before the include\n\\input{part}\nafter it.\n"),
        extra={"part.tex": "Inside the part.\n"},
    )

    assert [(block.extent.file, cutting.text(block)) for block in cutting.blocks()] == [
        ("paper.tex", "Before the include"),
        ("part.tex", "Inside the part."),
        ("paper.tex", "after it."),
    ]


def test_a_block_id_is_the_position_of_its_first_owned_character(paper) -> None:
    cutting = paper(doc("% a note\n  Indented text.\n"))

    (block,) = cutting.blocks()
    assert block.id == "paper.tex:3:3"


# --- Doubts ---


def test_a_boundary_inside_a_line_is_doubted(paper, spell) -> None:
    cutting = cut_of(paper, spell, "\\begin{lemma}\nA.\n\\end{lemma} Next, prose.\n")

    assert cutting.holding("Next, prose.").doubts == (Doubt.MID_LINE_BOUNDARY,)


def test_an_unclosed_environment_is_doubted(paper) -> None:
    cutting = paper(md("\\begin{lemma}\nNothing closes this.\n"), name="paper.md")

    (block,) = cutting.blocks(Form.STATEMENT)
    assert Doubt.UNCLOSED_ENVIRONMENT in block.doubts


def test_a_footnote_in_a_footnote_nests_in_it(paper) -> None:
    cutting = paper(doc("Text\\footnote{Outer \\footnote{inner} tail.} more.\n"))

    outer, inner = cutting.blocks(Form.FOOTNOTE)
    paragraph = cutting.holding("more.")
    assert (outer.within, inner.within) == (paragraph.id, outer.id)
    assert cutting.text(outer) == "\\footnote{Outer tail.}"
    assert cutting.text(inner) == "\\footnote{inner}"


def test_text_after_a_nested_markdown_item_is_the_outer_item_s(paper) -> None:
    cutting = paper(
        md("- outer\n  - inner\n\n  outer tail\n\n- next\n"), name="paper.md"
    )

    assert cutting.texts(Form.ITEM) == ["- outer", "- inner", "outer tail", "- next"]


def test_a_wrapped_line_starting_with_a_number_is_not_an_item(paper) -> None:
    """CommonMark: an ordered item that does not start at 1 cannot interrupt
    a paragraph."""
    cutting = paper(
        md("Para line\n2. not a list\n\n1. one\n   wrapped\n2. two\n"),
        name="paper.md",
    )

    assert cutting.texts(Form.PROSE) == ["Para line 2. not a list"]
    assert cutting.texts(Form.ITEM) == ["1. one wrapped", "2. two"]


# --- Run-in headings set alone, and bare braces ---


def test_a_run_in_heading_set_alone_joins_its_paragraph(paper) -> None:
    """A `\\textbf{Step 2: …}` on a line of its own, then a blank
    line, joins its paragraph, as `\\paragraph{…}` does."""
    cutting = paper(
        doc(
            "\\textbf{Step 2: Errors.\n}\n\nIn this step we go on.\n\n"
            "{\\bf Remarks:}\n\nMore words.\n\n"
            "\\textbf{Bold} words open this one.\n\nThe next one.\n"
        )
    )

    assert cutting.texts() == [
        "\\textbf{Step 2: Errors. } In this step we go on.",
        "{\\bf Remarks:} More words.",
        "\\textbf{Bold} words open this one.",
        "The next one.",
    ]


def test_an_emphasised_paragraph_set_alone_stays_a_block(paper) -> None:
    """Italics also emphasise a claim: an italic paragraph of its own is not
    a heading, however short, and joins nothing."""
    cutting = paper(
        doc(
            "{\\em The problem is hard.}\n\nWe show why.\n\n"
            "\\noindent\\emph{A remark.}\n\nIts text.\n"
        )
    )

    assert cutting.texts() == [
        "{\\em The problem is hard.}",
        "We show why.",
        "\\noindent\\emph{A remark.}",
        "Its text.",
    ]
    assert cutting.cut.parts == ()


def test_a_bare_closing_brace_joins_the_block_it_closes(paper) -> None:
    """A `{\\color{black}… \\end{defn}}`: the brace renders nothing in
    LaTeX."""
    cutting = paper(
        doc(
            "{\\color{black}Also, we define\n"
            "\\begin{definition}\nA term.\n\\end{definition}}\n\n"
            "Next paragraph.\n"
        )
    )

    assert cutting.texts() == [
        "{\\color{black}Also, we define",
        "\\begin{definition} A term. \\end{definition}}",
        "Next paragraph.",
    ]
