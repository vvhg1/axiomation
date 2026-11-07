"""A front stage answered as lines: the lines read against the blocks the
stage's file shows, and laid into it as the stage's check reads it.
Synthetic files, in words of our own."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiomation.questions import Stage, gates
from axiomation.questions import front_lines as fl

ROLES_FILE = "\n".join(
    [
        "##### main.md",
        "",
        "⟦b1⟧",
        "# Bounds",
        "",
        "⟦b2⟧",
        "The gain is below one. Hence the output is bounded.",
        "",
        "⟦b3⟧",
        "Proof. Since the gain is below one,",
        "the output never grows.",
        "",
    ]
)

#: A footnote (b3) inside a paragraph (b2), whose text resumes after it.
NESTED = "\n".join(
    [
        "##### main.md",
        "",
        "⟦b1⟧",
        "A loop is stable when its gain is small.",
        "",
        "⟦b2⟧",
        "The gain is below one",
        "",
        "⟦b3⟧",
        "Measured on the bench.",
        "",
        "⟦b2 continued⟧",
        "and so the output is bounded.",
        "",
        "⟦b4⟧",
        "We now turn to noise.",
        "",
    ]
)


def _laid(stage: Stage, shown: str, answer: str) -> fl.Laid:
    return fl.laid(stage, shown, answer)


def test_roles_lines_go_into_the_markers_with_a_part_where_its_words_start() -> None:
    answer = 'b1 none\nb2 assumption | claim "Hence the output"\nb3 justification\n'
    found = _laid(Stage.ROLES, ROLES_FILE, answer)
    assert found.problems == ()
    lines = found.text.split("\n")
    assert "⟦b1 none⟧" in lines and "⟦b3 justification⟧" in lines
    at = lines.index("⟦b2 assumption⟧")
    assert lines[at + 1 : at + 4] == [
        "The gain is below one.",
        "⟦+ claim⟧",
        "Hence the output is bounded.",
    ]


def test_a_roles_flag_stands_under_its_block_s_marker() -> None:
    answer = (
        "b1 none\nb2 claim\nb3 justification\n"
        'b2 ? split "Hence the output" — two results in one paragraph\n'
    )
    lines = _laid(Stage.ROLES, ROLES_FILE, answer).text.split("\n")
    at = lines.index("⟦b2 claim⟧")
    assert (
        lines[at + 1] == '⟦? split "Hence the output" — two results in one paragraph⟧'
    )


def test_a_roles_line_that_does_not_read_is_named_and_nothing_is_laid() -> None:
    answer = (
        'b1 none\nb2 claim | claim "nowhere here"\nb9 claim\n'
        'b2 ? split "The gain"\n'
        'b3 justification | claim "Proof. Since"\n'
    )
    found = _laid(Stage.ROLES, ROLES_FILE, answer)
    assert found.text == ROLES_FILE
    assert any("stand nowhere in b2" in p for p in found.problems)
    assert any("'b9 claim'" in p and "handle" in p for p in found.problems)
    assert any("a flag is" in p for p in found.problems)  # a flag needs its note
    assert any('"Proof. Since" start b3' in p for p in found.problems)
    assert "no roles line for b2" not in found.problems


def test_parts_stand_in_order_and_change_the_role() -> None:
    same = 'b1 none\nb2 claim | claim "Hence the output"\nb3 justification\n'
    found = _laid(Stage.ROLES, ROLES_FILE, same)
    assert any("the one before it" in p for p in found.problems)
    backwards = (
        "b1 none\nb3 justification\n"
        'b2 claim | none "Hence the" | assumption "is below one"\n'
    )
    found = _laid(Stage.ROLES, ROLES_FILE, backwards)
    assert any("the order they stand" in p for p in found.problems)


def test_a_block_s_text_resumed_after_a_nested_block_is_its_own() -> None:
    """The words after `⟦b2 continued⟧` are b2's, not the footnote's: a
    part quoted there is laid there."""
    answer = (
        "b1 claim\n"
        'b2 assumption | claim "and so the output"\n'
        "b3 justification\nb4 none\n"
    )
    found = _laid(Stage.ROLES, NESTED, answer)
    assert found.problems == ()
    lines = found.text.split("\n")
    at = lines.index("⟦b2 continued⟧")
    assert lines[at + 1 : at + 3] == ["⟦+ claim⟧", "and so the output is bounded."]
    wrong = 'b1 claim\nb2 assumption\nb3 justification | claim "and so the"\nb4 none\n'
    assert any(
        "stand nowhere in b3" in p for p in _laid(Stage.ROLES, NESTED, wrong).problems
    )


CUT = (
    ROLES_FILE.replace("⟦b1⟧", "⟦b1 none⟧ — form: heading")
    .replace("⟦b2⟧", "⟦b2 claim⟧ — form: prose")
    .replace("⟦b3⟧", "⟦b3 claim⟧ — form: prose")
)


def test_cut_lines_split_join_and_move() -> None:
    answer = 'split b2 "Hence the output"\nmove b3 "the output never"\n'
    found = _laid(Stage.BOUNDARIES, CUT, answer)
    assert found.problems == ()
    assert "⟦b3 claim⟧ — form: prose" not in found.text
    assert found.text.count(fl.NEW) == 2
    assert "Proof. Since the gain is below one,\n⟦new⟧\nthe output never grows." in (
        found.text
    )
    joined = _laid(Stage.BOUNDARIES, CUT, "join b3\n").text
    assert joined.count("⟦b") == 2


def test_a_move_reaches_back_into_the_block_before() -> None:
    found = _laid(Stage.BOUNDARIES, CUT, 'move b3 "Hence the output"\n')
    assert found.problems == ()
    lines = found.text.split("\n")
    assert lines[lines.index("The gain is below one.") + 1 : -1][:2] == [
        fl.NEW,
        "Hence the output is bounded.",
    ]


def test_cut_lines_that_do_not_read() -> None:
    found = _laid(
        Stage.BOUNDARIES,
        CUT,
        'join b1\nsplit b2 "The gain"\njoin b3 "Proof"\nsplit b7 "x"\n'
        'join b2\nmove b2 "Hence"\nshift b2\n',
    )
    assert found.problems == (
        "'join b1': no block of yours stands before b1",
        '\'split b2 "The gain"\': "The gain" start b2: no split there',
        "'join b3 \"Proof\"': `join <block>` joins a block to the one before it, "
        "and names no words",
        "'split b7 \"x\"': no block b7 in `paper.md`",
        "'move b2 \"Hence\"': b2 has a join line already",
        "'shift b2': a line is `split <block> \"<words>\"`, `join <block>` or "
        '`move <block> "<words>"`',
    )


def test_a_block_after_the_context_joins_nothing() -> None:
    shown = CUT.replace(
        "⟦b2 claim⟧ — form: prose", "⟦context⟧\n\n⟦b2 claim⟧ — form: prose"
    )
    shown = shown.replace(
        "⟦b1 none⟧ — form: heading\n# Bounds", "⟦b1 none⟧ — form: heading\n# Bounds\n"
    )
    found = _laid(
        Stage.BOUNDARIES,
        shown.replace("⟦context⟧\n", "⟦context⟧\nAn earlier line.\n"),
        "join b2\n",
    )
    assert found.problems == ("'join b2': no block of yours stands before b2",)


GROUPED = ROLES_FILE.replace("⟦b2⟧", "⟦group proof old⟧\n\n⟦b2⟧").replace(
    "⟦b3⟧", "⟦end group old⟧\n\n⟦b3⟧"
)


def test_groups_lines_replace_the_proposed_groups() -> None:
    answer = 'group proof bound_proof b2 b3\nb3 ? two-block "Proof. Since" — one step\n'
    found = _laid(Stage.GROUPS, GROUPED, answer)
    assert found.problems == ()
    lines = found.text.rstrip("\n").split("\n")
    assert "⟦group proof old⟧" not in lines and "⟦end group old⟧" not in lines
    assert lines[lines.index("⟦b2⟧") - 1] == "⟦group proof bound_proof⟧"
    assert lines[lines.index("⟦b3⟧") + 1] == '⟦? two-block "Proof. Since" — one step⟧'
    assert lines[-1] == "⟦end group bound_proof⟧"


def test_nested_groups_open_outer_first_and_close_inner_first() -> None:
    answer = "group proof outer b1 b3\ngroup derivation inner b1 b2\n"
    lines = _laid(Stage.GROUPS, ROLES_FILE, answer).text.split("\n")
    marked = [line for line in lines if line.startswith(("⟦group", "⟦end", "⟦b"))]
    assert marked == [
        "⟦group proof outer⟧",
        "⟦group derivation inner⟧",
        "⟦b1⟧",
        "⟦b2⟧",
        "⟦end group inner⟧",
        "⟦b3⟧",
        "⟦end group outer⟧",
    ]


def test_a_group_ends_after_its_last_block_s_resumed_text() -> None:
    lines = _laid(Stage.GROUPS, NESTED, "group proof run b2 b3\n").text.split("\n")
    assert lines.index("⟦end group run⟧") > lines.index("and so the output is bounded.")
    assert lines.index("⟦end group run⟧") < lines.index("⟦b4⟧")


def test_groups_lines_that_do_not_read() -> None:
    found = _laid(
        Stage.GROUPS,
        ROLES_FILE,
        "group proof one b2 b2\ngroup lemma l b1 b2\ngroup proof a b1 b2\n"
        "group proof A b2 b3\ngroup proof b b2 b3\n",
    )
    assert found.problems == (
        "'group proof one b2 b2': a group holds two or more blocks, b2 first",
        "'group lemma l b1 b2': a group's kind is one of proof, proof-section, "
        "derivation",
        "'group proof A b2 b3': another group is named A",
        "groups a and b partly overlap: nest one inside the other, or make them "
        "disjoint",
    )


def test_a_file_is_not_lines() -> None:
    """The file itself, its markers edited, is read as the file; lines are
    lines, a stray marker among them too, and so is a block's marker copied
    from the file: it is read, and named, as a line."""
    edited = ROLES_FILE.replace("⟦b1⟧", "⟦b1 none⟧")
    assert fl.is_file(edited, ROLES_FILE)
    assert not fl.is_file('b1 none\nb2 claim | claim "x"\n', ROLES_FILE)
    assert not fl.is_file("b1 none\n⟦+ claim⟧\nb2 claim\n", ROLES_FILE)
    spec = {fl.STAGE: "roles", fl.SHOWN: ROLES_FILE}
    assert fl.problems(spec, edited) == []
    assert not fl.is_file("b1 none\n⟦b2 claim⟧\nb3 claim\n", ROLES_FILE)
    assert fl.is_file(edited.replace("Hence", "Thus"), ROLES_FILE)
    assert fl.problems(spec, "b1 none\n⟦b2 claim⟧\nb3 claim\n")


def test_an_answer_that_says_nothing_is_named() -> None:
    """An empty file is no answer, nor one of headings alone: the session
    is told to write its lines or `none`, which then reads."""
    for stage, shown in (
        (Stage.ROLES, ROLES_FILE),
        (Stage.BOUNDARIES, CUT),
        (Stage.GROUPS, GROUPED),
    ):
        for answer in ("", "\n\n", "# Groups\n"):
            found = _laid(stage, shown, answer)
            assert found.text == shown
            assert found.problems == (
                f"{fl.ANSWER_FILES[stage]} says nothing: write your lines, "
                "or the one line `none`",
            )


def test_nothing_to_write_is_the_one_line_none() -> None:
    cut = _laid(Stage.BOUNDARIES, CUT, "none\n")
    assert cut.problems == () and cut.text == CUT
    groups = _laid(Stage.GROUPS, GROUPED, "none\n")
    assert groups.problems == () and "⟦group" not in groups.text
    assert _laid(Stage.GROUPS, GROUPED, "none\ngroup proof a b2 b3\n").problems


def test_quoted_words_start_a_word() -> None:
    found = _laid(
        Stage.ROLES, ROLES_FILE, 'b1 none\nb2 claim | none "ence the"\nb3 claim\n'
    )
    assert any('"ence the" stand nowhere' in p for p in found.problems)
    punctuated = ROLES_FILE.replace("Hence the output", "(Hence) the output")
    found = _laid(
        Stage.ROLES, punctuated, 'b1 none\nb2 claim | none "(Hence)"\nb3 claim\n'
    )
    assert found.problems == ()


def test_a_bar_inside_quoted_words_is_the_paper_s() -> None:
    shown = ROLES_FILE.replace("Hence the output", "Hence |x| the output")
    answer = 'b1 none\nb2 assumption | claim "Hence |x| the"\nb3 claim\n'
    found = _laid(Stage.ROLES, shown, answer)
    assert found.problems == ()
    assert "⟦+ claim⟧\nHence |x| the output is bounded." in found.text


def test_two_groups_on_one_run_are_refused() -> None:
    found = _laid(
        Stage.GROUPS, ROLES_FILE, "group proof a b1 b3\ngroup derivation b b1 b3\n"
    )
    assert found.problems == (
        "'group derivation b b1 b3': another group holds b1 to b3: keep one",
    )


#: A footnote (b3) at the end of a paragraph (b2), no text of b2 after it.
FOOTNOTE = "\n".join(
    [
        "##### main.md",
        "",
        "⟦b1 none⟧ — form: prose",
        "A loop is stable when its gain is small.",
        "",
        "⟦b2 claim⟧ — form: prose",
        "The gain is below one.",
        "",
        "⟦b3 none⟧ — form: footnote",
        "Measured on the bench.",
        "",
        "⟦b4 claim⟧ — form: prose",
        "So the output is bounded.",
        "",
    ]
)


def test_a_join_or_a_move_passes_over_a_nested_block() -> None:
    """b4 joins b2, the paragraph, not its footnote; words of the footnote
    are not where b4 can start."""
    joined = fl.laid(Stage.BOUNDARIES, FOOTNOTE, "join b4\n", nested=["b3"])
    assert joined.problems == ()
    moved = fl.laid(
        Stage.BOUNDARIES, FOOTNOTE, 'move b4 "on the bench"\n', nested=["b3"]
    )
    assert any("stand nowhere in b2 and b4" in p for p in moved.problems)
    inside = fl.laid(Stage.BOUNDARIES, FOOTNOTE, 'move b4 "below one"\n', nested=["b3"])
    assert inside.problems == ()


def _gate(tmp_path: Path, answer: str) -> list[str]:
    spec = tmp_path / "gate.json"
    spec.write_text(json.dumps({fl.STAGE: "roles", fl.SHOWN: ROLES_FILE}))
    edited = tmp_path / "roles.md"
    edited.write_text(answer)
    return [str(spec), str(edited)]


def test_the_gate_blocks_at_most_its_rounds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _gate(tmp_path, "b1 none\n")
    for _ in range(gates.ROUNDS):
        assert fl.main(args) == 0
        said = json.loads(capsys.readouterr().out)
        assert said["decision"] == "block"
        assert "roles.md does not read yet" in said["reason"]
        assert "no roles line for b2, b3" in said["reason"]
    assert fl.main(args) == 0
    assert capsys.readouterr().out == ""


def test_the_gate_lets_lines_that_read_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert fl.main(_gate(tmp_path, "b1 none\nb2 claim\nb3 justification\n")) == 0
    assert capsys.readouterr().out == ""
    logged = (tmp_path / "gate-checks.jsonl").read_text().splitlines()
    assert json.loads(logged[-1]) == {"mode": "stop", "problems": [], "blocked": False}


def test_the_gate_reads_and_writes_through_no_link(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A session can write beside the gate's files: an answer made a link
    to a closed file is not read, and a round count made a link to another
    file is not written through."""
    closed = tmp_path / "closed.md"
    closed.write_text("a key's lines\n")
    args = _gate(tmp_path, "")
    edited = Path(args[1])
    edited.unlink()
    edited.symlink_to(closed)
    assert fl.main(args) == 0
    said = capsys.readouterr()
    assert said.out == "" and "a key's lines" not in said.err
    edited.unlink()
    edited.write_text("b1 none\n")
    (tmp_path / gates.TRIES).symlink_to(closed)
    assert fl.main(args) == 0
    assert capsys.readouterr().out == ""  # it cannot count, so it lets go
    assert closed.read_text() == "a key's lines\n"
