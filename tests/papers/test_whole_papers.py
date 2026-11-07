"""The reader and the cutter on two whole papers of our own.

`tests/fixtures/papers/` holds two papers written for these tests, each a
modern rewrite of a public-domain result with no source text copied:
`euler-primes`, LaTeX over several files, and `gauss-agm`, Markdown. They
are written apart, not as one paper in two spellings, so that each carries
the shapes its own format meets. The first tests assert on both what must
hold on any paper; the rest, per paper, the shapes it carries where the
reader and the cutter handle them.

Each paper also carries shapes the cutter is known to cut wrong: in
`euler-primes` `\\ifdefined`'s opener read as text and a proof after a
one-line sentence naming nothing;
in `gauss-agm` a blockquote's `>`, a pipe table and its caption, and a
pandoc footnote, each read as prose. They are there so that the
whole-paper properties hold across them; no test pins how they are cut.

Each also carries names only a vocabulary map reads: in `euler-primes` a
reference command of its own (`\\thmref`), in `gauss-agm` a frame its YAML
header declares and an environment it never declares, beside a frame every
paper means the same way (`mdframed`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from axiomation.cutter import Block, Cut, CutRules, DisplayRule, Form, GroupKind, cut
from axiomation.cutter.coverage import coverage
from axiomation.cutter.groups import Naming
from axiomation.cutter.parts import PartKind, is_step
from axiomation.exchange import cut_payload
from axiomation.reader import (
    CandidateShape,
    Document,
    EnvEntry,
    EnvKind,
    FileRole,
    HiddenKind,
    Vocabulary,
    VocabularyKind,
    read_paper,
)
from axiomation.reader.vocabulary import candidates
from axiomation.soundness import Kept, dangling_references, duplicate_labels

PAPERS = Path(__file__).resolve().parents[1] / "fixtures" / "papers"
EULER = PAPERS / "euler-primes"
GAUSS = PAPERS / "gauss-agm"
BOTH = [EULER, GAUSS]
NAMES = [paper.name for paper in BOTH]


@pytest.fixture(scope="module")
def read() -> dict[Path, Document]:
    return {paper: read_paper(paper) for paper in BOTH}


def cut_of(document: Document, displays: DisplayRule = DisplayRule.OWN_BLOCK) -> Cut:
    return cut(document, CutRules(displays))


def text(document: Document, block: Block) -> str:
    return " ".join(" ".join(document.text(run) for run in block.owned).split())


def holding(document: Document, found: Cut, fragment: str) -> Block:
    """The one block whose owned text holds `fragment`."""
    (block,) = [b for b in found.blocks if fragment in text(document, b)]
    return block


def named_proofs(
    found: Cut, unasserted: str | None = None
) -> list[tuple[str, GroupKind, Naming | None]]:
    """Each proof group that names a result, by its label, but
    `unasserted`'s, a known miss whose naming is not pinned either way."""
    return sorted(
        (group.named_label, group.kind, group.named_by)
        for group in found.groups
        if group.kind in (GroupKind.PROOF, GroupKind.PROOF_SECTION)
        and group.named_label is not None
        and group.named_label != unasserted
    )


# --- What holds on any paper ---


@pytest.mark.parametrize("paper", BOTH, ids=NAMES)
def test_the_paper_is_read_and_every_environment_closes(read, paper) -> None:
    document = read[paper]

    assert document.files[0].role is FileRole.ROOT
    assert [env.name for env in document.environments if not env.closed] == []
    assert document.title is not None
    assert document.sections


@pytest.mark.parametrize("paper", BOTH, ids=NAMES)
def test_the_title_is_rendered(read, paper) -> None:
    """The title is rendered text: a hidden span that touches it is a
    comment, a structure line, or front matter inside it (a `\\thanks`),
    so the cutter's blocks must cover it."""
    document = read[paper]
    title = document.title
    source = document.file(title.file)

    assert document.text(title).strip()
    assert [
        (hidden.kind, hidden.span)
        for hidden in source.hidden
        if hidden.span.start < title.end
        and hidden.span.end > title.start
        and hidden.kind not in (HiddenKind.COMMENT, HiddenKind.STRUCTURE)
        and not (
            hidden.kind is HiddenKind.FRONT_MATTER
            and title.start < hidden.span.start
            and hidden.span.end <= title.end
        )
    ] == []


@pytest.mark.parametrize("displays", list(DisplayRule), ids=lambda r: r.value)
@pytest.mark.parametrize("paper", BOTH, ids=NAMES)
def test_every_rendered_character_is_owned_exactly_once(read, paper, displays):
    document = read[paper]
    covered = coverage(document, cut_of(document, displays))

    assert covered.unowned == ()
    assert covered.owned_twice == ()


@pytest.mark.parametrize("paper", BOTH, ids=NAMES)
def test_axiomation_cut_gives_the_same_bytes_in_two_processes(paper, tmp_path):
    """`axiomation cut` twice, each in its own interpreter with its own hash
    seed, so an order that hangs on a set or a hash shows, in the reader or
    the cutter."""
    outputs = []
    for seed in ("1", "2"):
        output = tmp_path / f"{seed}.json"
        subprocess.run(
            [sys.executable, "-m", "axiomation.cli", "cut", str(paper)]
            + ["-o", str(output)],
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        outputs.append(output.read_bytes())

    assert outputs[0] == outputs[1]


#: Labels each paper points at through a reference command of its own
#: kind (`\\cref` lists and ranges, `\\vref`, `\\nameref`, `\\autoref`,
#: `\\eqref`, `\\ref` to an item), so that no dangling reference is not
#: no reference read.
REFERENCED = {
    EULER: {
        "sec:remarks",
        "sec:introduction",
        "eq:integral",
        "eq:integral-value",
        "lem:tail",
        "fig:partial",
        "it:upper",
    },
    GAUSS: {"thm:agm", "lem:order", "lem:gap", "sec:proof"},
}


@pytest.mark.parametrize("paper", BOTH, ids=NAMES)
def test_no_label_dangles_or_repeats(read, paper) -> None:
    document = read[paper]
    found = cut_of(document, DisplayRule.IN_PARAGRAPH)
    kept = Kept(document, cut_payload(document, found))
    targets = {target for ref in document.references for target in ref.targets}

    assert REFERENCED[paper] <= targets
    assert dangling_references(kept) == []
    assert duplicate_labels(kept) == []


# --- euler-primes: LaTeX over several files ---


def test_euler_reads_its_files_in_reading_order(read) -> None:
    """`divergence.tex` comes in by `\\include` and pulls in `remarks.tex`
    on its last line, with no newline after it; the appendix comes last."""
    document = read[EULER]

    assert [(f.path, f.role) for f in document.files] == [
        ("main.tex", FileRole.ROOT),
        ("preamble.tex", FileRole.PREAMBLE),
        ("eulernotes.sty", FileRole.PACKAGE),
        ("sections/introduction.tex", FileRole.BODY),
        ("sections/product.tex", FileRole.BODY),
        ("sections/divergence.tex", FileRole.BODY),
        ("sections/remarks.tex", FileRole.BODY),
        ("appendix/proofs.tex", FileRole.BODY),
    ]
    assert document.never_included == ("README.md", "notes.tex")


def test_euler_hides_its_iffalse_section_and_its_title_s_thanks(read) -> None:
    document = read[EULER]
    found = cut_of(document)

    assert "An older argument" not in [s.title for s in document.sections]
    title = holding(document, found, "The Euler Product")
    assert title.form is Form.TITLE
    assert text(document, title) == (
        "The Euler Product and the Divergence of the Prime Reciprocals"
    )


def test_euler_s_declarations_carry_their_meanings(read) -> None:
    """The babel name prints its word; a name with no standard meaning
    (`claim`, `example`) is a statement of none; a wrapper of a proof is
    read as one."""
    declared = {d.name: (d.printed, d.meaning) for d in read[EULER].declarations}

    assert declared == {
        "thm": ("Theorem", "theorem"),
        "lem": ("Lemma", "lemma"),
        "prop": ("Proposition", "proposition"),
        "cor": ("Corollary", "corollary"),
        "claim": ("Claim", None),
        "defn": ("Definition", "definition"),
        "assum": ("Assumption", "assumption"),
        "example": ("Example", None),
        "rem": ("Remark", "remark"),
        "proofsketch": ("", "proof"),
    }


def test_euler_s_restatable_is_its_theorem_and_the_appendix_restates_it(
    read,
) -> None:
    document = read[EULER]
    (restatable,) = [e for e in document.environments if e.name == "restatable"]
    (sketch,) = [e for e in document.environments if e.name == "proofsketch"]

    assert (restatable.kind, restatable.meaning, restatable.title) == (
        EnvKind.STATEMENT,
        "theorem",
        "Euler product",
    )
    assert sketch.kind is EnvKind.PROOF
    assert [
        (r.command, r.word, r.targets, r.at.file)
        for r in document.references
        if r.command == "eulerproduct"
    ] == [("eulerproduct", "Theorem", ("eulerproduct",), "appendix/proofs.tex")]
    found = cut_of(document)
    theorem = holding(document, found, "For every $s > 1$ the product")
    restated = holding(document, found, "\\eulerproduct*")
    assert (theorem.form, theorem.meaning) == (Form.STATEMENT, "theorem")
    assert restated.form is Form.PROSE


def test_euler_read_with_a_map_keeps_what_code_reads() -> None:
    """A kept state and an update read with a map, an empty one where no
    name is asked: code's rows (the restating command among them) read
    the same under any map."""
    bare = read_paper(EULER)
    for vocabulary in (
        Vocabulary(),
        Vocabulary(environments=(EnvEntry("quote", VocabularyKind.CONTAINER),)),
    ):
        mapped = read_paper(EULER, vocabulary=vocabulary)

        assert mapped.references == bare.references
        assert mapped.labels == bare.labels
        assert cut_of(mapped).candidates == cut_of(bare).candidates


def test_euler_s_float_barrier_makes_no_block(read) -> None:
    """The `\\FloatBarrier` line after the figure lays out the page and
    prints nothing (`LAYOUT_COMMANDS`)."""
    document = read[EULER]

    assert not any(
        "FloatBarrier" in text(document, block) for block in cut_of(document).blocks
    )


def test_euler_s_directive_in_a_verbatim_block_is_an_example(read) -> None:
    assert [(d.name, d.body) for d in read[EULER].directives] == [
        ("claim", "C001 from=thm:divergence")
    ]


def test_euler_s_proofs_name_their_results(read) -> None:
    """By position, by a proof's title, by a section's heading. The proof
    of `lem:tail`, after a one-line sentence, is a known miss: whether it
    names its lemma is not asserted."""
    found = named_proofs(cut_of(read[EULER]), unasserted="lem:tail")

    assert found == [
        ("lem:geometric", GroupKind.PROOF, Naming.POSITION),
        ("lem:log", GroupKind.PROOF, Naming.POSITION),
        ("thm:divergence", GroupKind.PROOF, Naming.TITLE),
        ("thm:euler-product", GroupKind.PROOF_SECTION, Naming.HEADING),
    ]


def test_euler_s_parts(read) -> None:
    """Step headings, labelled `align` rows, a `\\label` just before a
    display, `{\\bf …}` and `\\paragraph` run-ins, italic run-ins at
    items, and items inside a statement."""
    document = read[EULER]
    found = cut_of(document)
    steps = [p for p in found.parts if is_step(p)]
    displays = [p.marker for p in found.parts if p.kind is PartKind.LABELLED_DISPLAY]
    headed = [
        p.marker for p in found.parts if p.kind is PartKind.HEADED and not is_step(p)
    ]
    lemma = holding(document, found, "Let $s > 1$. Then:")

    assert [p.marker for p in steps] == ["Step 1.", "Step 2."]
    assert displays == [
        "eq:euler-product",
        "eq:tail",
        "eq:integral",
        "eq:integral-value",
    ]
    assert headed == [
        "What is new here.",
        "A consequence.",
        "(Chebyshev's bound.)",
        "(Partial summation.)",
    ]
    assert [p.block for p in found.parts if p.kind is PartKind.ITEM].count(
        lemma.id
    ) == 2


def test_euler_s_footnotes_nest_in_their_blocks(read) -> None:
    """One in a paragraph, its argument holding `\\citet[…]{…}` and a
    braced `{\\S}`; one in a statement."""
    document = read[EULER]
    found = cut_of(document)
    footnotes = [b for b in found.blocks if b.form is Form.FOOTNOTE]
    forms = {b.id: b.form for b in found.blocks}

    assert [forms.get(b.within) for b in footnotes] == [
        Form.PROSE,
        Form.STATEMENT,
    ]
    assert text(document, footnotes[0]).endswith("the units.}")


def test_euler_s_citations_in_order(read) -> None:
    keys = [key for citation in read[EULER].citations for key in citation.keys]

    assert keys == [
        "euler1737",
        "hardywright",
        "primer2001",
        "primer2001",
        "primer2001",
        "mertens1874",
        "primer2001",
        "primer2001",
    ]


def test_euler_s_unknown_environment_is_a_doubted_block_and_its_frame_is_read(
    read,
) -> None:
    document = read[EULER]
    found = cut_of(document)
    unknown = [
        b.environment for b in found.blocks if b.form is Form.ENVIRONMENT and b.doubts
    ]
    code = holding(document, found, "x = 1000000")

    assert unknown == ["quote"]
    assert code.form is Form.CODE
    # tcolorbox's own frame is a container in every paper
    assert holding(document, found, "A computation of the partial sums").form is (
        Form.PROSE
    )


def test_euler_s_own_reference_command_is_read_by_code(read) -> None:
    document = read[EULER]
    shapes = {c.name: c.shape for c in candidates(document)}

    # `\\thmref`'s body is one `\\ref` over `#1`: a static row, no map entry
    assert shapes == {"quote": CandidateShape.ENVIRONMENT}
    assert [
        (r.word, r.targets) for r in document.references if r.command == "thmref"
    ] == [("Theorem", ("thm:divergence",))]


# --- gauss-agm: Markdown ---


def test_gauss_reads_its_front_matter_and_heading_attributes(read) -> None:
    document = read[GAUSS]

    assert document.text(document.title) == (
        "The Arithmetic–Geometric Mean and Its Common Limit"
    )
    assert document.never_included == ("notes.tex",)
    labels = [label.name for label in document.labels]
    assert {"sec:intro", "sec:limit", "appendix", "sec:proof"} <= set(labels)
    assert [s.title for s in document.sections] == [
        "Introduction",
        "Two means",
        "The common limit",
        "Appendix",
        "Proof of \\cref{thm:agm}",
    ]


def test_gauss_s_directive_in_a_code_fence_is_an_example(read) -> None:
    assert [(d.name, d.body) for d in read[GAUSS].directives] == [
        ("claim", "C001 from=thm:agm"),
        ("claim", "C002 from=prop:homogeneous"),
    ]


def test_gauss_s_proofs_name_their_results(read) -> None:
    """A trailing proof of two paragraphs, and an appendix section titled
    for the theorem; the one-paragraph proof is a block, no group."""
    found = named_proofs(cut_of(read[GAUSS]))

    assert found == [
        ("lem:order", GroupKind.PROOF, Naming.POSITION),
        ("thm:agm", GroupKind.PROOF_SECTION, Naming.HEADING),
    ]


def test_gauss_s_parts(read) -> None:
    """`**Step 1.**` runs in a proof, a bold run opening a paragraph,
    labelled `align` rows, and list items with their markers, nested."""
    found = cut_of(read[GAUSS])

    assert [p.marker for p in found.parts if is_step(p)] == ["Step 1.", "Step 2."]
    assert [p.marker for p in found.parts if p.kind is PartKind.ITEM] == [
        "1.",
        "2.",
        "3.",
        "-",
        "-",
    ]
    assert [p.marker for p in found.parts if p.kind is PartKind.LABELLED_DISPLAY] == [
        "eq:arith",
        "eq:geom",
    ]
    assert "What we prove." in [
        p.marker for p in found.parts if p.kind is PartKind.HEADED
    ]


def test_gauss_s_displays_and_prose(read) -> None:
    """A display set apart after a broken sentence, a lower-case line after
    it continuing the paragraph under the in-paragraph rule; a wrapped
    line that opens with a number is prose; `%` is text in Markdown; an
    emphasised paragraph set alone stays a block."""
    document = read[GAUSS]
    own = cut_of(document)
    joined = cut_of(document, DisplayRule.IN_PARAGRAPH)

    assert holding(document, own, "{8\\,a_{n+2}}").form is Form.DISPLAY
    assert "which is the identity" in text(
        document, holding(document, joined, "{8\\,a_{n+2}}")
    )
    assert text(document, holding(document, own, "1799 is the year")).startswith(
        "The constant 1799"
    )
    assert text(document, holding(document, own, "That $M$ is positive")).endswith(
        "% not a comment in Markdown"
    )
    assert text(document, holding(document, own, "*Note.*")) == "*Note.*"


def test_gauss_s_footnote_nests_in_its_paragraph(read) -> None:
    document = read[GAUSS]
    found = cut_of(document)
    (footnote,) = [
        b
        for b in found.blocks
        if b.form is Form.FOOTNOTE and "\\citep{cox1984" in text(document, b)
    ]

    assert footnote.within == (
        holding(document, found, "The iteration is defined for positive numbers").id
    )
    assert "\\citep{cox1984,lectures2010}" in text(document, footnote)
    assert [env.kind for env in document.environments].count(EnvKind.VERBATIM) == 1


def test_gauss_s_frames_and_its_undeclared_environment(read) -> None:
    document = read[GAUSS]
    found = {c.name: c for c in candidates(document)}
    mapped = read_paper(
        GAUSS,
        vocabulary=Vocabulary(
            environments=(EnvEntry("exercise", VocabularyKind.STATEMENT),)
        ),
    )
    boxed = cut_of(mapped)

    # `summarybox` is mdframed's `\\newmdenv` in the YAML header: a frame
    # code reads; `exercise` is declared nowhere and is the map's
    assert list(found) == ["exercise"]
    kinds = {e.name: e.kind for e in document.environments}
    assert kinds["summarybox"] is kinds["mdframed"] is EnvKind.CONTAINER
    assert holding(mapped, boxed, "Each step at least squares").form is Form.PROSE
    assert holding(mapped, boxed, "Compute $M(1, 2)$").form is Form.STATEMENT
    assert (
        candidates(
            mapped,
            Vocabulary(environments=(EnvEntry("exercise", VocabularyKind.STATEMENT),)),
        )
        == ()
    )
