"""What like runs cost: each attempt's estimate recorded, the store's cost
over estimate, and the runner's check on a budget the paper's settings
gave. A fake host; no model is called."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.driver import checker
from axiomation.records import AnswerRecord, RecordStore, TemplateRef
from axiomation.resolver import Resolver
from axiomation.runner import (
    UNMEASURED_FACTOR,
    LikeRuns,
    Runner,
    Stop,
    like,
    like_runs,
)
from axiomation.runner.hosts import Rates
from questions.review_paper import file_questions, make_paper
from runner.fakes import FakeHost, fenced

#: Every attempt is estimated at $0.006, whatever its prompt.
FLAT = Rates(input=0.0, output=1.0)
EMPTY = fenced({"corrections": []})


@pytest.fixture
def paper(tmp_path: Path):
    return make_paper(tmp_path / "paper")


@pytest.fixture
def questions(paper):
    (base,) = file_questions(paper, None)
    base = replace(base, workspace=None, prompt="Correct the cut; answer in JSON.")
    return tuple(
        replace(base, key=f"{n:02d}{base.key[2:]}", prompt=f"{base.prompt}\n{n}")
        for n in range(4)
    )


def answer(template: str, cost: float, estimate: float | None) -> AnswerRecord:
    return AnswerRecord(
        key="k",
        kind="q",
        stage="edges",
        template=TemplateRef(template, "v"),
        attempt=1,
        host="fake",
        model="m",
        time="t",
        seconds=1.0,
        cost=cost,
        estimate=estimate,
    )


def test_each_attempt_records_the_runner_s_estimate(paper, questions, tmp_path):
    store = RecordStore(tmp_path / "store")
    runner = Runner(FakeHost(lambda prompt: EMPTY), Resolver(store), 1.0, FLAT)

    runner.run(questions[:1], checker(paper.document))

    (recorded,) = store.answers()
    assert recorded.estimate == pytest.approx(0.006)
    assert RecordStore(tmp_path / "store").answers()[0].estimate == recorded.estimate


def test_a_session_s_fitted_turns_add_to_its_estimate(paper, questions, tmp_path):
    store = RecordStore(tmp_path / "store")
    runner = Runner(FakeHost(lambda prompt: EMPTY), Resolver(store), 1.0, FLAT)

    runner.run([replace(questions[0], turns_estimate=0.5)], checker(paper.document))

    (recorded,) = store.answers()
    assert recorded.estimate == pytest.approx(0.506)


def test_like_runs_are_a_template_s_cost_over_its_estimate() -> None:
    found = like_runs(
        [
            answer("review-argument+scope", 6.0, 1.0),
            answer("review-argument", 4.0, 1.0),
            answer("edges-update", 3.0, 1.0),
            # an attempt recorded before estimates were: not counted
            answer("edges-update", 50.0, None),
        ]
    )

    assert like("review-argument+scope") == "review-argument"
    assert found.factor("review-argument") == pytest.approx(5.0)
    assert found.counts["review-argument"] == 2
    assert found.factor("edges-update") == pytest.approx(3.0)
    assert found.expected("edges-update", 2.0) == pytest.approx(6.0)


def test_a_session_s_runs_under_its_earlier_name_are_its_own() -> None:
    """`citationcheck` was `citation`: a store's runs under the earlier
    name price the session as its own."""
    found = like_runs(
        [
            answer("review-citation", 2.0, 1.0),
            answer("review-citationcheck", 4.0, 1.0),
        ]
    )

    assert like("review-citation+finder") == "review-citationcheck"
    assert found.counts["review-citationcheck"] == 2
    assert found.factor("review-citationcheck") == pytest.approx(3.0)


def test_a_run_the_store_holds_none_of_takes_the_unmeasured_factor() -> None:
    found = LikeRuns()

    assert found.factor("edges-reread") == UNMEASURED_FACTOR
    # an estimate whose turns a price fitted on recorded sessions completes
    assert found.factor("review-citationcheck", fitted=True) == 1.0


def test_a_default_budget_asks_nothing_of_a_call_expected_above_it(
    paper, questions, tmp_path
):
    """Four attempts estimated at $0.006, ×8 where nothing like them is
    held: $0.192 expected, above a $0.10 budget."""
    host = FakeHost(lambda prompt: EMPTY, cost=0.006)
    store = RecordStore(tmp_path / "store")
    runner = Runner(host, Resolver(store), 0.10, FLAT, expect=LikeRuns())

    report = runner.run(questions, checker(paper.document))

    assert report.stopped is Stop.BUDGET and host.calls == 0
    assert "expected to cost $0.19" in report.detail
    assert "--dry-run" in report.detail and "--budget" in report.detail
    assert store.answers() == ()


def test_a_default_budget_asks_a_call_expected_within_it(paper, questions, tmp_path):
    """What like runs cost in the store moves the expectation: at ×2, the
    four attempts are expected at $0.048."""
    template = questions[0].template.name
    host = FakeHost(lambda prompt: EMPTY, cost=0.006)
    store = RecordStore(tmp_path / "store")
    expect = like_runs([answer(template, 2.0, 1.0)])
    runner = Runner(host, Resolver(store), 0.10, FLAT, expect=expect)

    report = runner.run(questions, checker(paper.document))

    assert report.stopped is None and host.calls == 4


def test_a_given_budget_takes_no_expectation(paper, questions, tmp_path):
    host = FakeHost(lambda prompt: EMPTY, cost=0.006)
    runner = Runner(host, Resolver(RecordStore(tmp_path / "store")), 0.10, FLAT)

    report = runner.run(questions, checker(paper.document))

    assert report.stopped is None and host.calls == 4
