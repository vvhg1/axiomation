"""A block's lines placed at its parts by an agent: the part each line
leaves from, and the parts each part writes.

An edge relying on a block's content that names some of its parts enters
the block at those parts, where the block is placed: some line of it
names a part it leaves from (`docs/model.md` § 9). Until one does, the
walk takes the block whole, each of its sentences' lines with it, and a
claim resting on one part rests on what another part relies on. The
placing is a queue (docs/architecture.md § 2): a task a block that is not
placed, that an edge relying on its content enters at parts holding less
than the block's words, and whose lines the walk would give beyond what
those parts name (a uses line out of it, or a qualifier of another part).
One step, the edges agent's, answers each block:

- **`placed`**, with why: the parts it adds, for words a line leaves from
  that no part holds (a content role, never a claim, as the circles'
  question adds them); each of its lines with the parts it leaves from,
  or none (the block whole); and for each part the parts of the block it
  writes, with the words that write them. A line from the block whole is
  given at every part, so a block of one part is placed only by adding
  another. Code writes the lines: each part's legend line, each line
  placed with its source bracket rewritten, each part's sibling line
  (`→ uses [<part>] <block> [<parts>]`); and checks them at once on the
  working state (`placing_problems`, then the edges check);
- **`whole`**, with why: no line can be placed, or the parts write each
  other throughout. Filed as a `WholeBlock` under the block's key
  (`block_key`), never asked again while the key stands.

A line a person pinned stays as written. A complete run ends with one
edges edit, the placing's (`runner.given`), shown no stale mark. Asked of
the blocks named, else of every block to place, in the paper's order.
`place --ask` asks it, and a build or an update at its end, after the
support search and before the circles' question, which reads what
claims rest on.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from axiomation import edits, judgments, queries, queue
from axiomation.changes import Layer
from axiomation.circles import CONTENT_ROLES, CircleError, with_ends
from axiomation.driver import ReviewSettings
from axiomation.profiles import run_settings
from axiomation.questions import Stage, Template, edges_file, load_template
from axiomation.questions import edge_sheet as es
from axiomation.queue import Accepted, Refused, Task, Tool, Working
from axiomation.records import JudgmentRecords, RecordStore, WholeBlock
from axiomation.runner import Runner, given, queued
from axiomation.runner.hosts import Host
from axiomation.versions import State

TEMPLATE = "edges-place"
KIND = "edges-place"
#: The workflow a queue run opens (`queue.Run.factory`).
FACTORY = "axiomation.placing:placing"
KICKOFF = queue.KICKOFF
#: What a block is expected to cost where the store holds none of the
#: placing's sessions: the circles' question's fit (Opus at effort high,
#: the edges writer's brief written once, then a task served with its lines
#: and read with a few reads), a like task, until its own runs are recorded.
FITTED = queued.Fitted(
    "claude-opus-5-5", start=0.06, task=0.044, like="circles question"
)


class PlaceError(ValueError):
    """Blocks that cannot be asked, or an answer that cannot be given."""


@dataclass(frozen=True, slots=True)
class Part:
    """A part of the block: its label, its words and its role."""

    label: str
    words: str
    role: str


@dataclass(frozen=True, slots=True)
class Line:
    """A line of the sheet: its owner's handle, its text, and whether a
    person pinned it (it stays as written)."""

    owner: str
    text: str
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class AskedBlock:
    """A block asked: its key (`block_key`), its handle, its parts, its
    uses lines out (each placed at the parts it leaves from), the
    qualifies lines onto it, and the lines entering it at its parts."""

    key: str
    handle: str
    parts: tuple[Part, ...]
    lines: tuple[Line, ...]
    qualifiers: tuple[Line, ...]
    entering: tuple[Line, ...]

    @property
    def labels(self) -> frozenset[str]:
        return frozenset(p.label for p in self.parts)


def block_key(
    sheet: es.Sheet,
    handle: str,
    lines: Sequence[Line],
    qualifiers: Sequence[Line],
) -> str:
    """A block's key as the placing reads it: its words, its parts, its
    uses lines out and the qualifies lines onto it. A change to any of
    them makes it another block to place."""
    data = {
        "text": es.norm(sheet.texts.get(handle, "")),
        "parts": sorted(
            [label, es.norm(words), sheet.legend_roles.get((t, label), "")]
            for (t, label), words in sheet.legend.items()
            if t == handle
        ),
        "lines": sorted(es.norm(x.text) for x in lines),
        "qualifiers": sorted([x.owner, es.norm(x.text)] for x in qualifiers),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]


def _content_parts(sheet: es.Sheet, e: es.Edge) -> tuple[str, ...]:
    """The parts of its target a uses line relying on content names: its
    target end's labels where each is a part of no `claim` role and none
    a claim's handle (`established._Graph._relied`); else none."""
    if e.kind != "uses" or not e.parts or e.source == e.target:
        return ()
    claims = es.claim_handles(sheet, e.target)
    for p in e.parts:
        if p in claims or (e.target, p) not in sheet.legend:
            return ()
        if sheet.legend_roles.get((e.target, p)) == "claim":
            return ()
    return e.parts


#: What is no word of a block's own: an environment's, a label's, a
#: reference's or a citation's command with its argument, a run break,
#: punctuation. Any other command's name and argument are words (`\pi`,
#: `\textbf{…}`).
_NO_WORD = re.compile(
    r"\\(?:begin|end|label|ref|eqref|cref|Cref|autoref|\w*cite\w*)\*?"
    r"(?:\[[^\]]*\])?\{[^}]*\}|⟦[^⟧]*⟧|[^\w]"
)


def _covers(text: str, words: Sequence[str]) -> bool:
    """Whether `words`, parts of a block, hold every word of its `text`:
    then no line leaves from outside them."""
    rest = es.norm(text)
    for w in words:
        rest = rest.replace(es.norm(w), " ")
    return not _NO_WORD.sub("", rest)


def _row(rows: Sequence[str], e: es.Edge) -> str:
    return rows[e.line - 1].strip().removeprefix(es.KEEP_MARK)


@dataclass(frozen=True, slots=True)
class ReadWhole:
    """A block read whole: the record and its author's name."""

    record: WholeBlock
    author: str


def read_whole(store: RecordStore) -> dict[str, ReadWhole]:
    """Each block's key to the latest word that it is read whole. Keyed on
    the block alone: one whose words, parts or lines change is asked
    again."""
    found = store.judgment_records().wholes
    if not found:
        return {}
    answers = store.authoritative()
    return {
        r.block: ReadWhole(r, judgments.author_of(answers, r.answer).name)
        for r in found
    }


def to_place(
    sheet_text: str, pinned: Collection[tuple[str, str]] = ()
) -> list[AskedBlock]:
    """The blocks of `sheet_text` to place, in the paper's order: those no
    line names a part it leaves from, that a uses line relying on their
    content enters at parts that hold less than the block's words, and
    that give beyond what those parts name: a uses line out of them that
    is not `assumed` (an assumed line leaves from its block whole), or a
    qualifies line onto other parts of them. `pinned`: a person's lines,
    `(owner, line)`."""
    sheet = es.parse(sheet_text)
    rows = sheet_text.split("\n")
    entered: dict[str, list[tuple[es.Edge, tuple[str, ...]]]] = {}
    placed: set[str] = set()
    for e in sheet.edges:
        if e.kind == "uses" and es.source_parts(sheet, e):
            placed.add(e.source)
        parts = _content_parts(sheet, e)
        if parts:
            entered.setdefault(e.target, []).append((e, parts))
    found = []
    for handle in sheet.block_handles:
        if handle in placed or handle not in entered:
            continue
        text = sheet.texts.get(handle, "")
        narrower = [
            parts
            for _, parts in entered[handle]
            if not _covers(text, [sheet.legend[handle, p] for p in parts])
        ]
        if not narrower:
            continue
        out = [
            e
            for e in sheet.edges
            if e.source == handle
            and e.kind == "uses"
            and not e.assumed
            and e.target != handle
        ]
        onto = [e for e in sheet.edges if e.kind == "qualifies" and e.target == handle]
        # a qualifier naming no part of the block (its claims, or none)
        # is given at every part
        beyond = bool(out) or any(
            labels and labels.isdisjoint(parts)
            for q in onto
            for labels in [{p for p in q.parts if (handle, p) in sheet.legend}]
            for parts in narrower
        )
        if not beyond:
            continue
        lines = tuple(
            Line(handle, t, (handle, t) in pinned) for t in (_row(rows, e) for e in out)
        )
        qualifiers = tuple(Line(q.source, _row(rows, q)) for q in onto)
        parts = tuple(
            Part(label, words, sheet.legend_roles.get((t, label), ""))
            for (t, label), words in sheet.legend.items()
            if t == handle
        )
        found.append(
            AskedBlock(
                block_key(sheet, handle, lines, qualifiers),
                handle,
                parts,
                lines,
                qualifiers,
                tuple(Line(e.source, _row(rows, e)) for e, _ in entered[handle]),
            )
        )
    return found


def listed(view: queries.View) -> list[AskedBlock]:
    """The blocks to place at the view's state (`to_place`), each listed
    whether or not a word reads it whole."""
    with edits.opened(view.store, view.state, None, view.marks) as kept:
        return to_place(edits.shown(kept, Layer.EDGES))


def asked_blocks(
    view: queries.View,
    kept: edits.Kept,
    named: Sequence[str] = (),
    uncounted: Collection[str] = (),
) -> list[AskedBlock]:
    """The blocks to place on the view's state, opened as `kept`, their
    lines found on its sheet and its person's pins (`to_place`): those
    named (a handle, a node id or a handle's prefix), else each no word
    reads whole. A word filed with an answer in `uncounted` reads none
    whole. Refused where a name is no block to place, or one read whole."""
    sheet_text = edits.shown(kept, Layer.EDGES)
    pinned = set()
    for p in kept.pins:
        if p.layer is Layer.EDGES and p.held and p.kind in edges_file.STANDS:
            # a pin's owner by its block's id or handle
            for owner in {p.owner, view.handle(p.owner)}:
                pinned.add((owner, p.line.strip()))
    found = to_place(sheet_text, pinned)
    whole = {
        k: r
        for k, r in read_whole(view.store).items()
        if r.record.answer not in uncounted
    }
    if not named:
        return [a for a in found if a.key not in whole]
    by_handle = {a.handle: a for a in found}
    chosen = []
    for name in named:
        try:
            handle = view.handle(view.resolve(name))
        except queries.QueryError as error:
            raise PlaceError(f"{name}: {error}") from None
        a = by_handle.get(handle)
        if a is None:
            raise PlaceError(
                f"{handle} is no block to place at state {view.state.id}: "
                "`axiomation place PAPER` lists them"
            )
        read = whole.get(a.key)
        if read is not None:
            raise PlaceError(
                f"{handle} is read whole by {read.author} at "
                f"{read.record.state[:8]}: a change to its words, parts or lines "
                "makes it another block, asked again"
            )
        chosen.append(a)
    return list(dict.fromkeys(chosen))


# --- the gate ---------------------------------------------------------------------


def placing_problems(
    a: AskedBlock, fragment: edits.Fragment, claims: Collection[str] = ()
) -> list[str]:
    """What a placed answer does that is no placing (`docs/model.md`
    § 9): a part added on another block, of no content role, with a label
    the block has, holding a claim's words (`claims`, the block's) or
    words a part holds, or named by no line; a line off the block, a
    line taken off and not given back, or given back differing beyond its
    source bracket or already naming parts it leaves from; a pinned line
    rewritten; a line added that is no sibling line of the block; a label
    that is no part of the block; and an answer that places nothing."""
    where = f"block {a.handle}"
    problems: list[str] = []
    added_parts: set[str] = set()
    added_words: list[tuple[str, str]] = []
    for legend in fragment.legend:
        m = es.LEGEND.match(legend)
        said = f"{where}: {legend.strip()}"
        if m is None:
            problems.append(f"{said}: {es.LEGEND_FORM}")
            continue
        target, label, words, role = m.groups()
        if target != a.handle:
            problems.append(f"{said}: a placing adds a part only on its own block")
        if role not in CONTENT_ROLES:
            problems.append(
                f"{said}: a part takes "
                f"{', '.join(f'({r})' for r in CONTENT_ROLES)}, never a claim: a "
                "line the claim relies on leaves from its reason's words, a "
                "(justification)"
            )
        if label in a.labels or label in added_parts:
            problems.append(f"{said}: {a.handle} has [{label}] already")
        # the walk reads parts by their labels, never their spans: a line
        # placed at a part holding another's words is lost where that other
        # is entered
        held = [(p.label, p.words) for p in a.parts] + added_words
        for other, theirs in held:
            if es.norm(theirs) in es.norm(words) or es.norm(words) in es.norm(theirs):
                problems.append(
                    f"{said}: its words share [{other}]'s: a part added holds "
                    "only words no part holds; a line leaving from a part's words "
                    "is placed at that part"
                )
        added_words.append((label, words))
        if any(es.norm(c) in es.norm(words) for c in claims):
            problems.append(
                f"{said}: the words hold a claim of {a.handle}: a part holds only "
                "what a line leaves from as content"
            )
        added_parts.add(label)
    labels = a.labels | added_parts
    named: set[str] = set()
    on = {x.text: x for x in a.lines}
    taken: dict[tuple[str, str, str], str] = {}
    added: dict[tuple[str, str, str], str] = {}
    siblings = []
    for owner, op, text in fragment.lines:
        said = f"{where}: ⟦{owner}⟧ {op} {text}"
        m = es.EDGE.match(text)
        if owner != a.handle or m is None or m.group("kind") != "uses":
            problems.append(f"{said}: a placing writes the block's own uses lines")
            continue
        if op == edits.KEEP:
            problems.append(f"{said}: a placing keeps nothing: leave the line out")
            continue
        if m.group("target") == a.handle:
            if op == edits.RETRACT:
                problems.append(f"{said}: a placing takes off no line inside the block")
            else:
                siblings.append((said, m))
            continue
        same = (m.group("target") or "", m.group("mark") or "", m.group("evidence"))
        if op == edits.RETRACT:
            line = on.get(text)
            if line is None:
                problems.append(
                    f"{said}: not a line of the block, as edges.md holds it"
                )
            elif line.pinned:
                problems.append(f"{said}: a person's line, pinned: it stays as written")
            taken[same] = text
        else:
            added[same] = text
    for same, old in taken.items():
        new = added.pop(same, None)
        if new is None:
            problems.append(
                f"{where}: ⟦{a.handle}⟧ - {old}: a line is placed, never taken off"
            )
            continue
        o, n = es.EDGE.match(old), es.EDGE.match(new)
        assert o is not None and n is not None
        for group in ("kind", "target", "parts", "mark", "flag", "evidence", "note"):
            if (o.group(group) or "") != (n.group(group) or ""):
                problems.append(
                    f"{where}: ⟦{a.handle}⟧ + {new}: its {group} changed: only the "
                    "bracket after its kind places it"
                )
        if _tokens(o.group("own")):
            problems.append(
                f"{where}: ⟦{a.handle}⟧ - {old}: names a part it leaves from already"
            )
        named |= _tokens(n.group("own"))
        unknown = _tokens(n.group("own")) - labels
        if not _tokens(n.group("own")) or unknown:
            problems.append(
                f"{where}: ⟦{a.handle}⟧ + {new}: names "
                + (
                    f"[{', '.join(sorted(unknown))}], no part of the block"
                    if unknown
                    else "no part it leaves from: a line from the block whole stays "
                    "as it is"
                )
            )
    for new in added.values():
        problems.append(
            f"{where}: ⟦{a.handle}⟧ + {new}: takes no line off: a placed line "
            "replaces one of the block's with the same target and quote"
        )
    for said, m in siblings:
        leaving, onto = _tokens(m.group("own")), _tokens(m.group("parts"))
        named |= leaving | onto
        unknown = (leaving | onto) - labels
        if unknown:
            problems.append(
                f"{said}: [{', '.join(sorted(unknown))}] is no part of the block"
            )
        if not leaving or not onto:
            problems.append(
                f"{said}: a line inside the block names a part at both ends"
            )
    problems += [
        f"{where}: [{label}] is added and named by no line: add a part only for "
        "the words a line leaves from"
        for label in sorted(added_parts - named)
    ]
    if not taken and not siblings:
        problems.append(f"{where}: {_NOTHING}")
    return problems


def _tokens(bracket: str | None) -> set[str]:
    return {t.strip() for t in (bracket or "").split(",") if t.strip()}


def placed_problems(
    kept: edits.Kept,
    a: AskedBlock,
    lines: str,
    author: edits.Author,
    sheet_text: str,
) -> list[str]:
    """What the placing's gate refuses in a placed answer's `lines` on
    `sheet_text`, the working state's sheet: its placing
    (`placing_problems`), then the edit as `author`'s edges edit would be
    checked."""
    if not lines.strip():
        return [f"block {a.handle}: {_NOTHING}"]
    try:
        fragment = edits.parse_fragment(lines)
    except edits.EditError as error:
        return [f"block {a.handle}: {error}"]
    claims = es.claim_handles(es.parse(sheet_text), a.handle).values()
    problems = placing_problems(a, fragment, claims)
    if problems:
        return problems
    try:
        text = edits.applied_fragment(sheet_text, fragment)
    except edits.EditError as error:
        return [str(error)]
    return list(edits.edit_problems(kept, Layer.EDGES, text, author))


# --- the placing as a queue (docs/architecture.md § 2) ----------------------------

_LABELS = {"type": "array", "items": {"type": "string"}}
PLACED = Tool(
    "placed",
    "File the task's block placed: the parts each line leaves from, and the "
    "parts each part writes, written by code and checked at once. Accepted, "
    "it returns the next task.",
    {
        "type": "object",
        "required": ["task", "why"],
        "properties": {
            "task": {"type": "string"},
            "why": {
                "type": "string",
                "description": "where each line's reliance is written, part by part",
            },
            "parts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["label", "words", "role"],
                    "properties": {
                        "label": {"type": "string"},
                        "words": {
                            "type": "string",
                            "description": "the block's own words a line leaves "
                            "from that no part holds, never a claim",
                        },
                        "role": {"type": "string", "enum": list(CONTENT_ROLES)},
                    },
                },
            },
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["line", "from"],
                    "properties": {
                        "line": {
                            "type": "string",
                            "description": "the line's number, as the task "
                            "numbers them (L1, L2, …)",
                        },
                        "from": {
                            **_LABELS,
                            "description": "the labels of the parts it leaves "
                            "from; [] for the block whole",
                        },
                    },
                },
            },
            "writes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["part", "uses", "quote"],
                    "properties": {
                        "part": {
                            "type": "string",
                            "description": "a part's label: the part that writes",
                        },
                        "uses": {
                            **_LABELS,
                            "description": "the labels of the other parts its "
                            "words write",
                        },
                        "quote": {
                            "type": "string",
                            "description": "the block's own words, in that part, "
                            "that write them",
                        },
                    },
                },
            },
        },
    },
)
WHOLE = Tool(
    "whole",
    "File the task's block read whole: no line leaves from one part apart, "
    "or its parts write each other throughout. Accepted, it returns the next "
    "task.",
    {
        "type": "object",
        "required": ["task", "why"],
        "properties": {"task": {"type": "string"}, "why": {"type": "string"}},
    },
)
TOOLS = (PLACED, WHOLE)
_NOTHING = (
    f"`{PLACED.name}` places no line and names no part another writes: place a "
    f"line, or answer `{WHOLE.name}`"
)


class Placing:
    """The placing on a kept state as a queue: a task a block asked, while
    its lines stand on the working sheet; one step. Its lines are the edges
    writer's."""

    restating = False
    reads: tuple[queue.Read, ...] = ()
    by_graph = True
    serving = queue.Serving()

    def __init__(
        self,
        kept: edits.Kept,
        view: queries.View,
        asked: Sequence[AskedBlock],
        author: edits.Author,
        writer: Template,
    ) -> None:
        self.kept = kept
        self.view = view
        self.files = kept.directory
        self.sheet = edits.shown(kept, Layer.EDGES)
        self.asked = tuple(asked)
        self.author = author
        self.steps = (Placer(self, writer),)
        self._by_task = {f"b{a.key}": a for a in self.asked}

    def draw(self, working: Working) -> list[Task]:
        written = edges_file.written_lines(working.sheet)
        found = []
        for task, a in self._by_task.items():
            if any(x.text not in written.get(x.owner, []) for x in a.lines):
                continue
            ident = self.view.resolve(a.handle)
            found.append(Task(task, ident, {"block": a.handle}))
        return found

    def block(self, task: Task) -> AskedBlock:
        """The task's block, as it was asked."""
        return self._by_task[task.id]

    def render(self, task: Task, working: Working) -> str:
        """A block as served: its place and text, its parts, the lines
        entering it at its parts, its lines numbered as the sheet holds
        them, and the qualifiers onto it."""
        a = self.block(task)
        reads = working.reads
        ident = reads.resolve(a.handle)
        lines = [
            "where does each line of this block leave from?",
            f"block {reads.place(ident)}",
            working.view.texts[ident],
            "its parts:",
            *(
                f'  [{p.label}] "{p.words}"' + (f" ({p.role})" if p.role else "")
                for p in a.parts
            ),
            "lines entering it at its parts:",
            *(f"  ⟦{x.owner}⟧ {x.text}" for x in a.entering),
            "its lines:",
            *(
                f"L{n} ⟦{x.owner}⟧ {x.text}"
                + ("  (a person's line, pinned: it stays)" if x.pinned else "")
                for n, x in enumerate(a.lines, 1)
            ),
        ]
        if a.qualifiers:
            lines += [
                "qualifiers onto it (one naming parts qualifies those parts alone):",
                *(f"  ⟦{x.owner}⟧ {x.text}" for x in a.qualifiers),
            ]
        return "\n".join(lines) + "\n"

    def answered(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        """`tool`'s answer on the working state: a `whole` filed as a
        `WholeBlock`; a `placed` as the lines code writes from it, checked
        by the gate where `check`."""
        why = " ".join(str(args.get("why") or "").split())
        if not why:
            return Refused(("give the reason (why)",))
        a = self.block(task)
        if tool == WHOLE.name:
            whole = WholeBlock("", None, self.view.state.id, a.key, why)
            return Accepted(judgments=JudgmentRecords(wholes=(whole,)))
        try:
            lines = placed_lines(a, args)
        except PlaceError as error:
            return Refused((str(error),))
        if not check:
            return Accepted(lines)
        problems = placed_problems(self.kept, a, lines, self.author, working.sheet)
        return Refused(tuple(problems)) if problems else Accepted(lines)


def _number(ref: Any, count: int) -> int:
    n = str(ref).strip().upper().removeprefix("L")
    if not n.isdigit() or not 1 <= int(n) <= count:
        raise PlaceError(f"line {ref!r}: the block's are L1 to L{count}")
    return int(n) - 1


def _labels(value: Any, what: str) -> list[str]:
    if not isinstance(value, list):
        raise PlaceError(f"{what}: a list of the block's part labels")
    return [str(x).strip() for x in value if str(x).strip()]


def placed_lines(a: AskedBlock, args: Mapping[str, Any]) -> str:
    """A `placed` answer as a fragment code writes on the block: each part
    added, then each line given parts it leaves from taken off and given
    back with its source bracket naming them (a line left whole is not
    written), then each part's sibling line, quoting the words that write
    the parts it uses."""
    legend = []
    for part in args.get("parts") or []:
        if not isinstance(part, Mapping):
            raise PlaceError(f"parts: {part!r} is a label, words and a role")
        words = " ".join(str(part.get("words") or "").split()).replace('"', "'")
        label, role = str(part.get("label") or ""), str(part.get("role") or "")
        if not (label and words and role):
            raise PlaceError("a part takes its label, words and role")
        legend.append(f'+ {a.handle} [{label}]: "{words}" ({role})')
    body = []
    answered: set[int] = set()
    for entry in args.get("lines") or []:
        if not isinstance(entry, Mapping):
            raise PlaceError(f"lines: {entry!r} is a line and the parts it leaves from")
        n = _number(entry.get("line"), len(a.lines))
        if n in answered:
            raise PlaceError(f"L{n + 1} is placed twice: name it once")
        answered.add(n)
        leaving = _labels(entry.get("from"), f"L{n + 1}'s from")
        if not leaving:
            continue
        line = a.lines[n]
        try:
            given = with_ends(line.text, None, leaving)
        except CircleError as error:
            raise PlaceError(str(error)) from None
        body += [f"{edits.RETRACT} {line.text}", f"{edits.ADD} {given}"]
    left = [
        f"L{n}"
        for n, x in enumerate(a.lines, 1)
        if not x.pinned and n - 1 not in answered
    ]
    if left:
        raise PlaceError(
            f"{', '.join(left)}: place each line, at the parts it leaves from or "
            "[] for the block whole"
        )
    writing: set[str] = set()
    for entry in args.get("writes") or []:
        if not isinstance(entry, Mapping):
            raise PlaceError(f"writes: {entry!r} is a part, the parts it uses, a quote")
        part = str(entry.get("part") or "").strip()
        uses = _labels(entry.get("uses"), f"{part}'s uses")
        quote = " ".join(str(entry.get("quote") or "").split()).replace('"', "'")
        if not (part and uses and quote):
            raise PlaceError("writes: name the part, the parts it uses and its quote")
        if part in writing:
            raise PlaceError(f"writes: [{part}] is named twice: list what it uses once")
        writing.add(part)
        body.append(
            f'{edits.ADD} → uses [{part}] {a.handle} [{", ".join(uses)}] · "{quote}"'
        )
    return "\n".join([*legend, *([f"⟦{a.handle}⟧", *body] if body else [])])


class Placer:
    """The placing's one step: a block served, answered `placed` or
    `whole`."""

    kind = KIND
    stage = Stage.EDGES
    kickoff = KICKOFF
    tools = TOOLS

    def __init__(self, placing: Placing, writer: Template) -> None:
        self.placing = placing
        self.template = brief(writer)

    def render(self, task: Task, working: Working) -> str:
        return self.placing.render(task, working)

    def answer(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        return self.placing.answered(task, tool, args, working, check)


def brief(writer: Template) -> Template:
    """The step's brief: the edges writer's (the lines' notation), then the
    placing's; one text, so its version binds both."""
    own = load_template(TEMPLATE)
    return Template(TEMPLATE, f"{writer.text}\n\n{own.text}")


@contextmanager
def placing(
    store: RecordStore, state: State, options: Mapping[str, Any]
) -> Iterator[Placing]:
    """The placing's queue on `state` (`queue.Run.factory`): the blocks
    `options` names (`blocks`), else each no word reads whole, a word this
    run's own sessions filed not counted; its kept state opened under the
    profile `options` names; its lines checked as `model`'s."""
    view = queries.View(store, state)
    settings = run_settings(options)
    mine = queue.Run(store, state, FACTORY, options).sessions
    own = {
        a.key
        for a in store.answers()
        if a.kind == KIND and a.state == state.id and (a.session or "").startswith(mine)
    }
    author = edits.agent(str(options.get("model") or "agent"), None)
    writer = (settings or ReviewSettings()).templates.edges
    with edits.opened(store, state, settings, view.marks) as kept:
        asked = asked_blocks(view, kept, options.get("blocks") or (), own)
        yield Placing(kept, view, asked, author, writer)


# --- a placing run ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Said:
    """A block a run settled: placed or read whole, and why."""

    block: AskedBlock
    whole: bool
    why: str


@dataclass(frozen=True, slots=True)
class Done:
    """A placing run (`runner.given.Outcome`), each block it settled read."""

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
    """`run`, the placing's queue, asked of `hosts` through `runner` (None:
    what the store holds replayed); complete, the lines it placed given as
    `model`'s edges edit, shown no stale mark (`runner.given.ask`).
    Refused before it is asked where the state takes no edit."""
    try:
        out = given.ask(
            run,
            model,
            _reason,
            False,
            runner,
            hosts,
            None,
            context_tokens,
            wait_seconds,
        )
    except given.NotAsked as error:
        raise PlaceError(str(error)) from None
    with queue.opened(run) as found:
        assert isinstance(found, Placing)
        said = tuple(_said(found, u) for u in out.settled)
    return Done(out, said)


def _said(placing: Placing, u: queue.Used) -> Said:
    return Said(
        placing.block(u.task),
        u.call.tool == WHOLE.name,
        " ".join(str(u.call.args.get("why") or "").split()),
    )


def _reason(placing: queue.Workflow, settled: Sequence[queue.Used]) -> str:
    """The edit's reason: each placed block's, by its handle."""
    assert isinstance(placing, Placing)
    return "; ".join(
        f"{s.block.handle}: {s.why}"
        for s in (_said(placing, u) for u in settled)
        if not s.whole
    )


def price(store: RecordStore, model: str) -> queued.Price:
    """What the placing is expected to cost on `model`: its session's start
    and a block's (`runner.queued.price`)."""
    try:
        return queued.price(store, TEMPLATE, model, FITTED)
    except queued.PriceError as error:
        raise PlaceError(str(error)) from None
