"""Chunks: greedy, by sections, then subsections, then blocks.

A synthetic paper written for these tests: a short section; a section
with a lead paragraph and two subsections, the second of four paragraphs;
and a last section in an included file. Each paragraph is 60 owned
characters, each heading its command's length.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.questions.chunks import chunks, size
from questions.review_paper import make_paper


def para(word: str) -> str:
    text = f"The {word} paragraph says one thing plainly"
    return text + "." * (60 - len(text))


MAIN = rf"""\documentclass{{article}}
\begin{{document}}
\section{{One}}
{para("first")}

\section{{Two}}
{para("lead")}

\subsection{{Two a}}
{para("second")}

\subsection{{Two b}}
{para("third")}

{para("fourth")}

{para("fifth")}

{para("sixth")}

\input{{extra}}
\end{{document}}
"""

EXTRA = f"\\section{{Three}}\n{para('last')}\n"


@pytest.fixture
def tree(tmp_path: Path):
    return make_paper(tmp_path / "tree", MAIN, EXTRA)


def words(tree, chunk_blocks) -> list[str]:
    """Each block by its heading or its paragraph's second word."""
    found = []
    for block_id in chunk_blocks:
        block = tree.cut.block(block_id)
        text = tree.document.text(block.owned[0])
        found.append(
            text.split("{")[1].rstrip("}") if "section" in text else text.split()[1]
        )
    return found


def chunked(tree, budget, overlap=2):
    return [
        words(tree, c.blocks) for c in chunks(tree.document, tree.cut, budget, overlap)
    ]


def test_the_paragraphs_and_headings_have_the_sizes_the_tests_assume(tree) -> None:
    sizes = {
        w: size(tree.document, [b])
        for w, b in zip(
            words(tree, [b.id for b in tree.cut.blocks]), tree.cut.blocks, strict=True
        )
    }

    assert sizes["first"] == sizes["sixth"] == 60
    assert sizes["One"] == len(r"\section{One}")
    assert sizes["Two a"] == len(r"\subsection{Two a}")


def test_sections_are_packed_greedily_across_files(tree) -> None:
    """One (73), Two (409) and Three (75) in order: at 10,000 all fit; at
    490 One and Two do (482), and Three does not."""
    everything = ["One", "first", "Two", "lead", "Two a", "second", "Two b",
                  "third", "fourth", "fifth", "sixth"]  # fmt: skip

    assert chunked(tree, 10_000) == [[*everything, "Three", "last"]]
    assert chunked(tree, 490) == [everything, ["Three", "last"]]


def test_a_lead_too_large_to_join_its_first_subsection_is_a_chunk_alone(tree):
    """At 140, Two's lead (73) and Two a (78) do not fit together."""
    found = chunked(tree, 140)

    assert found[1:3] == [["Two", "lead"], ["Two a", "second"]]


def test_a_section_too_large_is_chunked_at_its_subsections(tree) -> None:
    """Two is 409 characters; its lead and Two a (151) fit together, and
    Two b (258) fits alone."""
    assert chunked(tree, 300) == [
        ["One", "first"],
        ["Two", "lead", "Two a", "second"],
        ["Two b", "third", "fourth", "fifth", "sixth"],
        ["Three", "last"],
    ]


def test_a_subsection_too_large_is_cut_between_blocks(tree) -> None:
    """Two b is 258 characters: at 200, its blocks until the next would
    overfill (198), then the rest."""
    found = chunked(tree, 200)

    assert ["Two b", "third", "fourth", "fifth"] in found
    assert ["sixth"] in found


def test_a_cut_between_blocks_shows_its_neighbours_as_context(tree) -> None:
    found = chunks(tree.document, tree.cut, 200, overlap=1)
    first = next(c for c in found if words(tree, c.blocks)[0] == "Two b")
    second = found[found.index(first) + 1]

    assert words(tree, first.before) == [] and words(tree, first.after) == ["sixth"]
    assert words(tree, second.before) == ["fifth"] and words(tree, second.after) == []
    assert all(not c.before and not c.after for c in found if c not in (first, second))


def test_the_overlap_is_as_many_blocks_as_asked(tree) -> None:
    found = chunks(tree.document, tree.cut, 200, overlap=2)
    second = next(c for c in found if words(tree, c.blocks)[0] == "sixth")

    assert words(tree, second.before) == ["fourth", "fifth"]
    assert all(
        not c.before and not c.after
        for c in chunks(tree.document, tree.cut, 200, overlap=0)
    )


def test_every_block_is_in_one_chunk_in_reading_order(tree) -> None:
    every = [b.id for b in tree.cut.blocks]
    for budget in (0, 50, 100, 200, 300, 10_000, None):
        found = chunks(tree.document, tree.cut, budget)
        assert [b for c in found for b in c.blocks] == every
        for chunk in found:
            assert not set(chunk.before + chunk.after) & set(chunk.blocks)


def test_a_block_over_the_budget_is_a_chunk_alone(tree) -> None:
    found = chunked(tree, 40)

    assert ["first"] in found and ["sixth"] in found


def test_none_is_the_whole_paper_and_zero_one_block_a_chunk(paper) -> None:
    (whole,) = chunks(paper.document, paper.cut, None)
    single = chunks(paper.document, paper.cut, 0)

    assert list(whole.blocks) == [b.id for b in paper.cut.blocks]
    assert ("main.tex:15:1", "main.tex:16:48") in [c.blocks for c in single]
    assert len(single) == len(paper.cut.blocks) - 1
