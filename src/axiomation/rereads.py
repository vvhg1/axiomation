"""A reread asked of an agent: the stale-marked owners' lines read again on a
kept state, after the update that marked them.

A mark doubts one line, and the question that draws lines answers it
(`docs/architecture.md` § 9). The reread is a queue (docs/architecture.md § 2): a
task an owner of an open mark, served with its lines as the working
state's sheet holds them, numbered, its marks, and what changed at each
marked line's far end, its words before the mark and now. One step, the
edges agent's, answers each owner:

- **`stands`**, with why: its lines hold. Filed with the answer as a
  reread (`records.Reread`), which clears every mark on the owner made at
  or before the state;
- **`changed`**, with why, the lines it takes off by their number and the
  lines it puts on in the sheet's notation: code writes them under the
  owner, checked at once on the working state as the edges edit would be.

A complete run ends with one edges edit, the reread's (`runner.given`),
holding every line changed, shown the marks: a new state that re-reads
the owners it changed, so their marks clear there and the readings
resting on a changed line lapse.

Asked of the owners named, else of every owner of an open mark, a gate
mark's first, each in the paper's order. A mark this run's own reread
cleared still draws its owner, so a resumed run draws what it drew.
`reread --ask` asks it, and an update at its end on the state it kept.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from axiomation import edits, judgments, queries, queue, versions
from axiomation.changes import Layer
from axiomation.driver import ReviewSettings
from axiomation.marks import Mark, folded
from axiomation.profiles import run_settings
from axiomation.questions import Stage, Template, edges_file, load_template
from axiomation.questions import edge_sheet as es
from axiomation.queue import Accepted, Refused, Task, Tool, Working
from axiomation.records import JudgmentRecords, RecordStore, Reread
from axiomation.runner import Runner, given, queued
from axiomation.runner.hosts import Host
from axiomation.versions import State

TEMPLATE = "edges-reread"
KIND = "edges-reread"
#: The workflow a queue run opens (`queue.Run.factory`).
FACTORY = "axiomation.rereads:rereading"
KICKOFF = queue.KICKOFF
#: What a reread costs where the store holds none of its sessions: on
#: Opus at effort high, about $1.33 for a session of 59 owners, its first
#: call $0.06 (the brief written once), the rest over the owners.
FITTED = queued.Fitted("claude-opus-5-5", start=0.06, task=0.022)


class RereadError(ValueError):
    """Owners that cannot be asked, with why."""


@dataclass(frozen=True, slots=True)
class Owner:
    """An owner asked: its kept handle, its node and its open marks."""

    handle: str
    node: str
    marks: tuple[Mark, ...]


def owners(
    view: queries.View, named: Sequence[str] = (), uncounted: Collection[str] = ()
) -> list[Owner]:
    """The owners to ask: those named (a handle, node id, block id or a
    handle's prefix), else every owner of an open mark, a gate mark's
    owners first, each in the paper's order. The marks are those open as
    if no reread filed with an answer in `uncounted` were filed, so a run
    draws its owners as it first drew them. Refused where a named one
    holds no open mark."""
    marks = (
        folded(view.store, view.state, view.readings, view.reader, uncounted)[0]
        if uncounted
        else view.marks
    )
    by_node: dict[str, list[Mark]] = {}
    for m in marks:
        by_node.setdefault(m.node, []).append(m)
    if named:
        nodes = [view.nodes[view.resolve(ref)] for ref in named]
        unmarked = [view.handle(n) for n in nodes if n not in by_node]
        if unmarked:
            raise RereadError(
                f"{', '.join(unmarked)} holds no open stale mark at state "
                f"{view.state.id}: `axiomation open --kind stale` lists them"
            )
    else:
        at = view.readings.at
        nodes = sorted(
            by_node,
            key=lambda n: (
                not any(m.gate for m in by_node[n]),
                view.order(at.ident_of.get(n)),
                n,
            ),
        )
    return [
        Owner(by_node[n][0].handle, n, tuple(by_node[n])) for n in dict.fromkeys(nodes)
    ]


# --- what changed at a marked line's far end ---------------------------------------


class _Changes:
    """The words of the blocks at marked lines' far ends, before each mark
    and now, read once a state."""

    def __init__(self, view: queries.View) -> None:
        self.view = view
        self.chain = versions.chain(view.store, view.state)
        self.index = {s.id: n for n, s in enumerate(self.chain)}
        self.seen: dict[str, judgments.Seen] = {}

    def _at(self, state: State) -> judgments.Seen:
        if state.id not in self.seen:
            self.seen[state.id] = self.view.reader.seen(state)
        return self.seen[state.id]

    def of(self, m: Mark) -> list[str]:
        """What changed at `m`'s line's far end: its words before the update
        that marked it and now; and where they stand, the words of the
        block the edit began at, where those changed."""
        line = m.line
        if line is None:
            return ["(the line is no longer on the sheet)"]
        view = self.view
        now = view.readings.at
        far = line.target if line.owner == line.source else line.source
        n = self.index.get(m.since, 0)
        then = self._at(self.chain[n - 1]) if n else None
        found = [f"at its far end, {_named(view, far)}:", *_before_now(then, now, far)]
        # a far end whose words stand moved by the edit its cause began
        # at: that block's words, where they changed
        roots = dict.fromkeys(c["root"] for c in m.causes if c["root"])
        for root in roots:
            node = _node(view, root)
            if node is None or node == far or _stands(then, now, node):
                continue
            delta = m.edits.get(root)
            named = f"{root} (Δ {delta})" if delta else root
            found += [f"the edit it began at, {named}:", *_before_now(then, now, node)]
        return found

    def far_ends(self, owner: Owner) -> list[str]:
        """The blocks at the far ends of an owner's marked lines, by id."""
        at = self.view.readings.at
        found = []
        for m in owner.marks:
            if m.line is None:
                continue
            far = m.line.target if m.line.owner == m.line.source else m.line.source
            ident = at.ident_of.get(far)
            if ident is not None:
                found.append(ident)
        return list(dict.fromkeys(found))


def _named(view: queries.View, node: str) -> str:
    """A node by its handle at the state, or by its id where it has none
    there (a block the update deleted)."""
    found = view.readings.at.handle(node)
    return node if found == node else f"⟦{found}⟧"


def _node(view: queries.View, handle: str) -> str | None:
    try:
        return view.nodes[view.resolve(handle)]
    except (KeyError, queries.QueryError):
        return None


def _stands(then: judgments.Seen | None, now: judgments.Seen, node: str) -> bool:
    before = then.words_of.get(node) if then is not None else None
    return before == now.words_of.get(node)


def _before_now(
    then: judgments.Seen | None, now: judgments.Seen, node: str
) -> list[str]:
    """A block's words before the update and now, or once where they stand."""
    before = then.words_of.get(node) if then is not None else None
    after = now.words_of.get(node)
    if _stands(then, now, node):
        return [
            "its words did not change (its meaning moved with the edit the mark "
            "names):",
            _quoted(after, "(not in the paper)"),
        ]
    return [
        "before:",
        _quoted(before, "(not in the paper before)"),
        "now:",
        _quoted(after, "(not in the paper now)"),
    ]


def _quoted(words: str | None, absent: str) -> str:
    return absent if words is None else f"> {words}"


# --- the reread as a queue (docs/architecture.md § 2) ------------------------------

STANDS = Tool(
    "stands",
    "File the task's owner read: its lines read true of the paper as it is "
    "now. Accepted, it returns the next task.",
    {
        "type": "object",
        "required": ["task", "why"],
        "properties": {
            "task": {"type": "string"},
            "why": {
                "type": "string",
                "description": "what the far end says now, and why the lines hold",
            },
        },
    },
)
CHANGED = Tool(
    "changed",
    "File the task's owner changed: the lines you take off, by their number, "
    "and the lines you put on, in the sheet's notation; checked at once. To "
    "correct a line, take it off and put the corrected one on. Accepted, it "
    "returns the next task.",
    {
        "type": "object",
        "required": ["task", "why"],
        "properties": {
            "task": {"type": "string"},
            "why": {"type": "string"},
            "take_off": {
                "type": "array",
                "items": {"type": "string"},
                "description": "the numbers of the owner's lines that no longer "
                "read true, as the task numbers them (L1, L2, …)",
            },
            "put_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "each line to put on the owner, `→ …` or `⊢ …`, as "
                "the sheet writes it",
            },
        },
    },
)
TOOLS = (STANDS, CHANGED)


def _judged_lines(sheet: str, owner: str) -> list[str]:
    """An owner's edge and standing lines, what its reread judges, as `sheet`
    holds them."""
    return [
        line
        for line in edges_file.written_lines(sheet).get(owner, [])
        if es.EDGE.match(line) or es.SUPPORT.match(line)
    ]


class Rereading:
    """The reread on a kept state as a queue: a task an owner asked; one
    step. Its lines are the edges writer's, an about line a description
    unless it names what it restates."""

    restating = False
    reads: tuple[queue.Read, ...] = ()
    by_graph = True
    serving = queue.Serving()

    def __init__(
        self,
        kept: edits.Kept,
        view: queries.View,
        asked: Sequence[Owner],
        author: edits.Author,
        writer: Template,
    ) -> None:
        self.kept = kept
        self.view = view
        self.files = kept.directory
        self.sheet = edits.shown(kept, Layer.EDGES)
        self.asked = tuple(asked)
        self.author = author
        self.changes = _Changes(view)
        self.steps = (Owners(self, writer),)
        self._by_task = {f"o{n}": o for n, o in enumerate(self.asked, 1)}

    def draw(self, working: Working) -> list[Task]:
        at = self.view.readings.at
        return [
            Task(
                task,
                at.ident_of.get(o.node, o.node),
                {"owner": o.handle, "node": o.node},
            )
            for task, o in self._by_task.items()
        ]

    def owner(self, task: Task) -> Owner:
        """The task's owner, as it was asked."""
        return self._by_task[task.id]

    def render(self, task: Task, working: Working) -> str:
        """An owner as served: its place and text, its lines numbered as the
        working sheet holds them, its marks, what changed at each marked
        line's far end, and the far ends as pointers."""
        o = self.owner(task)
        reads = working.reads
        lines = ["do this owner's lines still read true?"]
        if task.block in self.view.groups:
            # a group: its blocks by handle, each read on demand
            lines.append(reads.block(reads.handle(task.block)))
        elif task.block in self.view.blocks:
            lines += [f"block {reads.place(task.block)}", self.view.texts[task.block]]
        else:
            lines.append(f"owner ⟦{o.handle}⟧")
        lines += [
            "its lines:",
            *(
                f"L{n} {line}"
                for n, line in enumerate(_judged_lines(working.sheet, o.handle), 1)
            ),
        ]
        for m in o.marks:
            lines += [m.sheet_line(), *self.changes.of(m)]
        far = self.changes.far_ends(o)
        if far:
            lines.append(
                "pointers: "
                + ", ".join(f"⟦{reads.handle(p)}⟧ {reads.section(p)}" for p in far)
            )
        return "\n".join(lines) + "\n"

    def answered(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        """`tool`'s answer on the working state: a `stands` filed as a
        reread; a `changed` as its lines under the owner, checked by the
        gate where `check`."""
        why = " ".join(str(args.get("why") or "").split())
        if not why:
            return Refused(("give the reason (why)",))
        o = self.owner(task)
        if tool == STANDS.name:
            reread = Reread("", None, self.view.state.id, o.node, why)
            return Accepted(judgments=JudgmentRecords(rereads=(reread,)))
        try:
            lines = _changed_lines(working.sheet, o.handle, args)
        except RereadError as error:
            return Refused((str(error),))
        if not check:
            return Accepted(lines)
        problems = _line_problems(self.kept, lines, self.author, working.sheet)
        return Refused(tuple(problems)) if problems else Accepted(lines)


def _changed_lines(sheet: str, owner: str, args: Mapping[str, Any]) -> str:
    """A `changed` answer's fragment under `owner`: each line taken off by
    its number, as `sheet` holds it, then each line put on."""
    take_off = [str(x).strip() for x in args.get("take_off") or []]
    put_on = [" ".join(str(x).split()) for x in args.get("put_on") or []]
    if not take_off and not put_on:
        raise RereadError(
            "a changed owner takes a line off or puts one on: give take_off or "
            "put_on, or answer `stands`"
        )
    own = _judged_lines(sheet, owner)
    taken = []
    for ref in take_off:
        n = ref.upper().removeprefix("L")
        if not n.isdigit() or not 1 <= int(n) <= len(own):
            raise RereadError(
                f"take_off {ref!r}: the owner's lines are L1 to L{len(own)}"
            )
        taken.append(own[int(n) - 1])
    for line in put_on:
        if not (es.EDGE.match(line) or es.SUPPORT.match(line)):
            raise RereadError(
                f"put_on {line!r}: an edge line `→ …` or a standing `⊢ …`, as the "
                "sheet writes it"
            )
    body = [f"{edits.RETRACT} {x}" for x in dict.fromkeys(taken)]
    body += [f"{edits.ADD} {x}" for x in put_on]
    return "\n".join([f"⟦{owner}⟧", *body])


def _line_problems(
    kept: edits.Kept, lines: str, author: edits.Author, sheet_text: str
) -> list[str]:
    """What the reread's gate refuses in `lines` on `sheet_text`, the working
    state's sheet: a fragment that does not apply, then the edit as
    `author`'s edges edit would be checked."""
    try:
        text = edits.applied_fragment(sheet_text, edits.parse_fragment(lines))
    except edits.EditError as error:
        return [str(error)]
    return list(edits.edit_problems(kept, Layer.EDGES, text, author))


class Owners:
    """The reread's one step: an owner served, answered `stands` or
    `changed`."""

    kind = KIND
    stage = Stage.EDGES
    kickoff = KICKOFF
    tools = TOOLS

    def __init__(self, rereading: Rereading, writer: Template) -> None:
        self.rereading = rereading
        self.template = brief(writer)

    def render(self, task: Task, working: Working) -> str:
        return self.rereading.render(task, working)

    def answer(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        return self.rereading.answered(task, tool, args, working, check)


def brief(writer: Template) -> Template:
    """The step's brief: the edges writer's (the lines' notation), then the
    reread's; one text, so its version binds both."""
    own = load_template(TEMPLATE)
    return Template(TEMPLATE, f"{writer.text}\n\n{own.text}")


@contextmanager
def rereading(
    store: RecordStore, state: State, options: Mapping[str, Any]
) -> Iterator[Rereading]:
    """The reread's queue on `state` (`queue.Run.factory`): the owners
    `options` names (`owners`), else every owner of an open mark, a mark
    this run's own sessions' rereads cleared counted open; its kept state
    opened under the profile `options` names (`profile`, from the file
    `profiles` where given); its lines checked as `model`'s."""
    view = queries.View(store, state)
    settings = run_settings(options)
    mine = queue.Run(store, state, FACTORY, options).sessions
    own = {
        a.key
        for a in store.answers()
        if a.kind == KIND and a.state == state.id and (a.session or "").startswith(mine)
    }
    asked = owners(view, options.get("owners") or (), own)
    author = edits.agent(str(options.get("model") or "agent"), None)
    writer = (settings or ReviewSettings()).templates.edges
    with edits.opened(store, state, settings, view.marks) as kept:
        yield Rereading(kept, view, asked, author, writer)


# --- a reread run ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Said:
    """An owner a reread settled: its lines stand or changed, and why."""

    owner: Owner
    stands: bool
    why: str


@dataclass(frozen=True, slots=True)
class Done:
    """A reread's run (`runner.given.Outcome`), each owner it settled read."""

    outcome: given.Outcome
    said: tuple[Said, ...]

    @property
    def complete(self) -> bool:
        return self.outcome.complete


def ask(
    run: queue.Run,
    model: str,
    runner: Runner | None = None,
    hosts: Sequence[Host] = (),
    context_tokens: int | None = None,
    wait_seconds: float = queue.WAIT_SECONDS,
) -> Done:
    """`run`, the reread's queue, asked of `hosts` through `runner` (None:
    what the store holds replayed); complete, the lines it changed given as
    `model`'s edges edit, shown the marks (`runner.given.ask`). Refused
    before it is asked where the state takes no edit."""
    try:
        out = given.ask(
            run, model, _reason, True, runner, hosts, None, context_tokens, wait_seconds
        )
    except given.NotAsked as error:
        raise RereadError(str(error)) from None
    with queue.opened(run) as rereading:
        assert isinstance(rereading, Rereading)
        said = tuple(_said(rereading, u) for u in out.settled)
    return Done(out, said)


def _said(rereading: Rereading, u: queue.Used) -> Said:
    return Said(
        rereading.owner(u.task),
        u.call.tool == STANDS.name,
        " ".join(str(u.call.args.get("why") or "").split()),
    )


def _reason(rereading: queue.Workflow, settled: Sequence[queue.Used]) -> str:
    """The edit's reason: each changed owner's, by its handle."""
    assert isinstance(rereading, Rereading)
    return "; ".join(
        f"{s.owner.handle}: {s.why}"
        for s in (_said(rereading, u) for u in settled)
        if not s.stands
    )


def price(store: RecordStore, model: str) -> queued.Price:
    """What a reread is expected to cost on `model`: its session's start and
    an owner's (`runner.queued.price`)."""
    try:
        return queued.price(store, TEMPLATE, model, FITTED)
    except queued.PriceError as error:
        raise RereadError(str(error)) from None
