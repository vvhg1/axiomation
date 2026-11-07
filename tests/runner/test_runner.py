"""The runner, through a fake host. No model is called."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import record_file
from axiomation.driver import checker
from axiomation.questions import Stage
from axiomation.records import RecordStore
from axiomation.resolver import Resolver
from axiomation.runner import Runner, Seat, Stop
from axiomation.runner.hosts import HostAnswer, HostOverBudget, Rates
from questions.review_paper import file_questions, make_paper
from runner.fakes import FakeHost, breach, fenced, out_of_credit, uncounted

#: Every attempt is estimated at $0.006, whatever its prompt.
FLAT = Rates(input=0.0, output=1.0)
EMPTY = fenced({"corrections": []})


@pytest.fixture
def paper(tmp_path: Path):
    return make_paper(tmp_path / "paper")


@pytest.fixture
def questions(paper):
    """Ten questions answered in JSON from their prompt alone: a boundary
    question with no session, keyed and worded ten ways."""
    (base,) = file_questions(paper, None)
    base = replace(base, workspace=None, prompt="Correct the cut; answer in JSON.")
    return tuple(
        replace(base, key=f"{n:02d}{base.key[2:]}", prompt=f"{base.prompt}\n{n}")
        for n in range(10)
    )


def run(
    paper, questions, host, directory: Path, budget=10.0, jobs=1, rates=FLAT, **options
):
    store = RecordStore(directory)
    runner = Runner(host, Resolver(store), budget, rates, jobs=jobs, **options)
    return runner.run(questions, checker(paper.document)), store


def test_all_valid(paper, questions, tmp_path) -> None:
    host = FakeHost(lambda prompt: EMPTY)

    report, store = run(paper, questions, host, tmp_path / "store")

    keys = tuple(q.key for q in questions)
    assert (report.asked, report.answered, report.failed) == (keys, keys, ())
    assert report.stopped is None and report.attempts == 10
    assert report.spent == pytest.approx(0.10)
    assert host.calls == 10
    assert set(store.authoritative()) == set(keys)


def test_invalid_then_valid(paper, questions, tmp_path) -> None:
    host = FakeHost(
        lambda prompt: EMPTY if "## Your previous answer" in prompt else "no JSON here"
    )

    report, store = run(paper, questions, host, tmp_path / "store")

    assert len(report.answered) == 10 and host.calls == 20
    first = questions[0].key
    answers = [a for a in store.answers() if a.key == first]
    assert [(a.attempt, a.valid) for a in answers] == [(1, False), (2, True)]
    assert answers[0].problems == ("the answer holds no JSON object",)
    assert store.authoritative()[first] == answers[1]
    reasked = [p for p in host.prompts if "## Your previous answer" in p]
    assert all("- the answer holds no JSON object" in p for p in reasked)


@pytest.mark.usefixtures("develop_mode")
def test_an_answer_the_check_lays_into_another_form_is_kept_in_that_form(
    paper, questions, tmp_path
) -> None:
    """A refused answer is kept as written, and its re-ask shows it so; a
    valid one as the check gave it (`Validation.kept`)."""
    from axiomation.questions import Validation

    def check(question, text):
        if text == "first":
            return Validation(problems=("not yet",))
        return Validation(kept=f"laid: {text}")

    host = FakeHost(
        lambda prompt: "second" if "## Your previous answer" in prompt else "first"
    )
    store = RecordStore(tmp_path / "store")
    Runner(host, Resolver(store), 10.0, FLAT).run(questions[:1], check)

    answers = [a for a in store.answers() if a.key == questions[0].key]
    assert [(a.valid, record_file.text(store.directory, a)) for a in answers] == [
        (False, "first"),
        (True, "laid: second"),
    ]
    assert "first" in host.prompts[1]


def test_three_invalid_answers_fail_the_question(paper, questions, tmp_path) -> None:
    host = FakeHost(lambda prompt: "no")

    report, store = run(paper, questions[:2], host, tmp_path / "store")

    assert report.failed == (questions[0].key, questions[1].key)
    assert report.answered == () and report.stopped is None
    assert host.calls == 6
    assert [a.attempt for a in store.answers() if a.key == questions[0].key] == [
        1,
        2,
        3,
    ]
    assert store.authoritative() == {}


def test_a_tool_use_fails_the_attempt(paper, questions, tmp_path) -> None:
    host = FakeHost(
        lambda prompt: EMPTY, tool_uses=({"tool": "read", "input": {"path": "a"}},)
    )

    report, store = run(paper, questions[:1], host, tmp_path / "store")

    assert report.failed == (questions[0].key,)
    assert {a.problems for a in store.answers()} == {("the host used a tool: read",)}


def test_a_breach_stops_the_run_at_once(paper, questions, tmp_path) -> None:
    host = FakeHost(breach)

    report, store = run(paper, questions, host, tmp_path / "store")

    assert report.stopped is Stop.BREACH
    assert "outside its directory" in report.detail
    assert host.calls == 1 and report.attempts == 1
    (answer,) = store.answers()
    assert not answer.valid and answer.problems[0].startswith("the host was breached")
    assert report.spent == pytest.approx(0.02)


def test_each_attempt_may_spend_what_is_left_of_the_budget(
    paper, questions, tmp_path
) -> None:
    """Each attempt's limit is the budget less what was spent and what
    the attempts in flight reserved: one at a time, $1 less $0.05 an
    answer."""
    host = FakeHost(lambda prompt: EMPTY, cost=0.05)

    run(paper, questions[:3], host, tmp_path / "store", budget=1.0)

    assert host.spend_maxes == pytest.approx([1.0, 0.95, 0.90])


@pytest.mark.usefixtures("develop_mode")
def test_an_attempt_ended_at_its_limit_is_recorded_and_stops_the_run(
    paper, questions, tmp_path
) -> None:
    """A session that reaches what was left of the budget is not asked
    again as a failed answer: the run stops, its partial answer and its
    cost recorded; a resumed run asks the question again."""

    def spent_out(prompt: str) -> str:
        raise HostOverBudget(
            "the session reached its limit of $1.00",
            HostAnswer(text="half an answer", model="fake/model", cost=1.02),
        )

    host = FakeHost(spent_out)

    report, store = run(paper, questions, host, tmp_path / "store", budget=1.0)

    assert report.stopped is Stop.BUDGET and "reached its limit" in report.detail
    assert host.calls == 1 and report.spent == pytest.approx(1.02)
    (answer,) = store.answers()
    assert not answer.valid
    assert record_file.text(store.directory, answer) == "half an answer"
    assert answer.problems[0].startswith("stopped at the budget")
    assert store.authoritative() == {}


def test_the_budget_stops_the_run_and_a_resumed_run_asks_only_the_rest(
    paper, questions, tmp_path
) -> None:
    """$0.02 buys three attempts at $0.006 estimated and spent; the fourth
    could pass it, so nothing starts after the third."""
    host = FakeHost(lambda prompt: EMPTY, cost=0.006)

    stopped, store = run(paper, questions, host, tmp_path / "store", budget=0.02)

    assert stopped.stopped is Stop.BUDGET
    assert host.calls == 3 and stopped.attempts == 3
    assert stopped.answered == tuple(q.key for q in questions[:3])
    assert stopped.spent == pytest.approx(0.018)

    again = FakeHost(lambda prompt: EMPTY, cost=0.006)
    resumed, _ = run(paper, questions, again, tmp_path / "store")

    assert resumed.skipped == stopped.answered
    assert resumed.answered == tuple(q.key for q in questions[3:])
    assert again.calls == 7
    asked = {p for p in again.prompts}
    assert not asked & {q.prompt for q in questions[:3]}
    assert len(store.answers()) == 10


def test_a_billing_refusal_is_a_clean_stop(paper, questions, tmp_path) -> None:
    """The third call is refused: nothing is recorded for it, it is not
    re-asked, and the run resumes from its records."""
    calls = []

    def answer(prompt: str) -> str:
        calls.append(prompt)
        return out_of_credit(prompt) if len(calls) == 3 else EMPTY

    host = FakeHost(answer)
    transcripts = tmp_path / "store" / "transcripts"

    report, store = run(
        paper, questions, host, tmp_path / "store", transcripts=transcripts
    )

    assert report.stopped is Stop.CREDIT
    assert "Insufficient balance" in report.detail
    assert host.calls == 3
    assert report.answered == tuple(q.key for q in questions[:2])
    assert report.failed == ()
    assert len(store.answers()) == 2 and all(a.valid for a in store.answers())
    assert (transcripts / f"{questions[2].key[:16]}-refused.jsonl").exists()

    resumed, _ = run(paper, questions, FakeHost(lambda p: EMPTY), tmp_path / "store")
    assert resumed.answered == tuple(q.key for q in questions[2:])


def test_an_uncounted_attempt_is_a_clean_stop(paper, questions, tmp_path) -> None:
    """The host cannot say what the third call cost: the run stops, the
    answer is not recorded, its cost stays out of `spent`, and the run
    resumes from its records."""
    calls = []

    def answer(prompt: str) -> str:
        calls.append(prompt)
        return uncounted(prompt) if len(calls) == 3 else EMPTY

    host = FakeHost(answer)
    transcripts = tmp_path / "store" / "transcripts"

    report, store = run(
        paper, questions, host, tmp_path / "store", transcripts=transcripts
    )

    assert report.stopped is Stop.UNCOUNTED
    assert "cost is unknown" in report.detail
    assert host.calls == 3
    assert report.answered == tuple(q.key for q in questions[:2])
    assert report.failed == ()
    assert report.spent == pytest.approx(2 * host.cost)
    assert len(store.answers()) == 2
    assert (transcripts / f"{questions[2].key[:16]}-uncounted.jsonl").exists()

    resumed, _ = run(paper, questions, FakeHost(lambda p: EMPTY), tmp_path / "store")
    assert resumed.answered == tuple(q.key for q in questions[2:])


def test_a_host_failing_again_and_again_stops_the_run(paper, questions, tmp_path):
    host = FakeHost(
        lambda prompt: HostAnswer(
            text="", model="fake/model", exit_code=1, error="boom"
        )
    )

    report, store = run(paper, questions, host, tmp_path / "store")

    assert report.stopped is Stop.HOST
    assert "failed 3 attempts in a row" in report.detail and "boom" in report.detail
    assert host.calls == 3
    assert [a.problems for a in store.answers()] == [("the host failed: boom",)] * 3
    # A host failure is retried as it was asked: nothing for the model to fix.
    assert host.prompts == [questions[0].prompt] * 3


def test_four_jobs_and_one_give_the_same_records(paper, questions, tmp_path) -> None:
    def answer(prompt: str) -> str:
        if "## Your previous answer" in prompt or "Setting" not in prompt:
            return EMPTY
        return "not yet"

    one, store_one = run(paper, questions, FakeHost(answer), tmp_path / "one")
    four, store_four = run(
        paper, questions, FakeHost(answer), tmp_path / "four", jobs=4
    )

    def shape(store):
        return sorted(
            (a.key, a.attempt, a.valid, a.problems, a.records) for a in store.answers()
        )

    assert shape(store_one) == shape(store_four)
    assert sorted(store_one.corrections(), key=lambda c: c.id) == sorted(
        store_four.corrections(), key=lambda c: c.id
    )
    assert one.answered == four.answered


def test_each_attempt_s_transcript_is_kept(paper, questions, tmp_path) -> None:
    transcripts = tmp_path / "store" / "transcripts"
    host = FakeHost(lambda prompt: EMPTY if "previous answer" in prompt else "no")

    run(paper, questions[:1], host, tmp_path / "store", transcripts=transcripts)

    key = questions[0].key[:16]
    assert sorted(p.name for p in transcripts.iterdir()) == [
        f"{key}-1.jsonl",
        f"{key}-2.jsonl",
    ]


def test_a_question_asked_twice_in_one_run_is_asked_once(paper, questions, tmp_path):
    host = FakeHost(lambda prompt: EMPTY)

    report, _ = run(paper, [questions[0], questions[0]], host, tmp_path / "store")

    assert host.calls == 1 and report.answered == (questions[0].key,)


def test_an_error_in_one_question_starts_nothing_more(paper, questions, tmp_path):
    """A host that raises what the runner does not expect: the run raises,
    after the attempts in flight, and asks nothing new meanwhile."""

    def broken(prompt: str) -> str:
        raise RuntimeError("the fake host broke")

    host = FakeHost(broken)

    with pytest.raises(RuntimeError, match="the fake host broke"):
        run(paper, questions, host, tmp_path / "store", jobs=1)
    assert host.calls == 1


def test_the_budget_holds_across_a_runner_s_calls(paper, questions, tmp_path):
    """Two calls on one runner share its budget."""
    host = FakeHost(lambda prompt: EMPTY, cost=0.006)
    store = RecordStore(tmp_path / "store")
    runner = Runner(host, Resolver(store), 0.02, FLAT, jobs=1)
    check = checker(paper.document)

    first = runner.run(questions[:2], check)
    second = runner.run(questions[2:], check)

    assert first.stopped is None and first.spent == pytest.approx(0.012)
    assert second.stopped is Stop.BUDGET and host.calls == 3
    assert second.spent == pytest.approx(0.006)
    assert runner.spent == pytest.approx(0.018)


def test_an_empty_answer_without_an_error_trips_the_breaker(paper, questions, tmp_path):
    """A refusal the adapter does not recognise, exit 0 and no output: no
    re-ask of the model, and three in a row stop the run."""
    host = FakeHost(
        lambda prompt: HostAnswer(text="", model="fake/model", tokens={"output": 0})
    )

    report, store = run(paper, questions, host, tmp_path / "store")

    assert report.stopped is Stop.HOST and host.calls == 3
    assert [a.problems for a in store.answers()] == [("the host gave no answer",)] * 3
    assert host.prompts == [questions[0].prompt] * 3


def test_a_reask_the_host_cannot_take_fails_the_question(paper, questions, tmp_path):
    from axiomation.runner.hosts import PromptTooLong

    def answer(prompt: str) -> str:
        if "## Your previous answer" in prompt:
            raise PromptTooLong("the prompt is 600,000 bytes, over the limit")
        return "no"

    host = FakeHost(answer)

    report, store = run(paper, questions[:1], host, tmp_path / "store")

    assert report.failed == (questions[0].key,) and report.stopped is None
    assert report.attempts == 1 and len(store.answers()) == 1
    assert "over the limit" in report.detail


def test_a_breach_aborts_the_host(paper, questions, tmp_path) -> None:
    host = FakeHost(breach)

    run(paper, questions, host, tmp_path / "store", jobs=4)

    assert host.aborted >= 1


def test_a_breach_aborts_the_judge_s_host_too(paper, questions, tmp_path) -> None:
    judge = FakeHost(lambda prompt: EMPTY)
    host = FakeHost(breach)

    seats = {Stage.JUDGE: Seat(judge, FLAT)}
    run(paper, questions, host, tmp_path / "store", seats=seats)

    assert host.aborted >= 1 and judge.aborted >= 1
    assert judge.calls == 0  # no question of the judge's stage


def test_the_judge_s_questions_are_estimated_at_its_rates(paper, questions, tmp_path):
    """The author's rates would pass the budget; the judge's cheaper ones
    do not."""
    judging = tuple(replace(q, stage=Stage.JUDGE) for q in questions[:2])
    judge = FakeHost(lambda prompt: EMPTY, cost=0.0)
    dear = Rates(input=0.0, output=1000.0)  # $6 an attempt

    report, _ = run(
        paper, judging, FakeHost(lambda p: EMPTY), tmp_path / "s", budget=1.0,
        seats={Stage.JUDGE: Seat(judge, FLAT)}, rates=dear,
    )  # fmt: skip

    assert report.stopped is None and judge.calls == 2


def test_every_refusal_s_transcript_is_kept(paper, questions, tmp_path) -> None:
    transcripts = tmp_path / "store" / "transcripts"
    for _ in range(2):
        run(
            paper,
            questions[:1],
            FakeHost(out_of_credit),
            tmp_path / "store",
            transcripts=transcripts,
        )

    key = questions[0].key[:16]
    assert sorted(p.name for p in transcripts.iterdir()) == [
        f"{key}-refused-2.jsonl",
        f"{key}-refused.jsonl",
    ]


# --- sessions on files ----------------------------------------------------


def test_a_session_gets_its_workspace_and_its_tools_are_the_host_s(paper, tmp_path):
    sessions = file_questions(paper)
    host = FakeHost(
        lambda prompt: "",
        edit=lambda text: text,
        tool_uses=({"tool": "Edit", "input": {"file_path": "cut.md"}},),
    )

    report, store = run(paper, sessions, host, tmp_path / "store")

    assert len(report.answered) == 3 and host.calls == 3
    assert [w.edit for w in host.workspaces] == ["cut.md"] * 3
    assert all(a.valid for a in store.answers())
    assert list(store.corrections()) == []


def test_a_session_whose_edit_cannot_be_used_starts_again(paper, tmp_path):
    session = file_questions(paper)[1]
    edits = iter(["the text rewritten", session.workspace.files["cut.md"]])
    host = FakeHost(lambda prompt: "", edit=lambda text: next(edits))

    report, store = run(paper, (session,), host, tmp_path / "store")

    assert report.answered == (session.key,) and host.calls == 2
    assert host.prompts[0] == session.prompt
    assert host.prompts[1].startswith(session.prompt)
    assert "An earlier session's edit of `cut.md` could not be used" in host.prompts[1]
    assert "the paper's text changed" in host.prompts[1]
    first, second = store.answers()
    assert not first.valid and second.valid


def test_a_session_s_estimate_counts_its_files(paper, tmp_path) -> None:
    from axiomation.runner.hosts import estimate

    session = file_questions(paper)[1]
    files = sum(len(text) for text in session.workspace.files.values())
    shown = len(session.prompt) + len(session.workspace.instructions) + files

    assert estimate(session.prompt, Rates(3.0, 0.0), session.workspace) == (
        pytest.approx(shown / 3 * 3.0 / 1_000_000)
    )


def test_a_file_searched_in_place_is_left_out_of_the_estimate(paper) -> None:
    """A cited work laid in to grep is not read whole:
    the estimate counts the session's other files alone."""
    from axiomation.runner.hosts import estimate

    session = file_questions(paper)[1]
    book = {**session.workspace.files, "sources/book.md": "x" * 3_000_000}
    rates = Rates(3.0, 0.0)
    searched = replace(
        session.workspace, files=book, searched=frozenset({"sources/book.md"})
    )
    read = replace(session.workspace, files=book)
    assert estimate(session.prompt, rates, searched) == estimate(
        session.prompt, rates, session.workspace
    )
    assert (
        estimate(session.prompt, rates, read)
        > estimate(session.prompt, rates, searched) + 2.9
    )


def test_the_harness_s_own_prompt_is_priced_once_at_the_cache_write_rate() -> None:
    """Claude Code's own prompt is most of a short session's cost: some
    25,000 tokens written at the cache-write rate against a few hundred
    out."""
    from axiomation.runner.claude_code import PROMPT_TOKENS, ClaudeCodeHost
    from axiomation.runner.hosts import OUTPUT_TOKENS, estimate

    opus = Rates(4.0, 20.0, 0.20, 8.0)
    plain = Rates(4.0, 20.0)
    bare = estimate("", opus)

    assert estimate("", opus, None, PROMPT_TOKENS) - bare == pytest.approx(
        PROMPT_TOKENS * 8.0 / 1_000_000
    )
    assert estimate("", plain, None, 1_000) - estimate("", plain) == pytest.approx(
        1_000 * 4.0 / 1_000_000
    )
    assert bare == pytest.approx(OUTPUT_TOKENS * 20.0 / 1_000_000)
    assert ClaudeCodeHost.prompt_tokens == PROMPT_TOKENS
