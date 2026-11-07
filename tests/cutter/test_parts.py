"""Marked parts.

Items with a written marker, with none and with a label; `align` rows with
labels; a label written just before its display; `\\paragraph`,
a bold step heading and `**Step 1.**`.
"""

from __future__ import annotations

import pytest

from axiomation.cutter import DisplayRule, Form, PartKind
from axiomation.cutter.parts import is_step
from paper_shapes import STEP_HEADINGS


def doc(body: str) -> str:
    return "\\begin{document}\n" + body + "\\end{document}\n"


def parts(cutting, kind: PartKind):
    return [part for part in cutting.cut.parts if part.kind is kind]


def test_items_in_a_statement_are_parts_of_its_block(paper) -> None:
    cutting = paper(
        doc(
            "\\begin{theorem}\nAssume\n\\begin{enumerate}\n"
            "\\item[(a)] a first; \\label{cond:a}\n"
            "\\item a second.\n"
            "\\end{enumerate}\n\\end{theorem}\n"
        )
    )
    (statement,) = cutting.blocks(Form.STATEMENT)

    first, second = parts(cutting, PartKind.ITEM)
    assert (first.block, first.marker, first.ordinal, first.label) == (
        statement.id,
        "(a)",
        1,
        "cond:a",
    )
    assert (second.marker, second.ordinal, second.label) == (None, 2, None)
    assert cutting.document.text(second.at) == "\\item a second."


def test_items_of_a_nested_list_count_in_their_own_list(paper) -> None:
    cutting = paper(
        doc(
            "\\begin{enumerate}\n\\item Outer.\n"
            "\\begin{enumerate}\n\\item Inner. \\label{it:inner}\n\\end{enumerate}\n"
            "\\item Outer two.\n\\end{enumerate}\n"
        )
    )

    found = [(part.ordinal, part.label) for part in parts(cutting, PartKind.ITEM)]
    assert found == [(1, None), (1, "it:inner"), (2, None)]
    outer, inner, _ = parts(cutting, PartKind.ITEM)
    assert outer.block != inner.block


def test_markdown_items_carry_their_marker(paper) -> None:
    cutting = paper(
        "# S\n\n1. one\n2. two \\label{it:two}\n\n- dash\n", name="paper.md"
    )

    found = [
        (part.marker, part.ordinal, part.label)
        for part in parts(cutting, PartKind.ITEM)
    ]
    assert found == [("1.", 1, None), ("2.", 2, "it:two"), ("-", 1, None)]


def test_each_labelled_align_row_is_a_part(paper) -> None:
    cutting = paper(
        doc(
            "\\begin{align}\n"
            "a &= b \\label{eq:one} \\\\\n"
            "c &= d \\nonumber \\\\[2pt]\n"
            "e &= f \\label{eq:three}\n"
            "\\end{align}\n"
            "\\begin{equation}\\label{eq:single}\nx = y\n\\end{equation}\n"
        )
    )

    found = parts(cutting, PartKind.LABELLED_DISPLAY)
    assert [part.marker for part in found] == ["eq:one", "eq:three", "eq:single"]
    one, three, single = found
    assert cutting.document.text(one.at) == "a &= b \\label{eq:one}"
    assert cutting.document.text(three.at) == "e &= f \\label{eq:three}"
    assert cutting.document.text(single.at).startswith("\\begin{equation}")
    assert one.block == three.block != single.block


LABEL_BEFORE = (
    "Then \\label{eq:before}\nOPEN\nx = y\nCLOSE\n"
    "Here \\label{eq:far} is text.\nOPEN\nu = v\nCLOSE\n"
    "So \\label{eq:outer}\n"
    "\\begin{equation}\\label{eq:own}\na = b\n\\end{equation}\n"
)


@pytest.mark.parametrize(
    ("name", "text", "display"),
    [
        (
            "paper.tex",
            doc(LABEL_BEFORE.replace("OPEN", "\\[").replace("CLOSE", "\\]")),
            "\\[ x = y \\]",
        ),
        (
            "paper.md",
            LABEL_BEFORE.replace("OPEN", "$$").replace("CLOSE", "$$"),
            "$$ x = y $$",
        ),
    ],
)
def test_a_label_just_before_a_display_names_it(paper, name, text, display) -> None:
    """A sentence's last word, then a `\\label{…}` before its display, in
    both formats. A label with text between it and the display, or
    before a display with a label of its own, names nothing."""
    cutting = paper(text, name=name, displays=DisplayRule.IN_PARAGRAPH)

    found = parts(cutting, PartKind.LABELLED_DISPLAY)
    assert [part.marker for part in found] == ["eq:before", "eq:own"]
    before = found[0]
    assert " ".join(cutting.document.text(before.at).split()) == display
    assert before.block == cutting.holding("Then").id


def test_a_paragraph_heading_is_a_headed_part(paper) -> None:
    cutting = paper(doc("\\paragraph{Case 1: $x > 0$.} Then it holds.\n"))

    (part,) = parts(cutting, PartKind.HEADED)
    assert part.marker == "Case 1: $x > 0$."
    assert cutting.document.text(part.at) == "\\paragraph{Case 1: $x > 0$.}"
    assert is_step(part)


def test_a_bold_step_heading(paper) -> None:
    """The second heading's closing brace is on the next line."""
    cutting = paper(STEP_HEADINGS)

    first, second = parts(cutting, PartKind.HEADED)
    assert first.marker == "Step 1: A bound for one round."
    assert second.marker == "Step 2: Summing over the rounds."
    assert is_step(first) and is_step(second)
    assert first.block == cutting.holding("Fix a round").id


def test_a_markdown_bold_run_that_opens_a_block(paper) -> None:
    cutting = paper(
        "# S\n\n**Step 1.** First.\n\nText with **bold** inside.\n", name="paper.md"
    )

    (part,) = parts(cutting, PartKind.HEADED)
    assert part.marker == "Step 1."
    assert is_step(part)


def test_a_bold_run_after_noindent_or_a_proof_opening(paper) -> None:
    cutting = paper(
        doc(
            "\\noindent\\textbf{Remark.} Plain.\n\n"
            "\\begin{proof}\n\\textbf{Stage two} goes.\n\\end{proof}\n"
        )
    )

    markers = [part.marker for part in parts(cutting, PartKind.HEADED)]
    assert markers == ["Remark.", "Stage two"]
    assert [is_step(part) for part in parts(cutting, PartKind.HEADED)] == [False, True]


# --- LaTeX's other bold and italic runs ---


def test_bold_declarations_open_a_block_as_headed_parts(paper) -> None:
    cutting = paper(
        "\\documentclass{article}\n\\title{{\\bf A Title}}\n\\begin{document}\n"
        "\\maketitle\n"
        "{\\bf 1) A heading:} Text.\n\n{\\bfseries Another.} More.\n"
        "\\end{document}\n"
    )

    found = parts(cutting, PartKind.HEADED)
    assert [part.marker for part in found] == ["1) A heading:", "Another."]
    assert cutting.document.text(found[0].at) == "{\\bf 1) A heading:}"


def test_an_italic_run_in_heading_opens_a_block_or_an_item(paper) -> None:
    """An `\\item {\\em Step 1 (…):}` in a `\\begin{list}`, as an
    algorithm's steps are written; an italic run that does not end as a heading does is
    emphasis."""
    cutting = paper(
        doc(
            "\\begin{list}{\\labelitemi}{\\leftmargin=1em}\n"
            "\\item {\\em Step 1 (Update):} First.\n"
            "\\item \\textit{Step 2.} Second.\n"
            "\\end{list}\n\n"
            "\\emph{Not} a heading.\n\n{\\it Remark:} one.\n"
        )
    )

    found = parts(cutting, PartKind.HEADED)
    assert [part.marker for part in found] == [
        "Step 1 (Update):",
        "Step 2.",
        "Remark:",
    ]
    assert [is_step(part) for part in found] == [True, True, False]
    assert found[0].block == cutting.holding("First.").id


def test_an_italic_run_is_a_heading_only_when_short_and_run_in(paper) -> None:
    """An emphasised opening sentence of more than eight words, or an italic
    run with nothing after it in its block, marks no part."""
    cutting = paper(
        doc(
            "{\\em We prove the conjecture for every n and every k.} The proof"
            " follows.\n\n"
            "\\emph{Hard.}\n\n"
            "\\textit{Note:} it runs in.\n"
        )
    )

    found = parts(cutting, PartKind.HEADED)
    assert [part.marker for part in found] == ["Note:"]


def test_a_run_in_heading_at_an_item_inside_a_statement(paper) -> None:
    """An `\\item \\emph{(… .)}` heading, as an item of a
    proposition may write one: a heading opens the item, not the block,
    and may end in `.)`."""
    cutting = paper(
        doc(
            "\\begin{proposition}\nThen:\n\\begin{enumerate}\n"
            "\\item first;\n"
            "\\item \\emph{(A named claim.)} The process is bounded.\n"
            "\\end{enumerate}\n\\end{proposition}\n"
        )
    )

    (heading,) = parts(cutting, PartKind.HEADED)
    assert heading.marker == "(A named claim.)"
    assert heading.block == cutting.blocks(Form.STATEMENT)[0].id
    assert len(parts(cutting, PartKind.ITEM)) == 2


def test_an_item_s_part_ends_where_a_statement_cuts_its_block(paper) -> None:
    """A remark's item holding a lemma: a part lies inside its block."""
    cutting = paper(
        doc(
            "\\begin{itemize}\n"
            "\\item It leads to\n"
            "\\begin{lemma}\\label{lem:x}\nA claim.\n\\end{lemma}\n"
            "A comment.\n"
            "\\item Two.\n"
            "\\end{itemize}\n"
        )
    )

    first, second = parts(cutting, PartKind.ITEM)
    assert cutting.document.text(first.at) == "\\item It leads to"
    assert first.block == cutting.holding("It leads to").id
    assert cutting.document.text(second.at) == "\\item Two."
