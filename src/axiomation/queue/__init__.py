"""A question as a queue of tasks (docs/architecture.md § 2).

Code draws a workflow's tasks from a kept state, or from a question no
kept state holds yet (`Asked`). A session pulls them one at a time
through a tool server (`queue.server`): `next` serves a task, lean, with
pointers into the paper and only the text its workflow gives it; the
reads (`queue.reads`) give more on demand; a typed answer is turned into
the workflow's own lines by code and checked by its own gate on that one
task, against the working state.
An accepted answer returns the next task.

**The working state** is the kept state with the lines the run settled so
far laid over it: every task served, every read and every check sees
them. Each task's key binds the kind, the stage, the template and its
version, the workflow's steps, the state, the task (its id and its text
as its step renders it, not its place in the count), the key of the
last answer that laid lines on the working state, and a review session's
round, so an answer replays only on the working state and in the round
it was given in (`advance`). A key 0.1.0 made
(the task as served, its count with it, after the key settled before it)
is looked up too, so a store of 0.1.0 resumes as it is.

**Steps in lockstep.** A workflow may hand an answer to a second step,
another model's session, before it settles. Both sessions run at once on
one spool: a step serves only where the run stands at it, and its `next`
waits otherwise, returning before the host's tool limit with "call `next`
again". The working state so holds settled answers only.

**The spool** lies outside the session, in the store's `sessions/`: each
answer accepted, in order, and the run's end once a session ended. The
runner files what it settled through the resolver, the store's only
writer, then removes it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from axiomation import edits, queries
from axiomation.questions import (
    QueueSession,
    Stage,
    Template,
    ToolServer,
)
from axiomation.queue.reads import ORDER_HOPS, ReadError, Reads
from axiomation.records import JudgmentRecords, RecordStore
from axiomation.versions import State

#: A queue session's prompt: its brief says the rest.
KICKOFF = "Call `next` and work through your tasks."
#: The tool server's name in a session's configuration: its tools are
#: `mcp__q__<name>` to the host.
SERVER_NAME = "q"
#: How long `next` waits for the other step before it returns, and the
#: margin the host's tool limit is set above it.
WAIT_SECONDS = 540.0
TOOL_LIMIT_MARGIN = 60.0
POLL_SECONDS = 0.25

EMPTY = "the queue is empty: stop now and say done."
ENDED = "the run has ended: stop now and say done."
AGAIN = "nothing to serve yet: the other step is working. Call `next` again."
SHARE_DONE = "your share of the tasks is done: stop now and say done."


class QueueError(RuntimeError):
    """A run that cannot go on: a recorded answer its step no longer
    takes."""


# --- what a workflow gives ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool a session is given: its name, what it is told it does, and
    its arguments' JSON schema."""

    name: str
    description: str
    schema: Mapping[str, Any]

    def listed(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.schema),
        }


@dataclass(frozen=True, slots=True)
class Task:
    """A task: its id within the run, the block it is on (a node id, which
    the order walks from), and what its steps render and check."""

    id: str
    block: str
    subject: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Accepted:
    """An answer its step accepts: the sheet lines it settles (a fragment,
    `edits.parse_fragment`'s; empty, none) and the judgments filed with it
    where it settles its task, or what it hands to the next step instead
    (`hand_on`, added to the task's subject there)."""

    lines: str = ""
    hand_on: Mapping[str, Any] | None = None
    judgments: JudgmentRecords = JudgmentRecords()


@dataclass(frozen=True, slots=True)
class Refused:
    """An answer its step refuses, and why: nothing is spooled."""

    problems: tuple[str, ...]


class Step(Protocol):
    """One step of a workflow, one model's session: its kind and stage as
    its answers are recorded, its brief, the message that starts it, and
    its answer tools. A step may pass a task by kind (`takes`, where it
    gives one): the next step is served it as drawn. The last takes every
    task. A step whose `web` is true (where it gives one) searches the
    web as well (`QueueSession.web`)."""

    kind: str
    stage: Stage
    template: Template
    kickoff: str
    tools: tuple[Tool, ...]

    def render(self, task: Task, working: Working) -> str:
        """The task as served, lean: what its key binds."""
        ...

    def answer(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        """`tool`'s answer to `task` at the working state, its lines by
        code; checked by the workflow's gate where `check` (a replay's
        answers were checked when given)."""
        ...


@dataclass(frozen=True, slots=True)
class Image:
    """A read's answer that is a picture, a page's PNG, with the line that
    says what it shows."""

    png: bytes
    said: str


@dataclass(frozen=True, slots=True)
class Read:
    """A read a workflow adds to the core's, or gives in place of the
    core's of its name: its tool, and what it answers at the working
    state, text or a picture, refused by a `ReadError`."""

    tool: Tool
    answer: Callable[[Working, Mapping[str, Any]], str | Image]


@dataclass(frozen=True, slots=True)
class Serving:
    """How a workflow's sessions are served: the core's reads it withholds
    by name; the files laid in a session's folder, by path, which the
    session reads with the host's own read tools, confined there; and the
    tasks a session answers before it ends and a fresh one takes the
    queue on (None: a session runs to the queue's end)."""

    withheld: frozenset[str] = frozenset()
    files: Mapping[str, str] = field(default_factory=dict)
    session_tasks: int | None = None


class Workflow(Protocol):
    """A workflow on a kept state, or on a question no kept state holds:
    its view (None on a question, which then names the question's key as
    `anchor`, gives its own reads and draws in the paper's order), the
    paper's files there, the edges sheet the state shows, its steps, the
    first drawing the tasks, whether its briefs write an about line as a
    restatement (its settled lines are applied so, as its gate applies them,
    `edits.applied_fragment`), the reads it adds to the core's, and whether
    its tasks are served nearest the last one settled on the graph or in
    the paper's order alone (a task drawn whole paper long, where the graph
    is what the run writes), and how its sessions are served. A workflow
    whose settled lines are its own notation, not a fragment, lays them
    itself (`apply(sheet, lines)`, where it gives one). A review session's
    workflow names its review round (`round`, where it gives one): its
    tasks' keys bind it, so another round, which a person's dismissal or
    proposal makes, asks its tasks anew though their text is the same."""

    view: queries.View | None
    files: Path
    sheet: str
    steps: tuple[Step, ...]
    restating: bool
    reads: tuple[Read, ...]
    by_graph: bool
    serving: Serving

    def draw(self, working: Working) -> list[Task]:
        """The tasks open at the working state, in the paper's order; a
        task settled is left out by the core."""
        ...


# --- the working state ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settled:
    """A task settled: its answers' keys, one a step it reached, the lines
    it settled, and the keys 0.1.0 gave its answers (`advance`)."""

    task: Task
    keys: tuple[str, ...]
    lines: str
    keys_0_1_0: tuple[str, ...]


class Working:
    """The kept state with the lines settled so far laid over its sheet."""

    def __init__(self, workflow: Workflow) -> None:
        self.view = workflow.view
        self.sheet = workflow.sheet
        self.restating = workflow.restating
        #: what its answers' keys bind: its kept state, or its question
        self.at = anchor_of(workflow)
        #: the core's reads, over its kept state; none on a question
        self.reads = (
            None if workflow.view is None else Reads(workflow.view, workflow.files)
        )
        self.settled: list[Settled] = []
        #: A workflow whose settled lines are its own notation, not a
        #: fragment, lays them over the sheet itself.
        self.apply: Callable[[str, str], str] | None = getattr(workflow, "apply", None)
        #: the review round its tasks' keys bind, where it is one's
        self.round: str | None = getattr(workflow, "round", None)

    @property
    def chain(self) -> str:
        """What the next task's key follows: the key of the last answer
        that laid lines on the working state, which is all a later task
        sees of the answers before it; none where none laid any."""
        laid = [s for s in self.settled if s.lines.strip()]
        return laid[-1].keys[-1] if laid else ""

    @property
    def chain_0_1_0(self) -> str:
        """What the next task's key followed in 0.1.0: the key settled last."""
        return self.settled[-1].keys_0_1_0[-1] if self.settled else ""

    @property
    def ids(self) -> set[str]:
        return {s.task.id for s in self.settled}

    def settle(
        self,
        task: Task,
        keys: Sequence[str],
        lines: str,
        keys_0_1_0: Sequence[str],
    ) -> None:
        if lines.strip() and self.apply is not None:
            self.sheet = self.apply(self.sheet, lines)
        elif lines.strip():
            fragment = edits.parse_fragment(lines)
            self.sheet = edits.applied_fragment(
                self.sheet, fragment, restating=self.restating
            )
            if self.reads is not None:
                self.reads.settle(fragment.lines)
        self.settled.append(Settled(task, tuple(keys), lines, tuple(keys_0_1_0)))


def anchor_of(workflow: Workflow) -> str:
    """What a workflow's answers' keys bind: its kept state's id, or, on a
    question, the question's key."""
    if workflow.view is not None:
        return workflow.view.state.id
    anchor: str | None = getattr(workflow, "anchor", None)
    if anchor is None:
        raise QueueError("a workflow with no kept state names its question (anchor)")
    return anchor


def task_key(
    step: Step,
    at: str,
    served: str,
    after: str,
    steps: Sequence[str] = (),
    round_id: str | None = None,
) -> str:
    """A task's answer's key: its step's kind, stage, template and version,
    the kinds of its workflow's steps (an answer a later step settled is
    not one that settles alone), the kept state, the task as `served`, the
    key it follows (`Working.chain`), and its review round where it is one's
    (`Working.round`)."""
    given = {"round": round_id} if round_id is not None else {}
    return hashlib.sha256(
        json.dumps(
            {
                **given,
                "kind": step.kind,
                "stage": step.stage.value,
                "template": step.template.name,
                "version": step.template.version,
                "steps": list(steps),
                "state": at,
                "task": served,
                "after": after,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class Call:
    """An answer as a session gave it: its tool and arguments. Recorded as
    its answer's `call`."""

    tool: str
    args: Mapping[str, Any]

    @property
    def raw(self) -> str:
        return json.dumps(
            {"tool": self.tool, "args": dict(self.args)},
            ensure_ascii=False,
            sort_keys=True,
        )

    @classmethod
    def read(cls, raw: str) -> Call:
        data = json.loads(raw)
        return cls(data["tool"], data["args"])


@dataclass(frozen=True, slots=True)
class Position:
    """Where a run stands: the task open and the step it is at, as served,
    and its key; `task` None once every task is settled."""

    task: Task | None
    step: int = 0
    served: str = ""
    key: str = ""


@dataclass(frozen=True, slots=True)
class Used:
    """An answer a replay took: its key, step and task as served, and its
    call. `settled` where its task settled; the answer that settled it
    carries the judgments filed with it."""

    key: str
    step: int
    task: Task
    served: str
    call: Call
    settled: bool
    judgments: JudgmentRecords = JudgmentRecords()


def _nearest(open_: Sequence[Task], last: Task | None, reads: Reads) -> Task:
    """The task served next: the nearest on the graph to the task settled
    last, within `ORDER_HOPS`; else the first in the paper's order."""
    if last is None:
        return open_[0]
    found = reads.distances(last.block, ORDER_HOPS)
    near = [t for t in open_ if t.block in found]
    if not near:
        return open_[0]
    return min(near, key=lambda t: (found[t.block], open_.index(t)))


def takes(step: Step, task: Task) -> bool:
    """Whether `step` answers `task`: every task, where it gives no
    `takes`."""
    given: Callable[[Task], bool] | None = getattr(step, "takes", None)
    return True if given is None else given(task)


def advance(
    workflow: Workflow, working: Working, known: Mapping[str, Call]
) -> tuple[Position, list[Used]]:
    """The run carried on from `working` by the answers `known` holds, by
    key: each task served at the working state, each step's answer found
    by its key and taken, a settled task laid over the working state.
    Returns where it stops, the first answer not known, and the answers
    taken, in order."""
    used: list[Used] = []
    kinds = [s.kind for s in workflow.steps]
    while True:
        done = working.ids
        open_ = [t for t in workflow.draw(working) if t.id not in done]
        if not open_:
            return Position(None), used
        last = working.settled[-1].task if working.settled else None
        task = (
            _nearest(open_, last, working.reads)
            if workflow.by_graph and working.reads is not None
            else open_[0]
        )
        if not takes(workflow.steps[-1], task):
            raise QueueError(f"task {task.id}: the last step takes every task")
        head = f"task {task.id} ({len(done) + 1} of {len(done) + len(open_)})\n"
        handed, keys, keys_0_1_0, taken = task, [], [], []
        for n, step in enumerate(workflow.steps):
            last_step = n == len(workflow.steps) - 1
            if not last_step and not takes(step, handed):
                continue
            rendered = step.render(handed, working)
            served = head + rendered
            key = task_key(
                step,
                working.at,
                f"task {task.id}\n{rendered}",
                working.chain,
                kinds,
                working.round,
            )
            old = task_key(step, working.at, served, working.chain_0_1_0, kinds)
            found_key = key if key in known else old
            call = known.get(found_key)
            if call is None:
                used += taken
                return Position(handed, n, served, key), used
            found = step.answer(handed, call.tool, call.args, working, check=False)
            if isinstance(found, Refused):
                raise QueueError(
                    f"task {task.id}'s recorded answer {found_key[:12]} reads no more: "
                    + "; ".join(found.problems)
                )
            # what the next task follows is the key its answer is stored
            # under: one key, one answer
            keys.append(found_key)
            keys_0_1_0.append(old)
            if found.hand_on is None or last_step:
                used += [dataclasses.replace(u, settled=True) for u in taken]
                used.append(
                    Used(found_key, n, handed, served, call, True, found.judgments)
                )
                working.settle(task, keys, found.lines, keys_0_1_0)
                break
            taken.append(Used(found_key, n, handed, served, call, False))
            handed = Task(task.id, task.block, {**task.subject, **found.hand_on})


# --- the spool --------------------------------------------------------------------


class Spool:
    """A run's answers accepted, one JSON object a line, appended by each
    step's server under a lock: `answer` entries, and `ended` once a
    session ended."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextlib.contextmanager
    def _locked(self, mode: int) -> Iterator[Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle, mode)
            try:
                yield handle
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def append(self, entry: Mapping[str, Any]) -> None:
        with self._locked(fcntl.LOCK_EX) as handle:
            handle.write(json.dumps(dict(entry), ensure_ascii=False) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._locked(fcntl.LOCK_SH) as handle:
            handle.seek(0)
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def calls(self) -> dict[str, Call]:
        return {
            e["key"]: Call(e["tool"], e["args"])
            for e in self.entries()
            if e["type"] == "answer"
        }

    def sessions(self) -> dict[str, str]:
        """Each answer's session, by its key."""
        return {e["key"]: e["session"] for e in self.entries() if e["type"] == "answer"}

    def end(self, why: str) -> None:
        self.append({"type": "ended", "why": why, "time": time.time()})

    @property
    def ended(self) -> str | None:
        return next((e["why"] for e in self.entries() if e["type"] == "ended"), None)


# --- a run ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Asked:
    """What a run answers where no kept state holds it: a question, by its
    key (an update's session, asked before the update keeps NEW)."""

    key: str

    @property
    def id(self) -> str:
        return self.key


#: What a run stands on: the kept state its workflow reads, or the question
#: it answers.
Anchor = State | Asked


@dataclass(frozen=True, slots=True)
class Run:
    """A queue run: the workflow `factory` (`module:function`, a context
    manager taking the store, the anchor and `options`) on its anchor, a
    kept state or a question."""

    store: RecordStore
    at: Anchor
    factory: str
    options: Mapping[str, Any] = field(default_factory=dict)

    @property
    def state(self) -> State | None:
        """The kept state it stands on; None where it answers a question."""
        return self.at if isinstance(self.at, State) else None

    @property
    def key(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "factory": self.factory,
                    "options": dict(self.options),
                    "state": self.at.id,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @property
    def sessions(self) -> str:
        """What its sessions' ids begin with: the answers its tasks were
        given by bear them."""
        return f"{self.key[:8]}-"

    @property
    def directory(self) -> Path:
        """Where its spools and its sessions' server settings lie."""
        return self.store.sessions / f"queue-{self.key[:16]}"


@contextlib.contextmanager
def opened(run: Run) -> Iterator[Workflow]:
    module, _, name = run.factory.partition(":")
    factory: Callable[..., contextlib.AbstractContextManager[Workflow]] = getattr(
        importlib.import_module(module), name
    )
    with factory(run.store, run.at, run.options) as workflow:
        yield workflow


def recorded(store: RecordStore, workflow: Workflow) -> dict[str, Call]:
    """The answers the store holds for the workflow's steps at its state,
    by key; for a run on a question, every answer of its steps' kinds that
    names no state, its keys binding the question."""
    kinds = {s.kind for s in workflow.steps}
    state = None if workflow.view is None else workflow.view.state.id
    return {
        a.key: Call(a.call["tool"], a.call["args"])
        for a in store.authoritative().values()
        if a.kind in kinds and a.state == state and a.call is not None
    }


def tool_server(
    run: Run, step: int, session: str, spool: Path, wait: float = WAIT_SECONDS
) -> ToolServer:
    """The tool server a step's session is given: this package's
    `queue.server` on a settings file written beside the spool, run by
    this Python in isolated mode. Every path it is handed is absolute: the
    host runs it in a directory of its own, whatever form the paper was
    named in."""
    path = (run.directory / f"{session}.json").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "store": str(run.store.directory.resolve()),
                "state": run.at.id,
                "asked": isinstance(run.at, Asked),
                "factory": run.factory,
                "options": dict(run.options),
                "step": step,
                "session": session,
                "spool": str(spool.resolve()),
                "wait_seconds": wait,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k.startswith("AXIOMATION_")}
    # a methods session runs the paper's scripts here, by their runner
    # (`uv run python`): found on the path, as in the shell; the person's
    # user file is found from the home or `XDG_CONFIG_HOME`
    for name in ("HOME", "PATH", "XDG_CONFIG_HOME"):
        if name in os.environ:
            env[name] = os.environ[name]
    return ToolServer(
        SERVER_NAME,
        (sys.executable, "-I", "-m", "axiomation.queue.server", str(path)),
        env,
        wait + TOOL_LIMIT_MARGIN,
    )


#: How long a tool server may take to list its tools: it opens its
#: workflow first, which reads the kept state and the paper.
SERVER_START_SECONDS = 300.0


def started(server: ToolServer, seconds: float = SERVER_START_SECONDS) -> str | None:
    """Why `server` does not start, else None: started as a host starts it,
    from an empty directory of its own with its own environment, and asked
    to begin and to list its tools. Its last error line where it lists
    none."""
    begin = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + "\n"
    )
    with tempfile.TemporaryDirectory(prefix="axiomation-server-") as directory:
        try:
            ran = subprocess.run(
                server.argv,
                input=begin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=directory,
                env=dict(server.env),
                timeout=seconds,
                check=False,
            )
            out, err, code = ran.stdout, ran.stderr, str(ran.returncode)
        except subprocess.TimeoutExpired as late:
            # a server that listed its tools and then hung still started
            out, err = _text(late.stdout), _text(late.stderr)
            code = f"no end within {seconds:g} s"
        except OSError as error:
            return str(error)
    if _lists_tools(out):
        return None
    errors = [line.strip() for line in err.splitlines() if line.strip()]
    return errors[-1] if errors else f"it listed no tools ({code})"


def _text(given: str | bytes | None) -> str:
    if isinstance(given, bytes):
        return given.decode("utf-8", "replace")
    return given or ""


def _lists_tools(out: str) -> bool:
    """Whether a server's output holds its answer to `tools/list`."""
    for line in out.splitlines():
        try:
            reply = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(reply, dict)
            and reply.get("id") == 2
            and isinstance(reply.get("result"), dict)
            and "tools" in reply["result"]
        ):
            return True
    return False


def queue_session(
    step: Step,
    server: ToolServer,
    context_tokens: int | None,
    files: Mapping[str, str] | None = None,
) -> QueueSession:
    return QueueSession(
        step.kickoff,
        step.template.render(),
        server,
        context_tokens,
        dict(files or {}),
        bool(getattr(step, "web", False)),
    )


# --- a step's session served ------------------------------------------------------

_H = {
    "type": "string",
    "description": "a block's or group's handle, as the task and the reads print it",
}


def _on_handle(name: str, description: str) -> Tool:
    return Tool(
        name,
        description,
        {"type": "object", "required": ["handle"], "properties": {"handle": _H}},
    )


NEXT = Tool("next", "Serve your task.", {"type": "object", "properties": {}})
READS = (
    _on_handle("block", "A block's text, or a group's members."),
    Tool(
        "view",
        "100 lines of a paper file from a line, with the blocks they hold.",
        {
            "type": "object",
            "required": ["file", "line"],
            "properties": {"file": {"type": "string"}, "line": {"type": "integer"}},
        },
    ),
    Tool(
        "search",
        "A case-insensitive regular expression over the paper's lines: at most "
        "50 hits, each its line, block and a snippet.",
        {
            "type": "object",
            "required": ["pattern"],
            "properties": {"pattern": {"type": "string"}},
        },
    ),
    _on_handle("edges", "A node's edges in and out."),
    _on_handle("hops", "The nodes within two hops, nearest first."),
    _on_handle("standing", "A claim's standing and the rulings on it."),
    _on_handle("relied", "What uses a node."),
    _on_handle("group", "A group's members, or a block's groups."),
    _on_handle("cited", "The works a block cites."),
)


class Served:
    """A step's session served: its calls answered at the working state,
    which the spool carries on as either step's answers are accepted."""

    def __init__(
        self,
        workflow: Workflow,
        known: Mapping[str, Call],
        spool: Spool,
        step: int,
        session: str,
        wait: float = WAIT_SECONDS,
        poll: float = POLL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.workflow = workflow
        self.known = dict(known)
        self.spool = spool
        self.step = step
        self.session = session
        self.wait = wait
        self.poll = poll
        self.clock = clock
        self.sleep = sleep
        self.working = Working(workflow)
        self.position = Position(None)
        #: the spool's entries the position was last carried on by
        self._seen = -1
        #: the answers this session gave that were accepted
        self.accepted = 0

    @property
    def core_reads(self) -> tuple[Tool, ...]:
        """The core's reads this session is served: those its workflow
        neither withholds nor gives in place of its own."""
        own = {r.tool.name for r in self.workflow.reads}
        withheld = self.workflow.serving.withheld
        if self.working.reads is None:
            return ()
        return tuple(t for t in READS if t.name not in own | withheld)

    @property
    def tools(self) -> tuple[Tool, ...]:
        return (
            NEXT,
            *self.workflow.steps[self.step].tools,
            *self.core_reads,
            *(r.tool for r in self.workflow.reads),
        )

    @property
    def share_done(self) -> bool:
        share = self.workflow.serving.session_tasks
        return share is not None and self.accepted >= share

    def refresh(self) -> Position:
        return self._read()[0]

    def _read(self) -> tuple[Position, str | None]:
        """Where the run stands, carried on where the spool grew, and why
        it ended, if it did."""
        entries = self.spool.entries()
        ended = next((e["why"] for e in entries if e["type"] == "ended"), None)
        if len(entries) != self._seen:
            self._seen = len(entries)
            calls = {
                e["key"]: Call(e["tool"], e["args"])
                for e in entries
                if e["type"] == "answer"
            }
            self.position, _ = advance(
                self.workflow, self.working, {**self.known, **calls}
            )
        return self.position, ended

    def call(self, name: str, args: Mapping[str, Any]) -> tuple[str | Image, bool]:
        """`name`'s answer to the session, and whether it was refused."""
        if name == NEXT.name:
            return self.next(), False
        if name in {t.name for t in self.workflow.steps[self.step].tools}:
            return self.answer(name, args)
        added = next((r for r in self.workflow.reads if r.tool.name == name), None)
        if added is not None:
            try:
                return added.answer(self.working, args), False
            except (ReadError, KeyError, ValueError) as error:
                return f"refused: {error}", True
        if name in {t.name for t in self.core_reads}:
            return self.read(name, args)
        return f"refused: no tool {name!r}", True

    def next(self) -> str:
        deadline = self.clock() + self.wait
        while True:
            position, ended = self._read()
            if ended is not None:
                return ENDED
            if position.task is None:
                return EMPTY
            if position.step == self.step:
                # a share ends where a new task would be served: the other
                # step settles the one this step answered last
                return SHARE_DONE if self.share_done else position.served
            if self.clock() >= deadline:
                return AGAIN
            self.sleep(self.poll)

    def answer(self, name: str, args: Mapping[str, Any]) -> tuple[str, bool]:
        position, ended = self._read()
        if ended is not None:
            return ENDED, False
        if position.task is None:
            return EMPTY, False
        if position.step != self.step:
            return "refused: no task of yours is open; call `next`", True
        task = position.task
        if args.get("task") != task.id:
            return (
                f"refused: your open task is {task.id}, not {args.get('task')!r}",
                True,
            )
        step = self.workflow.steps[self.step]
        found = step.answer(task, name, args, self.working, check=True)
        if isinstance(found, Refused):
            listed = "\n".join(f"- {p}" for p in found.problems)
            return f"refused, nothing filed; fix and answer again:\n{listed}", True
        self.spool.append(
            {
                "type": "answer",
                "key": position.key,
                "step": self.step,
                "task": task.id,
                "session": self.session,
                "tool": name,
                "args": dict(args),
                "time": time.time(),
            }
        )
        self.accepted += 1
        return f"accepted: task {task.id}.\n\n{self.next()}", False

    def read(self, name: str, args: Mapping[str, Any]) -> tuple[str, bool]:
        reads = self.working.reads
        if reads is None:
            return f"refused: no read {name} here", True
        try:
            if name == "view":
                return reads.view_lines(str(args["file"]), int(args["line"])), False
            if name == "search":
                return reads.search(str(args["pattern"])), False
            return getattr(reads, name)(str(args["handle"])), False
        except (ReadError, KeyError, ValueError) as error:
            return f"refused: {error}", True
