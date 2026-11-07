"""Marks that are right and correctable: the open
stale marks computed from the kept states, shown on a state's sheet and
cleared by an answer that re-reads its owner; an update's Δ corrected on
its state, its marks given again and the judgments lapsing by it.

On the chain of `test_update` (the setting edited, the definition
qualified by it, the proof using the definition, the sharpness using the
proof), updated in one session and kept as a state; and on synthetic kept
states for the marks across versions. No model is called.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from axiomation import edits, versions
from axiomation.changes import Layer
from axiomation.cli import BUILD_STAGES, main
from axiomation.driver import ReviewSettings
from axiomation.edits import A_PERSON, EditError, agent
from axiomation.exchange import dumps
from axiomation.judgments import Author, CauseKind, StatusKind, judge, readings, seen
from axiomation.judgments import subject as judged
from axiomation.marks import open_marks
from axiomation.queries import View, show
from axiomation.reader import document_hash
from axiomation.records import Aspect, Depth, RecordStore, Verdict
from axiomation.update import update_payload
from axiomation.versions import Command
from test_update import (
    CHAIN,
    CHAIN_LEGEND,
    DEFINITION,
    EDITED_SETTING,
    FILES,
    PROOF_BLOCK,
    SETTING,
    SHARP,
    answering,
    host,
    make_paper,
    update,
)
from test_update import versions as two_papers
from test_update_single import (
    CARRIED_SETTINGS,
    THEOREM,
    as_assumption,
)
from test_versions import V1, V2, export, identity, keep

#: One session, every stage the command's, so a cut edit's questions are
#: the re-run's too.
#: Carried wholly (a cap of 0 Δ lines on moved blocks): the marks are the
#: rule's, every moved block's and every asked one's.
SETTINGS = dataclasses.replace(
    CARRIED_SETTINGS, review=ReviewSettings(stages=BUILD_STAGES, files=FILES)
)
NARROWED = "Δ narrowed — scalars of a subfield only"
REWORDED = "Δ reworded — a clause added"
SHARP_LINE = f'→ uses {PROOF_BLOCK} · "Next, the bound is sharp."'


def kept_update(tmp_path: Path, delta: str = NARROWED):
    """The chain updated in one session, the setting's Δ `delta`, kept as a
    state as `axiomation update` keeps it: the papers and the state."""
    v = two_papers(tmp_path, EDITED_SETTING, CHAIN, CHAIN_LEGEND)
    made = update(v, host(answering({SETTING: [delta]}, as_assumption)), SETTINGS)
    assert made.complete
    document = v.new.document
    state, _ = versions.record(
        v.store,
        versions.snapshot(v.new.root, document),
        document_hash(document),
        document.root,
        dumps(update_payload(document, made, v.store)).encode("utf-8"),
        Command.UPDATE,
        identity=made.identity.key,
        session=made.session_facts(document),
    )
    return v, state


def rerun(v) -> bytes:
    """The update run again from the store, the answers given on its version
    applied by its person rounds."""
    found = update(v, None, SETTINGS)
    assert found.complete, found.unanswered
    return dumps(update_payload(v.new.document, found, v.store)).encode("utf-8")


def given(store: RecordStore, edit, author=A_PERSON) -> edits.Given:
    with edits.opened(store, versions.latest(store)) as kept:
        shown = edits.shown(kept, Layer.EDGES)
        return edits.give(kept, Layer.EDGES, edit(shown), author)


def record_of(store: RecordStore, state: versions.State) -> dict:
    return json.loads(versions.kept_export(store, state))["review"]["update"]


def kept_marked(text: str, line: str) -> str:
    return text.replace(f"\n{line}\n", f"\n= {line}\n", 1)


# --- the marks, open and shown ---------------------------------------------------


def test_the_sheet_shows_each_open_mark_and_the_update_s_deltas(tmp_path) -> None:
    v, state = kept_update(tmp_path)

    marks = open_marks(v.store, state)
    assert [m.handle for m in marks] == [THEOREM, SHARP]
    assert {m.since for m in marks} == {state.id}
    with edits.opened(v.store, state) as kept:
        sheet = edits.shown(kept, Layer.EDGES)
    for m in marks:
        assert m.sheet_line() in sheet
        owned = sheet[sheet.index(f"⟦{m.handle} ") :]
        assert owned.index(m.sheet_line()) < owned.index(f"⟦done {m.handle}⟧")
    assert f"\n{NARROWED}\n⟦done {SETTING}⟧" in sheet
    assert (
        "⟦stale since"
        not in sheet[sheet.index(f"⟦{DEFINITION} ") :].split(f"⟦done {DEFINITION}⟧")[0]
    )

    shown = show(View(v.store, state), THEOREM)
    (line,) = shown.sections["marks"]
    assert line.line.startswith(f"stale since {state.id}: ")
    assert line.data["handle"] == THEOREM


@pytest.mark.usefixtures("develop_mode")
def test_a_kept_line_clears_its_owner_s_mark_as_the_re_run_does(tmp_path) -> None:
    v, state = kept_update(tmp_path)

    done = given(v.store, lambda t: kept_marked(t, SHARP_LINE))

    assert done.state is not None, done.problems
    key = done.recorded.answer.key
    record = record_of(v.store, done.state)
    cleared = {s["handle"]: s["cleared_by"] for s in record["stale"]}
    assert cleared == {THEOREM: None, SHARP: key}
    assert record["reread"] == [{"handle": SHARP, "by": key}]
    assert [m.handle for m in open_marks(v.store, done.state)] == [THEOREM]
    assert rerun(v) == versions.kept_export(v.store, done.state)


@pytest.mark.usefixtures("develop_mode")
def test_lines_given_unshown_the_marks_clear_none_as_the_re_run_does(tmp_path) -> None:
    """`change`'s lines are given without the sheet, its marks unseen: the
    same kept line clears nothing, and the re-run agrees."""
    v, state = kept_update(tmp_path)

    with edits.opened(v.store, state) as kept:
        shown = edits.shown(kept, Layer.EDGES)
        done = edits.give(
            kept, Layer.EDGES, kept_marked(shown, SHARP_LINE), marks_shown=False
        )

    assert done.state is not None, done.problems
    assert done.recorded.answer.marks_shown is False
    record = record_of(v.store, done.state)
    assert all(s["cleared_by"] is None for s in record["stale"])
    assert "reread" not in record
    assert [m.handle for m in open_marks(v.store, done.state)] == [THEOREM, SHARP]
    assert rerun(v) == versions.kept_export(v.store, done.state)


@pytest.mark.usefixtures("develop_mode")
def test_an_agent_clears_a_mark_only_by_changing_a_line(tmp_path) -> None:
    v, _ = kept_update(tmp_path)
    by = agent("model-a", "s1")

    with pytest.raises(EditError):
        # the sheet shown again, unchanged: nothing to record, nothing read
        given(v.store, lambda t: t, by)
    done = given(v.store, lambda t: t.replace(f"{SHARP_LINE}\n", "", 1), by)

    assert done.state is not None, done.problems
    record = record_of(v.store, done.state)
    (sharp,) = [s for s in record["stale"] if s["handle"] == SHARP]
    assert sharp["cleared_by"] == done.recorded.answer.key
    assert rerun(v) == versions.kept_export(v.store, done.state)


def test_a_mark_written_and_a_delta_added_or_removed_are_refused(tmp_path) -> None:
    v, _ = kept_update(tmp_path)
    forged = "⟦stale since 0: written by hand⟧"

    for edit, said in (
        (
            # with a line changed, so the gate reads it
            lambda t: t.replace(
                f"⟦done {DEFINITION}⟧", f"{forged}\n⟦done {DEFINITION}⟧"
            ).replace(f"{SHARP_LINE}\n", "", 1),
            "a stale mark is the store's",
        ),
        (
            lambda t: t.replace(
                f"⟦done {DEFINITION}⟧", f"Δ narrowed — said here\n⟦done {DEFINITION}⟧"
            ),
            "a Δ line corrects one the sheet shows",
        ),
        (lambda t: t.replace(f"{NARROWED}\n", ""), "never removed"),
        (
            lambda t: t.replace(f"{NARROWED}\n", f"{NARROWED}\n{REWORDED}\n"),
            "a second Δ line on one block and part",
        ),
    ):
        found = given(v.store, edit)
        assert found.state is None
        assert any(said in p for p in found.problems), found.problems
    assert len(versions.states(v.store)) == 1


def test_a_mark_line_dropped_records_nothing_and_a_delta_needs_one_shown(
    tmp_path,
) -> None:
    v, state = kept_update(tmp_path)
    (theorem, *_) = open_marks(v.store, state)

    with pytest.raises(EditError, match="nothing to record"):
        given(v.store, lambda t: t.replace(f"{theorem.sheet_line()}\n", ""))
    with edits.opened(v.store, state) as kept:
        sheet = edits.shown(kept, Layer.EDGES)
    with pytest.raises(EditError, match="shows no Δ line to correct"):
        edits.delta_lines(sheet, SHARP, "Δ narrowed — said here")
    assert len(versions.states(v.store)) == 1
    assert open_marks(v.store, state)[0] == theorem


# --- a Δ corrected -------------------------------------------------------------------


@pytest.mark.usefixtures("develop_mode")
def test_a_delta_corrected_marks_again_and_lapses_as_the_re_run_does(
    tmp_path, capsys
) -> None:
    v, state = kept_update(tmp_path, REWORDED)
    assert record_of(v.store, state)["stale"] == []
    at = seen(v.store, state)
    judgment = judge(
        v.store,
        state,
        Aspect.TRUE,
        judged(at, Aspect.TRUE, at_block(at, SHARP)),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "read it",
        Author("agent", "model-a", "s1"),
    ).records.judgments[0]

    args = [str(v.new.root), "--store", str(v.store.directory)]
    capsys.readouterr()
    assert main(["change", *args, "--delta", SETTING, NARROWED]) == 0, (
        capsys.readouterr().err
    )
    capsys.readouterr()

    now = versions.latest(v.store)
    assert now.command is Command.EDIT
    record = record_of(v.store, now)
    (corrected,) = [d for d in record["deltas"] if d["handle"] == SETTING]
    assert (corrected["class"], corrected["by"]) == ("narrowed", "person")
    assert [m["handle"] for m in record["moved_by_rule"]] == [DEFINITION, PROOF_BLOCK]
    assert [s["handle"] for s in record["stale"]] == [THEOREM, SHARP]
    assert all(s["cleared_by"] is None for s in record["stale"])
    assert rerun(v) == versions.kept_export(v.store, now)

    found = readings(v.store, now)
    (read,) = [r for r in found.reads if r.judgment.id == judgment.id]
    assert read.walked.lapsed
    assert {c.kind for c in read.walked.causes} == {CauseKind.MEANING}
    (status,) = [s for s in found.statuses if s.subject == judgment.subject]
    assert status.status is StatusKind.LAPSED


@pytest.mark.usefixtures("develop_mode")
def test_a_correction_keeps_what_was_cleared_and_by_an_agent_says_so(
    tmp_path,
) -> None:
    v, _ = kept_update(tmp_path)
    kept = given(v.store, lambda t: kept_marked(t, SHARP_LINE))
    assert kept.state is not None, kept.problems

    widened = "Δ widened — scalars of any field"
    done = given(
        v.store, lambda t: t.replace(NARROWED, widened), agent("model-a", "s1")
    )

    assert done.state is not None, done.problems
    record = record_of(v.store, done.state)
    (corrected,) = [d for d in record["deltas"] if d["handle"] == SETTING]
    assert (corrected["class"], corrected["by"]) == ("widened", "agent")
    cleared = {s["handle"]: s["cleared_by"] for s in record["stale"]}
    assert cleared == {
        THEOREM: None,
        SHARP: kept.recorded.answer.key,
    }
    assert rerun(v) == versions.kept_export(v.store, done.state)

    back = given(v.store, lambda t: t.replace(widened, REWORDED))
    assert back.state is not None, back.problems
    record = record_of(v.store, back.state)
    assert (record["moved_by_rule"], record["stale"]) == ([], [])
    assert open_marks(v.store, back.state) == ()
    assert rerun(v) == versions.kept_export(v.store, back.state)


def at_block(at, handle: str) -> str:
    return next(i for i, h in at.handles.items() if h == handle)


# --- the next update ----------------------------------------------------------------


def test_a_dry_run_prices_the_update_s_queue_by_its_tasks(tmp_path, capsys) -> None:
    """`update --dry-run` from the store's latest state makes its identity
    answer in memory, recording none: the one session is priced as its
    queue, by the tasks it serves first."""
    from axiomation import update_queue

    v, _ = kept_update(tmp_path)
    third = make_paper(
        tmp_path / "third",
        main=EDITED_SETTING.replace(
            "A second sentence of the setting, edited.",
            "A second sentence of the setting, edited again.",
        ),
    )
    store = ["--store", str(v.store.directory)]
    args = [
        "update",
        str(third.root),
        *store,
        "--profile",
        "standard",
        "--dry-run",
        "--json",
    ]
    capsys.readouterr()
    assert main(args) == 0
    out = capsys.readouterr().out
    dry = json.loads(out[out.index("{") :])

    assert dry["shape"] == "single-session" and dry["answered"] == 0
    assert dry["tasks"] >= 1 and dry["attempts_priced"] == 1
    fitted = update_queue.FITTED
    assert dry["estimate"] == round(fitted.start + dry["tasks"] * fitted.task, 4)

    # --prompts: the queue's brief and the tasks it serves first, its
    # working files on a scratch copy: nothing kept in the store's run
    prompts = tmp_path / "prompts"
    runs = sorted(p.name for p in v.store.run_directory.rglob("*"))
    assert main([*args, "--prompts", str(prompts)]) == 0
    capsys.readouterr()
    written = {p.name for p in prompts.iterdir()}
    assert "tasks.md" in written and f"{update_queue.KIND}.md" in written
    assert (prompts / "tasks.md").read_text(encoding="utf-8").startswith("task ")
    assert sorted(p.name for p in v.store.run_directory.rglob("*")) == runs


# --- across versions, on synthetic states --------------------------------------------

TEXT = "Let f be a gain. Then f is bounded."
BLOCKS = [("d", "Let f be a gain."), ("t", "Then f is bounded.")]
V3 = "3" * 64
CAUSE = {
    "why": "asked by the rule, from d's change",
    "from": "d",
    "via": "t → uses d",
    "root": "d",
}


def version(store, ver, parent, previous, record, command=Command.UPDATE):
    payload = export(TEXT, BLOCKS, update=record, handles={b: b for b, _ in BLOCKS})
    key = None
    if previous is not None:
        key = identity(
            store, previous, ver, {"blocks": payload["blocks"]}, same("d", "t")
        )
    return keep(store, ver, TEXT, payload, command, parent, key)


def same(*blocks: str):
    return [([b], [b], "same", None) for b in blocks]


def stale(*handles: str, cleared: str | None = None) -> dict:
    return {
        "deltas": [],
        "reopened": [],
        "shape": "single-session",
        "moved_by_rule": [],
        "stale": [
            {"handle": h, "causes": [CAUSE], "cleared_by": cleared} for h in handles
        ],
    }


def test_marks_accumulate_across_versions_until_an_answer_re_reads_them(
    tmp_path,
) -> None:
    store = RecordStore(tmp_path / "store")
    built = version(store, V1, None, None, None, Command.BUILD)
    first = version(store, V2, built, V1, stale("t"))

    (mark,) = open_marks(store, first)
    assert (mark.handle, mark.since) == ("t", first.id)

    # the next version marks nothing new, and reopens or re-reads nothing
    second = version(store, V3, first, V2, stale())
    assert [(m.handle, m.since) for m in open_marks(store, second)] == [("t", first.id)]
    # an edit of that version that re-read t closes the earlier mark
    record = {**stale(), "reread": [{"handle": "t", "by": "k"}]}
    edited = version(store, V3, second, None, record, Command.EDIT)
    assert open_marks(store, edited) == ()


def test_a_mark_goes_with_its_node(tmp_path) -> None:
    store = RecordStore(tmp_path / "store")
    built = version(store, V1, None, None, None, Command.BUILD)
    first = version(store, V2, built, V1, stale("t"))
    blocks = [("d", "Let f be a gain.")]
    payload = export(TEXT, blocks, update=stale(), handles={"d": "d"})
    key = identity(
        store,
        V2,
        V3,
        {"blocks": payload["blocks"]},
        [(["d"], ["d"], "same", None), (["t"], [], "deleted", None)],
    )
    second = keep(store, V3, TEXT, payload, Command.UPDATE, first, key)

    assert second.node_of("t") is None
    assert open_marks(store, second) == ()


def test_the_next_update_s_reopen_closes_a_mark_and_the_latest_record_speaks(
    tmp_path,
) -> None:
    store = RecordStore(tmp_path / "store")
    built = version(store, V1, None, None, None, Command.BUILD)
    first = version(store, V2, built, V1, stale("t"))
    # within its version the latest record speaks: an edit that cleared it
    cleared = version(store, V2, first, None, stale("t", cleared="k"), Command.EDIT)
    assert open_marks(store, cleared) == ()
    assert len(open_marks(store, first)) == 1

    record = {
        **stale(),
        "reopened": [{"handle": "t", "why": "", "session": 1, "from": None}],
    }
    second = version(store, V3, first, V2, record)
    assert open_marks(store, second) == ()


@pytest.mark.usefixtures("develop_mode")
def test_a_per_stage_update_s_delta_is_corrected_with_no_marks(
    tmp_path, capsys
) -> None:
    from test_edits import updated_state

    root, store = updated_state(tmp_path, capsys)
    before = versions.latest(store)
    assert "shape" not in record_of(store, before)

    done = given(
        store,
        lambda t: t.replace(
            "Δ reworded — a clause added", "Δ narrowed — fewer fields now"
        ),
    )

    assert done.state is not None, done.problems
    record = record_of(store, done.state)
    (corrected,) = [d for d in record["deltas"] if d["by"] == "person"]
    assert corrected["class"] == "narrowed"
    assert "stale" not in record and "moved_by_rule" not in record
    out = tmp_path / "rerun.json"
    capsys.readouterr()
    assert main(["update", str(root), "--host", "none", "-o", str(out)]) == 0, (
        capsys.readouterr().err
    )
    assert out.read_bytes() == versions.kept_export(store, done.state)


@pytest.mark.usefixtures("develop_mode")
def test_a_corrected_delta_follows_a_later_cut_edit_as_the_re_run_does(
    tmp_path,
) -> None:
    """A Δ corrected, then a person's cut edit merging its block into the
    heading before it, the re-ask taking the qualifies line off the merged
    block, which holds no assumption role: the corrected entry goes on the
    heir on both paths."""
    from axiomation.resolver import Resolver
    from axiomation.runner import Runner
    from questions.test_edges_file import done
    from test_edits import FLAT, given_on
    from test_person import host as person_host
    from test_update import without_qualifies

    v, _ = kept_update(tmp_path)
    corrected = given(
        v.store, lambda t: t.replace(NARROWED, "Δ widened — scalars of any field")
    )
    assert corrected.state is not None, corrected.problems

    merged = given_on(
        v.store,
        Layer.CUT,
        lambda t: "\n".join(
            line for line in t.split("\n") if not line.startswith("⟦b2 ")
        ),
        Runner(
            person_host(reask=lambda t: done(without_qualifies(t))),
            Resolver(v.store),
            10.0,
            FLAT,
        ),
    )

    assert merged.state is not None, merged.unanswered
    record = record_of(v.store, merged.state)
    (person,) = [d for d in record["deltas"] if d["by"] == "person"]
    with edits.opened(v.store, merged.state) as kept:
        assert person["block"] in kept.handles().values()
        sheet = edits.shown(kept, Layer.EDGES)
    assert "Δ widened — scalars of any field" in sheet
    assert rerun(v) == versions.kept_export(v.store, merged.state)


def test_a_per_stage_update_s_follow_up_session_re_reads_nothing(tmp_path) -> None:
    """Session 2 of an update per stage is the update's own, not an answer
    after it."""
    from test_update import (
        CLOSING_PARAGRAPH,
        NARROWED_CLOSING,
        by_session,
        closing_cited,
        without_qualifies,
    )

    v = closing_cited(tmp_path)
    made = update(v, host(by_session(answering(NARROWED_CLOSING, without_qualifies))))
    assert made.complete and made.second_question is not None
    found = update_payload(v.new.document, made, v.store)["review"]["update"]
    assert CLOSING_PARAGRAPH in {r["handle"] for r in found["reopened"]}
    assert "reread" not in found


# --- the edit a mark and a lapse began at --------------------------------------


def test_a_lapse_s_brief_and_a_mark_s_line_name_the_edit_once(tmp_path, capsys) -> None:
    from axiomation import review
    from axiomation.queries import reask_report
    from axiomation.questions import review_file as rf

    v, state = kept_update(tmp_path, REWORDED)
    at = seen(v.store, state)
    judgment = judge(
        v.store,
        state,
        Aspect.TRUE,
        judged(at, Aspect.TRUE, at_block(at, SHARP)),
        Verdict.VERIFIED,
        Depth.INFORMAL,
        "read it",
        Author("agent", "model-a", "s1"),
    ).records.judgments[0]
    args = [str(v.new.root), "--store", str(v.store.directory)]
    assert main(["change", *args, "--delta", SETTING, NARROWED]) == 0
    capsys.readouterr()
    now = versions.latest(v.store)
    view = View(v.store, now)

    # the brief carries the edit the move began at: the setting, its Δ and
    # note, its text, though the setting is no element of the basis
    brief = reask_report(view, judgment.id)
    (edit,) = brief.sections["the_edit"]
    assert edit.data == {"root": SETTING, "state": now.id}
    assert edit.line.startswith(f"{SETTING}'s edit, at state {now.id}")
    assert f"  {NARROWED}" in edit.line
    assert "now: We fix a field of scalars." in edit.line
    assert list(brief.sections).index("the_edit") < list(brief.sections).index(
        "basis_now"
    )

    # the mark's line names the edit once, with its Δ
    (sharp,) = [m for m in open_marks(v.store, now) if m.handle == SHARP]
    assert sharp.why == (
        f"from {SETTING}'s edit (Δ narrowed): asked by the rule, from "
        f"{PROOF_BLOCK}'s change (via {SHARP} → uses {PROOF_BLOCK})"
    )

    # a review's sheet shows each edit once, above the briefs that name it
    hints = review.hints_text(
        view,
        rf.COLUMNS[rf.Session.ARGUMENT],
        {(s.aspect, s.subject) for s in view.readings.statuses},
    )
    assert hints.count(f"{SETTING}'s edit, at state {now.id}") == 2
    assert hints.count(f"  {NARROWED}") == 1
    assert f"{SETTING}'s edit, at state {now.id}: above" in hints


def test_a_cause_that_came_the_same_way_twice_is_named_once() -> None:
    from axiomation.marks import Mark

    via = "group:p → justifies thm"
    mark = Mark(
        "n1",
        "thm",
        "s1",
        (
            {
                "why": "what justifies it changed: group:p",
                "from": "a",
                "via": via,
                "root": None,
            },
            {
                "why": "what justifies it changed: group:p",
                "from": "b",
                "via": via,
                "root": None,
            },
        ),
    )
    assert mark.why == (f"what justifies it changed: group:p (via {via}), from a and b")
    rooted = Mark(
        "n1",
        "thm",
        "s1",
        tuple({**c, "root": "r"} for c in mark.causes),
        {"r": "narrowed"},
    )
    assert rooted.why == f"from r's edit (Δ narrowed): {mark.why}"


# --- a reread clears what it was shown ------------------------------------------


def test_a_reread_clears_its_owner_s_marks_and_pins_nothing(tmp_path, capsys) -> None:
    v, state = kept_update(tmp_path)
    args = [str(v.new.root), "--store", str(v.store.directory)]
    before = {m.handle for m in open_marks(v.store, state)}
    assert {SHARP, THEOREM} <= before
    capsys.readouterr()

    reason = "read against the setting's edit: the sharpness still holds"
    assert main(["reread", *args, SHARP, "--reason", reason]) == 0
    out = capsys.readouterr().out
    assert f"at state {state.id} by person: {SHARP}" in out

    # a filing: no state, no line changed, no pin; the mark alone is gone
    assert versions.latest(v.store) == state
    assert {m.handle for m in open_marks(v.store, state)} == before - {SHARP}
    with edits.opened(v.store, state) as kept:
        assert kept.pins == ()
    view = View(v.store, state)
    assert SHARP not in {m.handle for m in view.marks}

    # shown nothing to clear, it is refused
    assert main(["reread", *args, SHARP, "--reason", reason]) == 1
    assert "holds no open mark" in capsys.readouterr().err

    # an agent's reread, counted by `check` for one audit read
    agent_args = ["--agent", "model-a", "--agent-session", "s1"]
    assert main(["reread", *args, THEOREM, "--reason", reason, *agent_args]) == 0
    capsys.readouterr()
    main(["check", *args, "--json"])
    found = json.loads(capsys.readouterr().out)
    assert {(r["handle"], r["by_person"]) for r in found["reread"]} == {
        (SHARP, True),
        (THEOREM, False),
    }
    main(["check", *args])
    assert "stale marks: 2 cleared by rereads, 1 of them by agents alone" in (
        capsys.readouterr().out
    )


def marks_reread(store: RecordStore, state: versions.State, handle: str) -> None:
    from axiomation.marks import reread

    view = View(store, state)
    reread(store, state, [view.nodes[view.resolve(handle)]], "read it")


def test_a_reread_clears_no_mark_whose_causes_came_after_it(tmp_path, capsys) -> None:
    """Within a version a corrected Δ gives the marks again: a mark that
    went and came back stands since the state it came back at, and a
    reread before it, shown the earlier mark, does not clear it."""
    v, state = kept_update(tmp_path)
    marks_reread(v.store, state, SHARP)
    args = [str(v.new.root), "--store", str(v.store.directory)]
    assert main(["change", *args, "--delta", SETTING, REWORDED]) == 0
    assert SHARP not in {
        m.handle for m in open_marks(v.store, versions.latest(v.store))
    }
    assert main(["change", *args, "--delta", SETTING, NARROWED]) == 0
    capsys.readouterr()
    again = versions.latest(v.store)

    (sharp,) = [m for m in open_marks(v.store, again) if m.handle == SHARP]
    assert sharp.since == again.id


def test_the_later_rule_is_read_again_once_a_reread_clears_a_mark(
    tmp_path, capsys, monkeypatch
) -> None:
    """The stage files a state shows hold its open marks beside their
    owners, so what the check finds in them is kept while the marks
    stand and worked out again once a reread clears one, the same as
    worked out afresh."""
    from axiomation import queries

    v, state = kept_update(tmp_path)
    opened = edits.opened
    asked: list[str] = []

    def counted(store, kept, *args, **kwargs):
        asked.append(kept.id)
        return opened(store, kept, *args, **kwargs)

    monkeypatch.setattr(edits, "opened", counted)

    def lines() -> list[str]:
        found = View(v.store, state).later_rule
        return [found.unread or "", *(i.line for i in found.items)]

    first = lines()
    assert lines() == first and asked == [state.id]

    args = [str(v.new.root), "--store", str(v.store.directory)]
    assert main(["reread", *args, SHARP, "--reason", "read: it holds"]) == 0
    capsys.readouterr()

    after = lines()
    assert asked == [state.id, state.id]
    monkeypatch.setattr(queries, "_LATER_RULES", versions.Held(8))
    assert lines() == after
