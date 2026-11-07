"""The groups as a session on `groups.md`, per chunk: the file,
its key, its parse, its problems, and the gate the host runs on it."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.assembler import corrected_cut
from axiomation.cutter.model import GroupKind, Role, Segment
from axiomation.questions import Stage, gates, load_template
from axiomation.questions.groups_file import (
    FILE_TEMPLATE,
    GROUPS_FILE,
    KICKOFF,
    KIND,
    LINES_KICKOFF,
    SESSION_TEMPLATE,
    check,
    main,
    session_questions,
)
from axiomation.records import NONE, CutOp, Signal
from questions.review_paper import MAIN, make_paper
from questions.test_roles_file import first_pass

GOLDEN = Path(__file__).parent / "golden"
#: The file's brief: a person edits the file, and a session's lines are
#: laid into it, so the file is what these tests read.
SESSION = load_template(FILE_TEMPLATE)
LINES = load_template(SESSION_TEMPLATE)

START = "⟦group proof by_definition_the_gain⟧"
END = "⟦end group by_definition_the_gain⟧"


@pytest.fixture
def roled(paper):
    """The synthetic paper's cut with a roles pass applied: its one group,
    the proof (b6 to b8, the footnote b7 nested in b6)."""
    return first_pass(paper)


def session(paper, cut, budget=None, overlap=2):
    return session_questions(paper.document, cut, budget, SESSION, overlap)


def whole(paper, cut):
    (question,) = session(paper, cut)
    return question


def shown(question) -> str:
    return question.workspace.files[GROUPS_FILE]


def corrections(paper, question, text):
    found = check(question, text, paper.document)
    assert found.valid, found.problems
    return found.corrections


def problems(paper, question, text) -> tuple[str, ...]:
    found = check(question, text, paper.document)
    assert not found.valid
    return found.problems


def applied(paper, question, found):
    result = corrected_cut(
        paper.document,
        question.input_cut,
        [replace(c, id=str(n)) for n, c in enumerate(found, 1)],
    )
    assert result.problems == ()
    return result.cut


# --- the file and its key ----------------------------------------------------------


def test_one_session_for_a_paper_of_one_chunk(paper, roled) -> None:
    question = whole(paper, roled)

    assert question.stage is Stage.GROUPS and question.kind == KIND
    assert question.prompt == KICKOFF
    workspace = question.workspace
    assert workspace.edit == GROUPS_FILE and workspace.reask_from_edit
    assert workspace.instructions == SESSION.text
    assert workspace.gate.module == "axiomation.questions.groups_file"
    assert [h for h in question.handles if h.startswith("b")] == [
        f"b{n}" for n in range(1, 12)
    ]
    assert question.handles["g1"] == roled.groups[0].id


def test_a_session_reads_the_file_as_paper_md_and_answers_as_lines(
    paper, roled
) -> None:
    (question,) = session_questions(paper.document, roled, None, LINES, 2, lines=True)
    file = whole(paper, roled)

    workspace = question.workspace
    assert question.prompt == LINES_KICKOFF
    assert workspace.files == {"paper.md": shown(file), GROUPS_FILE: ""}
    assert workspace.edit == GROUPS_FILE and workspace.reask_from_edit
    assert workspace.instructions == LINES.text
    assert workspace.gate.module == "axiomation.questions.front_lines"
    assert workspace.tools == ("Bash", "Read", "Edit", "Write")
    assert workspace.brief_alone
    assert question.key != file.key
    # the proposed group kept as a line reads as the file left as shown
    kept = check(question, "group proof by_definition_the_gain b6 b8\n", paper.document)
    assert kept.valid and kept.corrections == ()
    assert kept.kept.split() == shown(file).split()  # white space aside
    moved = check(question, "group derivation proof_run b5 b8\n", paper.document)
    assert [c.op for c in moved.corrections] == [CutOp.UNGROUP, CutOp.GROUP]
    assert check(question, shown(file), paper.document).valid  # a kept file


def test_the_file_shows_the_roles_and_the_group_in_place(paper, roled) -> None:
    text = shown(whole(paper, roled))

    assert f"{START}\n\n⟦b6 justification⟧\n\\begin{{proof}}" in text
    assert "\\end{proof}\n\n" + END + "\n\n⟦b9 none⟧" in text
    assert "⟦b7 definition⟧" in text and "⟦+ claim⟧\nHence the output" in text


def test_the_file_matches_its_golden(paper, roled) -> None:
    path = GOLDEN / "groups-file.md"
    text = shown(whole(paper, roled))
    assert path.exists(), f"missing golden {path}; write it deliberately, reviewed"
    assert text == path.read_text(encoding="utf-8"), (
        "groups.md differs from its golden; review the change before updating it"
    )


def test_a_cut_without_groups_shows_no_group_line(paper, roled) -> None:
    text = shown(whole(paper, replace(roled, groups=())))

    assert "⟦group" not in text and "⟦end group" not in text


def test_chunks_follow_the_other_stages(paper, roled) -> None:
    """At 300 characters, the stage chunks as the roles stage does, block
    for block; the proof's group is shown in the one chunk that holds it."""
    from axiomation.questions.roles_file import session_questions as roles

    questions = session(paper, roled, budget=300)
    by_roles = roles(paper.document, roled, 300, load_template("roles-lines"))

    assert len(questions) > 1
    assert [dict(q.handles) for q in by_roles] == [
        {h: b for h, b in q.handles.items() if not h.startswith("g")} for q in questions
    ]
    assert len([q for q in questions if "⟦group proof " in shown(q)]) == 1


def test_a_group_a_chunk_edge_cuts_is_not_shown_and_stays(paper, roled) -> None:
    """One block per chunk: no chunk holds the whole proof, so no file shows
    it, and an unchanged answer corrects nothing."""
    questions = session(paper, roled, budget=0, overlap=0)

    assert all("⟦group" not in shown(q) for q in questions)
    for question in questions:
        assert corrections(paper, question, shown(question)) == ()


def test_the_key_binds_the_roles_the_groups_and_the_text(paper, roled, tmp_path):
    base = whole(paper, roled).key
    target = paper.id_at(r"\begin{theorem}")
    reroled = tuple(
        replace(b, roles=(Role.DEFINITION,), segments=(Segment("", Role.DEFINITION),))
        if b.id == target
        else b
        for b in roled.blocks
    )
    edited = make_paper(
        tmp_path / "edited",
        MAIN.replace("Next, the bound is sharp.", "Next, the bound is tight."),
    )

    assert whole(paper, roled).key == base
    assert whole(paper, replace(roled, blocks=reroled)).key != base
    assert whole(paper, replace(roled, groups=())).key != base
    assert whole(edited, first_pass(edited)).key != base


def test_the_key_ignores_what_a_group_names(paper, roled) -> None:
    """What a group justifies is an edge's: the file does not show it, and the key
    does not bind it."""
    group = replace(roled.groups[0], names=None)

    assert whole(paper, replace(roled, groups=(group,))).key == whole(paper, roled).key


# --- the answer ---------------------------------------------------------------------


def test_an_unchanged_file_is_no_correction(paper, roled) -> None:
    question = whole(paper, roled)

    assert corrections(paper, question, shown(question)) == ()


def test_deleting_both_lines_ungroups(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(START + "\n", "").replace(END + "\n", "")

    (ungroup,) = corrections(paper, question, text)

    assert ungroup.op is CutOp.UNGROUP and ungroup.group == roled.groups[0].id
    assert applied(paper, question, (ungroup,)).groups == ()


def test_new_lines_add_a_group_that_names_nothing(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(
        "⟦b10 none⟧", "⟦group derivation closing_derivation⟧\n\n⟦b10 none⟧"
    )
    text = text.rstrip("\n") + "\n\n⟦end group closing_derivation⟧\n"

    (group,) = corrections(paper, question, text)

    assert group.op is CutOp.GROUP and group.kind is GroupKind.DERIVATION
    assert (group.first, group.last) == (
        paper.id_at("\\section{More}", "extra.tex"),
        paper.id_at("A closing paragraph", "extra.tex"),
    )
    assert group.names == NONE
    assert len(applied(paper, question, (group,)).groups) == 2


def test_a_group_may_nest_inside_another(paper, roled) -> None:
    """A derivation over the proof's first paragraph and its footnote,
    inside the proof."""
    question = whole(paper, roled)
    text = shown(question).replace(
        f"{START}\n", f"{START}\n\n⟦group derivation first_step⟧\n"
    )
    text = text.replace("⟦b8 claim⟧", "⟦end group first_step⟧\n\n⟦b8 claim⟧")

    (group,) = corrections(paper, question, text)

    assert group.kind is GroupKind.DERIVATION
    assert (group.first, group.last) == (
        paper.id_at(r"\begin{proof}"),
        paper.id_at(r"\footnote"),
    )


def nested_in_an_outer_proof(question) -> str:
    """The file with a proof around the theorem, its proof and the closing
    words: two proofs, one inside the other."""
    text = shown(question).replace(
        "⟦b5 claim⟧", "⟦group proof outer_proof⟧\n\n⟦b5 claim⟧"
    )
    return text.replace(
        "Closing words of the results.\n",
        "Closing words of the results.\n\n⟦end group outer_proof⟧\n",
    )


def test_a_proof_nested_in_a_proof_pairs_by_name(paper, roled) -> None:
    """Two groups of one kind, one inside the other: each end names the
    group it ends."""
    question = whole(paper, roled)

    (group,) = corrections(paper, question, nested_in_an_outer_proof(question))

    assert group.kind is GroupKind.PROOF
    assert (group.first, group.last) == (
        paper.id_at(r"\begin{theorem}"),
        paper.id_at("Closing words"),
    )


def test_moving_an_end_regroups(paper, roled) -> None:
    """The proof ended after the footnote's block, where b6's text ends:
    b6 and the footnote nested in it, the last block the footnote."""
    question = whole(paper, roled)
    text = shown(question).replace(END + "\n", "")
    text = text.replace("⟦b8 claim⟧", END + "\n\n⟦b8 claim⟧")

    ungroup, group = corrections(paper, question, text)

    assert ungroup.op is CutOp.UNGROUP and group.op is CutOp.GROUP
    proof, footnote = paper.id_at(r"\begin{proof}"), paper.id_at(r"\footnote")
    assert (group.first, group.last) == (proof, footnote)
    (made,) = applied(paper, question, (ungroup, group)).groups
    assert made.blocks == (proof, footnote) and made.kind is GroupKind.PROOF


def test_changing_the_kind_regroups(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(
        START, "⟦group proof-section by_definition_the_gain⟧"
    )

    ungroup, group = corrections(paper, question, text)

    assert ungroup.op is CutOp.UNGROUP and group.kind is GroupKind.PROOF_SECTION
    (made,) = applied(paper, question, (ungroup, group)).groups
    assert made.blocks == roled.groups[0].blocks


def test_lines_elsewhere_do_not_move_a_group(paper, roled) -> None:
    """A group added before the proof shifts its lines down the file; the
    proof's group is still unchanged."""
    question = whole(paper, roled)
    text = shown(question).replace(
        "⟦b2 assumption⟧", "⟦group derivation setting_derivation⟧\n\n⟦b2 assumption⟧"
    )
    text = text.replace("⟦b4 none⟧", "⟦end group setting_derivation⟧\n\n⟦b4 none⟧")

    (group,) = corrections(paper, question, text)

    assert group.op is CutOp.GROUP and group.kind is GroupKind.DERIVATION


def test_a_group_of_one_block_is_refused(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(
        "⟦b9 none⟧", "⟦group derivation closing_words⟧\n\n⟦b9 none⟧"
    )
    text = text.replace(
        "Closing words of the results.\n",
        "Closing words of the results.\n\n⟦end group closing_words⟧\n",
    )

    (problem,) = problems(paper, question, text)

    assert problem == (
        "lines 46 to 51: ⟦group derivation closing_words⟧ holds one block, b9: a "
        "group holds two or more; one block is a node of its own, so remove the "
        "group"
    )


# --- what a refused edit is told ----------------------------------------------------


def test_a_start_inside_a_block_s_text_says_where_it_may_go(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(
        "Hence the output is bounded.",
        "⟦group derivation derivation_b6⟧\nHence the output is bounded.",
    )
    text = text.replace(END, "⟦end group derivation_b6⟧\n" + END)

    found = problems(paper, question, text)

    assert found[0].startswith(
        "line 38: ⟦group derivation derivation_b6⟧ is inside b6's text"
    )
    assert "such as ⟦b6 justification⟧ (line 27) or ⟦b8 claim⟧ (line 41)" in found[0]


def test_an_end_inside_a_block_s_text_says_where_it_may_go(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(END + "\n", "")
    text = text.replace("⟦+ claim⟧\nHence", END + "\n⟦+ claim⟧\nHence")

    found = problems(paper, question, text)

    assert found[0].startswith(f"line 37: {END} is inside b6's text, after “.”")
    assert "before ⟦b6 justification⟧ (line 27)" in found[0]
    assert "before ⟦b8 claim⟧ (line 41)" in found[0]
    assert found[1] == (
        f"line 25: {START} has no {END}: add it on its own line just after its "
        "last block's text"
    )


def test_a_group_may_not_hold_part_of_a_block(paper, roled) -> None:
    """Ending before the footnote's marker cuts b6, which continues."""
    question = whole(paper, roled)
    text = shown(question).replace(END + "\n", "")
    text = text.replace("⟦b7 definition⟧", END + "\n\n⟦b7 definition⟧")

    (problem,) = problems(paper, question, text)

    assert problem.startswith(f"lines 25 to 31: {START} holds only part of b6")


def test_an_end_of_another_kind_ends_no_group(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(END, "⟦end group derivation_b6⟧")

    found = problems(paper, question, text)

    assert found[0] == (
        f"line 44: ⟦end group derivation_b6⟧ ends no group: open here: "
        f"{START} (line 25)"
    )


def test_a_crossing_pair_is_told_to_nest_or_part(paper, roled) -> None:
    """A derivation that starts before the proof and ends inside it."""
    question = whole(paper, roled)
    text = shown(question).replace(
        "⟦b5 claim⟧", "⟦group derivation derivation_b5⟧\n\n⟦b5 claim⟧"
    )
    text = text.replace("⟦b8 claim⟧", "⟦end group derivation_b5⟧\n\n⟦b8 claim⟧")

    found = problems(paper, question, text)

    assert found == (
        f"line 27: {START} starts inside ⟦group derivation derivation_b5⟧ (lines 20 "
        "to 42) and ends after it: nest one inside the other, or make them "
        "disjoint",
    )


@pytest.mark.parametrize(
    ("line", "told"),
    [
        (
            "⟦group lemma lemma_b9⟧",
            "'lemma' is not a kind: proof, proof-section, derivation",
        ),
        ("⟦group step step_b9⟧", "'step' is not a kind"),
        ("⟦group proof⟧", "is not a group line: write ⟦group <kind> <name>⟧"),
        ("⟦group proof of b5⟧", "is not a group line"),
        ("⟦proof⟧", "is not a group line, and the other lines are fixed"),
    ],
)
def test_a_malformed_line_is_named(paper, roled, line, told) -> None:
    question = whole(paper, roled)
    text = shown(question).replace("⟦b9 none⟧", f"{line}\n\n⟦b9 none⟧")

    found = problems(paper, question, text)

    assert any(told in p for p in found), found


def test_a_changed_text_is_shown_where_it_changed(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace("Next, the bound is sharp.", "Next, it is sharp.")

    (problem,) = problems(paper, question, text)

    assert problem == (
        "line 41: the paper's text changed: it reads “Next, it is sharp.” where "
        "it read “Next, the bound is sharp.”; put the text back as it was"
    )


def test_a_changed_role_is_refused(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace("⟦b8 claim⟧", "⟦b8 justification⟧")

    (problem,) = problems(paper, question, text)

    assert problem == (
        "line 40: ⟦b8 justification⟧ was ⟦b8 claim⟧; the blocks' lines and their "
        "roles are fixed: put it back as it was"
    )


def test_a_removed_block_line_is_refused(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace("⟦b8 claim⟧\n", "")

    (problem,) = problems(paper, question, text)

    assert problem.startswith("⟦b8 claim⟧ is gone")
    assert "put it back just before “Next, the bound is sharp.”" in problem


def test_a_name_pairs_in_either_case_and_either_joiner(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(END, "⟦end group By-Definition-The-Gain⟧")

    assert corrections(paper, question, text) == ()


def test_two_groups_may_not_share_a_name(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(
        "⟦b9 none⟧", "⟦group derivation by_definition_the_gain⟧\n⟦b9 none⟧"
    )

    found = problems(paper, question, text)

    assert found[0] == (
        "lines 25 and 46: two groups are named by_definition_the_gain; give each "
        "its own name"
    )


def test_lines_deleted_from_a_nested_pair_do_not_pair_anew(paper, roled) -> None:
    """A proof inside a proof; deleting the outer start and the inner end
    leaves two lines that name different groups: refused, where plain
    brackets would have paired them into a group nobody marked."""
    question = whole(paper, roled)
    nested = nested_in_an_outer_proof(question)
    assert check(question, nested, paper.document).valid
    text = nested.replace("⟦group proof outer_proof⟧\n", "").replace(END + "\n", "")

    found = problems(paper, question, text)

    assert "⟦end group outer_proof⟧ ends no group: open here: " + START in found[0]
    assert f"{START} has no {END}" in found[1]


def test_two_groups_of_a_kind_may_not_start_at_one_block(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(START, f"⟦group proof another_proof⟧\n{START}")
    text = text.replace(END, f"{END}\n⟦end group another_proof⟧")

    found = problems(paper, question, text)

    assert any("two proof groups start at b6; keep one" in p for p in found), found


def test_a_group_with_no_block_is_refused(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(
        "⟦b9 none⟧",
        "⟦group derivation derivation_b9⟧\n⟦end group derivation_b9⟧\n\n⟦b9 none⟧",
    )

    found = problems(paper, question, text)

    assert any("holds no block" in p for p in found), found


def test_a_group_line_in_the_context_is_refused(paper, roled) -> None:
    """Where a chunk cuts inside a section, it shows the blocks around it
    as context: a group there is refused."""
    question = next(
        q for q in session(paper, roled, budget=200) if "⟦context⟧" in shown(q)
    )
    lines = shown(question).splitlines()
    at = lines.index("⟦context⟧") + 1
    while not lines[at].strip() or lines[at].startswith("#####"):
        at += 1
    lines[at : at + 1] = [
        "⟦group derivation derivation_b1⟧",
        lines[at],
        "⟦end group derivation_b1⟧",
    ]

    found = problems(paper, question, "\n".join(lines) + "\n")

    assert any("lies in the ⟦context⟧, shown for reading" in p for p in found), found


# --- the gate -----------------------------------------------------------------------


def gate(question, tmp_path: Path, text: str, monkeypatch, capsys) -> str:
    spec = tmp_path / "gate.json"
    spec.write_text(question.workspace.gate.spec, encoding="utf-8")
    edited = tmp_path / GROUPS_FILE
    edited.write_text(text, encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name": "Stop"}'))
    assert main([str(spec), str(edited)]) == 0
    return capsys.readouterr().out


def test_the_gate_is_silent_on_a_file_that_reads(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = whole(paper, roled)

    assert gate(question, tmp_path, shown(question), monkeypatch, capsys) == ""


def test_the_gate_blocks_with_the_check_s_problems(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(END, "⟦end group derivation_b6⟧")

    out = gate(question, tmp_path, text, monkeypatch, capsys)

    decision = json.loads(out)
    assert decision["decision"] == "block"
    for problem in problems(paper, question, text):
        assert f"- {problem}" in decision["reason"]
    assert decision["reason"].startswith(f"{GROUPS_FILE} does not read yet")


def test_the_gate_lets_the_session_end_after_its_rounds(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(END, "")

    outs = [
        gate(question, tmp_path, text, monkeypatch, capsys)
        for _ in range(gates.ROUNDS + 1)
    ]

    assert all(outs[: gates.ROUNDS]) and outs[gates.ROUNDS] == ""
    assert (tmp_path / gates.TRIES).read_text() == str(gates.ROUNDS)


def test_a_gate_that_cannot_run_lets_the_session_end(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert main([str(tmp_path / "missing.json"), str(tmp_path / GROUPS_FILE)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "could not run" in captured.err


# --- passes ------------------------------------------------------------------------


def test_a_later_pass_has_a_key_of_its_own_and_the_first_keeps_its_own(
    paper, roled
) -> None:
    first = whole(paper, roled)
    (again,) = session_questions(paper.document, roled, None, SESSION, 2, 1)
    (second,) = session_questions(paper.document, roled, None, SESSION, 2, 2)

    assert again.key == first.key
    assert second.key != first.key
    assert second.workspace.files == first.workspace.files


def test_the_first_pass_binds_no_pass_and_a_later_one_its_own(
    paper, roled, monkeypatch
) -> None:
    """The first pass's key inputs are as they were before passes, so every
    store recorded then replays."""
    from axiomation.questions import groups_file

    seen = []
    real = groups_file.question_key

    def capture(stage, template, inputs, kind):
        seen.append(inputs)
        return real(stage, template, inputs, kind)

    monkeypatch.setattr(groups_file, "question_key", capture)
    session_questions(paper.document, roled, None, SESSION, 2, 1)
    session_questions(paper.document, roled, None, SESSION, 2, 2)

    first, second = seen
    assert set(first) == {"blocks", "groups"}
    assert second["pass"] == 2


# --- an older cut's one-block group --------------------------------------------------


def with_a_one_block_group(paper, roled):
    """The cut with a one-block derivation at the closing words, as a cut
    made earlier may hold."""
    from axiomation.cutter.model import Group

    closing = paper.id_at("Closing words")
    single = Group(
        id=f"derivation@{closing}",
        kind=GroupKind.DERIVATION,
        blocks=(closing,),
        names=None,
        named_label=None,
        named_by=None,
        role=Role.JUSTIFICATION,
    )
    return replace(roled, groups=(*roled.groups, single)), single


def test_a_one_block_group_is_not_shown_and_every_answer_drops_it(paper, roled) -> None:
    cut, single = with_a_one_block_group(paper, roled)
    (question,) = session(paper, cut)

    assert shown(question) == shown(whole(paper, roled))
    assert question.key != whole(paper, roled).key
    (ungroup,) = corrections(paper, question, shown(question))
    assert (ungroup.op, ungroup.group) == (CutOp.UNGROUP, single.id)
    assert applied(paper, question, (ungroup,)).groups == roled.groups


def test_a_group_from_a_dropped_one_block_group_s_block_applies(paper, roled) -> None:
    """A derivation from the closing words over two blocks is not refused
    for the one-block derivation there, which the session cannot see."""
    cut, _ = with_a_one_block_group(paper, roled)
    (question,) = session(paper, cut)
    text = shown(question).replace(
        "⟦b9 none⟧", "⟦group derivation closing_run⟧\n\n⟦b9 none⟧"
    )
    text = text.replace(
        "A closing paragraph in another file.\n",
        "A closing paragraph in another file.\n\n⟦end group closing_run⟧\n",
    )

    found = corrections(paper, question, text)

    assert [c.op for c in found] == [CutOp.UNGROUP, CutOp.GROUP]
    assert len(applied(paper, question, found).groups) == 2


def test_a_gate_called_without_its_arguments_lets_the_session_end(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert main([]) == 0
    assert "could not run" in capsys.readouterr().err


# --- flags -----------------------------------------------------------

PROOF_MARKER = "⟦b6 justification⟧"


def test_a_flag_line_is_a_flag_and_changes_no_group(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(
        PROOF_MARKER,
        f'{PROOF_MARKER}\n⟦? run-unclear "the gain is small" — the proof may '
        "start earlier⟧",
        1,
    )

    found = check(question, text, paper.document)

    assert found.valid, found.problems
    assert found.corrections == ()
    (flag,) = found.flags
    assert flag.blocks == (question.handles["b6"],)
    assert flag.signal is Signal.RUN_UNCLEAR
    assert flag.evidence == ("the gain is small",)


def test_the_gate_blocks_a_flag_with_another_stage_s_signal(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(
        PROOF_MARKER, f'{PROOF_MARKER}\n⟦? split "We fix a field" — a roles signal⟧'
    )

    out = gate(question, tmp_path, text, monkeypatch, capsys)

    reason = json.loads(out)["reason"]
    assert "'split' is not a signal here" in reason
    assert any("is not a signal here" in p for p in problems(paper, question, text))


def test_a_flag_under_a_group_s_line_is_refused(paper, roled) -> None:
    question = whole(paper, roled)
    text = shown(question).replace(
        START, f'{START}\n⟦? run-unclear "the gain is small" — where it starts⟧', 1
    )

    found = problems(paper, question, text)

    assert any("stands under no block of this part" in p for p in found), found
