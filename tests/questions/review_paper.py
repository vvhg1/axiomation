"""A synthetic LaTeX paper for the cut-review question, shared
by the question's, the resolver's, the runner's and the driver's tests.

Two sections and a third in an included file; a definition, a theorem and
its proof; a footnote; a comment, which no prompt may show. Written for
these tests, from no paper.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from axiomation.cutter import Cut, CutRules, DisplayRule, cut
from axiomation.reader import Document, read_paper

MAIN = r"""\documentclass{article}
\begin{document}
\section{Setting}
We fix a field of scalars. % a hidden remark about the draft
A second sentence of the setting.

\begin{definition}\label{def:gain}
The gain is the ratio of output to input.
\end{definition}

\section{Results}
\begin{theorem}\label{thm:bound}
The output is bounded.
\end{theorem}
\begin{proof}
By Definition~\ref{def:gain}, the gain is small\footnote{Small means below one.}.
Hence the output is bounded.

Next, the bound is sharp.
\end{proof}

Closing words of the results.
\input{extra}
\end{document}
"""

EXTRA = "\\section{More}\nA closing paragraph in another file.\n"

HIDDEN = "a hidden remark about the draft"


@dataclass(frozen=True)
class Paper:
    root: Path
    document: Document
    cut: Cut

    def id_at(self, words: str, file: str = "main.tex") -> str:
        """The id of the block whose first owned characters are `words`."""
        text = self.document.file(file).text
        offset = text.index(words)
        line = text.count("\n", 0, offset) + 1
        column = offset - (text.rfind("\n", 0, offset) + 1) + 1
        return f"{file}:{line}:{column}"


def make_paper(root: Path, main: str = MAIN, extra: str = EXTRA) -> Paper:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tex").write_text(main, encoding="utf-8")
    (root / "extra.tex").write_text(extra, encoding="utf-8")
    document = read_paper(root)
    return Paper(root, document, cut(document, CutRules(DisplayRule.IN_PARAGRAPH)))


def file_questions(paper: Paper, budget: int | None = 300, overlap: int = 2):
    """The boundary sessions over the paper's cut. At 300 characters: the
    section "Setting", then "Results" (b1 its heading, b2 the theorem, b3
    and b4 the proof's first paragraph and its footnote, b5 its second,
    b6 the closing paragraph), then `extra.tex`'s "More"."""
    from axiomation.questions import load_template
    from axiomation.questions.cut_file import FILE_TEMPLATE
    from axiomation.questions.cut_file import file_questions as build

    return build(
        paper.document, paper.cut, budget, load_template(FILE_TEMPLATE), overlap
    )
