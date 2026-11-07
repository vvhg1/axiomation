"""Two cuts lined up.

A paper in both formats pairs every block as `SAME`, `MATH_DIFFERS` or
`MARKUP_DIFFERS`. Then planted cases: a split, a merge, a changed paragraph,
a block only in one, and a change to the mathematics alone or the markup
alone.
"""

from __future__ import annotations

import pytest

from axiomation.cutter.correspondence import PairKind, correspond, words

MARKDOWN = """---
title: One paper
---

# Setting {#sec:setting}

We fix a *probability* space and a sample of size $n$.

\\begin{lemma}[Bound] \\label{lem:bound}
For all $t > 0$ it holds that
$$\\mathbb{P}(X \\ge t) \\le e^{-t}.$$
\\end{lemma}

\\begin{proof}
Markov's inequality, applied to $e^{X}$.
\\end{proof}

The bound is used in \\cref{lem:bound} and in the `sample_size.py` script.
"""

LATEX = """\\documentclass{article}
\\title{One paper}
\\begin{document}
\\maketitle
\\section{Setting}\\label{sec:setting}

We fix a \\emph{probability} space and a sample of size $n$.

\\begin{lemma}[Bound] \\label{lem:bound}
For all $t>0$ it holds that
\\[\\mathbb{P}(X\\geq t)\\leq e^{-t}.\\]
\\end{lemma}

\\begin{proof}
Markov's inequality, applied to $e^{X}$.
\\end{proof}

The bound is used in \\cref{lem:bound} and in the \\texttt{sample\\_size.py} script.
\\end{document}
"""


def kinds(found) -> list[PairKind]:
    return [pair.kind for pair in found.pairs]


def line_up(paper, a: tuple[str, str], b: tuple[str, str]):
    one = paper(a[0], name=a[1])
    other = paper(b[0], name=b[1])
    return correspond((one.document, one.cut), (other.document, other.cut))


def test_the_key_drops_mathematics_commands_and_emphasis() -> None:
    assert words("We fix a *probability* space $x + y$ \\emph{here}.") == (
        "We",
        "fix",
        "a",
        "probability",
        "space",
        "here",
    )
    assert words("{\\color{black} the \\texttt{sample\\_size.py}}") == (
        "the",
        "sample_size",
        "py",
    )


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # a float's font switch on a counter
        (
            "\\ifnum\\value{figure}>0 { \\small } { } Column Head",
            ("Column", "Head"),
        ),
        # a list's spacing
        ("\\setlength\\itemsep{0em} \\item First", ("First",)),
        ("\\setlength{\\parindent}{2pt} Text", ("Text",)),
        ("\\addtolength{\\textwidth}{-1.5in} Text", ("Text",)),
        ("\\setcounter{page}{3} Page \\arabic{page} here", ("Page", "here")),
        ("\\stepcounter{theorem} Next", ("Next",)),
        ("\\ifdim\\textwidth>5in wide\\fi", ("wide",)),
        ("the value of 0 em is kept", ("the", "value", "of", "0", "em", "is", "kept")),
    ],
)
def test_a_register_s_arguments_are_no_words(text, kept) -> None:
    """TeX's counters and lengths, set, read or compared, take a register's
    name, a number or a dimension, never printed text."""
    assert words(text) == kept


def test_one_paper_in_two_spellings_is_one_cut(paper) -> None:
    found = line_up(paper, (MARKDOWN, "paper.md"), (LATEX, "paper.tex"))

    assert set(kinds(found)) <= {
        PairKind.SAME,
        PairKind.MATH_DIFFERS,
        PairKind.MARKUP_DIFFERS,
    }
    assert PairKind.MATH_DIFFERS in kinds(found)
    assert PairKind.MARKUP_DIFFERS in kinds(found)
    summary = found.summary()
    assert summary["blocks_a"] == summary["blocks_b"] == len(found.pairs)


def test_a_paper_against_itself_keeps_every_hash(paper) -> None:
    found = line_up(paper, (LATEX, "paper.tex"), (LATEX, "paper.tex"))

    assert set(kinds(found)) == {PairKind.SAME}
    assert found.hashes_kept == found.blocks_a


def test_a_split_and_a_merge(paper) -> None:
    whole = "We fix a *probability* space and a sample of size $n$."
    halves = "We fix a *probability* space\n\nand a sample of size $n$."
    split = MARKDOWN.replace(whole, halves)

    forward = line_up(paper, (MARKDOWN, "paper.md"), (split, "paper.md"))
    backward = line_up(paper, (split, "paper.md"), (MARKDOWN, "paper.md"))

    (one,) = [pair for pair in forward.pairs if pair.kind is PairKind.SPLIT]
    assert (len(one.a), len(one.b)) == (1, 2)
    (other,) = [pair for pair in backward.pairs if pair.kind is PairKind.MERGED]
    assert (len(other.a), len(other.b)) == (2, 1)
    assert set(kinds(forward)) == {PairKind.SAME, PairKind.SPLIT}


def test_a_changed_paragraph(paper) -> None:
    edited = MARKDOWN.replace("a sample of size $n$", "a large sample of size $n$")

    found = line_up(paper, (MARKDOWN, "paper.md"), (edited, "paper.md"))

    assert kinds(found).count(PairKind.CHANGED) == 1
    assert set(kinds(found)) == {PairKind.SAME, PairKind.CHANGED}


def test_a_block_only_in_one(paper) -> None:
    added = MARKDOWN + "\nAn entirely new closing remark, unlike any other.\n"

    found = line_up(paper, (MARKDOWN, "paper.md"), (added, "paper.md"))

    assert kinds(found)[-1] is PairKind.ONLY_B
    assert set(kinds(found)) == {PairKind.SAME, PairKind.ONLY_B}
    back = line_up(paper, (added, "paper.md"), (MARKDOWN, "paper.md"))
    assert kinds(back)[-1] is PairKind.ONLY_A


def test_a_change_to_the_mathematics_alone(paper) -> None:
    edited = MARKDOWN.replace("e^{-t}", "e^{-2t}")

    found = line_up(paper, (MARKDOWN, "paper.md"), (edited, "paper.md"))

    assert kinds(found).count(PairKind.MATH_DIFFERS) == 1
    assert found.hashes_kept == found.blocks_a - 1


def test_a_change_to_the_markup_alone(paper) -> None:
    edited = MARKDOWN.replace("a *probability* space", "a **probability** space")

    found = line_up(paper, (MARKDOWN, "paper.md"), (edited, "paper.md"))

    assert kinds(found).count(PairKind.MARKUP_DIFFERS) == 1
    assert PairKind.MATH_DIFFERS not in kinds(found)
