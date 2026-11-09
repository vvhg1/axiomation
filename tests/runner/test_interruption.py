"""A person's interrupt (Ctrl-C) as a run's stop (`runner.interruption`):
the sessions in flight ended, none of them recorded, the run stopped
`interrupted` and resumed by the same command; a second Ctrl-C raises at
once. Through the fake hosts and fake executables; no model is called.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from axiomation.commands import Exit
from axiomation.driver import checker
from axiomation.queue import Run, Spool, opened
from axiomation.records import RecordStore
from axiomation.resolver import Resolver
from axiomation.runner import Runner, Stop, cut_off, interruption, like_runs
from axiomation.runner.claude_code import ClaudeCodeHost
from axiomation.runner.hosts import (
    HostAnswer,
    HostInterrupted,
    HostOverBudget,
    Rates,
    rates_for,
)
from axiomation.runner.opencode import OpenCodeHost
from axiomation.runner.queued import Fitted, Price, price, run_queue, tasks_left
from questions.review_paper import file_questions, make_paper
from queues.fake_workflow import FACTORY, saying
from runner.fakes import (
    FAKE_CLAUDE_ENV,
    FakeHost,
    FakeQueueHost,
    events,
    fake_claude,
    fake_opencode,
    fenced,
    step_finish,
    step_start,
    text,
    working_through,
)
from runner.test_queued import runner as queue_runner
from runner.test_queued import sessions, tasks
from test_marks import kept_update

#: Every attempt is estimated at $0.006, whatever its prompt.
FLAT = Rates(input=0.0, output=1.0)
EMPTY = fenced({"corrections": []})


@pytest.fixture
def asked(tmp_path: Path):
    """A paper and five questions answered in JSON from their prompt
    alone, keyed and worded five ways."""
    paper = make_paper(tmp_path / "paper")
    (base,) = file_questions(paper, None)
    base = replace(base, workspace=None, prompt="Correct the cut; answer in JSON.")
    return paper, tuple(
        replace(base, key=f"{n:02d}{base.key[2:]}", prompt=f"{base.prompt}\n{n}")
        for n in range(5)
    )


def _run(asked, host, store: RecordStore, jobs: int = 1):
    paper, questions = asked
    runner = Runner(
        host,
        Resolver(store),
        10.0,
        FLAT,
        jobs=jobs,
        transcripts=store.transcripts,
    )
    return runner.run(questions, checker(paper.document))


def _cut(answer: str = "", cost: float = 0.0) -> HostInterrupted:
    return HostInterrupted(
        "the session was cut off by an interrupt",
        HostAnswer(text=answer, model="fake/model", cost=cost),
        whole=False,
    )


# --- the runner's file sessions ----------------------------------------------


def test_a_session_cut_off_answers_nothing_and_the_same_run_resumes(
    asked, tmp_path
) -> None:
    """The second question's session is cut off: the first is kept, the
    second recorded as an attempt that answered nothing, with the least it
    spent, counted in the run's spend; nothing more asked; the resumed run
    asks the second and the rest."""
    store = RecordStore(tmp_path / "store")
    keys = [q.key for q in asked[1]]

    def answer(prompt: str) -> str:
        if prompt.endswith("\n1"):
            interruption.interrupt()
            raise _cut("half an answer", cost=0.25)
        return EMPTY

    report = _run(asked, FakeHost(answer), store)

    assert report.stopped is Stop.INTERRUPTED
    assert report.answered == (keys[0],)
    assert report.asked == tuple(keys[:2])
    assert report.spent == pytest.approx(0.01 + 0.25)
    assert "1 session cut off mid-answer" in report.detail
    assert "counted from its turns, at least" in report.detail
    assert report.detail.endswith("the same command resumes")
    assert set(store.authoritative()) == {keys[0]}
    (cut,) = [a for a in store.answers() if a.key == keys[1]]
    assert not cut.valid and cut.cost == pytest.approx(0.25)
    assert cut.problems == ("interrupted: the session was cut off by an interrupt",)
    assert cut_off(cut) and not like_runs([cut]).factors

    interruption.clear()
    resumed = FakeHost(lambda prompt: EMPTY)
    report = _run(asked, resumed, store)

    assert report.stopped is None
    assert report.answered == tuple(keys[1:])
    assert report.skipped == (keys[0],)
    assert resumed.calls == 4
    # the cut attempt's prompt, from the start
    assert resumed.prompts[0] == asked[1][1].prompt


def test_a_session_that_answered_as_the_interrupt_came_is_filed(
    asked, tmp_path
) -> None:
    """What was paid for is kept: the answer given is filed, and nothing
    after it is asked."""
    store = RecordStore(tmp_path / "store")
    keys = [q.key for q in asked[1]]

    def answer(prompt: str) -> str:
        interruption.interrupt()
        return EMPTY

    host = FakeHost(answer)
    report = _run(asked, host, store)

    assert report.stopped is Stop.INTERRUPTED
    assert report.answered == (keys[0],) and host.calls == 1
    assert set(store.authoritative()) == {keys[0]}
    assert report.detail == "by Ctrl-C: the same command resumes"


def test_several_sessions_in_flight_are_each_cut_off(asked, tmp_path) -> None:
    """Four jobs, every session cut off as it runs: none is filed, and the
    stop counts them."""
    store = RecordStore(tmp_path / "store")
    started = threading.Barrier(4)

    def answer(prompt: str) -> str:
        started.wait(timeout=5)
        interruption.interrupt()
        raise _cut()

    report = _run(asked, FakeHost(answer), store, jobs=4)

    assert report.stopped is Stop.INTERRUPTED
    assert "4 sessions cut off mid-answer" in report.detail
    assert report.answered == () and store.authoritative() == {}
    assert len(store.answers()) == 4 and all(map(cut_off, store.answers()))


def test_a_run_after_an_interrupt_asks_nothing(asked, tmp_path) -> None:
    """`sync`'s later steps, a review's later sessions: once interrupted,
    the process asks nothing more."""
    interruption.interrupt()
    host = FakeHost(lambda prompt: EMPTY)

    report = _run(asked, host, RecordStore(tmp_path / "store"))

    assert report.stopped is Stop.INTERRUPTED and host.calls == 0


def test_a_ctrl_c_during_a_run_is_its_stop_and_a_second_raises(asked, tmp_path) -> None:
    """A real SIGINT to this process while a session runs: no
    `KeyboardInterrupt`, the run stopped; then, in a second run, two:
    the second raises at once."""
    store = RecordStore(tmp_path / "store")

    def answer(prompt: str) -> str:
        os.kill(os.getpid(), signal.SIGINT)
        _until(interruption.interrupted)
        raise _cut()

    report = _run(asked, FakeHost(answer), store)

    assert report.stopped is Stop.INTERRUPTED and store.authoritative() == {}
    assert signal.getsignal(signal.SIGINT) is not interruption._on_interrupt

    interruption.clear()
    seen = threading.Event()

    def twice(prompt: str) -> str:
        os.kill(os.getpid(), signal.SIGINT)
        _until(interruption.interrupted)
        os.kill(os.getpid(), signal.SIGINT)
        seen.wait(timeout=5)
        raise _cut()

    with pytest.raises(KeyboardInterrupt):
        try:
            _run(asked, FakeHost(twice), store)
        finally:
            seen.set()


def _until(condition, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.01)


# --- the hosts ---------------------------------------------------------------


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    # a child of the session, reparented once it ended, may linger a
    # moment as a zombie: `ps` says so
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return state == "" or state.startswith("Z")


def test_claude_code_ends_a_session_and_its_children_on_an_interrupt(
    tmp_path, monkeypatch, host_scratch
) -> None:
    executable = fake_claude(tmp_path / "bin")
    log = tmp_path / "log.jsonl"
    for name in FAKE_CLAUDE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "60")
    monkeypatch.setenv("FAKE_CLAUDE_CHILD", "60")
    monkeypatch.setenv("FAKE_CLAUDE_TURN", "claude-haiku-5-5")
    host = ClaudeCodeHost(
        model="haiku",
        executable=str(executable),
        scratch=host_scratch,
        projects=tmp_path / "projects",
    )

    def interrupt_once_started() -> None:
        _until(log.exists)
        interruption.interrupt()

    threading.Timer(0.5, interrupt_once_started).start()
    started = time.monotonic()

    with pytest.raises(HostInterrupted) as raised:
        host.ask("Answer in JSON.", 120)

    assert time.monotonic() - started < 10
    assert raised.value.whole is False
    # the least it spent: its one turn, counted once, at the model's rates
    turn = {"input": 1000, "output": 500, "cache_read": 20000, "cache_write": 4000}
    assert raised.value.answer.cost == pytest.approx(
        rates_for("claude-haiku-5-5").priced({**turn, "reasoning": 0})
    )
    assert raised.value.answer.tokens["output"] == 500
    assert "interrupted" in raised.value.answer.error
    (record,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert _gone(record["pid"])
    _until(lambda: _gone(record["child_pid"]))
    assert not host._live


def test_opencode_ends_a_session_on_an_interrupt_and_counts_its_cost(
    tmp_path, monkeypatch, host_scratch
) -> None:
    executable = fake_opencode(tmp_path / "bin")
    played = tmp_path / "events.jsonl"
    played.write_text(
        events(step_start(), text("half"), step_finish(cost=0.004, input=10, output=5)),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_OPENCODE_LOG", str(tmp_path / "opencode.log"))
    monkeypatch.setenv("FAKE_OPENCODE_EVENTS", str(played))
    monkeypatch.setenv("FAKE_OPENCODE_SLEEP", "60")
    for name in ("EXIT", "EXPORT", "EXPORT_EXIT", "EDIT", "FIX", "SESSIONS"):
        monkeypatch.delenv(f"FAKE_OPENCODE_{name}", raising=False)
    host = OpenCodeHost(executable=str(executable), scratch=host_scratch)
    threading.Timer(0.5, interruption.interrupt).start()
    started = time.monotonic()

    with pytest.raises(HostInterrupted) as raised:
        host.ask("Answer in JSON.", 120)

    assert time.monotonic() - started < 10
    assert raised.value.whole is True
    assert raised.value.answer.cost == pytest.approx(0.004)
    assert not host._live


# --- a queue run ---------------------------------------------------------------


@pytest.fixture
def queued(tmp_path):
    v, state = kept_update(tmp_path)
    return v.store, state


def _interrupting(script):
    """A session's script that interrupts the run as it ends."""

    def interrupted(call):
        text = script(call)
        interruption.interrupt()
        return text

    return interrupted


def test_a_queue_session_cut_off_files_what_it_settled_and_resumes(queued) -> None:
    """Cut off after two answers: both are filed, its session record
    says why it stopped, no pair follows; the resumed run serves only the
    third task."""
    store, state = queued
    run = Run(store, state, FACTORY, {})
    cut = FakeQueueHost(
        _interrupting(working_through(saying, stop_after=2)),
        ends=lambda message, answer: HostInterrupted(message, answer, False),
    )

    report = run_queue(queue_runner(store), run, [cut], wait_seconds=0.1)

    assert report.stopped is Stop.INTERRUPTED
    assert (report.settled, report.open) == (2, 1)
    assert len(cut.sessions) == 1
    assert "1 session cut off: the tasks it settled are filed" in report.detail
    (session,) = sessions(store)
    assert session.problems[0].startswith("interrupted: ")
    assert not list(run.directory.glob("spool-*"))

    interruption.clear()
    served: list[str] = []

    def answer(text: str):
        served.append(text.split()[1])
        return saying(text)

    report = run_queue(
        queue_runner(store),
        run,
        [FakeQueueHost(working_through(answer))],
        wait_seconds=0.1,
    )
    assert report.complete and served == ["t3"]
    assert [a.order for a in tasks(store)] == [1, 2, 3]


def test_a_queue_interrupted_between_pairs_starts_no_further_pair(queued) -> None:
    """The first pair ends as the interrupt comes: what it settled is
    filed, and no second pair starts."""
    store, state = queued
    run = Run(store, state, FACTORY, {})
    host = FakeQueueHost(_interrupting(working_through(saying, stop_after=1)))

    report = run_queue(queue_runner(store), run, [host], wait_seconds=0.1)

    assert report.stopped is Stop.INTERRUPTED
    assert (report.settled, report.open) == (1, 2)
    assert len(host.sessions) == 1
    (session,) = sessions(store)
    assert session.valid


def test_an_interrupt_while_the_tool_servers_are_checked_runs_no_session(
    queued,
) -> None:
    """The interrupt reaches the server being checked, which then fails:
    the stop is the interrupt, and no session runs."""
    store, state = queued
    run = Run(store, state, FACTORY, {})

    class Checked(FakeQueueHost):
        def tool_server_error(self, server):
            interruption.interrupt()
            return "KeyboardInterrupt"

    host = Checked(working_through(saying))

    report = run_queue(queue_runner(store), run, [host], wait_seconds=0.1)

    assert report.stopped is Stop.INTERRUPTED
    assert "tool server" not in report.detail
    assert host.sessions == [] and sessions(store) == []
    assert not list(run.directory.glob("*"))


def test_a_queue_after_an_interrupt_asks_nothing(queued) -> None:
    store, state = queued
    interruption.interrupt()
    host = FakeQueueHost(working_through(saying))

    report = run_queue(
        queue_runner(store), Run(store, state, FACTORY, {}), [host], wait_seconds=0.1
    )

    assert report.stopped is Stop.INTERRUPTED and host.sessions == []


def test_a_cut_session_counts_in_the_spend_and_not_in_the_price(queued) -> None:
    """A Claude Code queue session cut off is recorded, and counted in the
    run's spend, at the least it spent; a step's price reads only the
    sessions no interrupt cut off."""
    store, state = queued
    run = Run(store, state, FACTORY, {})
    cut = FakeQueueHost(
        _interrupting(working_through(saying, stop_after=2)),
        cost=0.2,
        ends=lambda message, answer: HostInterrupted(message, answer, False),
    )
    report = run_queue(queue_runner(store), run, [cut], wait_seconds=0.1)
    assert report.spent == pytest.approx(0.2)
    (session,) = sessions(store)
    assert session.cost == pytest.approx(0.2) and cut_off(session)
    interruption.clear()
    run_queue(
        queue_runner(store),
        run,
        [FakeQueueHost(working_through(saying), cost=0.03)],
        wait_seconds=0.1,
    )
    template = session.template.name

    assert price(store, template, "fake/queue", Fitted("fake/queue", 0.0, 0.01)) == (
        Price(0.0, 0.03, 1, 1)
    )


def test_a_dry_run_after_a_cut_counts_only_the_tasks_left(queued) -> None:
    """Two of three tasks settled before the cut: the resume, and its dry
    run's price, serve the third alone."""
    store, state = queued
    run = Run(store, state, FACTORY, {})
    with opened(run) as workflow:
        assert [t.id for t in tasks_left(store, workflow)] == ["t1", "t2", "t3"]
    cut = FakeQueueHost(
        _interrupting(working_through(saying, stop_after=2)),
        ends=lambda message, answer: HostInterrupted(message, answer, False),
    )
    run_queue(queue_runner(store), run, [cut], wait_seconds=0.1)

    with opened(run) as workflow:
        assert [t.id for t in tasks_left(store, workflow)] == ["t3"]


def test_a_spool_line_a_kill_tore_is_left_out(tmp_path) -> None:
    spool = Spool(tmp_path / "spool-1.jsonl")
    spool.append({"type": "ended", "why": "whole"})
    with spool.path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "answer", "ke')

    assert spool.entries() == [{"type": "ended", "why": "whole"}]


# --- the handler -----------------------------------------------------------------


def test_the_handler_a_run_replaced_is_back_after_it(asked, tmp_path) -> None:
    def own(signum, frame) -> None:
        raise AssertionError("not while the run asks")

    signal.signal(signal.SIGINT, own)
    _run(asked, FakeHost(lambda prompt: EMPTY), RecordStore(tmp_path / "a"))
    assert signal.getsignal(signal.SIGINT) is own

    def answer(prompt: str) -> str:
        os.kill(os.getpid(), signal.SIGINT)
        _until(interruption.interrupted)
        raise _cut()

    _run(asked, FakeHost(answer), RecordStore(tmp_path / "b"))
    assert signal.getsignal(signal.SIGINT) is own


def test_a_process_that_ignores_ctrl_c_goes_on_ignoring_it(asked, tmp_path) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def answer(prompt: str) -> str:
        assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
        return EMPTY

    report = _run(asked, FakeHost(answer), RecordStore(tmp_path / "store"))

    assert report.stopped is None
    assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN


def test_sessions_cut_off_under_another_stop_are_named(asked, tmp_path) -> None:
    """Two jobs: one session ends at the budget, then the interrupt cuts
    the other off. The stop is the budget's, and the cut session is
    named with it."""
    store = RecordStore(tmp_path / "store")
    both = threading.Barrier(2)

    def answer(prompt: str) -> str:
        both.wait(timeout=5)
        if prompt.endswith("\n0"):
            raise HostOverBudget(
                "the session reached its limit", HostAnswer("", "fake/model")
            )
        time.sleep(0.2)
        interruption.interrupt()
        raise _cut()

    report = _run(asked, FakeHost(answer), store, jobs=2)

    assert report.stopped is Stop.BUDGET
    assert report.detail.startswith("the session reached its limit")
    assert "; 1 session cut off mid-answer" in report.detail
    assert "counted from its turns, at least" in report.detail


# --- the command line ---------------------------------------------------------


def test_a_command_interrupted_outside_a_run_ends_in_one_line(
    monkeypatch, capsys
) -> None:
    """A Ctrl-C where no run asks raises as Python does; the command ends
    with one line and the shell's code for it, not a traceback."""
    from axiomation import cli

    def interrupted(argv):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_command", interrupted)

    assert cli.main(["review", "paper"]) == Exit.INTERRUPTED
    err = capsys.readouterr().err
    assert err == (
        "axiomation review: interrupted; what was filed is kept: the same "
        "command resumes\n"
    )


def test_review_cut_stops_on_ctrl_c_with_no_session_left(
    tmp_path, host_scratch
) -> None:
    """The real command, its own process: a Ctrl-C while its sessions run
    prints the stop, exits 3, leaves no session running; the same command
    then asks what is missing and keeps its state."""
    paper = make_paper(tmp_path / "paper")
    bin_directory = tmp_path / "bin"
    fake_claude(bin_directory)
    log = tmp_path / "claude.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_CLAUDE")} | {
        "PATH": f"{bin_directory}:/usr/bin:/bin",
        "AXIOMATION_HOST_SCRATCH": str(host_scratch),
        "FAKE_CLAUDE_LOG": str(log),
        "FAKE_CLAUDE_PROJECTS": str(tmp_path / "projects"),
        "FAKE_CLAUDE_SLEEP": "60",
    }
    store = tmp_path / "store"
    command = [
        sys.executable,
        "-m",
        "axiomation.cli",
        "review-cut",
        str(paper.root),
        "--store",
        str(store),
        "--host",
        "claude-code",
        "--rates",
        "1,5",
        "--budget",
        "1",
        "--chunk",
        "0",
        "--jobs",
        "1",
    ]
    process = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        _until(lambda: log.exists() and log.read_text().strip(), seconds=60)
        process.send_signal(signal.SIGINT)
        out, err = process.communicate(timeout=30)
    finally:
        process.kill()

    # the report out, the process ends by SIGINT: a calling shell stops
    assert process.returncode == -signal.SIGINT, err
    assert "Traceback" not in err
    assert "stopped: interrupted: by Ctrl-C; 1 session cut off" in out
    (record,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert _gone(record["pid"])
    (cut,) = RecordStore(store).answers()
    assert cut_off(cut) and not cut.valid

    env.pop("FAKE_CLAUDE_SLEEP")
    resumed = subprocess.run(command, env=env, capture_output=True, text=True)
    assert resumed.returncode == 0, resumed.stderr
    # each question asked once more, the cut one among them, and answered
    asked_again = log.read_text().splitlines()[1:]
    assert len(asked_again) == len(RecordStore(store).authoritative())
    first = json.loads(log.read_text().splitlines()[0])["stdin"]
    assert first in [json.loads(line)["stdin"] for line in asked_again]
