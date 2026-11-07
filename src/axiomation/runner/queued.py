"""A queue run (docs/architecture.md § 2): each step's session at once on
one spool, then what they settled filed through the resolver.

The run's budget is the runner's, split between the steps' sessions by
their shares, each session's limit its part of what is left. When one
session ends, the spool says the run has ended, and the other stops at
its next call; one that has not stopped `END_GRACE_SECONDS` later is
ended. A pair of sessions one of which breached files nothing: what it
settled may rest on what it read outside its tools. Else the settled
answers are filed in their order, one answer
record a task's step (cost 0), and one record a session with its cost,
tokens and the task answers it gave; each in the runner's review round,
where it has one. An answer accepted but not settled
is dropped: its task is served again. A run with tasks still open and
budget left starts fresh sessions on the rest; one whose sessions settled
nothing stops. Without hosts it files what a stopped run's spool left and
replays what the store holds.

A step's price (`price`) is what its session's start and each task cost:
fitted on recorded runs, then measured on the store's sessions.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

from axiomation import queue
from axiomation.questions import Question, QueueSession, Validation
from axiomation.questions.review_file import current_kind
from axiomation.records import RecordError, RecordStore
from axiomation.resolver import Attempt, torn
from axiomation.runner import Runner, Stop
from axiomation.runner.hosts import (
    Host,
    HostAnswer,
    HostBreach,
    HostOutOfCredit,
    HostOverBudget,
    HostUncounted,
    rates_for,
)

#: A session record's kind: the cost of one step's session, over the task
#: answers it gave.
SESSION_KIND = "queue-session"
#: How long a queue session may run.
QUEUE_TIMEOUT = 4 * 3600.0
#: How long a step's session may go on once the other ended.
END_GRACE_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class QueueReport:
    """A queue run: the tasks settled in it and those left open, the
    session records it wrote, what it spent, and why it stopped (None:
    every task is settled, or no host was given)."""

    settled: int = 0
    open: int = 0
    sessions: tuple[str, ...] = ()
    spent: float = 0.0
    stopped: Stop | None = None
    detail: str = ""

    @property
    def complete(self) -> bool:
        return self.open == 0


@dataclass(frozen=True, slots=True)
class _Session:
    step: int
    key: str
    #: the run's `sessions` prefix, then the key's head
    id: str
    host: Host
    asked: QueueSession
    spend_max: float


@dataclass(frozen=True, slots=True)
class _Ended:
    answer: HostAnswer
    stopped: Stop | None = None
    detail: str = ""


def run_queue(
    runner: Runner,
    run: queue.Run,
    hosts: Sequence[Host] = (),
    shares: Sequence[float] | None = None,
    context_tokens: int | None = None,
    wait_seconds: float = queue.WAIT_SECONDS,
    timeout: float = QUEUE_TIMEOUT,
) -> QueueReport:
    """`run` asked of `hosts`, one a step, under the runner's budget, its
    sessions' limits split by `shares` (equal where not given); without
    hosts, what the store holds replayed and nothing asked."""
    store = runner.resolver.store
    started = runner.spent
    with runner.resolver.claim(), queue.opened(run) as workflow:
        steps = workflow.steps
        if hosts and len(hosts) != len(steps):
            raise ValueError(f"{len(steps)} steps take a host each, not {len(hosts)}")
        weights = list(shares or [1.0] * len(steps))
        settled = 0
        written: list[str] = []
        notes: list[str] = []
        for left in sorted(run.directory.glob("spool-*.jsonl")):
            n = _file(runner, workflow, run, queue.Spool(left))
            settled += n
            if n:
                notes.append(f"filed {n} tasks a stopped run left; its cost is unknown")
            left.unlink()
        # a run that met a key joined copies answered apart refuses before
        # it pays for any task: each run would end there again
        conflicts = store.answer_conflicts()
        met = next(
            (
                a.key
                for a in store.answers()
                if a.key in conflicts and (a.session or "").startswith(run.sessions)
            ),
            None,
        )
        if met is not None:
            raise RecordError(torn(store, met) or met)
        pair = 0
        stopped: Stop | None = None
        detail = ""
        while True:
            working = queue.Working(workflow)
            position, _ = queue.advance(
                workflow, working, queue.recorded(store, workflow)
            )
            if position.task is None or not hosts or stopped is not None:
                break
            # a task whose key joined copies answered apart is not served
            # again: each run would pay for an answer no read takes
            refusal = torn(store, position.key)
            if refusal is not None:
                raise RecordError(refusal)
            left = runner.budget - runner.spent
            if left <= 0:
                stopped, detail = (
                    Stop.BUDGET,
                    f"the budget of ${runner.budget:g} is spent",
                )
                break
            pair += 1
            spool = queue.Spool(run.directory / f"spool-{pair}.jsonl")
            spool.path.unlink(missing_ok=True)
            sessions = []
            for n, (step, host) in enumerate(zip(steps, hosts, strict=True)):
                key = hashlib.sha256(
                    json.dumps([run.key, pair, n, time.time()]).encode("utf-8")
                ).hexdigest()
                sid = f"{run.sessions}{key[:8]}"
                server = queue.tool_server(run, n, sid, spool.path, wait_seconds)
                sessions.append(
                    _Session(
                        n,
                        key,
                        sid,
                        host,
                        queue.queue_session(
                            step, server, context_tokens, workflow.serving.files
                        ),
                        left * weights[n] / sum(weights),
                    )
                )
                spool.append(
                    {
                        "type": "session",
                        "session": sid,
                        "step": n,
                        "host": host.name,
                        "model": host.model,
                        "effort": host.effort,
                    }
                )
            ended = _sessions(sessions, spool, timeout)
            for s in sessions:
                runner.spent += ended[s.id].answer.cost
            breached = any(e.stopped is Stop.BREACH for e in ended.values())
            n = 0 if breached else _file(runner, workflow, run, spool)
            settled += n
            for s in sessions:
                written.append(_session_record(runner, workflow, run, s, ended[s.id]))
                (run.directory / f"{s.id}.json").unlink(missing_ok=True)
            spool.path.unlink(missing_ok=True)
            stops = [e for e in ended.values() if e.stopped is not None]
            if stops:
                first = next((e for e in stops if e.stopped is Stop.BREACH), stops[0])
                stopped, detail = first.stopped, first.detail
            elif n == 0:
                stopped, detail = Stop.HOST, "the sessions settled no task"
        return QueueReport(
            settled,
            0 if position.task is None else open_tasks(workflow, working),
            tuple(written),
            runner.spent - started,
            stopped,
            "; ".join(filter(None, [*notes, detail])),
        )


def open_tasks(workflow: queue.Workflow, working: queue.Working) -> int:
    """The tasks the run still draws that no answer settled."""
    done = working.ids
    return sum(t.id not in done for t in workflow.draw(working))


def _sessions(
    sessions: Sequence[_Session], spool: queue.Spool, timeout: float
) -> dict[str, _Ended]:
    """Each step's session asked at once; the first to end ends the run's
    spool, and a session still going `END_GRACE_SECONDS` later is ended."""

    def ask(s: _Session) -> _Ended:
        try:
            return _Ended(s.host.ask_queue(s.asked, timeout, s.spend_max))
        except HostOverBudget as over:
            return _Ended(over.answer, Stop.BUDGET, str(over))
        except HostOutOfCredit as refusal:
            return _Ended(refusal.answer, Stop.CREDIT, str(refusal))
        except HostUncounted as uncounted:
            return _Ended(uncounted.answer, Stop.UNCOUNTED, str(uncounted))
        except HostBreach as breach:
            for other in sessions:
                other.host.abort()
            return _Ended(
                breach.answer, Stop.BREACH, f"the host was breached: {breach}"
            )

    with ThreadPoolExecutor(max_workers=len(sessions)) as pool:
        futures = {pool.submit(ask, s): s for s in sessions}
        done, going = wait(futures, return_when=FIRST_COMPLETED)
        first = futures[next(iter(done))]
        spool.end(f"step {first.step}'s session ended")
        if going:
            _, still = wait(going, timeout=END_GRACE_SECONDS)
            for f in still:
                futures[f].host.abort()
        return {futures[f].id: f.result() for f in futures}


def _file(
    runner: Runner,
    workflow: queue.Workflow,
    run: queue.Run,
    spool: queue.Spool,
) -> int:
    """The spool's settled answers filed in their order, each its step's
    answer record; returns the tasks settled. Its session's host and model
    are the spool's."""
    store = runner.resolver.store
    known = queue.recorded(store, workflow)
    entries = spool.entries()
    hosts = {e["session"]: e for e in entries if e["type"] == "session"}
    by_key = {e["key"]: e["session"] for e in entries if e["type"] == "answer"}
    _, used = queue.advance(
        workflow, queue.Working(workflow), {**known, **spool.calls()}
    )
    tasks: set[str] = set()
    for order, u in enumerate(used, 1):
        if not u.settled or u.key in known:
            continue
        step = workflow.steps[u.step]
        session = by_key[u.key]
        given = hosts.get(session, {})
        runner.resolver.record(
            Attempt(
                question=Question(
                    u.key, step.kind, step.stage, step.template, u.served
                ),
                host=str(given.get("host", "unknown")),
                model=str(given.get("model", "unknown")),
                text=u.call.raw,
                call={"tool": u.call.tool, "args": dict(u.call.args)},
                cost=0.0,
                seconds=0.0,
                validation=Validation(judgments=u.judgments),
                effort=given.get("effort"),
                profile=runner.profile,
                review_round=runner.review_round,
                session=session,
                state=None if run.state is None else run.state.id,
                order=order,
            )
        )
        tasks.add(u.task.id)
    return len(tasks)


def _session_record(
    runner: Runner,
    workflow: queue.Workflow,
    run: queue.Run,
    session: _Session,
    ended: _Ended,
) -> str:
    """The session's record: its cost and tokens, the task answers it
    gave, why it stopped; returns its key."""
    store = runner.resolver.store
    step = workflow.steps[session.step]
    gave = tuple(
        a.key
        for a in store.answers()
        if a.session == session.id and a.kind == step.kind
    )
    answer = ended.answer
    problems = (
        () if ended.stopped is None else (f"{ended.stopped.value}: {ended.detail}",)
    )
    asked = Question(
        session.key, SESSION_KIND, step.stage, step.template, session.asked.prompt
    )
    runner.resolver.record(
        Attempt(
            question=asked,
            host=session.host.name,
            model=answer.model or session.host.model,
            text=answer.text,
            cost=answer.cost,
            seconds=answer.seconds,
            validation=Validation(problems=problems),
            tokens=answer.tokens,
            tokens_by_model=answer.tokens_by_model,
            effort=session.host.effort,
            profile=runner.profile,
            review_round=runner.review_round,
            session=session.id,
            state=None if run.state is None else run.state.id,
            tasks=gave,
        )
    )
    runner._transcript(asked, "session", answer, keep=True)
    return session.key


# --- a step's price ----------------------------------------------------------


class PriceError(ValueError):
    """A step that cannot be priced: no rates for its model."""


@dataclass(frozen=True, slots=True)
class Fitted:
    """A step's price fitted on recorded runs: on `model`, a session's
    start (its brief and the host's prompt written once, the call that
    serves the first task) and each task's cost."""

    model: str
    start: float
    task: float


@dataclass(frozen=True, slots=True)
class Price:
    """What a step is expected to cost: its session's start and each task,
    and the sessions and tasks the task's cost was measured on in this
    store (none: fitted)."""

    start: float
    task: float
    sessions: int = 0
    tasks: int = 0

    def cost(self, tasks: int, sessions: int = 1) -> float:
        """A run of `tasks` tasks over `sessions` sessions: each session's
        start and every task; no task, no session."""
        return sessions * self.start + tasks * self.task if tasks else 0.0


def price(store: RecordStore, template: str, model: str, fitted: Fitted) -> Price:
    """What the step whose brief is `template` is expected to cost on
    `model`. The start is `fitted`'s, scaled by `model`'s input rate over
    the measured model's: a session record holds no turn's cost, so the
    store cannot tell its start from its tasks. A task is what the store's
    sessions of that step on that model cost, every one, less their
    starts, over the tasks they gave; where they gave none, `fitted`'s,
    scaled so. A session is matched by the model its host reported, so a
    profile naming an alias is priced as fitted. A model `prices.toml`
    does not price is priced by the store's sessions alone, each task
    bearing its share of their starts, and refused where it holds none."""
    sessions = [
        a
        for a in store.answers()
        if a.kind == SESSION_KIND
        and current_kind(a.template.name) == template
        and a.model == model
    ]
    tasks = sum(len(a.tasks) for a in sessions)
    spent = sum(a.cost for a in sessions)
    scale = _scale(model, fitted.model)
    if scale is None:
        if not tasks:
            raise PriceError(
                f"no rates are known for {model}: add its price to prices.toml"
            )
        return Price(0.0, spent / tasks, len(sessions), tasks)
    start = fitted.start * scale
    if not tasks:
        return Price(start, fitted.task * scale)
    return Price(
        start, max(spent - len(sessions) * start, 0.0) / tasks, len(sessions), tasks
    )


def _scale(model: str, measured: str) -> float | None:
    """`model`'s input rate over `measured`'s; None where either is unknown."""
    if model == measured:
        return 1.0
    mine, theirs = rates_for(model), rates_for(measured)
    if mine is None or theirs is None:
        return None
    return mine.input / theirs.input
