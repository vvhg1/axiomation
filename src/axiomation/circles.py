"""A circle asked of an agent: what each end on it relies on, by the text.

A circle of what claims rest on (`docs/model.md` § 9) closes most often
because a line's end names less than the text relies on. The circles
question is a queue (docs/architecture.md § 2): a task a circle, served
with its claims and its lines as the working state's sheet holds them,
numbered, with their candidate ends. One step, the edges agent's, answers
each circle:

- **`refined`**, with why: the lines it narrows by their number, each with
  the names its ends keep; the lines whose candidate end it keeps as
  they stand; the parts it adds (a block's definition, assumption or
  justification words, labelled); the `restated` claims of the circle it
  makes `unsupported`. Code writes the lines, a narrowed one its line with
  its brackets rewritten, and checks them at once on the working state;
- **`real`**, with why and a proposal (what a person should change): every
  end named, and the circle stays. Filed with the answer as a
  `RealCircle` under the circle's key, never as a finding: the circle
  fails `unsupported-claims` already.

The answer may only narrow (`narrowing_problems`): it names the part or
claim a target end relies on, or the claims an about line's source end
restates for, or none (`[-]`); it may make a `restated` claim on the
circle `unsupported`. It deletes no line, widens no end and adds no
support. A complete run ends with one edges edit, the circles'
(`runner.given`), shown no stale mark.

Asked of the circles named by their key's prefix, else of every circle
no word holds real, the heaviest first. A circle with a line an earlier
answer narrowed leaves the queue: it is another circle, asked on the
state the run makes. One this run read real is drawn still, so a resumed
run draws what it drew. `circles --ask` asks it, and a build or an update
at its end.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from axiomation import edits, queries, queue
from axiomation.changes import Layer
from axiomation.driver import ReviewSettings
from axiomation.established import Circle, Coarse
from axiomation.profiles import run_settings
from axiomation.questions import Stage, Template, edges_file, load_template
from axiomation.questions import edge_sheet as es
from axiomation.queue import Accepted, Refused, Task, Tool, Working
from axiomation.queue.reads import ReadError
from axiomation.records import JudgmentRecords, RealCircle, RecordStore
from axiomation.runner import Runner, given, queued
from axiomation.runner.hosts import Host
from axiomation.versions import State

TEMPLATE = "edges-circles"
KIND = "edges-circles"
#: The workflow a queue run opens (`queue.Run.factory`).
FACTORY = "axiomation.circles:circling"
KICKOFF = queue.KICKOFF
#: The roles a legend part the answer adds may take: content, never a claim.
CONTENT_ROLES = ("definition", "assumption", "justification")
#: What the circles cost where the store holds none of their sessions: on
#: Opus at effort high, a session of two circles costs about $0.15, its
#: first call about $0.06 (the brief written once), the rest over the
#: circles.
FITTED = queued.Fitted("claude-opus-5-5", start=0.06, task=0.044)


class CircleError(ValueError):
    """Circles that cannot be asked, or an answer that cannot be given."""


@dataclass(frozen=True, slots=True)
class Line:
    """A line on a circle as the sheet holds it: its owner's handle, its
    text, its candidate ends, and whether a person pinned it (it stays as
    written)."""

    owner: str
    text: str
    coarse: tuple[Coarse, ...]
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class AskedCircle:
    """A circle asked: its key as the views show it, the circle, its text
    as `open` shows it, its lines as the sheet holds them, and its claims
    by their owner's handle and words."""

    key: str
    circle: Circle
    text: str
    lines: tuple[Line, ...]
    claims: tuple[tuple[str, str], ...]

    @property
    def targets(self) -> frozenset[str]:
        """The handles at the target end of its lines."""
        found = set()
        for line in self.lines:
            m = es.EDGE.match(line.text)
            if m is not None and m.group("target"):
                found.add(m.group("target"))
        return frozenset(found)


def asked_circles(
    view: queries.View,
    kept: edits.Kept,
    named: Sequence[str] = (),
    uncounted: Collection[str] = (),
) -> list[AskedCircle]:
    """The circles to ask on the view's state, opened as `kept`, their
    lines found on its sheet and its person's pins: those named by a
    prefix of their key, else every circle no word holds real, the one
    holding back the most claims first. A word filed with an answer in
    `uncounted` holds none real. Refused where a name is no circle's, or
    one's read real under its key: a line changed makes it another
    circle."""
    found = list(view.circles)
    read_real = {
        k: r for k, r in view.read_real.items() if r.record.answer not in uncounted
    }
    if named:
        chosen = []
        for name in named:
            hits = [c for c in found if c.key.startswith(name)]
            if len(hits) != 1:
                raise CircleError(
                    f"{name} names {'no circle' if not hits else 'several circles'} "
                    f"at state {view.state.id}: `axiomation open --kind circle` "
                    "lists them"
                )
            real = read_real.get(hits[0].key)
            if real is not None:
                raise CircleError(
                    f"circle {name} is read real by {real.author} at "
                    f"{real.record.state[:8]}: a line or a standing changed on it "
                    "makes it another circle, asked again"
                )
            chosen.append(hits[0])
        found = list(dict.fromkeys(chosen))
    else:
        found = [c for c in found if c.key not in read_real]
    sheet_text = edits.shown(kept, Layer.EDGES)
    # a person's written or kept lines, by their owner's block id
    pinned = {
        (p.owner, p.line.strip())
        for p in kept.pins
        if p.layer is Layer.EDGES and p.held and p.kind in edges_file.STANDS
    }
    return [_asked(view, c, sheet_text, pinned) for c in found]


def _asked(
    view: queries.View,
    circle: Circle,
    sheet_text: str,
    pinned: Collection[tuple[str, str]],
) -> AskedCircle:
    sheet = es.parse(sheet_text)
    rows = sheet_text.split("\n")
    lines: dict[tuple[str, str], Line] = {}
    for line in circle.lines:
        edge = line.step.edge
        if edge is None:
            continue
        text = _sheet_line(view, sheet, rows, edge).removeprefix(es.KEEP_MARK)
        owner = view.handle(edge["source"])
        held = lines.get((owner, text))
        coarse = tuple(dict.fromkeys((*(held.coarse if held else ()), *line.coarse)))
        lines[owner, text] = Line(owner, text, coarse, (edge["source"], text) in pinned)
    claims = []
    for c in circle.claims:
        block = view.readings.block_of(c.node)
        claims.append(("-" if block is None else view.handle(block), c.words))
    return AskedCircle(
        circle.key[:8],
        circle,
        queries.circle_text(view, circle),
        tuple(lines.values()),
        tuple(claims),
    )


def _sheet_line(
    view: queries.View, sheet: es.Sheet, rows: Sequence[str], edge: dict
) -> str:
    """The sheet's line for an edge of the export: its source, kind and
    target, the one quoting its evidence where several stand; else the
    line as the views print it."""
    source = view.handle(edge["source"])
    target = edge["target"]
    onto = target.get("block", target.get("group"))
    handle = None if onto is None else view.handle(onto)
    same = [
        e
        for e in sheet.edges
        if e.source == source and e.kind == edge["kind"] and e.target == handle
    ]
    quote = es.norm((edge.get("evidence") or [{}])[0].get("text", ""))
    quoted = [e for e in same if es.norm(e.evidence) == quote] or same
    if quoted and quoted[0].line:
        return rows[quoted[0].line - 1].strip()
    return view.edge_line(edge)


def narrowing_problems(
    sheet: es.Sheet,
    a: AskedCircle,
    fragment: edits.Fragment,
    kept: Sequence[tuple[str, str]],
) -> list[str]:
    """What a refined answer does that is no narrowing (`docs/architecture.md`
    § 9): a line off the circle, one taken off and not given back, one
    given that takes none off, a pair differing beyond its brackets or
    widening an end, a line given or taken twice; an about line's source
    end naming a claim off the circle, which would lend it a route; a
    standing other than a `restated` claim of the circle made
    `unsupported`; a legend line on another block, of no content role,
    with a label its block has, or holding a claim's words; a line with a
    candidate end left unanswered; and an answer that narrows nothing."""
    where = f"circle {a.key}"
    problems: list[str] = []
    on = {(line.owner, line.text) for line in a.lines}
    # the blocks some line of which names a part it leaves from
    placed = {
        e.source for e in sheet.edges if e.kind == "uses" and es.source_parts(sheet, e)
    }
    members = {(owner, es.norm(words)) for owner, words in a.claims}
    taken: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    given: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    standings: dict[tuple[str, str], dict[str, re.Match[str]]] = {}
    for owner, op, text in fragment.lines:
        if m := es.SUPPORT.match(text):
            claim = es.norm(m.group("claim"))
            if (owner, claim) not in members:
                problems.append(
                    f"{where}: ⟦{owner}⟧ {text}: only a claim on the circle may take "
                    "another standing"
                )
                continue
            standings.setdefault((owner, claim), {})[op] = m
            continue
        m = es.EDGE.match(text)
        if m is None or m.group("kind") == es.SELF:
            problems.append(f"{where}: ⟦{owner}⟧ {text}: an edge line or a standing")
            continue
        same = (owner, m.group("kind"), m.group("target") or "", m.group("evidence"))
        lines = taken if op == edits.RETRACT else given
        if same in lines:
            problems.append(
                f"{where}: ⟦{owner}⟧ {text}: a line is narrowed once: name it in "
                "`narrow` once"
            )
        if op == edits.RETRACT and (owner, text) not in on:
            problems.append(
                f"{where}: ⟦{owner}⟧ {text}: not a line on the circle, as "
                "edges.md holds it"
            )
        lines[same] = (owner, text)
    for same, (owner, text) in taken.items():
        if same not in given:
            problems.append(
                f"{where}: ⟦{owner}⟧ {text} is taken off and not given back: a line "
                "is narrowed, never deleted"
            )
            continue
        new = given[same][1]
        problems += [f"{where}: {p}" for p in _narrowed(text, new, owner)]
        # a claim named at an about line's or an assumed line's source end
        # restates or assumes through it: only one of the circle's, never a
        # route for a claim off it; a plain uses line's names the parts it
        # leaves from, on a block already read by its parts alone
        own = es.EDGE.match(new)
        assert own is not None
        if (
            own.group("kind") == "uses"
            and "assumed" not in (own.group("flag") or "").split()
        ):
            if _tokens(own.group("own")) and owner not in placed:
                problems.append(
                    f"{where}: ⟦{owner}⟧ {new}: names a part its line leaves from, "
                    f"where {owner}'s lines name none: placing a block's lines is a "
                    "word on every line of it and the parts each part writes, "
                    "`place --ask`'s; keep this end as it is"
                )
            continue
        held = es.claim_handles(sheet, owner)
        for h in _tokens(own.group("own")) - {es.RESTATES_NONE}:
            if (owner, es.norm(held.get(h, ""))) not in members:
                problems.append(
                    f"{where}: ⟦{owner}⟧ {new}: [{h}] is no claim of the circle: a "
                    "source end names only the circle's claims it restates for, or "
                    f"none ([{es.RESTATES_NONE}])"
                )
    for same, (owner, text) in given.items():
        if same not in taken:
            problems.append(
                f"{where}: ⟦{owner}⟧ {text} takes no line off: a line given must "
                "replace one on the circle with the same kind, target and quote"
            )
    for (owner, claim), ops in standings.items():
        old, new = ops.get(edits.RETRACT), ops.get(edits.ADD)
        if (
            old is None
            or new is None
            or old.group("status") != "restated"
            or new.group("status") != "unsupported"
        ):
            problems.append(
                f'{where}: ⟦{owner}⟧ ⊢ "{claim}": a standing may go only from '
                "restated to unsupported, where no original exists"
            )
    for legend in fragment.legend:
        m = es.LEGEND.match(legend)
        if m is None:
            problems.append(f"{where}: {legend.strip()}: {es.LEGEND_FORM}")
            continue
        target, label, _, role = m.groups()
        if target not in a.targets:
            problems.append(
                f"{where}: {legend.strip()}: a part is added only on a block a line "
                "on the circle points at"
            )
        if role not in CONTENT_ROLES:
            problems.append(
                f"{where}: {legend.strip()}: a part takes "
                f"{', '.join(f'({r})' for r in CONTENT_ROLES)}, never a claim: a "
                "claim is named by its handle"
            )
        if (target, label) in sheet.legend:
            problems.append(
                f"{where}: {legend.strip()}: {target} has [{label}] already"
            )
        words = es.norm(m.group(3))
        if any(
            es.norm(claim) in words
            for claim in es.claim_handles(sheet, target).values()
        ):
            problems.append(
                f"{where}: {legend.strip()}: the words hold a claim of {target}: a "
                "part holds only what a line relies on as content; where the words "
                "cannot be parted from the claim, answer `real`"
            )
    answered = {(o, t) for o, t in taken.values()} | set(kept)
    for o, t in kept:
        if (o, t) not in on:
            problems.append(
                f"{where}: ⟦{o}⟧ = {t}: not a line on the circle, as edges.md holds it"
            )
    if not taken and not standings:
        problems.append(
            f"{where}: `{REFINED}` narrows no line: narrow an end (`narrow`), or "
            f"answer `{REAL}`"
        )
    for line in a.lines:
        if line.coarse and not line.pinned and (line.owner, line.text) not in answered:
            problems.append(
                f"{where}: ⟦{line.owner}⟧ {line.text} shows a candidate end: narrow "
                "it (`narrow`), or keep it (`keep`)"
            )
    return problems


def _narrowed(old: str, new: str, owner: str) -> list[str]:
    """Why `new` is no narrowing of `old`: it differs beyond its
    brackets, changes nothing, or widens an end. A target end naming none
    may come to name parts or claims, one naming some a part of those; an
    about line's own end naming none may come to name claims or none
    (`[-]`), one naming some a part of those, or none; a uses line's may
    come to name the parts it leaves from, one naming some a part of
    those, never none."""
    o, n = es.EDGE.match(old), es.EDGE.match(new)
    assert o is not None and n is not None
    said = f"⟦{owner}⟧ {old} → {new}"
    for group in ("kind", "target", "mark", "flag", "evidence"):
        if (o.group(group) or "") != (n.group(group) or ""):
            return [f"{said}: its {group} changed: only its brackets may narrow"]

    problems = []
    old_t, new_t = _tokens(o.group("parts")), _tokens(n.group("parts"))
    old_o, new_o = _tokens(o.group("own")), _tokens(n.group("own"))
    if (old_t, old_o) == (new_t, new_o):
        problems.append(f"{said}: names what it named: give the narrowed end")
    if old_t and not (new_t and new_t <= old_t):
        problems.append(
            f"{said}: its target end named [{', '.join(sorted(old_t))}]: it may "
            "name only some of those"
        )
    if o.group("kind") != "about" and es.RESTATES_NONE in new_o:
        problems.append(
            f"{said}: [{es.RESTATES_NONE}] is an about line's: a uses line's source "
            "end names the parts it leaves from"
        )
    elif old_o and not (new_o == {es.RESTATES_NONE} or (new_o and new_o <= old_o)):
        problems.append(
            f"{said}: its source end named [{', '.join(sorted(old_o))}]: it may "
            f"name only some of those, or none ([{es.RESTATES_NONE}])"
        )
    return problems


def _tokens(bracket: str | None) -> set[str]:
    return {t.strip() for t in (bracket or "").split(",") if t.strip()}


# --- the circles as a queue (docs/architecture.md § 2) -----------------------------

_NUMBERED = {
    "type": "array",
    "items": {"type": "string"},
}
REAL = Tool(
    "real",
    "File the task's circle real: every end names what the text relies on, "
    "and the circle stays. Accepted, it returns the next task.",
    {
        "type": "object",
        "required": ["task", "why", "proposal"],
        "properties": {
            "task": {"type": "string"},
            "why": {"type": "string"},
            "proposal": {
                "type": "string",
                "description": "what a person should change: the line you think "
                "is wrong, or what the paper must add, or where a block should "
                "be split",
            },
        },
    },
)
REFINED = Tool(
    "refined",
    "File the task's circle refined: the ends you name more exactly, written "
    "by code and checked at once. Accepted, it returns the next task.",
    {
        "type": "object",
        "required": ["task", "why"],
        "properties": {
            "task": {"type": "string"},
            "why": {
                "type": "string",
                "description": "what the text relies on, line by line",
            },
            "narrow": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["line"],
                    "properties": {
                        "line": {
                            "type": "string",
                            "description": "the line's number, as the task "
                            "numbers them (L1, L2, …)",
                        },
                        "target_end": {
                            **_NUMBERED,
                            "description": "the labels of the parts, or the "
                            "handles of the claims, its target end names; omit "
                            "to leave that end as it is",
                        },
                        "own_end": {
                            **_NUMBERED,
                            "description": "an about line: the handles of its "
                            'own block\'s claims it restates for, or ["-"] for '
                            "none; a uses line of a block whose lines name the "
                            "parts they leave from: the labels of its own "
                            "block's parts it leaves from; omit to leave that "
                            "end as it is",
                        },
                    },
                },
            },
            "keep": {
                **_NUMBERED,
                "description": "the numbers of the lines with a candidate end "
                "that relies on what it names as it stands",
            },
            "parts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["block", "label", "words", "role"],
                    "properties": {
                        "block": {
                            "type": "string",
                            "description": "the block a line on the circle points at",
                        },
                        "label": {"type": "string"},
                        "words": {
                            "type": "string",
                            "description": "the block's own words holding the "
                            "definition or hypothesis, never a claim",
                        },
                        "role": {"type": "string", "enum": list(CONTENT_ROLES)},
                    },
                },
            },
            "unsupported": {
                **_NUMBERED,
                "description": "the numbers of the circle's `restated` claims "
                "that restate no original (C1, C2, …)",
            },
        },
    },
)
TOOLS = (REFINED, REAL)


def _written_lines(sheet: str, owner: str) -> list[str]:
    """Every line an owner writes, as `sheet` holds it."""
    return edges_file.written_lines(sheet).get(owner, [])


class Circling:
    """The circles question on a kept state as a queue: a task a circle
    asked, while its lines stand on the working sheet; one step. Its lines
    are the edges writer's, an about line a description unless it names
    what it restates."""

    restating = False
    reads: tuple[queue.Read, ...] = ()
    by_graph = True
    serving = queue.Serving()

    def __init__(
        self,
        kept: edits.Kept,
        view: queries.View,
        asked: Sequence[AskedCircle],
        author: edits.Author,
        writer: Template,
    ) -> None:
        self.kept = kept
        self.view = view
        self.files = kept.directory
        self.sheet = edits.shown(kept, Layer.EDGES)
        self.asked = tuple(asked)
        self.author = author
        self.steps = (Circles(self, writer),)
        self._by_task = {f"k{a.key}": a for a in self.asked}

    def draw(self, working: Working) -> list[Task]:
        written = edges_file.written_lines(working.sheet)
        at = self.view.readings.at
        found = []
        for task, a in self._by_task.items():
            if any(line.text not in written.get(line.owner, []) for line in a.lines):
                continue
            first = a.circle.claims[0].node if a.circle.claims else ""
            found.append(Task(task, at.ident_of.get(first, first), {"circle": a.key}))
        return found

    def circle(self, task: Task) -> AskedCircle:
        """The task's circle, as it was asked."""
        return self._by_task[task.id]

    def render(self, task: Task, working: Working) -> str:
        """A circle as served: its head, its claims numbered, its lines
        numbered as the sheet holds them under their owners, with their
        candidate ends, and the blocks at their ends as pointers."""
        a = self.circle(task)
        shown = a.text.split("\n")
        lines = [
            "does each line on this circle name what the text relies on?",
            shown[0],
            "its claims:",
            *(
                f"C{n} {claim.strip()}"
                for n, claim in enumerate(shown[1 : 1 + len(a.claims)], 1)
            ),
            "its lines:",
            *(
                f"L{n} ⟦{line.owner}⟧ {line.text}"
                + (
                    "  — " + "; ".join(queries.COARSE_WORDS[c] for c in line.coarse)
                    if line.coarse
                    else ""
                )
                + ("  (a person's line, pinned: it stays)" if line.pinned else "")
                for n, line in enumerate(a.lines, 1)
            ),
        ]
        reads = working.reads
        ends = []
        for line in a.lines:
            ends.append(line.owner)
            m = es.EDGE.match(line.text)
            if m is not None and m.group("target"):
                ends.append(m.group("target"))
        pointers = []
        for handle in dict.fromkeys(ends):
            try:
                ident = reads.resolve(handle)
            except ReadError:  # a file or the paper's scope: no block
                continue
            pointers.append(f"⟦{reads.handle(ident)}⟧ {reads.section(ident)}")
        if pointers:
            lines.append("pointers: " + ", ".join(pointers))
        return "\n".join(lines) + "\n"

    def answered(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        """`tool`'s answer on the working state: a `real` filed as a
        `RealCircle`; a `refined` as the lines code writes from it, checked
        by the gate where `check`."""
        why = " ".join(str(args.get("why") or "").split())
        if not why:
            return Refused(("give the reason (why)",))
        a = self.circle(task)
        if tool == REAL.name:
            proposal = " ".join(str(args.get("proposal") or "").split())
            if not proposal:
                return Refused(("give the proposal: what a person should change",))
            real = RealCircle("", None, self.view.state.id, a.circle.key, why, proposal)
            return Accepted(judgments=JudgmentRecords(circles=(real,)))
        try:
            lines, keeps = _refined_lines(working.sheet, a, args)
        except CircleError as error:
            return Refused((str(error),))
        if not check:
            return Accepted(lines)
        problems = refined_problems(
            self.kept, a, lines, keeps, self.author, working.sheet
        )
        return Refused(tuple(problems)) if problems else Accepted(lines)


def _numbered(refs: Sequence[Any], prefix: str, count: int, what: str) -> list[int]:
    found = []
    for ref in refs:
        n = str(ref).strip().upper().removeprefix(prefix)
        if not n.isdigit() or not 1 <= int(n) <= count:
            raise CircleError(
                f"{what} {ref!r}: the circle's are {prefix}1 to {prefix}{count}"
            )
        found.append(int(n) - 1)
    return found


def _names(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise CircleError(f"{what}: one name at least, or omit it")
    return [str(x).strip() for x in value if str(x).strip()]


def with_ends(line: str, parts: Sequence[str] | None, own: Sequence[str] | None) -> str:
    """An edge line with its target end naming `parts` and its own end
    `own`, each None where it stays; everything else as written."""
    m = es.EDGE.match(line)
    if m is None:
        raise CircleError(f"{line!r} is no edge line")

    def bracket(names: Sequence[str] | None, held: str | None) -> str:
        if names is None:
            return f" [{held}]" if held is not None else ""
        return f" [{', '.join(names)}]"

    target = m.group("target")
    if parts is not None and target is None:
        raise CircleError(f"{line!r} has no target end to name")
    written = (
        f"→ {m.group('kind')}"
        + bracket(own, m.group("own"))
        + (f" {target}" if target else "")
        + bracket(parts, m.group("parts"))
        + (f" {m.group('mark')}" if m.group("mark") else "")
        + (m.group("flag") or "")
        + f' · "{m.group("evidence")}"'
        + (f" — {m.group('note')}" if m.group("note") is not None else "")
    )
    if parts is None and own is None and written != line:
        raise CircleError(f"{line!r} cannot be written again by its parts")
    return written


def _refined_lines(
    sheet: str, a: AskedCircle, args: Mapping[str, Any]
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """A `refined` answer as a fragment code writes on `sheet`: each part
    added, then under each owner each line narrowed taken off and given
    back with its brackets rewritten, and each `restated` claim made
    `unsupported`; and the lines kept, `(owner, line)`."""
    narrow = list(args.get("narrow") or [])
    legend = []
    for part in args.get("parts") or []:
        if not isinstance(part, Mapping):
            raise CircleError(f"parts: {part!r} is a block, a label, words and a role")
        words = " ".join(str(part.get("words") or "").split()).replace('"', "'")
        block, label = str(part.get("block") or ""), str(part.get("label") or "")
        role = str(part.get("role") or "")
        if not (block and label and words and role):
            raise CircleError("a part takes its block, label, words and role")
        legend.append(f'+ {block} [{label}]: "{words}" ({role})')
    by_owner: dict[str, list[str]] = {}
    for entry in narrow:
        if not isinstance(entry, Mapping):
            raise CircleError(f"narrow: {entry!r} is a line and the ends it names")
        (n,) = _numbered([entry.get("line")], "L", len(a.lines), "narrow")
        line = a.lines[n]
        parts = (
            None
            if entry.get("target_end") is None
            else _names(entry["target_end"], "target_end")
        )
        own = (
            None
            if entry.get("own_end") is None
            else _names(entry["own_end"], "own_end")
        )
        if parts is None and own is None:
            raise CircleError(
                f"narrow L{n + 1}: name its target end, its own end or both"
            )
        given = with_ends(line.text, parts, own)
        by_owner.setdefault(line.owner, []).extend(
            [f"{edits.RETRACT} {line.text}", f"{edits.ADD} {given}"]
        )
    for n in _numbered(
        args.get("unsupported") or [], "C", len(a.claims), "unsupported"
    ):
        owner, words = a.claims[n]
        held = [
            x
            for x in _written_lines(sheet, owner)
            if (m := es.SUPPORT.match(x))
            and es.norm(m.group("claim")) == es.norm(words)
        ]
        if not held:
            raise CircleError(f"C{n + 1}: its block holds no standing line for it")
        m = es.SUPPORT.match(held[0])
        assert m is not None
        handle = f" [{m.group('handle')}]" if m.group("handle") else ""
        claim = m.group("claim")
        by_owner.setdefault(owner, []).extend(
            [
                f"{edits.RETRACT} {held[0]}",
                f'{edits.ADD} ⊢ unsupported{handle} · "{claim}"',
            ]
        )
    keeps = tuple(
        (a.lines[n].owner, a.lines[n].text)
        for n in _numbered(args.get("keep") or [], "L", len(a.lines), "keep")
    )
    body = [*legend]
    for owner, lines in by_owner.items():
        body += [f"⟦{owner}⟧", *lines]
    return "\n".join(body), keeps


def refined_problems(
    kept: edits.Kept,
    a: AskedCircle,
    lines: str,
    keeps: Sequence[tuple[str, str]],
    author: edits.Author,
    sheet_text: str,
) -> list[str]:
    """What the circles' gate refuses in a refined answer's `lines` on
    `sheet_text`, the working state's sheet: its narrowing
    (`narrowing_problems`), a narrowing that would stand a claim argued
    (whether the paper argues a claim is the support search's, and a
    narrowing adds no support), then the edit as `author`'s edges edit
    would be checked."""
    if not lines.strip():
        return [
            f"circle {a.key}: `refined` narrows no line: narrow an end, or answer "
            "`real`"
        ]
    try:
        fragment = edits.parse_fragment(lines)
    except edits.EditError as error:
        return [f"circle {a.key}: {error}"]
    problems = narrowing_problems(es.parse(sheet_text), a, fragment, keeps)
    if problems:
        return problems
    try:
        argued = edges_file.stood_argued(sheet_text, fragment)
    except edges_file.FragmentError as error:
        return [f"circle {a.key}: {error}"]
    problems = [
        f"circle {a.key}: ⟦{owner}⟧ {line}: the narrowing would stand this claim "
        "argued, and a narrowing adds no support: name another end, or answer "
        f"`{REAL.name}`"
        for owner, op, line in argued
        if op == edits.ADD
    ]
    if problems:
        return problems
    try:
        text = edits.applied_fragment(sheet_text, fragment)
    except edits.EditError as error:
        return [str(error)]
    return list(edits.edit_problems(kept, Layer.EDGES, text, author))


class Circles:
    """The circles' one step: a circle served, answered `refined` or
    `real`."""

    kind = KIND
    stage = Stage.EDGES
    kickoff = KICKOFF
    tools = TOOLS

    def __init__(self, circling: Circling, writer: Template) -> None:
        self.circling = circling
        self.template = brief(writer)

    def render(self, task: Task, working: Working) -> str:
        return self.circling.render(task, working)

    def answer(
        self,
        task: Task,
        tool: str,
        args: Mapping[str, Any],
        working: Working,
        check: bool,
    ) -> Accepted | Refused:
        return self.circling.answered(task, tool, args, working, check)


def brief(writer: Template) -> Template:
    """The step's brief: the edges writer's (the lines' notation), then the
    circles'; one text, so its version binds both."""
    own = load_template(TEMPLATE)
    return Template(TEMPLATE, f"{writer.text}\n\n{own.text}")


@contextmanager
def circling(
    store: RecordStore, state: State, options: Mapping[str, Any]
) -> Iterator[Circling]:
    """The circles' queue on `state` (`queue.Run.factory`): the circles
    `options` names (`circles`, by their key's prefix), else each no word
    holds real, a word this run's own sessions filed not counted; its kept
    state opened under the profile `options` names; its lines checked as
    `model`'s."""
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
        asked = asked_circles(view, kept, options.get("circles") or (), own)
        yield Circling(kept, view, asked, author, writer)


# --- a circles run -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Said:
    """A circle a run settled: refined or real, why, and a real one's
    proposal."""

    circle: AskedCircle
    real: bool
    why: str
    proposal: str = ""


@dataclass(frozen=True, slots=True)
class Done:
    """A circles run (`runner.given.Outcome`), each circle it settled read."""

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
    """`run`, the circles' queue, asked of `hosts` through `runner` (None:
    what the store holds replayed); complete, the lines it narrowed given
    as `model`'s edges edit, shown no stale mark (`runner.given.ask`).
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
        raise CircleError(str(error)) from None
    with queue.opened(run) as found:
        assert isinstance(found, Circling)
        said = tuple(_said(found, u) for u in out.settled)
    return Done(out, said)


def _said(circling: Circling, u: queue.Used) -> Said:
    args = u.call.args
    return Said(
        circling.circle(u.task),
        u.call.tool == REAL.name,
        " ".join(str(args.get("why") or "").split()),
        " ".join(str(args.get("proposal") or "").split()),
    )


def _reason(circling: queue.Workflow, settled: Sequence[queue.Used]) -> str:
    """The edit's reason: each refined circle's, by its key."""
    assert isinstance(circling, Circling)
    return "; ".join(
        f"circle {s.circle.key}: {s.why}"
        for s in (_said(circling, u) for u in settled)
        if not s.real
    )


def price(store: RecordStore, model: str) -> queued.Price:
    """What the circles are expected to cost on `model`: their session's
    start and a circle's (`runner.queued.price`)."""
    try:
        return queued.price(store, TEMPLATE, model, FITTED)
    except queued.PriceError as error:
        raise CircleError(str(error)) from None
