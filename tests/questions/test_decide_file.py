"""The deciding step on `cut.md`: what it shows, its key, and
its edits read back with their evidence."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.cutter.model import Group, GroupKind
from axiomation.flags import Raised
from axiomation.questions import Stage, gates, load_template
from axiomation.questions.chunks import Chunk, chunks
from axiomation.questions.cut_file import CUT_FILE
from axiomation.questions.decide_file import (
    TEMPLATE,
    Shown,
    check,
    decide_questions,
    main,
)
from axiomation.records import CutOp, Flag, Signal
from questions.test_roles_file import first_pass

DECIDE = load_template(TEMPLATE)
DEFINITION = "main.tex:7:1"
B4 = "⟦b4 none⟧"
B5 = "⟦b5 claim⟧"
B6 = "⟦b6 claim, justification⟧"
B8 = "⟦b8 claim⟧"
B9 = "⟦b9 none⟧"
B11 = "⟦b11 claim⟧"


@pytest.fixture
def roled(paper):
    return first_pass(paper)


def flag() -> Raised:
    return Raised(
        Flag("f1", "k1", (DEFINITION,), Signal.SPLIT, ("ratio of output",), "two"),
        "roles",
        None,  # type: ignore[arg-type] - the cut is the export's, not shown
    )


def ask(paper, cut, flags=(), chunk=None):
    found = [chunk] if chunk else chunks(paper.document, cut, None, 2)
    (question,) = decide_questions(paper.document, cut, found, DECIDE, flags)
    return question


def shown(question) -> str:
    return question.workspace.files[CUT_FILE]


def marker_line(text: str, marker: str) -> str:
    """The whole marker line that starts with `marker`."""
    return next(line for line in text.splitlines() if line.startswith(marker))


def joined(text: str, marker: str, line: str) -> str:
    return text.replace(marker_line(text, marker), line, 1)


def corrections(paper, question, text):
    found = check(question, text, paper.document)
    assert found.valid, found.problems
    return found.corrections


def problems(paper, question, text):
    found = check(question, text, paper.document)
    assert not found.valid
    return found.problems


# --- what it shows --------------------------------------------------------------------


def test_it_shows_roles_parts_groups_and_flags(paper, roled) -> None:
    question = ask(paper, roled, [flag()])
    text = shown(question)

    assert question.stage is Stage.DECIDE
    assert question.workspace.instructions == DECIDE.render(
        outline_intro="", outline_note=""
    )
    assert B6 in text and "⟦+ claim⟧" in text
    assert "⟦group proof by_definition_the_gain⟧" in text
    assert "⟦end group by_definition_the_gain⟧" in text
    assert '⟦? roles: split · "ratio of output" — two⟧' in text
    # The flag stands at the start of its block's text.
    assert (
        text.index("⟦b3 definition⟧")
        < text.index("⟦? roles")
        < text.index("\\begin{definition}")
    )


def test_an_unchanged_file_decides_nothing(paper, roled) -> None:
    question = ask(paper, roled, [flag()])

    assert corrections(paper, question, shown(question)) == ()


def test_the_key_binds_the_flags_and_the_groups(paper, roled) -> None:
    plain = ask(paper, roled)
    flagged = ask(paper, roled, [flag()])
    ungrouped = ask(paper, replace(roled, groups=()))

    assert len({plain.key, flagged.key, ungrouped.key}) == 3


def test_a_touched_run_with_no_flags_is_asked_on_its_one_block(paper, roled) -> None:
    blocks = [b.id for b in roled.blocks]
    chunk = Chunk(tuple(blocks[4:5]), tuple(blocks[2:4]), tuple(blocks[5:6]))

    question = ask(paper, roled, chunk=chunk)

    text = shown(question)
    assert [h for h in question.handles if h.startswith("b")] == ["b1"]
    assert text.count("⟦context⟧") == 2 and "⟦b1 claim⟧" in text
    assert corrections(paper, question, text) == ()


# --- its edits -----------------------------------------------------------------------


def test_a_split_carries_its_words_and_its_reason(paper, roled) -> None:
    question = ask(paper, roled)
    text = shown(question).replace(
        "Hence the output", "⟦new — the conclusion is a claim⟧\nHence the output", 1
    )

    (split,) = corrections(paper, question, text)

    assert split.op is CutOp.SPLIT and split.block == question.handles["b6"]
    assert split.at == split.evidence == ("Hence the output",)
    assert split.why == "the conclusion is a claim"


def test_a_join_quotes_both_blocks_and_gives_a_reason(paper, roled) -> None:
    question = ask(paper, roled)
    text = joined(
        shown(question), B11, '⟦join · "More" "A closing  paragraph" — one unit⟧'
    )

    (merge,) = corrections(paper, question, text)

    assert merge.op is CutOp.MERGE
    assert merge.blocks == (question.handles["b10"], question.handles["b11"])
    assert merge.evidence == ("More", "A closing paragraph")
    assert merge.why == "one unit" and merge.decision is None


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ('⟦join · "Results" "The output" —⟧', "say why the blocks are one verdict"),
        (
            '⟦join · "Setting" "The output" — x⟧',
            "the first words in quotes are not b4's",
        ),
        ('⟦join · "Results" "Setting" — x⟧', "the second words in quotes are not b5's"),
        ('⟦join · "Results" — x⟧', "is not a join line"),
    ],
)
def test_a_join_without_its_evidence_is_refused(paper, roled, line, problem) -> None:
    question = ask(paper, roled)

    found = problems(paper, question, joined(shown(question), B5, line))

    assert any(problem in p for p in found), found


def test_a_deleted_marker_without_a_join_line_is_refused(paper, roled) -> None:
    question = ask(paper, roled)
    text = shown(question).replace(marker_line(shown(question), B5) + "\n", "", 1)

    found = problems(paper, question, text)

    assert any("put a join line in its place" in p for p in found), found


def test_a_join_line_where_no_marker_was_is_refused(paper, roled) -> None:
    question = ask(paper, roled)
    text = shown(question).replace(
        "\\end{theorem}", '⟦join · "Results" "The output" — x⟧\n\\end{theorem}', 1
    )

    found = problems(paper, question, text)

    assert any("stands where no block's marker was" in p for p in found), found


def test_a_join_across_a_group_s_edge_is_refused(paper, roled) -> None:
    question = ask(paper, roled)
    text = joined(
        shown(question), B9, '⟦join · "the bound is sharp" "Closing words" — x⟧'
    )

    found = problems(paper, question, text)

    assert any("crosses the edge of a group" in p for p in found), found


def test_a_join_of_a_whole_group_is_a_decision(paper, roled) -> None:
    blocks = [b.id for b in roled.blocks]
    pair = Group(
        f"derivation@{blocks[9]}",
        GroupKind.DERIVATION,
        (blocks[9], blocks[10]),
        None,
        None,
        None,
        None,
    )
    cut = replace(roled, groups=(*roled.groups, pair))
    question = ask(paper, cut)
    text = joined(shown(question), B11, '⟦join · "More" "A closing" — one statement⟧')

    ungroup, merge = corrections(paper, question, text)

    assert ungroup.op is CutOp.UNGROUP and ungroup.group == pair.id
    assert merge.op is CutOp.MERGE and merge.blocks == (blocks[9], blocks[10])
    assert ungroup.decision == merge.decision == "d1"


def test_a_moved_marker_is_a_move_with_its_words_and_reason(paper, roled) -> None:
    question = ask(paper, roled)
    text = shown(question)
    line = marker_line(text, B11)
    text = text.replace(line + "\n", "", 1).replace(
        "in another file.", "\n⟦b11 claim — the claim starts here⟧\nin another file.", 1
    )

    merge, split = corrections(paper, question, text)

    assert merge.op is CutOp.MERGE and merge.blocks == (
        question.handles["b10"],
        question.handles["b11"],
    )
    assert split.op is CutOp.SPLIT and split.evidence == split.at
    assert merge.evidence == split.at
    assert merge.why == split.why == "the claim starts here"
    assert merge.decision == split.decision == "d1"


@pytest.mark.parametrize("where", ["past the end", "onto the next block's start"])
def test_a_marker_moved_where_no_block_starts_is_refused(paper, roled, where) -> None:
    """A move that lands nowhere would be a join without its words:
    refused."""
    question = ask(paper, roled)
    text = shown(question)
    if where == "past the end":
        line = marker_line(text, B11)
        text = text.replace(line + "\n", "", 1) + "⟦b11 claim — moved⟧\n"
    else:
        line = marker_line(text, "⟦b10 none⟧")
        text = text.replace(line + "\n", "", 1).replace(
            marker_line(text, B11), "⟦b10 none — moved⟧\n" + marker_line(text, B11), 1
        )

    found = problems(paper, question, text)

    assert any("moved where no block can start" in p for p in found), found


@pytest.mark.parametrize("where", ["forward", "backward"])
def test_a_marker_moved_past_another_marker_is_refused(paper, roled, where) -> None:
    """A move stays within the two blocks it moves between: past a
    neighbour's marker its words would be a third block's, and its merge
    and split two decisions."""
    question = ask(paper, roled)
    text = shown(question)
    if where == "forward":  # b2's marker into b3's text
        line = marker_line(text, "⟦b2 assumption⟧")
        text = text.replace(line + "\n", "", 1).replace(
            "The gain is the ratio", "⟦b2 assumption — x⟧\nThe gain is the ratio", 1
        )
    else:  # b4's marker into b2's text
        line = marker_line(text, B4)
        text = text.replace(line + "\n", "", 1).replace(
            "A second sentence", "⟦b4 none — x⟧\nA second sentence", 1
        )

    found = problems(paper, question, text)

    assert any("moved past another block's marker" in p for p in found), found


def test_a_join_that_moves_the_start_of_a_named_block_is_refused(paper, roled) -> None:
    """The proof's group names the theorem (b5): joining it to its heading
    would move the start the group names, which the assembler refuses."""
    question = ask(paper, roled)
    text = joined(shown(question), B5, '⟦join · "Results" "The output" — one unit⟧')

    found = problems(paper, question, text)

    assert any("moves the start of b5, which a group names" in p for p in found)


def test_a_join_across_two_files_is_refused(paper, roled) -> None:
    question = ask(paper, roled)
    text = joined(
        shown(question), "⟦b10 none⟧", '⟦join · "Closing words" "More" — one unit⟧'
    )

    found = problems(paper, question, text)

    assert any("crosses a '##### <file>' line" in p for p in found), found


def test_a_shown_line_moved_is_refused(paper, roled) -> None:
    """A moved a group's end line: read as nothing, so told."""
    question = ask(paper, roled)
    text = shown(question)
    end = "⟦end group by_definition_the_gain⟧\n"
    text = text.replace(end, "", 1).replace("Next, the bound", end + "Next, the bound")

    found = problems(paper, question, text)

    assert any("are shown, not read" in p for p in found), found


def test_a_malformed_join_line_is_reported_not_dropped(paper, roled) -> None:
    """Read as a new marker, it would put the deleted one back and the
    join would vanish unseen."""
    question = ask(paper, roled)
    line = '⟦Join "the bound is sharp" "Closing words"⟧'

    found = problems(paper, question, joined(shown(question), B9, line))

    assert any("is not a join line" in p for p in found), found


def test_a_join_line_without_a_space_after_join_is_read(paper, roled) -> None:
    question = ask(paper, roled)
    text = joined(shown(question), B11, '⟦join·"More" "A closing" — one unit⟧')

    (merge,) = corrections(paper, question, text)

    assert merge.op is CutOp.MERGE and merge.evidence == ("More", "A closing")


def test_every_boundary_problem_is_reported_at_once(paper, roled) -> None:
    """A fixed line removed and the footnote's marker
    deleted, both reported in one round."""
    question = ask(paper, roled)
    text = shown(question).replace("⟦b6 continued⟧\n", "", 1)
    text = text.replace(marker_line(text, "⟦b7 definition⟧") + "\n", "", 1)

    found = problems(paper, question, text)

    assert any("these lines are fixed" in p for p in found), found
    assert any("never joined" in p for p in found), found


# --- the gate ------------------------------------------------------------------


def gate(question, tmp_path: Path, text: str, monkeypatch, capsys) -> str:
    spec = tmp_path / "gate.json"
    spec.write_text(question.workspace.gate.spec, encoding="utf-8")
    edited = tmp_path / CUT_FILE
    edited.write_text(text, encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO('{"hook_event_name": "Stop"}'))
    assert main([str(spec), str(edited)]) == 0
    return capsys.readouterr().out


def blocked(question, tmp_path, text, monkeypatch, capsys) -> str:
    decision = json.loads(gate(question, tmp_path, text, monkeypatch, capsys))
    assert decision["decision"] == "block"
    assert decision["reason"].startswith(f"{CUT_FILE} does not read yet")
    return decision["reason"]


def test_the_session_has_a_gate_whose_spec_is_what_it_shows(paper, roled) -> None:
    question = ask(paper, roled)
    shown_ = Shown.of(question, paper.document)

    assert question.workspace.gate.module == "axiomation.questions.decide_file"
    assert Shown.from_spec(question.workspace.gate.spec) == shown_
    assert shown_.top == tuple(f"b{n}" for n in range(1, 12) if n != 7)
    assert shown_.groups == (("b6", "b7", "b8"),)


def test_the_gate_is_silent_on_a_file_that_reads(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = ask(paper, roled, [flag()])
    text = joined(
        shown(question), B11, '⟦join · "More" "A closing  paragraph" — one unit⟧'
    )

    assert gate(question, tmp_path, shown(question), monkeypatch, capsys) == ""
    assert gate(question, tmp_path, text, monkeypatch, capsys) == ""


def test_the_gate_refuses_a_removed_continued_line(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    """A fixed line removed, or a group's end line moved."""
    question = ask(paper, roled)
    text = shown(question).replace("⟦b6 continued⟧\n", "", 1)

    reason = blocked(question, tmp_path, text, monkeypatch, capsys)

    assert "these lines are fixed" in reason
    assert any("these lines are fixed" in p for p in problems(paper, question, text))


def test_the_gate_refuses_a_join_of_a_footnote(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    """A nested block is never joined."""
    question = ask(paper, roled)
    text = joined(
        shown(question),
        "⟦b7 definition⟧",
        '⟦join · "the gain is small" "Small means below one." — one unit⟧',
    )

    reason = blocked(question, tmp_path, text, monkeypatch, capsys)

    assert "a nested block is never joined" in reason
    assert any("never joined" in p for p in problems(paper, question, text))


@pytest.mark.parametrize(
    "line",
    [
        '⟦join · "Results" "The output" —⟧',
        '⟦join · "Setting" "The output" — x⟧',
        '⟦join · "Results" — x⟧',
    ],
)
def test_the_gate_gives_the_check_s_problems(
    paper, roled, tmp_path, monkeypatch, capsys, line
) -> None:
    question = ask(paper, roled)
    text = joined(shown(question), B5, line)

    reason = blocked(question, tmp_path, text, monkeypatch, capsys)

    for problem in problems(paper, question, text):
        assert f"- {problem}" in reason


def test_the_gate_refuses_a_join_across_a_group_s_edge(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = ask(paper, roled)
    text = joined(
        shown(question), B9, '⟦join · "the bound is sharp" "Closing words" — x⟧'
    )

    assert "crosses the edge of a group" in blocked(
        question, tmp_path, text, monkeypatch, capsys
    )


def test_the_gate_refuses_a_marker_moved_past_the_end(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = ask(paper, roled)
    text = shown(question)
    text = text.replace(marker_line(text, B11) + "\n", "", 1) + "⟦b11 claim — x⟧\n"

    assert "moved where no block can start" in blocked(
        question, tmp_path, text, monkeypatch, capsys
    )


def test_the_gate_lets_the_session_end_after_its_rounds(
    paper, roled, tmp_path, monkeypatch, capsys
) -> None:
    question = ask(paper, roled)
    text = shown(question).replace("⟦b6 continued⟧\n", "", 1)

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

    assert main([str(tmp_path / "missing.json"), str(tmp_path / CUT_FILE)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "could not run" in captured.err


# --- the pins ---------------------------------------------------------------------


def test_the_deciding_step_s_question_is_pinned(paper) -> None:
    """Protects recorded answers' replays: the deciding step's
    key, cut.md and gate spec, with no flags, stay byte-equal, so an edit
    that moves them fails here."""
    (decided,) = decide_questions(
        paper.document, paper.cut, chunks(paper.document, paper.cut, None, 2), DECIDE
    )

    def sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    assert (
        decided.key
        == "32a67eda20614c8b7d5165a444f31f03b269418a08116a146f6deb7767daa566"
    )
    assert (
        sha(shown(decided))
        == "906158b92e34168d860ee9f584511669d2d6412773edbb1957baa4fdaf191f2d"
    )
    assert (
        sha(decided.workspace.gate.spec)
        == "44b9f25c9a2c7934c947338ff21661bdcc9c7ddeca10bb2646a2672ccb7f3a05"
    )


def test_the_deciding_step_s_template_is_unchanged() -> None:
    """The deciding step's key, and the replays of answers recorded on it,
    rest on its template's version."""
    assert DECIDE.version == "a9dee23927ff"
    assert hashlib.sha256(DECIDE.text.encode()).hexdigest()[:12] == DECIDE.version
