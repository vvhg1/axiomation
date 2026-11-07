"""Line mechanics of the Markdown+LaTeX and plain-LaTeX formats.

Covers `get_format`, line stripping, heading levels, titles, the fence map,
environment ends and trailing proofs (with column results), the format
choice by file extension (`.sty` and `.cls` read as LaTeX) and the LaTeX
format's inheritance of the environment syntax.

Also the column-precise helpers: `LatexFormat.heading_end`,
`heading_starred` (both formats), `MdLatexFormat.heading_attributes`,
`references()`'s `FoundReference`, and `formats.latex.read_mask` and
`unread_blocks`.
"""

from __future__ import annotations

import pytest

from axiomation.reader.formats import (
    LatexFormat,
    MdLatexFormat,
    UnknownFormatError,
    format_for,
    get_format,
)
from axiomation.reader.formats.latex import read_mask, unread_blocks
from axiomation.reader.formats.md_latex import EnvEnd, TrailingProof, references


@pytest.fixture
def fmt() -> MdLatexFormat:
    return MdLatexFormat()


class TestGetFormat:
    def test_md_latex(self):
        assert isinstance(get_format("md-latex"), MdLatexFormat)

    def test_default(self):
        assert isinstance(get_format(), MdLatexFormat)

    def test_unknown_raises(self):
        with pytest.raises(UnknownFormatError, match="unknown document format"):
            get_format("xml")


def test_a_tex_file_is_latex_and_anything_else_markdown() -> None:
    assert format_for("paper.tex").name == "latex"
    assert format_for("Paper.TEX").name == "latex"
    assert format_for("paper.md").name == "md-latex"
    assert format_for("notes.txt").name == "md-latex"
    # a `.sty` or `.cls` shipped beside a paper
    # is read through the same passes as a `.tex` file.
    assert format_for("macros.sty").name == "latex"
    assert format_for("Class.CLS").name == "latex"


class TestStripLine:
    def test_strips_latex(self, fmt):
        assert fmt.strip_line("\\begin{theorem}[Main]") == "[Main]"

    def test_strips_heading(self, fmt):
        assert fmt.strip_line("## 3.1 Background") == "3.1 Background"

    def test_collapses_math_spaces(self, fmt):
        assert fmt.strip_line("Text $x + y$ more") == "Text $x+y$ more"

    def test_display_math_on_a_prose_line_does_not_shift_the_delimiters(self, fmt):
        """A `$$ ... $$` inline with prose pairs its closing delimiter with
        its opening one, never with the prose after it: paired wrong, every
        run from there on is off by one, and two copies of one sentence
        normalize differently depending on what precedes them."""
        line = "Back: $$ L = C^{-a}. $$ Expanding at $r = 0$: $$ b = c. $$ For $s = 1$:"

        assert fmt.strip_line(line) == (
            "Back: $$L=C^{-a}.$$ Expanding at $r=0$: $$b=c.$$ For $s=1$:"
        )

    def test_one_sentence_normalizes_the_same_wherever_it_sits(self, fmt):
        """The property the anchor rests on: content, nothing positional."""
        alone = fmt.strip_line("Expanding at $r = 0$:")
        after_display = fmt.strip_line("$$ L = C^{-a}. $$ Expanding at $r = 0$:")

        assert alone == "Expanding at $r=0$:"
        assert after_display.endswith(alone)

    def test_a_lone_display_delimiter_is_left_alone(self, fmt):
        """Display math over several lines is the ordinary spelling, and
        `strip_line` reads one line, so neither half is a run of its own."""
        assert fmt.strip_line("$$") == "$$"
        assert fmt.strip_line("L = A + B") == "L = A + B"

    def test_empty(self, fmt):
        assert fmt.strip_line("") == ""
        assert fmt.strip_line("  ") == ""


class TestHeadingLevel:
    def test_levels(self, fmt):
        assert fmt.heading_level("# Title") == 1
        assert fmt.heading_level("## Sub") == 2
        assert fmt.heading_level("### Subsub") == 3

    def test_deep_heading(self, fmt):
        """A #### heading is still a heading."""
        assert fmt.heading_level("#### Deep") == 4

    def test_not_heading(self, fmt):
        assert fmt.heading_level("plain text") is None
        assert fmt.heading_level("\\begin{theorem}") is None


class TestTitles:
    def test_heading_title_drops_label(self, fmt):
        assert fmt.heading_title("## 5.5 Scaling \\label{sec:scaling}") == "5.5 Scaling"

    def test_env_title(self, fmt):
        assert fmt.env_title("\\begin{theorem}[Main result]") == "Main result"

    def test_env_title_absent(self, fmt):
        assert fmt.env_title("\\begin{theorem}") is None


class TestBuildFenceMap:
    def test_fenced(self, fmt):
        lines = ["before", "```", "inside", "```", "after"]
        assert fmt.build_fence_map(lines) == [False, True, True, False, False]

    def test_no_fences(self, fmt):
        assert fmt.build_fence_map(["a", "b"]) == [False, False]


def test_the_latex_format_inherits_the_environment_syntax() -> None:
    assert LatexFormat().statement_envs == format_for("a.md").statement_envs


class TestFindEnvEnd:
    def test_multiline(self, fmt):
        lines = ["\\begin{theorem}[Main]", "Statement.", "\\end{theorem}"]
        assert fmt.find_env_end(lines, 0, "theorem") == EnvEnd(2, 13, True)

    def test_same_line(self, fmt):
        lines = ["\\begin{proof}[Sketch] Direct computation. \\qed \\end{proof}"]
        assert fmt.find_env_end(lines, 0, "proof") == EnvEnd(0, 58, True)

    def test_nested_depth(self, fmt):
        lines = [
            "\\begin{remark}",
            "\\begin{remark}Inner.\\end{remark}",
            "Outer continues.",
            "\\end{remark}",
        ]
        assert fmt.find_env_end(lines, 0, "remark") == EnvEnd(3, 12, True)

    def test_fence_map_skips(self, fmt):
        lines = [
            "\\begin{theorem}",
            "```",
            "\\end{theorem}",
            "```",
            "Real content.",
            "\\end{theorem}",
        ]
        fence_map = [False, True, True, False, False, False]
        assert fmt.find_env_end(lines, 0, "theorem", fence_map) == EnvEnd(5, 13, True)

    def test_no_fence_map(self, fmt):
        lines = ["\\begin{theorem}", "```", "\\end{theorem}", "```"]
        assert fmt.find_env_end(lines, 0, "theorem", None) == EnvEnd(2, 13, True)

    def test_not_found(self, fmt):
        """Unclosed gives the last line, unclosed, at its own length."""
        lines = ["\\begin{theorem}", "No closing tag."]
        assert fmt.find_env_end(lines, 0, "theorem") == EnvEnd(1, 15, False)


class TestFindTrailingProof:
    def test_proof_after_env(self, fmt):
        lines = [
            "\\begin{lemma}",
            "Statement.",
            "\\end{lemma}",
            "",
            "\\begin{proof}",
            "By computation.",
            "\\end{proof}",
        ]
        assert fmt.find_trailing_proof(lines, 2) == TrailingProof(
            4, 0, EnvEnd(6, 11, True)
        )

    def test_same_line_proof(self, fmt):
        lines = [
            "\\begin{lemma}",
            "Statement.",
            "\\end{lemma}",
            "",
            "\\begin{proof}[Sketch] Direct. \\qed \\end{proof}",
        ]
        assert fmt.find_trailing_proof(lines, 2) == TrailingProof(
            4, 0, EnvEnd(4, 46, True)
        )

    def test_no_proof(self, fmt):
        assert fmt.find_trailing_proof(["\\end{lemma}", "", "Some prose."], 0) is None

    def test_blank_lines_between(self, fmt):
        lines = [
            "\\end{theorem}",
            "",
            "",
            "\\begin{proof}",
            "Proof text.",
            "\\end{proof}",
        ]
        assert fmt.find_trailing_proof(lines, 0) == TrailingProof(
            3, 0, EnvEnd(5, 11, True)
        )

    def test_gap_too_large(self, fmt):
        lines = ["\\end{theorem}"] + [""] * 10 + ["\\begin{proof}", "\\end{proof}"]
        assert fmt.find_trailing_proof(lines, 0, max_gap=5) is None

    def test_non_proof_stops_search(self, fmt):
        lines = [
            "\\end{theorem}",
            "",
            "\\begin{remark}Note.\\end{remark}",
            "\\begin{proof}",
            "\\end{proof}",
        ]
        assert fmt.find_trailing_proof(lines, 0) is None


class TestHeadingEnd:
    """`LatexFormat.heading_end`, the column-precise close of a heading's
    braced title, wrapped titles included."""

    def test_a_wrapped_title_reads_over_its_lines(self):
        lines = [
            "\\section{A very long title that",
            "wraps across two lines}",
            "Body text.",
        ]
        assert LatexFormat.heading_end(lines, 0) == (1, 23)

    def test_a_title_on_one_line_ends_on_it(self):
        lines = ["\\section{Short}", "Body."]
        assert LatexFormat.heading_end(lines, 0) == (0, 15)

    def test_a_title_that_never_closes_ends_with_its_line(self):
        lines = ["\\section{Unclosed", "still no brace"]
        assert LatexFormat.heading_end(lines, 0) == (0, 17)


class TestHeadingStarred:
    """`heading_starred` on both formats. LaTeX reads `\\section*`;
    Markdown reads pandoc's `.unnumbered` or `-` attribute."""

    def test_a_starred_latex_command_is_unnumbered(self):
        assert LatexFormat.heading_starred("\\section*{No number}") is True

    def test_an_unstarred_latex_command_is_numbered(self):
        assert LatexFormat.heading_starred("\\section{Numbered}") is False

    def test_a_non_heading_line_is_not_starred(self):
        assert LatexFormat.heading_starred("plain text") is False

    def test_pandocs_unnumbered_class_is_starred(self):
        assert MdLatexFormat.heading_starred("# Appendix {#appendix .unnumbered}")

    def test_pandocs_dash_is_starred(self):
        assert MdLatexFormat.heading_starred("# Appendix {#appendix -}")

    def test_no_attributes_is_not_starred(self):
        assert MdLatexFormat.heading_starred("# Appendix {#appendix}") is False


class TestHeadingAttributes:
    """`MdLatexFormat.heading_attributes`, pandoc's `{#id .class}`."""

    def test_the_id_and_unnumbered_flag_on_an_appendix_heading(self):
        line = "# Appendix {#appendix .unnumbered}"
        assert MdLatexFormat.heading_attributes(line) == (11, 34, "appendix", True)

    def test_no_attributes_gives_none(self):
        assert MdLatexFormat.heading_attributes("# Plain heading") is None

    def test_a_non_heading_line_gives_none(self):
        assert MdLatexFormat.heading_attributes("plain text {#not a heading}") is None


class TestReferences:
    """`references()`'s `FoundReference`. Columns bound the command,
    not the word before it."""

    def test_the_word_precedes_the_command_but_the_span_starts_at_it(self):
        text = "See Theorem~\\ref{thm:a} above."
        found = references(text)
        assert len(found) == 1
        assert found[0].word == "Theorem"
        assert found[0].command == "ref"
        assert found[0].targets == ("thm:a",)
        assert text[found[0].start : found[0].end] == "\\ref{thm:a}"

    def test_a_cleveref_list_names_every_target(self):
        text = "As \\cref{lem:a,lem:b} show."
        found = references(text)
        assert found[0].command == "cref"
        assert found[0].targets == ("lem:a", "lem:b")

    def test_a_cleveref_range_names_both_ends(self):
        text = "See \\Crefrange{lem:a}{lem:b}."
        found = references(text)
        assert found[0].command == "Crefrange"
        assert found[0].targets == ("lem:a", "lem:b")

    def test_a_comma_outside_cleveref_is_part_of_the_label(self):
        found = references("\\ref{lem:a,b_c}")
        assert found[0].command == "ref"
        assert found[0].targets == ("lem:a,b_c",)

    def test_s_ref(self):
        text = "See \\S\\ref{sec:intro}."
        found = references(text)
        assert found[0].command == "S\\ref"
        assert found[0].targets == ("sec:intro",)
        assert text[found[0].start : found[0].end] == "\\S\\ref{sec:intro}"

    def test_no_pageref(self):
        assert references("\\pageref{sec:intro}") == []


class TestReadMask:
    """`formats.latex.read_mask`, the character-precise comment,
    verbatim and `\\iffalse` mask."""

    def test_a_comment_is_masked_to_end_of_line_length_kept(self):
        text = "before % a comment\nafter"
        mask = read_mask(text)
        assert mask.comments == ((7, 18),)
        assert mask.visible == "before" + " " * 12 + "\nafter"
        assert len(mask.visible) == len(text)

    def test_a_percent_inside_verbatim_is_not_a_comment(self):
        text = "\\begin{verbatim}\n50% done\n\\end{verbatim}\nafter"
        mask = read_mask(text)
        assert mask.comments == ()
        verbatim = mask.verbatims[0]
        assert (verbatim.name, verbatim.closed) == ("verbatim", True)
        assert "%" not in mask.visible[verbatim.body_start : verbatim.body_end]
        assert len(mask.visible) == len(text)

    def test_an_iffalse_block_is_masked_up_to_its_else(self):
        text = "before \\iffalse hidden \\else shown \\fi after"
        mask = read_mask(text)
        disabled = mask.disabled[0]
        assert text[disabled.start : disabled.end] == "\\iffalse hidden \\else"
        assert "hidden" not in mask.visible
        assert "shown" in mask.visible
        assert "\\fi" in mask.visible
        assert len(mask.visible) == len(text)

    def test_a_conditional_inside_a_definition_opens_nothing(self):
        for definition in (
            "\\def\\ifexpository{\\iffalse}",
            "\\gdef\\hide#1#2{\\iffalse}",
            "\\let\\ifdraft\\iffalse",
            "\\let\\ifdraft = \\iffalse",
            "\\newcommand{\\hide}[1]{\\iffalse #1}",
            "\\renewcommand*\\hide[2][d]{\\ifx#1#2\\fi\\iffalse}",
            "\\newif\\ifdraft",
            "\\def\\hide#1\n  {\\iffalse}",
        ):
            text = definition + "\nshown \\iffalse hidden \\fi after"
            mask = read_mask(text)
            assert "shown" in mask.visible, definition
            assert "hidden" not in mask.visible, definition
            assert "after" in mask.visible, definition

    def test_newlines_are_kept_through_every_masked_line(self):
        text = "a % c\nb % d\nc"
        mask = read_mask(text)
        assert mask.visible.count("\n") == text.count("\n")
        assert mask.visible == "a    \nb    \nc"


class TestUnreadBlocks:
    """`formats.latex.unread_blocks`, by lines over `read_mask`."""

    def test_a_verbatim_body_is_unread_begin_and_end_included(self):
        lines = ["before", "\\begin{verbatim}", "code", "\\end{verbatim}", "after"]
        assert unread_blocks(lines) == [False, True, True, True, False]

    def test_an_iffalse_block_with_no_else_is_unread_to_its_fi(self):
        lines = ["before", "\\iffalse", "hidden", "\\fi", "after"]
        assert unread_blocks(lines) == [False, True, True, True, False]

    def test_an_iffalse_else_block_is_unread_only_up_to_the_else(self):
        lines = ["before", "\\iffalse", "hidden", "\\else", "shown", "\\fi", "after"]
        assert unread_blocks(lines) == [
            False,
            True,
            True,
            True,
            False,
            False,
            False,
        ]
