"""The files a paper is made of.

Each case writes a small paper under `tmp_path` and reads it with
`axiomation.reader.includes`: `find_root` for the root file, `walk` for the
files it reaches, the includes that reached them, and the files it never
reached.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.reader.document import FileRole, Format
from axiomation.reader.includes import ReaderError, find_root, walk

# --- Finding the root ---


class TestFindingTheRoot:
    def test_the_one_tex_file_with_begin_document_is_the_root(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nHi.\n\\end{document}\n",
            encoding="utf-8",
        )
        (tmp_path / "macros.tex").write_text("\\newcommand{\\x}{1}\n", encoding="utf-8")

        assert find_root(tmp_path) == (tmp_path / "main.tex").resolve()

    def test_two_roots_are_ambiguous_and_the_error_names_both(self, tmp_path) -> None:
        for name in ("a.tex", "b.tex"):
            (tmp_path / name).write_text(
                "\\begin{document}\nHi.\n\\end{document}\n", encoding="utf-8"
            )

        with pytest.raises(ReaderError) as excinfo:
            find_root(tmp_path)

        message = str(excinfo.value)
        assert "a.tex" in message
        assert "b.tex" in message

    def test_root_given_by_name_overrides_the_search(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\nHi.\n\\end{document}\n", encoding="utf-8"
        )
        (tmp_path / "other.tex").write_text(
            "\\begin{document}\nBye.\n\\end{document}\n", encoding="utf-8"
        )

        assert (
            find_root(tmp_path, Path("other.tex")) == (tmp_path / "other.tex").resolve()
        )


# --- Body includes: reading order and nesting ---


class TestBodyIncludes:
    def test_body_includes_come_in_reading_order_and_nest(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "before\n\\input{a}\nafter\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        (tmp_path / "a.tex").write_text("in a\n\\input{b}\nstill a\n", encoding="utf-8")
        (tmp_path / "b.tex").write_text("in b\n", encoding="utf-8")

        found = walk(tmp_path, find_root(tmp_path))

        assert [source.path for source in found.files] == ["main.tex", "a.tex", "b.tex"]
        assert [(include.at.file, include.target) for include in found.includes] == [
            ("main.tex", "a"),
            ("a.tex", "b"),
        ]

    def test_a_last_line_input_with_no_trailing_newline_is_followed(
        self, tmp_path
    ) -> None:
        """A file that ends in an `\\input{…}` with no newline after it:
        the include is still followed."""
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\n\\input{proof}\n\\end{document}\n", encoding="utf-8"
        )
        (tmp_path / "proof.tex").write_text(
            "the proof\n\\input{more}", encoding="utf-8"
        )
        (tmp_path / "more.tex").write_text("more text\n", encoding="utf-8")

        found = walk(tmp_path, find_root(tmp_path))

        assert [source.path for source in found.files] == [
            "main.tex",
            "proof.tex",
            "more.tex",
        ]

    def test_input_without_extension_include_and_subimport(self, tmp_path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\n"
            "\\input{a}\n"
            "\\include{b}\n"
            "\\subimport{sub/}{c}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        (tmp_path / "a.tex").write_text("a\n", encoding="utf-8")
        (tmp_path / "b.tex").write_text("b\n", encoding="utf-8")
        (tmp_path / "sub" / "c.tex").write_text("c\n", encoding="utf-8")

        found = walk(tmp_path, find_root(tmp_path))

        assert [source.path for source in found.files] == [
            "main.tex",
            "a.tex",
            "b.tex",
            "sub/c.tex",
        ]
        assert [include.command for include in found.includes] == [
            "input",
            "include",
            "subimport",
        ]


# --- What is not followed ---


class TestUnfollowedIncludes:
    def test_a_commented_include_is_not_followed(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\n% \\input{ghost}\n\\end{document}\n",
            encoding="utf-8",
        )
        (tmp_path / "ghost.tex").write_text("boo\n", encoding="utf-8")

        found = walk(tmp_path, find_root(tmp_path))

        assert found.includes == ()
        assert [source.path for source in found.files] == ["main.tex"]

    def test_an_iffalse_include_is_not_followed(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\n\\iffalse\n\\input{ghost}\n\\fi\n\\end{document}\n",
            encoding="utf-8",
        )
        (tmp_path / "ghost.tex").write_text("boo\n", encoding="utf-8")

        found = walk(tmp_path, find_root(tmp_path))

        assert found.includes == ()

    def test_a_verbatim_include_is_not_followed(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\n"
            "\\begin{verbatim}\n\\input{ghost}\n\\end{verbatim}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        (tmp_path / "ghost.tex").write_text("boo\n", encoding="utf-8")

        found = walk(tmp_path, find_root(tmp_path))

        assert found.includes == ()


# --- File roles ---


class TestFileRoles:
    def test_a_preamble_include_is_role_preamble(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n\\input{defs}\n"
            "\\begin{document}\nHi.\n\\end{document}\n",
            encoding="utf-8",
        )
        (tmp_path / "defs.tex").write_text("\\newcommand{\\x}{1}\n", encoding="utf-8")

        found = walk(tmp_path, find_root(tmp_path))
        defs = next(source for source in found.files if source.path == "defs.tex")

        assert defs.role is FileRole.PREAMBLE
        assert found.includes[0].role is FileRole.PREAMBLE

    def test_a_shipped_style_file_is_role_package(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n\\usepackage{mine}\n"
            "\\begin{document}\nHi.\n\\end{document}\n",
            encoding="utf-8",
        )
        (tmp_path / "mine.sty").write_text("\\newcommand{\\y}{2}\n", encoding="utf-8")

        found = walk(tmp_path, find_root(tmp_path))

        assert found.includes[0].role is FileRole.PACKAGE
        assert found.includes[0].resolved == "mine.sty"
        assert found.files[1].format is Format.LATEX

    def test_a_package_not_shipped_beside_the_paper_is_no_include(
        self, tmp_path
    ) -> None:
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n\\usepackage{amsmath}\n"
            "\\begin{document}\nHi.\n\\end{document}\n",
            encoding="utf-8",
        )

        found = walk(tmp_path, find_root(tmp_path))

        assert found.includes == ()
        assert [source.path for source in found.files] == ["main.tex"]


# --- Unresolved includes ---


class TestUnresolvedIncludes:
    def test_an_unresolvable_include_is_recorded_with_resolved_none(
        self, tmp_path
    ) -> None:
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\n\\input{missing}\n\\end{document}\n",
            encoding="utf-8",
        )

        found = walk(tmp_path, find_root(tmp_path))

        assert len(found.includes) == 1
        assert found.includes[0].target == "missing"
        assert found.includes[0].resolved is None
        assert [source.path for source in found.files] == ["main.tex"]

    def test_a_macro_built_path_is_recorded_unresolved(self, tmp_path) -> None:
        """A path built from a macro, `\\input{\\foo}`, keeps its backslash
        and is never resolved."""
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\n\\input{\\foo}\n\\end{document}\n",
            encoding="utf-8",
        )

        found = walk(tmp_path, find_root(tmp_path))

        assert found.includes[0].target == "\\foo"
        assert found.includes[0].resolved is None


# --- A cycle stops ---


class TestReadOnce:
    def test_a_file_that_includes_itself_is_read_once(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n\\input{main}\n"
            "\\begin{document}\nHi.\n\\end{document}\n",
            encoding="utf-8",
        )

        found = walk(tmp_path, find_root(tmp_path))

        assert [source.path for source in found.files] == ["main.tex"]
        assert found.includes[0].resolved == "main.tex"


# --- Never included ---


class TestNeverIncluded:
    def test_a_stray_tex_and_a_stray_md_are_never_included(self, tmp_path) -> None:
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\nHi.\n\\end{document}\n", encoding="utf-8"
        )
        (tmp_path / "orphan.tex").write_text("nobody reads me\n", encoding="utf-8")
        (tmp_path / "notes.md").write_text("nor me\n", encoding="utf-8")

        found = walk(tmp_path, find_root(tmp_path))

        assert found.never_included == ("notes.md", "orphan.tex")

    def test_a_file_in_a_hidden_directory_is_not_listed(self, tmp_path) -> None:
        hidden = tmp_path / ".git"
        hidden.mkdir()
        (hidden / "stray.tex").write_text("nope\n", encoding="utf-8")
        (tmp_path / "main.tex").write_text(
            "\\begin{document}\nHi.\n\\end{document}\n", encoding="utf-8"
        )

        found = walk(tmp_path, find_root(tmp_path))

        assert found.never_included == ()


# --- A Markdown root ---


class TestMarkdownRoot:
    def test_a_single_markdown_file_is_the_root(self, tmp_path) -> None:
        (tmp_path / "essay.md").write_text("# Title\n", encoding="utf-8")

        assert find_root(tmp_path) == (tmp_path / "essay.md").resolve()

    def test_paper_md_is_the_root_among_several(self, tmp_path) -> None:
        (tmp_path / "paper.md").write_text("# Title\n", encoding="utf-8")
        (tmp_path / "notes.md").write_text("# Notes\n", encoding="utf-8")

        assert find_root(tmp_path) == (tmp_path / "paper.md").resolve()


def test_an_include_after_end_document_is_not_followed(tmp_path) -> None:
    """LaTeX stops reading at `\\end{document}`."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "main.tex").write_text(
        "\\begin{document}\nText.\n\\end{document}\n\\input{late}\n", encoding="utf-8"
    )
    (root / "late.tex").write_text("Never read.\n", encoding="utf-8")

    found = walk(root, root / "main.tex")

    assert [source.path for source in found.files] == ["main.tex"]
    assert found.includes == ()
    assert found.never_included == ("late.tex",)


def test_a_given_root_outside_the_directory_is_an_error(tmp_path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    outside = tmp_path / "elsewhere.tex"
    outside.write_text("\\begin{document}\n\\end{document}\n", encoding="utf-8")

    with pytest.raises(ReaderError, match="is not a file in"):
        find_root(root, outside)
