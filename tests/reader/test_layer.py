"""The document layer's passes: sections, environments, references, citations,
footnotes, macros, the title and the hidden map.

Also: footnote spans across nested braces, an inline
`$$…$$` display, macros recorded and not expanded, the title in YAML and in
`\\title{…}` (after `\\begin{document}` and in the preamble), pandoc's heading
attributes, a layout-only line, reading order across an `\\input`, and the
hidden map's coverage of a small paper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.reader import read_paper
from axiomation.reader.document import Document, EnvKind, HiddenKind
from paper_shapes import FOOTNOTE, INLINE_DISPLAY


def doc(body: str, preamble: str = "") -> str:
    """`body`, wrapped in a minimal `\\begin{document}` shell.

    The root finder takes the one `.tex` file with an uncommented
    `\\begin{document}`; most cases below need
    no more of a preamble than that.
    """
    return preamble + "\\begin{document}\n" + body + "\\end{document}\n"


@pytest.fixture
def paper(tmp_path: Path):
    """Write one file into its own subdirectory and read its document layer.

    `extra` writes further files beside it, keyed by their path, for a case
    that needs an `\\input` target.
    """
    counter = 0

    def build(
        text: str, name: str = "paper.tex", extra: dict[str, str] | None = None
    ) -> Document:
        nonlocal counter
        counter += 1
        root = tmp_path / f"case{counter}"
        root.mkdir()
        (root / name).write_text(text, encoding="utf-8")
        for path, content in (extra or {}).items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return read_paper(root)

    return build


# --- Headings ---


class TestHeadings:
    def test_each_sectioning_command_is_a_heading_at_its_level(self, paper) -> None:
        document = paper(
            doc(
                "\\part{P}\n\\chapter{C}\n\\section{S}\n\\subsection{SS}\n"
                "\\subsubsection{SSS}\n"
            )
        )

        assert [(s.level, s.title) for s in document.sections] == [
            (1, "P"),
            (2, "C"),
            (3, "S"),
            (4, "SS"),
            (5, "SSS"),
        ]

    def test_a_run_in_paragraph_is_not_a_section(self, paper) -> None:
        """`\\paragraph{Notation.}` is bold run-in text, not a heading
        (`SECTIONING`)."""
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n\\paragraph{Notation.} We write $x$.\n"
                "\\subparagraph{Aside.} And $y$.\n"
            )
        )

        assert [s.title for s in document.sections] == ["S"]

    def test_a_starred_heading_and_a_short_title(self, paper) -> None:
        document = paper(
            doc("\\section*{Unnumbered}\n\\section[Short]{The $\\{0,1\\}$ case}\n")
        )

        assert [(s.title, s.starred) for s in document.sections] == [
            ("Unnumbered", True),
            ("The $\\{0,1\\}$ case", False),
        ]

    def test_a_label_on_the_heading_line_or_below_it(self, paper) -> None:
        document = paper(
            doc(
                "\\section{One}\\label{sec:one}\nText.\n\n"
                "\\section{Two}\n\\label{sec:two}\nText.\n"
            )
        )

        assert [s.label for s in document.sections] == ["sec:one", "sec:two"]

    def test_a_hash_line_in_a_tex_file_is_text(self, paper) -> None:
        document = paper(doc("# Not a heading\n\nSome text.\n"))

        assert document.sections == ()


# --- What is not prose ---


class TestWhatIsNotProse:
    def test_preamble_and_end_matter_are_hidden(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\nBody text.\n",
                preamble="\\documentclass{article}\n\\newcommand{\\R}{\\mathbb{R}}\n",
            )
            + "After the end.\n"
        )

        source = document.file(document.root)
        preamble = next(h for h in source.hidden if h.kind is HiddenKind.PREAMBLE)
        end_matter = next(h for h in source.hidden if h.kind is HiddenKind.END_MATTER)
        assert "newcommand" in document.text(preamble.span)
        assert "After the end." in document.text(end_matter.span)
        assert all("Body text." not in document.text(h.span) for h in source.hidden)

    def test_a_commented_environment_is_none_and_a_commented_reference_is_none(
        self, paper
    ) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "% \\begin{lemma}\\label{lem:gone}\n% Gone.\n% \\end{lemma}\n"
                "\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n"
                "Prose citing \\ref{lem:b} % and \\ref{def:a} in a comment.\n"
            )
        )

        assert [e.name for e in document.environments] == ["lemma"]
        assert [label.name for label in document.labels] == ["sec:s", "lem:b"]
        assert [r.targets for r in document.references] == [("lem:b",)]

    def test_an_escaped_percent_is_text(self, paper) -> None:
        document = paper(
            doc("\\section{S}\\label{sec:s}\nFifty \\% of \\ref{def:a}.\n")
        )

        assert [r.targets for r in document.references] == [("def:a",)]
        source = document.file(document.root)
        assert all("Fifty" not in document.text(h.span) for h in source.hidden)

    def test_a_verbatim_block_is_not_read(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n\\begin{verbatim}\n"
                "\\begin{lemma}\\label{lem:code}\n\\end{lemma}\n\\end{verbatim}\n"
            )
        )

        assert [e.name for e in document.environments] == ["verbatim"]
        assert [label.name for label in document.labels] == ["sec:s"]

    def test_a_structure_line_is_hidden(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\nProse.\n\\input{other}\n\\appendix\n"
                "\\printbibliography\n"
            )
        )

        source = document.file(document.root)
        structure = [
            document.text(h.span)
            for h in source.hidden
            if h.kind is HiddenKind.STRUCTURE
        ]
        assert "\\input{other}" in structure
        assert any("\\appendix" in text for text in structure)
        assert any("\\printbibliography" in text for text in structure)
        assert all("Prose." not in document.text(h.span) for h in source.hidden)


# --- The LaTeX an author writes ---


class TestAuthorHabits:
    """Idioms a converted paper never has and a written one does."""

    def test_a_line_break_then_a_percent_is_a_comment(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n"
                "Line one\\\\% \\ref{lem:b} is commented out\n"
            )
        )

        assert document.references == ()

    def test_a_percent_in_verb_or_url_is_text(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n"
                "\\begin{definition}\\label{def:a}\nA.\n\\end{definition}\n"
                "Type \\verb|%| then see \\ref{lem:b}.\n"
                "At \\url{https://x.org/a%20b} see \\ref{def:a}.\n"
            )
        )

        assert [r.targets for r in document.references] == [("lem:b",), ("def:a",)]

    def test_a_wrapped_heading_title(self, paper) -> None:
        document = paper(
            doc("\\section{A long title\n  that wraps}\\label{sec:s}\nText.\n")
        )

        (section,) = document.sections
        assert section.title == "A long title that wraps"
        assert section.label == "sec:s"

    def test_an_iffalse_block_is_hidden_and_its_else_is_not(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n\\iffalse\n"
                "\\begin{lemma}\\label{lem:off}\nOff.\n\\end{lemma}\n"
                "\\ifx\\a\\b nested\\fi\n\\else\n"
                "\\begin{lemma}\\label{lem:on}\nOn.\n\\end{lemma}\n\\fi\n"
            )
        )

        assert [label.name for label in document.labels] == ["sec:s", "lem:on"]

    def test_an_iffalse_a_definition_holds_hides_nothing(self, paper) -> None:
        """A preamble's `\\def\\ifexpository{\\iffalse}`, a `\\let` of one
        or a command whose body holds one opens no block: the document
        after it is read, as TeX reads it. Inside a real `\\iffalse` block,
        a definition whose conditionals balance ends nothing early."""
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n\\iffalse\n"
                "\\def\\hide#1{\\iffalse #1\\fi}\n"
                "\\begin{lemma}\\label{lem:off}\nOff.\n\\end{lemma}\n\\fi\n"
                "\\begin{lemma}\\label{lem:on}\nOn.\n\\end{lemma}\n",
                preamble="\\def\\ifexpository{\\iffalse}\n"
                "\\let\\ifdraft=\\iffalse\n"
                "\\newcommand{\\note}[1][x]{\\iffalse #1\\fi}\n",
            )
        )

        assert [label.name for label in document.labels] == ["sec:s", "lem:on"]

    def test_a_directive_in_a_verbatim_block_is_an_example(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n\\begin{verbatim}\n% @sorry an example\n"
                "\\end{verbatim}\n\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n"
            )
        )

        assert document.directives == ()

    def test_a_directive_in_a_markdown_code_fence_is_an_example(self, paper) -> None:
        document = paper(
            "# S\n\\label{sec:s}\n```\n<!-- @sorry an example -->\n```\n"
            "\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n",
            name="paper.md",
        )

        assert document.directives == ()


# --- References: every command that points at a label ---


class TestReferences:
    def test_a_cref_list_names_every_target(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n"
                "\\begin{definition}\\label{def:a}\nA.\n\\end{definition}\n"
                "\\begin{theorem}\\label{thm:c}\nBy \\cref{def:a,lem:b}.\n"
                "\\end{theorem}\n"
            )
        )

        reference = next(r for r in document.references if r.command == "cref")
        assert reference.targets == ("def:a", "lem:b")

    def test_a_comma_is_part_of_a_label_outside_cleveref(self, paper) -> None:
        """Outside cleveref, a comma is part of a label:
        `\\label{lem:a,b_c}` is one label, `\\ref{lem:a,b_c}` one
        reference to it."""
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "\\begin{lemma}\\label{lem:a,b_c}\nY.\n\\end{lemma}\n"
                "\\begin{theorem}\\label{thm:t}\nBy Lemma~\\ref{lem:a,b_c}.\n"
                "\\end{theorem}\n"
            )
        )

        assert any(label.name == "lem:a,b_c" for label in document.labels)
        reference = next(r for r in document.references if r.command == "ref")
        assert reference.targets == ("lem:a,b_c",)

    def test_every_reference_command_is_read_and_pageref_is_not(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n"
                "\\begin{definition}\\label{def:a}\nA.\n\\end{definition}\n"
                "\\begin{theorem}\\label{thm:c}\n"
                "\\Cref{def:a} \\eqref{eq:e} \\vref{lem:b} \\nameref{sec:s} "
                "\\autoref*{def:a} \\crefrange{def:a}{lem:b} \\pageref{lem:b}.\n"
                "\\end{theorem}\n"
            )
        )

        targets = [t for r in document.references for t in r.targets]
        assert targets == ["def:a", "eq:e", "lem:b", "sec:s", "def:a", "def:a", "lem:b"]


# --- Directives: a mark in the other format's spelling is text ---


def test_an_html_directive_in_a_tex_file_is_text(paper) -> None:
    document = paper(
        doc(
            "\\section{S}\\label{sec:s}\n<!-- @sorry not a directive here -->\n"
            "\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n"
        )
    )

    assert document.directives == ()


def test_a_percent_directive_in_a_markdown_file_is_text(paper) -> None:
    document = paper(
        "# S\n\\label{sec:s}\n% @sorry not a directive here\n"
        "\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n",
        name="paper.md",
    )

    assert document.directives == ()


# --- Citations ---


class TestCites:
    def test_command_and_locator(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "It holds \\autocite[Theorem~3.2]{hardy1949}.\n"
            )
        )

        (citation,) = document.citations
        assert (citation.command, citation.keys, citation.locator) == (
            "autocite",
            ("hardy1949",),
            "Theorem~3.2",
        )

    def test_a_bare_cite_has_no_locator(self, paper) -> None:
        document = paper(doc("\\section{S}\\label{sec:s}\nSee \\cite{erdos1950}.\n"))

        (citation,) = document.citations
        assert citation.command == "cite"
        assert citation.locator is None

    def test_several_keys_in_one_call(self, paper) -> None:
        document = paper(doc("\\section{S}\\label{sec:s}\n\\autocite{a,b,c}\n"))

        (citation,) = document.citations
        assert citation.keys == ("a", "b", "c")

    def test_prenote_and_postnote(self, paper) -> None:
        document = paper(doc("\\section{S}\\label{sec:s}\n\\autocite[see][p. 4]{a}\n"))

        (citation,) = document.citations
        assert citation.locator == "p. 4"


# --- Quotations ---


def quoted(document: Document) -> list[tuple[str, tuple[str, ...], str | None]]:
    return [(q.text, q.keys, q.locator) for q in document.quotations]


class TestQuotations:
    """The three shapes of a quotation site and the term under eight words."""

    def test_a_quote_environment_with_a_cite_inside(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "\\begin{quote}\n"
                "The rate is sharp and bounded above by the constant. "
                "\\cite{hardy1949}\n"
                "\\end{quote}\n"
            )
        )

        (site,) = document.quotations
        # the cite command is apparatus, not quoted words
        assert site.text == "The rate is sharp and bounded above by the constant."
        assert site.keys == ("hardy1949",)
        assert document.text(site.citation) == "\\cite{hardy1949}"
        assert document.text(site.at).startswith("The rate")

    def test_a_quote_environment_with_the_cite_in_the_next_sentence(
        self, paper
    ) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "\\begin{quote}\n"
                "The rate is sharp and bounded above by the constant.\n"
                "\\end{quote}\n"
                "This is shown by \\autocite[Theorem~3.2]{hardy1949}.\n"
            )
        )

        assert quoted(document) == [
            (
                "The rate is sharp and bounded above by the constant.",
                ("hardy1949",),
                "Theorem~3.2",
            )
        ]

    def test_a_quote_environment_cited_after_a_blank_line_is_not_a_site(
        self, paper
    ) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "\\begin{quote}\nThe rate is sharp.\n\\end{quote}\n\n"
                "A new paragraph cites \\cite{hardy1949}.\n"
            )
        )

        assert document.quotations == ()

    def test_a_quote_environment_with_no_cite_is_not_a_site(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "\\begin{quote}\nA quotation no one cites.\n\\end{quote}\n"
            )
        )

        assert document.quotations == ()

    def test_an_enquote_with_a_cite_in_the_same_sentence(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "As \\enquote{the rate is sharp and bounded above} "
                "shows \\cite{hardy1949}.\n"
            )
        )

        assert quoted(document) == [
            ("the rate is sharp and bounded above", ("hardy1949",), None)
        ]

    def test_a_textquote_is_the_same_shape(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "As \\textquote{the rate is sharp} shows \\cite{hardy1949}.\n"
            )
        )

        assert [q.text for q in document.quotations] == ["the rate is sharp"]

    def test_an_enquote_with_no_cite_in_its_sentence_is_not_a_site(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "As \\enquote{the rate is sharp} shows nothing. "
                "A later \\cite{hardy1949} is a new sentence.\n"
            )
        )

        assert document.quotations == ()

    def test_a_straight_double_quoted_span_over_eight_words(self, paper) -> None:
        document = paper(
            "## S\n\n"
            'The result "the rate is sharp and bounded above by the constant" '
            "is due to \\cite{hardy1949}.\n",
            name="paper.md",
        )

        (site,) = document.quotations
        assert site.text == "the rate is sharp and bounded above by the constant"
        assert document.text(site.at) == site.text

    def test_a_curly_double_quoted_span_over_eight_words(self, paper) -> None:
        document = paper(
            "## S\n\n"
            "The result \u201cthe rate is sharp and bounded above by the "
            "constant\u201d is due to \\cite{hardy1949}.\n",
            name="paper.md",
        )

        assert [q.text for q in document.quotations] == [
            "the rate is sharp and bounded above by the constant"
        ]

    def test_latex_s_double_quotes_over_eight_words(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n"
                "The result ``the rate is sharp and bounded above by the "
                "constant'' is due to \\cite{hardy1949}.\n"
            )
        )

        assert [q.text for q in document.quotations] == [
            "the rate is sharp and bounded above by the constant"
        ]

    def test_a_span_wrapped_over_two_lines_is_one_site(self, paper) -> None:
        document = paper(
            "## S\n\n"
            'They define the "rate at which the running mean settles\n'
            'on its limit" of the chain \\autocite[p.~43]{smith2010}.\n',
            name="paper.md",
        )

        (site,) = document.quotations
        assert site.locator == "p.~43"
        assert " ".join(site.text.split()) == (
            "rate at which the running mean settles on its limit"
        )

    def test_a_double_quoted_term_under_eight_words_is_not_recorded(
        self, paper
    ) -> None:
        document = paper(
            '## S\n\nThe "sharp rate" is due to \\cite{hardy1949}.\n',
            name="paper.md",
        )

        assert document.quotations == ()

    def test_a_quote_of_two_keys_is_one_site_naming_both(self, paper) -> None:
        document = paper(
            "## S\n\n"
            'They say "the rate is sharp and bounded above by the constant" '
            "\\autocite{a,b}.\n",
            name="paper.md",
        )

        assert [q.keys for q in document.quotations] == [("a", "b")]


# --- References ---


class TestRefs:
    def test_the_qualifying_word_is_kept(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n\\begin{lemma}\\label{lem:a}\nA.\n"
                "\\end{lemma}\nTheorem~\\ref{lem:a}.\n"
            )
        )

        reference = next(r for r in document.references if r.command == "ref")
        assert reference.word == "Theorem"

    def test_autoref_and_s_ref(self, paper) -> None:
        document = paper(
            doc(
                "\\section{S}\\label{sec:s}\n\\begin{theorem}\\label{thm:a}\nA.\n"
                "\\end{theorem}\n\\autoref{thm:a} and \\S\\ref{sec:s}.\n"
            )
        )

        assert [(r.command, r.word, r.targets) for r in document.references] == [
            ("autoref", None, ("thm:a",)),
            ("ref", "\\S", ("sec:s",)),
        ]


# --- Footnotes: nested braces  ---


def test_a_footnote_span_holds_nested_braces(paper) -> None:
    """The footnote's argument holds a `\\citep{...}` with two keys, and
    the sentence goes on after it."""
    document = paper(FOOTNOTE)

    (footnote,) = document.footnotes
    at_text = document.text(footnote.at)
    inner = document.text(footnote.text)
    assert at_text.startswith("\\footnote{")
    assert at_text.endswith("}")
    assert inner.startswith("Strictly")
    assert inner.endswith("jones2012proper}.")
    assert "\\citep{smith2001scores,jones2012proper}" in inner


# --- Displays: an inline `$$…$$`  ---


def test_an_inline_dollar_display_span(paper) -> None:
    """The display opens and closes mid-line, inside a sentence, as a
    conversion to Markdown writes a LaTeX display."""
    document = paper(INLINE_DISPLAY, name="paper.md")

    (display,) = [e for e in document.environments if e.kind is EnvKind.DISPLAY]
    assert display.name == "$$"
    assert display.closed is True
    body_text = document.text(display.body)
    assert body_text.startswith("\\sum_{j\\in J'}")
    assert body_text.endswith("a^{\\star}.")
    assert document.text(display.span) == "$$" + body_text + "$$"


# --- Macros: recorded, never expanded  ---


def test_macros_are_recorded_not_expanded(paper) -> None:
    document = paper(
        doc(
            "\\section{S}\\label{sec:s}\nUsing \\R and \\norm{x}.\n",
            preamble=(
                "\\newcommand{\\R}{\\mathbb{R}}\n"
                "\\newcommand{\\norm}[1]{\\lVert #1 \\rVert}\n"
                "\\def\\Prob{\\mathbb{P}}\n"
                "\\DeclareMathOperator{\\argmax}{arg\\,max}\n"
                "\\newenvironment{tightlist}{\\begin{itemize}}{\\end{itemize}}\n"
            ),
        )
    )

    macros = {macro.name: macro for macro in document.macros}
    assert set(macros) == {"\\R", "\\norm", "\\Prob", "\\argmax", "tightlist"}
    assert macros["\\R"].command == "newcommand"
    assert macros["\\R"].definition == "{\\mathbb{R}}"
    assert macros["\\norm"].arguments == 1
    assert "#1" in macros["\\norm"].definition
    assert macros["\\Prob"].command == "def"
    assert "\\mathbb{P}" in macros["\\Prob"].definition
    assert macros["\\argmax"].command == "DeclareMathOperator"
    assert "arg\\,max" in macros["\\argmax"].definition
    assert macros["tightlist"].command == "newenvironment"
    assert "itemize" in macros["tightlist"].definition


# --- The title: YAML, after \begin{document}, and in the preamble ---


def test_the_title_in_yaml_front_matter(paper) -> None:
    document = paper(
        '---\ntitle: "A Paper About $X$"\nauthor: A. Author\ndate: 2024\n---\n\n'
        "# S\n\nText.\n",
        name="paper.md",
    )

    assert document.text(document.title) == "A Paper About $X$"
    source = document.file(document.root)
    title = document.title
    assert not [
        hidden
        for hidden in source.hidden
        if hidden.span.start < title.end and hidden.span.end > title.start
    ], "the title is rendered text"
    front = [h for h in source.hidden if h.kind is HiddenKind.FRONT_MATTER]
    assert source.text[front[0].span.start : front[0].span.end] == '---\ntitle: "'
    assert "author: A. Author" in document.text(front[1].span)


def test_the_title_after_begin_document(paper) -> None:
    document = paper(
        doc(
            "\\title{A Paper About $X$}\n\\author{A. Author}\n\\maketitle\n"
            "\\section{S}\\label{sec:s}\nText.\n"
        )
    )

    assert document.text(document.title) == "A Paper About $X$"
    source = document.file(document.root)
    title = document.title
    assert not [
        hidden
        for hidden in source.hidden
        if hidden.span.start < title.end and hidden.span.end > title.start
    ], "the title is rendered text"


def test_the_title_in_the_preamble(paper) -> None:
    """A paper's `\\title` may sit after `\\begin{document}`, but the common
    case is the preamble; both must give the title's span. `\\author` and
    `\\date` here sit in the preamble too, so the preamble region hides them
    (`_paint` repaints `PREAMBLE` last, over any `FRONT_MATTER` coat within
    it); the separate `FRONT_MATTER` kind shows only where a front-matter
    command escapes the preamble, as the next two cases test."""
    document = paper(
        doc(
            "\\section{S}\\label{sec:s}\n\\maketitle\nText.\n",
            preamble=(
                "\\documentclass{article}\n"
                "\\title{A Paper About $X$}\n"
                "\\author{A. Author}\n\\date{2024}\n"
            ),
        )
    )

    assert document.text(document.title) == "A Paper About $X$"
    source = document.file(document.root)
    title = document.title
    assert not [
        hidden
        for hidden in source.hidden
        if hidden.span.start < title.end and hidden.span.end > title.start
    ], "the title is rendered text"
    source = document.file(document.root)
    preamble = [
        document.text(h.span) for h in source.hidden if h.kind is HiddenKind.PREAMBLE
    ]
    assert any("A. Author" in text for text in preamble)
    assert any("2024" in text for text in preamble)


def test_author_date_and_a_thanks_inside_the_title_are_hidden_as_front_matter(
    paper,
) -> None:
    """`\\title`, `\\author` and `\\date` all sit after
    `\\begin{document}`, outside any preamble
    region, so each hides as `FRONT_MATTER` on its own. The title's span is
    rendered text, but a `\\thanks` nested inside it is still
    front matter: the coats painted over the whole file are painted again
    within the title's span."""
    document = paper(
        doc(
            "\\title{A Paper About $X$\\thanks{Funded by Grant~1.}}\n"
            "\\author{A. Author}\n\\date{2024}\n\\maketitle\n"
            "\\section{S}\\label{sec:s}\nText.\n",
            preamble="\\documentclass{article}\n",
        )
    )

    assert document.text(document.title) == (
        "A Paper About $X$\\thanks{Funded by Grant~1.}"
    )
    source = document.file(document.root)
    front_matter = [
        document.text(h.span)
        for h in source.hidden
        if h.kind is HiddenKind.FRONT_MATTER
    ]
    assert any("A. Author" in text for text in front_matter)
    assert any("2024" in text for text in front_matter)
    assert any("Funded by Grant" in text for text in front_matter)


# --- Pandoc's heading attributes  ---


def test_pandoc_heading_attributes_give_the_label_and_are_hidden(paper) -> None:
    document = paper(
        "# S\n\n## Appendix {#appendix .unnumbered}\n\nText.\n", name="paper.md"
    )

    section = next(s for s in document.sections if s.title == "Appendix")
    assert section.label == "appendix"
    assert section.starred is True
    assert any(label.name == "appendix" for label in document.labels)
    source = document.file(document.root)
    structure = [
        document.text(h.span) for h in source.hidden if h.kind is HiddenKind.STRUCTURE
    ]
    assert any("#appendix" in text for text in structure)


# --- A layout-only line is structure  ---


def test_a_layout_only_line_is_structure(paper) -> None:
    document = paper(
        doc("\\section{S}\\label{sec:s}\nBefore.\n\\allowdisplaybreaks\nAfter.\n")
    )

    source = document.file(document.root)
    structure = [
        document.text(h.span) for h in source.hidden if h.kind is HiddenKind.STRUCTURE
    ]
    assert any("allowdisplaybreaks" in text for text in structure)
    assert all("Before." not in text and "After." not in text for text in structure)


def test_a_layout_command_beside_text_is_not_structure(paper) -> None:
    """`\\allowdisplaybreaks` is only hidden when its whole line holds
    nothing else (`LAYOUT_COMMANDS`); unlike `STRUCTURE_RE`'s commands, it is
    not hidden unconditionally."""
    document = paper(
        doc("\\section{S}\\label{sec:s}\nSome text \\allowdisplaybreaks continues.\n")
    )

    source = document.file(document.root)
    structure = [
        document.text(h.span) for h in source.hidden if h.kind is HiddenKind.STRUCTURE
    ]
    assert not any("allowdisplaybreaks" in text for text in structure)


def test_newpage_beside_text_is_still_structure(paper) -> None:
    """`\\newpage` is also one of `STRUCTURE_RE`'s commands, so unlike
    `\\allowdisplaybreaks` it hides wherever it stands, even beside prose on
    the same line."""
    document = paper(
        doc("\\section{S}\\label{sec:s}\nSome text \\newpage continues.\n")
    )

    source = document.file(document.root)
    structure = [
        document.text(h.span) for h in source.hidden if h.kind is HiddenKind.STRUCTURE
    ]
    assert any("newpage" in text for text in structure)


@pytest.mark.parametrize(
    "command",
    ["\\vspace{-.1in}", "\\vspace*{1em}", "\\centering{}", "\\normalsize"],
)
def test_a_line_of_one_spacing_or_size_switch_is_structure(paper, command) -> None:
    """`\\vspace`, `\\centering{}` and `\\normalsize` print nothing on a
    line of their own: they are `LAYOUT_COMMANDS`."""
    document = paper(
        doc(f"\\section{{S}}\\label{{sec:s}}\nBefore.\n{command}\nAfter.\n")
    )

    source = document.file(document.root)
    structure = [
        document.text(h.span) for h in source.hidden if h.kind is HiddenKind.STRUCTURE
    ]
    assert command in structure


@pytest.mark.parametrize(
    "command",
    [
        "\\FloatBarrier",
        "\\medskip",
        "\\bigskip",
        "\\enlargethispage{\\baselineskip}",
        "\\pagebreak[3]",
        "\\small",
        "\\footnotesize",
        "\\Large",
    ],
)
def test_a_line_of_one_float_barrier_skip_or_size_is_structure(paper, command) -> None:
    """`\\FloatBarrier`, the skips, `\\enlargethispage` and the size
    switches stand on lines of their own in many papers and print
    nothing there."""
    document = paper(
        doc(f"\\section{{S}}\\label{{sec:s}}\nBefore.\n{command}\nAfter.\n")
    )

    source = document.file(document.root)
    structure = [
        document.text(h.span) for h in source.hidden if h.kind is HiddenKind.STRUCTURE
    ]
    assert command in structure


def test_a_skip_command_is_not_a_prefix_match(paper) -> None:
    """`\\smallskipamount` is a length, not `\\small` or `\\smallskip`."""
    document = paper(doc("Before.\n\\smallskipamount\nAfter.\n"))

    source = document.file(document.root)
    assert not any(
        "small" in document.text(h.span)
        for h in source.hidden
        if h.kind is HiddenKind.STRUCTURE
    )


def test_a_spacing_switch_beside_text_is_text(paper) -> None:
    document = paper(doc("Before \\vspace{1em} after.\n"))

    source = document.file(document.root)
    assert not any(
        "vspace" in document.text(h.span)
        for h in source.hidden
        if h.kind is HiddenKind.STRUCTURE
    )


def test_a_macro_defined_in_the_body_is_structure(paper) -> None:
    """A paper may set `\\renewcommand{\\arraystretch}{1.15}` in its
    body: a definition prints nothing."""
    document = paper(doc("Text.\n\\renewcommand{\\arraystretch}{1.15}\nMore.\n"))

    source = document.file(document.root)
    structure = [
        document.text(h.span) for h in source.hidden if h.kind is HiddenKind.STRUCTURE
    ]
    assert "\\renewcommand{\\arraystretch}{1.15}" in structure
    assert [macro.name for macro in document.macros] == ["\\arraystretch"]


def test_a_spacing_switch_inside_the_title_is_structure(paper) -> None:
    """`\\title{%` then `\\vspace{-2em}` on a line of its own: the title
    is rendered, the switch is not."""
    document = paper(
        doc("\\maketitle\nText.\n", preamble="\\title{%\n\\vspace{-2em}\nA title\n}\n")
    )

    source = document.file(document.root)
    title = document.text(document.title)
    assert "A title" in title
    rendered_title = "".join(
        char
        for index, char in enumerate(source.text)
        if document.title.start <= index < document.title.end
        and not any(h.span.start <= index < h.span.end for h in source.hidden)
    )
    assert rendered_title.split() == ["A", "title"]


# --- Reading order across an \input  ---


def test_reading_order_across_an_input(paper) -> None:
    document = paper(
        doc(
            "\\section{Before}\\label{sec:before}\nBefore text.\n"
            "\\input{included}\n"
            "\\section{After}\\label{sec:after}\nAfter text.\n"
        ),
        extra={
            "included.tex": (
                "\\section{Included}\\label{sec:included}\nIncluded text.\n"
            )
        },
    )

    assert [s.label for s in document.sections] == [
        "sec:before",
        "sec:included",
        "sec:after",
    ]


# --- The hidden map: coverage  ---


def test_every_unhidden_character_is_body_and_outside_comments(paper) -> None:
    """On a small paper, every character the hidden map does not cover lies
    between `\\begin{document}` and `\\end{document}` and outside a `%`
    comment: what the blocks must cover, and nothing else."""
    text = (
        doc(
            "\\section{S}\\label{sec:s}\n"
            "Rendered prose. % a trailing comment\n"
            "\\iffalse\nHidden by iffalse.\n\\fi\n"
            "More rendered prose.\n",
            preamble="\\documentclass{article}\n\\newcommand{\\R}{\\mathbb{R}}\n",
        )
        + "After the end.\n"
    )
    document = paper(text)

    source = document.file(document.root)
    body_start = text.index("\\begin{document}") + len("\\begin{document}")
    body_end = text.index("\\end{document}")
    hidden = bytearray(len(text))
    for h in source.hidden:
        for index in range(h.span.start, h.span.end):
            hidden[index] = 1
    for index, flag in enumerate(hidden):
        if not flag:
            assert body_start <= index < body_end
            assert text[index] != "%"
