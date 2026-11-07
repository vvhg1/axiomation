"""A paper's vocabulary: the names code finds for the map, and the map read.

Each case writes the smallest paper that shows its shape, in Markdown
(`paper.md`) or LaTeX (`main.tex`), and reads what
`axiomation.reader.vocabulary.candidates` lists, or what `read_paper` makes
of a `Vocabulary`.
"""

from __future__ import annotations

from pathlib import Path

from axiomation.cutter import Form, cut
from axiomation.reader import (
    CandidateShape,
    Document,
    EnvEntry,
    EnvKind,
    MapAnswer,
    RefEntry,
    Vocabulary,
    VocabularyKind,
    read_paper,
)
from axiomation.reader.document import COMMAND, ENVIRONMENT
from axiomation.reader.formats.md_latex import header_includes
from axiomation.reader.vocabulary import candidates

LATEX_PREAMBLE = "\\documentclass{article}\n\\usepackage{amsthm}\n"


def markdown(tmp_path: Path, body: str, header: str = "") -> Path:
    paper = tmp_path / "paper"
    paper.mkdir()
    front = f"---\ntitle: A paper\n{header}---\n\n" if header else ""
    (paper / "paper.md").write_text(front + body, encoding="utf-8")
    return paper


def latex(tmp_path: Path, body: str, preamble: str = "") -> Path:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text(
        LATEX_PREAMBLE + preamble + "\\begin{document}\n" + body + "\\end{document}\n",
        encoding="utf-8",
    )
    return paper


def found(
    document: Document,
) -> dict[str, tuple[CandidateShape, list[str], str | None]]:
    """Each candidate's shape, its uses' text and its declaration's text."""
    return {
        candidate.name: (
            candidate.shape,
            [document.text(use) for use in candidate.uses],
            document.text(candidate.declaration) if candidate.declaration else None,
        )
        for candidate in candidates(document)
    }


def kinds(document: Document) -> dict[str, EnvKind]:
    return {env.name: env.kind for env in document.environments}


# --- What code finds ---


class TestEnvironments:
    def test_an_undeclared_environment_is_one(self, tmp_path: Path) -> None:
        paper = markdown(tmp_path, "\\begin{example}\nA case.\n\\end{example}\n")

        assert found(read_paper(paper)) == {
            "example": (CandidateShape.ENVIRONMENT, ["\\begin{example}"], None)
        }

    def test_a_frame_declared_in_the_yaml_header_names_its_declaration(
        self, tmp_path: Path
    ) -> None:
        paper = markdown(
            tmp_path,
            "\\begin{lawbox}\nA law.\n\\end{lawbox}\n",
            header="header-includes:\n  - \\newenvironment{lawbox}{\\hrule}{\\hrule}\n",
        )

        shape, uses, declaration = found(read_paper(paper))["lawbox"]

        assert (shape, uses) == (CandidateShape.ENVIRONMENT, ["\\begin{lawbox}"])
        assert declaration == "\\newenvironment{lawbox}{\\hrule}{\\hrule}"

    def test_a_declaration_is_shown_with_every_argument_after_the_name(
        self, tmp_path: Path
    ) -> None:
        paper = latex(
            tmp_path,
            "\\begin{steps}\n\\item One.\n\\end{steps}\n",
            preamble="\\newlist{steps}{enumerate}{2}\n\\setlist[steps]{label=(\\arabic*)}\n",
        )

        _, _, declaration = found(read_paper(paper))["steps"]

        assert declaration == "\\newlist{steps}{enumerate}{2}"

    def test_one_inside_a_display_a_float_or_a_listing_is_not(
        self, tmp_path: Path
    ) -> None:
        paper = latex(
            tmp_path,
            "\\begin{equation}\\begin{smallmatrix}a\\end{smallmatrix}\\end{equation}\n"
            "\\begin{table}\\begin{tabular}{c}x\\end{tabular}\\end{table}\n",
        )

        assert found(read_paper(paper)) == {}

    def test_newenvironment_declares_one_unused(self, tmp_path: Path) -> None:
        paper = latex(
            tmp_path,
            "Text.\n",
            preamble="\\newenvironment{mythm}{\\begin{center}\\begin{theorem}}"
            "{\\end{theorem}\\end{center}}\n"
            "\\renewenvironment{abstract}{}{}\n",
        )

        shape, uses, declaration = found(read_paper(paper))["mythm"]

        assert (shape, uses) == (CandidateShape.ENVIRONMENT, [])
        assert declaration is not None and declaration.startswith(
            "\\newenvironment{mythm}"
        )
        assert "abstract" not in found(read_paper(paper))

    def test_each_name_once_its_first_three_uses_shown(self, tmp_path: Path) -> None:
        body = "".join(f"\\begin{{box}}\n{n}\n\\end{{box}}\n\n" for n in range(5))

        shape, uses, _ = found(read_paper(markdown(tmp_path, body)))["box"]

        assert uses == ["\\begin{box}"] * 3

    def test_the_static_frames_are_containers_not_candidates(
        self, tmp_path: Path
    ) -> None:
        paper = markdown(
            tmp_path,
            "\\begin{mdframed}\nOne.\n\n Two.\n\\end{mdframed}\n\n"
            "\\begin{framed}\nThree.\n\\end{framed}\n",
        )
        document = read_paper(paper)

        assert kinds(document) == {
            "mdframed": EnvKind.CONTAINER,
            "framed": EnvKind.CONTAINER,
        }
        assert found(document) == {}


class TestCommands:
    LABELS = (
        "\\begin{theorem}\\label{thm:main}\nA.\n\\end{theorem}\n"
        "\\begin{lemma}\\label{lem:key}\nB.\n\\end{lemma}\n"
    )

    def test_a_macro_whose_body_uses_a_reference(self, tmp_path: Path) -> None:
        paper = latex(
            tmp_path,
            self.LABELS + "By \\lemref{key}.\n",
            preamble="\\newcommand{\\lemref}[1]{\\lemmaname~\\ref{lem:#1}}\n"
            "\\newcommand{\\R}{\\mathbb{R}}\n",
        )

        assert found(read_paper(paper)) == {
            "lemref": (
                CandidateShape.MACRO,
                ["\\lemref"],
                "\\newcommand{\\lemref}[1]{\\lemmaname~\\ref{lem:#1}}",
            )
        }

    def test_a_package_s_command_found_by_its_labels(self, tmp_path: Path) -> None:
        paper = latex(
            tmp_path,
            self.LABELS + "By \\lemmaref{lem:key} and \\theoremref{main}, "
            "\\bothref{thm:main,lem:key}.\n",
        )

        assert found(read_paper(paper)) == {
            "lemmaref": (CandidateShape.USE, ["\\lemmaref{lem:key}"], None),
            "theoremref": (CandidateShape.USE, ["\\theoremref{main}"], None),
            "bothref": (CandidateShape.USE, ["\\bothref{thm:main,lem:key}"], None),
        }

    def test_a_command_that_also_takes_other_words_is_not_one(
        self, tmp_path: Path
    ) -> None:
        paper = latex(
            tmp_path,
            self.LABELS
            + "The \\emph{key} step, \\emph{again}, and \\pageref{lem:key}.\n",
        )

        assert found(read_paper(paper)) == {}

    def test_the_command_around_a_dangling_target_defines_it(
        self, tmp_path: Path
    ) -> None:
        paper = latex(
            tmp_path, "\\customlabel{step:two}{2}Step two.\nSee \\ref{step:two}.\n"
        )

        assert found(read_paper(paper)) == {
            "customlabel": (
                CandidateShape.LABEL_DEFINER,
                ["\\customlabel{step:two}"],
                None,
            )
        }

    def test_a_known_reference_or_citation_is_not_one(self, tmp_path: Path) -> None:
        paper = latex(
            tmp_path,
            self.LABELS + "\\cref{thm:main}, \\eqref{lem:key}, \\citet{lem:key}.\n",
        )

        assert found(read_paper(paper)) == {}


class TestAnswered:
    def test_a_name_the_map_answers_is_not_listed_again(self, tmp_path: Path) -> None:
        paper = markdown(tmp_path, "\\begin{example}\nA.\n\\end{example}\n")
        vocabulary = Vocabulary(
            environments=(EnvEntry("example", VocabularyKind.NONE),)
        )

        document = read_paper(paper, vocabulary=vocabulary)

        assert candidates(document, vocabulary) == ()
        assert kinds(document) == {"example": EnvKind.OTHER}


# --- The map read ---


class TestTheMapRead:
    def test_a_statement_with_its_base_and_one_with_none(self, tmp_path: Path) -> None:
        paper = markdown(
            tmp_path,
            "\\begin{satz}\nA.\n\\end{satz}\n\n\\begin{example}\nB.\n\\end{example}\n",
        )
        vocabulary = Vocabulary(
            environments=(
                EnvEntry("satz", VocabularyKind.STATEMENT, "theorem"),
                EnvEntry("example", VocabularyKind.STATEMENT),
            )
        )

        document = read_paper(paper, vocabulary=vocabulary)

        assert [(e.name, e.kind, e.meaning) for e in document.environments] == [
            ("satz", EnvKind.STATEMENT, "theorem"),
            ("example", EnvKind.STATEMENT, None),
        ]

    def test_a_container_s_body_is_cut_as_if_it_stood_alone(
        self, tmp_path: Path
    ) -> None:
        paper = markdown(
            tmp_path,
            "\\begin{lawbox}\n**A law.** First part.\n\nSecond part.\n\\end{lawbox}\n",
        )
        mapped = Vocabulary(
            environments=(EnvEntry("lawbox", VocabularyKind.CONTAINER),)
        )

        unknown = cut(read_paper(paper)).blocks
        framed = cut(read_paper(paper, vocabulary=mapped)).blocks

        assert [b.form for b in unknown] == [Form.ENVIRONMENT]
        assert [b.form for b in framed] == [Form.PROSE, Form.PROSE]
        assert not any(b.doubts for b in framed)

    def test_a_list(self, tmp_path: Path) -> None:
        paper = markdown(
            tmp_path, "\\begin{steps}\n\\item One.\n\\item Two.\n\\end{steps}\n"
        )
        mapped = Vocabulary(
            environments=(EnvEntry("steps", VocabularyKind.LIST, "enumerate"),)
        )

        blocks = cut(read_paper(paper, vocabulary=mapped)).blocks

        assert [b.form for b in blocks] == [Form.ITEM, Form.ITEM]

    def test_a_reference_with_its_prefix_and_word(self, tmp_path: Path) -> None:
        paper = latex(
            tmp_path,
            "\\begin{lemma}\\label{lem:key}\nB.\n\\end{lemma}\n"
            "By \\lemref{key} and \\lemmaref{lem:key,lem:key}.\n",
        )
        mapped = Vocabulary(
            references=(
                RefEntry("lemref", prefix="lem:", word="Lemma"),
                RefEntry("lemmaref", takes_list=True),
            )
        )

        document = read_paper(paper, vocabulary=mapped)

        assert [(r.command, r.word, r.targets) for r in document.references] == [
            ("lemref", "Lemma", ("lem:key",)),
            ("lemmaref", None, ("lem:key", "lem:key")),
        ]
        assert candidates(document, mapped) == ()

    def test_a_label_command_defines_the_target_a_reference_names(
        self, tmp_path: Path
    ) -> None:
        paper = latex(
            tmp_path, "\\customlabel{step:two}{2}Step two.\nSee \\ref{step:two}.\n"
        )
        mapped = Vocabulary(labels=("customlabel",))

        document = read_paper(paper, vocabulary=mapped)

        assert [label.name for label in document.labels] == ["step:two"]
        assert candidates(document, mapped) == ()

    def test_a_declaration_a_static_name_and_a_standard_command_win(
        self, tmp_path: Path
    ) -> None:
        paper = latex(
            tmp_path,
            "\\begin{thm}\\label{thm:a}\nA.\n\\end{thm}\n"
            "\\begin{figure}x\\end{figure}\n\\begin{proof}P.\\end{proof}\n"
            "See \\ref{thm:a}.\n",
            preamble="\\newtheorem{thm}{Theorem}\n",
        )
        mapped = Vocabulary(
            environments=(
                EnvEntry("thm", VocabularyKind.CONTAINER),
                EnvEntry("figure", VocabularyKind.STATEMENT, "theorem"),
                EnvEntry("proof", VocabularyKind.STATEMENT, "theorem"),
            ),
            references=(RefEntry("ref", prefix="x:"),),
        )

        document = read_paper(paper, vocabulary=mapped)

        assert kinds(document) == {
            "thm": EnvKind.STATEMENT,
            "figure": EnvKind.FLOAT,
            "proof": EnvKind.PROOF,
        }
        assert [r.targets for r in document.references] == [("thm:a",)]


# --- A Markdown file's header-includes ---


class TestHeaderIncludes:
    @staticmethod
    def lines(text: str) -> list[str]:
        return [text[start:end] for start, end in header_includes(text)]

    def test_items_less_their_comments(self) -> None:
        text = (
            "---\ntitle: T\nheader-includes:\n"
            "  # a frame\n"
            "  - \\newmdenv{box}\n"
            "  - \\expositoryfalse   # set to true for Part I\n"
            "#  - \\usepackage{tikz}\n"
            "date: 1\n---\nBody \\newtheorem{x}{X}\n"
        )

        assert self.lines(text) == ["\\newmdenv{box}", "\\expositoryfalse"]

    def test_a_block_scalar_and_a_plain_value(self) -> None:
        block = "---\nheader-includes: |\n  \\newtheorem{satz}{Satz}\n---\n"
        plain = "---\nheader-includes: \\newtheorem{a}{A} # c\n---\n"

        assert self.lines(block) == ["\\newtheorem{satz}{Satz}"]
        assert self.lines(plain) == ["\\newtheorem{a}{A}"]

    def test_no_header_no_includes(self) -> None:
        assert self.lines("Body \\newtheorem{x}{X}\n") == []

    def test_a_theorem_declared_there_bounds_a_statement(self, tmp_path: Path) -> None:
        paper = markdown(
            tmp_path,
            "\\begin{satz}\nA.\n\\end{satz}\n",
            header="header-includes:\n  - \\newtheorem{satz}{Theorem}\n",
        )

        document = read_paper(paper)

        assert [(d.name, d.meaning) for d in document.declarations] == [
            ("satz", "theorem")
        ]
        assert kinds(document) == {"satz": EnvKind.STATEMENT}


class TestHeaderIncludesFollowed:
    def test_an_input_there_is_a_preamble_file_read_for_its_declarations(
        self, tmp_path: Path
    ) -> None:
        paper = markdown(
            tmp_path,
            "\\begin{satz}\nA.\n\\end{satz}\n",
            header="header-includes:\n  - \\input{theorems.tex}\n",
        )
        (paper / "theorems.tex").write_text(
            "\\newtheorem{satz}{Theorem}\n", encoding="utf-8"
        )

        document = read_paper(paper)

        assert [(f.path, f.role.value) for f in document.files] == [
            ("paper.md", "root"),
            ("theorems.tex", "preamble"),
        ]
        assert kinds(document) == {"satz": EnvKind.STATEMENT}
        assert document.never_included == ()


# --- A name declared more than once ---

#: A reference command whose body is richer than code reads: the map's.
LEMREF = "\\newcommand{\\lemref}[1]{Lemma~\\ref{lem:#1} on page~\\pageref{lem:#1}}\n"
#: The same command redefined later in the paper to print its argument.
PLAIN = "\\renewcommand{\\lemref}[1]{#1}\n"
REDEFINED_BODY = (
    "\\begin{lemma}\\label{lem:key}\nB.\n\\end{lemma}\n"
    "Before, \\lemref{key}.\n\n" + PLAIN + "\nAfter, \\lemref{key}.\n"
)


def mapped(answers: list[MapAnswer]) -> Vocabulary:
    return Vocabulary.of(answers)


def _beside(tmp_path: Path) -> Path:
    """A second paper's directory beside the first."""
    other = tmp_path / "b"
    other.mkdir()
    return other


class TestRedefined:
    def test_each_declaring_text_is_a_candidate_with_the_uses_it_is_in_force_at(
        self, tmp_path: Path
    ) -> None:
        document = read_paper(latex(tmp_path, REDEFINED_BODY, LEMREF))

        listed = candidates(document)

        assert [(c.name, c.shape, c.index, c.count) for c in listed] == [
            ("lemref", CandidateShape.MACRO, 0, 2),
            ("lemref", CandidateShape.REDEFINED, 1, 2),
        ]
        assert [document.text(c.declaration) for c in listed] == [
            LEMREF.strip(),
            PLAIN.strip(),
        ]
        assert [[document.text(u) for u in c.uses] for c in listed] == [
            ["\\lemref"],
            ["\\lemref"],
        ]
        first, second = (c.uses[0].start for c in listed)
        assert first < document.file("main.tex").text.index(PLAIN) < second

    def test_a_redefinition_in_another_file_is_in_force_after_its_include(
        self, tmp_path: Path
    ) -> None:
        paper = latex(
            tmp_path,
            "\\begin{lemma}\\label{lem:key}\nB.\n\\end{lemma}\n"
            "Before, \\lemref{key}.\n\\input{later}\n",
            LEMREF,
        )
        (paper / "later.tex").write_text(PLAIN + "After, \\lemref{key}.\n")
        document = read_paper(paper)

        listed = candidates(document)

        assert [(c.index, c.declaration.file) for c in listed] == [
            (0, "main.tex"),
            (1, "later.tex"),
        ]
        assert [c.uses[0].file for c in listed] == ["main.tex", "later.tex"]

    def test_one_text_declared_twice_is_one_candidate(self, tmp_path: Path) -> None:
        body = "\\begin{lemma}\\label{lem:key}\nB.\n\\end{lemma}\nSee \\lemref{key}.\n"
        document = read_paper(
            latex(tmp_path, body, LEMREF + LEMREF.replace("newcommand", "renewcommand"))
        )
        twice = read_paper(latex(_beside(tmp_path), body, LEMREF + LEMREF))

        assert [(c.index, c.count) for c in candidates(twice)] == [(0, 1)]
        assert [c.count for c in candidates(document)] == [2, 2]

    def test_a_label_beside_an_environment_of_its_name_declares_nothing(
        self, tmp_path: Path
    ) -> None:
        paper = latex(
            tmp_path,
            "\\begin{example}\\label{example}\nA.\n\\end{example}\n",
            "\\newenvironment{example}{\\par}{\\par}\n",
        )
        document = read_paper(paper)

        assert [(c.name, c.count) for c in candidates(document)] == [("example", 1)]

    def test_each_use_is_read_under_the_declaration_in_force(
        self, tmp_path: Path
    ) -> None:
        document = read_paper(latex(tmp_path, REDEFINED_BODY, LEMREF))
        vocabulary = mapped(
            [
                MapAnswer(
                    COMMAND,
                    "lemref",
                    LEMREF.strip(),
                    "reference prefix=lem: word=Lemma",
                    "Lemma",
                ),
                MapAnswer(COMMAND, "lemref", PLAIN.strip(), "none", "#1"),
            ]
        )

        read = read_paper(
            latex(_beside(tmp_path), REDEFINED_BODY, LEMREF), vocabulary=vocabulary
        )

        refs = [
            (r.command, r.targets) for r in read.references if r.command == "lemref"
        ]
        assert refs == [("lemref", ("lem:key",))]
        assert read.references[-1].at.start < read.file("main.tex").text.index(PLAIN)
        # listed on the paper read without the map, as an update lists them
        assert candidates(document, vocabulary) == ()
        assert not [r for r in document.references if r.command == "lemref"]

    def test_an_environment_redefined_reads_as_each_declaration_says(
        self, tmp_path: Path
    ) -> None:
        box = "\\newenvironment{box}{\\par}{\\par}\n"
        again = "\\renewenvironment{box}{\\par\\itshape}{\\par}\n"
        body = (
            "\\begin{box}\nA.\n\\end{box}\n\n"
            + again
            + "\n\\begin{box}\nB.\n\\end{box}\n"
        )
        vocabulary = mapped(
            [
                MapAnswer(ENVIRONMENT, "box", box.strip(), "container", "\\par"),
                MapAnswer(
                    ENVIRONMENT, "box", again.strip(), "statement theorem", "\\itshape"
                ),
            ]
        )

        read = read_paper(latex(tmp_path, body, box), vocabulary=vocabulary)

        assert [
            (e.name, e.kind, e.meaning) for e in read.environments if e.name == "box"
        ] == [
            ("box", EnvKind.CONTAINER, "box"),
            ("box", EnvKind.STATEMENT, "theorem"),
        ]

    def test_an_edit_of_one_declaration_asks_that_one_again(
        self, tmp_path: Path
    ) -> None:
        vocabulary = mapped(
            [
                MapAnswer(
                    COMMAND,
                    "lemref",
                    LEMREF.strip(),
                    "reference prefix=lem: word=Lemma",
                    "Lemma",
                ),
                MapAnswer(COMMAND, "lemref", PLAIN.strip(), "none", "#1"),
            ]
        )
        edited = REDEFINED_BODY.replace(PLAIN, "\\renewcommand{\\lemref}[1]{(#1)}\n")

        # listed on the paper read without the map, as an update lists them
        document = read_paper(latex(tmp_path, edited, LEMREF))

        assert [
            (c.index, document.text(c.declaration))
            for c in candidates(document, vocabulary)
        ] == [(1, "\\renewcommand{\\lemref}[1]{(#1)}")]


class TestNotADeclaration:
    """A command that names an environment without saying what it is: a
    counter of its name, a frame put around it, a label."""

    ENV = "\\newenvironment{lawbox}{\\par}{\\par}\n"
    BODY = "\\begin{lawbox}\nA.\n\\end{lawbox}\n\n\\begin{lawbox}\nB.\n\\end{lawbox}\n"

    def listed(self, tmp_path: Path, preamble: str) -> list[tuple[int, int, str]]:
        tmp_path.mkdir(parents=True, exist_ok=True)
        document = read_paper(latex(tmp_path, self.BODY, preamble))
        return [
            (c.index, c.count, document.text(c.declaration))
            for c in candidates(document)
            if c.name == "lawbox"
        ]

    def test_a_counter_of_its_name_either_side(self, tmp_path: Path) -> None:
        counter = "\\newcounter{lawbox}\n"
        before = self.listed(tmp_path / "a", counter + self.ENV)
        after = self.listed(tmp_path / "b", self.ENV + counter)

        assert [(i, n) for i, n, _ in before] == [(0, 1)]
        assert [(i, n) for i, n, _ in after] == [(0, 1)]
        assert after[0][2] == self.ENV.strip()

    def test_a_frame_put_around_it(self, tmp_path: Path) -> None:
        framed = self.ENV + "\\surroundwithmdframed[linewidth=1pt]{lawbox}\n"

        assert self.listed(tmp_path, framed) == [(0, 1, self.ENV.strip())]
