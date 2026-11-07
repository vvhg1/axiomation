"""Proposed groups.

A proof is a group of blocks that names the result's block. Step headings
make no group.
"""

from __future__ import annotations

import pytest

from axiomation.cutter import Form, GroupKind, Naming, Role
from axiomation.cutter.parts import is_step
from paper_shapes import STEP_HEADINGS


def doc(body: str, preamble: str = "") -> str:
    return preamble + "\\begin{document}\n" + body + "\\end{document}\n"


def groups(cutting, kind: GroupKind):
    return [group for group in cutting.cut.groups if group.kind is kind]


def statement(cutting, environment: str):
    (found,) = [
        block
        for block in cutting.blocks(Form.STATEMENT)
        if block.environment == environment
    ]
    return found


SECTION = "## S\n\\label{sec:s}\n\n"
LEMMA = "\\begin{lemma}[L] \\label{lem:l}\nStatement.\n\\end{lemma}\n"


# --- proof cases ---


def test_trailing_proof(paper) -> None:
    cutting = paper(
        SECTION
        + LEMMA
        + "\n\\begin{proof}\nBy computation.\n\nHence the claim.\n\\end{proof}\n",
        name="paper.md",
    )

    (proof,) = groups(cutting, GroupKind.PROOF)
    assert proof.names == statement(cutting, "lemma").id
    assert (proof.named_label, proof.named_by) == ("lem:l", Naming.POSITION)
    assert proof.role is Role.JUSTIFICATION
    assert [cutting.text(cutting.cut.block(b)) for b in proof.blocks] == [
        "\\begin{proof} By computation.",
        "Hence the claim. \\end{proof}",
    ]


def test_detached_proof_named_by_its_title(paper) -> None:
    cutting = paper(
        SECTION
        + LEMMA
        + "\n## A\n\\label{app:a}\n\n"
        + "\\begin{proof}[Proof of Lemma~\\ref{lem:l}]\nLater.\n\n"
        "Hence the claim.\n\\end{proof}\n",
        name="paper.md",
    )

    (proof,) = groups(cutting, GroupKind.PROOF)
    assert proof.names == statement(cutting, "lemma").id
    assert (proof.named_label, proof.named_by) == ("lem:l", Naming.TITLE)


def test_a_standalone_proof_stays_unattached(paper) -> None:
    cutting = paper(
        SECTION
        + LEMMA
        + "\nSome prose in between.\n\n"
        + "\\begin{proof}\nUnnamed.\n\nHence the claim.\n\\end{proof}\n",
        name="paper.md",
    )

    (proof,) = groups(cutting, GroupKind.PROOF)
    assert (proof.names, proof.named_label, proof.named_by) == (None, None, None)


def test_a_named_proof_joins_an_adjacent_sketch(paper) -> None:
    """Both are groups naming the same result: they join in the
    result's proof ranges."""
    cutting = paper(
        SECTION + "\\begin{theorem}[T] \\label{thm:t}\nStatement.\n\\end{theorem}\n\n"
        "\\begin{proof}[Proof sketch] Two ingredients.\n\n"
        "And a third.\n\\end{proof}\n\n"
        "## A\n\\label{app:a}\n\n"
        "\\begin{proof}[Proof of Theorem~\\ref{thm:t}]\nIn full.\n\n"
        "Hence the claim.\n\\end{proof}\n",
        name="paper.md",
    )

    sketch, full = groups(cutting, GroupKind.PROOF)
    theorem = statement(cutting, "theorem").id
    assert (sketch.names, sketch.named_by) == (theorem, Naming.POSITION)
    assert (full.names, full.named_by) == (theorem, Naming.TITLE)


def test_a_proof_titled_for_a_section_attaches_to_nothing(paper) -> None:
    cutting = paper(
        SECTION + "\\begin{proof}[Proof of the results in Section~\\ref{sec:s}]\n"
        "Done.\n\nHence the claim.\n\\end{proof}\n",
        name="paper.md",
    )

    (proof,) = groups(cutting, GroupKind.PROOF)
    assert proof.names is None
    assert (proof.named_label, proof.named_by) == ("sec:s", Naming.TITLE)


def test_a_proof_after_a_definition_names_nothing(paper) -> None:
    """A definition is not proved."""
    cutting = paper(
        doc(
            "\\begin{definition}\nD.\n\\end{definition}\n\\begin{proof}\nP.\n\n"
            "Hence the claim.\n\\end{proof}\n"
        )
    )

    (proof,) = groups(cutting, GroupKind.PROOF)
    assert proof.names is None


# --- proof sections and `cref` titles ---


STATEMENTS = (
    "\\begin{definition}\\label{def:a}\nA.\n\\end{definition}\n"
    "\\begin{lemma}\\label{lem:b}\nB.\n\\end{lemma}\n"
)


def test_a_proof_titled_with_cref_attaches(paper) -> None:
    cutting = paper(
        doc(
            "\\section{S}\\label{sec:s}\n" + STATEMENTS + "Some prose between.\n\n"
            "\\begin{proof}[Proof of \\cref{lem:b}]\nBy \\cref{def:a}.\n\n"
            "Hence the claim.\n\\end{proof}\n"
        )
    )

    (proof,) = groups(cutting, GroupKind.PROOF)
    assert proof.names == statement(cutting, "lemma").id


class TestAProofSection:
    """`\\section{Proof of Lemma~\\ref{lem:b}}` is a proof title set as a
    heading: its body is a group naming the lemma."""

    def test_its_body_is_a_group_naming_the_result(self, paper) -> None:
        cutting = paper(
            doc(
                "\\section{S}\\label{sec:s}\n" + STATEMENTS + "\n"
                "\\section{Proof of Lemma~\\ref{lem:b}}\\label{sec:p}\n"
                "By Definition~\\ref{def:a}, done.\n\n"
                "And more.\n"
                "\\section{Next}\nOutside.\n"
            )
        )

        (section,) = groups(cutting, GroupKind.PROOF_SECTION)
        assert section.names == statement(cutting, "lemma").id
        assert (section.named_label, section.named_by) == ("lem:b", Naming.HEADING)
        assert section.role is Role.JUSTIFICATION
        assert [cutting.text(cutting.cut.block(b)) for b in section.blocks] == [
            "By Definition~\\ref{def:a}, done.",
            "And more.",
        ]

    def test_a_proof_inside_it_stays_a_group_of_its_own(self, paper) -> None:
        cutting = paper(
            doc(
                "\\section{S}\\label{sec:s}\n" + STATEMENTS + "\n"
                "\\section{Proof of Lemma~\\ref{lem:b}}\\label{sec:p}\n"
                "First a remark.\n"
                "\\begin{proof}\nBy Definition~\\ref{def:a}.\n\n"
                "Hence the claim.\n\\end{proof}\n"
            )
        )

        (section,) = groups(cutting, GroupKind.PROOF_SECTION)
        (proof,) = groups(cutting, GroupKind.PROOF)
        assert set(proof.blocks) < set(section.blocks)
        assert len(section.blocks) == 3

    def test_a_subsection_ends_at_the_next_heading_of_its_level(self, paper) -> None:
        cutting = paper(
            doc(
                "\\section{S}\\label{sec:s}\n" + STATEMENTS + "\n"
                "\\subsection{Proof of Lemma~\\ref{lem:b}}\n"
                "Inside.\n"
                "\\subsubsection{A detail}\nStill inside.\n"
                "\\subsection{Other}\nOutside.\n"
            )
        )

        (section,) = groups(cutting, GroupKind.PROOF_SECTION)
        texts = [cutting.text(cutting.cut.block(b)) for b in section.blocks]
        assert texts == ["Inside.", "\\subsubsection{A detail}", "Still inside."]

    @pytest.mark.parametrize(
        ("heading", "grouped"),
        [
            ("Discussion of Lemma~\\ref{lem:b}", False),
            ("Proof of the main results", False),
            ("Proof of Section~\\ref{sec:s}", True),
        ],
    )
    def test_a_heading_that_names_no_result_names_nothing(
        self, paper, heading: str, grouped: bool
    ) -> None:
        cutting = paper(
            doc(
                "\\section{S}\\label{sec:s}\n" + STATEMENTS + "\n"
                f"\\section{{{heading}}}\\label{{sec:p}}\n"
                "Some prose.\n\n"
                "More prose.\n"
            )
        )

        found = groups(cutting, GroupKind.PROOF_SECTION)
        assert bool(found) is grouped
        assert all(group.names is None for group in found)


# --- declared environments ---


def test_a_declared_environment_is_a_statement_with_its_proof(paper) -> None:
    preamble = (
        "\\documentclass{article}\n"
        "\\newtheorem{defn}{Definition}\n"
        "\\newtheorem{lem}{Lemma}\n"
    )
    cutting = paper(
        doc(
            "\\section{S}\n"
            "\\begin{defn}\\label{def:a}\nA thing.\n\\end{defn}\n"
            "\\begin{lem}\\label{lem:b}\nBy \\ref{def:a}, it holds.\n\\end{lem}\n"
            "\\begin{proof}\nDirect.\n\nHence the claim.\n\\end{proof}\n",
            preamble,
        )
    )

    lemma = statement(cutting, "lem")
    assert (lemma.meaning, lemma.roles) == ("lemma", (Role.CLAIM,))
    assert statement(cutting, "defn").roles == (Role.DEFINITION,)
    (proof,) = groups(cutting, GroupKind.PROOF)
    assert (proof.names, proof.named_label) == (lemma.id, "lem:b")


def test_a_restatable_is_its_statement_and_its_restatement_names_the_proof(
    paper,
) -> None:
    preamble = "\\documentclass{article}\n\\usepackage{thmtools}\n"
    cutting = paper(
        doc(
            "\\section{S}\n"
            "\\begin{restatable}[Main]{theorem}{maintheorem}\\label{thm:main}\n"
            "Every case holds.\n\\end{restatable}\n\n"
            "\\section{A}\n"
            "\\maintheorem*\n"
            "\\begin{proof}\nBy cases.\n\nHence the claim.\n\\end{proof}\n",
            preamble,
        )
    )

    theorem = statement(cutting, "restatable")
    assert (theorem.meaning, theorem.roles) == ("theorem", (Role.CLAIM,))
    (proof,) = groups(cutting, GroupKind.PROOF)
    assert (proof.names, proof.named_label, proof.named_by) == (
        theorem.id,
        "maintheorem",
        Naming.POSITION,
    )


# --- across files ---


def root(*parts: str) -> str:
    return doc("".join(f"\\input{{{part}}}\n" for part in parts))


STATEMENT_FILE = "\\section{S}\n" + (
    "\\begin{definition}\\label{def:d}\nD.\n\\end{definition}\n"
    "\\begin{lemma}\\label{lem:l}\nL.\n\\end{lemma}\n"
)
APPENDIX = (
    "\\section{Appendix}\n"
    "\\begin{proof}[Proof of Lemma~\\ref{lem:l}]\n"
    "By Definition~\\ref{def:d}.\n\n"
    "Hence the claim.\n"
    "\\end{proof}\n"
)


class TestAcrossFiles:
    def test_a_titled_proof_in_a_later_file_names_its_result(self, paper) -> None:
        cutting = paper(
            root("a", "b"), extra={"a.tex": STATEMENT_FILE, "b.tex": APPENDIX}
        )

        (proof,) = groups(cutting, GroupKind.PROOF)
        lemma = statement(cutting, "lemma")
        assert proof.names == lemma.id
        assert lemma.extent.file == "a.tex"
        assert {cutting.cut.block(b).extent.file for b in proof.blocks} == {"b.tex"}

    def test_a_titled_proof_in_an_earlier_file_names_its_result(self, paper) -> None:
        cutting = paper(
            root("b", "a"), extra={"a.tex": STATEMENT_FILE, "b.tex": APPENDIX}
        )

        (proof,) = groups(cutting, GroupKind.PROOF)
        assert proof.names == statement(cutting, "lemma").id
        order = [block.id for block in cutting.cut.blocks]
        assert order.index(proof.blocks[0]) < order.index(proof.names)

    def test_an_untitled_proof_in_another_file_names_nothing(self, paper) -> None:
        untitled = APPENDIX.replace("[Proof of Lemma~\\ref{lem:l}]", "")
        cutting = paper(
            root("a", "b"), extra={"a.tex": STATEMENT_FILE, "b.tex": untitled}
        )

        (proof,) = groups(cutting, GroupKind.PROOF)
        assert proof.names is None

    def test_a_proof_section_in_another_file(self, paper) -> None:
        section = (
            "\\section{Proof of Lemma~\\ref{lem:l}}\nBy Definition~\\ref{def:d}.\n"
        )
        cutting = paper(
            root("a", "b", "c"),
            extra={"a.tex": STATEMENT_FILE, "b.tex": section, "c.tex": "Later.\n"},
        )

        (group,) = groups(cutting, GroupKind.PROOF_SECTION)
        assert group.names == statement(cutting, "lemma").id
        texts = [cutting.text(cutting.cut.block(b)) for b in group.blocks]
        assert texts == ["By Definition~\\ref{def:d}.", "Later."]

    def test_a_label_defined_in_two_files_is_held_by_the_first(self, paper) -> None:
        duplicate = "\\begin{remark}\\label{lem:l}\nA second copy.\n\\end{remark}\n"
        cutting = paper(
            root("a", "b", "c"),
            extra={"a.tex": STATEMENT_FILE, "b.tex": duplicate, "c.tex": APPENDIX},
        )

        (proof,) = groups(cutting, GroupKind.PROOF)
        assert proof.names == statement(cutting, "lemma").id


# --- Steps ---


def test_step_headings_make_no_group(paper) -> None:
    """Two step headings in one section are parts; a run of steps is
    judged as a whole only where its proof or section is."""
    cutting = paper(STEP_HEADINGS)

    assert cutting.cut.groups == ()
    steps = [part for part in cutting.cut.parts if is_step(part)]
    assert [part.block for part in steps] == [
        cutting.holding("Step 1:").id,
        cutting.holding("Step 2:").id,
    ]


def test_step_headings_in_a_proof_leave_one_group(paper) -> None:
    cutting = paper(
        doc(
            "\\section{S}\n"
            "\\begin{proof}\n"
            "\\paragraph{Case 1.} One.\n\nMore of one.\n\n"
            "\\paragraph{Case 2.} Two.\n"
            "\\end{proof}\n"
            "\\paragraph{Case 3.} Outside the proof.\n"
        )
    )

    (proof,) = cutting.cut.groups
    assert proof.kind is GroupKind.PROOF
    assert len(proof.blocks) == 3


def test_a_proof_holds_the_blocks_of_a_file_its_body_includes(paper) -> None:
    cutting = paper(
        doc("\\begin{proof}\nFirst.\n\\input{sub}\nLast.\n\\end{proof}\nAfter.\n"),
        extra={"sub.tex": "Included.\n"},
    )

    (proof,) = groups(cutting, GroupKind.PROOF)
    texts = [cutting.text(cutting.cut.block(b)) for b in proof.blocks]
    assert texts == ["\\begin{proof} First.", "Included.", "Last. \\end{proof}"]
