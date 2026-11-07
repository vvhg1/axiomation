"""The record store: answers and cut corrections, append-only."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

import record_file
from axiomation import keys
from axiomation.cutter.model import GroupKind, PartKind, Role, Segment
from axiomation.evidence import ReadStatus
from axiomation.records import (
    RECORD,
    TIPS,
    Anchor,
    AnchorKind,
    AnswerRecord,
    Aspect,
    BasisElement,
    BasisKind,
    CorrectionClass,
    CutCorrection,
    CutOp,
    Declared,
    Delta,
    Depth,
    Edge,
    EdgeKind,
    EdgeRecords,
    EdgeTarget,
    Entry,
    EntryKind,
    Finding,
    FindingAnswer,
    FindingStatus,
    Flag,
    Frame,
    ImpactClass,
    Judgment,
    JudgmentRecords,
    Mark,
    Partial,
    RecordError,
    RecordKind,
    RecordStore,
    SelfMark,
    Signal,
    Standing,
    Status,
    Subject,
    TargetKind,
    TemplateRef,
    Verdict,
    WorkUnread,
    _entry_line,
)


def answer(key: str = "k1", attempt: int = 1, valid: bool = True, **fields):
    values = dict(
        key=key,
        kind="cut-review",
        stage="boundaries",
        template=TemplateRef("cut-review-boundaries", "0123456789ab"),
        attempt=attempt,
        host="opencode",
        model="opencode/deepseek-v4.1-flash",
        time="2026-09-29T12:00:00Z",
        seconds=12.5,
        cost=0.0123,
        tokens={"input": 1000, "output": 200},
        valid=valid,
        problems=() if valid else ("names block x, which does not exist",),
        records=(),
    )
    values.update(fields)
    return AnswerRecord(**values)


def split(cid: str, key: str | None = "k1") -> CutCorrection:
    return CutCorrection(
        id=cid,
        op=CutOp.SPLIT,
        cls=CorrectionClass.ARGUMENT_BOUNDARY,
        why="two claims",
        answer=key,
        block="main.tex:4:1",
        at=("Second sentence", "Third sentence"),
    )


EVERY_OP = [
    split("c1"),
    CutCorrection("c2", CutOp.MERGE, CorrectionClass.FOOTNOTE, "w", "k1",
                  blocks=("a:1:1", "a:2:1", "a:3:1")),
    CutCorrection("c3", CutOp.REBOUND, CorrectionClass.OTHER, "w", "k1",
                  blocks=("a:1:1", "a:2:1"), at=("Hence",)),
    CutCorrection("c4", CutOp.GROUP, CorrectionClass.DERIVATION_BEFORE_THEOREM,
                  "w", "k1", kind=GroupKind.DERIVATION, first="a:1:1",
                  last="a:2:1", names="a:3:1"),
    CutCorrection("c5", CutOp.UNGROUP, CorrectionClass.OTHER, "w", "k1",
                  group="proof@a:1:1"),
    CutCorrection("c6", CutOp.ATTACH, CorrectionClass.UNATTACHED_PROOF, "w",
                  "k1", group="proof@a:1:1", names="none"),
    CutCorrection("c7", CutOp.PART, CorrectionClass.MISSED_PART, "w", "k1",
                  block="a:1:1", kind=PartKind.ITEM, marker="(a)", at=("(a) x",)),
    CutCorrection("c8", CutOp.UNPART, CorrectionClass.WRONG_PART, "w", "k1",
                  block="a:1:1", marker="(a)"),
    CutCorrection("c9", CutOp.ROLES, CorrectionClass.STATEMENT_DEFINES, "w",
                  "k1", block="a:1:1", roles=(Role.DEFINITION,),
                  segments=(Segment("", Role.DEFINITION),)),
    CutCorrection("c10", CutOp.ROLES, CorrectionClass.OTHER, "w", "k1",
                  block="a:1:1"),
    CutCorrection("c11", CutOp.ROLES, CorrectionClass.OTHER, "w", "k1",
                  block="a:1:1", roles=(Role.DEFINITION, Role.CLAIM),
                  segments=(Segment("", Role.DEFINITION),
                            Segment("and so", Role.CLAIM))),
    # a span inside the block that plays none
    CutCorrection("c12", CutOp.ROLES, CorrectionClass.OTHER, "w", "k1",
                  block="a:1:1", roles=(Role.DEFINITION,),
                  segments=(Segment("", Role.DEFINITION),
                            Segment("To make it", None))),
]  # fmt: skip


def _without(data: dict, field: str, key: str | None = None) -> dict:
    """`data` as a writer before `field` left it; only the answer `key`'s
    where one is named."""
    if key is not None and data["key"] != key:
        return data
    return {k: v for k, v in data.items() if k != field}


def test_both_kinds_round_trip(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "store")
    first = answer(records=tuple(c.id for c in EVERY_OP))
    store.append(first, EVERY_OP)

    again = RecordStore(tmp_path / "store")
    assert again.answers() == (first,)
    assert again.corrections() == tuple(EVERY_OP)
    assert again.problems() == ()
    assert len(record_file.entries(tmp_path / "store")) == 1
    lines = record_file.written(tmp_path / "store", RecordKind.CUT_CORRECTIONS)
    assert lines[0] == {
        "id": "c1",
        "op": "split",
        "class": "argument-boundary",
        "why": "two claims",
        "answer": "k1",
        "block": "main.tex:4:1",
        "at": ["Second sentence", "Third sentence"],
    }


def test_a_judge_s_answer_names_the_author_it_judged(tmp_path: Path) -> None:
    """A judge's answer names the author answer it reviewed."""
    store = RecordStore(tmp_path)
    judge = answer(key="j1", stage="judge", judges="k1", records=("c1",))
    store.append(judge, [split("c1", "j1")])

    again = RecordStore(tmp_path)
    assert again.answers() == (judge,)
    assert again.answers()[0].judges == "k1"


def test_an_answer_keeps_its_effort_level(tmp_path: Path) -> None:
    """The host's effort level is recorded on each answer, null where the
    host gives none."""
    store = RecordStore(tmp_path)
    store.append(answer(key="k1", effort="high"), [])
    store.append(answer(key="k2"), [])

    first, second = RecordStore(tmp_path).answers()
    assert (first.effort, second.effort) == ("high", None)


def test_an_answer_keeps_its_profile(tmp_path: Path) -> None:
    """The profile a run is under is recorded on each answer, null where it
    runs under none."""
    store = RecordStore(tmp_path)
    store.append(answer(key="k1", profile="standard"), [])
    store.append(answer(key="k2"), [])

    first, second = RecordStore(tmp_path).answers()
    assert (first.profile, second.profile) == ("standard", None)


def test_tokens_per_model_round_trip_written_only_where_set(tmp_path: Path) -> None:
    """A session that ran two models keeps each one's counts; one that ran
    a single model writes no such field, as before."""
    store = RecordStore(tmp_path)
    by_model = {"opus": {"output": 7, "cache_write_1h": 2}, "haiku": {"output": 3}}
    store.append(answer(key="k1", tokens_by_model=by_model), [])
    store.append(answer(key="k2"), [])

    first, second = RecordStore(tmp_path).answers()
    assert (first.tokens_by_model, second.tokens_by_model) == (by_model, {})
    assert "tokens_by_model" not in record_file.answers(tmp_path)[1]


def test_the_first_valid_answer_per_key_is_authoritative(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    failed = answer(attempt=1, valid=False)
    valid = answer(attempt=2, records=("c1",))
    later = answer(attempt=3)
    other = answer(key="k2", valid=False)
    store.append(failed)
    store.append(valid, [split("c1")])
    store.append(later)
    store.append(other)

    assert store.answers() == (failed, valid, later, other)
    assert store.authoritative() == {"k1": valid}
    assert store.corrections_of("k1") == (split("c1"),)
    assert store.corrections_of("k2") == ()


def test_a_store_reads_what_another_appended_since_it_last_read(
    tmp_path: Path,
) -> None:
    """A store keeps what it parsed while the files stand: an append by
    another store on the same directory, as by another process, is read."""
    reading, writing = RecordStore(tmp_path), RecordStore(tmp_path)
    reading.append(answer(records=("c1",)), [split("c1")])
    assert [c.id for c in reading.corrections()] == ["c1"]

    writing.append(answer(key="k2", records=("c2",)), [split("c2", "k2")])

    assert [a.key for a in reading.answers()] == ["k1", "k2"]
    assert [c.id for c in reading.corrections()] == ["c1", "c2"]
    assert reading.corrections_of("k2") == (split("c2", "k2"),)


def test_corrections_of_keeps_file_order(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    store.append(answer(records=("c2", "c1")), [split("c2"), split("c1")])
    store.append(answer(key="k2", records=("c3",)), [split("c3", "k2")])

    assert [c.id for c in store.corrections_of("k1")] == ["c2", "c1"]
    assert [c.id for c in store.corrections()] == ["c2", "c1", "c3"]


def test_append_refuses_what_would_not_load(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    with pytest.raises(ValueError, match="records"):
        store.append(answer(records=()), [split("c1")])
    with pytest.raises(ValueError, match="names answer"):
        store.append(answer(records=("c1",)), [split("c1", key="other")])
    with pytest.raises(ValueError, match="at: split does not take|marker"):
        store.append(answer(records=("c1",)), [replace(split("c1"), marker="(a)")])
    store.append(answer(records=("c1",)), [split("c1")])
    with pytest.raises(ValueError, match="in the store already"):
        store.append(answer(attempt=2, records=("c1",)), [split("c1")])
    assert len(store.answers()) == 1


@pytest.mark.parametrize(
    ("correction", "problem"),
    [
        (replace(split("c"), at=()), "at: missing, split takes it"),
        (replace(split("c"), group="g"), "group: split does not take it"),
        (
            CutCorrection("c", CutOp.MERGE, CorrectionClass.OTHER, "w",
                          blocks=("a:1:1",)),
            "blocks: a merge names two blocks or more",
        ),
        (
            CutCorrection("c", CutOp.REBOUND, CorrectionClass.OTHER, "w",
                          blocks=("a", "b", "c"), at=("x",)),
            "blocks: a rebound names two blocks",
        ),
        (
            CutCorrection("c", CutOp.PART, CorrectionClass.OTHER, "w",
                          block="a", kind=GroupKind.STEP, marker="m", at=("x",)),
            "kind: <GroupKind.STEP: 'step'> is not a part kind",
        ),
        (
            CutCorrection("c", CutOp.ROLES, CorrectionClass.OTHER, "w",
                          block="a", roles=("lemma",)),
            "roles: 'lemma' is not a role",
        ),
        (
            CutCorrection("c", CutOp.ROLES, CorrectionClass.OTHER, "w",
                          block="a", roles=(Role.CLAIM,)),
            "segments: a single-role block is one segment",
        ),
        (
            CutCorrection("c", CutOp.ROLES, CorrectionClass.OTHER, "w",
                          block="a", roles=(Role.DEFINITION, Role.CLAIM),
                          segments=(Segment("", Role.CLAIM),)),
            "segments: the segments' roles are not the block's roles",
        ),
    ],
)  # fmt: skip
def test_check_names_each_broken_field(correction, problem) -> None:
    assert correction.check() == (problem,)


def test_every_op_is_well_formed() -> None:
    assert [c.check() for c in EVERY_OP] == [()] * len(EVERY_OP)


def test_a_proposal_s_evidence_round_trips_and_is_written_only_when_given(
    tmp_path: Path,
) -> None:
    proposed = replace(split("c1"), evidence=("Second sentence", "Third"))
    store = RecordStore(tmp_path / "store")
    store.append(answer(records=("c1", "c2")), [proposed, split("c2")])

    again = RecordStore(tmp_path / "store")
    assert again.corrections() == (proposed, split("c2"))
    lines = record_file.written(tmp_path / "store", RecordKind.CUT_CORRECTIONS)
    assert lines[0]["evidence"] == ["Second sentence", "Third"]
    # A correction without evidence writes the line it wrote before
    # corrections carried evidence.
    assert "evidence" not in lines[1]


def _store_with_correction(tmp_path: Path, data: dict) -> RecordStore:
    """A store whose first answer holds, after its good correction, a
    second one as `data` says it."""
    store = RecordStore(tmp_path)
    store.append(answer(records=("c1",)), [split("c1")])

    def add(body: dict) -> dict:
        body["records"][RecordKind.CUT_CORRECTIONS].append(data)
        return body

    record_file.rewrite(tmp_path, add)
    return store


def _store_with_answer(tmp_path: Path, data: dict) -> RecordStore:
    """A store whose first answer is good and whose second entry is an
    answer as `data` says it."""
    store = RecordStore(tmp_path)
    store.append(answer(records=("c1",)), [split("c1")])
    record_file.append(tmp_path, EntryKind.ANSWER, {"answer": data, "records": {}})
    return store


def _correction_data(**changes) -> dict:
    data = {
        "id": "c9",
        "op": "split",
        "class": "footnote",
        "why": "w",
        "answer": None,
        "block": "a:1:1",
        "at": ["x"],
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not ...}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_correction_data(colour="red"), ":2: field colour: unknown"),
        (_correction_data(at=...), ":2: field at: missing"),
        (_correction_data(op="shatter"), ":2: field op: 'shatter' is not one of"),
        (_correction_data(**{"class": "vibes"}), ":2: field class: 'vibes'"),
        (_correction_data(at="x"), ":2: field at: expected list"),
        (_correction_data(evidence="x"), ":2: field evidence: expected list"),
        (_correction_data(evidence=[1]), ":2: field evidence: expected strings"),
        (_correction_data(marker="(a)"), ":2: field marker: unknown"),
        (
            _correction_data(op="role", block="a", at=..., role="lemma"),
            ":2: field op: 'role' is not one of",
        ),
        (
            _correction_data(
                op="roles", block="a", at=..., roles=["lemma"], segments=[]
            ),
            ":2: field roles: 'lemma' is not one of",
        ),
        (
            _correction_data(
                op="roles",
                block="a",
                at=...,
                roles=[],
                segments=[{"starts": "", "colour": "red"}],
            ),
            ":2: field segments.colour: unknown",
        ),
    ],
)
def test_a_bad_correction_line_raises_naming_file_line_and_field(
    tmp_path: Path, data: dict, message: str
) -> None:
    store = _store_with_correction(tmp_path, data)
    with pytest.raises(RecordError) as raised:
        store.corrections()
    assert f"{RECORD}{message}" in str(raised.value)


def _answer_data(**changes) -> dict:
    data = json.loads(json.dumps({
        "key": "k9", "kind": "cut-review", "stage": "groups",
        "template": {"name": "t", "version": "v"}, "attempt": 1,
        "host": "h", "model": "m", "time": "t", "seconds": 1, "cost": 0,
        "tokens": {}, "valid": False, "problems": [], "records": [],
        "judges": None, "effort": None, "profile": None,
    }))  # fmt: skip
    data.update(changes)
    return {k: v for k, v in data.items() if v is not ...}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_answer_data(colour="red"), ":3: field colour: unknown"),
        (_answer_data(cost=...), ":3: field cost: missing"),
        (_answer_data(valid="yes"), ":3: field valid: expected bool"),
        (_answer_data(attempt=True), ":3: field attempt: expected a number"),
        (_answer_data(template={"name": "t"}), ":3: field template.version"),
        (
            _answer_data(template={"name": "t", "version": "v", "asks": {"ruling": 0}}),
            ":3: field template.asks: expected versions from 1",
        ),
        (_answer_data(tokens={"input": "many"}), ":3: field tokens.input"),
        (
            _answer_data(tokens_by_model={"opus": {"output": 1.5}}),
            ":3: field tokens_by_model.opus.output",
        ),
        (_answer_data(judges=7), ":3: field judges: expected str or null"),
        (_answer_data(effort=3), ":3: field effort: expected str or null"),
        (_answer_data(profile=["cheap"]), ":3: field profile: expected str or null"),
    ],
)
def test_a_bad_answer_line_raises_naming_file_line_and_field(
    tmp_path: Path, data: dict, message: str
) -> None:
    store = _store_with_answer(tmp_path, data)
    with pytest.raises(RecordError) as raised:
        store.answers()
    assert f"{RECORD}{message}" in str(raised.value)


def test_a_review_s_answer_keeps_what_its_brief_asks(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    asked = TemplateRef("review-argument", "v", (("ruling", 2),))
    store.append(answer(template=asked))
    (read,) = RecordStore(tmp_path).answers()
    assert read.template == asked
    (line,) = record_file.answers(tmp_path)
    assert line["template"] == {
        "name": "review-argument",
        "version": "v",
        "asks": {"ruling": 2},
    }


def test_a_line_that_is_not_json_raises(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    (tmp_path / RECORD).write_text("{not json\n")
    with pytest.raises(RecordError, match=f"{RECORD}:1: not an entry"):
        store.answers()
    (tmp_path / RECORD).unlink()
    line = record_file.sealed_line(tmp_path, EntryKind.ANSWER, [], "{}")
    path = tmp_path / RECORD
    grant = path.read_text()
    digest = json.loads(grant)["hash"]
    unparsed = record_file.sealed_line(
        tmp_path, EntryKind.ANSWER, [digest], "{not json}"
    )
    forged = '{"hash":"' + "0" * 64 + line[len('{"hash":"') + 64 :]
    path.write_text(grant + forged)
    # the header is the cipher's associated data: a changed hash does not
    # open, and the entry is without effect
    assert RecordStore(tmp_path).kept_states() == ()
    assert any(
        f"{RECORD}:2: entry 000000000000: " in p
        for p in RecordStore(tmp_path).problems()
    )
    # a grant is not sealed: its hash is checked against its body
    path.write_text('{"hash":"' + "0" * 64 + grant[len('{"hash":"') + 64 :])
    with pytest.raises(RecordError, match=f"{RECORD}:1: entry 0+ does not hold"):
        RecordStore(tmp_path).kept_states()
    # an answer's body is read the first time an answer is wanted: the kept
    # states read without it
    path.write_text(grant + unparsed)
    assert RecordStore(tmp_path).kept_states() == ()
    with pytest.raises(RecordError, match=f"{RECORD}:2: not JSON"):
        RecordStore(tmp_path).answers()


def test_an_entry_sealed_under_another_paper_key_is_without_effect(
    tmp_path: Path,
) -> None:
    """An entry whose body does not open under its grant's paper key was
    written without it: it counts for nothing, and a hostile line appended
    keeps no one from the record (docs/architecture.md § 4)."""
    RecordStore(tmp_path).append(answer())
    path = tmp_path / RECORD
    grant, entry = path.read_text().splitlines(True)
    parents = [json.loads(entry)["hash"]]
    stranger = _entry_line(
        EntryKind.ANSWER, parents, "someone", {}, 1, keys.paper_key()
    )[1]
    path.write_text(grant + entry + stranger)

    store = RecordStore(tmp_path)
    assert [a.key for a in store.answers()] == ["k1"]
    assert any(f"{RECORD}:3: " in p and "does not open" in p for p in store.problems())


def test_a_line_no_one_can_open_on_top_keeps_the_tip(tmp_path: Path) -> None:
    """A hostile line appended over the head leaves the tip on the entries
    read, so the record cut back under it afterwards is still refused."""
    paper = tmp_path / "paper"
    store = RecordStore(paper)
    _answered(store, 1)
    whole = (paper / RECORD).read_text()
    _answered(store, 2)
    grown = (paper / RECORD).read_text()
    parents = [json.loads(grown.splitlines()[-1])["hash"]]
    stranger = _entry_line(
        EntryKind.ANSWER, parents, "someone", {}, 1, keys.paper_key()
    )[1]
    (paper / RECORD).write_text(grown + stranger)
    assert [a.key for a in RecordStore(paper).answers()] == ["e1", "e2"]

    (paper / RECORD).write_text(whole)

    with pytest.raises(RecordError, match="no longer reaches the tip"):
        RecordStore(paper).answers()


def test_a_body_not_sealed_is_refused_unless_a_grant(tmp_path: Path) -> None:
    RecordStore(tmp_path).append(answer())
    path = tmp_path / RECORD
    whole = path.read_text()
    parents = [json.loads(whole.splitlines()[-1])["hash"]]
    plain = _entry_line(EntryKind.ANSWER, parents, "someone", {}, 1, None)[1]
    path.write_text(whole + plain)

    with pytest.raises(RecordError, match=f"{RECORD}:3: .* only a grant is not"):
        RecordStore(tmp_path).kept_states()


def test_a_record_is_read_only_with_a_key_its_grants_wrap(
    tmp_path: Path, monkeypatch
) -> None:
    """The record holds no text in clear, and opens with the writer's
    reading key or the recovery key, never a stranger's."""
    RecordStore(tmp_path).append(answer(key="the-key-in-clear"))
    assert "the-key-in-clear" not in (tmp_path / RECORD).read_text()

    stranger = tmp_path / "stranger"
    keys.made(stranger)
    monkeypatch.setenv(keys.ENV, str(stranger))
    with pytest.raises(RecordError, match="no trusted grant in the record wraps"):
        RecordStore(tmp_path).answers()


def test_a_truncated_last_line_is_a_problem_not_an_error(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    store.append(answer(records=("c1",)), [split("c1")])
    path = tmp_path / RECORD
    path.write_text(path.read_text() + '{"hash":"0f","kind":"answer","for')

    assert [c.id for c in store.corrections()] == ["c1"]
    assert store.problems() == (f"{path}:3: the last line has no newline; skipped",)
    # the next append drops the torn line instead of running on from it
    store.append(answer(key="k2", records=("c3",)), [split("c3", "k2")])
    assert [c.id for c in store.corrections()] == ["c1", "c3"]
    assert store.problems() == ()


def test_an_answer_whose_corrections_were_never_written_is_ignored(
    tmp_path: Path,
) -> None:
    """A crash between the two files leaves an answer without its
    corrections: reported and ignored, and a resumed run appends after it."""
    store = RecordStore(tmp_path)
    store.append(answer(records=("c1",)), [split("c1")])
    store.append(
        answer(key="k2", records=("c2", "c3")), [split("c2", "k2"), split("c3", "k2")]
    )
    record_file.rewrite_records(
        tmp_path, RecordKind.CUT_CORRECTIONS, lambda c: None if c["id"] == "c3" else c
    )

    assert [a.key for a in store.answers()] == ["k1"]
    assert [c.id for c in store.corrections()] == ["c1"]
    assert store.authoritative() == {"k1": answer(records=("c1",))}
    [problem] = store.problems()
    assert "answer k2 attempt 1" in problem and "'c3'" in problem

    store.append(answer(key="k2", attempt=2, records=("c4",)), [split("c4", "k2")])
    assert [c.id for c in store.corrections_of("k2")] == ["c4"]


def test_two_threads_appending_give_whole_lines(tmp_path: Path) -> None:
    def work(thread: int) -> None:
        store = RecordStore(tmp_path)
        for n in range(50):
            key = f"t{thread}-{n}"
            ids = (f"{key}-1", f"{key}-2")
            store.append(
                answer(key=key, records=ids),
                [split(ids[0], key), split(ids[1], key)],
            )

    threads = [threading.Thread(target=work, args=(t,)) for t in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    store = RecordStore(tmp_path)
    assert len(store.answers()) == 100
    assert len(store.corrections()) == 200
    assert store.problems() == ()
    for a in store.answers():
        assert [c.id for c in store.corrections_of(a.key)] == list(a.records)


def test_a_resumed_run_asks_an_orphaned_key_again_under_a_new_attempt(
    tmp_path: Path,
) -> None:
    """A valid attempt 1 orphaned with one of its two
    corrections written; the resumed run takes the next attempt number and
    fresh ids, and its answer is authoritative and alone in
    `corrections_of`."""
    store = RecordStore(tmp_path)
    store.append(
        answer(key="k2", records=("k2-1-1", "k2-1-2")),
        [split("k2-1-1", "k2"), split("k2-1-2", "k2")],
    )
    record_file.rewrite_records(
        tmp_path,
        RecordKind.CUT_CORRECTIONS,
        lambda c: None if c["id"] == "k2-1-2" else c,
    )
    assert store.authoritative() == {}

    with pytest.raises(ValueError, match="attempt 1 is in the store already"):
        store.append(answer(key="k2", attempt=1))
    assert store.next_attempt("k2") == 2
    assert store.next_attempt("never-asked") == 1
    resumed = answer(key="k2", attempt=2, records=("k2-2-1",))
    store.append(resumed, [split("k2-2-1", "k2")])

    assert store.authoritative() == {"k2": resumed}
    assert [c.id for c in store.corrections_of("k2")] == ["k2-2-1"]
    assert [c.id for c in store.corrections()] == ["k2-2-1"]
    [problem] = store.problems()
    assert "answer k2 attempt 1" in problem


def test_only_the_authoritative_answer_s_corrections_are_returned(
    tmp_path: Path,
) -> None:
    store = RecordStore(tmp_path)
    store.append(answer(attempt=1, records=("k1-1-1",)), [split("k1-1-1")])
    store.append(answer(attempt=2, records=("k1-2-1",)), [split("k1-2-1")])

    assert [c.id for c in store.corrections_of("k1")] == ["k1-1-1"]
    assert [c.id for c in store.corrections()] == ["k1-1-1", "k1-2-1"]


def test_a_failed_answer_makes_no_corrections(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    with pytest.raises(ValueError, match="failed, and makes no records"):
        store.append(answer(valid=False, records=("c1",)), [split("c1")])
    assert store.answers() == ()


# --- the edge stage's records ---------------------------------------


def edge_records(key: str | None = "e1", prefix: str = "r") -> EdgeRecords:
    """One record of each kind, ids `<prefix>1` to `<prefix>6`."""
    return EdgeRecords(
        entries=(
            Entry(
                f"{prefix}1", key, "main.tex:7:1", "gain", "The gain", Role.DEFINITION
            ),
        ),
        edges=(
            Edge(
                f"{prefix}2",
                key,
                "main.tex:15:1",
                EdgeKind.USES,
                EdgeTarget(TargetKind.BLOCK, "main.tex:7:1"),
                ("By Definition",),
                parts=("gain",),
                mark=Mark.REF,
            ),
            Edge(
                f"{prefix}3",
                key,
                "proof@main.tex:15:1",
                EdgeKind.JUSTIFIES,
                EdgeTarget(TargetKind.FILE, "main.tex"),
                ("Hence",),
                where_used=False,
                note="a note",
            ),
        ),
        selfs=(SelfMark(f"{prefix}4", key, "main.tex:15:1", ("Hence",)),),
        standings=(
            Standing(f"{prefix}5", key, "main.tex:12:1", "bounded", Status.JUSTIFIED),
        ),
        frame=(Frame(f"{prefix}6", key, "main.tex:4:1"),),
    )


def edges_answer(key: str = "e1", prefix: str = "r", **fields) -> AnswerRecord:
    fields.setdefault("records", tuple(f"{prefix}{n}" for n in range(1, 7)))
    return answer(key=key, kind="edges", stage="edges", **fields)


def test_each_edge_record_kind_round_trips(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    store.append(edges_answer(), edges=edge_records())

    again = RecordStore(tmp_path)
    assert again.edge_records_of("e1") == edge_records()
    assert again.problems() == ()
    for kind in (
        RecordKind.ENTRIES,
        RecordKind.EDGES,
        RecordKind.SELF_MARKS,
        RecordKind.STANDINGS,
        RecordKind.FRAME,
    ):
        assert record_file.written(tmp_path, kind), kind
    first, second = record_file.written(tmp_path, RecordKind.EDGES)
    assert first == {
        "id": "r2",
        "answer": "e1",
        "source": "main.tex:15:1",
        "kind": "uses",
        "target": {"block": "main.tex:7:1"},
        "parts": ["gain"],
        "evidence": ["By Definition"],
        "mark": "ref",
        "where_used": False,
        "note": None,
    }
    assert second["target"] == {"file": "main.tex"}


def test_a_partial_and_a_declaration_round_trip_written_only_when_set(
    tmp_path: Path,
) -> None:
    """`partial` on an edge and a self mark, `declared` on a
    standing; a record without them reads as None (the round trip above
    writes none)."""
    plain = edge_records()
    marked = replace(
        plain,
        edges=(plain.edges[0], replace(plain.edges[1], partial=Partial.ANALOGY)),
        selfs=(replace(plain.selfs[0], partial=Partial.SKETCH),),
        standings=(
            replace(
                plain.standings[0],
                status=Status.UNSUPPORTED,
                declared=Declared.PROMISED,
            ),
        ),
    )
    store = RecordStore(tmp_path)
    store.append(edges_answer(), edges=marked)
    assert RecordStore(tmp_path).edge_records_of("e1") == marked
    first, second = record_file.written(tmp_path, RecordKind.EDGES)
    assert "partial" not in first
    assert second["partial"] == "analogy"
    (self_mark,) = record_file.written(tmp_path, RecordKind.SELF_MARKS)
    assert self_mark["partial"] == "sketch"
    (standing,) = record_file.written(tmp_path, RecordKind.STANDINGS)
    assert standing["declared"] == "promised"


def test_a_generalizes_flag_round_trips_written_only_when_set(tmp_path: Path) -> None:
    """A record without the flag reads as unflagged (the round trip above
    writes none)."""
    plain = edge_records()
    flagged = replace(
        plain, edges=(replace(plain.edges[0], generalizes=True), plain.edges[1])
    )
    RecordStore(tmp_path).append(edges_answer(), edges=flagged)
    assert RecordStore(tmp_path).edge_records_of("e1") == flagged
    first, second = record_file.written(tmp_path, RecordKind.EDGES)
    assert first["generalizes"] is True
    assert "generalizes" not in second


def test_an_assumed_flag_round_trips_written_only_when_set(tmp_path: Path) -> None:
    """With the claims of its source it is a hypothesis for; a record from
    before the flag reads as unflagged."""
    plain = edge_records()
    flagged = replace(
        plain,
        edges=(
            replace(plain.edges[0], assumed=True, source_claims=("f is red",)),
            plain.edges[1],
        ),
    )
    RecordStore(tmp_path).append(edges_answer(), edges=flagged)
    assert RecordStore(tmp_path).edge_records_of("e1") == flagged
    first, second = record_file.written(tmp_path, RecordKind.EDGES)
    assert first["assumed"] is True
    assert first["source_claims"] == ["f is red"]
    assert "assumed" not in second


def test_an_answer_lists_its_corrections_then_its_edge_records(tmp_path) -> None:
    store = RecordStore(tmp_path)
    with pytest.raises(ValueError, match="the records are"):
        store.append(edges_answer(records=("r2", "r1")), edges=edge_records())
    with pytest.raises(ValueError, match="names answer"):
        store.append(edges_answer(), edges=edge_records(key="other"))
    with pytest.raises(ValueError, match="makes no records"):
        store.append(edges_answer(valid=False), edges=edge_records())

    assert store.answers() == ()  # nothing written above

    store.append(edges_answer(), edges=edge_records())
    with pytest.raises(ValueError, match="record id r1 is in the store already"):
        store.append(edges_answer(key="e2"), edges=edge_records(key="e2"))


def test_an_answer_whose_edge_records_were_cut_short_is_ignored(tmp_path) -> None:
    store = RecordStore(tmp_path)
    store.append(edges_answer(), edges=edge_records())
    record_file.rewrite_records(tmp_path, RecordKind.STANDINGS, lambda r: None)

    assert store.answers() == ()
    assert store.edge_records_of("e1") == EdgeRecords()
    [problem] = store.problems()
    assert "answer e1 attempt 1" in problem and "'r5'" in problem

    store.append(edges_answer(attempt=2, prefix="s"), edges=edge_records(prefix="s"))
    assert store.edge_records_of("e1").ids == tuple(f"s{n}" for n in range(1, 7))


def test_only_the_authoritative_answer_s_edge_records_are_returned(tmp_path) -> None:
    store = RecordStore(tmp_path)
    store.append(edges_answer(), edges=edge_records())
    store.append(edges_answer(attempt=2, prefix="s"), edges=edge_records(prefix="s"))

    assert store.edge_records_of("e1") == edge_records()
    assert store.edge_records_of("unknown") == EdgeRecords()


def test_a_bad_edge_record_line_raises_naming_file_line_and_field(tmp_path) -> None:
    store = RecordStore(tmp_path)
    store.append(edges_answer(), edges=edge_records())
    record_file.rewrite_records(
        tmp_path,
        RecordKind.EDGES,
        lambda r: {**r, "kind": "leans"} if r["kind"] == "uses" else r,
    )

    with pytest.raises(RecordError, match=rf"{RECORD}:2: field kind: 'leans'"):
        store.edge_records_of("e1")


def test_a_store_written_before_the_edge_stage_loads(tmp_path) -> None:
    """A store whose entries hold none of the edge stage's records loads as
    it did."""
    store = RecordStore(tmp_path)
    store.append(answer(records=("c1",)), [split("c1")])

    assert record_file.written(tmp_path, RecordKind.EDGES) == []
    assert [c.id for c in RecordStore(tmp_path).corrections()] == ["c1"]
    assert store.edge_records_of("k1") == EdgeRecords()


# --- flags -----------------------------------------------------------


def flag(fid: str = "f1", key: str | None = "k1", **fields) -> Flag:
    values = dict(
        id=fid,
        answer=key,
        blocks=("main.tex:7:1",),
        signal=Signal.SPLIT,
        evidence=("The gain is the ratio",),
        note="two units",
    )
    values.update(fields)
    return Flag(**values)


def test_a_flag_round_trips(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    store.append(answer(records=("c1", "f1")), [split("c1")], flags=[flag()])

    again = RecordStore(tmp_path)
    assert again.flags_of("k1") == (flag(),)
    assert again.flags() == (flag(),)
    assert again.problems() == ()
    assert record_file.written(tmp_path, RecordKind.FLAGS) == [
        {
            "id": "f1",
            "answer": "k1",
            "blocks": ["main.tex:7:1"],
            "signal": "split",
            "evidence": ["The gain is the ratio"],
            "note": "two units",
        }
    ]


def test_an_answer_lists_its_flags_after_its_other_records(tmp_path) -> None:
    store = RecordStore(tmp_path)
    with pytest.raises(ValueError, match="the records are"):
        store.append(answer(records=("f1", "c1")), [split("c1")], flags=[flag()])
    with pytest.raises(ValueError, match="names answer"):
        store.append(answer(records=("f1",)), flags=[flag(key="other")])
    assert store.answers() == ()

    store.append(answer(records=("f1",)), flags=[flag()])
    with pytest.raises(ValueError, match="record id f1 is in the store already"):
        store.append(answer(key="k2", records=("f1",)), flags=[flag(key="k2")])


def test_an_answer_whose_flags_were_cut_short_is_ignored(tmp_path) -> None:
    store = RecordStore(tmp_path)
    store.append(answer(records=("f1",)), flags=[flag()])
    record_file.rewrite_records(tmp_path, RecordKind.FLAGS, lambda r: None)

    assert store.answers() == () and store.flags_of("k1") == ()
    [problem] = store.problems()
    assert "answer k1 attempt 1" in problem and "'f1'" in problem


def test_only_the_authoritative_answer_s_flags_are_returned(tmp_path) -> None:
    store = RecordStore(tmp_path)
    store.append(answer(records=("f1",)), flags=[flag()])
    store.append(answer(attempt=2, records=("f2",)), flags=[flag("f2")])

    assert store.flags_of("k1") == (flag(),)
    assert store.flags_of("unknown") == ()


def test_a_bad_flag_line_raises_naming_file_line_and_field(tmp_path) -> None:
    store = RecordStore(tmp_path)
    store.append(answer(records=("f1",)), flags=[flag()])
    record_file.rewrite_records(
        tmp_path, RecordKind.FLAGS, lambda r: {**r, "signal": "splat"}
    )

    with pytest.raises(RecordError, match=rf"{RECORD}:2: field signal: 'splat'"):
        store.answers()


def test_a_store_written_before_flags_loads_and_writes_the_same_bytes(
    tmp_path,
) -> None:
    store = RecordStore(tmp_path)
    store.append(answer(records=("c1",)), [split("c1")])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}

    again = RecordStore(tmp_path)
    assert again.flags() == () and again.flags_of("k1") == ()
    assert [c.id for c in again.corrections()] == ["c1"]
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert after == before
    assert record_file.written(tmp_path, RecordKind.FLAGS) == []


def test_a_decision_round_trips_and_is_written_only_when_given(tmp_path) -> None:
    store = RecordStore(tmp_path)
    ungroup = CutCorrection(
        "c1",
        CutOp.UNGROUP,
        CorrectionClass.OTHER,
        "merged",
        "k1",
        group="g",
        decision="c1",
    )
    store.append(answer(records=("c1", "c2")), [ungroup, split("c2")])

    first, second = record_file.written(tmp_path, RecordKind.CUT_CORRECTIONS)
    assert first["decision"] == "c1"
    assert "decision" not in second
    assert RecordStore(tmp_path).corrections() == (ungroup, split("c2"))


# --- an identity answer's lineage ----------------------------------------


def _identity(store, new_hash: str, n: int):
    import json as _json

    from axiomation.records import LineageLink
    from axiomation.resolver import Resolver

    return Resolver(store, clock=lambda: "2026-10-04T00:00:00+00:00").resolve_identity(
        str(n) * 64,
        "identity-1",
        _json.dumps({"new": new_hash}),
        (
            LineageLink("", None, ("a:1:1",), ("a:2:1",), "same", "text-map"),
            LineageLink("", None, ("a:3:1",), (), "deleted", "none"),
            LineageLink(
                "", None, ("a:4:1",), ("a:5:1", "a:6:1"), "split", "words", "a:6:1"
            ),
        ),
    )


def test_an_identity_answer_and_its_lineage_round_trip(tmp_path) -> None:
    from axiomation.records import IDENTITY, RecordStore

    store = RecordStore(tmp_path / "store")
    assert store.identity_for("h2") is None  # no lineage file yet: none
    answer = _identity(store, "h2", 1)
    assert (answer.kind, answer.host, answer.cost) == (IDENTITY, "axiomation", 0.0)
    assert len(record_file.written(tmp_path / "store", RecordKind.LINEAGE)) == 3
    again = RecordStore(tmp_path / "store")
    # its payload held as run data until its update keeps its step
    assert again.identity_for("h2") == replace(answer, raw="")
    assert again.held_identity(answer.key)["new"] == "h2"
    links = again.lineage_of(answer.key)
    assert [link.id for link in links] == list(answer.records)
    assert links[2].heir == "a:6:1" and links[1].new == ()
    assert again.problems() == ()


def test_two_identity_answers_to_one_version_are_refused_without_a_key(
    tmp_path,
) -> None:
    from axiomation.records import RecordError, RecordStore

    store = RecordStore(tmp_path / "store")
    first, second = _identity(store, "h2", 1), _identity(store, "h2", 2)
    with pytest.raises(RecordError, match="--from"):
        store.identity_for("h2")
    assert store.identity_for("h2", key=second.key) == replace(second, raw="")
    assert store.identity_for("h2", key=first.key) == replace(first, raw="")


# --- an update's Δ lines ---------------------------------------------------


def test_an_update_s_deltas_round_trip_after_the_edge_kinds(tmp_path) -> None:
    store = RecordStore(tmp_path)
    deltas = (
        Delta("r7", "e1", "main.tex:7:1", "gain", ImpactClass.NARROWED, "fewer"),
        Delta("r8", "e1", "main.tex:4:1", None, ImpactClass.REWORDED, "same claim"),
    )
    records = replace(edge_records(), deltas=deltas)
    store.append(edges_answer(records=records.ids), edges=records)

    again = RecordStore(tmp_path)
    assert again.edge_records_of("e1") == records
    assert records.ids[-2:] == ("r7", "r8")
    assert record_file.written(tmp_path, RecordKind.DELTAS)[1] == {
        "id": "r8",
        "answer": "e1",
        "block": "main.tex:4:1",
        "entry": None,
        "class": "reworded",
        "note": "same claim",
    }


def test_a_store_without_deltas_loads_and_writes_none(tmp_path) -> None:
    """An answer with no Δ line numbers and reads its records without
    them: no entry holds a delta, and none is needed to load."""
    store = RecordStore(tmp_path)
    store.append(edges_answer(), edges=edge_records())

    assert record_file.written(tmp_path, RecordKind.DELTAS) == []
    assert RecordStore(tmp_path).edge_records_of("e1") == edge_records()
    assert RecordStore(tmp_path).problems() == ()


def test_a_bad_delta_class_raises_naming_the_file_line_and_field(tmp_path) -> None:
    store = RecordStore(tmp_path)
    delta = Delta("r7", "e1", "main.tex:7:1", None, ImpactClass.MOVED, "elsewhere")
    records = replace(edge_records(), deltas=(delta,))
    store.append(edges_answer(records=records.ids), edges=records)
    record_file.rewrite_records(
        tmp_path, RecordKind.DELTAS, lambda r: {**r, "class": "shifted"}
    )

    with pytest.raises(RecordError, match=rf"{RECORD}:2: field class: 'shifted'"):
        store.edge_records_of("e1")


# --- judgments, findings and their answers ---------------------------------


def _filing() -> JudgmentRecords:
    node = BasisElement(BasisKind.NODE, "n2", "abc", ("K",))
    edge = BasisElement(BasisKind.EDGE, "n3", None, ("a gain",), ("uses", "n2"))
    return JudgmentRecords(
        judgments=(
            Judgment(
                "j1",
                "f1",
                "s1",
                Aspect.VALID,
                Subject("g1", "n2", "f is bounded"),
                Verdict.NOT_SHOWN,
                Depth.COMPUTATIONAL,
                "the step from (3) is missing",
                (node, edge),
                unread=(WorkUnread("baker1984", ReadStatus.ABSENT),),
            ),
        ),
        findings=(
            Finding(
                "j2",
                "f1",
                "s1",
                Aspect.WELL_POSED,
                3,
                "f undefined at 0",
                None,
                (
                    Anchor(AnchorKind.PART, "n2", "f is bounded"),
                    Anchor(AnchorKind.PAPER),
                ),
                (BasisElement(BasisKind.PAPER, "paper", "def"),),
            ),
            Finding(
                "j5",
                "f1",
                "s2",
                Aspect.WELL_POSED,
                3,
                "f undefined at 0",
                None,
                (Anchor(AnchorKind.PAPER),),
                (BasisElement(BasisKind.PAPER, "paper", "ghi"),),
                reaffirms="j2",
                evidence="0 is still in the domain",
            ),
        ),
        answers=(
            FindingAnswer("j3", "f1", "j2", FindingStatus.POSTPONED, "later"),
            FindingAnswer(
                "a2", "f1", "j2", FindingStatus.FIXED, "reworded", proposed=True
            ),
        ),
    )


def test_a_filing_s_records_read_back_as_written(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    filing = _filing()
    store.append(
        answer("f1", kind="judgment", stage="judgments", records=filing.ids),
        judgments=filing,
    )

    assert store.judgment_records() == filing
    # a proposal says so; an answer that is none writes no field
    kept = record_file.written(tmp_path, RecordKind.FINDING_ANSWERS)
    assert ["proposed" in line for line in kept] == [False, True]
    for kind in (RecordKind.JUDGMENTS, RecordKind.FINDINGS):
        assert record_file.written(tmp_path, kind), kind


def test_a_filing_cut_short_is_ignored_and_an_unknown_field_refused(
    tmp_path: Path,
) -> None:
    store = RecordStore(tmp_path)
    filing = _filing()
    store.append(
        answer("f1", kind="judgment", stage="judgments", records=(*filing.ids, "j4")),
        judgments=replace(
            filing, answers=(*filing.answers, replace(filing.answers[0], id="j4"))
        ),
    )
    record_file.rewrite_records(
        tmp_path,
        RecordKind.FINDING_ANSWERS,
        lambda r: None if r["id"] == "j4" else r,
    )

    assert store.judgment_records() == JudgmentRecords()
    assert any("never written" in p for p in store.problems())

    record_file.rewrite_records(
        tmp_path, RecordKind.JUDGMENTS, lambda r: {**r, "weight": 1}
    )
    with pytest.raises(RecordError, match="weight"):
        store.judgment_records()


def test_the_claims_a_line_names_round_trip_written_only_when_set(
    tmp_path: Path,
) -> None:
    """The words of the claims an edge names at its
    target and an about edge at its source, and a self mark's; a record
    naming none reads none."""
    plain = edge_records()
    named = replace(
        plain,
        edges=(
            replace(plain.edges[0], claims=("Then f is bounded.",)),
            replace(plain.edges[1], source_claims=("So f is small.",)),
        ),
        selfs=(replace(plain.selfs[0], claims=("f is tiny",)),),
    )
    store = RecordStore(tmp_path)
    store.append(edges_answer(), edges=named)
    assert RecordStore(tmp_path).edge_records_of("e1") == named
    first, second = record_file.written(tmp_path, RecordKind.EDGES)
    assert first["claims"] == ["Then f is bounded."]
    assert "source_claims" not in first
    assert second["source_claims"] == ["So f is small."]
    (self_mark,) = record_file.written(tmp_path, RecordKind.SELF_MARKS)
    assert self_mark["claims"] == ["f is tiny"]


def test_an_about_line_restating_none_round_trips_written_only_when_set(
    tmp_path: Path,
) -> None:
    """An about line whose source end restates none of its block's claims
    (`→ about [-] T`); a record without the mark reads as naming none."""
    plain = edge_records()
    none = replace(
        plain, edges=(plain.edges[0], replace(plain.edges[1], restates_none=True))
    )
    store = RecordStore(tmp_path)
    store.append(edges_answer(), edges=none)
    assert RecordStore(tmp_path).edge_records_of("e1") == none
    first, second = record_file.written(tmp_path, RecordKind.EDGES)
    assert "restates_none" not in first
    assert second["restates_none"] is True


# --- read on: a file only appended to is read from where it was read -------


def test_an_answer_s_records_are_kept_as_their_change_of_the_latest_of_their_kind(
    tmp_path: Path,
) -> None:
    """A second edges answer holds each kind of its records as their change
    of the first's, its ids by their rule, its answer and its record ids
    named once; read back as given."""
    store = RecordStore(tmp_path)
    first = edges_answer("e1", "e1-1-")
    store.append(first, edges=edge_records("e1", "e1-1-"))
    second = edges_answer("e2", "e2-1-")
    store.append(second, edges=edge_records("e2", "e2-1-"))
    head, entry = record_file.entries(tmp_path)
    held = entry["body"]

    assert "records" not in held["answer"]
    assert held["records"]["edges"] == {
        "from": head["hash"],
        "runs": [],
        "ids": ["e2", 1, 2],
        "answer": True,
    }
    again = RecordStore(tmp_path)
    assert again.answers() == (first, second)
    assert again.edge_records_of("e2") == edge_records("e2", "e2-1-")


def test_an_answer_s_text_is_never_in_the_record(tmp_path: Path) -> None:
    """The answer in hand carries its text while it is applied; the record
    keeps everything but it (docs/architecture.md § 4)."""
    given = answer(raw="the answer's own words")
    RecordStore(tmp_path).append(given)

    assert "the answer's own words" not in (tmp_path / RECORD).read_text("utf-8")
    assert RecordStore(tmp_path).answers() == (replace(given, raw=""),)


def test_an_answer_entry_holding_a_text_is_refused(tmp_path: Path) -> None:
    data = _answer_data(raw="words")
    record_file.append(tmp_path, EntryKind.ANSWER, {"answer": data, "records": {}})

    with pytest.raises(RecordError, match="field raw: unknown"):
        RecordStore(tmp_path).answers()


def _answered(store: RecordStore, n: int) -> None:
    store.append(
        edges_answer(key=f"e{n}", prefix=f"s{n}-"),
        edges=edge_records(f"e{n}", f"s{n}-"),
    )


def test_a_store_held_reads_an_append_on_as_a_new_one_reads_it_whole(
    tmp_path: Path,
) -> None:
    store = RecordStore(tmp_path)
    _answered(store, 1)
    assert [a.key for a in store.answers()] == ["e1"]
    _answered(store, 2)

    assert store.answers() == RecordStore(tmp_path).answers()
    assert [a.key for a in store.answers()] == ["e1", "e2"]
    assert store.edge_records_of("e2") == edge_records("e2", "s2-")


def test_a_file_written_over_in_place_is_read_whole_again(tmp_path: Path) -> None:
    """Every write appends; a file written over, as a person's hand may,
    whatever its length, is read again from its start."""
    store = RecordStore(tmp_path)
    _answered(store, 1)
    _answered(store, 2)
    store.answers()
    record_file.rewrite_answers(
        tmp_path, lambda a: {**a, "seconds": 99.5} if a["key"] == "e1" else a
    )
    assert [a.seconds for a in store.answers()] == [99.5, 12.5]

    record_file.rewrite_answers(tmp_path, lambda a: None if a["key"] == "e1" else a)
    assert [a.key for a in store.answers()] == ["e2"]


def test_a_last_line_cut_short_then_finished_is_read_once(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    _answered(store, 1)
    path = tmp_path / RECORD
    whole = path.read_text()
    grown = tmp_path / "grown"
    grown.mkdir()
    (grown / RECORD).write_text(whole)
    _answered(RecordStore(grown), 3)
    next_entry = (grown / RECORD).read_text()[len(whole) :]
    path.write_text(whole + next_entry[: len(next_entry) // 2])
    assert [a.key for a in store.answers()] == ["e1"]

    path.write_text(whole + next_entry)

    assert [a.key for a in store.answers()] == ["e1", "e3"]
    assert store.answers() == RecordStore(tmp_path).answers()


def test_a_record_cut_short_is_refused_against_the_tip_this_machine_held(
    tmp_path: Path,
) -> None:
    paper = tmp_path / "paper"
    store = RecordStore(paper)
    _answered(store, 1)
    whole = (paper / RECORD).read_text()
    _answered(store, 2)
    assert [a.key for a in store.answers()] == ["e1", "e2"]
    (paper / RECORD).write_text(whole)  # a host serves the older record

    with pytest.raises(RecordError, match="no longer reaches the tip") as refused:
        RecordStore(paper).answers()

    # its person, who set it back on purpose, removes the tip it names
    Path(str(refused.value).rsplit("remove ", 1)[1].split()[0]).unlink()
    reread = RecordStore(paper)
    assert [a.key for a in reread.answers()] == ["e1"]
    assert reread.unpinned()


@pytest.mark.parametrize("cut", ["emptied", "removed"])
def test_a_record_cut_to_nothing_is_refused_against_the_tip_by_its_directory(
    tmp_path: Path, cut: str
) -> None:
    """A record emptied or removed whole names no first entry: its tip is
    found by its directory, and the read is refused, not a new empty
    store's."""
    paper = tmp_path / "paper"
    _answered(RecordStore(paper), 1)
    assert [a.key for a in RecordStore(paper).answers()] == ["e1"]  # read here
    if cut == "emptied":
        (paper / RECORD).write_text("")
    else:
        (paper / RECORD).unlink()

    with pytest.raises(RecordError, match="cut to\\s+nothing") as refused:
        RecordStore(paper).answers()

    Path(str(refused.value).rsplit("remove ", 1)[1].split()[0]).unlink()
    assert RecordStore(paper).answers() == ()


def test_a_new_store_keeps_no_tip_and_reads_as_pinned(tmp_path: Path) -> None:
    """A store that holds no entry yet: nothing to pin, nothing to say."""
    tips = keys.directory() / TIPS
    before = set(tips.glob("*.json")) if tips.is_dir() else set()
    store = RecordStore(tmp_path / "paper")

    assert store.answers() == ()
    assert not store.unpinned()
    assert (set(tips.glob("*.json")) if tips.is_dir() else set()) == before


def test_a_second_copy_on_one_machine_holds_its_own_tip(tmp_path: Path) -> None:
    paper, copy = tmp_path / "paper", tmp_path / "copy"
    _answered(RecordStore(paper), 1)
    copy.mkdir()
    (copy / RECORD).write_text((paper / RECORD).read_text())
    _answered(RecordStore(paper), 2)

    behind = RecordStore(copy)
    assert [a.key for a in behind.answers()] == ["e1"]
    assert behind.unpinned()  # a fresh clone: nothing to tell it by yet
    assert not RecordStore(paper).unpinned()


def test_keys_held_in_memory_alone_keep_no_tip(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    mine = replace(keys.mine(), home=None)
    _answered(RecordStore(paper, mine), 1)
    whole = (paper / RECORD).read_text()
    _answered(RecordStore(paper, mine), 2)
    (paper / RECORD).write_text(whole)

    assert [a.key for a in RecordStore(paper, mine).answers()] == ["e1"]


def _append_bad_edge_entry(directory: Path) -> None:
    """An answer entry after the first, whose first edge has a kind that is
    none."""
    (first,) = record_file.plain(directory)
    text = json.dumps(first["body"])
    for old, new in (('"s1-', '"s9-'), ('"e1"', '"e9"'), ('"uses"', '"leans"')):
        text = text.replace(old, new)
    record_file.append(directory, EntryKind.ANSWER, json.loads(text))


def test_a_bad_edge_line_appended_after_a_check_still_raises(tmp_path: Path) -> None:
    store = RecordStore(tmp_path)
    _answered(store, 1)
    store.answers()  # the record read, what was read kept
    _append_bad_edge_entry(tmp_path)

    with pytest.raises(RecordError, match=rf"{RECORD}:3: field kind: 'leans'"):
        store.edge_records_of("e9")
    with pytest.raises(RecordError, match=rf"{RECORD}:3: field kind: 'leans'"):
        RecordStore(tmp_path).edge_records_of("e9")


def test_a_copy_grown_past_its_store_and_the_store_read_on_alike(
    tmp_path: Path,
) -> None:
    """A copy of a store (a dry run's) appended to after it, and the store
    read again, each give what a store read whole gives."""
    store = RecordStore(tmp_path / "store")
    _answered(store, 1)
    store.answers()
    copy = tmp_path / "copy"
    copy.mkdir()
    for path in (tmp_path / "store").glob("*.jsonl"):
        (copy / path.name).write_bytes(path.read_bytes())
    grown = RecordStore(copy)
    _answered(grown, 2)
    assert [a.key for a in grown.answers()] == ["e1", "e2"]

    again = RecordStore(tmp_path / "store")
    assert [a.key for a in again.answers()] == ["e1"]
    _answered(again, 3)
    assert [a.key for a in RecordStore(tmp_path / "store").answers()] == ["e1", "e3"]
    assert RecordStore(copy).edge_records_of("e2") == edge_records("e2", "s2-")


def test_crlf_lines_are_numbered_as_the_whole_read_numbers_them(
    tmp_path: Path,
) -> None:
    store = RecordStore(tmp_path)
    _answered(store, 1)
    _append_bad_edge_entry(tmp_path)
    path = tmp_path / RECORD
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    with pytest.raises(RecordError, match=rf"{RECORD}:3: field kind: 'leans'"):
        RecordStore(tmp_path).edge_records_of("e9")
