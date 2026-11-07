"""Marked edges as candidates.

One candidate per target of every reference and per key of every citation;
its source is the block that owns the command; its target resolves to the
block that holds the label, the first definition in reading order winning.
"""

from __future__ import annotations

from axiomation.cutter import CandidateKind, Form


def doc(body: str) -> str:
    return "\\begin{document}\n" + body + "\\end{document}\n"


BODY = doc(
    "\\section{Setting}\\label{sec:set}\n"
    "\\begin{lemma}\\label{lem:a}\n"
    "It holds:\n"
    "\\begin{enumerate}\n\\item \\label{it:one} one;\n\\end{enumerate}\n"
    "\\end{lemma}\n\n"
    "\\begin{equation}\\label{eq:e}\nx = y\n\\end{equation}\n\n"
    "By \\cref{lem:a,it:one} and \\eqref{eq:e}, see \\S\\ref{sec:set}"
    " and \\citep[p.~2]{smith2020,jones2021}.\\footnote{Also \\ref{lem:a}.}\n\n"
    "\\begin{proof}[Proof of \\cref{lem:a}]\nClear, unlike \\ref{nowhere}.\n"
    "\\end{proof}\n"
)


def candidates(cutting, kind=None):
    return [
        candidate
        for candidate in cutting.cut.candidates
        if kind is None or candidate.kind is kind
    ]


def test_one_candidate_per_target_and_key_in_reading_order(paper) -> None:
    cutting = paper(BODY)

    assert [(c.kind, c.command, c.target) for c in candidates(cutting)] == [
        (CandidateKind.REFERENCE, "cref", "lem:a"),
        (CandidateKind.REFERENCE, "cref", "it:one"),
        (CandidateKind.REFERENCE, "eqref", "eq:e"),
        (CandidateKind.REFERENCE, "ref", "sec:set"),
        (CandidateKind.CITATION, "citep", "smith2020"),
        (CandidateKind.CITATION, "citep", "jones2021"),
        (CandidateKind.REFERENCE, "ref", "lem:a"),
        (CandidateKind.REFERENCE, "cref", "lem:a"),
        (CandidateKind.REFERENCE, "ref", "nowhere"),
    ]


def test_the_source_is_the_block_that_owns_the_command(paper) -> None:
    cutting = paper(BODY)
    paragraph = cutting.holding("By \\cref")
    (footnote,) = cutting.blocks(Form.FOOTNOTE)
    proof = cutting.holding("\\begin{proof}")

    sources = [c.source for c in candidates(cutting)]
    assert sources[:6] == [paragraph.id] * 6
    assert sources[6] == footnote.id
    assert sources[7:] == [proof.id, proof.id]


def test_resolution_to_a_statement_an_item_a_display_and_a_heading(paper) -> None:
    cutting = paper(BODY)
    (lemma,) = cutting.blocks(Form.STATEMENT)
    (display,) = cutting.blocks(Form.DISPLAY)
    (heading,) = cutting.blocks(Form.HEADING)

    resolved = {c.target: c.resolved for c in candidates(cutting)}
    assert resolved["lem:a"] == lemma.id
    assert resolved["it:one"] == lemma.id
    assert resolved["eq:e"] == display.id
    assert resolved["sec:set"] == heading.id
    assert resolved["nowhere"] is None


def test_an_item_outside_a_statement_holds_its_label(paper) -> None:
    cutting = paper(
        doc(
            "\\begin{enumerate}\n\\item \\label{cond:a} First.\n\\item Second.\n"
            "\\end{enumerate}\nUnder \\ref{cond:a}.\n"
        )
    )

    (candidate,) = candidates(cutting)
    assert candidate.resolved == cutting.holding("First.").id


def test_citations_stay_unresolved(paper) -> None:
    cutting = paper(BODY)

    assert all(c.resolved is None for c in candidates(cutting, CandidateKind.CITATION))


def test_a_duplicate_label_resolves_to_its_first_holder(paper) -> None:
    cutting = paper(
        doc(
            "\\begin{lemma}\\label{lem:x}\nFirst.\n\\end{lemma}\n"
            "\\begin{remark}\\label{lem:x}\nSecond.\n\\end{remark}\n"
            "See \\ref{lem:x}.\n"
        )
    )

    (candidate,) = candidates(cutting)
    assert candidate.resolved == cutting.holding("First.").id
