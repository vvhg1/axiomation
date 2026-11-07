"""A claim's support searched by agents: whether the paper argues a claim
standing `unsupported` after all, before anyone rules on it.

A claim stands `unsupported` where no line the edges stage wrote argues
it; the stage reads each block, so an argument given in another block
(a section the claim announces or sums up) can be missed. The search is a
queue (docs/architecture.md § 2): a task a claim standing `unsupported`
that no live search read, served while it stands so on the working state
(`docs/model.md` § 8).

- **The worker's step** answers each claim argued, its line written by
  code from the typed answer (another block's justifies line onto the
  claim, the claim's block's self line, or its about line onto a claim the
  paper states) and checked by this module's gate, or unargued, naming
  the closest block it read.
- **The settling step**, where the profile names one, confirms the
  worker's line, served with the block it names read whole (a proof's
  group), or answers the claim itself, argued with its own line or
  unargued. Where the worker gave no line, it searches as the worker did,
  never told what the worker said.

A line settled stands the claim argued on the working state. A line onto
a claim whose block another line of the same source and kind already
argues is that line given back naming both (one edge per source, kind and
target). A complete run ends with one edges edit, the search's
(`edits.give`), holding every line settled: a new state. A claim the last
step read unargued is filed with that answer as a search
(`records.Searched`). It speaks while the claim stands `unsupported`, and
lapses as a ruling does, when the claim's words change or end. The
argument review asks a rule line only of a claim a live search read.

`support --ask` runs it, and a build or an update at its end, after the
reread and before the circles question.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from axiomation import edits, judgments, queries, queue
from axiomation.changes import Layer
from axiomation.judgments import Author, Reader, Walked, kept_reader
from axiomation.profiles import run_settings
from axiomation.questions import Stage, edges_file, load_template
from axiomation.questions import edge_sheet as es
from axiomation.queue import Accepted, Refused, Task, Tool, Working
from axiomation.records import (
    EdgeKind,
    JudgmentRecords,
    RecordStore,
    Searched,
    Status,
    Subject,
)
from axiomation.runner import Runner, given, queued
from axiomation.runner.hosts import Host
from axiomation.versions import State

#: Each step's kind and brief: the worker answers a claim at a time, argued
#: with its line or unargued with the closest block read; the settling step
#: confirms the worker's line or answers the claim itself.
WORKER_KIND = "support-worker"
WORKER_TEMPLATE = "support-worker"
SETTLER_KIND = "support-settle"
SETTLER_TEMPLATE = "support-settle"
#: The workflow a queue run opens (`queue.Run.factory`).
FACTORY = "axiomation.support_search:searching"
QUEUE_KICKOFF = queue.KICKOFF
#: The kinds of line an argued answer gives: each argues its claim.
KINDS = (EdgeKind.JUSTIFIES.value, es.SELF, EdgeKind.ABOUT.value)
#: The most of a named block's text a confirm task serves; `block` reads a
#: member whole.
NAMED_CHARS = 6000
#: What a step costs where the store holds none of its sessions, by the
#: model it was fitted on (the worker, Haiku at effort low, its first call
#: about $0.001, $0.0014 a claim; the settling step, Opus at effort low, its
#: first call about $0.05, $0.020 a claim, confirming most and searching the
#: rest again). Another model's is scaled by its input rate.
FITTED = {
    WORKER_KIND: queued.Fitted("claude-haiku-5-5", start=0.001, task=0.0014),
    SETTLER_KIND: queued.Fitted("claude-opus-5-5", start=0.05, task=0.020),
}


class SearchError(ValueError):
    """Claims that cannot be asked, or an answer that cannot be given."""


# --- the searches at a state ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchRead:
    """A search read at a kept state: the record, its author, and its
    claim walked there."""

    record: Searched
    author: Author
    walked: Walked

    @property
    def live(self) -> bool:
        return not self.walked.lapsed


def claim_key(on: Subject) -> tuple[str, str]:
    """A claim as the searches are keyed: its node and its words, their
    whitespace as one space."""
    return on.node, es.norm(on.words or "")


def searched(
    store: RecordStore, kept: State, reader: Reader | None = None
) -> dict[tuple[str, str], SearchRead]:
    """Each claim's latest live search bearing on `kept` (made on it or on
    a state along its parents), by the claim as it reads there
    (`claim_key`). A search lapses when its claim ends or its words
    change: what it searched for is that claim."""
    held = store.judgment_records().searches
    if not held:
        return {}
    reader = reader or kept_reader(store)
    answers = store.authoritative()
    found: dict[tuple[str, str], SearchRead] = {}
    for r in held:
        walked = judgments.walk(
            reader,
            r.state,
            judgments.NO_HOP,
            [r.subject.node],
            (),
            kept,
            r.subject.words,
        )
        if walked is None or walked.lapsed:
            continue
        if walked.words is not None and es.norm(walked.words) != es.norm(
            r.subject.words or ""
        ):
            continue
        node = walked.nodes[0] or r.subject.node
        on = Subject(node, None, walked.words or r.subject.words)
        found[claim_key(on)] = SearchRead(
            r, judgments.author_of(answers, r.answer), walked
        )
    reader.keep()
    return found


@dataclass(frozen=True, slots=True)
class Claim:
    """A claim asked: its number among those asked, its subject, its
    block's handle, its handle where its block holds several claims, its
    section and its words."""

    n: int
    subject: Subject
    block: str
    handle: str | None
    section: str | None

    @property
    def words(self) -> str:
        return self.subject.words or ""


def standing_unsupported(view: queries.View) -> list[Subject]:
    """The claims standing `unsupported` at the view's state, in the
    sheet's order."""
    at = view.readings.at
    return [
        Subject(node, None, words)
        for (node, words), s in zip(
            at.standings, at.export.get("standings", []), strict=True
        )
        if s["status"] == Status.UNSUPPORTED.value
    ]


def asked_claims(
    view: queries.View,
    named: Sequence[str] = (),
    every: bool = False,
    uncounted: Collection[str] = (),
) -> list[Claim]:
    """The claims to ask at the view's state: those standing `unsupported`
    that no live search read (`every`: each standing `unsupported`), a
    search filed with an answer `uncounted` names not counted, of the
    blocks named where any are (a handle, node id, block id or a handle's
    prefix), in the paper's order. Refused where a named block holds no
    claim standing `unsupported`."""
    at = view.readings.at
    standing = standing_unsupported(view)
    if named:
        blocks = {view.resolve(ref) for ref in named}
        held = {at.ident_of.get(on.node) for on in standing}
        empty = [view.handle(b) for b in blocks if b not in held]
        if empty:
            raise SearchError(
                f"{', '.join(empty)} holds no claim standing unsupported at state "
                f"{view.state.id}: `axiomation support PAPER` lists them"
            )
        standing = [on for on in standing if at.ident_of.get(on.node) in blocks]
    if not every:
        live = {
            k
            for k, r in searched(view.store, view.state, view.reader).items()
            if r.record.answer not in uncounted
        }
        standing = [on for on in standing if claim_key(on) not in live]
    found = []
    for n, on in enumerate(standing, 1):
        ident = at.ident_of[on.node]
        handles = view.claim_handles(ident)
        found.append(
            Claim(
                n,
                on,
                view.handle(ident),
                handles.get(on.words or "") if len(handles) > 1 else None,
                view.place(ident)["section"],
            )
        )
    return found


# --- the gate ---------------------------------------------------------------------


def _tokens(bracket: str | None) -> set[str]:
    return {t.strip() for t in (bracket or "").split(",") if t.strip()}


def _bare(words: str) -> str:
    """`words` as a quote is held against a claim: whitespace as one space,
    no closing punctuation."""
    return es.norm(words).rstrip(".;,:")


def arguing_problems(
    sheet: es.Sheet, c: Claim, lines: Sequence[tuple[str, str, str]]
) -> list[str]:
    """What an argued claim's lines do that is no search's (`docs/model.md`
    § 8): a line other than a justifies, self or about line, or a
    standing; a `=` line; a `+` line that argues no claim, or another
    claim than this one (`edges_file.argued_by`), a justifies line from
    the claim's own block, or a self line quoting the claim itself, not
    its step; a `-` line not given back, on the same owner, kind and
    target, naming at each end what it named; and lines that argue this
    claim nowhere."""
    where = "the claim"
    held = es.standings_by_handle(sheet, c.block)
    claim = next(
        (h for h, s in held.items() if es.norm(s.claim) == es.norm(c.words)), None
    )
    problems: list[str] = []
    added: dict[tuple[str, str, str], str] = {}
    taken: dict[tuple[str, str, str], str] = {}
    argues = False
    for owner, op, line in lines:
        said = f"{where}: ⟦{owner}⟧ {op} {line}"
        m = es.EDGE.match(line)
        if m is None or m.group("kind") not in KINDS:
            problems.append(
                f"{said}: a search gives a justifies, self or about line, and no "
                "standing: the line stands the claim argued"
            )
            continue
        if op == edits.KEEP:
            problems.append(f"{said}: a search keeps nothing: give `+` lines")
            continue
        same = (owner, m.group("kind"), m.group("target") or "")
        if op == edits.RETRACT:
            taken[same] = line
            continue
        added[same] = line
        if m.group("kind") == EdgeKind.JUSTIFIES and owner == c.block:
            problems.append(
                f"{said}: a justifies line comes from another block; the claim's "
                "own block gives its step as its self line"
            )
            continue
        if m.group("kind") == es.SELF and _bare(m.group("evidence")) == _bare(c.words):
            problems.append(
                f"{said}: its quote is the claim itself: quote the step that argues "
                "it, or its citation"
            )
            continue
        block, _, claims = edges_file.argued_by(sheet, owner, line, restating=True)
        if block == c.block and claim in claims:
            argues = True
        else:
            problems.append(
                f"{said}: argues no claim of ⟦{c.block}⟧"
                + (f" named [{claim}]" if len(held) > 1 else "")
                + ": a justifies line onto it, or its block's self or about line, "
                "naming it where its block holds several"
            )
    for same, old in taken.items():
        new = added.get(same)
        if new is None:
            problems.append(
                f"{where}: ⟦{same[0]}⟧ - {old}: a search takes no line off but to "
                "give it back naming this claim too"
            )
            continue
        o, n = es.EDGE.match(old), es.EDGE.match(new)
        assert o is not None and n is not None
        for group in ("mark", "flag", "evidence"):
            if (o.group(group) or "") != (n.group(group) or ""):
                problems.append(
                    f"{where}: ⟦{same[0]}⟧ + {new}: its {group} changed: a line "
                    "given back differs from the one taken off in its brackets "
                    "alone"
                )
        if es.RESTATES_NONE in _tokens(o.group("own")):
            problems.append(
                f"{where}: ⟦{same[0]}⟧ - {old}: its source end restates none "
                f"([{es.RESTATES_NONE}]), a reader's word: write another line, or "
                "answer unargued"
            )
        for end in ("parts", "own"):
            before = _tokens(o.group(end)) - {es.RESTATES_NONE}
            if before and not before <= _tokens(n.group(end)):
                problems.append(
                    f"{where}: ⟦{same[0]}⟧ + {new}: names less than the line it "
                    f"gives back named, [{', '.join(sorted(before))}]"
                )
    if not argues and not problems:
        problems.append(f"{where}: no line argues it")
    return problems


def _unsupported(sheet: es.Sheet) -> set[tuple[str, str]]:
    """The claims a sheet holds standing `unsupported`, by block and
    words."""
    return {
        (s.source, es.norm(s.claim))
        for s in sheet.supports
        if s.status == Status.UNSUPPORTED
    }


def line_problems(
    kept: edits.Kept, c: Claim, lines: str, author: edits.Author, sheet_text: str
) -> list[str]:
    """What the search's gate refuses in `lines`, a fragment arguing `c`,
    on `sheet_text` (the working state's sheet): a fragment that does not
    read, a part added, what no search's line does (`arguing_problems`),
    the claim left standing unsupported, another claim stood argued, then
    the edit as `author`'s edges edit would be checked."""
    try:
        fragment = edits.parse_fragment(lines)
    except edits.EditError as error:
        return [str(error)]
    if fragment.legend:
        return [f"{fragment.legend[0].strip()}: a search adds no part"]
    sheet = es.parse(sheet_text)
    problems = arguing_problems(sheet, c, fragment.lines)
    if problems:
        return problems
    try:
        # its brief writes an about line as a restatement
        text = edits.applied_fragment(sheet_text, fragment, restating=True)
    except edits.EditError as error:
        return [str(error)]
    after = _unsupported(es.parse(text))
    this = (c.block, es.norm(c.words))
    if this in after:
        problems.append("its line leaves the claim standing unsupported")
    # a line stands only the claim it is given for: another it names is
    # another task's
    problems += [
        f'⟦{block}⟧ "{claim}": the line stands it argued too: name only this '
        "claim, by its handle"
        for block, claim in sorted(_unsupported(sheet) - after - {this})
    ]
    return problems + list(edits.edit_problems(kept, Layer.EDGES, text, author))


# --- the search as a queue (docs/architecture.md § 2) -----------------------------

_HANDLE = {
    "type": "string",
    "description": "a block's or group's handle, as the task and the reads print it",
}
ANSWER_ARGUED = Tool(
    "answer_argued",
    "File the task argued: the line that argues the claim, checked at once. "
    "Accepted, it returns the next task.",
    {
        "type": "object",
        "required": ["task", "kind", "quote", "why"],
        "properties": {
            "task": {"type": "string"},
            "kind": {"type": "string", "enum": list(KINDS)},
            "by": {
                **_HANDLE,
                "description": "justifies: the block that argues it; about: the "
                "claim's block it restates; self: omit",
            },
            "quote": {
                "type": "string",
                "description": "the owner block's own words that give the step",
            },
            "target_claim": {
                "type": "string",
                "description": "about: the target's claim handle where its "
                "block holds several",
            },
            "why": {"type": "string"},
        },
    },
)
ANSWER_UNARGUED = Tool(
    "answer_unargued",
    "File the task unargued: the closest block you rejected and why it falls "
    "short. Accepted, it returns the next task.",
    {
        "type": "object",
        "required": ["task", "closest", "why"],
        "properties": {
            "task": {"type": "string"},
            "closest": _HANDLE,
            "why": {"type": "string"},
        },
    },
)
CONFIRM = Tool(
    "confirm",
    "Confirm the proposed line: it argues the claim. Accepted, it returns the "
    "next task.",
    {
        "type": "object",
        "required": ["task", "why"],
        "properties": {"task": {"type": "string"}, "why": {"type": "string"}},
    },
)
WORKER_TOOLS = (ANSWER_ARGUED, ANSWER_UNARGUED)
SETTLER_TOOLS = (CONFIRM, ANSWER_ARGUED, ANSWER_UNARGUED)
#: The tools whose answer settles a claim argued.
ARGUING_TOOLS = (CONFIRM.name, ANSWER_ARGUED.name)


class Searching:
    """The search on a kept state as a queue: a task a claim asked, served
    while it stands unsupported on the working state's sheet; the worker's
    step, then the settling step's where `settle`. Its briefs write an
    about line as a restatement."""

    restating = True
    reads: tuple[queue.Read, ...] = ()
    by_graph = True
    serving = queue.Serving()

    def __init__(
        self,
        kept: edits.Kept,
        view: queries.View,
        asked: Sequence[Claim],
        author: edits.Author,
        settle: bool,
    ) -> None:
        self.kept = kept
        self.view = view
        self.files = kept.directory
        self.sheet = edits.shown(kept, Layer.EDGES)
        self.asked = tuple(asked)
        self.author = author
        self.steps: tuple[Worker | Settler, ...] = (
            (Worker(self), Settler(self)) if settle else (Worker(self),)
        )
        self._by_task = {f"c{c.n}": c for c in self.asked}
        self._standing: tuple[str | None, set[tuple[str, str]]] = (None, set())

    def unsupported(self, sheet: str) -> set[tuple[str, str]]:
        """The claims `sheet` holds standing `unsupported`, parsed once a
        sheet."""
        if self._standing[0] is not sheet:
            self._standing = (sheet, _unsupported(es.parse(sheet)))
        return self._standing[1]

    def draw(self, working: Working) -> list[Task]:
        standing = self.unsupported(working.sheet)
        at = self.view.readings.at
        return [
            Task(
                f"c{c.n}",
                at.ident_of[c.subject.node],
                {
                    "node": c.subject.node,
                    "words": c.words,
                    "block": c.block,
                    "handle": c.handle,
                    "section": c.section,
                },
            )
            for c in self.asked
            if (c.block, es.norm(c.words)) in standing
        ]

    def claim(self, task: Task) -> Claim:
        """The task's claim, as it was asked."""
        return self._by_task[task.id]

    def answered(
        self, task: Task, tool: str, args: Mapping[str, Any], working: Working
    ) -> Accepted | Refused:
        """`tool`'s answer to `task` on the working state, a confirm's
        aside: an argued one's line by code (`_line`), handed on as proposed;
        an unargued one handed on with none, its search filed where it
        settles the claim. Refused where it gives no reason or names no
        block; the line is not checked here."""
        why = " ".join(str(args.get("why") or "").split())
        if not why:
            return Refused(("give the reason (why)",))
        claim = self.claim(task)
        if tool == ANSWER_UNARGUED.name:
            closest = str(args.get("closest") or "")
            if not closest:
                return Refused(
                    ("name the closest block you rejected (closest), and why",)
                )
            try:
                self.view.resolve(closest)
            except queries.QueryError as error:
                return Refused((f"closest {closest!r}: {error}",))
            searched = Searched("", None, self.view.state.id, claim.subject, why)
            return Accepted(
                hand_on={"proposed": None},
                judgments=JudgmentRecords(searches=(searched,)),
            )
        try:
            given = _line(self.view, working.sheet, claim, args)
        except SearchError as error:
            return Refused((str(error),))
        return Accepted(given["lines"], hand_on=given)

    def checked(
        self, task: Task, found: Accepted | Refused, working: Working, check: bool
    ) -> Accepted | Refused:
        """`found` with its line checked by the gate where `check`."""
        if not check or isinstance(found, Refused) or not found.lines:
            return found
        problems = line_problems(
            self.kept, self.claim(task), found.lines, self.author, working.sheet
        )
        return Refused(tuple(problems)) if problems else found


@contextmanager
def searching(
    store: RecordStore, state: State, options: Mapping[str, Any]
) -> Iterator[Searching]:
    """The search's queue on `state` (`queue.Run.factory`): the claims
    `asked_claims` gives for `options`' `blocks` and `every`, a search this
    run's own sessions filed not counted, so a resumed run draws the tasks
    it drew; its kept state opened under the profile `options` names
    (`profile`, from the file `profiles` where given); the settling step
    where `settle`; its lines checked as `model`'s."""
    view = queries.View(store, state)
    settings = run_settings(options)
    # a search this run's own sessions filed: another run's counts
    mine = queue.Run(store, state, FACTORY, options).sessions
    own = {
        a.key
        for a in store.answers()
        if a.kind in (WORKER_KIND, SETTLER_KIND)
        and a.state == state.id
        and (a.session or "").startswith(mine)
    }
    asked = asked_claims(
        view, options.get("blocks") or (), bool(options.get("every")), own
    )
    author = edits.agent(str(options.get("model") or "agent"), None)
    with edits.opened(store, state, settings, view.marks) as kept:
        yield Searching(kept, view, asked, author, bool(options.get("settle")))


def _search_task(task: Task, working: Working) -> str:
    """A claim as a search serves it: its words, its block's place and text,
    and pointers to the blocks near it."""
    reads = working.reads
    ident = task.block
    pointers = reads.pointers(ident)
    lines = [
        "is this claim argued?",
        f'claim: "{task.subject["words"]}"',
        f"block {reads.place(ident)}",
        working.view.texts[ident],
    ]
    if pointers:
        lines.append(
            "pointers: "
            + ", ".join(f"⟦{reads.handle(p)}⟧ {reads.section(p)}" for p in pointers)
        )
    return "\n".join(lines) + "\n"


def _confirm_task(task: Task, working: Working) -> str:
    """A claim with the line proposed for it: its words, its block's place
    and text, the line, and the block it names read whole, a block of a
    proof or another group as the whole group, since the group argues as
    one (the gate takes a member's line for its group's)."""
    reads = working.reads
    ident = task.block
    named = reads.resolve(task.subject["named"])
    whole = reads.whole(named)
    lines = [
        "does this line argue the claim?",
        f'claim: "{task.subject["words"]}"',
        f"block {reads.place(ident)}",
        working.view.texts[ident],
        "proposed line:",
        task.subject["proposed"],
    ]
    if task.subject["lines"] != task.subject["proposed"]:
        lines += ["given on the sheet, one line for its source and target, as:"]
        lines.append(task.subject["lines"])
    body = "\n".join(reads.block(reads.handle(m)) for m in reads.members(whole))
    if len(body) > NAMED_CHARS:
        body = body[:NAMED_CHARS] + " …(cut; `block` reads a member whole)"
    if whole != named:
        lines += [
            f"⟦{reads.handle(named)}⟧ is a block of ⟦{reads.handle(whole)}⟧, "
            "which argues as a whole; its blocks:",
            body,
        ]
    elif named != ident:
        lines += [f"what it names, ⟦{reads.handle(named)}⟧:", body]
    return "\n".join(lines) + "\n"


class Worker:
    """The worker's step: a claim served lean with pointers, answered
    argued with its line, checked by the search's gate on the working
    state, or unargued with the closest block read."""

    kind = WORKER_KIND
    stage = Stage.EDGES
    kickoff = QUEUE_KICKOFF
    tools = WORKER_TOOLS

    def __init__(self, search: Searching) -> None:
        self.search = search
        self.template = load_template(WORKER_TEMPLATE)

    def render(self, task: Task, working: Working) -> str:
        return _search_task(task, working)

    def answer(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        found = self.search.answered(task, tool, args, working)
        return self.search.checked(task, found, working, check)


class Settler:
    """The settling step: the worker's line served with what it names,
    confirmed; or the claim answered again, argued with a line of its own
    or unargued, where it rejects the line or the worker gave none."""

    kind = SETTLER_KIND
    stage = Stage.EDGES
    kickoff = QUEUE_KICKOFF
    tools = SETTLER_TOOLS

    def __init__(self, search: Searching) -> None:
        self.search = search
        self.template = load_template(SETTLER_TEMPLATE)

    def render(self, task: Task, working: Working) -> str:
        if task.subject.get("proposed") is None:
            return _search_task(task, working)
        return _confirm_task(task, working)

    def answer(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        if tool != CONFIRM.name:
            found = self.search.answered(task, tool, args, working)
            return self.search.checked(task, found, working, check)
        if task.subject.get("proposed") is None:
            return Refused(
                (
                    "no line is proposed for this task: search, then answer it "
                    "argued or unargued",
                )
            )
        if not str(args.get("why") or "").strip():
            return Refused(("give the reason (why)",))
        # the worker's line, checked by the gate when it was given
        return Accepted(task.subject["lines"])


def _line(
    view: queries.View, sheet: str, claim: Claim, args: Mapping[str, Any]
) -> dict[str, str]:
    """A typed argued answer, written by code: the line as given, its owner
    first (`proposed`), the fragment that gives it on `sheet` (`lines`,
    `_given_on`), and the block it names (`named`): the arguing block, the
    claim's restated, or the claim's own."""
    kind = args.get("kind")
    if kind not in KINDS:
        raise SearchError(f"kind {kind!r}: one of {', '.join(KINDS)}")
    quote = " ".join(str(args.get("quote") or "").split()).replace('"', "'")
    if not quote:
        raise SearchError("give the quote: the owner block's own words")
    own = f" [{claim.handle}]" if claim.handle else ""
    if kind == es.SELF:
        owner, named, line = claim.block, claim.block, f'→ self{own} · "{quote}"'
    else:
        by = str(args.get("by") or "")
        if not by:
            raise SearchError(f"a {kind} line names its block (by)")
        try:
            named = view.handle(view.resolve(by))
        except queries.QueryError as error:
            raise SearchError(f"by {by!r}: {error}") from None
        if kind == EdgeKind.JUSTIFIES:
            owner, line = named, f'→ justifies {claim.block}{own} · "{quote}"'
        else:
            target = args.get("target_claim")
            parts = f" [{target}]" if target else ""
            owner, line = claim.block, f'→ about{own} {named}{parts} · "{quote}"'
    return {
        "proposed": f"⟦{owner}⟧\n+ {line}",
        "lines": _given_on(sheet, owner, line, claim.handle),
        "named": named,
    }


def _given_on(sheet: str, owner: str, line: str, handle: str | None) -> str:
    """The fragment that gives `line` on `owner`: the line added; or, for an
    about line whose source block already restates other claims of the
    same target (one about edge per source and target), that line given
    back naming this claim and its target's claims too, its words and its
    other brackets as they were. A justifies line naming claims of its
    target apart, and a self line, stand beside the lines there
    (`docs/model.md` § 3)."""
    m = es.EDGE.match(line)
    assert m is not None
    if m.group("kind") != EdgeKind.ABOUT or handle is None:
        return f"⟦{owner}⟧\n+ {line}"
    target = m.group("target") or ""
    for old in edges_file.written_lines(sheet).get(owner, []):
        o = es.EDGE.match(old)
        if o is None or (o.group("kind"), o.group("target") or "") != (
            EdgeKind.ABOUT,
            target,
        ):
            continue
        own = _listed(o.group("own"))
        parts, adding = _listed(o.group("parts")), _listed(m.group("parts"))
        # a line restating none, or one end whole and the other in part, is
        # no line naming one more claim: the gate says why
        if es.RESTATES_NONE in own or not own or bool(parts) != bool(adding):
            break
        merged = old
        # the later bracket first, so the earlier one's place holds
        for end, names in (
            ("parts", [*parts, *(p for p in adding if p not in parts)]),
            ("own", [*own, *([] if handle in own else [handle])]),
        ):
            if o.group(end) is not None:
                start, stop = o.span(end)
                merged = merged[:start] + ", ".join(names) + merged[stop:]
        return f"⟦{owner}⟧\n- {old}\n+ {merged}"
    return f"⟦{owner}⟧\n+ {line}"


def _listed(bracket: str | None) -> list[str]:
    return [t.strip() for t in (bracket or "").split(",") if t.strip()]


# --- a search run -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settled:
    """A claim a search settled: argued or not, and the reason of the
    answer that settled it."""

    claim: Claim
    argued: bool
    why: str


@dataclass(frozen=True, slots=True)
class Found:
    """A search's run (`runner.given.Outcome`; its edit None where it argued
    no claim), each claim it settled, in its order."""

    outcome: given.Outcome
    settled: tuple[Settled, ...] = ()

    @property
    def complete(self) -> bool:
        return self.outcome.complete


def ask(
    run: queue.Run,
    model: str,
    runner: Runner | None = None,
    hosts: Sequence[Host] = (),
    shares: Sequence[float] | None = None,
    context_tokens: int | None = None,
    wait_seconds: float = queue.WAIT_SECONDS,
) -> Found:
    """`run`, the search's queue, asked of `hosts`, one a step, through
    `runner` (None: what the store holds replayed, nothing asked);
    complete, replayed or asked, the lines it settled given as the search's edges edit,
    `model`'s in the run's session, its sessions shown no stale mark
    (`runner.given.ask`)."""
    try:
        out = given.ask(
            run,
            model,
            _reason,
            False,
            runner,
            hosts,
            shares,
            context_tokens,
            wait_seconds,
        )
    except given.NotAsked as error:
        raise SearchError(str(error)) from None
    with queue.opened(run) as search:
        assert isinstance(search, Searching)
        settled = tuple(_settled(search, u) for u in out.settled)
    return Found(out, settled)


def _settled(search: Searching, u: queue.Used) -> Settled:
    return Settled(
        search.claim(u.task),
        u.call.tool in ARGUING_TOOLS,
        " ".join(str(u.call.args.get("why") or "").split()),
    )


def _reason(search: queue.Workflow, settled: Sequence[queue.Used]) -> str:
    """The search's edit's reason: each claim argued, by its block, and why."""
    assert isinstance(search, Searching)
    found = [_settled(search, u) for u in settled]
    return "; ".join(
        s.claim.block
        + (f" [{s.claim.handle}]" if s.claim.handle else "")
        + f": {s.why}"
        for s in found
        if s.argued
    )


def price(store: RecordStore, kind: str, model: str) -> queued.Price:
    """What the step `kind` is expected to cost on `model`: its session's
    start and a claim's (`runner.queued.price`, on `FITTED` where the store
    holds none of its sessions)."""
    template = {WORKER_KIND: WORKER_TEMPLATE, SETTLER_KIND: SETTLER_TEMPLATE}[kind]
    try:
        return queued.price(store, template, model, FITTED[kind])
    except queued.PriceError as error:
        raise SearchError(str(error)) from None
