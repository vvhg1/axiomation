"""The environments and reference commands a paper declares.

Each case writes a small paper, or one file's text, and reads what
`axiomation.reader.declarations` makes of it.

`TestWhereTheyAre` covers where declarations are read: `includes.walk`
follows a body include too, and `read_declarations` reads every file the
walk reached, whatever its `FileRole` — so a `\\newtheorem` inside a
body-included file *is* read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiomation.reader import read_paper
from axiomation.reader.declarations import (
    environment_table,
    meaning,
    read_declarations,
    reference_table,
)
from axiomation.reader.document import EnvKind, FileRole, Format, SourceFile
from axiomation.reader.formats.md_latex import ReferenceTable
from axiomation.reader.includes import find_root, walk

PREAMBLE = "\\documentclass{article}\n\\usepackage{amsthm}\n"
MACROS = (
    "\\newtheorem{thm}{Theorem}\n"
    "\\newtheorem{lem}[thm]{Lemma}\n"
    "\\newtheorem{defn}{Definition}\n"
    "\\newtheorem{ex}{Example}\n"
)
BODY = (
    "\\section{Results}\\label{sec:results}\n"
    "\\begin{defn}\\label{def:a}\nA thing is a thing.\n\\end{defn}\n\n"
    "\\begin{lem}\\label{lem:b}\nBy Definition~\\ref{def:a}, it holds.\n\\end{lem}\n"
    "\\begin{proof}\nDirect from Definition~\\ref{def:a}.\n\\end{proof}\n\n"
    "\\begin{thm}\\label{thm:c}\nLemma~\\ref{lem:b} gives the bound.\n\\end{thm}\n"
)


def write_paper(
    root: Path,
    body: str = BODY,
    macros: str | None = MACROS,
    *,
    preamble: str = PREAMBLE,
) -> Path:
    """`main.tex`, and `macros.tex` beside it when `macros` is given."""
    root.mkdir(parents=True, exist_ok=True)
    included = "\\input{macros}\n" if macros is not None else ""
    (root / "main.tex").write_text(
        preamble + included + "\\begin{document}\n" + body + "\\end{document}\n",
        encoding="utf-8",
    )
    if macros is not None:
        (root / "macros.tex").write_text(macros, encoding="utf-8")
    return root


def _source(text: str, path: str = "main.tex") -> SourceFile:
    """A LaTeX `SourceFile` built from text alone: `read_declarations` reads
    what a walk gives it, and never touches the disk itself."""
    return SourceFile(
        path=path,
        role=FileRole.ROOT,
        format=Format.LATEX,
        sha256="",
        text=text,
        hidden=(),
    )


def declared(text: str) -> dict[str, tuple[str, str | None]]:
    """What one file declares: name to (printed, meaning)."""
    return {
        entry.name: (entry.printed, entry.meaning)
        for entry in read_declarations([_source(text)])
    }


def files_of(root: Path) -> tuple[SourceFile, ...]:
    """The files the walk reads from `root`."""
    return walk(root, find_root(root)).files


# --- The spellings ---


class TestTheSpellings:
    def test_amsthm_with_a_shared_counter_and_a_within(self) -> None:
        found = declared(
            "\\newtheorem{thm}{Theorem}[section]\n\\newtheorem{lem}[thm]{Lemma}\n"
        )

        assert found == {"thm": ("Theorem", "theorem"), "lem": ("Lemma", "lemma")}

    def test_a_starred_declaration_is_one(self) -> None:
        assert declared("\\newtheorem*{rem}{Remark}\n") == {"rem": ("Remark", "remark")}

    def test_springer_takes_two_font_arguments_after(self) -> None:
        found = declared(
            "\\spnewtheorem{conj}[theorem]{Conjecture}{\\bfseries}{\\itshape}\n"
        )

        assert found == {"conj": ("Conjecture", "conjecture")}

    def test_thmtools_names_the_printed_word_in_its_options(self) -> None:
        found = declared(
            "\\declaretheorem[style=basic,sibling=theorem,name={Lemma}]{lemma}\n"
            "\\declaretheorem[title=Closing Remark]{closingremark}\n"
            "\\declaretheorem{corollary}\n"
            "\\declaretheorem[style=definition,name={Remark},numbered=no]{remark*}\n"
        )

        assert found == {
            "lemma": ("Lemma", "lemma"),
            "closingremark": ("Closing Remark", "remark"),
            "corollary": ("Corollary", "corollary"),
            "remark*": ("Remark", "remark"),
        }

    def test_a_printed_name_with_a_font_command_reads_as_its_word(self) -> None:
        assert declared("\\newtheorem{thm}{{\\bf Theorem}}\n") == {
            "thm": ("Theorem", "theorem")
        }

    def test_a_declaration_across_lines_is_read(self) -> None:
        found = declared("\\newtheorem{lem}\n  [thm]\n  {Lemma}\n")

        assert found == {"lem": ("Lemma", "lemma")}

    def test_a_style_command_declares_nothing(self) -> None:
        text = (
            "\\newtheoremstyle{mine}{3pt}{3pt}{}{}{\\bfseries}{.}{ }{}\n"
            "\\declaretheoremstyle[headfont=\\bfseries]{basic}\n"
        )

        assert declared(text) == {}

    def test_a_commented_or_disabled_declaration_is_none(self) -> None:
        text = (
            "% \\newtheorem{thm}{Theorem}\n"
            "\\iffalse\n\\newtheorem{lem}{Lemma}\n\\fi\n"
            "\\begin{verbatim}\n\\newtheorem{prop}{Proposition}\n\\end{verbatim}\n"
        )

        assert declared(text) == {}

    def test_the_first_declaration_of_a_name_wins(self) -> None:
        text = "\\newtheorem{thm}{Theorem}\n\\newtheorem{thm}{Lemma}\n"

        assert declared(text) == {"thm": ("Theorem", "theorem")}

    def test_a_protect_name_macro_prints_and_means_its_word(self) -> None:
        """babel's and LyX's `\\<word>name` convention, as a LyX export
        writes it: each `\\newtheorem` under its `\\theoremstyle`, the
        name a `\\protect`ed macro."""
        text = (
            PREAMBLE
            + "\\numberwithin{equation}{section}\n"
            + "\\theoremstyle{plain}\n"
            + "\\newtheorem{thm}{\\protect\\theoremname}\n"
            + "\\theoremstyle{definition}\n"
            + "\\newtheorem{defn}[thm]{\\protect\\definitionname}\n"
            + "\\theoremstyle{definition}\n"
            + "\\newtheorem{example}[thm]{\\protect\\examplename}\n"
            + "\\theoremstyle{plain}\n"
            + "\\newtheorem{lem}[thm]{\\protect\\lemmaname}\n"
            + "\\begin{document}\n\\end{document}\n"
        )

        found = declared(text)

        assert found["thm"] == ("Theorem", "theorem")
        assert found["lem"] == ("Lemma", "lemma")


# --- What a name means ---


class TestWhatANameMeans:
    @pytest.mark.parametrize(
        ("name", "printed", "expected"),
        [
            ("thm", "Theorem", "theorem"),
            ("closingremark", "Closing Remark", "remark"),
            ("assum", "Assumption", "assumption"),
            ("model", "Example", None),
            ("claim", "Claim", None),
            # A standard name keeps its meaning when the printed word has none.
            ("theorem", "Satz", "theorem"),
            ("satz", "Satz", None),
        ],
    )
    def test_the_last_printed_word_or_the_standard_name(
        self, name, printed, expected
    ) -> None:
        assert meaning(name, printed) == expected


# --- Where they are read ---


class TestWhereTheyAre:
    def test_a_preamble_input_is_read_as_a_preamble_file(self, tmp_path) -> None:
        root = write_paper(tmp_path / "p")
        files = files_of(root)

        assert {entry.name for entry in read_declarations(files)} == {
            "thm",
            "lem",
            "defn",
            "ex",
        }
        macros = next(source for source in files if source.path == "macros.tex")
        assert macros.role is FileRole.PREAMBLE

    def test_a_package_shipped_beside_the_paper_is_read(self, tmp_path) -> None:
        root = write_paper(
            tmp_path / "p",
            macros=None,
            preamble="\\documentclass{article}\n\\usepackage{amsthm,mine}\n",
        )
        (root / "mine.sty").write_bytes(
            b"% caf\xe9, in Latin-1\n\\newtheorem{lem}{Lemma}\n"
        )
        files = files_of(root)
        found = read_declarations(files)

        assert [entry.name for entry in found] == ["lem"]
        mine = next(source for source in files if source.path == "mine.sty")
        assert mine.role is FileRole.PACKAGE
        assert mine.format is Format.LATEX

    def test_a_local_class_is_read(self, tmp_path) -> None:
        root = write_paper(
            tmp_path / "p", macros=None, preamble="\\documentclass{paperclass}\n"
        )
        (root / "paperclass.cls").write_text(
            "\\LoadClass{article}\n\\newtheorem{lem}{Lemma}\n", encoding="utf-8"
        )

        assert [entry.name for entry in read_declarations(files_of(root))] == ["lem"]

    def test_an_include_in_the_body_is_followed(self, tmp_path) -> None:
        """The walk follows a body include too, so a `\\newtheorem`
        inside one is read here."""
        root = write_paper(tmp_path / "p", body="\\input{later}\n", macros=None)
        (root / "later.tex").write_text("\\newtheorem{lem}{Lemma}\n", encoding="utf-8")

        assert [entry.name for entry in read_declarations(files_of(root))] == ["lem"]

    def test_a_declaration_file_that_inputs_itself_is_read_once(self, tmp_path) -> None:
        root = write_paper(tmp_path / "p", macros="\\input{macros}\n" + MACROS)
        files = files_of(root)

        assert [source.path for source in files] == ["main.tex", "macros.tex"]
        assert {entry.name for entry in read_declarations(files)} == {
            "thm",
            "lem",
            "defn",
            "ex",
        }

    def test_a_markdown_root_declares_nothing(self, tmp_path) -> None:
        root = tmp_path / "md"
        root.mkdir()
        (root / "paper.md").write_text("\\newtheorem{lem}{Lemma}\n", encoding="utf-8")

        assert read_declarations(files_of(root)) == ()


# --- A declared proof is still a proof ---


def test_a_declared_proof_is_still_a_proof(tmp_path) -> None:
    """A paper without amsthm declares its own `proof`; it still reads as a
    proof, not a statement.

    `environment_table` keeps `proof` out of the statement names it builds
    (`RESERVED_ENVS`), and `read_paper` still reads the paper's
    `\\begin{proof}` as an environment of kind `EnvKind.PROOF`.
    """
    root = tmp_path / "p"
    root.mkdir()
    (root / "main.tex").write_text(
        PREAMBLE
        + "\\newtheorem{lem}{Lemma}\n"
        + "\\newtheorem*{proof}{Proof}\n"
        + "\\begin{document}\n"
        + BODY
        + "\\end{document}\n",
        encoding="utf-8",
    )

    document = read_paper(root)
    table = environment_table(document.declarations)
    proofs = [env for env in document.environments if env.name == "proof"]

    assert "proof" not in table.names
    assert proofs and all(env.kind is EnvKind.PROOF for env in proofs)


def test_first_is_reading_order_across_a_preamble_input(tmp_path) -> None:
    """The root declares `thm` below `\\input{defs}`, and `defs.tex` declares
    it too: LaTeX meets `defs.tex`'s first, so it wins."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "main.tex").write_text(
        "\\documentclass{article}\n\\input{defs}\n\\newtheorem{thm}{Lemma}\n"
        "\\begin{document}\nText.\n\\end{document}\n",
        encoding="utf-8",
    )
    (root / "defs.tex").write_text("\\newtheorem{thm}{Theorem}\n", encoding="utf-8")

    document = read_paper(root)

    assert [
        (found.name, found.printed, found.at.file) for found in document.declarations
    ] == [("thm", "Theorem", "defs.tex")]


# --- Frames, boxes and wrappers ---


def read_kinds(text: str) -> dict[str, tuple[EnvKind, str | None]]:
    """What one file declares: name to (kind, meaning)."""
    return {
        entry.name: (entry.kind, entry.meaning)
        for entry in read_declarations([_source(text)])
    }


class TestFramesAndBoxes:
    def test_mdframed_and_tcolorbox_frames_are_containers(self) -> None:
        found = read_declarations(
            [
                _source(
                    "\\newmdenv[linewidth=1pt]{lawbox}\n"
                    "\\newtcolorbox[auto counter]{notebox}[1][]{colback=white,#1}\n"
                )
            ]
        )

        assert [(e.name, e.kind, e.meaning, e.printed) for e in found] == [
            ("lawbox", EnvKind.CONTAINER, None, ""),
            ("notebox", EnvKind.CONTAINER, None, ""),
        ]
        assert [found[1].at.start, found[1].at.end] == [
            len("\\newmdenv[linewidth=1pt]{lawbox}\n"),
            len(
                "\\newmdenv[linewidth=1pt]{lawbox}\n"
                "\\newtcolorbox[auto counter]{notebox}[1][]{colback=white,#1}"
            ),
        ]

    def test_boxed_theorems_are_statements_of_their_printed_word(self) -> None:
        assert declared(
            "\\newmdtheoremenv[linewidth=1pt]{mdlem}{Lemma}[section]\n"
            "\\newtcbtheorem[number within=section]{tcbthm}{Theorem}"
            "{colback=white}{th}\n"
        ) == {
            "mdlem": ("Lemma", "lemma"),
            "tcbthm": ("Theorem", "theorem"),
            "tcbthm*": ("Theorem", "theorem"),
        }

    def test_a_tcolorbox_theorem_takes_a_title_and_a_prefixed_label(
        self, tmp_path
    ) -> None:
        root = write_paper(
            tmp_path / "paper",
            "\\begin{rule}[colback=red]{Ties}{ties}\nEqual heads go left.\n"
            "\\end{rule}\n"
            "\\begin{rule*}{Plain}\nNo number.\n\\end{rule*}\n"
            "By \\ref{rl:ties}.\n",
            macros="\\newtcbtheorem[number within=section]{rule}{Rule}"
            "{colback=white}{rl}\n",
        )

        document = read_paper(root)

        assert [
            (e.name, e.kind, e.title, document.text(e.body).strip())
            for e in document.environments
        ] == [
            ("rule", EnvKind.STATEMENT, "Ties", "Equal heads go left."),
            ("rule*", EnvKind.STATEMENT, "Plain", "No number."),
        ]
        assert [(label.name, document.text(label.at)) for label in document.labels] == [
            ("rl:ties", "{ties}")
        ]

    def test_a_frame_reads_as_a_container(self, tmp_path) -> None:
        root = write_paper(
            tmp_path / "paper",
            "\\begin{lawbox}\nA law.\n\\end{lawbox}\n",
            macros="\\newmdenv{lawbox}\n",
        )

        document = read_paper(root)

        assert {e.name: e.kind for e in document.environments}["lawbox"] is (
            EnvKind.CONTAINER
        )


class TestWrappers:
    def test_a_wrapper_of_a_statement_is_that_statement(self) -> None:
        assert read_kinds(
            "\\newenvironment{mythm}[1][]{\\begin{theorem}[#1]}{\\end{theorem}}\n"
            "\\newenvironment{mylem}{\\begin{lem}}{\\end{lem}}\n"
            "\\newtheorem{lem}{Lemma}\n"
        ) == {
            "lem": (EnvKind.STATEMENT, "lemma"),
            "mythm": (EnvKind.STATEMENT, "theorem"),
            "mylem": (EnvKind.STATEMENT, "lemma"),
        }

    def test_a_wrapper_of_a_name_the_reader_knows_is_read_as_it(self, tmp_path) -> None:
        root = write_paper(
            tmp_path / "paper",
            "\\begin{proofsketch}\nBy cases.\n\\end{proofsketch}\n",
            macros="\\newenvironment{proofsketch}"
            "{\\begin{proof}[Sketch]}{\\end{proof}}\n",
        )

        document = read_paper(root)

        assert read_kinds(document.file("macros.tex").text) == {
            "proofsketch": (EnvKind.PROOF, "proof")
        }
        assert {e.name: (e.kind, e.meaning) for e in document.environments}[
            "proofsketch"
        ] == (EnvKind.PROOF, "proof")

    def test_any_other_newenvironment_is_no_declaration(self) -> None:
        assert (
            read_kinds(
                "\\newenvironment{twice}{\\begin{center}\\begin{theorem}}"
                "{\\end{theorem}\\end{center}}\n"
                "\\newenvironment{ruled}{\\hrule}{\\hrule}\n"
                "\\newenvironment{odd}{\\begin{mystery}}{\\end{mystery}}\n"
                "\\newenvironment{proof}{\\begin{center}}{\\end{center}}\n"
                "\\renewenvironment{again}{\\begin{theorem}}{\\end{theorem}}\n"
            )
            == {}
        )

    def test_the_table_reads_each_kind(self) -> None:
        table = environment_table(
            read_declarations(
                [
                    _source(
                        "\\newmdenv{lawbox}\n"
                        "\\newenvironment{mythm}{\\begin{theorem}}{\\end{theorem}}\n"
                        "\\newenvironment{sketch}{\\begin{proof}}{\\end{proof}}\n"
                    )
                ]
            )
        )

        assert "lawbox" in table.containers
        assert table.meaning("mythm") == "theorem"
        assert table.wrapped("sketch") == "proof"


# --- Reference commands ---


def reference_rows(text: str) -> list[tuple[str, bool, str, str | None]]:
    from axiomation.reader.declarations import own_references, package_references

    files = [_source(text)]
    return [
        (entry.command, entry.takes_list, entry.prefix, entry.word)
        for entry in (*own_references(files), *package_references(files))
    ]


class TestReferenceRows:
    def test_a_paper_s_own_one_argument_reference(self) -> None:
        assert reference_rows(
            "\\newcommand{\\lemref}[1]{Lemma~\\ref{lem:#1}}\n"
            "\\def\\figref#1{figure~\\ref{#1}}\n"
            "\\newcommand*\\eqnref[1]{(\\ref{eq:#1})}\n"
            "\\newcommand{\\allref}[1]{\\cref{#1}}\n"
            "\\newcommand{\\lemref}[1]{Lemmas~\\ref{#1}}\n"
        ) == [
            ("lemref", False, "lem:", "Lemma"),
            ("figref", False, "", "figure"),
            ("eqnref", False, "eq:", None),
            ("allref", True, "", None),
        ]

    def test_a_richer_body_is_the_map_s(self) -> None:
        assert (
            reference_rows(
                "\\newcommand{\\a}[1]{\\lemmaname~\\ref{#1}}\n"
                "\\newcommand{\\b}[2]{\\ref{#1} and \\ref{#2}}\n"
                "\\newcommand{\\c}[1][x]{\\ref{#1}}\n"
                "\\newcommand{\\d}[1]{see \\ref{#1} below}\n"
            )
            == []
        )

    def test_a_package_s_commands_only_where_it_is_loaded(self, tmp_path) -> None:
        assert reference_rows("\\usepackage{amsthm}\n") == []
        jmlr = reference_rows("\\documentclass[pmlr]{jmlr}\n")
        assert ("lemmaref", True, "", "Lemma") in jmlr
        assert len(jmlr) == 15
        assert [
            (r.command, r.targets)
            for r in read_paper(
                write_paper(
                    tmp_path / "fancy",
                    "\\section{S}\\label{sec:s}\nSee \\fref[vario]{sec:s}.\n",
                    macros=None,
                    preamble=PREAMBLE + "\\usepackage{fancyref}\n",
                )
            ).references
        ] == [("fref", ("sec:s",))]
        assert reference_rows("\\usepackage{fancyref,prettyref}\n") == [
            ("fref", False, "", None),
            ("Fref", False, "", None),
            ("prettyref", False, "", None),
        ]

    def test_refstyle_adds_its_key_prefix_unless_told_not_to(self) -> None:
        keyed = reference_rows("\\usepackage{refstyle}\n")
        bare = reference_rows("\\usepackage[nokeyprefix]{refstyle}\n")

        assert ("figref", True, "fig:", "figure") in keyed
        assert ("Figref", True, "fig:", "Figure") in keyed
        assert ("figref", True, "", "figure") in bare

    def test_a_paper_s_own_wins_and_the_references_read(self, tmp_path) -> None:
        root = write_paper(
            tmp_path / "paper",
            "\\section{S}\\label{sec:s}\n"
            "\\begin{lemma}\\label{lem:key}\nA.\n\\end{lemma}\n"
            "By \\lemref{key}, \\lemmaref{lem:key,sec:s} and \\secref{s}.\n",
            macros="\\newcommand{\\lemref}[1]{Lemma~\\ref{lem:#1}}\n"
            "\\def\\secref#1{Section~\\ref{sec:#1}}\n",
            preamble="\\documentclass{jmlr}\n\\usepackage{refstyle}\n",
        )

        document = read_paper(root)

        assert [(r.command, r.word, r.targets) for r in document.references] == [
            ("lemref", "Lemma", ("lem:key",)),
            ("lemmaref", "Lemma", ("lem:key", "sec:s")),
            ("secref", "Section", ("sec:s",)),
        ]


# --- restatable ---


def test_a_restatable_is_a_statement_its_command_a_label_and_a_reference(
    tmp_path,
) -> None:
    root = write_paper(
        tmp_path / "paper",
        "\\begin{restatable}[Main]{thm}{maintheorem}\\label{thm:main}\n"
        "Every case holds.\n\\end{restatable}\n\n"
        "Again: \\maintheorem* and \\maintheorem.\n",
    )

    document = read_paper(root)

    (restatable,) = [e for e in document.environments if e.name == "restatable"]
    assert (restatable.kind, restatable.meaning, restatable.title) == (
        EnvKind.STATEMENT,
        "theorem",
        "Main",
    )
    assert document.text(restatable.body).strip().startswith("\\label{thm:main}")
    labels = [(label.name, document.text(label.at)) for label in document.labels]
    assert labels[0] == ("maintheorem", "{maintheorem}")
    assert [
        (r.command, r.word, r.targets, document.text(r.at)) for r in document.references
    ] == [
        ("maintheorem", "Theorem", ("maintheorem",), "\\maintheorem*"),
        ("maintheorem", "Theorem", ("maintheorem",), "\\maintheorem"),
    ]


def table_of(text: str) -> ReferenceTable:
    return reference_table([_source(text)])


class TestReadAsLatexDoes:
    def test_keyed_refstyle_reads_eqref_with_its_prefix(self) -> None:
        keyed = table_of("\\usepackage{amsmath}\n\\usepackage{refstyle}\n")
        bare = table_of("\\usepackage[nokeyprefix]{refstyle}\n")
        plain = table_of("\\usepackage{amsmath}\n")

        assert dict(keyed.prefixes)["eqref"] == "eq:"
        assert "eqref" in keyed.lists
        assert "eqref" not in dict(bare.prefixes)
        assert "eqref" not in dict(plain.prefixes) and "eqref" not in plain.lists

    def test_providecommand_loses_to_a_package_and_renewcommand_wins(self) -> None:
        provided = table_of(
            "\\documentclass{jmlr}\n\\providecommand{\\theoremref}[1]{Theorem~\\ref{#1}}\n"
        )
        renewed = table_of(
            "\\documentclass{jmlr}\n"
            "\\renewcommand{\\theoremref}[1]{Theorem~\\ref{thm:#1}}\n"
            "\\renewcommand{\\lemmaref}[1]{\\textsc{Lemma}~\\ref{#1}}\n"
        )

        assert "theoremref" in provided.lists
        assert "theoremref" not in dict(provided.prefixes)
        assert dict(renewed.prefixes)["theoremref"] == "thm:"
        assert "theoremref" not in renewed.lists
        # redefined as something code cannot read: the package's reading is gone
        assert "lemmaref" not in renewed.commands

    def test_a_redefinition_replaces_the_first(self) -> None:
        table = table_of(
            "\\newcommand{\\lemref}[1]{Lemma~\\ref{lem:#1}}\n"
            "\\newcommand{\\lemref}[1]{Claim~\\ref{cl:#1}}\n"
            "\\renewcommand{\\lemref}[1]{Lemma~\\ref{L:#1}}\n"
        )

        assert dict(table.prefixes)["lemref"] == "L:"
