"""Synthetic papers, cut in process, for the assembler.

Two small papers, one LaTeX and one Markdown, with a reference in the
second paragraph so a split can move a candidate. Every corrected cut the
`Paper.apply` helper returns is checked for coverage: every rendered
character owned by exactly one block.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from axiomation.assembler import Corrected, corrected_cut, owned_runs
from axiomation.cutter import Cut, CutRules, DisplayRule, cut
from axiomation.cutter.coverage import coverage
from axiomation.reader import Document, read_paper
from axiomation.records import CutCorrection

LATEX = r"""\documentclass{article}
\begin{document}
\section{Intro}
First sentence of the intro. Second sentence names a constant $C$.
Third sentence starts a new claim.

Opening. A second paragraph\footnote{A note.} goes on. Later it uses Lemma~\ref{lem:a}.

\begin{lemma}\label{lem:a}
Let $x$ be positive. (a) It holds. (b) It also holds.
\end{lemma}
\begin{proof}
By the bound, it holds. Hence done.

A second step of the proof.
\end{proof}

Closing words.

Final words. More final words.

\begin{definition}\label{def:b}
A term is defined.
\end{definition};
\end{document}
"""

MARKDOWN = """# A title

First sentence here. Second sentence here.

Another paragraph. It carries on.

Last paragraph.
"""


@dataclass(frozen=True)
class Paper:
    """A synthetic paper's document layer and its `in-paragraph` cut."""

    document: Document
    cut: Cut
    file: str

    def id_at(self, words: str) -> str:
        """The block id a block starting at `words` takes."""
        text = self.document.file(self.file).text
        offset = text.index(words)
        line = text.count("\n", 0, offset) + 1
        column = offset - (text.rfind("\n", 0, offset) + 1) + 1
        return f"{self.file}:{line}:{column}"

    def apply(self, *corrections: CutCorrection) -> Corrected:
        corrected = corrected_cut(self.document, self.cut, corrections)
        covered = coverage(self.document, corrected.cut)
        assert covered.complete, covered
        return corrected

    def owned(self, found: Cut, block_id: str) -> str:
        """A block's owned runs, joined by a space."""
        return " ".join(owned_runs(self.document, found.block(block_id)))


def _paper(root: Path, name: str, text: str, extra: dict[str, str]) -> Paper:
    root.mkdir()
    (root / name).write_text(text, encoding="utf-8")
    for path, content in extra.items():
        (root / path).write_text(content, encoding="utf-8")
    document = read_paper(root)
    return Paper(document, cut(document, CutRules(DisplayRule.IN_PARAGRAPH)), name)


@pytest.fixture
def latex(tmp_path: Path) -> Paper:
    return _paper(tmp_path / "tex", "main.tex", LATEX, {"extra.tex": "Never.\n"})


@pytest.fixture
def markdown(tmp_path: Path) -> Paper:
    return _paper(tmp_path / "md", "paper.md", MARKDOWN, {})


@pytest.fixture
def two_files(tmp_path: Path) -> Paper:
    main = (
        "\\documentclass{article}\n\\begin{document}\n"
        "First paragraph.\n\n\\input{second}\n\\end{document}\n"
    )
    return _paper(
        tmp_path / "two", "main.tex", main, {"second.tex": "Second paragraph.\n"}
    )
