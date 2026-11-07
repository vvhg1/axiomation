"""The open stale marks at a kept state.

An update of one session marks stale the owners its Δs ask. A mark is
the graph's doubt about one line (`Line`): an edge whose far end
changed, asked of its owner until an answer reads it on the new state
(`docs/model.md` § 5, "Stale marks"). The marks are computed here from the
kept states, never carried or stored:

- **what a record asks** is its `stale` and `lost` entries (the rule's
  question, as the update recorded it), each cause a line, kept where
  `asks` says the line can be wrong: where its far end's words changed;
  for a line a standing rests on, where its far end has a Δ of its own;
  for a `uses` line no reading's one hop holds, where its far end has a
  Δ or moved by rule; always, for a line the state no longer holds;
- **within a version** the latest record speaks: the version's last
  state's, those no answer cleared (`cleared_by`, which the record's own
  fold sets);
- **across versions** they accumulate, one mark per line at its latest
  `since`: an earlier version's mark goes on while its owner's node
  does, gives way to a later mark on the same line, and closes where a
  later version's record lists its owner as `reopened` (the next update's
  session re-read it) or `reread` (an answer of that version did);
- **a reread** (`axiomation reread`), a person's or an agent's filing at a
  state along the chain, clears every mark on its owner's node made at or
  before that state; a later update's mark on the node stands;
- **a reading** closes a mark: a `verified` judgment made at or after its
  `since` whose basis holds the line as its subject's own (`own_line`);
  it stays closed if the reading later lapses, since the line was read.

An `about` mark no standing rests on is a description (`MarkState`):
listed, in no gate. Nothing here writes but `reread`, a filing; a build
has no marks.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from typing import Any

from axiomation import derived, judgments, versions
from axiomation.questions import edge_sheet as es
from axiomation.questions.review_file import COLUMNS
from axiomation.records import (
    PERSON,
    REREAD,
    Aspect,
    BasisElement,
    BasisKind,
    JudgmentRecords,
    RecordStore,
    Reread,
    Subject,
    Verdict,
)
from axiomation.resolver import Filed
from axiomation.versions import State

#: A cause's edge as an update writes it: its source, its kind (the source
#: claims a line names after it, on a lost line), its target and the parts
#: it names, by that version's handles; a lost line runs on to its quote.
VIA = re.compile(
    r"^(?P<source>\S+) → (?P<kind>[\w-]+)(?: \[[^\]]+\])? (?P<target>[^\s\[]\S*)"
    r"(?: \[(?P<parts>[^\]]*)\])?"
)

#: The kind of a line no edge carries: a standing left resting on nothing.
STANDING = "standing"

#: The aspects a review session judges: only a reading of one can lapse in
#: a mark's place.
JUDGED = frozenset(a for column in COLUMNS.values() for a in column.judged)


class MarkState(StrEnum):
    """Where an open mark stands: in the gates and on the established walk,
    or a description, an `about` line no standing rests on, which no
    session judges and no gate asks."""

    GATE = "gate"
    DESCRIPTION = "description"


@dataclass(frozen=True, slots=True)
class Line:
    """What a mark doubts: an edge by its source's node, kind and target's
    node (a file or the paper's scope as itself; a deleted block as its
    cause names it), and the node it marks, its owner: the edge's source,
    or for a justification's support its target."""

    owner: str
    source: str
    kind: str
    target: str


@dataclass(frozen=True, slots=True)
class Mark:
    """An open stale mark: its owner's node and kept handle, the update's
    state that marked it, the causes that update recorded for its line,
    each `{why, from, via, root}` (`from` and `root` in kept handles, `via`
    in that version's; `root` null where no edit began it), the Δ
    classes of the edits they began at, by root, its line (`edge`, in kept
    handles) and where it stands."""

    node: str
    handle: str
    since: str
    causes: tuple[Mapping[str, Any], ...]
    edits: Mapping[str, str] = field(default_factory=dict)
    edge: str = ""
    line: Line | None = None
    state: MarkState = MarkState.GATE

    @property
    def why(self) -> str:
        """The causes by the edit each began at, each edit named once with
        its Δ; a cause that came the same way from several changed blocks
        (a group's members) once, naming them."""
        by_root: dict[str | None, dict[tuple[str, str | None], list[str]]] = {}
        for c in self.causes:
            held = by_root.setdefault(c["root"], {})
            froms = held.setdefault((c["why"], c["via"]), [])
            if c.get("from") and c["from"] not in froms:
                froms.append(c["from"])
        found = []
        for root, held in by_root.items():
            said = "; ".join(
                why
                + (f" (via {self.edge or via})" if self.edge or via else "")
                + (f", from {' and '.join(froms)}" if len(froms) > 1 else "")
                for (why, via), froms in held.items()
            )
            if root is not None:
                delta = f" (Δ {self.edits[root]})" if root in self.edits else ""
                said = f"from {root}'s edit{delta}: {said}"
            found.append(said)
        return "; ".join(found)

    def sheet_line(self) -> str:
        """The mark's line on a sheet, under its owner."""
        return es.mark_line(self.since, self.why)

    @property
    def gate(self) -> bool:
        return self.state is MarkState.GATE

    def data(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "handle": self.handle,
            "since": self.since,
            "edge": self.edge,
            "state": self.state.value,
            "causes": [dict(c) for c in self.causes],
        }


def _segments(chain: Sequence[State]) -> list[list[State]]:
    """The chain cut into its versions' runs, in order."""
    found: list[list[State]] = []
    for s in chain:
        if found and found[-1][-1].version == s.version:
            found[-1].append(s)
        else:
            found.append([s])
    return found


def _nodes(kept: State, handles: Mapping[str, str]) -> dict[str, str]:
    """Each handle of the state's export (`handles`, id → handle) to its
    node."""
    node_of = {b.block: b.node for b in kept.nodes} | dict(kept.groups)
    return {h: node_of[i] for i, h in handles.items() if i in node_of}


@dataclass(frozen=True, slots=True)
class Cleared:
    """A mark an answer closed: a reread, or a reading that read its line
    (`docs/model.md` § 5), and whether a person filed it."""

    mark: Mark
    reread: Reread | None
    by_person: bool
    reading: judgments.Read | None = None

    @property
    def by(self) -> str:
        """What closed it, by its id."""
        if self.reread is not None:
            return self.reread.id
        assert self.reading is not None
        return self.reading.judgment.id


def _open_causes(record: Mapping[str, Any], node: Mapping[str, str]) -> dict[str, str]:
    """Each owner's open causes in a record, stale and lost, by node, as
    one comparable text, the blocks they come from by node: a record names
    them by its own export's handles, which an edit may give otherwise."""
    found: dict[str, list[Any]] = {}
    for s in (*record.get("stale", []), *record.get("lost", [])):
        owner = node.get(s["handle"])
        if s.get("cleared_by") is None and owner is not None:
            found.setdefault(owner, []).extend(_renamed(s["causes"], node))
    return {n: json.dumps(c, sort_keys=True) for n, c in found.items()}


def _renamed(
    causes: Sequence[Mapping[str, Any]], name: Mapping[str, str]
) -> tuple[dict[str, Any], ...]:
    """`causes` with the handles of the blocks they come from (`from`,
    `root`) given by `name`, where it holds them, in their words too."""
    found = []
    for c in causes:
        renamed = {k: name.get(c[k], c[k]) for k in ("from", "root") if c.get(k)}
        why = c["why"]
        if c.get("from") and renamed["from"] != c["from"]:
            why = why.replace(f"{c['from']}'s change", f"{renamed['from']}'s change")
        found.append({**c, **renamed, "why": why})
    return tuple(found)


def _since(
    store: RecordStore,
    run: Sequence[State],
    record: Mapping[str, Any],
    node: Mapping[str, str],
) -> dict[str, str]:
    """Each owner's mark in a version's run, by node: the first state from
    which its causes stand as the run's last record has them (a Δ corrected
    within the version may add one), so a reread before that state never
    clears a cause it was not shown. The run's first state where none of
    its later states changed them."""
    now = _open_causes(record, node)
    since = dict.fromkeys(now, run[-1].id)
    for earlier, later in zip(reversed(run[:-1]), reversed(run[1:]), strict=True):
        outline = versions.export_outline(store, earlier)
        held = _open_causes(outline.update_record(), _nodes(earlier, outline.handles))
        for n in now:
            if since[n] == later.id and held.get(n) == now[n]:
                since[n] = earlier.id
    return since


def own_line(read: judgments.Read, line: Line, at: judgments.Seen) -> bool:
    """Whether a reading holds `line` as its subject's own (`holds_own`)."""
    j = read.judgment
    return holds_own(at, j.aspect, j.subject, j.basis, line)


def holds_own(
    at: judgments.Seen,
    aspect: Aspect,
    on: Subject,
    basis: Sequence[BasisElement],
    line: Line,
) -> bool:
    """Whether a subject's basis (a reading's, or the one hop a reading
    would rest on) holds `line` as the subject's own: the line's owner is
    the subject's node, or for `valid` J, one of J's members, or T; and the
    basis holds the edge. A line that enters the hop only as a qualifier of
    a hop node is a third block's, and is not."""
    if line.owner not in _own(at, aspect, on):
        return False
    edge = (line.kind, line.target)
    return any(
        e.kind is BasisKind.EDGE and e.name == line.source and e.edge == edge
        for e in basis
    )


def open_marks(
    store: RecordStore,
    kept: State,
    readings: judgments.Readings | None = None,
    reader: judgments.Reader | None = None,
) -> tuple[Mark, ...]:
    """The stale marks open at `kept`, earlier versions' first, each
    version's in its record's order: the gate's and the descriptions.
    `readings` are `kept`'s, read here where not given, by `reader` where
    given (one a caller keeps while the kept states stand)."""
    return folded(store, kept, readings, reader)[0]


def cleared_marks(
    store: RecordStore,
    kept: State,
    readings: judgments.Readings | None = None,
    reader: judgments.Reader | None = None,
) -> tuple[Cleared, ...]:
    """The marks a reread or a reading along `kept`'s chain closed, each
    with the first answer that did."""
    return folded(store, kept, readings, reader)[1]


def _line(cause: Mapping[str, Any], owner: str, node: Mapping[str, str]) -> Line:
    """A cause's line, by node where its version's handles (`node`) name
    one."""
    m = VIA.match(cause.get("via") or "")
    if m is None:
        return Line(owner, owner, STANDING, owner)
    source, target = m["source"], m["target"]
    # the owner is the edge's source, or a support cause's target
    return Line(owner, node.get(source, source), m["kind"], node.get(target, target))


def _edge_text(cause: Mapping[str, Any], name: Mapping[str, str]) -> str:
    """A cause's edge in kept handles (`name`), its parts as written."""
    m = VIA.match(cause.get("via") or "")
    if m is None:
        return ""
    parts = f" [{m['parts']}]" if m["parts"] else ""
    source, target = m["source"], m["target"]
    return f"{name.get(source, source)} → {m['kind']} {name.get(target, target)}{parts}"


class _Asks:
    """A version's run as the rule that keeps a mark reads it (`asks`): its
    last record, its handles' nodes, the step from the version before into
    its first state (None for the first version), and its last state."""

    def __init__(
        self,
        reader: judgments.Reader,
        record: Mapping[str, Any],
        node: Mapping[str, str],
        before: State | None,
        first: State,
        last: State,
    ) -> None:
        self.record = record
        self.node = node
        self.step = None if before is None else reader.step(before, first)
        self.at = reader.seen(last)
        self.own_delta = {d["handle"] for d in record.get("deltas", [])}
        self.moved = {r["handle"] for r in record.get("moved_by_rule", [])}

    @cached_property
    def held(self) -> frozenset[tuple[str, str, str]]:
        """The edges, by source node, kind and target, that the one hop of
        a subject the state offers and a session judges holds: where a
        reading can lapse."""
        at = self.at
        found: set[tuple[str, str, str]] = set()
        for aspect, s in judgments.offered(at):
            if aspect not in JUDGED:
                continue
            nodes = [s.node] + ([s.target] if s.target is not None else [])
            for e in judgments.one_hop(at, aspect, nodes, s.file):
                if e.kind is BasisKind.EDGE and e.edge is not None:
                    found.add((e.name, *e.edge))
        return frozenset(found)

    @cached_property
    def edges(self) -> frozenset[tuple[str, str, str]]:
        """The state's edges, by source node, kind and target."""
        return frozenset(
            (source, kind, target)
            for source, kind, target, _ in self.at.edge_ids.values()
        )

    @cached_property
    def restating(self) -> frozenset[str]:
        """The nodes holding a `restated` standing."""
        at = self.at
        return frozenset(
            at.node_of[s["block"]]
            for s in at.export.get("standings", [])
            if s["status"] == "restated" and s["block"] in at.node_of
        )

    @cached_property
    def claims(self) -> frozenset[str]:
        """The nodes holding a standing's claim."""
        return frozenset(n for n, _ in self.at.standings)

    def words_changed(self, far: str) -> bool:
        """Whether the edit changed `far`'s words (a group's, any member's):
        the lapse's own test (`judgments.texts_changed`); a node new in the
        version, or a version with none before it, did."""
        if self.step is None:
            return True
        parent, child, t = self.step.parent, self.step.child, self.step.t
        nodes = child.members.get(far, (far,))
        if any(m not in parent.text_of or m not in child.ident_of for m in nodes):
            return True
        return bool(judgments.texts_changed(t, parent, child, list(nodes)))

    def asks(self, cause: Mapping[str, Any], line: Line) -> bool:
        """Whether the record's cause asks a line that can be wrong
        (`docs/model.md` § 5, "Stale marks")."""
        if line.kind == STANDING:
            return True
        if (line.source, line.kind, line.target) not in self.edges:
            return True  # retracted, or lost: the line itself changed
        origin = cause.get("from")
        far = self.node.get(origin or "")
        if far is None or self.words_changed(far):
            return True
        carries = origin in self.own_delta
        if carries_standing(line, self.restating, self.claims):
            return carries
        if line.kind == "uses" and (carries or origin in self.moved):
            return (line.source, line.kind, line.target) not in self.held
        return False


def carries_standing(
    line: Line, restating: frozenset[str], claims: frozenset[str]
) -> bool:
    """Whether a standing rests on the line: a `justifies` line, or an
    `about` line out of a block holding a `restated` standing onto one
    holding a claim (`restating`, `claims`: those nodes)."""
    if line.kind == "justifies":
        return True
    return line.kind == "about" and line.source in restating and line.target in claims


def _state(line: Line, at: judgments.Seen) -> MarkState:
    """Where a mark on `line` stands at `at`: an `about` line no standing
    rests on, and not one that generalizes a narrower premise, is a
    description."""
    if line.kind != "about":
        return MarkState.GATE
    restating = frozenset(
        at.node_of[s["block"]]
        for s in at.export.get("standings", [])
        if s["status"] == "restated" and s["block"] in at.node_of
    )
    if carries_standing(line, restating, frozenset(n for n, _ in at.standings)):
        return MarkState.GATE
    generalizing = {at.edge_ids[e["id"]][:3] for e in at.edges if e.get("generalizes")}
    if (line.source, line.kind, line.target) in generalizing:
        return MarkState.GATE
    return MarkState.DESCRIPTION


def folded(
    store: RecordStore,
    kept: State,
    readings: judgments.Readings | None = None,
    reader: judgments.Reader | None = None,
    unfiled: Collection[str] = (),
) -> tuple[tuple[Mark, ...], tuple[Cleared, ...]]:
    """The marks open at `kept`, and those a reread or a reading closed; a
    reread filed with an answer in `unfiled` read as never filed."""
    reader = reader or judgments.kept_reader(store)
    if readings is None:
        readings = judgments.readings(store, kept, reader)
    found: dict[Line, Mark] = {}
    # each block a mark names shown by its kept handle (`docs/model.md` § 7)
    names = versions.handles(store, kept).kept
    chain = versions.chain(store, kept)
    before: State | None = None
    for n, run in enumerate(_segments(chain)):
        last = run[-1]
        outline = versions.export_outline(store, last)
        record = outline.update_record()
        node = _nodes(last, outline.handles)
        kept_name = {h: names.get(n, h) for h, n in node.items()}
        edits: dict[str, list[str]] = {}
        for d in record.get("deltas", []):
            root = d.get("handle") or d["block"]
            classes = edits.setdefault(kept_name.get(root, root), [])
            if d["class"] not in classes:
                classes.append(d["class"])
        edit_of = {h: ", ".join(c) for h, c in edits.items()}
        if n:
            closed = {
                node.get(r["handle"])
                for r in (*record.get("reopened", []), *record.get("reread", []))
            }
            alive = set(node.values())
            found = {
                k: m
                for k, m in found.items()
                if m.node in alive and m.node not in closed
            }
        entries = [
            *((s, False) for s in record.get("stale", [])),
            # an edge the update lost and drew nothing in place of marks its
            # source as a stale mark does, its causes beside the rule's
            *((s, True) for s in record.get("lost", [])),
        ]
        if any(s.get("cleared_by") is None for s, _ in entries):
            asks = _Asks(
                reader,
                record,
                node,
                before,
                run[0],
                last,
            )
            # the run and its last record never change: worked out once
            since = derived.held(
                f"since-{run[0].id}-{last.id}",
                functools.partial(_since, store, run, record, node),
            )
            lines: dict[Line, list[Mapping[str, Any]]] = {}
            handle_of: dict[str, str] = {}
            for s, lost in entries:
                owner = node.get(s["handle"])
                if s.get("cleared_by") is not None or owner is None:
                    continue
                handle_of.setdefault(owner, s["handle"])
                for c in s["causes"]:
                    line = _line(c, owner, node)
                    if lost or asks.asks(c, line):
                        lines.setdefault(line, []).append(c)
            for line, causes in lines.items():
                found[line] = Mark(
                    line.owner,
                    handle_of[line.owner],
                    since.get(line.owner, run[0].id),
                    _renamed(causes, kept_name),
                    edit_of,
                    _edge_text(causes[0], kept_name),
                    line,
                )
        before = last
    at = readings.at
    named = [
        dataclasses.replace(
            m,
            handle=names.get(m.node, m.handle),
            state=_state(m.line, at) if m.line is not None else MarkState.GATE,
        )
        for m in found.values()
    ]
    order = {s.id: i for i, s in enumerate(chain)}
    rereads = [
        r
        for r in store.judgment_records().rereads
        if r.state in order and r.answer not in unfiled
    ]
    verified = sorted(
        (
            r
            for r in readings.reads
            if r.judgment.verdict is Verdict.VERIFIED and r.judgment.state in order
        ),
        key=lambda r: (order[r.judgment.state], r.order),
    )
    answers = store.authoritative() if rereads or verified else {}
    still: list[Mark] = []
    cleared: list[Cleared] = []
    for m in named:
        by = next(
            (
                r
                for r in rereads
                if r.node == m.node and order[m.since] <= order[r.state]
            ),
            None,
        )
        if by is not None:
            answer = answers.get(by.answer or "")
            cleared.append(Cleared(m, by, answer is not None and answer.host == PERSON))
            continue
        read = (
            None
            if m.line is None
            else next(
                (
                    r
                    for r in verified
                    if order[m.since] <= order[r.judgment.state]
                    and own_line(r, m.line, at)
                ),
                None,
            )
        )
        if read is not None:
            cleared.append(Cleared(m, None, read.author.person, read))
            continue
        still.append(m)
    return tuple(still), tuple(cleared)


@dataclass(frozen=True, slots=True)
class Unread:
    """What an update carried unread from one edit (`docs/model.md` § 5):
    the edited block it began at (its kept handle; None for the update
    whole, where no edit began the move, a retraction), the update's
    state, the edit's Δ classes, and the blocks the rule moved that
    neither a Δ of their own nor a verified reading (`read_nodes`) has
    read since, by node."""

    edit: str | None
    since: str
    classes: str
    nodes: tuple[str, ...]


def unread_rings(
    store: RecordStore,
    kept: State,
    readings: judgments.Readings | None = None,
    reader: judgments.Reader | None = None,
) -> tuple[Unread, ...]:
    """What the updates along `kept`'s chain carried unread, an item per
    update and edit, in order: each block a record moved by the rule
    (`moved_by_rule`, the version's last record speaking), until a later
    version's record gives it a Δ of its own, a `verified` reading made at
    or after the update holds it as its subject's own (`read_nodes`), or
    its node ends. A later update that does not read it leaves it; an
    update per stage moves nothing unread."""
    names = versions.handles(store, kept).kept
    found: list[tuple[str | None, str, str, list[str]]] = []
    for n, run in enumerate(_segments(versions.chain(store, kept))):
        outline = versions.export_outline(store, run[-1])
        record = outline.update_record()
        node = _nodes(run[-1], outline.handles)
        kept_name = {h: names.get(n, h) for h, n in node.items()}
        if n:
            alive = set(node.values())
            read = {
                node.get(d["handle"])
                for d in record.get("deltas", [])
                if d.get("by") != "rule"
            }
            found = [
                (
                    edit,
                    since,
                    classes,
                    [x for x in held if x in alive and x not in read],
                )
                for edit, since, classes, held in found
            ]
        classes: dict[str, list[str]] = {}
        for d in record.get("deltas", []):
            held = classes.setdefault(d.get("handle") or d["block"], [])
            if d["class"] not in held:
                held.append(d["class"])
        moved: dict[str | None, list[str]] = {}
        for r in record.get("moved_by_rule", []):
            owner = node.get(r["handle"])
            if owner is None:
                continue
            for root in dict.fromkeys(c["root"] for c in r["causes"]) or (None,):
                held = moved.setdefault(root, [])
                if owner not in held:
                    held.append(owner)
        found += [
            (
                None if root is None else kept_name.get(root, root),
                run[0].id,
                ", ".join(classes.get(root or "", [])),
                held,
            )
            for root, held in moved.items()
        ]
    reader = reader or judgments.kept_reader(store)
    if readings is None:
        readings = judgments.readings(store, kept, reader)
    chain = versions.chain(store, kept)
    order = {s.id: i for i, s in enumerate(chain)}
    # each block a verified reading read, at the latest state one did; a
    # group's members as they stood when it was read
    read_at: dict[str, int] = {}
    for r in readings.reads:
        if r.judgment.verdict is Verdict.VERIFIED and r.judgment.state in order:
            at = order[r.judgment.state]
            for n in read_nodes(reader.seen(chain[at]), r):
                read_at[n] = max(at, read_at.get(n, at))
    found = [
        (edit, since, classes, [n for n in held if read_at.get(n, -1) < order[since]])
        for edit, since, classes, held in found
    ]
    return tuple(
        Unread(edit, since, classes, tuple(held))
        for edit, since, classes, held in found
        if held
    )


def read_nodes(at: judgments.Seen, read: judgments.Read) -> set[str]:
    """The blocks a reading reads as its subject's own (`holds_own`)."""
    return _own(at, read.judgment.aspect, read.judgment.subject)


def _own(at: judgments.Seen, aspect: Aspect, on: Subject) -> set[str]:
    """A subject's own blocks: its node, a group's members, and for `valid`
    J, T."""
    own = {on.node, *at.members.get(on.node, ())}
    if aspect is Aspect.VALID and on.target is not None:
        own.add(on.target)
    return own


def by_owner(marks: Sequence[Mark]) -> dict[str, list[str]]:
    """The marks' lines by their owners' handles, as a sheet shows them."""
    lines: dict[str, list[str]] = {}
    for m in marks:
        lines.setdefault(m.handle, []).append(m.sheet_line())
    return lines


def reread(
    store: RecordStore,
    kept: State,
    nodes: Sequence[str],
    reason: str,
    author: judgments.Author = judgments.Author(),  # noqa: B008 - frozen
) -> Filed:
    """A person's or an agent's word that it read each owner (`nodes`, at
    `kept`) against what changed, and its lines stand: one filing, a reread
    per owner, which clears every mark on it made at or before `kept`. It
    pins nothing and makes no state. Refused without a reason, or where an
    owner holds no open mark: a reread is shown what it clears."""
    if not reason.strip():
        raise judgments.JudgmentError("a reread gives its reason")
    marked = {m.node for m in open_marks(store, kept)}
    unmarked = [n for n in nodes if n not in marked]
    if unmarked:
        raise judgments.JudgmentError(
            f"{', '.join(unmarked)} holds no open mark at state {kept.id}: a reread "
            "clears the marks it was shown (`axiomation open --kind stale`)"
        )
    records = tuple(Reread("", None, kept.id, n, reason) for n in dict.fromkeys(nodes))
    return judgments.file_records(
        store, REREAD, JudgmentRecords(rereads=records), author
    )
