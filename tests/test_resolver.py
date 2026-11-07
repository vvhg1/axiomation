"""The resolver: attempts to records."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

import record_file
from axiomation.questions import Stage, Validation
from axiomation.questions.cut_review import validate
from axiomation.records import (
    CorrectionClass,
    CutCorrection,
    CutOp,
    Delta,
    EdgeRecords,
    ImpactClass,
    RecordStore,
)
from axiomation.resolver import Attempt, Resolver, StoreBusy
from questions.review_paper import file_questions, make_paper
from test_records import edge_records, flag


@pytest.fixture
def question(tmp_path: Path):
    paper = make_paper(tmp_path / "paper")
    q = file_questions(paper)[1]
    return paper, q


def attempt(question, validation, text="{}", cost=0.01) -> Attempt:
    return Attempt(
        question=question,
        host="fake",
        model="fake/model",
        text=text,
        cost=cost,
        seconds=1.5,
        validation=validation,
        tokens={"input": 100, "output": 20},
    )


def split_answer(at):
    return {
        "corrections": [
            {
                "op": "split",
                "block": "b3",
                "at": at,
                "class": "argument-boundary",
                "why": "A new step.",
            }
        ]
    }


def test_a_valid_attempt_writes_its_answer_and_its_corrections(tmp_path, question):
    paper, q = question
    store = RecordStore(tmp_path / "store")
    validation = validate(
        q, split_answer("Hence the output is bounded."), paper.document
    )

    recorded = Resolver(store, clock=lambda: "2026-09-29T12:00:00+00:00").record(
        attempt(q, validation)
    )

    (correction,) = store.corrections()
    assert correction.id == f"{q.key[:12]}-1-1"
    assert correction.answer == q.key
    assert correction.block == "main.tex:15:1"
    assert correction.at == ("Hence the output is bounded.",)  # normalised
    (answer,) = store.answers()
    # the record keeps all but the text the answer in hand carries
    assert answer == replace(recorded.answer, raw="")
    assert (answer.attempt, answer.valid, answer.records) == (1, True, (correction.id,))
    assert (answer.host, answer.model, answer.cost, answer.seconds) == (
        "fake",
        "fake/model",
        0.01,
        1.5,
    )
    assert answer.template == q.template.ref
    assert answer.stage == "boundaries" and answer.kind == "cut-review"
    assert answer.judges is None
    assert answer.time == "2026-09-29T12:00:00+00:00"
    assert answer.tokens == {"input": 100, "output": 20}


def test_the_resolver_writes_the_author_a_judge_s_answer_judged(tmp_path, question):
    """A judge's answer records its stage and the author question
    it reviewed."""
    _, q = question
    judge = replace(q, stage=Stage.JUDGE, judges="author-key")
    store = RecordStore(tmp_path / "store")

    Resolver(store).record(attempt(judge, Validation(problems=("no JSON",))))

    (answer,) = store.answers()
    assert answer.stage == "judge"
    assert answer.judges == "author-key"


@pytest.mark.usefixtures("develop_mode")
def test_an_invalid_attempt_writes_an_answer_with_its_problems_only(tmp_path, question):
    _, q = question
    store = RecordStore(tmp_path / "store")

    Resolver(store).record(attempt(q, Validation(problems=("no JSON",)), text="hm"))

    (answer,) = store.answers()
    text = record_file.text(store.directory, answer)
    assert (answer.valid, answer.problems, answer.records, text) == (
        False,
        ("no JSON",),
        (),
        "hm",
    )
    assert store.corrections() == ()
    assert store.authoritative() == {}


def test_attempts_are_numbered_by_the_store_and_ids_stay_unique(tmp_path, question):
    """A failed attempt, a valid one, and a second valid one (which a
    resumed run could write): three numbers, ids that never repeat, and
    the first valid answer authoritative."""
    paper, q = question
    store = RecordStore(tmp_path / "store")
    resolver = Resolver(store)
    valid = validate(q, split_answer(["Hence the output is bounded."]), paper.document)

    resolver.record(attempt(q, Validation(problems=("bad",))))
    first = resolver.record(attempt(q, valid))
    second = Resolver(RecordStore(tmp_path / "store")).record(attempt(q, valid))

    assert [a.attempt for a in store.answers()] == [1, 2, 3]
    assert [c.id for c in store.corrections()] == [
        f"{q.key[:12]}-2-1",
        f"{q.key[:12]}-3-1",
    ]
    assert store.authoritative()[q.key] == replace(first.answer, raw="")
    assert store.corrections_of(q.key) == first.corrections
    assert second.answer.attempt == 3


def test_threads_never_take_the_same_attempt_number(tmp_path, question):
    paper, q = question
    store = RecordStore(tmp_path / "store")
    resolver = Resolver(store)
    valid = validate(q, split_answer("Hence the output is bounded."), paper.document)

    threads = [
        threading.Thread(target=resolver.record, args=(attempt(q, valid),))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(a.attempt for a in store.answers()) == list(range(1, 9))
    assert len({c.id for c in store.corrections()}) == 8


def test_a_second_claim_on_a_store_is_refused(tmp_path):
    store = RecordStore(tmp_path / "store")

    other = Resolver(RecordStore(tmp_path / "store"))
    with (
        Resolver(store).claim(),
        pytest.raises(StoreBusy, match="another run holds the store"),
        other.claim(),
    ):
        pass
    with Resolver(store).claim():
        pass


def test_corrections_named_by_the_question_are_not_changed(tmp_path, question):
    """The resolver sets only a correction's id and answer."""
    paper, q = question
    validation = validate(
        q, split_answer("Hence the output is bounded."), paper.document
    )

    recorded = Resolver(RecordStore(tmp_path / "store")).record(attempt(q, validation))

    assert recorded.corrections[0] == replace(
        validation.corrections[0], id=f"{q.key[:12]}-1-1", answer=q.key
    )


def test_an_edges_attempt_numbers_its_records_across_kinds(tmp_path, question):
    """The edge stage's records are numbered after any
    corrections, in the order entries, edges, self marks, standings, frame."""
    _, q = question
    edges_q = replace(q, kind="edges", stage=Stage.EDGES)
    store = RecordStore(tmp_path / "store")
    blank = edge_records(key=None, prefix="")

    recorded = Resolver(store).record(attempt(edges_q, Validation(edges=blank)))

    prefix = f"{q.key[:12]}-1-"
    assert recorded.edges.ids == tuple(f"{prefix}{n}" for n in range(1, 7))
    assert recorded.answer.records == recorded.edges.ids
    assert all(r.answer == q.key for r in recorded.edges.all())
    assert store.edge_records_of(q.key) == recorded.edges
    assert store.corrections() == ()


def test_an_update_s_deltas_are_numbered_after_the_frame(tmp_path, question):
    """An update's Δ lines are numbered after the edge stage's kinds and
    before the flags."""
    _, q = question
    edges_q = replace(q, kind="edges", stage=Stage.EDGES)
    store = RecordStore(tmp_path / "store")
    delta = Delta("", None, "main.tex:7:1", "gain", ImpactClass.WIDENED, "more")
    found = Validation(
        edges=replace(edge_records(key=None, prefix=""), deltas=(delta,)),
        flags=(flag("", None),),
    )

    recorded = Resolver(store).record(attempt(edges_q, found))

    prefix = f"{q.key[:12]}-1-"
    assert recorded.edges.deltas[0].id == f"{prefix}7"
    assert recorded.flags[0].id == f"{prefix}8"
    assert store.edge_records_of(q.key).deltas == recorded.edges.deltas


def test_an_invalid_edges_attempt_writes_no_record(tmp_path, question):
    _, q = question
    store = RecordStore(tmp_path / "store")
    found = Validation(problems=("not done: b1",), edges=edge_records(key=None))

    recorded = Resolver(store).record(attempt(q, found))

    assert recorded.answer.records == () and not recorded.edges
    assert store.edge_records_of(q.key) == EdgeRecords()


def test_an_attempt_numbers_its_flags_after_its_other_records(tmp_path, question):
    """The flags are numbered last, after every other record."""
    _, q = question
    edges_q = replace(q, kind="edges", stage=Stage.EDGES)
    store = RecordStore(tmp_path / "store")
    blank = edge_records(key=None, prefix="")
    raised = flag(fid="", key=None)

    recorded = Resolver(store).record(
        attempt(edges_q, Validation(edges=blank, flags=(raised, raised)))
    )

    prefix = f"{q.key[:12]}-1-"
    assert [f.id for f in recorded.flags] == [f"{prefix}7", f"{prefix}8"]
    assert recorded.answer.records == (*recorded.edges.ids, f"{prefix}7", f"{prefix}8")
    assert all(f.answer == q.key for f in recorded.flags)
    assert store.flags_of(q.key) == recorded.flags


def test_an_invalid_attempt_writes_no_flag(tmp_path, question):
    _, q = question
    store = RecordStore(tmp_path / "store")
    found = Validation(problems=("not done: b1",), flags=(flag(fid="", key=None),))

    recorded = Resolver(store).record(attempt(q, found))

    assert recorded.answer.records == () and recorded.flags == ()
    assert store.flags() == ()


def test_a_decision_s_tag_becomes_its_first_correction_s_id(tmp_path, question):
    """An answer tags a decision's corrections; the store names
    it by its first correction."""
    _, q = question
    store = RecordStore(tmp_path / "store")

    def made(op, tag, **fields):
        return CutCorrection("", op, CorrectionClass.OTHER, "w", decision=tag, **fields)

    found = Validation(
        (
            made(CutOp.UNGROUP, "d1", group="g1"),
            made(CutOp.MERGE, "d1", blocks=("a", "b")),
            made(CutOp.MERGE, None, blocks=("c", "d")),
        )
    )

    recorded = Resolver(store).record(attempt(q, found))

    first, second, third = recorded.corrections
    assert first.decision == second.decision == first.id
    assert third.decision is None
