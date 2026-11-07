"""The roles as a session on `roles.md`: the file, its key,
its parse and check."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.cutter.model import Role, Segment
from axiomation.questions import Stage, load_template, roles_file
from axiomation.questions.front_lines import shown_file
from axiomation.questions.roles_file import (
    CONTEXT_NOTE,
    KIND,
    ROLES_FILE,
    SESSION_TEMPLATE,
    UNSEGMENTED_NOTE,
    check,
    kept_problems,
    session_questions,
    touched_question,
)
from axiomation.records import CutOp, Signal
from questions.review_paper import HIDDEN, MAIN, make_paper

GOLDEN = Path(__file__).parent / "golden"
SESSION = load_template(SESSION_TEMPLATE)

#: The synthetic paper's blocks as the whole-paper file numbers them.
ROLES = {
    "b1": "none",
    "b2": "assumption",
    "b3": "definition",
    "b4": "none",
    "b5": "claim",
    "b6": "justification",
    "b7": "definition",
    "b8": "claim",
    "b9": "none",
    "b10": "none",
    "b11": "claim",
}


def session(paper, cut=None, budget=None):
    (question,) = session_questions(paper.document, cut or paper.cut, budget, SESSION)
    return question


def filled(question, roles=ROLES) -> str:
    """The file with every block's `?` replaced by its role."""
    text = shown_file(question)
    for handle, role in roles.items():
        text = text.replace(f"⟦{handle}⟧", f"⟦{handle} {role}⟧", 1)
    return text


def by_block(paper, found):
    return {c.block: c for c in found.corrections}


# --- the file and its key ----------------------------------------------------------


def test_one_session_for_the_whole_paper(paper) -> None:
    question = session(paper)

    assert question.stage is Stage.ROLES and question.kind == KIND
    assert question.workspace.edit == ROLES_FILE
    assert question.workspace.instructions == SESSION.text
    assert [h for h in question.handles if h.startswith("b")] == list(ROLES)


def test_chunks_follow_the_boundary_stage(paper) -> None:
    questions = session_questions(paper.document, paper.cut, 300, SESSION)

    assert len(questions) > 1
    assert {q.input_cut for q in questions} == {paper.cut}


def test_every_marker_is_bare(paper) -> None:
    """The theorem carries the cutter's `claim`; its marker shows the
    handle alone, as every block's does."""
    text = shown_file(session(paper))

    markers = [line for line in text.splitlines() if line.startswith("⟦b")]
    assert all(
        m.endswith("⟧") and (" " not in m or " continued⟧" in m) for m in markers
    )
    assert "⟦b5⟧" in text and "claim" not in text and "form:" not in text


def test_the_key_ignores_roles_and_binds_the_text(paper, tmp_path) -> None:
    target = paper.id_at(r"\begin{theorem}")
    blocks = tuple(
        replace(b, roles=(Role.DEFINITION,), segments=(Segment("", Role.DEFINITION),))
        if b.id == target
        else b
        for b in paper.cut.blocks
    )
    roled = session(paper, replace(paper.cut, blocks=blocks))
    edited = make_paper(
        tmp_path / "edited",
        MAIN.replace("Next, the bound is sharp.", "Next, the bound is tight."),
    )

    assert roled.key == session(paper).key
    assert roled.workspace.files == session(paper).workspace.files
    assert session(edited).key != session(paper).key


def test_no_absolute_path_and_no_hidden_text(paper) -> None:
    text = shown_file(session(paper))

    assert str(paper.root) not in text and HIDDEN not in text


def test_the_file_matches_its_golden(paper) -> None:
    path = GOLDEN / "roles.md"
    text = shown_file(session(paper))
    assert path.exists(), f"missing golden {path}; write it deliberately, reviewed"
    assert text == path.read_text(encoding="utf-8"), (
        "roles.md differs from its golden; review the change before updating it"
    )


# --- answers ------------------------------------------------------------------------


def test_every_block_gets_its_roles(paper) -> None:
    question = session(paper)

    found = check(question, filled(question), paper.document)

    assert found.valid, found.problems
    assert len(found.corrections) == len(ROLES)
    assert all(c.op is CutOp.ROLES and c.check() == () for c in found.corrections)
    corrections = by_block(paper, found)
    theorem = corrections[paper.id_at(r"\begin{theorem}")]
    assert theorem.roles == (Role.CLAIM,)
    assert theorem.segments == (Segment("", Role.CLAIM),)
    heading = corrections[paper.id_at(r"\section{Setting}")]
    assert heading.roles == () and heading.segments == ()


def test_a_segment_line_starts_a_span_with_another_role(paper) -> None:
    """In the proof's first paragraph, its conclusion read as a claim: a
    line inside the block, mid-paragraph, after the footnote."""
    question = session(paper)
    text = filled(question).replace(
        "Hence the output is bounded.", "⟦+ claim⟧\nHence the output is bounded."
    )

    found = check(question, text, paper.document)

    assert found.valid, found.problems
    proof = by_block(paper, found)[paper.id_at(r"\begin{proof}")]
    assert proof.roles == (Role.CLAIM, Role.JUSTIFICATION)
    assert proof.segments == (
        Segment("", Role.JUSTIFICATION),
        Segment("Hence the output", Role.CLAIM),
    )


def test_a_segment_may_start_mid_line(paper) -> None:
    question = session(paper)
    text = filled(question).replace(
        "We fix a field of scalars.\nA second",
        "We fix a field\n⟦+ motivation⟧\nof scalars.\nA second",
    )

    found = check(question, text, paper.document)

    assert found.valid, found.problems
    setting = by_block(paper, found)[paper.id_at("We fix")]
    # The excerpt is placed as a split's is: the fewest words from there
    # that occur once, read from the source, so it may run into hidden text.
    assert setting.segments[1].role is Role.MOTIVATION
    assert setting.segments[1].starts.startswith("of scalars.")


def test_the_roles_apply_to_the_cut(paper) -> None:
    from axiomation.assembler import corrected_cut

    question = session(paper)
    text = filled(question).replace(
        "Hence the output is bounded.", "⟦+ claim⟧\nHence the output is bounded."
    )
    found = check(question, text, paper.document)

    applied = corrected_cut(
        paper.document,
        paper.cut,
        [replace(c, id=str(n)) for n, c in enumerate(found.corrections, 1)],
    )

    assert applied.problems == ()
    assert [b.id for b in applied.cut.blocks] == [b.id for b in paper.cut.blocks]
    proof = applied.cut.block(paper.id_at(r"\begin{proof}"))
    assert proof.roles == (Role.CLAIM, Role.JUSTIFICATION)


@pytest.mark.parametrize(
    ("edit", "problem"),
    [
        (lambda t: t.replace("⟦b5 claim⟧", "⟦b5⟧"), "b5 has no role yet"),
        (
            lambda t: t.replace("⟦b5 claim⟧", "⟦b5 claim, justification⟧"),
            "b5 names 2 roles",
        ),
        (lambda t: t.replace("⟦b5 claim⟧", "⟦b5 remark⟧"), "'remark' is not a role"),
        (
            lambda t: t.replace("Hence the", "⟦+ justification⟧\nHence the"),
            "continues the role before it",
        ),
        (
            lambda t: t.replace("\\section{Results}", "⟦+ claim⟧\n\\section{Results}"),
            "it starts b4",
        ),
        (
            lambda t: t.replace("Closing words", "⟦+ claim⟧\nClosing words"),
            "it starts b9",
        ),
        (
            lambda t: t.replace("\\section{More}", "\\section{More}\n⟦+ claim⟧"),
            "it starts b11",
        ),
        (
            lambda t: t.replace("⟦b9 none⟧", "⟦new⟧"),
            "was ⟦b9⟧; the blocks' lines are fixed",
        ),
        (
            lambda t: t.replace("⟦b9 none⟧ — ", "").replace("Closing", "⟦b9 none⟧\nX"),
            "text changed",
        ),
        (lambda t: t.replace("Closing words", "Opening words"), "text changed"),
    ],
)
def test_a_bad_edit_is_refused(paper, edit, problem) -> None:
    question = session(paper)

    found = check(question, edit(filled(question)), paper.document)

    assert not found.valid and not found.corrections
    assert any(problem in p for p in found.problems), found.problems


def test_a_span_inside_a_block_may_play_none(paper) -> None:
    """A transition inside a block: its span plays
    none, and the block's roles are its other spans'."""
    question = session(paper)
    text = filled(question).replace("Hence the", "⟦+ none⟧\nHence the")

    found = check(question, text, paper.document)

    assert found.valid, found.problems
    proof = by_block(paper, found)[paper.id_at(r"\begin{proof}")]
    assert proof.roles == (Role.JUSTIFICATION,)
    assert proof.segments == (
        Segment("", Role.JUSTIFICATION),
        Segment("Hence the output", None),
    )


def test_a_block_may_start_with_a_span_that_plays_none(paper) -> None:
    question = session(paper)
    text = filled(question).replace(
        "Closing words of the results.", "Closing words\n⟦+ claim⟧\nof the results."
    )

    found = check(question, text, paper.document)

    assert found.valid, found.problems
    closing = by_block(paper, found)[paper.id_at("Closing words")]
    assert closing.roles == (Role.CLAIM,)
    assert closing.segments == (
        Segment("", None),
        Segment("of the results.", Role.CLAIM),
    )


def test_context_takes_no_segment(paper) -> None:
    """At 100 characters a chunk shows its neighbours as context."""
    questions = session_questions(paper.document, paper.cut, 100, SESSION, overlap=2)
    question = next(q for q in questions if "⟦context⟧" in shown_file(q))
    text = shown_file(question)
    for handle in [h for h in question.handles if h.startswith("b")]:
        text = text.replace(f"⟦{handle}⟧", f"⟦{handle} none⟧", 1)
    context = text.split("⟦context⟧", 1)[1].strip().splitlines()[0]
    text = text.replace(context, "⟦+ claim⟧\n" + context, 1)

    found = check(question, text, paper.document)

    assert not found.valid
    assert any("lies in the context" in p for p in found.problems), found.problems


def test_a_first_pass_reads_paper_md_and_answers_as_lines(paper) -> None:
    """The lines a first pass writes correct as the same roles edited into
    the file, which the answer keeps."""
    question = session(paper)
    workspace = question.workspace
    assert set(workspace.files) == {"paper.md", ROLES_FILE}
    assert workspace.files[ROLES_FILE] == ""
    assert workspace.gate.module == "axiomation.questions.front_lines"
    assert workspace.tools == ("Bash", "Read", "Edit", "Write")
    assert workspace.brief_alone and workspace.reask_from_edit

    lines = "\n".join(f"{h} {role}" for h, role in ROLES.items() if h != "b6")
    lines += '\nb6 justification | claim "Hence the output is bounded."\n'
    edited = filled(question).replace(
        "Hence the output is bounded.", "⟦+ claim⟧\nHence the output is bounded."
    )
    by_lines = check(question, lines, paper.document)
    by_file = check(question, edited, paper.document)
    assert by_lines.valid, by_lines.problems
    assert by_lines.corrections == by_file.corrections
    assert by_lines.kept is not None and by_lines.kept.split() == edited.split()
    assert by_file.kept is None  # a file is read, and kept, as it is


# --- a review pass ------------------------------------------------------------------

REVIEW = load_template("roles-review")


def first_pass(paper):
    """The cut with a first pass's roles applied: the proof's conclusion a
    claim of its own, and a transition span that plays none."""
    from axiomation.assembler import corrected_cut

    question = session(paper)
    text = (
        filled(question)
        .replace(
            "Hence the output is bounded.", "⟦+ claim⟧\nHence the output is bounded."
        )
        .replace("A second sentence", "⟦+ none⟧\nA second sentence")
    )
    found = check(question, text, paper.document)
    assert found.valid, found.problems
    return corrected_cut(
        paper.document,
        paper.cut,
        [replace(c, id=str(n)) for n, c in enumerate(found.corrections, 1)],
    ).cut


def review(paper, cut):
    (question,) = session_questions(paper.document, cut, None, REVIEW, pass_=2)
    return question


def test_a_review_shows_the_roles_the_cut_carries(paper) -> None:
    text = shown_file(review(paper, first_pass(paper)))

    assert "⟦b5 claim⟧" in text and "⟦b1 none⟧" in text
    assert "⟦b6 justification⟧" in text
    assert "⟦+ claim⟧\nHence the output is bounded." in text
    assert "⟦+ none⟧\nA second sentence" in text


def test_an_unchanged_review_reads_back_as_the_roles_it_shows(paper) -> None:
    cut = first_pass(paper)
    question = review(paper, cut)

    found = check(question, shown_file(question), paper.document)

    assert found.valid, found.problems
    for correction in found.corrections:
        block = cut.block(correction.block)
        assert (correction.roles, correction.segments) == (block.roles, block.segments)


def _held_unsegmented(paper):
    """The first pass's cut with the theorem holding the claim role beside
    its motivation and no part, as a standing's claim leaves a block
    (docs/model.md § 4), and its review."""
    cut = first_pass(paper)
    theorem = paper.id_at(r"\begin{theorem}")
    both = replace(cut.block(theorem), roles=(Role.CLAIM, Role.MOTIVATION), segments=())
    cut = replace(cut, blocks=tuple(both if b.id == theorem else b for b in cut.blocks))
    return theorem, review(paper, cut)


#: The theorem's marker as `_held_unsegmented`'s review shows it.
HELD = f"⟦b5 claim, motivation — {UNSEGMENTED_NOTE}⟧"


def test_a_block_held_unsegmented_reads_back_as_held(paper) -> None:
    theorem, question = _held_unsegmented(paper)
    shown = shown_file(question)
    assert HELD in shown
    assert kept_problems(question, paper.document) == ()

    reordered = shown.replace(HELD, "⟦b5 motivation, claim⟧")
    for text in (shown, reordered):
        found = check(question, text, paper.document)
        assert found.valid, found.problems
        corrections = by_block(paper, found)
        assert (corrections[theorem].roles, corrections[theorem].segments) == (
            (Role.CLAIM, Role.MOTIVATION),
            (),
        )


def test_a_block_is_never_made_unsegmented_by_its_marker(paper) -> None:
    _, question = _held_unsegmented(paper)
    shown = shown_file(question)
    assert "\nThe output is bounded." in shown

    other = check(
        question,
        shown.replace(HELD, "⟦b5 claim, justification⟧"),
        paper.document,
    )
    parted = check(
        question,
        shown.replace(
            "\nThe output is bounded.", "\n⟦+ claim⟧\nThe output is bounded."
        ),
        paper.document,
    )

    assert any("b5 names 2 roles" in p for p in other.problems), other.problems
    assert any("holds unsegmented" in p for p in parted.problems), parted.problems


def test_a_held_marker_copied_onto_another_block_is_refused(paper) -> None:
    """The marker of a block held unsegmented says it is the cut's: another
    block given its roles so is refused, with the form to write instead."""
    _, question = _held_unsegmented(paper)
    shown = shown_file(question)
    assert "⟦b8 claim⟧" in shown

    copied = check(
        question,
        shown.replace("⟦b8 claim⟧", HELD.replace("b5", "b8")),
        paper.document,
    )

    assert any(
        "b8 names 2 roles" in p and "only where the cut holds a block so" in p
        for p in copied.problems
    ), copied.problems


def test_a_held_marker_keeps_its_note_beside_another(paper) -> None:
    theorem, held = _held_unsegmented(paper)
    (question,) = session_questions(
        paper.document,
        held.input_cut,
        None,
        REVIEW,
        pass_=2,
        notes={theorem: "claim added by edges"},
    )
    shown = shown_file(question)

    assert f"⟦b5 claim, motivation — claim added by edges; {UNSEGMENTED_NOTE}⟧" in shown
    assert check(question, shown, paper.document).valid


def test_a_block_left_as_shown_is_not_judged_under_a_later_rule(
    paper, monkeypatch
) -> None:
    """The file's check tightened after the state was kept: it no longer
    reads a block held unsegmented. An edit elsewhere leaves that line as
    shown, so it is the state's: not judged, and its roles kept
    (docs/model.md § 7)."""
    monkeypatch.setattr(roles_file, "_held_unsegmented", lambda *_: False)
    theorem, question = _held_unsegmented(paper)
    shown = shown_file(question)
    assert any("b5 names 2 roles" in p for p in kept_problems(question, paper.document))

    found = check(
        question, shown.replace("⟦b1 none⟧", "⟦b1 motivation⟧"), paper.document
    )

    assert found.valid, found.problems
    corrections = by_block(paper, found)
    assert (corrections[theorem].roles, corrections[theorem].segments) == (
        (Role.CLAIM, Role.MOTIVATION),
        (),
    )
    # a part marked where the next kept block starts is the edit's, and judged
    marked = shown.replace("⟦b6 justification⟧", "⟦+ claim⟧\n⟦b6 justification⟧")
    refused = check(question, marked, paper.document)
    assert not refused.valid
    assert any("it starts b6" in p for p in refused.problems), refused.problems
    # changed, the line is the edit's, and judged
    changed = shown.replace(HELD, "⟦b5 motivation, claim⟧")
    refused = check(question, changed, paper.document)
    assert not refused.valid
    assert any("b5 names 2 roles" in p for p in refused.problems), refused.problems


def test_a_review_changes_a_role_and_drops_a_part(paper) -> None:
    cut = first_pass(paper)
    question = review(paper, cut)
    text = (
        shown_file(question)
        .replace("⟦b5 claim⟧", "⟦b5 definition⟧")
        .replace("⟦+ claim⟧\n", "")
    )

    found = check(question, text, paper.document)

    assert found.valid, found.problems
    corrections = by_block(paper, found)
    assert corrections[paper.id_at(r"\begin{theorem}")].roles == (Role.DEFINITION,)
    proof = corrections[paper.id_at(r"\begin{proof}")]
    assert proof.segments == (Segment("", Role.JUSTIFICATION),)


def test_a_review_s_key_binds_its_pass_and_the_roles_shown(paper) -> None:
    cut = first_pass(paper)

    assert review(paper, cut).key != session(paper, cut).key
    assert review(paper, cut).key != review(paper, paper.cut).key
    (third,) = session_questions(paper.document, cut, None, REVIEW, pass_=3)
    assert third.key != review(paper, cut).key


def test_the_review_file_matches_its_golden(paper) -> None:
    path = GOLDEN / "roles-review.md"
    text = shown_file(review(paper, first_pass(paper)))
    assert path.exists(), f"missing golden {path}; write it deliberately, reviewed"
    assert text == path.read_text(encoding="utf-8"), (
        "roles-review.md differs from its golden; review the change before updating it"
    )


# --- the roles of the blocks a cut correction changed ------------


def test_a_touched_question_asks_the_changed_blocks_alone(paper) -> None:
    theorem = paper.id_at(r"\begin{theorem}")
    closing = paper.id_at("Closing words of the results.")

    question = touched_question(
        paper.document, paper.cut, [closing, theorem], SESSION, overlap=1
    )

    assert question.stage is Stage.ROLES and question.kind == KIND
    # The changed blocks, in reading order, are the session's; a block on
    # either side of each is context.
    ours = {h: b for h, b in question.handles.items() if h.startswith("b")}
    assert ours == {"b1": theorem, "b2": closing}
    order = [b.id for b in paper.cut.blocks]
    near = {order[order.index(theorem) + d] for d in (-1, 1)} | {
        order[order.index(closing) + d] for d in (-1, 1)
    }
    context = {b for h, b in question.handles.items() if h.startswith("c")}
    assert context == near - {theorem, closing}
    text = shown_file(question)
    assert "⟦b1⟧" in text and "⟦b2⟧" in text and "⟦context⟧ — roles: " in text
    assert question.workspace.instructions == f"{SESSION.text}\n\n{CONTEXT_NOTE}\n"

    answer = text.replace("⟦b1⟧", "⟦b1 claim⟧").replace("⟦b2⟧", "⟦b2 none⟧")
    found = check(question, answer, paper.document)
    assert found.valid, found.problems
    assert [(c.op, c.block) for c in found.corrections] == [
        (CutOp.ROLES, theorem),
        (CutOp.ROLES, closing),
    ]
    # as lines, the context written no line
    by_lines = check(question, "b1 claim\nb2 none\n", paper.document)
    assert by_lines.corrections == found.corrections


def test_a_touched_question_s_key_binds_its_blocks_and_round(paper) -> None:
    theorem = paper.id_at(r"\begin{theorem}")

    def key(blocks, round_=1, overlap=2):
        return touched_question(
            paper.document, paper.cut, blocks, SESSION, overlap, round_
        ).key

    assert key([theorem]) == key([theorem], overlap=0)  # context is context
    assert key([theorem], round_=2) != key([theorem])
    assert key([theorem, paper.id_at("Closing words")]) != key([theorem])


# --- flags -----------------------------------------------------------


def flagged(question, line: str, under: str = "⟦b3 definition⟧") -> str:
    """The filled file with `line` under the marker `under`."""
    return filled(question).replace(under, f"{under}\n{line}", 1)


def test_a_flag_line_is_a_flag_and_changes_nothing_else(paper) -> None:
    question = session(paper)
    plain = check(question, filled(question), paper.document)

    found = check(
        question,
        flagged(question, '⟦? split "ratio of  output" — a second unit starts⟧'),
        paper.document,
    )

    assert found.valid, found.problems
    assert found.corrections == plain.corrections and plain.flags == ()
    (flag,) = found.flags
    assert flag.blocks == (question.handles["b3"],)
    assert flag.signal is Signal.SPLIT
    assert flag.evidence == ("ratio of output",)
    assert flag.note == "a second unit starts"
    assert (flag.id, flag.answer) == ("", None)


def test_a_flag_without_a_signal_word_is_friction(paper) -> None:
    question = session(paper)

    found = check(
        question, flagged(question, '⟦? "The gain" — hard to place⟧'), paper.document
    )

    assert found.valid, found.problems
    assert [f.signal for f in found.flags] == [Signal.OTHER]


def test_a_flag_under_a_continued_marker_is_its_block_s(paper) -> None:
    question = session(paper)
    text = flagged(
        question,
        '⟦? role-doubt "Hence the output" — a conclusion in a proof⟧',
        under="⟦b6 continued⟧",
    )

    found = check(question, text, paper.document)

    assert found.valid, found.problems
    assert [f.blocks for f in found.flags] == [(question.handles["b6"],)]


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ('⟦? split "We fix a field" — b2 words⟧', "are not b3's"),
        ('⟦? split "ratio of output" —⟧', "say what you read there"),
        ('⟦? splat "ratio of output" — a typo⟧', "'splat' is not a signal here"),
        ('⟦? two-block "ratio of output" — a groups signal⟧', "is not a signal"),
        ("⟦? split ratio of output — no quotes⟧", "is not a flag line"),
    ],
)
def test_a_bad_flag_is_refused(paper, line, problem) -> None:
    question = session(paper)

    found = check(question, flagged(question, line), paper.document)

    assert not found.valid
    assert any(problem in p for p in found.problems), found.problems


def test_a_flag_in_the_context_is_refused(paper) -> None:
    theorem = paper.cut.blocks[4].id
    question = touched_question(paper.document, paper.cut, [theorem], SESSION)
    text = shown_file(question).replace("⟦b1⟧", "⟦b1 claim⟧")
    text = text.replace("⟦context⟧", '⟦context⟧\n⟦? split "Results" — a heading⟧', 1)

    found = check(question, text, paper.document)

    assert any("stands under no block of this part" in p for p in found.problems)


def test_a_touched_question_shows_and_binds_a_later_step_s_notes(paper) -> None:
    theorem = paper.cut.blocks[4].id
    plain = touched_question(paper.document, paper.cut, [theorem], SESSION)

    noted = touched_question(
        paper.document, paper.cut, [theorem], SESSION, notes={theorem: "a definition"}
    )

    assert noted.key != plain.key
    assert "- b1: a definition" in noted.prompt
    assert noted.workspace == plain.workspace


def test_a_touched_question_shows_each_context_block_with_its_settled_roles(
    paper,
) -> None:
    """A chunk of one changed block: the definition touched, the Setting and the
    Results with their headings as context. Each context block has its own
    `⟦context⟧` line with its settled roles after it, so the theorem reads
    as a claim already; the brief says what context is, and the key binds
    that note.
    An answer that marks a part in the context is still refused."""
    roled = first_pass(paper)
    definition = paper.id_at(r"\begin{definition}")
    question = touched_question(paper.document, roled, [definition], SESSION)

    text = shown_file(question)
    theorem = roled.block(paper.id_at(r"\begin{theorem}"))
    assert (
        f"⟦context⟧ — roles: {', '.join(r.value for r in theorem.roles)}\n"
        + ("\\begin{theorem}")
        in text
    )
    assert "⟦context⟧ — roles: none\n\\section{Results}" in text
    assert CONTEXT_NOTE in question.workspace.instructions

    valid = text.replace("⟦b1⟧", "⟦b1 definition⟧")
    assert check(question, valid, paper.document).valid
    marked = valid.replace("\\begin{theorem}", "⟦+ claim⟧\n\\begin{theorem}", 1)
    assert any(
        "it lies in the context" in p
        for p in check(question, marked, paper.document).problems
    )
