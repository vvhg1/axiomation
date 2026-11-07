"""An edit on a kept state: a person's or an agent's
answer to a stage's file as the kept state shows it, applied by the person
rounds' own step and kept as a state at once, with no replay. On the
synthetic paper, built by a fake host and kept as a state; no model is
called.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from axiomation import develop, driver, edits, run_state, versions
from axiomation.changes import Layer
from axiomation.cli import BUILD_STAGES, main
from axiomation.commands import Exit
from axiomation.cutter import CutRules, DisplayRule, cut
from axiomation.driver import ReviewSettings, Templates, map_vocabulary, review_cut
from axiomation.edits import A_PERSON, EditError, agent
from axiomation.exchange import cut_from_payload, dumps, vocabulary_of
from axiomation.queries import View, changes_report, open_report
from axiomation.questions import Stage, edges_file, vocabulary_file
from axiomation.questions import edge_sheet as es
from axiomation.reader import read_paper
from axiomation.reader.includes import STORE_DIRECTORY
from axiomation.records import AGENT, IDENTITY, PERSON, RECORD, RecordStore
from axiomation.resolver import Resolver
from axiomation.runner import Runner
from axiomation.update import UpdateSettings
from axiomation.update import run as run_update
from axiomation.versions import Command
from questions.review_paper import EXTRA, make_paper
from questions.test_edges_file import done
from runner.fakes import FakeHost
from test_driver import build_host
from test_person import (
    CLOSING,
    CLOSING_CLAIM,
    DEFINITION,
    DROPPED,
    SPLIT,
    WRITTEN,
    WROTE,
    cli_build,
    edited_edges,
)
from test_person import host as person_host
from test_queries import _updated
from test_update import FILES, FLAT, SETTING, SHARP, answering, stepping

#: An edge the build did not write: the closing words use the definition.
ADDED = '→ uses def:gain · "Closing words of the results."'
#: A line of the proof's block, whose footnote edge a person deleted.
SELF_MARK = '→ self · "Hence"'
#: The closing words' line the build wrote, after which an edit adds one.
SCOPE = '→ about scope:paper · "Closing words of the results."'


def adding(line: str):
    return lambda text: text.replace(SCOPE, f"{SCOPE}\n{line}")


def built_state(
    tmp_path: Path, capsys, lines: dict[str, list[str]] | None = None
) -> tuple[RecordStore, list[str]]:
    """The synthetic paper built (its edges `lines`, the person tests' by
    default), then kept as a state by a replay."""
    b, args = cli_build(tmp_path) if lines is None else cli_build(tmp_path, lines)
    capsys.readouterr()
    assert main(["build", "--json", *args, "--host", "none"]) == 0
    capsys.readouterr()
    return b.store, args


def test_a_build_that_keeps_its_state_leaves_no_run(tmp_path, capsys) -> None:
    """The working state is the run's: once the whole state is kept, it
    is gone (`docs/architecture.md` § 4)."""
    store, _ = built_state(tmp_path, capsys)

    assert versions.latest(store) is not None
    assert not run_state.path(store.directory, "build").exists()


def test_a_build_of_a_built_version_asks_nothing_and_returns_its_state(
    tmp_path, capsys
) -> None:
    """A whole state is never replaced because a build ran again, under
    the same settings or others (`docs/architecture.md` § 4)."""
    store, args = built_state(tmp_path, capsys)
    kept = versions.latest(store)
    assert kept.made_from is not None
    out = tmp_path / "again.json"

    assert main(["build", "--json", *args, "--host", "none", "-o", str(out)]) == 0
    said = capsys.readouterr()
    assert json.loads(said.out) == {
        "built": True,
        "state": {"id": kept.id, "parent": None},
    }
    assert said.err == ""
    assert out.read_bytes() == versions.kept_export(store, kept)
    assert versions.states(store) == (kept,)

    assert main(["build", "--json", *args, "--host", "none", "--edge-passes", "2"]) == 0
    said = capsys.readouterr()
    assert json.loads(said.out)["state"]["id"] == kept.id
    assert "made under other settings, and stands" in said.err
    assert versions.states(store) == (kept,)


def test_an_edit_is_applied_from_the_text_it_was_given(
    tmp_path, capsys, monkeypatch
) -> None:
    """The step reads the edit as given, never its text back from the
    store (`docs/architecture.md` § 4)."""
    store, _ = built_state(tmp_path, capsys)

    def unread(answer, run):
        if answer.host == "person":
            raise AssertionError("a given answer's text read from the store")
        return answer.raw

    monkeypatch.setattr(driver, "text_of", unread)
    given = give(store, edited_edges)

    assert not given.problems and given.state is not None


@pytest.mark.usefixtures("develop_mode")
def test_replay_is_develop_mode_and_asks_nothing(tmp_path, capsys) -> None:
    store, args = built_state(tmp_path, capsys)
    kept = versions.latest(store)
    out = tmp_path / "replay.json"

    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, kept)
    assert versions.states(store) == (kept,)
    assert main(["build", "--json", *args, "--host", "claude-code", "--replay"]) != 0
    assert "--replay asks nothing" in capsys.readouterr().err


def test_replay_is_refused_outside_develop_mode(tmp_path, capsys) -> None:
    """A replay reads the filed answers' texts again, which only develop
    mode keeps: outside it, it is refused before it reads anything."""
    store, args = built_state(tmp_path, capsys)
    capsys.readouterr()

    assert main(["build", "--json", *args, "--host", "none", "--replay"]) == 1
    assert develop.ENV in capsys.readouterr().err
    assert len(versions.states(store)) == 1


def latest_export(store: RecordStore) -> bytes:
    found = versions.latest(store)
    assert found is not None
    return versions.kept_export(store, found)


def give(store: RecordStore, edit, author=A_PERSON, reason=None) -> edits.Given:
    with edits.opened(store, versions.latest(store)) as kept:
        edited = edit(edits.shown(kept, Layer.EDGES))
        return edits.give(kept, Layer.EDGES, edited, author, reason)


def replayed(args: list[str], capsys) -> int:
    capsys.readouterr()
    code = main(["build", "--json", *args, "--host", "none", "--replay"])
    capsys.readouterr()
    return code


# --- the rule: a state is what the store's answers give --------------------------


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_edges_edit_makes_the_state_a_replay_gives(tmp_path, capsys) -> None:
    store, args = built_state(tmp_path, capsys)
    before = versions.latest(store)

    given = give(store, edited_edges)

    assert not given.problems and given.state is not None
    made = given.state
    assert (made.command, made.parent, made.version) == (
        Command.EDIT,
        before.id,
        before.version,
    )
    assert made.answers == (*before.answers, given.recorded.answer.key)
    export = json.loads(versions.kept_export(store, made))
    assert export["edges_answer"] == given.recorded.answer.key
    # only the line the person wrote is theirs
    assert [
        e["provenance"]["by"]
        for e in export["edges"]
        if e["provenance"]["question"] == given.recorded.answer.key
    ] == ["person"]
    assert [(p["kind"], p["line"]) for p in export["review"]["person"]["pins"]] == [
        ("deleted", DROPPED),
        ("written", WROTE),
    ]
    # the state before stays readable as it was
    assert versions.kept_export(store, before) == versions.kept_export(
        store, versions.state(store, before.id)
    )

    kept = versions.states(store)
    assert replayed(args, capsys) == 0
    assert versions.states(store) == kept  # the replay adds no state
    out = tmp_path / "replay.json"
    main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
    assert out.read_bytes() == versions.kept_export(store, made)


@pytest.mark.usefixtures("develop_mode")
def test_an_agent_s_edit_is_recorded_as_its_pins_nothing_and_replays(
    tmp_path, capsys
) -> None:
    store, args = built_state(tmp_path, capsys)

    given = give(
        store,
        adding(ADDED),
        agent("claude-opus-5-5", "s-1"),
        "the closing words name the gain",
    )

    assert not given.problems, given.problems
    answer = given.recorded.answer
    assert (answer.host, answer.model, answer.session, answer.reason) == (
        AGENT,
        "claude-opus-5-5",
        "s-1",
        "the closing words name the gain",
    )
    assert answer.version == given.state.version
    export = json.loads(versions.kept_export(store, given.state))
    assert export["review"]["person"]["pins"] == []
    assert export["review"]["person"]["applied"] == [
        {"key": answer.key, "stage": "edges", "round": 1, "by": AGENT}
    ]
    assert (
        len(export["edges"])
        == len(json.loads(latest_export_of(store, given.state.parent))["edges"]) + 1
    )
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


def test_an_agent_cannot_change_a_person_s_pin(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    assert give(store, edited_edges).state is not None
    answers = (store.directory / RECORD).read_text(encoding="utf-8")

    for edit in (
        lambda t: t.replace(WROTE + "\n", ""),
        lambda t: t.replace(SELF_MARK, f"{SELF_MARK}\n{DROPPED}"),
    ):
        given = give(store, edit, agent("m", "s"))
        assert given.state is None and given.problems
        assert any("pinned" in p for p in given.problems), given.problems
    assert (store.directory / RECORD).read_text(encoding="utf-8") == answers


@pytest.mark.usefixtures("develop_mode")
def test_a_person_s_keep_pins_a_line_as_it_stands(tmp_path, capsys) -> None:
    store, args = built_state(tmp_path, capsys)

    given = give(store, lambda t: t.replace(DROPPED, "= " + DROPPED), reason="read")

    assert given.state is not None, given.problems
    export = json.loads(versions.kept_export(store, given.state))
    assert [(p["kind"], p["line"]) for p in export["review"]["person"]["pins"]] == [
        ("kept", DROPPED)
    ]
    # the edge stands as it stood
    before = json.loads(latest_export_of(store, given.state.parent))
    assert len(export["edges"]) == len(before["edges"])
    # an agent may neither take it off nor keep a line itself
    took = give(store, lambda t: t.replace(DROPPED + "\n", ""), agent("m", "s"))
    assert took.state is None and any("pinned" in p for p in took.problems)
    keeps = give(store, lambda t: t.replace(SCOPE, "= " + SCOPE), agent("m", "s"))
    assert keeps.state is None and any("keep mark" in p for p in keeps.problems)
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


def latest_export_of(store: RecordStore, state_id: str) -> bytes:
    return versions.kept_export(store, versions.state(store, state_id))


@pytest.mark.usefixtures("develop_mode")
def test_a_standing_on_a_block_with_no_claim_adds_the_role_in_the_state(
    tmp_path, capsys
) -> None:
    store, args = built_state(tmp_path, capsys)
    given = give(store, adding(CLOSING_CLAIM))

    assert given.state is not None, given.problems
    export = json.loads(versions.kept_export(store, given.state))
    (entry,) = export["review"]["cut_corrections"]
    assert (entry["op"], entry["by"], entry["applied"]) == ("roles", PERSON, True)
    closing = next(b for b in export["blocks"] if b["id"] == entry["block"])
    assert closing["roles"] == ["claim"]
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


def test_an_edit_is_refused_when_malformed_and_records_nothing(
    tmp_path, capsys
) -> None:
    store, _ = built_state(tmp_path, capsys)
    answers = (store.directory / RECORD).read_text(encoding="utf-8")
    for edit in (
        adding(ADDED.replace("Closing", "Opening")),
        adding(ADDED.replace("def:gain", "nowhere")),
    ):
        given = give(store, edit)
        assert given.state is None and given.problems
    assert (store.directory / RECORD).read_text(encoding="utf-8") == answers
    with pytest.raises(EditError, match="nothing to record"):
        give(store, lambda t: t)


def test_an_edit_replays_nothing(tmp_path, capsys, monkeypatch) -> None:
    """After a brief changed, a replay asks again; an edit reads the kept
    state alone."""
    store, _ = built_state(tmp_path, capsys)

    def no_replay(*_args, **_kwargs):
        raise AssertionError("an edit replays nothing")

    monkeypatch.setattr("axiomation.driver.review_cut", no_replay)
    monkeypatch.setattr("axiomation.commands.build.review_cut", no_replay)
    assert give(store, edited_edges).state is not None


def updated_state(tmp_path: Path, capsys) -> tuple[Path, RecordStore]:
    """The synthetic paper built, edited in place and updated, both runs
    kept as states in the paper's own store."""
    root, directory, _ = _updated(tmp_path, capsys)
    store = RecordStore(directory)
    assert main(["update", "--json", str(root), "--host", "none"]) == 5
    (key,) = [a.key for a in store.authoritative().values() if a.kind == "identity"]
    answered = run_update(
        read_paper(root),
        store,
        UpdateSettings(
            files=FILES,
            review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
            paper=root.resolve(),
        ),
        Runner(
            stepping(answering({"we-fix-a-field": ["Δ reworded — a clause added"]})),
            Resolver(store),
            10.0,
            FLAT,
        ),
        identity=key,
    )
    assert answered.complete, answered.unanswered
    capsys.readouterr()
    assert main(["update", "--json", str(root), "--host", "none"]) == 0
    capsys.readouterr()
    return root, store


def first_edge_retracted(text: str) -> str:
    line = next(x for x in text.split("\n") if x.startswith("→ "))
    return text.replace(line + "\n", "", 1)


@pytest.mark.usefixtures("develop_mode")
def test_an_edit_on_an_update_s_state_is_what_its_re_run_gives(
    tmp_path, capsys
) -> None:
    root, store = updated_state(tmp_path, capsys)
    before = versions.latest(store)
    assert before.command is Command.UPDATE

    given = give(store, first_edge_retracted)

    assert given.state is not None, given.problems
    assert given.state.identity == before.identity
    kept = versions.states(store)
    out = tmp_path / "rerun.json"
    capsys.readouterr()
    assert (
        main(["update", "--json", str(root), "--host", "none", "-o", str(out)]) == 0
    ), capsys.readouterr().err
    capsys.readouterr()
    assert out.read_bytes() == versions.kept_export(store, given.state)
    assert versions.states(store) == kept


@pytest.mark.usefixtures("develop_mode")
def test_an_update_on_an_edit_s_version_that_would_ask_is_nothing_to_update(
    tmp_path, capsys
) -> None:
    """Asking the update before the edit again would ask what the edit
    corrected: only its replay runs."""
    root, store = updated_state(tmp_path, capsys)
    given = give(store, first_edge_retracted)
    assert given.state is not None, given.problems
    kept = versions.states(store)

    for ask in (["--profile", "standard", "--dry-run"], ["--host", "opencode"]):
        capsys.readouterr()
        assert main(["update", "--json", str(root), *ask]) == 1
        err = capsys.readouterr().err
        assert f"state {given.state.id} kept (edit): nothing to update" in err
    assert versions.states(store) == kept


# --- a kept state under a later rule (docs/model.md § 7) --------------------------

#: The block whose lines a rule added after the state was kept refuses.
TIGHTENED = "thm:bound"
#: What that rule says.
LATER_RULE = "a rule added after the state was kept"


def tightened(monkeypatch, owner: str = TIGHTENED) -> None:
    """A rule added after the state was kept: every standing and every
    `about` edge under `owner` refused by the sheet's check."""
    before = es._handle_problems

    def handle_problems(sheet: es.Sheet) -> list[str]:
        return [
            *before(sheet),
            *(
                f"{s.source} ⊢ {s.claim!r}: {LATER_RULE}"
                for s in sheet.supports
                if s.source == owner
            ),
            *(
                f"{e.source} → {e.kind} {e.target}: {LATER_RULE}"
                for e in sheet.edges
                if e.source == owner and e.kind == "about"
            ),
        ]

    monkeypatch.setattr(es, "_handle_problems", handle_problems)


def test_an_edit_is_refused_only_for_a_problem_it_adds(
    tmp_path, capsys, monkeypatch
) -> None:
    store, args = built_state(tmp_path, capsys)
    tightened(monkeypatch)
    with edits.opened(store, versions.latest(store)) as kept:
        assert [p for p in kept.kept_edges_problems if LATER_RULE in p], (
            kept.kept_edges_problems
        )

    # an edit elsewhere is taken, the state's problem standing
    given = give(store, adding(ADDED))
    assert given.state is not None, given.problems

    # never forced: a line added on the block that holds it is taken too
    on_it = '→ uses def:gain · "The output is bounded."'
    given = give(
        store,
        lambda text: text.replace(
            f"⟦done {TIGHTENED}⟧", f"{on_it}\n⟦done {TIGHTENED}⟧", 1
        ),
    )
    assert given.state is not None, given.problems

    # a problem the edit adds is refused, and only it is named
    given = give(store, adding('→ uses def:gain · "No such words."'))
    assert given.state is None
    assert any("evidence not in" in p for p in given.problems), given.problems
    assert not any(LATER_RULE in p for p in given.problems)

    # `sheet` names the state's problems; they are no edit's
    capsys.readouterr()
    sheet = ["sheet", *kept_args(store, tmp_path), "--stage", "edges"]
    assert main([*sheet, "-o", str(tmp_path / "e.md")]) == 0
    err = capsys.readouterr().err
    assert "holds 1 problem(s)" in err and LATER_RULE in err

    # `open` lists them on every state that holds them, the edits' too
    report = open_report(View(store, versions.latest(store)), kinds=["later-rule"])
    (item,) = report.sections["later_rule"]
    assert item.line.startswith(f"{TIGHTENED}: a line of its edges file breaks")
    assert item.data["handle"] == TIGHTENED and LATER_RULE in item.data["cause"]
    assert "axiomation edit --stage edges" in item.data["clears"]
    assert report.notes == []


@pytest.mark.usefixtures("develop_mode")
def test_an_update_from_a_state_holding_a_later_rule_s_problem_leaves_it(
    tmp_path, capsys, monkeypatch
) -> None:
    """OLD's sheet holds what a later rule refuses: the closing words' edge,
    which the update reopens for its scope. The writer leaves the line as
    it was; the session is not refused for it, and the line stands."""
    root, directory, _ = _updated(tmp_path, capsys)
    store = RecordStore(directory)
    tightened(monkeypatch, CLOSING)
    assert main(["update", "--json", str(root), "--host", "none"]) == 5
    (key,) = [a.key for a in store.authoritative().values() if a.kind == "identity"]
    answered = run_update(
        read_paper(root),
        store,
        UpdateSettings(
            files=FILES,
            review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
            paper=root.resolve(),
        ),
        Runner(
            stepping(answering({"we-fix-a-field": ["Δ reworded — a clause added"]})),
            Resolver(store),
            10.0,
            FLAT,
        ),
        identity=key,
    )

    assert answered.complete, answered.unanswered
    assert any(LATER_RULE in p for p in answered.plan.kept_problems)
    assert CLOSING in answered.plan.reopened
    capsys.readouterr()
    assert main(["update", "--json", str(root), "--host", "none"]) == 0
    found = versions.kept_sheet(store, versions.latest(store))
    assert found is not None and '→ about scope:paper · "Closing words"' in found[0]
    # a state's sheet is judged on its own files, never the next version's
    assert set(found[1]) == set(FILES)


def test_a_standing_s_minted_handle_is_not_what_its_line_says(tmp_path, capsys) -> None:
    """A standing with no kept handle reads back: a handle minted from the
    words is set aside, one a line writes is compared."""
    sharp = [*WRITTEN[SHARP], '⊢ conjectured · "the bound is sharp"']
    store, _ = built_state(tmp_path, capsys, {**WRITTEN, SHARP: sharp})
    kept = versions.latest(store)
    export = json.loads(versions.kept_export(store, kept))
    minted = [s for s in export["standings"] if "handle" in s]
    assert len(minted) == 2
    for s in minted:
        del s["handle"]
    files = {path: versions.blob(store, name) for path, name in kept.files.items()}
    older, _ = versions.record(
        store,
        files,
        kept.version,
        kept.root,
        dumps(export).encode("utf-8"),
        Command.EDIT,
        started_from=kept,
    )

    with edits.opened(store, older) as opened:
        assert opened.sheet is not None


def test_a_build_s_rule_flags_are_not_read_again(tmp_path, capsys, monkeypatch) -> None:
    """A rule that raises more flags than it did when the state was kept
    refuses no edit: the kept flags are the state's."""
    store, _ = built_state(tmp_path, capsys)
    export = json.loads(latest_export(store))
    assert any(
        f["by"] == "rule" and f["stage"] == "groups" for f in export["review"]["flags"]
    )
    today = edits.rule_flags
    monkeypatch.setattr(edits, "rule_flags", lambda *a, **k: today(*a, **k) * 2)

    assert give(store, adding(ADDED)).state is not None


# --- `axiomation change` -----------------------------------------------------------


def kept_args(store: RecordStore, tmp_path: Path) -> list[str]:
    return [str(tmp_path / "paper"), "--store", str(store.directory)]


def changed(args: list[str], capsys, *extra: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(["change", *args, *extra])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_change_without_store_is_given_on_the_paper_s_own(tmp_path, capsys) -> None:
    """`--store` left out: the paper's own store, as its help says."""
    store, _ = built_state(tmp_path, capsys)
    paper = tmp_path / "paper"
    shutil.copytree(store.directory, paper / STORE_DIRECTORY)
    own = RecordStore(paper / STORE_DIRECTORY)
    before, elsewhere = len(versions.states(own)), versions.states(store)

    code, _, err = changed([str(paper)], capsys, "--add", "closing-words", ADDED)

    assert code == 0, err
    assert len(versions.states(own)) == before + 1
    assert versions.states(store) == elsewhere


def test_a_shorthand_and_its_fragment_give_the_same_answer(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)
    fragment = tmp_path / "fragment.md"
    fragment.write_text(f"⟦closing-words-of-the⟧\n+ {ADDED}\n", encoding="utf-8")
    with edits.opened(store, versions.latest(store)) as kept:
        sheet = edits.shown(kept, Layer.EDGES)
        by_fragment = edits.applied_fragment(
            sheet, edits.parse_fragment(fragment.read_text(encoding="utf-8"))
        )
        by_shorthand = edits.applied_fragment(
            sheet,
            edits.Fragment(lines=(("closing-words-of-the", edits.ADD, ADDED),)),
        )
    assert by_fragment == by_shorthand == adding(ADDED)(sheet)

    # the owner by a unique prefix of its handle
    code, out, err = changed(args, capsys, "--add", "closing-words", ADDED)

    assert code == 0, err
    assert out.startswith("state ") and "by person, answer" in out
    assert "edges (1):" in out and "+ " in out
    assert "reach of closing-words-of-the" in out
    made = versions.latest(store)
    assert made.command is Command.EDIT


def test_a_change_says_what_it_lapsed_and_did_to_the_gates(tmp_path, capsys) -> None:
    """An edges edit asks nothing; the reading whose one hop it changes
    lapses (`docs/model.md` § 8), and `change` names it and each gate item
    that moved, so a count that stays the same hides nothing."""
    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)
    assert (
        main(
            [
                "judge",
                *args,
                "--aspect",
                "true",
                "--on",
                "thm:bound",
                "--verdict",
                "verified",
                "--depth",
                "informal",
                "--evidence",
                "read it",
            ]
        )
        == 0
    ), capsys.readouterr().err

    code, out, err = changed(
        args,
        capsys,
        "--add",
        "thm:bound",
        '→ uses def:gain · "The output is bounded."',
        "--json",
    )

    assert code == 0, err
    printed = json.loads(out)
    (lapsed,) = printed["lapsed"]["items"]
    assert lapsed["handle"] == "thm:bound"
    assert lapsed["cause"]["why"] == "the edge thm:bound → uses def:gain was added"
    moved = {g["gate"]: g for g in printed["gates"]["items"]}
    assert [i["handle"] for i in moved["unreviewed"]["new"]] == ["thm:bound"]


def store_files(store: RecordStore) -> dict[str, bytes]:
    return {
        str(f.relative_to(store.directory)): f.read_bytes()
        for f in sorted(store.directory.rglob("*"))
        if f.is_file()
    }


def test_a_change_s_dry_run_says_what_the_change_says_and_records_nothing(
    tmp_path, capsys
) -> None:
    """The same edit on a scratch copy of the store: its lines, what
    lapses and the gates as the change prints them, the store untouched."""
    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)
    line = '→ uses def:gain · "The output is bounded."'
    before = store_files(store)

    code, dry, err = changed(
        args, capsys, "--add", "thm:bound", line, "--json", "--dry-run"
    )

    assert code == 0, err
    assert store_files(store) == before
    code, given, err = changed(args, capsys, "--add", "thm:bound", line, "--json")
    assert code == 0, err
    dry, given = json.loads(dry), json.loads(given)
    assert dry.pop("dry_run") is True
    assert dry == given  # the same answer, state and report, recorded now
    capsys.readouterr()
    assert main(["change", *args, "--add", "thm:bound", line, "--dry-run"]) == 1
    # a dry run refuses as the edit does
    assert "holds the line" in capsys.readouterr().err


def test_a_change_s_dry_run_text_opens_with_its_head(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)

    code, out, err = changed(
        kept_args(store, tmp_path), capsys, "--add", "closing-words", ADDED, "--dry-run"
    )

    assert code == 0, err
    assert out.startswith("dry run on a scratch copy of the store: nothing recorded\n")
    assert len(versions.states(store)) == 1


def test_retract_keep_and_standing_each_make_a_state(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)
    proof = "by-definition-def:gain-the"

    code, _, err = changed(args, capsys, "--retract", proof, DROPPED)
    assert code == 1 and "needs --reason" in err
    code, out, err = changed(
        args, capsys, "--retract", proof, DROPPED, "--reason", "no such footnote"
    )
    assert code == 0, err
    retracted = versions.latest(store)
    answer = versions_answer(store, retracted)
    assert answer.reason == "no such footnote"
    assert DROPPED not in json.dumps(json.loads(versions.kept_export(store, retracted)))

    code, _, err = changed(
        args, capsys, "--retract", proof, DROPPED, "--reason", "again"
    )
    assert code == 1 and f"holds no line `{DROPPED}`" in err

    code, out, err = changed(
        args,
        capsys,
        "--standing",
        "next-the-bound-is",
        '⊢ conjectured · "Next, the bound is sharp."',
        "--json",
    )
    assert code == 0, err
    standings = json.loads(out)["standings"]["items"]
    assert {i["change"] for i in standings} == {"began", "ended"}

    code, out, err = changed(
        args, capsys, "--keep", "closing-words-of-the", SCOPE, "--reason", "read it"
    )
    assert code == 0, err
    export = json.loads(versions.kept_export(store, versions.latest(store)))
    assert [(p["kind"], p["line"]) for p in export["review"]["person"]["pins"]] == [
        ("deleted", DROPPED),
        ("written", '⊢ conjectured · "Next, the bound is sharp."'),
        ("deleted", '⊢ unsupported · "Next, the bound is sharp."'),
        ("kept", SCOPE),
    ]


def versions_answer(store: RecordStore, state: versions.State):
    return store.authoritative()[state.answers[-1]]


def test_a_line_given_without_its_tail_names_the_one_line_with_its_head(
    tmp_path, capsys
) -> None:
    """A retract or a keep names a line by its head and quote, as `show`
    prints it: its ` — <why>` tail aside; where no line or several have
    them, the refusal prints the lines whole, to copy."""
    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)
    tailed = f"{ADDED} — the closing words read the gain"
    code, _, err = changed(args, capsys, "--add", "closing-words", tailed)
    assert code == 0, err

    code, _, err = changed(
        args, capsys, "--retract", "closing-words", ADDED, "--reason", "not read"
    )
    assert code == 0, err
    export = json.loads(versions.kept_export(store, versions.latest(store)))
    assert ("deleted", tailed) in [
        (p["kind"], p["line"]) for p in export["review"]["person"]["pins"]
    ]

    # no line with its head and quote: the owner's edge lines printed whole
    unheld = '→ about scope:paper · "no such words"'
    code, _, err = changed(
        args, capsys, "--keep", "closing-words", unheld, "--reason", "read it"
    )
    assert code == 1
    assert f"holds no line `{unheld}`; it holds:\n  {SCOPE}" in err

    with edits.opened(store, versions.latest(store)) as kept:
        sheet = edits.shown(kept, Layer.EDGES)
    owner = "closing-words-of-the"
    both = edits.applied_fragment(
        sheet,
        edits.Fragment(
            lines=(
                (owner, edits.ADD, f"{ADDED} — one"),
                (owner, edits.ADD, f"{ADDED} — two"),
            )
        ),
    )
    with pytest.raises(EditError) as refused:
        edits.applied_fragment(
            both, edits.Fragment(lines=((owner, edits.RETRACT, ADDED),))
        )
    assert str(refused.value) == (
        f"⟦{owner}⟧ holds 2 lines with the head and quote of `{ADDED}`; give one "
        f"whole:\n  {ADDED} — one\n  {ADDED} — two"
    )
    kept_one = edits.applied_fragment(
        both, edits.Fragment(lines=((owner, edits.KEEP, f"{ADDED} — two"),))
    )
    assert f"\n{es.KEEP_MARK}{ADDED} — two\n" in kept_one
    # a `+` line keeps its exact test: the short form is a line of its own
    assert f"\n{ADDED}\n" in edits.applied_fragment(
        both, edits.Fragment(lines=((owner, edits.ADD, ADDED),))
    )


@pytest.mark.parametrize(
    "given",
    [
        '⊢ justified · "The bound holds."',
        '⊢ justified · "The bound holds." — read in the proof',
        '⊢ justified · "The bound holds." (argued in part)',
        '⊢ justified · "The bound holds." — read in the proof (argued in part)',
        '⊢ justified [bound] · "The bound holds." (argued in part)',
    ],
)
def test_a_standing_as_show_prints_it_names_the_sheet_s_line(given) -> None:
    """A standing as a person copies it, its claim's handle left out or
    kept, ending in its completeness where not `given` as `show` prints it: it
    names the sheet's line all the same, and no standing of another status
    or claim."""
    held = [
        '⊢ justified [bound] · "The bound holds." — read in the proof',
        '⊢ unsupported [sharp] · "The bound is sharp."',
        '→ uses def:gain · "The bound holds."',
    ]
    assert edges_file.named_lines(held, given) == [held[0]]
    assert edges_file.named_lines(held, '⊢ unsupported · "The bound holds."') == []


#: A block of two claims, each named by the handle its ⊢ line writes.
TWO_CLAIMS = "a-closing-paragraph-in"
TWO_CLAIMS_LINES = [
    '→ about [para] thm:bound · "A closing paragraph"',
    '→ about file:main.tex where-used · "A closing paragraph"',
    '⊢ restated [para] · "A closing paragraph"',
    '⊢ unsupported [other] · "in another file"',
]


def test_a_standing_set_again_keeps_its_claim_s_handle(tmp_path, capsys) -> None:
    """`--standing` replaces the standing on the same claim: a line that
    writes no handle keeps the one the sheet shows, so a line naming the
    claim by it in the same change still names it."""
    store, _ = built_state(tmp_path, capsys, {**WRITTEN, TWO_CLAIMS: TWO_CLAIMS_LINES})
    args = kept_args(store, tmp_path)

    code, _, err = changed(
        args,
        capsys,
        "--add",
        TWO_CLAIMS,
        '→ about [other] next-the-bound-is · "in another file"',
        "--standing",
        TWO_CLAIMS,
        '⊢ restated · "in another file"',
    )

    assert code == 0, err
    found = versions.kept_sheet(store, versions.latest(store))
    assert found is not None
    assert '⊢ restated [other] · "in another file"' in found[0]
    # a handle the line writes is its own
    with edits.opened(store, versions.latest(store)) as kept:
        sheet = edits.shown(kept, Layer.EDGES)
    assert edits.standing_lines(sheet, TWO_CLAIMS, '⊢ self [mine] · "in another file"')[
        -1
    ] == (TWO_CLAIMS, edits.ADD, '⊢ self [mine] · "in another file"')


def test_show_prints_a_claim_s_handle_as_the_sheet_does(tmp_path, capsys) -> None:
    """On a block of two claims each standing shows its handle, so a line
    copied from `show` names the claim; a block's only claim shows none, as
    on the sheet."""
    store, _ = built_state(tmp_path, capsys, {**WRITTEN, TWO_CLAIMS: TWO_CLAIMS_LINES})
    args = kept_args(store, tmp_path)

    capsys.readouterr()
    assert main(["show", *args, TWO_CLAIMS]) == 0
    two = capsys.readouterr().out
    assert main(["show", *args, "thm:bound"]) == 0
    one = capsys.readouterr().out

    assert '⊢ restated [para] · "A closing paragraph"' in two
    assert '⊢ unsupported [other] · "in another file"' in two
    assert '⊢ justified · "The output is bounded."' in one


def test_a_second_edge_s_refusal_names_the_line_to_file(tmp_path, capsys) -> None:
    """A second edge for one source, kind and target, refused: the change
    names the line the sheet holds and the one line naming both claims,
    which is then accepted as written."""
    store, _ = built_state(tmp_path, capsys, {**WRITTEN, TWO_CLAIMS: TWO_CLAIMS_LINES})
    args = kept_args(store, tmp_path)
    held = TWO_CLAIMS_LINES[0]
    merged = '→ about [para, other] thm:bound · "A closing paragraph"'

    code, _, err = changed(
        args,
        capsys,
        "--add",
        TWO_CLAIMS,
        '→ about [other] thm:bound · "in another file"',
    )

    assert code == 1
    assert (
        f"{TWO_CLAIMS} → about thm:bound: a second edge for one source, kind and "
        f"target; one line names them all: retract `{held}` and add `{merged}`"
    ) in err
    code, _, err = changed(
        args,
        capsys,
        "--retract",
        TWO_CLAIMS,
        held,
        "--reason",
        "both claims restate it",
        "--add",
        TWO_CLAIMS,
        merged,
    )
    assert code == 0, err


def test_a_merged_edge_says_what_both_lines_say_or_is_not_offered() -> None:
    """The line to file keeps the held line's evidence, note and marks, its
    brackets the union of every line's, in their order. A bracket one line
    leaves out stays out, since a line naming none relies on every claim;
    none is offered where one line cannot say what both say."""
    assert (
        edits._merged_edge(
            '→ uses t [a] ref · "Hence" — read',
            ['→ uses t [a] ref · "Hence" — read', '→ uses t [a, b] ref · "So"'],
        )
        == '→ uses t [a, b] ref · "Hence" — read'
    )
    assert (
        edits._merged_edge(
            '→ about [r] t · "x"', ['→ about [r] t · "x"', '→ about [s] t [p] · "y"']
        )
        == '→ about [r, s] t · "x"'
    )
    for lines in (
        ['→ justifies t [a] · "x"', '→ justifies t [a, b] · "y"'],
        ['→ uses t · "x"', '→ uses t sketch · "y"'],
        ['→ about [-] t · "x"', '→ about [s] t · "y"'],
    ):
        assert edits._merged_edge(lines[0], lines) is None


def test_an_agent_s_change_names_its_model_and_session(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)

    code, _, err = changed(args, capsys, "--add", "closing", ADDED, "--agent", "m")
    assert code == 1 and "--agent and --agent-session go together" in err
    code, out, err = changed(
        args,
        capsys,
        "--add",
        "closing",
        ADDED,
        "--agent",
        "m",
        "--agent-session",
        "s-9",
    )

    assert code == 0, err
    assert "by agent m (session s-9)" in out
    answer = versions_answer(store, versions.latest(store))
    assert (answer.host, answer.model, answer.session) == (AGENT, "m", "s-9")
    code, _, err = changed(
        args,
        capsys,
        "--keep",
        "closing",
        SCOPE,
        "--reason",
        "r",
        "--agent",
        "m",
        "--agent-session",
        "s",
    )
    assert code == 1 and "keep mark" in err


def test_a_fragment_adds_a_part_and_an_edge_onto_it(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)
    fragment = tmp_path / "fragment.md"
    fragment.write_text(
        '+ def:gain [ratio]: "the ratio of output to input"\n'
        "⟦closing-words-of-the⟧\n"
        '+ → uses def:gain [ratio] · "Closing words of the results."\n',
        encoding="utf-8",
    )

    code, out, err = changed(args, capsys, "--fragment", str(fragment), "--json")

    assert code == 0, err
    export = json.loads(versions.kept_export(store, versions.latest(store)))
    assert any(e["id"] == "def:gain [ratio]" for e in export["entries"])
    fragment.write_text("⟦nowhere⟧\n+ " + ADDED + "\n", encoding="utf-8")
    code, _, err = changed(args, capsys, "--fragment", str(fragment))
    assert code == 1 and "'nowhere' names no block" in err


def test_a_line_added_by_change_moves_its_claim_s_standing(tmp_path, capsys) -> None:
    """`change --add` of a justifies line onto a claim standing
    unsupported stands it justified in the same change, so the gate moves
    with the edge (`docs/model.md` § 4); its dry run says so first."""
    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)
    line = '→ justifies next-the-bound-is · "Hence the output is bounded."'
    proof = "group:by-definition-def:gain-the"

    code, out, err = changed(args, capsys, "--add", proof, line, "--dry-run")
    assert code == 0, err
    assert "unsupported-claims" in out

    code, out, err = changed(args, capsys, "--add", proof, line)
    assert code == 0, err
    export = json.loads(versions.kept_export(store, versions.latest(store)))
    (sharp,) = [
        s for s in export["standings"] if s["claim"]["text"].startswith("Next,")
    ]
    assert sharp["status"] == "justified"


def test_an_about_line_naming_no_claim_says_it_restates_none(tmp_path, capsys) -> None:
    """`change --add` of an about line naming no claim at its source end
    describes its target: its claim stays unsupported, and the dry run and
    the change say so with the line that would stand it restated."""
    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)
    owner = "next-the-bound-is"
    line = '→ about thm:bound · "Next, the bound is sharp."'

    for dry in (["--dry-run"], []):
        code, out, err = changed(args, capsys, "--add", owner, line, *dry)
        assert code == 0, err
        assert "names no claim at its source end, so it restates none" in err
        assert f"`→ about [{owner}] thm:bound · " in err
    export = json.loads(versions.kept_export(store, versions.latest(store)))
    (sharp,) = [
        s for s in export["standings"] if s["claim"]["text"].startswith("Next,")
    ]
    assert sharp["status"] == "unsupported"


def test_a_state_s_sheet_shows_its_own_edges(tmp_path, capsys) -> None:
    """The sheet an edit is shown is rendered from the state: a state kept
    with an edge fewer than its answer wrote shows that edge gone."""
    store, _ = built_state(tmp_path, capsys)
    kept = versions.latest(store)
    export = json.loads(versions.kept_export(store, kept))
    export["edges"] = export["edges"][1:]
    files = {path: versions.blob(store, name) for path, name in kept.files.items()}
    tampered, _ = versions.record(
        store,
        files,
        kept.version,
        kept.root,
        dumps(export).encode("utf-8"),
        Command.EDIT,
        started_from=kept,
    )

    with edits.opened(store, kept) as before:
        assert before.sheet is not None
        shown = es.parse(before.sheet).edges
    with edits.opened(store, tampered) as after:
        assert after.sheet is not None
        assert len(es.parse(after.sheet).edges) == len(shown) - 1


@pytest.mark.usefixtures("develop_mode")
def test_an_edit_on_an_update_that_carried_pins_is_what_its_re_run_gives(
    tmp_path,
) -> None:
    """OLD's person pins carried onto NEW by the update,
    its state kept, then a person's edges edit on it: the update re-run
    with that answer gives the edit's export byte for byte, the carried
    pins first."""
    from axiomation.exchange import dumps as dumped
    from axiomation.questions.edges_file import EDGES_FILE
    from axiomation.reader import document_hash
    from axiomation.runner.hosts import Rates
    from axiomation.update import run, update_payload
    from runner.fakes import FakeHost
    from test_person import CLOSING, DEFINITION, versions_with_pins
    from test_update import UPDATE, without_qualifies
    from test_update import answering as answering_update

    _, new, store = versions_with_pins(tmp_path)
    answer = answering_update(
        {
            "we-fix-a-field": ["Δ narrowed — the setting now says less"],
            DEFINITION: ['Δ unchanged — "The gain is the ratio": it reads the same'],
            CLOSING: ["Δ narrowed — it now closes less"],
        },
        without_qualifies,
    )
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: answer})
    found = run(
        new.document,
        store,
        UPDATE,
        Runner(fake, Resolver(store), 10.0, Rates(input=0.0, output=1.0)),
    )
    assert found.complete, found.unanswered
    payload = update_payload(new.document, found, store)
    assert payload["review"]["person"]["pins"]
    kept, _ = versions.record(
        store,
        versions.snapshot(new.root, new.document),
        document_hash(new.document),
        new.document.root,
        dumped(payload).encode("utf-8"),
        Command.UPDATE,
        identity=found.identity.key,
    )

    given = give(store, adding(ADDED))

    assert given.state is not None, given.problems
    replayed = update_payload(new.document, run(new.document, store, UPDATE), store)
    assert dumped(replayed).encode("utf-8") == versions.kept_export(store, given.state)


def test_a_state_s_own_pins_come_back_from_their_pairs_lapsed_ones_too(
    tmp_path, capsys
) -> None:
    """The person rounds decide a kept state's pins again from pairs of
    sheets (`edits._pairs`): each pair gives back exactly its pins, a
    written line the sheet no longer holds (a lapsed pin) and a deleted one
    it holds again among them, so the rounds see what a replay sees."""
    from axiomation.person import Pin, PinKind

    store, _ = built_state(tmp_path, capsys)
    with edits.opened(store, versions.latest(store)) as kept:
        ids = kept.handles()
        closing, proof = ids["closing-words-of-the"], ids["by-definition-def:gain-the"]
        pins = [
            Pin(Layer.EDGES, closing, PinKind.WRITTEN, ADDED, rank=1),  # absent
            Pin(Layer.EDGES, closing, PinKind.KEPT, SCOPE, rank=1),
            Pin(Layer.EDGES, proof, PinKind.DELETED, DROPPED, rank=1),  # present
            Pin(Layer.EDGES, proof, PinKind.WRITTEN, SELF_MARK, rank=2),  # present
            Pin(Layer.EDGES, closing, PinKind.DELETED, WROTE, rank=2),  # absent
        ]
        found = [
            (p.owner, p.kind, p.line, p.rank)
            for pair in edits._pairs(kept, pins)
            for p in pair.on(kept.document, kept.cut, kept.files)
        ]
        handle = {i: h for h, i in ids.items()}
        assert sorted(found) == sorted(
            (handle[p.owner], p.kind, p.line, p.rank) for p in pins
        )


# --- what an edit refuses before it records anything ------------------------------


#: A person's roles edit: the definition made an assumption.
TO_ASSUMPTION = ("⟦b3 definition⟧", "⟦b3 assumption⟧")


def given_on(
    store: RecordStore,
    layer: Layer,
    edit,
    runner: Runner | None = None,
    author=A_PERSON,
) -> edits.Given:
    """`edit` of `layer`'s file given on the latest state, what it reopens
    asked through `runner`."""
    with edits.opened(store, versions.latest(store)) as kept:
        edited = edit(edits.shown(kept, layer))
        return edits.give(kept, layer, edited, author, runner=runner)


def roles_edited(store: RecordStore, runner: Runner | None = None) -> edits.Given:
    """A person's roles edit on the latest state: a definition made an
    assumption, which reopens its owner and the qualifies edge's source."""
    return given_on(store, Layer.ROLES, lambda t: t.replace(*TO_ASSUMPTION), runner)


def handle_ids(export: dict) -> dict[str, str]:
    """An export's handles, each to the block or group id it names."""
    return {handle: ident for ident, handle in export["handles"].items()}


def asking(store: RecordStore) -> Runner:
    """A fake host answering what an edit reopens: the touched roles as the
    person tests' roles session, the re-ask marking its owners done."""
    return Runner(person_host(reask=done), Resolver(store), 10.0, FLAT)


def test_an_edges_edit_waits_for_a_pending_roles_edit(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    assert roles_edited(store).state is None
    answers = (store.directory / RECORD).read_text(encoding="utf-8")

    with pytest.raises(EditError, match="holds an edit no state applied: roles"):
        give(store, adding(ADDED))

    assert (store.directory / RECORD).read_text(encoding="utf-8") == answers


# --- a cut, roles or groups edit: a state at once -------------------------


@pytest.mark.usefixtures("develop_mode")
def test_a_roles_edit_waits_unanswered_then_resumes_into_the_state_a_replay_gives(
    tmp_path, capsys
) -> None:
    store, args = built_state(tmp_path, capsys)
    before = versions.latest(store)

    waiting = roles_edited(store)

    assert waiting.state is None
    (reask,) = waiting.unanswered
    assert reask.stage is Stage.EDGES
    key = waiting.recorded.answer.key
    assert [a.key for a in versions.pending_answers(store, before.version)] == [key]
    assert versions.latest(store) == before

    resumed = roles_edited(store, asking(store))

    assert resumed.recorded.answer.key == key  # recorded once, resumed
    made = resumed.state
    assert made is not None and not resumed.unanswered
    assert (made.command, made.parent, made.version) == (
        Command.EDIT,
        before.id,
        before.version,
    )
    assert versions.pending_answers(store, before.version) == ()
    export = json.loads(versions.kept_export(store, made))
    definition = handle_ids(export)[DEFINITION]
    assert next(b for b in export["blocks"] if b["id"] == definition)["roles"] == [
        "assumption"
    ]
    assert export["edges_answer"] == reask.key
    assert [(p["layer"], p["kind"]) for p in export["review"]["person"]["pins"]] == [
        ("roles", "changed")
    ]
    with pytest.raises(EditError, match="as the state shows it"):
        roles_edited(store, asking(store))  # given again: the state holds it

    kept = versions.states(store)
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, made)
    assert versions.states(store) == kept


@pytest.mark.usefixtures("develop_mode")
def test_a_cut_edit_asks_the_touched_roles_and_the_reask_as_a_replay_does(
    tmp_path, capsys
) -> None:
    store, args = built_state(tmp_path, capsys)
    blocks = len(json.loads(latest_export(store))["blocks"])

    given = given_on(store, Layer.CUT, lambda t: t.replace(*SPLIT), asking(store))

    assert given.state is not None, given.unanswered
    assert [r.answered for r in given.reports] == [
        (given.reports[0].asked[0],),
        (given.reports[1].asked[0],),
    ]
    export = json.loads(versions.kept_export(store, given.state))
    assert len(export["blocks"]) == blocks + 1
    assert export["review"]["questions"]["roles"] == 2
    assert [p["layer"] for p in export["review"]["person"]["pins"]] == ["cut"]
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


def test_a_person_s_split_carries_across_an_edit_while_its_words_stand(
    tmp_path, capsys
) -> None:
    """A person's cut edit split the setting's paragraph; the author then
    edits both its sentences. While the second's first words stand, the
    split carries: `changes` pairs each sentence with its own block and
    lists no correction lost. Once those words are rewritten, it lists the
    split lost, with its words and why, on the block that started there."""
    store, args = built_state(tmp_path, capsys)
    given = given_on(store, Layer.CUT, lambda t: t.replace(*SPLIT), asking(store))
    assert given.state is not None, given.unanswered
    paper = Path(args[0]) / "main.tex"
    text = paper.read_text(encoding="utf-8")

    def changes(edited: str) -> dict:
        paper.write_text(edited, encoding="utf-8")
        capsys.readouterr()
        assert main(["changes", *args[:3], "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    both = text.replace("a field of", "a ring of").replace(
        "of the setting.", "of the new setting."
    )
    found = changes(both)
    assert sorted(i["class"] for i in found["text"]["items"]) == ["changed", "changed"]
    assert found["corrections_lost"]["total"] == 0

    found = changes(both.replace("A second sentence", "Another sentence"))
    (lost,) = found["corrections_lost"]["items"]
    assert (lost["op"], lost["words"], lost["why"], lost["said"]) == (
        "split",
        "A second sentence",
        "words",
        "its words changed",
    )
    assert lost["node"] is not None and lost["handle"]
    capsys.readouterr()
    assert (
        main(
            ["update", "--json", *args[:3], "--host", "opencode", "--dry-run", "--json"]
        )
        == 0
    )
    planned = json.loads(capsys.readouterr().out)
    (entry,) = planned["corrections_lost"]
    assert (entry["op"], entry["why"]) == ("split", "words")


def test_changes_lists_a_group_whose_run_the_edit_cut(tmp_path, capsys) -> None:
    """A word of a proof's step changed leaves its group; a step inserted
    inside the proof cuts its run: `changes` and `update --dry-run` list
    that group lost, by OLD's handle, with why."""
    store, args = built_state(tmp_path, capsys)
    paper = Path(args[0]) / "main.tex"
    text = paper.read_text(encoding="utf-8")

    def changes(edited: str) -> dict:
        paper.write_text(edited, encoding="utf-8")
        capsys.readouterr()
        assert main(["changes", *args[:3], "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    found = changes(text.replace("the gain is small", "the gain is tall"))
    assert found["groups_lost"]["total"] == 0

    found = changes(
        text.replace(
            "Hence the output is bounded.\n\n",
            "Hence the output is bounded.\n\nAn inserted step.\n\n",
        )
    )
    (lost,) = found["groups_lost"]["items"]
    assert (lost["why"], lost["said"]) == (
        "run",
        "its members no longer stand as one run",
    )
    assert lost["handle"].startswith("group:") and lost["node"] != "-"
    capsys.readouterr()
    assert (
        main(
            ["update", "--json", *args[:3], "--host", "opencode", "--dry-run", "--json"]
        )
        == 0
    )
    planned = json.loads(capsys.readouterr().out)
    (entry,) = planned["groups_lost"]
    assert entry["why"] == "run"


def test_the_re_ask_a_cut_edit_opens_is_not_refused_for_the_state_s_problem(
    tmp_path, capsys, monkeypatch
) -> None:
    """A cut edit on a state holding what a later rule refuses: the re-ask
    it opens leaves that line as it was, and is not refused for it."""
    store, _ = built_state(tmp_path, capsys)
    tightened(monkeypatch)

    given = given_on(store, Layer.CUT, lambda t: t.replace(*SPLIT), asking(store))

    assert given.state is not None, given.unanswered
    export = json.loads(versions.kept_export(store, given.state))
    held = handle_ids(export)[TIGHTENED]
    assert any(s["block"] == held for s in export["standings"])


def test_a_split_by_edit_keeps_the_node_at_its_start_and_adds_the_rest(
    tmp_path, capsys
) -> None:
    """The setting split before its second, longer
    sentence. The first piece keeps the setting's node and reads as
    changed, the second is added from it; the line on the second
    sentence's words is carried to it, not ended and begun; the
    definition's use of the whole setting is to look at, beside what
    stands in its place."""
    moving = '→ about scope:paper · "A second sentence of the setting."'
    whole = '→ uses we-fix-a-field · "The gain is the ratio"'
    lines = {**WRITTEN, SETTING: [moving], DEFINITION: [whole]}
    store, _ = built_state(tmp_path, capsys, lines)
    before = versions.latest(store)
    setting = handle_ids(json.loads(latest_export(store)))[SETTING]

    given = given_on(store, Layer.CUT, lambda t: t.replace(*SPLIT), asking(store))

    after = given.state
    assert after is not None, given.unanswered
    kept = before.node_of(setting)
    assert after.node_of(setting) == kept
    (piece,) = {n.node for n in after.nodes} - {n.node for n in before.nodes}
    report = changes_report(store, View(store, before), View(store, after))
    text = report.sections["text"]
    assert [(i.data["node"], i.data["classes"], i.data["joined"]) for i in text] == [
        (kept, ["changed"], [piece]),
        (piece, ["added"], [kept]),
    ]
    assert f"added  (from {kept})" in text[1].line
    assert text[0].data["old"]["text"].endswith("A second sentence of the setting.")
    assert text[0].data["new"]["text"] == "We fix a field of scalars."
    assert report.sections["edges"] == []
    (carried,) = report.sections["carried"]
    assert carried.data["change"] == "carried"
    assert (carried.data["old"]["source_node"], carried.data["new"]["source_node"]) == (
        kept,
        piece,
    )
    (look,) = report.sections["to_look_at"]
    assert look.data["why"] == "its block split"
    assert look.data["old"]["edge"]["target"] == {"block": setting}
    assert [e["edge"]["target"] for e in look.data["now"]] == [{"block": setting}]
    assert look.line.startswith(f"! {DEFINITION} {whole}")


@pytest.mark.usefixtures("develop_mode")
def test_a_groups_edit_reasks_its_justifies_edge_as_a_replay_does(
    tmp_path, capsys
) -> None:
    store, args = built_state(tmp_path, capsys)
    end = "⟦end group by_definition_the_gain⟧"
    sharp = "⟦b8 claim⟧\nNext, the bound is sharp.\n\\end{proof}"

    given = given_on(
        store,
        Layer.GROUPS,
        lambda t: t.replace(f"{sharp}\n\n{end}", f"{end}\n\n{sharp}"),
        asking(store),
    )

    assert given.state is not None, given.unanswered
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


@pytest.mark.usefixtures("develop_mode")
def test_a_cut_edit_on_an_update_s_state_is_what_its_re_run_gives(
    tmp_path, capsys
) -> None:
    root, store = updated_state(tmp_path, capsys)
    edited = "A second sentence of the setting, edited."

    given = given_on(
        store,
        Layer.CUT,
        lambda t: t.replace(edited, f"⟦new — its own sentence⟧\n{edited}"),
        asking(store),
    )

    assert given.state is not None, given.unanswered
    kept = versions.states(store)
    out = tmp_path / "rerun.json"
    capsys.readouterr()
    assert (
        main(["update", "--json", str(root), "--host", "none", "-o", str(out)]) == 0
    ), capsys.readouterr().err
    capsys.readouterr()
    assert json.loads(out.read_bytes()) == json.loads(
        versions.kept_export(store, given.state)
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)
    assert versions.states(store) == kept


@pytest.mark.usefixtures("develop_mode")
def test_a_roles_edit_on_an_update_s_state_is_what_its_re_run_gives(
    tmp_path, capsys
) -> None:
    root, store = updated_state(tmp_path, capsys)

    given = roles_edited(store, asking(store))

    assert given.state is not None, given.unanswered
    out = tmp_path / "rerun.json"
    capsys.readouterr()
    assert (
        main(["update", "--json", str(root), "--host", "none", "-o", str(out)]) == 0
    ), capsys.readouterr().err
    capsys.readouterr()
    assert out.read_bytes() == versions.kept_export(store, given.state)


@pytest.mark.usefixtures("develop_mode")
def test_an_agent_s_roles_edit_is_applied_but_may_not_change_a_person_s_pin(
    tmp_path, capsys
) -> None:
    store, args = built_state(tmp_path, capsys)
    assert roles_edited(store, asking(store)).state is not None
    answers = (store.directory / RECORD).read_text(encoding="utf-8")

    definition = handle_ids(json.loads(latest_export(store)))[DEFINITION]
    with pytest.raises(EditError, match=f"what a person pinned: {definition}$"):
        given_on(
            store,
            Layer.ROLES,
            lambda t: t.replace("⟦b3 assumption⟧", "⟦b3 definition⟧"),
            asking(store),
            agent("m", "s"),
        )
    assert (store.directory / RECORD).read_text(encoding="utf-8") == answers

    given = given_on(
        store,
        Layer.ROLES,
        lambda t: t.replace("⟦b9 none⟧", "⟦b9 motivation⟧"),
        asking(store),
        agent("m", "s"),
    )

    assert given.state is not None, given.unanswered
    export = json.loads(versions.kept_export(store, given.state))
    assert [a.get("by", "person") for a in export["review"]["person"]["applied"]] == [
        "person",
        "agent",
    ]
    assert {p["layer"] for p in export["review"]["person"]["pins"]} == {"roles"}
    assert len(export["review"]["person"]["pins"]) == 1  # the agent pins nothing
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


def test_edit_prints_what_it_waits_for_and_the_same_command_resumes(
    tmp_path, capsys
) -> None:
    store, _ = built_state(tmp_path, capsys)
    with edits.opened(store, versions.latest(store)) as kept:
        text = edits.shown(kept, Layer.ROLES).replace(*TO_ASSUMPTION)
    edited = tmp_path / "roles.md"
    edited.write_text(text, encoding="utf-8")
    args = [*kept_args(store, tmp_path), "--stage", "roles", "--file", str(edited)]

    capsys.readouterr()
    code = main(["edit", *args])
    out = capsys.readouterr().out

    assert code == Exit.UNANSWERED
    assert "not whole, no state made: unanswered" in out
    assert "give --profile and --budget to ask" in out
    assert main(["edit", *args]) == Exit.UNANSWERED  # the same command resumes


def test_a_roles_edit_s_dry_run_prices_what_it_would_ask(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    with edits.opened(store, versions.latest(store)) as kept:
        text = edits.shown(kept, Layer.ROLES).replace(*TO_ASSUMPTION)
    edited = tmp_path / "roles.md"
    edited.write_text(text, encoding="utf-8")
    args = [*kept_args(store, tmp_path), "--stage", "roles", "--file", str(edited)]
    before = store_files(store)

    capsys.readouterr()
    code = main(["edit", *args, "--dry-run", "--json"])
    out = capsys.readouterr()

    assert code == 0, out.err
    assert store_files(store) == before
    printed = json.loads(out.out)
    assert printed["dry_run"] is True and printed["state"] is None
    asks = printed["asks"]["items"]
    assert asks and printed["estimate"] > 0
    # the total is printed to six places, the items as they are
    assert printed["estimate"] == pytest.approx(
        sum(a["estimate"] for a in asks), abs=5e-7
    )
    assert {a["stage"] for a in asks} <= {"roles", "groups", "edges"}


def test_an_edit_on_a_state_not_its_version_s_latest_is_refused(
    tmp_path, capsys
) -> None:
    store, _ = built_state(tmp_path, capsys)
    first = versions.latest(store)
    assert give(store, adding(ADDED)).state is not None
    answers = (store.directory / RECORD).read_text(encoding="utf-8")

    with (
        pytest.raises(EditError, match="is not its version's latest"),
        edits.opened(store, first) as kept,
    ):
        edits.give(kept, Layer.EDGES, edited_edges(edits.shown(kept, Layer.EDGES)))

    assert (store.directory / RECORD).read_text(encoding="utf-8") == answers


def test_a_fragment_s_retract_needs_a_reason(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    fragment = tmp_path / "fragment.md"
    fragment.write_text(f"⟦closing-words-of-the⟧\n- {SCOPE}\n", encoding="utf-8")

    code, _, err = changed(
        kept_args(store, tmp_path), capsys, "--fragment", str(fragment)
    )

    assert code == 1 and "needs --reason" in err


def test_an_answer_a_state_found_stale_is_not_pending(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    key = roles_edited(store).recorded.answer.key
    kept = versions.latest(store)
    assert [a.key for a in versions.pending_answers(store, kept.version)] == [key]
    export = json.loads(versions.kept_export(store, kept))
    export["review"]["person"] = {"applied": [], "stale": [key], "pins": []}
    files = {path: versions.blob(store, name) for path, name in kept.files.items()}
    versions.record(
        store,
        files,
        kept.version,
        kept.root,
        dumps(export).encode("utf-8"),
        Command.EDIT,
        started_from=kept,
    )

    assert versions.pending_answers(store, kept.version) == ()


@pytest.mark.usefixtures("develop_mode")
def test_a_flag_the_edit_s_claim_role_decides_is_decided_as_a_replay_decides(
    tmp_path, capsys
) -> None:
    """A model's `? role` flag on the closing paragraph, then a person's
    standing there that adds its claim role: the flag decided by
    that correction, as the replay decides it."""
    from questions.test_edges_file import WRITTEN as LINES

    flagged = {
        **LINES,
        "closing-words-of-the": [
            *LINES["closing-words-of-the"],
            '? role "Closing words of the results." — a claim?',
        ],
    }
    store, args = built_state(tmp_path, capsys, flagged)

    given = give(store, adding(CLOSING_CLAIM))

    assert given.state is not None, given.problems
    export = json.loads(versions.kept_export(store, given.state))
    (correction,) = export["review"]["cut_corrections"]
    (flag,) = [f for f in export["review"]["flags"] if f["signal"] == "role"]
    assert flag["decided"] == correction["id"]
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


@pytest.mark.usefixtures("develop_mode")
def test_a_second_person_edit_over_pins_is_what_a_replay_gives(
    tmp_path, capsys
) -> None:
    store, args = built_state(tmp_path, capsys)
    assert give(store, edited_edges).state is not None

    given = give(
        store,
        lambda t: t.replace(WROTE + "\n", "").replace(SCOPE, "= " + SCOPE),
        reason="the theorem is not used here",
    )

    assert given.state is not None, given.problems
    export = json.loads(versions.kept_export(store, given.state))
    assert [
        (p["kind"], p["line"], p["rank"]) for p in export["review"]["person"]["pins"]
    ] == [
        ("deleted", DROPPED, 1),
        ("deleted", WROTE, 2),
        ("kept", SCOPE, 2),
    ]
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


@pytest.mark.usefixtures("develop_mode")
def test_a_standing_a_person_changes_names_the_person_the_rest_the_model(
    tmp_path, capsys
) -> None:
    """A self mark and a standing name the answer that
    first wrote them, as an edge does."""
    store, args = built_state(tmp_path, capsys)
    before = json.loads(latest_export(store))
    model = before["edges_answer"]

    given = give(
        store,
        lambda t: t.replace(
            '⊢ unsupported · "Next, the bound is sharp."',
            '⊢ conjectured · "Next, the bound is sharp."',
        ),
    )

    assert given.state is not None, given.problems
    export = json.loads(versions.kept_export(store, given.state))
    person = given.recorded.answer.key
    authors = {
        (s["status"], s["provenance"]["by"], s["provenance"]["question"])
        for s in export["standings"]
    }
    assert ("conjectured", "person", person) in authors
    assert {q for status, _, q in authors if status != "conjectured"} == {model}
    assert {m["provenance"]["question"] for m in export["self"]} == {model}
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


def test_an_agent_may_not_make_again_a_group_a_person_dissolved(
    tmp_path, capsys
) -> None:
    store, _ = built_state(tmp_path, capsys)
    start = "⟦group proof by_definition_the_gain⟧\n\n"
    end = "\n\n⟦end group by_definition_the_gain⟧"

    def dissolved(text: str) -> str:
        return text.replace(start, "").replace(end, "")

    assert given_on(store, Layer.GROUPS, dissolved, asking(store)).state is not None
    answers = (store.directory / RECORD).read_text(encoding="utf-8")

    def made_again(text: str) -> str:
        first = "⟦b6 justification⟧"
        last = "Next, the bound is sharp.\n\\end{proof}"
        return text.replace(first, start + first).replace(last, last + end)

    with pytest.raises(EditError, match="what a person pinned: proof@"):
        given_on(store, Layer.GROUPS, made_again, asking(store), agent("m", "s"))
    assert (store.directory / RECORD).read_text(encoding="utf-8") == answers


#: The proof's justifies line, on its first block once the group is gone.
PROOF_LINE = '→ justifies thm:bound name · "Hence the output is bounded."'


def regrouped(store: RecordStore, keep_member: bool, keep_mark: bool = False):
    """The proof's group dissolved, its justifies line left on its first
    block, then made again by a person: the re-ask gives the group the line
    onto the theorem, and keeps the member's (`keep_member`) or drops it.
    With `keep_mark`, a person kept the member's line before."""
    start = "⟦group proof by_definition_the_gain⟧\n\n"
    end = "\n\n⟦end group by_definition_the_gain⟧"
    dissolved = given_on(
        store,
        Layer.GROUPS,
        lambda t: t.replace(start, "").replace(end, ""),
        asking(store),
    )
    assert dissolved.state is not None
    if keep_mark:
        kept = give(store, lambda t: t.replace(PROOF_LINE, "= " + PROOF_LINE))
        assert kept.state is not None, kept.problems
    asked: list[str] = []

    def reask(text: str) -> str:
        asked.append(text)
        closing = re.search(r"⟦end group (\S+)⟧\n", text)
        assert closing is not None
        text = text[: closing.end()] + f"\n{PROOF_LINE}\n" + text[closing.end() :]
        if not keep_member:
            text = text.replace(PROOF_LINE + "\n", "", 1)
        return done(text)

    first = "⟦b6 justification⟧"
    last = "Next, the bound is sharp.\n\\end{proof}"
    runner = Runner(person_host(reask=reask), Resolver(store), 10.0, FLAT)
    made = given_on(
        store,
        Layer.GROUPS,
        lambda t: t.replace(first, start + first).replace(last, last + end),
        runner,
    )
    return made, asked


def justifying(store: RecordStore, state) -> list[str]:
    """The sources of the justifies edges onto the theorem, by handle."""
    export = json.loads(versions.kept_export(store, state))
    handles = export["handles"]
    bound = handle_ids(export)["thm:bound"]
    return sorted(
        handles[e["source"]]
        for e in export["edges"]
        if e["kind"] == "justifies" and e["target"].get("block") == bound
    )


def test_a_group_made_again_takes_its_members_justifies_line(tmp_path, capsys) -> None:
    """A groups edit reopens the member that holds a justifies line, and
    the gate refuses the member's line onto the target the group's line
    names: dropped, the group alone justifies the theorem."""
    store, _ = built_state(tmp_path, capsys)
    made, asked = regrouped(store, keep_member=False)
    assert made.state is not None, made.unanswered
    # the member is reopened: shown without its done mark
    assert "⟦by-definition-def:gain-the justification+claim⟧" in asked[0]
    assert "⟦done by-definition-def:gain-the⟧" not in asked[0]
    assert justifying(store, made.state) == ["group:by-definition-def:gain-the-2"]


def test_a_member_s_line_beside_its_group_s_is_refused(tmp_path, capsys) -> None:
    store, _ = built_state(tmp_path, capsys)
    made, _ = regrouped(store, keep_member=True)
    assert made.state is None
    problems = [
        p
        for a in store.answers()
        if a.stage == "edges" and not a.valid
        for p in a.problems
    ]
    assert any("the group's line carries its members'" in p for p in problems)


def test_a_member_s_line_a_person_kept_stands_beside_its_group_s(
    tmp_path, capsys
) -> None:
    store, _ = built_state(tmp_path, capsys)
    made, _ = regrouped(store, keep_member=True, keep_mark=True)
    assert made.state is not None, made.unanswered
    assert justifying(store, made.state) == [
        "by-definition-def:gain-the",
        "group:by-definition-def:gain-the-2",
    ]


def test_a_state_kept_with_both_lines_takes_an_edit_elsewhere(
    tmp_path, capsys, monkeypatch
) -> None:
    """A state kept before the group rule holds the proof's first block's
    own line onto the theorem beside the group's: a person's edit of
    another line is not refused for it, nor an agent's."""
    from axiomation.questions import edges_file

    store, _ = built_state(tmp_path, capsys)
    self_line = '⊢ self · "Hence the output is bounded."'
    line = '→ justifies thm:bound · "Hence the output is bounded."'
    with monkeypatch.context() as before_the_rule:
        before_the_rule.setattr(edges_file, "group_line_problems", lambda *a, **k: [])
        doubled = give(
            store,
            lambda t: t.replace(self_line, f"{self_line}\n{line}"),
            agent("m", "s"),
        )
    assert doubled.state is not None, doubled.problems
    other = '→ uses thm:bound · "Closing words of the results."'
    for added, author in ((ADDED, A_PERSON), (other, agent("m", "t"))):
        edited = give(store, adding(added), author)
        assert edited.state is not None, edited.problems


def test_a_person_writes_a_member_s_line_beside_its_group_s_only_kept(
    tmp_path, capsys
) -> None:
    """A person's edges edit adding the proof's first block's own line onto
    the theorem is refused, told of the keep mark; kept by it, it stands."""
    store, _ = built_state(tmp_path, capsys)
    self_line = '⊢ self · "Hence the output is bounded."'
    line = '→ justifies thm:bound · "Hence the output is bounded."'
    refused = give(store, lambda t: t.replace(self_line, f"{self_line}\n{line}"))
    assert refused.state is None
    assert any("a person keeps it with the keep mark" in p for p in refused.problems)
    kept = give(store, lambda t: t.replace(self_line, f"{self_line}\n= {line}"))
    assert kept.state is not None, kept.problems
    assert justifying(store, kept.state) == [
        "by-definition-def:gain-the",
        "group:by-definition-def:gain-the",
    ]


@pytest.mark.usefixtures("develop_mode")
@pytest.mark.parametrize(
    ("layer", "edit"),
    [
        (Layer.ROLES, lambda t: t.replace(*TO_ASSUMPTION)),
        (
            Layer.CUT,
            lambda t: t.replace(
                "A second sentence of the setting, edited.",
                "⟦new — its own sentence⟧\nA second sentence of the setting, edited.",
            ),
        ),
    ],
    ids=["roles", "cut"],
)
def test_an_edit_on_an_update_that_carried_pins_asks_what_its_re_run_asks(
    tmp_path, layer, edit
) -> None:
    """OLD's person pins carried onto NEW, the update kept as a state, then
    a person's roles edit whose re-ask holds the carried edges pins: the
    update re-run with that answer gives the edit's export byte for byte."""
    from axiomation.exchange import dumps as dumped
    from axiomation.questions.edges_file import EDGES_FILE
    from axiomation.reader import document_hash
    from axiomation.runner.hosts import Rates
    from axiomation.update import run, update_payload
    from runner.fakes import FakeHost
    from test_person import CLOSING, versions_with_pins
    from test_update import UPDATE, without_qualifies
    from test_update import answering as answering_update

    _, new, store = versions_with_pins(tmp_path)
    answer = answering_update(
        {
            "we-fix-a-field": ["Δ narrowed — the setting now says less"],
            DEFINITION: ['Δ unchanged — "The gain is the ratio": it reads the same'],
            CLOSING: ["Δ narrowed — it now closes less"],
        },
        without_qualifies,
    )
    fake = FakeHost(lambda p: "", edits={EDGES_FILE: answer})
    found = run(
        new.document,
        store,
        UPDATE,
        Runner(fake, Resolver(store), 10.0, Rates(input=0.0, output=1.0)),
    )
    assert found.complete, found.unanswered
    payload = update_payload(new.document, found, store)
    versions.record(
        store,
        versions.snapshot(new.root, new.document),
        document_hash(new.document),
        new.document.root,
        dumped(payload).encode("utf-8"),
        Command.UPDATE,
        identity=found.identity.key,
    )

    with edits.opened(store, versions.latest(store), UPDATE.review) as kept:
        edited = edit(edits.shown(kept, layer))
        assert edited != edits.shown(kept, layer)
        given = edits.give(kept, layer, edited, runner=asking(store))

    assert given.state is not None, given.unanswered
    replayed = update_payload(new.document, run(new.document, store, UPDATE), store)
    assert dumped(replayed).encode("utf-8") == versions.kept_export(store, given.state)


#: The setting's edited sentence made a block of its own on an update's
#: state (`updated_state`'s paper).
EDITED_SPLIT = (
    "A second sentence of the setting, edited.",
    "⟦new — its own sentence⟧\nA second sentence of the setting, edited.",
)


@pytest.mark.usefixtures("develop_mode")
@pytest.mark.parametrize(
    ("layer", "edit"),
    [
        (
            Layer.ROLES,
            lambda t: re.sub(
                r"⟦(b\d+) definition⟧(?=\n\\begin\{definition\})",
                r"⟦\1 assumption⟧",
                t,
                count=1,
            ),
        ),
        (
            Layer.CUT,
            lambda t: re.sub(
                r"\n\n⟦b\d+ [^\n]*\n(?=Next, the bound)", "\n", t, count=1
            ),
        ),
        (
            Layer.GROUPS,
            lambda t: t.replace(
                "⟦group derivation by_definition_the_gain⟧\n\n", ""
            ).replace("\n\n⟦end group by_definition_the_gain⟧", ""),
        ),
    ],
    ids=["roles", "cut merge", "groups"],
)
def test_an_edit_on_an_update_s_state_a_cut_edit_changed_is_what_its_re_run_gives(
    tmp_path, capsys, layer, edit
) -> None:
    """The update's cut changed by a person's split (a round of its own in
    the re-run), then a second edit on that state: the kept cut is not the
    update's rebased one, so its `review.update` is taken onto the final cut
    from a cut a round already changed."""
    root, store = updated_state(tmp_path, capsys)
    first = given_on(
        store, Layer.CUT, lambda t: t.replace(*EDITED_SPLIT), asking(store)
    ).state
    assert first is not None

    given = given_on(store, layer, edit, asking(store))

    assert given.state is not None, given.unanswered
    assert given.state.parent == first.id
    kept = versions.states(store)
    out = tmp_path / "rerun.json"
    capsys.readouterr()
    assert (
        main(["update", "--json", str(root), "--host", "none", "-o", str(out)]) == 0
    ), capsys.readouterr().err
    capsys.readouterr()
    assert json.loads(out.read_bytes()) == json.loads(
        versions.kept_export(store, given.state)
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)
    assert versions.states(store) == kept


@pytest.mark.usefixtures("develop_mode")
def test_an_edit_clears_a_stale_mark_after_a_split_as_the_re_run_does(
    tmp_path,
) -> None:
    """An update of one session whose Δs left owners stale, kept
    as a state; a person's split of the changed setting, then a roles edit
    whose re-ask reopens the sharpness remark, stale until then: the update
    re-run with both answers gives the last edit's export byte for byte,
    its stale marks named on the final cut and the remark's cleared by the
    re-ask."""
    from axiomation.exchange import dumps as dumped
    from axiomation.reader import document_hash
    from axiomation.update import update_payload
    from test_update import SHARP
    from test_update import host as update_host
    from test_update import update as run_once
    from test_update_single import CARRIED_SETTINGS as PLAIN_SETTINGS
    from test_update_single import as_assumption, chain

    v, _ = chain(tmp_path)
    narrowed = {"we-fix-a-field": ["Δ narrowed — scalars of a subfield only"]}
    made = run_once(v, update_host(answering(narrowed, as_assumption)), PLAIN_SETTINGS)
    assert made.complete
    payload = update_payload(v.new.document, made, v.store)
    assert SHARP in {s["handle"] for s in payload["review"]["update"]["stale"]}
    versions.record(
        v.store,
        versions.snapshot(v.new.root, v.new.document),
        document_hash(v.new.document),
        v.new.document.root,
        dumped(payload).encode("utf-8"),
        Command.UPDATE,
        identity=made.identity.key,
    )
    settings = PLAIN_SETTINGS.review

    def given(layer: Layer, edit) -> edits.Given:
        with edits.opened(v.store, versions.latest(v.store), settings) as kept:
            edited = edit(edits.shown(kept, layer))
            assert edited != edits.shown(kept, layer)
            return edits.give(kept, layer, edited, runner=asking(v.store))

    assert given(Layer.CUT, lambda t: t.replace(*EDITED_SPLIT)).state is not None
    last = given(
        Layer.ROLES,
        lambda t: re.sub(
            r"⟦(b\d+) claim⟧\nNext, the bound is sharp\.",
            r"⟦\1 motivation⟧\nNext,\n⟦+ claim⟧\nthe bound is sharp.",
            t,
            count=1,
        ),
    )

    assert last.state is not None, last.unanswered
    export = json.loads(versions.kept_export(v.store, last.state))
    cleared = {
        s["handle"]: s["cleared_by"] for s in export["review"]["update"]["stale"]
    }
    assert cleared[SHARP] is not None
    replayed = update_payload(
        v.new.document, run_once(v, None, PLAIN_SETTINGS), v.store
    )
    assert dumped(replayed).encode("utf-8") == versions.kept_export(v.store, last.state)


@pytest.mark.usefixtures("develop_mode")
def test_an_edit_that_reasks_nothing_keeps_the_state_s_edges_pins(
    tmp_path, capsys, monkeypatch
) -> None:
    """Under the type rule every roles edit reopens its own block; with
    a rule that reopens nothing (patched, here and in the replay), the edit
    asks nothing, the edges stay as they were, and so do a person's pins on
    them: the replay decides them on the same sheet."""
    from axiomation.questions import edges_file

    store, args = built_state(tmp_path, capsys)
    assert give(store, edited_edges).state is not None
    pinned = [
        (p["kind"], p["line"])
        for p in json.loads(latest_export(store))["review"]["person"]["pins"]
    ]
    assert pinned == [("deleted", DROPPED), ("written", WROTE)]
    monkeypatch.setattr(edges_file, "reopened_by_roles", lambda *a, **k: {})

    given = roles_edited(store)

    assert given.state is not None and given.reports == ()
    export = json.loads(versions.kept_export(store, given.state))
    assert [(p["layer"], p["kind"]) for p in export["review"]["person"]["pins"]] == [
        ("roles", "changed"),
        ("edges", "deleted"),
        ("edges", "written"),
    ]
    out = tmp_path / "replay.json"
    capsys.readouterr()
    assert (
        main(["build", "--json", *args, "--host", "none", "--replay", "-o", str(out)])
        == 0
    )
    assert out.read_bytes() == versions.kept_export(store, given.state)


# --- the vocabulary map corrected on a kept state ---------------------------------

#: `extra.tex` with a frame of two laws the paper never declares.
BOXED = (
    EXTRA + "\n\\begin{lawbox}\nThe first boxed law.\n\nThe second boxed law.\n"
    "\\end{lawbox}\n"
)
WRONG = '- kind: none\n- evidence: "The first boxed law."'
#: The update session's Δ on each block the corrected map cut out of the frame.
SPLIT_BOX = {
    "the-first-boxed-law": ["Δ reworded — read apart from the frame"],
    "the-second-boxed-law": ["Δ reworded — read apart from the frame"],
}


def boxed_state(tmp_path: Path, capsys) -> tuple[Path, RecordStore]:
    """The synthetic paper with its frame, mapped `none` (the check round
    confirming it), built into its own store and kept by the command."""
    paper = make_paper(tmp_path / "paper", extra=BOXED)
    store = RecordStore(paper.root / STORE_DIRECTORY)
    mapping = FakeHost(
        lambda p: "",
        edits={
            "vocabulary.md": lambda t: t.replace("- kind: ?\n- evidence: ?", WRONG),
            "vocabulary-check.md": lambda t: t.replace(
                "- verdict: ?", "- verdict: confirm"
            ),
        },
    )
    runner = Runner(mapping, Resolver(store), 10.0, FLAT)
    mapped = map_vocabulary(paper.document, store, Templates.shipped(), runner)
    assert mapped.vocabulary is not None
    document = read_paper(paper.root, vocabulary=mapped.vocabulary)
    review = review_cut(
        document,
        cut(document, CutRules(DisplayRule.IN_PARAGRAPH)),
        store,
        ReviewSettings(stages=BUILD_STAGES, files=FILES),
        Runner(build_host(), Resolver(store), 10.0, FLAT),
    )
    assert review.complete, review.unanswered
    capsys.readouterr()
    assert main(["build", "--json", str(paper.root), "--host", "none"]) == 0
    capsys.readouterr()
    return paper.root, store


def blocks_of(store: RecordStore, state) -> list[tuple[str, str]]:
    """The state's blocks, each its id and its text."""
    document = versions.document_at(store, state)
    export = json.loads(versions.kept_export(store, state))
    found = cut_from_payload(
        document, export, CutRules(DisplayRule.IN_PARAGRAPH), roles=True
    )
    return [
        (b.id, " ".join(document.text(run) for run in b.owned).strip())
        for b in found.blocks
    ]


def boxes(store: RecordStore, state) -> list[str]:
    """The texts of the state's blocks inside the frame."""
    return [text for _, text in blocks_of(store, state) if "boxed law" in text]


def test_a_vocabulary_edit_s_dry_run_prices_its_update_and_records_nothing(
    tmp_path, capsys
) -> None:
    root, store = boxed_state(tmp_path, capsys)
    with edits.opened(store, versions.latest(store)) as kept:
        shown = edits.vocabulary_shown(kept)
    sheet = tmp_path / "vocabulary.md"
    sheet.write_text(shown.replace("- kind: none", "- kind: container"), "utf-8")
    before = store_files(store)

    capsys.readouterr()
    code = main(
        ["edit", str(root), "--stage", "vocabulary", "--file", str(sheet), "--dry-run"]
    )
    out = capsys.readouterr()

    assert code == 0, out.err
    assert "dry run on a scratch copy of the store: nothing recorded" in out.err
    priced = out.out[out.out.index("priced, nothing asked") :].splitlines()
    session = next(line for line in priced if line.startswith("  session: "))
    assert session.startswith("  session: 1 question, $")  # none answered yet
    assert priced[-1].startswith("estimate $")
    assert store_files(store) == before


def corrected_state(tmp_path: Path, capsys) -> tuple[Path, RecordStore, Path]:
    """`boxed_state` with its frame mapped `container` by a person's edit,
    what it reopens answered: the paper, its store and the edited file."""
    root, store = boxed_state(tmp_path, capsys)
    first = versions.latest(store)
    with edits.opened(store, first) as kept:
        shown = edits.vocabulary_shown(kept)
    sheet = tmp_path / "vocabulary.md"
    sheet.write_text(shown.replace("- kind: none", "- kind: container"), "utf-8")
    edit = ["edit", str(root), "--stage", "vocabulary", "--file", str(sheet)]
    capsys.readouterr()
    assert main(edit) == Exit.UNANSWERED
    identity = [a for a in store.answers() if a.kind == "identity"][-1]
    corrected = map_vocabulary(
        read_paper(root),
        store,
        Templates.shipped(),
        kept=vocabulary_of(json.loads(versions.kept_export(store, first))),
    )
    run_update(
        read_paper(root, vocabulary=corrected.vocabulary),
        store,
        UpdateSettings(
            files=FILES,
            review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
            paper=root.resolve(),
        ),
        Runner(stepping(answering(SPLIT_BOX)), Resolver(store), 10.0, FLAT),
        identity=identity.key,
        applied_before=first.answers,
    )
    assert main(edit) == 0
    capsys.readouterr()
    return root, store, sheet


@pytest.mark.usefixtures("develop_mode")
def test_an_agent_may_not_change_a_person_s_map_answer(tmp_path, capsys) -> None:
    _, store, sheet = corrected_state(tmp_path, capsys)
    answers = (store.directory / RECORD).read_text(encoding="utf-8")

    with edits.opened(store, versions.latest(store)) as kept:
        back = edits.vocabulary_shown(kept).replace("- kind: container", "- kind: none")
        with pytest.raises(
            EditError, match="may not change what a person pinned: environment lawbox"
        ):
            edits.give_vocabulary(kept, back, agent("m", "s"))

    assert (store.directory / RECORD).read_text(encoding="utf-8") == answers


def test_an_agent_may_not_re_cut_a_block_a_person_pinned(tmp_path, capsys) -> None:
    _, store = boxed_state(tmp_path, capsys)
    pinned = given_on(
        store,
        Layer.ROLES,
        lambda t: t.replace("⟦b13 definition⟧", "⟦b13 motivation⟧"),
        Runner(
            FakeHost(lambda p: "", edits={"roles.md": lambda t: t, "edges.md": done}),
            Resolver(store),
            10.0,
            FLAT,
        ),
    )
    assert pinned.state is not None, pinned.unanswered

    with edits.opened(store, versions.latest(store)) as kept:
        container = edits.vocabulary_shown(kept).replace(
            "- kind: none", "- kind: container"
        )
        with pytest.raises(
            EditError, match="may not change what a person pinned: roles"
        ):
            edits.give_vocabulary(kept, container, agent("m", "s"))
        # a person's own edit may: the pin lapses with the block it held
        assert edits.give_vocabulary(kept, container).answer.host == PERSON


@pytest.mark.usefixtures("develop_mode")
def test_a_given_map_is_the_version_s_and_stale_once_its_file_moves(
    tmp_path, capsys, monkeypatch
) -> None:
    root, store, _ = corrected_state(tmp_path, capsys)
    first, second = versions.states(store)
    (given,) = [
        a for a in store.answers() if a.stage == "vocabulary" and a.host == PERSON
    ]
    # the given map is its records and the file it was shown, never its text
    assert given.shown is not None and store.vocabulary_of(given.key)

    def unread(question, raw):
        raise AssertionError("a given map's text read again")

    monkeypatch.setattr(vocabulary_file, "read_answer", unread)

    # a rebuild of the version reads the model's map and lays the given one
    # over it, as it replays every edit given on the version
    rebuilt = map_vocabulary(read_paper(root), store, Templates.shipped())
    # the state it made carries the map; its file shows that map, so the
    # given answer, shown the map before it, is stale there and changes
    # nothing
    carried = map_vocabulary(
        read_paper(root),
        store,
        Templates.shipped(),
        kept=vocabulary_of(json.loads(versions.kept_export(store, second))),
    )

    for found in (rebuilt, carried):
        assert found.vocabulary is not None
        assert [a.kind for a in found.vocabulary.answers] == ["container"]
    assert (rebuilt.given, rebuilt.stale) == ((given.key,), ())
    assert (carried.given, carried.stale) == ((), (given.key,))


def test_the_same_map_given_again_records_nothing(tmp_path, capsys) -> None:
    root, store = boxed_state(tmp_path, capsys)
    with edits.opened(store, versions.latest(store)) as kept:
        shown = edits.vocabulary_shown(kept)
        with pytest.raises(EditError, match="nothing to record"):
            edits.give_vocabulary(kept, shown)
        reworded = shown.replace('"The first boxed law."', '"The second boxed law."')
        with pytest.raises(EditError, match="the map reads the same"):
            edits.give_vocabulary(kept, reworded)


@pytest.mark.usefixtures("develop_mode")
def test_a_map_corrected_on_a_kept_state_re_cuts_its_frame_alone(
    tmp_path, capsys
) -> None:
    root, store = boxed_state(tmp_path, capsys)
    first = versions.latest(store)
    assert boxes(store, first) == [
        "\\begin{lawbox}\nThe first boxed law.\n\nThe second boxed law.\n\\end{lawbox}"
    ]
    sheet = tmp_path / "vocabulary.md"
    assert main(["sheet", str(root), "--stage", "vocabulary", "-o", str(sheet)]) == 0
    shown = sheet.read_text(encoding="utf-8")
    assert WRONG in shown
    sheet.write_text(shown.replace("- kind: none", "- kind: container"), "utf-8")
    edit = ["edit", str(root), "--stage", "vocabulary", "--file", str(sheet)]

    capsys.readouterr()
    # the frame's blocks are re-cut: their roles and edges are asked
    assert main(edit) == Exit.UNANSWERED
    (given,) = versions.pending_answers(store, first.version)
    assert (given.stage, given.host) == ("vocabulary", PERSON)
    identity = [a for a in store.answers() if a.kind == "identity"][-1]
    corrected = map_vocabulary(
        read_paper(root),
        store,
        Templates.shipped(),
        kept=vocabulary_of(json.loads(versions.kept_export(store, first))),
    )
    assert corrected.given == (given.key,)
    answered = run_update(
        read_paper(root, vocabulary=corrected.vocabulary),
        store,
        UpdateSettings(
            files=FILES,
            review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
            paper=root.resolve(),
        ),
        Runner(stepping(answering(SPLIT_BOX)), Resolver(store), 10.0, FLAT),
        identity=identity.key,
        applied_before=first.answers,
    )
    assert answered.complete, answered.unanswered
    capsys.readouterr()
    assert main(edit) == 0  # the same command resumes, and keeps the state

    second = versions.latest(store)
    assert (second.command, second.parent, second.version) == (
        Command.EDIT,
        first.id,
        first.version,
    )
    assert given.key in second.answers
    assert versions.pending_answers(store, first.version) == ()
    # the frame cut as the corrected map reads it, a block per law
    fresh = read_paper(root, vocabulary=corrected.vocabulary)
    assert boxes(store, second) == [
        " ".join(fresh.text(run) for run in b.owned).strip()
        for b in cut(fresh, CutRules(DisplayRule.IN_PARAGRAPH)).blocks
        if "boxed law" in " ".join(fresh.text(run) for run in b.owned)
    ]
    assert len(boxes(store, second)) == 2
    # every block outside the frame is the state's as it was
    outside = [b for b in blocks_of(store, first) if "boxed law" not in b[1]]
    assert len(outside) > 5
    assert [b for b in blocks_of(store, second) if "boxed law" not in b[1]] == outside
    # the corrected map is the state's, carried onto the next version
    mapped = vocabulary_of(json.loads(versions.kept_export(store, second)))
    (lawbox,) = mapped.answers
    assert (lawbox.kind, lawbox.by) == ("container", PERSON)


def test_judge_propose_files_a_person_s_reading_that_changes_no_status(
    tmp_path, capsys
) -> None:
    """`judge --propose`: the person's reading is filed as a proposal, says
    so, and the subject's status is unchanged."""
    from axiomation import queries

    store, _ = built_state(tmp_path, capsys)
    args = kept_args(store, tmp_path)
    capsys.readouterr()
    code = main(
        [
            "judge",
            *args,
            "--aspect",
            "true",
            "--on",
            "thm:bound",
            "--verdict",
            "verified",
            "--depth",
            "informal",
            "--evidence",
            "the proof bounds it",
            "--propose",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "proposed verified by person" in out
    (record,) = store.judgment_records().judgments
    assert record.proposed
    view = queries.View(store, versions.latest(store))
    (status,) = [s for s in view.readings.statuses if s.proposal is not None]
    assert status.status.value == "unjudged" and status.proposal.asks


@pytest.mark.signed
def test_a_person_s_edit_takes_effect_under_their_word(tmp_path, capsys) -> None:
    """An edit filed in the person's name waits for their confirmation,
    and its state with it: the latest state is the one before until they
    confirm; an edit they drop never takes effect."""
    store, _ = built_state(tmp_path, capsys)
    store.confirm()
    before = versions.latest(store)

    given = give(store, edited_edges)
    assert given.state is not None
    assert versions.latest(RecordStore(store.directory)) == before

    RecordStore(store.directory).confirm([given.recorded.answer.key])
    assert versions.latest(RecordStore(store.directory)) == before

    again = give(RecordStore(store.directory), edited_edges)
    RecordStore(store.directory).confirm()
    assert versions.latest(RecordStore(store.directory)).id == again.state.id


@pytest.mark.signed
def test_a_second_edit_before_confirm_builds_on_the_first(tmp_path, capsys) -> None:
    """A person's edit waits for their word, out of every read; their next
    edit in the same sitting builds on it, and once they confirm, the
    latest holds both (`docs/architecture.md` § 4, "The person's word")."""
    store, _ = built_state(tmp_path, capsys)
    built = versions.latest(store)
    args = [*kept_args(store, tmp_path), "--stage", "edges"]
    sheet = tmp_path / "edges.md"

    assert main(["sheet", *args, "-o", str(sheet)]) == 0
    sheet.write_text(edited_edges(sheet.read_text(encoding="utf-8")), encoding="utf-8")
    assert main(["edit", *args, "--file", str(sheet)]) == 0
    first = versions.latest(store, waiting=True)
    assert versions.latest(store) == built  # waiting for the person's word

    assert main(["sheet", *args, "-o", str(sheet)]) == 0
    shown = sheet.read_text(encoding="utf-8")
    assert WROTE in shown and DROPPED not in shown  # the first edit's sheet
    sheet.write_text(adding(ADDED)(shown), encoding="utf-8")
    assert main(["edit", *args, "--file", str(sheet)]) == 0
    second = versions.latest(store, waiting=True)
    assert (first.parent, second.parent) == (built.id, first.id)
    assert versions.latest(store) == built

    # the first dropped alone would leave the second confirmed on a state
    # no view holds: refused, naming it
    capsys.readouterr()
    (key,) = first.answers
    assert main(["confirm", *kept_args(store, tmp_path), "--drop", key]) == 1
    assert "built on one you drop" in capsys.readouterr().err
    assert main(["confirm", *kept_args(store, tmp_path)]) == 0
    assert versions.latest(store) == second
    export = json.loads(versions.kept_export(store, second))
    assert [(p["kind"], p["line"]) for p in export["review"]["person"]["pins"]] == [
        ("deleted", DROPPED),
        ("written", WROTE),
        ("written", ADDED),
    ]
    nodes = [n.node for n in second.nodes]
    assert len(nodes) == len(set(nodes))


@pytest.mark.signed
@pytest.mark.usefixtures("develop_mode")
def test_an_update_while_an_edit_waits_is_kept_on_it_and_waits(
    tmp_path, capsys
) -> None:
    """A script added while a person's edit waits for their word: the
    update keeps its state on the edit's, the edit's line in it, and the
    state waits with it (`docs/architecture.md` § 4, "The person's
    word")."""
    store, args = built_state(tmp_path, capsys)
    store.confirm()
    confirmed = versions.latest(store)
    waiting = give(store, adding(ADDED)).state
    assert waiting is not None and confirmed is not None
    root = Path(args[0])
    (root / "checks").mkdir()
    (root / "checks" / "bound.py").write_text("print(1)\n", "utf-8")
    capsys.readouterr()
    update = [
        "update",
        "--json",
        args[0],
        "--store",
        str(store.directory),
        "--host",
        "none",
    ]
    assert main(update) == 0, capsys.readouterr().err

    kept = versions.latest(store, waiting=True)
    assert kept is not None and kept.parent == waiting.id
    assert versions.latest(store) == confirmed
    with edits.opened(store, kept) as opened:
        assert ADDED in edits.shown(opened, Layer.EDGES)


@pytest.mark.signed
@pytest.mark.usefixtures("develop_mode")
def test_a_stack_on_a_waiting_edit_is_confirmed_or_dropped_with_it(
    tmp_path, capsys
) -> None:
    """An agent's edit kept on a person's waiting edit waits with it: one
    `confirm` makes the stack's top the latest; a drop of the person's
    edit takes the stack back, the agent's answer too, and the next edit
    is given on the confirmed state (`docs/architecture.md` § 4, "The
    person's word")."""
    for drop in (False, True):
        store, _ = built_state(tmp_path / str(drop), capsys)
        store.confirm()
        confirmed = versions.latest(store)
        mine = give(store, adding(ADDED))
        assert mine.state is not None and mine.recorded is not None
        with edits.opened(store, mine.state) as kept:
            shown = edits.shown(kept, Layer.EDGES)
            edited = shown.replace(
                "⟦done a-closing-paragraph-in⟧",
                '→ uses def:gain · "another file"\n⟦done a-closing-paragraph-in⟧',
            )
            assert edited != shown
            theirs = edits.give(kept, Layer.EDGES, edited, agent("m", "s"), None)
        assert theirs.state is not None and theirs.state.parent == mine.state.id
        assert versions.latest(store) == confirmed

        store.confirm(dropped=[mine.recorded.answer.key] if drop else [])
        if not drop:
            assert versions.latest(store) == theirs.state
            continue
        assert versions.latest(store, waiting=True) == confirmed
        assert not versions.pending_answers(store, confirmed.version)
        # the same edit given again stands on its own, the latest
        again = give(store, adding(ADDED)).state
        assert again is not None and again.parent == confirmed.id
        assert versions.latest(store, waiting=True) == again
        store.confirm()
        assert versions.latest(store) == again


@pytest.mark.signed
@pytest.mark.usefixtures("develop_mode")
def test_what_would_keep_beside_a_waiting_edit_refuses_before_it_files(
    tmp_path, capsys
) -> None:
    """While a person's edit waits, a build of another version and an
    update from OLD given otherwise than as a state refuse before they
    file anything: their state would stand beside the edit's."""
    store, args = built_state(tmp_path, capsys)
    store.confirm()
    assert give(store, adding(ADDED)).state is not None
    before = len(store.answers())
    # a version built already asks nothing, and a dry run prices: neither
    # is refused
    where = [args[0], "--store", str(store.directory)]
    for free in (
        ["build", "--json", *args, "--host", "none"],
        ["build", "--json", *args, "--dry-run"],
    ):
        capsys.readouterr()
        assert main(free) == 0, (free, capsys.readouterr().err)
    # an update's dry run from OLD given otherwise prices: the waiting
    # edit does not refuse it (its dummy OLD does, after)
    capsys.readouterr()
    main(["update", "--json", *where, "--identity", "0" * 12, "--dry-run"])
    assert "waits for your word" not in capsys.readouterr().err
    root = Path(args[0])
    main_tex = root / "main.tex"
    main_tex.write_text(
        main_tex.read_text("utf-8").replace(
            "\\end{document}", "A closing line.\n\\end{document}"
        ),
        "utf-8",
    )
    where = [args[0], "--store", str(store.directory)]
    for asked in (
        ["build", "--json", *where, "--host", "none"],
        ["update", "--json", *where, "--identity", "0" * 12, "--host", "none"],
    ):
        capsys.readouterr()
        assert main(asked) != 0, asked
        assert "waits for your word" in capsys.readouterr().err, asked
    assert len(store.answers()) == before


@pytest.mark.usefixtures("develop_mode")
def test_a_paid_vocabulary_edit_s_update_keeps_its_expected_cost(
    tmp_path, capsys, monkeypatch
) -> None:
    """A vocabulary edit paid from the paper's budget takes it before the
    map is filed, and its update keeps what like runs cost for the
    runner's check."""
    from axiomation.commands import update as updating

    root, _ = boxed_state(tmp_path, capsys)
    (root / "axiomation.toml").write_text("[budget]\nrun_dollars_max = 5.0\n", "utf-8")
    sheet = tmp_path / "vocabulary.md"
    assert main(["sheet", str(root), "--stage", "vocabulary", "-o", str(sheet)]) == 0
    sheet.write_text(
        sheet.read_text(encoding="utf-8").replace("- kind: none", "- kind: container"),
        "utf-8",
    )
    seen = []

    def run(asked, say, edited=None):
        seen.append(asked.asking)
        return updating.Ran(0)

    monkeypatch.setattr(updating, "run", run)
    edit = ["edit", str(root), "--stage", "vocabulary", "--file", str(sheet)]
    assert main([*edit, "--profile", "standard"]) == 0
    (asking,) = seen
    assert asking.budget == 5.0 and asking.expect is not None


@pytest.mark.signed
@pytest.mark.usefixtures("develop_mode")
def test_a_vocabulary_edit_after_an_edges_edit_builds_on_it(tmp_path, capsys) -> None:
    """A vocabulary edit as the second edit of one sitting reads the state
    the first left waiting, as its sheet showed it: its update asks what
    the map reopens rather than finding no state (`docs/architecture.md`
    § 4, "The person's word")."""
    root, store = boxed_state(tmp_path, capsys)
    store.confirm()
    first = give(
        store,
        lambda t: t.replace(
            "⟦done hence-the-output-is⟧",
            '→ uses def:gain · "Hence"\n⟦done hence-the-output-is⟧',
        ),
    ).state
    assert first is not None
    sheet = tmp_path / "vocabulary.md"
    assert main(["sheet", str(root), "--stage", "vocabulary", "-o", str(sheet)]) == 0
    sheet.write_text(
        sheet.read_text(encoding="utf-8").replace("- kind: none", "- kind: container"),
        "utf-8",
    )
    capsys.readouterr()

    edit = ["edit", str(root), "--stage", "vocabulary", "--file", str(sheet)]
    assert main(edit) == Exit.UNANSWERED
    assert "no states" not in capsys.readouterr().err
    # its update carries the first edit's line: it started from that state
    (identity,) = [a for a in store.answers() if a.kind == IDENTITY]
    carried = store.held_identity(identity.key)["carried"]["sheet"]
    assert '→ uses def:gain · "Hence"' in carried


@pytest.mark.signed
@pytest.mark.usefixtures("develop_mode")
def test_an_edit_s_update_waits_for_the_person_s_word_and_goes_with_a_drop(
    tmp_path, capsys
) -> None:
    """A re-cutting edit before the person's word: its given answer waits
    and is pending all the same, its update's step is read from the state
    that waits (the step, and the state it was carried from, found among
    the waiting ones), and a drop takes the step with it."""
    root, store = boxed_state(tmp_path, capsys)
    store.confirm()
    first = versions.latest(store)
    sheet = tmp_path / "vocabulary.md"
    assert main(["sheet", str(root), "--stage", "vocabulary", "-o", str(sheet)]) == 0
    sheet.write_text(
        sheet.read_text(encoding="utf-8").replace("- kind: none", "- kind: container"),
        "utf-8",
    )
    edit = ["edit", str(root), "--stage", "vocabulary", "--file", str(sheet)]
    capsys.readouterr()
    assert main(edit) == Exit.UNANSWERED

    store = RecordStore(store.directory)
    (given,) = versions.pending_answers(store, first.version)
    identity = [a for a in store.answers() if a.kind == "identity"][-1]
    corrected = map_vocabulary(
        read_paper(root),
        store,
        Templates.shipped(),
        kept=vocabulary_of(json.loads(versions.kept_export(store, first))),
    )
    answered = run_update(
        read_paper(root, vocabulary=corrected.vocabulary),
        store,
        UpdateSettings(
            files=FILES,
            review=ReviewSettings(stages=BUILD_STAGES, files=FILES),
            paper=root.resolve(),
        ),
        Runner(stepping(answering(SPLIT_BOX)), Resolver(store), 10.0, FLAT),
        identity=identity.key,
        applied_before=first.answers,
    )
    assert answered.complete, answered.unanswered
    assert main(edit) == 0

    store = RecordStore(store.directory)
    second = versions.latest(store, waiting=True)
    assert versions.latest(store) == first  # the edit waits
    assert given.key in second.answers
    kept = store.kept_step(identity.key)
    assert kept is not None and kept[0] == second.id
    assert (
        versions.parent_by_raw(store, versions.step_raw(store, identity.key)) == first
    )

    capsys.readouterr()
    assert main(["confirm", str(root), "--drop", given.key]) == 0
    store = RecordStore(store.directory)
    assert versions.latest(store) == first
    assert store.kept_step(identity.key) is None
