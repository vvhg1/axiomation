"""Block hashes.

`TestNormalize` covers normalization. `TestBlockHashes` covers blocks: a
reflow, a whitespace edit or a moved label moves no hash, and an edit to a
statement moves its block's. An edit to a comment or a directive moves
no hash, because a block's owned text leaves both out.
"""

from __future__ import annotations

import pytest

from axiomation.cutter import Form
from axiomation.cutter.hashing import HASH_LENGTH, hash_text, normalize


def doc(body: str) -> str:
    return "\\begin{document}\n" + body + "\\end{document}\n"


MARKDOWN = """# Main
\\label{sec:main}

\\begin{theorem}[T] \\label{thm:t}
The statement says $x = y$.
\\end{theorem}

\\begin{proof}
By direct computation.
\\end{proof}
"""
LATEX = doc(
    "\\section{Main}\\label{sec:main}\n\n"
    "\\begin{theorem}[T] \\label{thm:t}\n"
    "The statement says $x = y$.\n"
    "\\end{theorem}\n\n"
    "\\begin{proof}\nBy direct computation.\n\\end{proof}\n"
)
SPELLINGS = [pytest.param(MARKDOWN, "paper.md", id="md"), (LATEX, "paper.tex")]


class TestNormalize:
    def test_drops_structural_latex(self):
        assert normalize(["\\begin{theorem}[T]", "\\label{thm:t}"]) == "[T]"

    def test_collapses_every_run_of_whitespace(self):
        assert normalize(["one", "", "  ", "two"]) == "one two"

    def test_collapses_spaces_in_math(self):
        assert normalize(["Text $x + y$"]) == "Text $x+y$"

    def test_length(self):
        assert len(hash_text("anything")) == HASH_LENGTH


def hashes(paper, text: str, name: str) -> tuple[str, str]:
    """The statement's hash and the proof paragraph's."""
    cutting = paper(text, name=name)
    (statement,) = cutting.blocks(Form.STATEMENT)
    proof = cutting.holding("computation")
    return statement.hash, proof.hash


@pytest.mark.parametrize(("text", "name"), SPELLINGS)
class TestBlockHashes:
    def test_a_proof_edit_moves_the_proof_s_block_only(self, paper, text, name):
        before = hashes(paper, text, name)
        after = hashes(
            paper, text.replace("By direct computation.", "By a computation."), name
        )
        assert before[0] == after[0]
        assert before[1] != after[1]

    def test_a_statement_edit_moves_its_block(self, paper, text, name):
        before = hashes(paper, text, name)
        after = hashes(paper, text.replace("$x = y$", "$x < y$"), name)
        assert before[0] != after[0]
        assert before[1] == after[1]

    def test_a_reflow_moves_nothing(self, paper, text, name):
        before = hashes(paper, text, name)
        after = hashes(
            paper, text.replace("The statement says", "The statement\nsays"), name
        )
        assert before == after

    def test_a_whitespace_edit_moves_nothing(self, paper, text, name):
        before = hashes(paper, text, name)
        after = hashes(
            paper,
            text.replace(
                "The statement says $x = y$.", "The  statement says $x =  y$. "
            ),
            name,
        )
        assert before == after

    def test_moving_the_label_moves_nothing(self, paper, text, name):
        before = hashes(paper, text, name)
        after = hashes(
            paper,
            text.replace(
                "\\begin{theorem}[T] \\label{thm:t}",
                "\\begin{theorem}[T]\n\\label{thm:t}",
            ),
            name,
        )
        assert before == after


def test_a_comment_edit_moves_no_hash(paper):
    text = LATEX.replace(
        "The statement says $x = y$.\n", "The statement says $x = y$. % check this\n"
    )
    before = hashes(paper, text, "paper.tex")
    after = hashes(paper, text.replace("check this", "checked, fine"), "paper.tex")
    assert before == after
    assert before == hashes(paper, LATEX, "paper.tex")


@pytest.mark.parametrize(
    ("text", "name", "directive", "edited"),
    [
        pytest.param(
            MARKDOWN,
            "paper.md",
            "<!-- @sorry the case y = 0 is missing -->",
            "<!-- @sorry both cases are missing -->",
            id="md",
        ),
        (LATEX, "paper.tex", "% @sorry the case y = 0 is missing", "% @sorry both"),
    ],
)
def test_a_directive_edit_moves_no_hash(paper, text, name, directive, edited):
    marked = text.replace(
        "By direct computation.\n", f"By direct computation.\n{directive}\n"
    )
    before = hashes(paper, marked, name)
    after = hashes(paper, marked.replace(directive, edited), name)
    assert before == after == hashes(paper, text, name)
