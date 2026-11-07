"""What an edges queue run gives (docs/architecture.md § 2): the run
asked, its answers replayed from the store, and once every task is
settled, the lines they settled given as one edges edit.

The support search, the reread and the circles end so. The edit is the
working sheet, given by the last step's model in the run's session; given
before, its state is found, not given again, so a replay with no host
reaches the same state. A run with tasks open gives nothing; a run a
breach stopped gives nothing either, since what it settled was not filed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from axiomation import edits, queue, versions
from axiomation.changes import Layer
from axiomation.runner import Runner, Stop
from axiomation.runner.hosts import Host
from axiomation.runner.queued import QueueReport, open_tasks, run_queue
from axiomation.versions import State


class NotAsked(ValueError):
    """A run refused before it is asked: its state takes no edit."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """A run's outcome: its queue's report (None: asked of no host), the
    answer that settled each task, in its order, the tasks still open, and
    once none is, its edges edit: the answer's key and the state it made,
    now or in a run before (None: it changed no line), or the problems it
    was refused with."""

    report: QueueReport | None
    settled: tuple[queue.Used, ...] = ()
    open: int = 0
    edit: str | None = None
    state: State | None = None
    problems: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.open == 0


def ask(
    run: queue.Run,
    model: str,
    reason: Callable[[queue.Workflow, Sequence[queue.Used]], str],
    marks_shown: bool,
    runner: Runner | None = None,
    hosts: Sequence[Host] = (),
    shares: Sequence[float] | None = None,
    context_tokens: int | None = None,
    wait_seconds: float = queue.WAIT_SECONDS,
) -> Outcome:
    """`run` asked of `hosts`, one a step, through `runner` (None: what the
    store holds replayed, nothing asked); complete, replayed or asked, the working
    sheet given as `model`'s edges edit in the run's session, with
    `reason(workflow, settled)` and whether its sessions were shown the
    stale marks (`edits.give`'s `marks_shown`). Refused before anything is
    asked where the state takes no edit (`edits.refused`)."""
    if runner is not None and hosts:
        with queue.opened(run) as workflow:
            why = edits.refused(_kept(workflow), "")
        if why is not None:
            raise NotAsked(f"not asked: {why}")
    report = (
        None
        if runner is None
        else run_queue(runner, run, hosts, shares, context_tokens, wait_seconds)
    )
    if report is not None and report.stopped is Stop.BREACH:
        return Outcome(report, open=report.open)
    with queue.opened(run) as workflow:
        working = queue.Working(workflow)
        position, used = queue.advance(
            workflow, working, queue.recorded(run.store, workflow)
        )
        last: dict[str, queue.Used] = {}
        for u in used:
            if u.settled:
                last[u.task.id] = u
        settled = tuple(last.values())
        if position.task is not None:
            return Outcome(report, settled, open_tasks(workflow, working))
        if working.sheet == workflow.sheet:
            return Outcome(report, settled)
        kept = _kept(workflow)
        # in the run's session, as its tasks' answers are: what they bear
        author = edits.agent(model, run.sessions)
        key = edits.question(kept, Layer.EDGES, working.sheet, author).key
        made = next((s for s in versions.states(run.store) if key in s.answers), None)
        if made is not None:
            return Outcome(report, settled, edit=key, state=made)
        given = edits.give(
            kept,
            Layer.EDGES,
            working.sheet,
            author,
            reason(workflow, settled),
            marks_shown=marks_shown,
        )
    if given.problems:
        return Outcome(report, settled, problems=given.problems)
    assert given.recorded is not None
    return Outcome(report, settled, edit=given.recorded.answer.key, state=given.state)


class EdgesWorkflow(queue.Workflow, Protocol):
    """A workflow whose lines are edges lines, on its kept state opened."""

    kept: edits.Kept


def _kept(workflow: queue.Workflow) -> edits.Kept:
    return cast(EdgesWorkflow, workflow).kept
