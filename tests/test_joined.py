"""Two copies of one record written apart and joined, in either order: the
same values, or the same conflict named (`docs/architecture.md` § 4,
"Copies joined"). No model is called."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from axiomation import judgments, keys, versions
from axiomation.cli import main
from axiomation.commands import Exit, Paper, filings, reads, scratch_copy
from axiomation.questions import Validation
from axiomation.records import (
    PERSON,
    TIPS,
    AnswerRecord,
    CorrectionClass,
    CutCorrection,
    CutOp,
    RecordError,
    RecordStore,
    TemplateRef,
    filed_after,
    unseen_by,
)
from axiomation.resolver import Attempt, Resolver
from axiomation.versions import Command, VersionError
from questions.review_paper import file_questions, make_paper
from test_commands import Said
from test_edits import ADDED, adding, built_state, edited_edges, give
from test_versions import V1, V2, export, keep

TEXT = "We fix a field. The output is bounded."
BLOCKS = [("k", "We fix a field."), ("t", "The output is bounded.")]


def answer(
    why: str, attempt: int = 1, valid: bool = True, at: str = "12"
) -> AnswerRecord:
    """An answer to the key `q`: a valid one splits one block, for `why`."""
    return AnswerRecord(
        key="q",
        kind="cut-review",
        stage="boundaries",
        template=TemplateRef("cut-review-boundaries", "0123456789ab"),
        attempt=attempt,
        host="opencode",
        model="opencode/deepseek-v4.1-flash",
        time=f"2026-11-03T{at}:00:00Z",
        seconds=1.0,
        cost=0.01,
        valid=valid,
        problems=() if valid else ("not an answer",),
        records=(f"q-{attempt}-1",) if valid else (),
    )


def given(store: RecordStore, why: str, attempt: int = 1, **fields) -> None:
    found = answer(why, attempt, **fields)
    store.append(
        found,
        ()
        if not found.valid
        else (
            CutCorrection(
                f"q-{attempt}-1",
                CutOp.SPLIT,
                CorrectionClass.ARGUMENT_BOUNDARY,
                why,
                "q",
                block="p.md:1:1",
                at=("The output",),
            ),
        ),
    )


def apart(tmp_path: Path) -> tuple[RecordStore, RecordStore, versions.State]:
    """A record with one state, and a copy of it: two copies to write
    apart."""
    here = RecordStore(tmp_path / "here")
    base = keep(here, V1, TEXT, export(TEXT, BLOCKS))
    return here, here.copy_to(tmp_path / "there"), base


def joined(
    here: RecordStore, there: RecordStore, tmp_path: Path
) -> tuple[RecordStore, RecordStore]:
    """The two copies joined each way: `there` into a copy of `here`, and
    `here` into a copy of `there`."""
    one = here.copy_to(tmp_path / "here-then-there")
    one.join(there.directory)
    other = there.copy_to(tmp_path / "there-then-here")
    other.join(here.directory)
    return RecordStore(one.directory), RecordStore(other.directory)


# --- one key's answers --------------------------------------------------------------


def test_two_answers_to_one_key_made_apart_conflict_in_either_order(tmp_path) -> None:
    """One key, one attempt, answered apart and differently: neither is
    the key's answer, and both orders name the same two entries."""
    here, there, _ = apart(tmp_path)
    given(here, "split a")
    given(there, "split b")

    found = [
        (s.authoritative(), s.answer_conflicts()) for s in joined(here, there, tmp_path)
    ]

    (first, torn), (second, again) = found
    assert "q" not in first and "q" not in second
    assert torn == again and len(torn["q"]) == 2


def test_two_equal_answers_made_apart_are_one_in_either_order(tmp_path) -> None:
    """The same answer given apart (at other times): one answer, read alike
    whichever copy was joined into which; both entries stay."""
    here, there, _ = apart(tmp_path)
    given(here, "split a", at="12")
    given(there, "split a", at="13")

    one, other = joined(here, there, tmp_path)

    assert one.answer_conflicts() == {} == other.answer_conflicts()
    assert one.authoritative()["q"] == other.authoritative()["q"]
    assert len([a for a in one.answers() if a.key == "q"]) == 2


def test_an_answer_both_copies_hold_stays_first_in_either_order(tmp_path) -> None:
    """A retry is no conflict: the answer under both copies' later ones is
    the key's, an invalid one before it skipped."""
    here = RecordStore(tmp_path / "here")
    given(here, "broken", attempt=1, valid=False)
    given(here, "split a", attempt=2)
    there = here.copy_to(tmp_path / "there")
    given(here, "split b", attempt=3)
    given(there, "split c", attempt=3)

    for store in joined(here, there, tmp_path):
        assert store.answer_conflicts() == {}
        assert store.authoritative()["q"].attempt == 2


# --- kept states --------------------------------------------------------------------


def test_two_states_kept_apart_have_no_latest_in_either_order(tmp_path) -> None:
    """Each copy keeps a state on one parent: joined either way, no state is
    the latest, both are named alike, each reads by its id with its nodes
    as kept, and nothing is kept on top."""
    here, there, base = apart(tmp_path)
    mine = keep(
        here, V2, TEXT + " Mine.", export(TEXT + " Mine.", BLOCKS), started_from=base
    )
    other_text = TEXT + " Theirs."
    theirs = keep(
        there, "3" * 64, other_text, export(other_text, BLOCKS), started_from=base
    )

    for store in joined(here, there, tmp_path):
        assert {s.id for s in versions.tips(store)} == {mine.id, theirs.id}
        with pytest.raises(VersionError, match="kept states apart"):
            versions.latest(store)
        assert versions.state(store, mine.id).nodes == mine.nodes
        assert versions.state(store, theirs.id).nodes == theirs.nodes
        with pytest.raises(VersionError, match="kept states apart"):
            keep(store, "4" * 64, TEXT, export(TEXT, BLOCKS), started_from=mine)
        # an entry written after both settles nothing
        given(store, "split a")
        assert len(versions.tips(RecordStore(store.directory))) == 2


def test_one_state_kept_alike_on_both_copies_is_the_latest(
    tmp_path, node_ids, monkeypatch
) -> None:
    """The same state kept apart (the same text and graph on one parent, at
    another second) is one state, whichever copy was joined into which:
    listed once, read by its id, and the same row in either order."""
    here, there, base = apart(tmp_path)
    text = TEXT + " Both."
    node_ids()
    at(monkeypatch, "2026-11-03T12:00:00+00:00")
    mine = keep(here, V2, text, export(text, BLOCKS), started_from=base)
    node_ids()
    at(monkeypatch, "2026-11-03T13:00:00+00:00")
    theirs = keep(there, V2, text, export(text, BLOCKS), started_from=base)
    assert mine.id == theirs.id and mine.at != theirs.at

    one, other = joined(here, there, tmp_path)
    for store in (one, other):
        assert [s.id for s in versions.states(store)] == [base.id, mine.id]
        assert versions.state(store, mine.id).id == mine.id
    assert versions.latest(one) == versions.latest(other)


def test_a_state_kept_alike_then_built_on_one_copy_is_under_its_child(
    tmp_path, node_ids, monkeypatch
) -> None:
    """The same state kept on both copies, then a state kept on it on one:
    that one is the latest in either order, the state's other copy under
    it all the same."""
    here, there, base = apart(tmp_path)
    text = TEXT + " Both."
    node_ids()
    at(monkeypatch, "2026-11-03T12:00:00+00:00")
    mine = keep(here, V2, text, export(text, BLOCKS), started_from=base)
    node_ids()
    at(monkeypatch, "2026-11-03T13:00:00+00:00")
    theirs = keep(there, V2, text, export(text, BLOCKS), started_from=base)
    assert mine.id == theirs.id
    later = text + " Later."
    child = keep(there, "3" * 64, later, export(later, BLOCKS), started_from=theirs)

    for store in joined(here, there, tmp_path):
        assert versions.latest(store).id == child.id


def at(monkeypatch, when: str) -> None:
    """States kept from now on are kept at `when`."""

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(when)

    monkeypatch.setattr(versions, "datetime", Fixed)


def test_a_state_kept_on_one_copy_alone_is_the_latest_in_either_order(tmp_path) -> None:
    here, there, base = apart(tmp_path)
    mine = keep(
        here, V2, TEXT + " Mine.", export(TEXT + " Mine.", BLOCKS), started_from=base
    )
    given(there, "split a")

    for store in joined(here, there, tmp_path):
        assert versions.latest(store) == mine


def test_a_join_names_what_was_made_apart(tmp_path, capsys) -> None:
    here, there, base = apart(tmp_path)
    keep(here, V2, TEXT + " Mine.", export(TEXT + " Mine.", BLOCKS), started_from=base)
    keep(
        there,
        "3" * 64,
        TEXT + " Theirs.",
        export(TEXT + " Theirs.", BLOCKS),
        started_from=base,
    )
    given(here, "split a")
    given(there, "split b")
    paper = tmp_path / "paper"
    paper.mkdir()

    args = ["join", str(paper), str(there.directory), "--store", str(here.directory)]
    assert main(args) == 0

    said = capsys.readouterr().err
    assert "kept states apart" in said
    assert "question q" in said and "answered apart and differently" in said


# --- the person's word --------------------------------------------------------------


@pytest.mark.signed
def test_a_waiting_state_and_a_confirmed_one_in_either_order(tmp_path, capsys) -> None:
    """A person's edit waits on one copy while the other's is confirmed:
    the latest in effect is the confirmed one, and with the waiting ones
    counted the two conflict; confirming after the join settles nothing;
    dropping the waiting edit leaves one latest."""
    here, _ = built_state(tmp_path / "paper", capsys)
    here.confirm()
    there = here.copy_to(tmp_path / "there")
    waits = give(here, edited_edges)
    done = give(there, lambda t: adding(ADDED)(edited_edges(t)))
    there.confirm()
    assert waits.state is not None and done.state is not None

    for store in joined(here, RecordStore(there.directory), tmp_path):
        assert versions.latest(store).id == done.state.id
        assert {s.id for s in versions.tips(store, waiting=True)} == {
            waits.state.id,
            done.state.id,
        }
        with pytest.raises(VersionError, match="kept states apart"):
            versions.latest(store, waiting=True)

    confirmed, dropped = (
        here.copy_to(tmp_path / name) for name in ("confirmed", "dropped")
    )
    for store in (confirmed, dropped):
        store.join(there.directory)
    RecordStore(confirmed.directory).confirm()
    with pytest.raises(VersionError, match="kept states apart"):
        versions.latest(RecordStore(confirmed.directory))
    RecordStore(dropped.directory).confirm([waits.recorded.answer.key])
    assert versions.latest(RecordStore(dropped.directory)).id == done.state.id


# --- filings ------------------------------------------------------------------------


def test_judgments_filed_apart_that_differ_are_named_and_fail_check(
    tmp_path, capsys
) -> None:
    """One claim judged on each copy, `verified` here and `contradicted` there:
    neither was filed after the other, so every copy names the place, and
    `check` fails; the same verdict on both is no conflict."""
    here, args = built_state(tmp_path / "paper", capsys)
    paper = Paper(Path(args[0]), here.directory)
    claims = reads.Blocks(paper=paper, role=("claim",), json=True)
    block = json.loads(reads.query(claims))["blocks"]["items"][0]["block"]
    there = here.copy_to(tmp_path / "there")
    alike = here.copy_to(tmp_path / "alike")

    def judge(store: RecordStore, verdict: str, evidence: str) -> None:
        asked = filings.Judge(
            paper=Paper(Path(args[0]), store.directory),
            aspect="true",
            on=block,
            verdict=verdict,
            depth="informal",
            evidence=evidence,
        )
        filings.file(asked, Said())

    judge(here, "verified", "it holds")
    judge(there, "contradicted", "it fails for n = 2")
    judge(alike, "verified", "it holds, read again")

    for store in joined(here, RecordStore(there.directory), tmp_path):
        (found,) = store.filing_conflicts()
        assert found.kind == "judgment" and len(found.entries) == 2
    one, other = joined(here, RecordStore(there.directory), tmp_path / "again")
    assert one.filing_conflicts() == other.filing_conflicts()
    capsys.readouterr()
    assert main(["check", args[0], "--store", str(one.directory)]) == Exit.FAILED
    assert "judgments on" in capsys.readouterr().out
    json_args = ["check", args[0], "--store", str(one.directory), "--json"]
    assert main(json_args) == Exit.FAILED
    data = json.loads(capsys.readouterr().out)
    assert data["passes"] is False and "judgments on" in data["made_apart"][0]

    agreed = here.copy_to(tmp_path / "agreed")
    agreed.join(alike.directory)
    assert RecordStore(agreed.directory).filing_conflicts() == ()


def test_an_update_on_states_kept_apart_is_refused_before_it_asks(
    tmp_path, capsys
) -> None:
    """With no latest state, `update` and `changes` refuse at once, naming
    both states, rather than start from one picked by line order; an
    update from a state named refuses too, as nothing is kept on top."""
    here, there, base = apart(tmp_path)
    mine = keep(
        here, V2, TEXT + " Mine.", export(TEXT + " Mine.", BLOCKS), started_from=base
    )
    theirs = keep(
        there,
        "3" * 64,
        TEXT + " Theirs.",
        export(TEXT + " Theirs.", BLOCKS),
        started_from=base,
    )
    given(here, "split a")
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "p.md").write_text(TEXT + " Later.\n")

    for store in joined(here, there, tmp_path):
        where = [str(paper), "--store", str(store.directory)]
        for args in (
            ["update", *where, "--host", "none"],
            ["update", *where, "--state", mine.id, "--host", "none"],
            ["changes", *where],
        ):
            assert main(args) != 0
            assert "kept states apart" in capsys.readouterr().err
        # the states are listed all the same, against no latest
        assert main(["states", *where, "--json"]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert {s["id"] for s in listed["states"]} >= {mine.id, theirs.id}
        assert listed["files"]["state"] is None


@pytest.mark.signed
@pytest.mark.usefixtures("develop_mode")
def test_what_keeps_a_state_refuses_on_states_apart_and_reads_do_not(
    tmp_path, capsys
) -> None:
    """Joined copies, one with a state confirmed and the other with one
    waiting for the person's word: each command that keeps a state refuses
    before it asks or files, a state named or not, counting the waiting
    one; a read of a state named still works (`docs/architecture.md` § 4,
    "Copies joined")."""
    here, args = built_state(tmp_path, capsys)
    here.confirm()
    there = here.copy_to(tmp_path / "there")
    mine = give(here, adding(ADDED)).state
    here.confirm()
    assert give(there, adding('→ uses def:gain · "Closing words"')).state is not None
    assert mine is not None
    at = args.index("--store") + 1
    sheet = tmp_path / "edges.md"
    sheet.write_text("unread: refused before\n", "utf-8")

    for store in joined(here, there, tmp_path):
        built = [*args[:at], str(store.directory), *args[at + 1 :]]
        where = [args[0], "--store", str(store.directory)]
        before = len(store.answers())
        # one tip in effect: what reads the latest alone sees no join
        assert versions.latest(store) is not None
        for asked in (
            ["update", *where, "--state", mine.id, "--host", "none"],
            ["review", *where, "--host", "none"],
            ["review", *where, "--state", mine.id, "--host", "none"],
            ["support", *where, "--ask", "--dry-run", "--prompts", str(tmp_path / "d")],
            ["circles", *where, "--ask", "--dry-run", "--prompts", str(tmp_path / "d")],
            ["reread", *where, "--ask", "--dry-run", "--prompts", str(tmp_path / "d")],
            ["sync", *where, "--dry-run", "--prompts", str(tmp_path / "d")],
            ["build", *built, "--host", "none"],
            ["build", *built, "--dry-run"],
            ["update", *where, "--identity", "0" * 12, "--host", "none"],
            ["edit", *where, "--stage", "edges", "--file", str(sheet)],
        ):
            capsys.readouterr()
            assert main(asked) != 0, asked
            assert "nothing is kept on top" in capsys.readouterr().err, asked
        assert len(store.answers()) == before
        for read in (
            ["support", *where, "--state", mine.id],
            ["circles", *where, "--state", mine.id],
            ["review", *where, "--state", mine.id, "--report"],
        ):
            assert main(read) == 0, read
        capsys.readouterr()


@pytest.mark.signed
def test_a_waiting_state_is_built_on_and_never_beside(tmp_path) -> None:
    """A state waiting for the person's word is the latest: a re-run that
    keeps its content again keeps nothing, and a state kept beside it, on
    the confirmed one under it, is refused, so the edit never falls off
    the state read next (`docs/architecture.md` § 4, "The person's word")."""
    store = RecordStore(tmp_path / "store")
    base = keep(store, V1, TEXT, export(TEXT, BLOCKS))
    store.confirm()
    store.append(
        replace(answer("split a"), host=PERSON),
        (
            CutCorrection(
                "q-1-1",
                CutOp.SPLIT,
                CorrectionClass.ARGUMENT_BOUNDARY,
                "split a",
                "q",
                block="p.md:1:1",
                at=("The output",),
            ),
        ),
    )
    edited = export(TEXT + " Edited.", BLOCKS)
    first = keep(
        store, V2, TEXT + " Edited.", edited, started_from=base, answers=("q",)
    )
    again = keep(
        store, V2, TEXT + " Edited.", edited, started_from=base, answers=("q",)
    )
    assert again.id == first.id
    assert [s.id for s in versions.tips(store, waiting=True)] == [first.id]
    with pytest.raises(VersionError, match="waits for your word"):
        keep(
            store,
            "3" * 64,
            TEXT + " Other.",
            export(TEXT + " Other.", BLOCKS),
            started_from=base,
        )


def test_a_key_answered_apart_is_not_answered_again(tmp_path) -> None:
    """A third answer to a key two copies answered apart and differently is
    refused wherever it is filed from, so no run pays for it again."""
    here, there, _ = apart(tmp_path)
    given(here, "split a")
    given(there, "split b")
    paper = make_paper(tmp_path / "paper")
    asked = replace(file_questions(paper)[1], key="q")

    for store in joined(here, there, tmp_path):
        with pytest.raises(RecordError, match="answered differently"):
            Resolver(store).record(
                Attempt(asked, "fake", "m", "{}", 0.0, 0.0, Validation())
            )


def test_a_scratch_copy_leaves_no_tip(tmp_path) -> None:
    """A dry run's copy of the store keeps a tip while it is open and none
    after: nothing is left beside the person's keys."""
    here, _, _ = apart(tmp_path)
    RecordStore(here.directory).answers()  # the store's own tip, kept
    tips = keys.directory() / TIPS
    before = set(tips.glob("*.json"))
    with scratch_copy(Paper(tmp_path, here.directory)) as copy:
        RecordStore(copy.store).answers()
        assert set(tips.glob("*.json")) > before
    assert set(tips.glob("*.json")) == before


def test_each_state_reads_the_answer_it_was_built_on(tmp_path) -> None:
    """A key two copies answered apart, each copy keeping a state after its
    own answer: the record reads neither, and each state, seen from, reads
    the one under it, in either join order."""
    here, there, base = apart(tmp_path)
    given(here, "split a")
    mine = keep(
        here, V2, TEXT + " Mine.", export(TEXT + " Mine.", BLOCKS), started_from=base
    )
    given(there, "split b")
    theirs = keep(
        there,
        "3" * 64,
        TEXT + " Theirs.",
        export(TEXT + " Theirs.", BLOCKS),
        started_from=base,
    )

    for store in joined(here, there, tmp_path):
        assert "q" not in store.authoritative()
        assert [c.why for c in store.seen_from(mine.id).corrections_of("q")] == [
            "split a"
        ]
        assert [c.why for c in store.seen_from(theirs.id).corrections_of("q")] == [
            "split b"
        ]
        assert "q" not in store.seen_from(base.id).authoritative()


def test_a_person_s_reading_and_an_agent_s_finding_made_apart_read_alike(
    tmp_path, capsys
) -> None:
    """A person reads a claim `verified` on one copy; an agent finds a
    blocking problem in it on the other. Neither was filed after the other,
    so the person's reading does not set the finding aside: the claim
    reads the same, and `check` says the same, in either join order."""
    here, args = built_state(tmp_path / "paper", capsys)
    paper = Path(args[0])
    claims = reads.Blocks(
        paper=Paper(paper, here.directory), role=("claim",), json=True
    )
    block = json.loads(reads.query(claims))["blocks"]["items"][0]["block"]
    there = here.copy_to(tmp_path / "there")
    filings.file(
        filings.Judge(
            paper=Paper(paper, here.directory),
            aspect="true",
            on=block,
            verdict="verified",
            depth="informal",
            evidence="it holds",
        ),
        Said(),
    )
    filings.file(
        filings.Find(
            paper=Paper(paper, there.directory),
            agent="opencode/deepseek-v4.1-flash",
            session="s1",
            aspect="true",
            severity=3,
            problem="it fails for n = 2",
            on=(block,),
        ),
        Said(),
    )

    for store in joined(here, RecordStore(there.directory), tmp_path):
        kept = versions.latest(store)
        assert kept is not None
        node = kept.node_of(block)
        (status,) = [
            s
            for s in judgments.readings(store, kept).statuses
            if s.aspect is judgments.Aspect.TRUE and s.subject.node == node
        ]
        # the finding was not filed before the person's reading: it counts
        assert status.status is judgments.StatusKind.DISPUTED
        assert len(status.counted) == 1
        # whatever their hashes rank them, neither saw the other
        filed = store.filed_order()
        (read,) = status.reads
        (found,) = status.counted
        a, b = filed[read.judgment.answer], filed[found.finding.answer]
        assert not filed_after(a, b) and not filed_after(b, a)
        assert unseen_by(a, b) and unseen_by(b, a)


# --- what a drop takes back ----------------------------------------------------------


def filed(
    store: RecordStore,
    key: str,
    person: bool = True,
    on: str | None = None,
    attempt: int = 1,
) -> None:
    """A valid answer to `key` that splits one block: the person's (their
    word, waiting) or an agent's, given on the state `on`."""
    found = replace(
        answer("split", attempt),
        key=key,
        records=(f"{key}-{attempt}-1",),
        host=PERSON if person else "opencode",
        state=on,
    )
    store.append(
        found,
        (
            CutCorrection(
                f"{key}-{attempt}-1",
                CutOp.SPLIT,
                CorrectionClass.ARGUMENT_BOUNDARY,
                "split",
                key,
                block="p.md:1:1",
                at=("The output",),
            ),
        ),
    )


def sitting(tmp_path: Path) -> tuple[RecordStore, versions.State, list[versions.State]]:
    """A confirmed state, then two edits of one sitting on it, each a state
    on the one before, both waiting: `p1` then `p2`."""
    store = RecordStore(tmp_path / "store")
    base = keep(store, V1, TEXT, export(TEXT, BLOCKS))
    store.confirm()
    made, on = [], base
    for n, key in enumerate(("p1", "p2"), 1):
        filed(store, key)
        text = TEXT + " Edited." * n
        on = keep(
            store,
            V1,
            text,
            export(text, BLOCKS),
            Command.EDIT,
            started_from=on,
            answers=[*on.answers, key],
        )
        made.append(on)
    return store, base, made


@pytest.mark.signed
def test_a_drop_of_a_sitting_s_second_edit_keeps_its_first(tmp_path) -> None:
    """A state's answers carry its parent's: a drop of the second edit
    takes back its own state alone, never the first edit under it."""
    store, _, (first, _) = sitting(tmp_path)

    store.confirm(dropped=["p2"])
    assert versions.latest(store) == first


@pytest.mark.signed
def test_what_was_given_on_a_dropped_edit_goes_with_it(tmp_path) -> None:
    """An agent's answer given on a person's waiting edit, applied by no
    state yet, goes with the edit's drop: no first answer is left to lock
    the version; a person's must be dropped with it."""
    store, base, (first, _) = sitting(tmp_path)
    filed(store, "theirs", person=False, on=first.id)
    filed(store, "mine", on=first.id)
    with pytest.raises(RecordError, match="edits mine are built on"):
        store.confirm(dropped=["p1", "p2"])

    store.confirm(dropped=["p1", "p2", "mine"])
    assert versions.latest(store) == base
    assert "theirs" not in store.authoritative()


@pytest.mark.signed
def test_an_edit_given_again_after_its_drop_stands_on_its_own(tmp_path) -> None:
    """The same edit given again after a drop is its key's later attempt:
    its state is the latest, and the dropped one stays dropped after the
    next confirmation."""
    store, base, (first, _) = sitting(tmp_path)
    store.confirm(dropped=["p1", "p2"])
    assert versions.latest(store) == base

    filed(store, "p1", attempt=2)
    text = TEXT + " Again."
    again = keep(
        store,
        V1,
        text,
        export(text, BLOCKS),
        Command.EDIT,
        started_from=base,
        answers=["p1"],
    )
    assert versions.latest(store, waiting=True) == again
    store.confirm()
    assert versions.latest(store) == again
    assert first not in versions.states(store, waiting=True)


def twins(tmp_path: Path, drop: bool) -> tuple[RecordStore, RecordStore]:
    """Two copies give the same edit `p1` on one confirmed state, keeping
    one state id: `here` confirms it and an agent's edit kept on it;
    `there` drops it, or leaves it waiting."""
    here = RecordStore(tmp_path / "here")
    base = keep(here, V1, TEXT, export(TEXT, BLOCKS))
    here.confirm()
    there = here.copy_to(tmp_path / "there")
    text = TEXT + " Edited."
    made = []
    for store in (here, there):
        filed(store, "p1")
        made.append(
            keep(
                store,
                V1,
                text,
                export(text, BLOCKS),
                Command.EDIT,
                started_from=base,
                answers=["p1"],
            )
        )
    assert made[0].id == made[1].id
    here.confirm()
    filed(here, "a1", person=False, on=made[0].id)
    more = TEXT + " Edited. More."
    keep(
        here,
        V1,
        more,
        export(more, BLOCKS),
        Command.EDIT,
        started_from=made[0],
        answers=["p1", "a1"],
    )
    here.confirm()
    if drop:
        there.confirm(dropped=["p1"])
    return here, there


@pytest.mark.signed
@pytest.mark.parametrize("drop", [True, False])
def test_a_twin_dropped_or_waiting_on_one_copy_reads_alike_joined_either_way(
    tmp_path, drop
) -> None:
    """One edit given on two copies, confirmed on one and dropped (or
    waiting) on the other: either join order reads the same states and
    answers, and the confirmed copy's stack stands: one copy's drop
    takes back no state the other confirmed and built on."""
    here, there = twins(tmp_path, drop)
    latest = versions.latest(here)
    read = []
    for store in joined(here, there, tmp_path):
        read.append(
            (
                versions.latest(store),
                [s.id for s in versions.states(store)],
                sorted(store.authoritative()),
            )
        )
    assert read[0] == read[1]
    assert read[0][0] == latest
    assert {"p1", "a1"} <= set(read[0][2])


@pytest.mark.signed
def test_a_drop_keeps_a_confirmed_answer_given_on_a_state_that_stays(tmp_path) -> None:
    """A person's answer given and confirmed on a state that stays, then
    applied by a state on an edit they drop, is not taken back: only the
    state goes, and the answer is given again, unapplied."""
    store = RecordStore(tmp_path / "store")
    base = keep(store, V1, TEXT, export(TEXT, BLOCKS))
    filed(store, "v", on=base.id)
    store.confirm()
    filed(store, "p1")
    text = TEXT + " Edited."
    first = keep(
        store,
        V1,
        text,
        export(text, BLOCKS),
        Command.EDIT,
        started_from=base,
        answers=["p1"],
    )
    keep(
        store,
        V2,
        text,
        export(text, BLOCKS),
        Command.UPDATE,
        started_from=first,
        answers=["p1", "v"],
    )
    store.confirm(dropped=["p1"])
    assert "v" in store.authoritative()
    assert versions.latest(store) == base


@pytest.mark.signed
def test_confirm_lists_what_a_drop_takes_with_it(tmp_path, capsys) -> None:
    """`confirm --drop --list` names the states a drop takes back and the
    agent's answer that goes with it, before anything is signed."""
    store, _, (first, second) = sitting(tmp_path)
    filed(store, "theirs", person=False, on=first.id)
    where = [str(tmp_path), "--store", str(store.directory)]
    capsys.readouterr()

    drop = ["confirm", *where, "--drop", "p1", "--drop", "p2", "--list"]
    assert main(drop) == 0
    out = capsys.readouterr().out
    assert first.id in out and second.id in out
    (line,) = [x for x in out.splitlines() if x.strip().startswith("theirs")]
    assert "waits with your edits (goes with the drop)" in line
    assert store.pending()


@pytest.mark.signed
def test_a_state_from_two_attempts_made_apart_waits_with_both(tmp_path) -> None:
    """One edit given on two copies and resumed after the join: its state
    is made from both attempts, neither under the other, and waits for
    the person's word on them."""
    here = RecordStore(tmp_path / "here")
    base = keep(here, V1, TEXT, export(TEXT, BLOCKS))
    here.confirm()
    there = here.copy_to(tmp_path / "there")
    filed(here, "p1", on=base.id)
    filed(there, "p1", on=base.id)
    for store in joined(here, there, tmp_path):
        text = TEXT + " Edited."
        keep(
            store,
            V1,
            text,
            export(text, BLOCKS),
            Command.EDIT,
            started_from=base,
            answers=["p1"],
        )
        assert versions.latest(store) == base


@pytest.mark.signed
def test_a_stack_dropped_on_one_copy_stays_dropped_joined_either_way(
    tmp_path,
) -> None:
    """A twin confirmed on one copy; on the other, an agent's edit kept on
    it, then the twin dropped there: joined either way, the dropped copy's
    stack does not come back."""
    here, there = twins(tmp_path, drop=False)
    twin = versions.latest(there, waiting=True)
    assert twin is not None
    filed(there, "k", person=False, on=twin.id)
    more = TEXT + " Edited. Theirs."
    gone = keep(
        there,
        V1,
        more,
        export(more, BLOCKS),
        Command.EDIT,
        started_from=twin,
        answers=["p1", "k"],
    )
    there.confirm(dropped=["p1"])
    for store in joined(here, there, tmp_path / "joined"):
        assert gone not in versions.states(store, waiting=True)
        assert "k" not in store.authoritative()


def resumed(tmp_path: Path) -> tuple[versions.State, list[RecordStore]]:
    """One edit `p1` given on two copies, joined either way, and resumed:
    its state kept on each join, from both attempts."""
    here = RecordStore(tmp_path / "here")
    base = keep(here, V1, TEXT, export(TEXT, BLOCKS))
    here.confirm()
    there = here.copy_to(tmp_path / "there")
    filed(here, "p1", on=base.id)
    filed(there, "p1", on=base.id)
    stores = list(joined(here, there, tmp_path))
    for store in stores:
        text = TEXT + " Edited."
        keep(
            store,
            V1,
            text,
            export(text, BLOCKS),
            Command.EDIT,
            started_from=base,
            answers=["p1"],
        )
    return base, stores


@pytest.mark.signed
def test_an_edit_given_on_two_copies_is_dropped_whole_by_its_key(tmp_path) -> None:
    """Its key names both attempts: `--drop` takes both, and the state
    made from them goes, joined either way."""
    base, stores = resumed(tmp_path)
    for store in stores:
        store.confirm(dropped=["p1"])
        assert versions.latest(store, waiting=True) == base


@pytest.mark.signed
def test_one_of_two_attempts_dropped_by_its_entry_leaves_the_state(tmp_path) -> None:
    """A state made from two attempts made apart stands on either: one
    dropped by its entry and the other confirmed, the state takes effect,
    joined either way."""
    base, stores = resumed(tmp_path)
    for store in stores:
        entries = sorted(p.entry for p in store.pending() if p.answer.key == "p1")
        store.confirm(dropped=[entries[0]])
        assert versions.latest(store) != base


@pytest.mark.signed
def test_an_edit_built_on_one_dropped_on_another_copy_is_named_as_such(
    tmp_path,
) -> None:
    """Dropped on one copy, built on in the other: the confirmation after
    the join names it as built on an edit dropped already, with no "keep
    it" to offer."""
    store = RecordStore(tmp_path / "store")
    base = keep(store, V1, TEXT, export(TEXT, BLOCKS))
    store.confirm()
    filed(store, "p1")
    text = TEXT + " Edited."
    first = keep(
        store,
        V1,
        text,
        export(text, BLOCKS),
        Command.EDIT,
        started_from=base,
        answers=["p1"],
    )
    there = store.copy_to(tmp_path / "there")
    store.confirm(dropped=["p1"])
    filed(there, "p3", on=first.id)
    for one in joined(store, there, tmp_path / "joined"):
        with pytest.raises(RecordError, match="built on an edit dropped already"):
            one.confirm()


@pytest.mark.signed
def test_confirm_lists_a_drop_made_before_as_no_part_of_this_one(
    tmp_path, capsys
) -> None:
    """After a drop, `confirm --list` names no state the earlier drop took
    as this drop's, and a new edit's drop names only its own."""
    store, _, (first, second) = sitting(tmp_path)
    store.confirm(dropped=["p2"])
    where = [str(tmp_path), "--store", str(store.directory)]
    capsys.readouterr()
    assert main(["confirm", *where, "--list"]) == 0
    assert "takes back" not in capsys.readouterr().out

    filed(store, "z", on=first.id)
    assert main(["confirm", *where, "--drop", "z", "--list"]) == 0
    assert second.id not in capsys.readouterr().out


@pytest.mark.signed
def test_a_refusal_names_each_edit_by_why_it_must_go(tmp_path) -> None:
    """One edit built on this drop, another on one dropped before: the
    refusal says which is which, and offers "keep it" for this one alone."""
    store, base, (first, second) = sitting(tmp_path)
    there = store.copy_to(tmp_path / "there")
    store.confirm(dropped=["p2"])
    filed(there, "late", on=second.id)
    one, _ = joined(store, there, tmp_path / "joined")
    filed(one, "q1")
    text = TEXT + " Edited. Q."
    q = keep(
        one,
        V1,
        text,
        export(text, BLOCKS),
        Command.EDIT,
        started_from=first,
        answers=[*first.answers, "q1"],
    )
    filed(one, "mine", on=q.id)
    with pytest.raises(RecordError) as refused:
        one.confirm(dropped=["q1"])
    said = str(refused.value)
    assert "the edits late are built on an edit dropped already" in said
    assert "the edits mine are built on one you drop" in said


@pytest.mark.signed
def test_confirm_lists_each_attempt_of_an_edit_given_on_two_copies(
    tmp_path, capsys
) -> None:
    """A key with two pending attempts is listed with each one's entry,
    which `--drop` names apart."""
    _, stores = resumed(tmp_path)
    store = stores[0]
    entries = sorted(p.entry for p in store.pending() if p.answer.key == "p1")
    capsys.readouterr()
    assert (
        main(["confirm", str(tmp_path), "--store", str(store.directory), "--list"]) == 0
    )
    out = capsys.readouterr().out
    assert all(e[:12] in out for e in entries)
