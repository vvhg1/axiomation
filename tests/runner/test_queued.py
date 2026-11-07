"""A queue run (docs/architecture.md § 2): the steps' sessions at once on
one spool, what they settled filed through the resolver in its order,
one record a session with its cost, the budget kept across them, and a
stopped run resumed from its records. Against `FakeQueueHost`, on
`fake_workflow`'s three tasks; no model is called.
"""

from __future__ import annotations

import pytest

from axiomation import queue
from axiomation.queue import Run, Spool, Working, advance, opened, recorded
from axiomation.records import RecordError, RecordStore
from axiomation.resolver import Resolver
from axiomation.runner import Runner, Stop, queued
from axiomation.runner.hosts import HostOverBudget
from axiomation.runner.queued import SESSION_KIND, run_queue
from queues.fake_workflow import FACTORY, saying
from runner.fakes import FakeQueueHost, working_through
from test_marks import kept_update
from test_update import FLAT


@pytest.fixture
def paper(tmp_path):
    v, state = kept_update(tmp_path)
    return v.store, state


def runner(store, budget: float = 1.0) -> Runner:
    return Runner(FakeQueueHost(working_through(saying)), Resolver(store), budget, FLAT)


def tasks(store) -> list:
    return [a for a in store.answers() if a.kind in ("fake-say", "fake-confirm")]


def sessions(store) -> list:
    """The fake workflow's sessions: the update that made the paper's
    state ran its own queue."""
    return [
        a
        for a in store.answers()
        if a.kind == SESSION_KIND and a.template.name.startswith("fake-")
    ]


def test_a_run_files_an_answer_a_task_in_its_order_and_a_record_a_session(
    paper,
) -> None:
    store, state = paper
    host = FakeQueueHost(working_through(saying), cost=0.03)
    ran = runner(store)
    report = run_queue(ran, Run(store, state, FACTORY, {}), [host], wait_seconds=0.1)
    assert report.complete and report.stopped is None
    assert report.settled == 3 and report.spent == pytest.approx(0.03)
    answers = tasks(store)
    assert [a.call["args"]["task"] for a in answers] == ["t1", "t2", "t3"]
    assert [a.order for a in answers] == [1, 2, 3]
    assert {a.cost for a in answers} == {0.0}
    assert {(a.host, a.model, a.state) for a in answers} == {
        ("fake-queue", "fake/queue", state.id)
    }
    (session,) = sessions(store)
    assert session.cost == pytest.approx(0.03) and session.valid
    assert session.tasks == tuple(a.key for a in answers)
    assert {a.session for a in answers} == {session.session}
    assert report.sessions == (session.key,)
    assert host.spend_maxes == [pytest.approx(1.0)]
    run = Run(store, state, FACTORY, {})
    assert not list(run.directory.glob("*"))


def test_a_run_stopped_at_its_budget_keeps_what_it_settled_and_resumes(
    paper,
) -> None:
    """The session ends at its limit after two answers: both are filed, the
    run stops at the budget; a resumed run serves only the third."""
    store, state = paper
    run = Run(store, state, FACTORY, {})
    over = FakeQueueHost(
        working_through(saying, stop_after=2), cost=0.5, ends=HostOverBudget
    )
    report = run_queue(runner(store), run, [over], wait_seconds=0.1)
    assert report.stopped is Stop.BUDGET
    assert (report.settled, report.open) == (2, 1)
    (session,) = sessions(store)
    assert not session.valid and session.problems[0].startswith("budget: ")
    served: list[str] = []

    def answer(text: str):
        served.append(text.split()[1])
        return saying(text)

    report = run_queue(
        runner(store), run, [FakeQueueHost(working_through(answer))], wait_seconds=0.1
    )
    assert report.complete and report.settled == 1
    assert served == ["t3"]
    assert [a.order for a in tasks(store)] == [1, 2, 3]


def test_without_hosts_a_run_replays_what_the_store_holds(paper) -> None:
    store, state = paper
    run = Run(store, state, FACTORY, {"argue": True})
    run_queue(
        runner(store), run, [FakeQueueHost(working_through(saying))], wait_seconds=0.1
    )
    report = run_queue(runner(store), run)
    assert report.complete and report.settled == 0 and report.spent == 0
    with opened(run) as workflow:
        working = Working(workflow)
        position, used = advance(workflow, working, recorded(store, workflow))
    assert position.task is None
    assert [u.task.id for u in used] == ["t1", "t2"]
    assert "→ justifies next-the-bound-is" in working.sheet


def test_a_run_with_nothing_asked_reports_what_is_open(paper) -> None:
    store, state = paper
    report = run_queue(runner(store), Run(store, state, FACTORY, {}))
    assert (report.settled, report.open, report.stopped) == (0, 3, None)
    assert not report.complete


def test_two_steps_run_at_once_and_settle_in_lockstep(paper) -> None:
    """The worker's and the settling step's sessions on one spool: each
    task's two answers filed in turn, the budget split by the shares."""
    store, state = paper
    worker = FakeQueueHost(working_through(saying), cost=0.01)
    settler = FakeQueueHost(working_through(saying), cost=0.05)
    report = run_queue(
        runner(store, budget=2.0),
        Run(store, state, FACTORY, {"steps": 2}),
        [worker, settler],
        shares=[1, 3],
        wait_seconds=0.2,
    )
    assert report.complete and report.settled == 3
    assert report.spent == pytest.approx(0.06)
    assert [(a.kind, a.call["args"]["task"]) for a in tasks(store)] == [
        ("fake-say", "t1"),
        ("fake-confirm", "t1"),
        ("fake-say", "t2"),
        ("fake-confirm", "t2"),
        ("fake-say", "t3"),
        ("fake-confirm", "t3"),
    ]
    assert worker.spend_maxes == [pytest.approx(0.5)]
    assert settler.spend_maxes == [pytest.approx(1.5)]
    found = sessions(store)
    assert sorted(a.cost for a in found) == [
        pytest.approx(0.01),
        pytest.approx(0.05),
    ]
    for session in found:
        gave = [a for a in tasks(store) if a.session == session.session]
        assert session.tasks == tuple(a.key for a in gave) and len(gave) == 3


def test_a_session_that_ends_ends_the_other_and_a_fresh_pair_goes_on(paper) -> None:
    """The settling step's first session ends after one answer: the
    worker's is told the run has ended, its unsettled answer is dropped,
    and a fresh pair settles the rest."""
    store, state = paper
    worker = FakeQueueHost(working_through(saying))
    settlers = iter([working_through(saying, stop_after=1), working_through(saying)])
    settler = FakeQueueHost(lambda call: next(settlers)(call))
    report = run_queue(
        runner(store, budget=2.0),
        Run(store, state, FACTORY, {"steps": 2}),
        [worker, settler],
        wait_seconds=0.2,
    )
    assert report.complete and report.settled == 3
    assert len(worker.sessions) == len(settler.sessions) == 2
    asked = [(a.kind, a.call["args"]["task"]) for a in tasks(store)]
    assert asked[:2] == [("fake-say", "t1"), ("fake-confirm", "t1")]
    assert len(asked) == 6


def test_sessions_that_settle_nothing_stop_the_run(paper) -> None:
    store, state = paper
    idle = FakeQueueHost(lambda call: "nothing")
    report = run_queue(
        runner(store), Run(store, state, FACTORY, {}), [idle], wait_seconds=0.1
    )
    assert report.stopped is Stop.HOST and report.open == 3
    assert len(idle.sessions) == 1


def test_a_stopped_run_s_spool_is_filed_first(paper) -> None:
    """A spool a run left unfiled (its process ended) is filed when the
    run starts again; its sessions' cost is unknown."""
    store, state = paper
    run = Run(store, state, FACTORY, {})
    with opened(run) as workflow:
        position, _ = advance(workflow, Working(workflow), {})
    spool = Spool(run.directory / "spool-1.jsonl")
    spool.append(
        {"type": "session", "session": "s1", "step": 0, "host": "h", "model": "m"}
    )
    spool.append(
        {
            "type": "answer",
            "key": position.key,
            "step": 0,
            "task": "t1",
            "session": "s1",
            "tool": "say",
            "args": {"task": "t1", "word": "one"},
        }
    )
    report = run_queue(runner(store), run)
    assert report.settled == 1 and "its cost is unknown" in report.detail
    (filed,) = tasks(store)
    assert (filed.key, filed.host, filed.model, filed.session) == (
        position.key,
        "h",
        "m",
        "s1",
    )
    assert filed.call == {"tool": "say", "args": {"task": "t1", "word": "one"}}
    assert not spool.path.exists()


def test_a_run_takes_a_host_a_step(paper) -> None:
    store, state = paper
    host = FakeQueueHost(working_through(saying))
    with pytest.raises(ValueError, match="2 steps take a host each, not 1"):
        run_queue(runner(store), Run(store, state, FACTORY, {"steps": 2}), [host])


def test_a_run_s_directory_is_under_the_store_s_sessions(paper) -> None:
    store, state = paper
    run = Run(store, state, FACTORY, {})
    assert run.directory.parent == store.directory / "sessions"
    assert run.key != Run(store, state, FACTORY, {"steps": 2}).key
    assert queue.SERVER_NAME == "q"


def test_a_run_that_met_a_task_answered_apart_refuses_before_it_pays(
    paper, monkeypatch
) -> None:
    """A run one of whose tasks joined copies answered apart and
    differently refuses before any session is started, as each would end
    at that task again (`docs/architecture.md` § 4, "Copies joined"). The
    fake workflow takes one answer a task, so the conflict is the store's
    word here."""
    store, state = paper
    run = Run(store, state, FACTORY, {})
    first = FakeQueueHost(working_through(saying, stop_after=1), ends=HostOverBudget)
    assert run_queue(runner(store), run, [first], wait_seconds=0.1).settled == 1
    (met,) = tasks(store)
    monkeypatch.setattr(
        RecordStore, "answer_conflicts", lambda self: {met.key: ("a", "b")}
    )
    monkeypatch.setattr(
        queued, "torn", lambda store, key: "answered apart" if key == met.key else None
    )

    counting = FakeQueueHost(working_through(saying))
    with pytest.raises(RecordError, match="answered apart"):
        run_queue(runner(store), run, [counting], wait_seconds=0.1)
    assert counting.spend_maxes == []
