"""A graph edit's classes, per layer.

A graph edit, a person's or an agent's, is a change between two assembled
states of one version, not between two texts: each record compared by what
it says, never by its id, since ids differ between answers. Its classes
stand beside the text classes (`identity.BlockClass`); what each re-asks is
`docs/model.md` § 5's.

Per layer, what is compared, and the classes:

- **cut:** blocks by `(id, hash)`, the changed ones joined where their
  characters overlap: split, merged, moved boundary; added, removed when
  a block overlaps none;
- **roles:** per unchanged block, its roles and segment starts: changed;
- **groups:** by kind and members: added, removed, membership changed (a
  changed kind is a removal and an addition);
- **edges:** by source, kind and target; parts by their entries' words
  and roles, never their labels; the where-used, generalizes and assumed
  flags, the partial and the mark: added, retracted, parts changed, flags
  changed;
- **standings:** by block and claim words: added, retracted, status (or
  its declaration) changed;
- **frame:** the block set: added, retracted.

Each record counts: a duplicate dropped on one side is a retraction.
Sections have no layer: an imposed section is an added heading block, a
text class.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from axiomation.cutter.model import Block, Cut
from axiomation.exchange import named_ends_key
from axiomation.records import EdgeRecords


class Layer(StrEnum):
    CUT = "cut"
    ROLES = "roles"
    GROUPS = "groups"
    EDGES = "edges"
    STANDINGS = "standings"
    FRAME = "frame"


class Change(StrEnum):
    SPLIT = "split"
    MERGED = "merged"
    MOVED_BOUNDARY = "moved-boundary"
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"
    MEMBERSHIP_CHANGED = "membership-changed"
    RETRACTED = "retracted"
    PARTS_CHANGED = "parts-changed"
    #: The same edge, its where-used, generalizes or assumed flag, its
    #: partial or its mark changed: where-used and the mark are read by what
    #: the edge re-asks (`docs/model.md` § 5; a `[position]` mark),
    #: generalizes by the gate (§ 10), assumed by whether the claim is
    #: established (§ 9), the partial by the claim's completeness.
    FLAGS_CHANGED = "flags-changed"
    STATUS_CHANGED = "status-changed"


@dataclass(frozen=True, slots=True)
class GraphState:
    """One assembled state of a version: its corrected cut, with roles and
    groups, and one answer's edge records."""

    cut: Cut
    records: EdgeRecords


@dataclass(frozen=True, slots=True)
class GraphChange:
    """`before` and `after` are the ids on each side: blocks for the cut,
    roles and frame, groups for groups, records for edges and standings."""

    layer: Layer
    change: Change
    before: tuple[str, ...]
    after: tuple[str, ...]


def graph_changes(before: GraphState, after: GraphState) -> list[GraphChange]:
    """Every difference in what the graph says between two states of one
    version, layer by layer in the list's order; an edge's evidence and a
    record's note are the writer's words for it, not compared."""
    return [
        *_cut(before.cut, after.cut),
        *_roles(before.cut, after.cut),
        *_groups(before.cut, after.cut),
        *_edges(before.records, after.records),
        *_standings(before.records, after.records),
        *_frame(before.records, after.records),
    ]


def _cut(before: Cut, after: Cut) -> list[GraphChange]:
    """The blocks only one side holds by `(id, hash)`, joined where their
    characters overlap: one into several is a split, several into one a
    merge, any other overlap a moved boundary."""
    held_after = {(b.id, b.hash) for b in after.blocks}
    held_before = {(b.id, b.hash) for b in before.blocks}
    gone = [b for b in before.blocks if (b.id, b.hash) not in held_after]
    new = [b for b in after.blocks if (b.id, b.hash) not in held_before]
    partners: dict[int, set[int]] = defaultdict(set)
    for i, one in enumerate(gone):
        for j, other in enumerate(new):
            if _overlap(one, other):
                partners[i].add(j)
    # components of the overlap graph, in `before`'s order
    seen_left: set[int] = set()
    seen_right: set[int] = set()
    out = []
    for start in range(len(gone)):
        if start in seen_left:
            continue
        left, right, queue = {start}, set(), [("l", start)]
        while queue:
            side, n = queue.pop()
            if side == "l":
                for j in partners[n] - right:
                    right.add(j)
                    queue.append(("r", j))
            else:
                for i, js in partners.items():
                    if n in js and i not in left:
                        left.add(i)
                        queue.append(("l", i))
        seen_left |= left
        seen_right |= right
        ids_before = tuple(gone[i].id for i in sorted(left))
        ids_after = tuple(new[j].id for j in sorted(right))
        if not right:
            change = Change.REMOVED
        elif len(left) == 1 and len(right) > 1:
            change = Change.SPLIT
        elif len(left) > 1 and len(right) == 1:
            change = Change.MERGED
        else:
            change = Change.MOVED_BOUNDARY
        out.append(GraphChange(Layer.CUT, change, ids_before, ids_after))
    out += [
        GraphChange(Layer.CUT, Change.ADDED, (), (new[j].id,))
        for j in range(len(new))
        if j not in seen_right
    ]
    return out


def _overlap(one: Block, other: Block) -> bool:
    return any(
        a.file == b.file and a.start < b.end and b.start < a.end
        for a in one.owned
        for b in other.owned
    )


def _roles(before: Cut, after: Cut) -> list[GraphChange]:
    """A block both sides hold unchanged, whose roles or segment starts
    differ."""
    held = {(b.id, b.hash): b for b in after.blocks}
    out = []
    for block in before.blocks:
        other = held.get((block.id, block.hash))
        if other is not None and _role_shape(block) != _role_shape(other):
            out.append(
                GraphChange(Layer.ROLES, Change.CHANGED, (block.id,), (other.id,))
            )
    return out


def _role_shape(block: Block) -> tuple[object, ...]:
    return (block.roles, tuple((s.starts, s.role) for s in block.segments))


def _groups(before: Cut, after: Cut) -> list[GraphChange]:
    """By kind and members: an equal group is no change; a group of one
    kind sharing a member with one on the other side changed its
    membership; else it was removed or added."""
    same = {(g.kind, g.blocks) for g in after.groups}
    gone = [g for g in before.groups if (g.kind, g.blocks) not in same]
    same = {(g.kind, g.blocks) for g in before.groups}
    new = [g for g in after.groups if (g.kind, g.blocks) not in same]
    out = []
    for group in gone:
        match = next(
            (
                g
                for g in new
                if g.kind is group.kind and set(g.blocks) & set(group.blocks)
            ),
            None,
        )
        if match is None:
            out.append(GraphChange(Layer.GROUPS, Change.REMOVED, (group.id,), ()))
        else:
            new.remove(match)
            out.append(
                GraphChange(
                    Layer.GROUPS, Change.MEMBERSHIP_CHANGED, (group.id,), (match.id,)
                )
            )
    out += [GraphChange(Layer.GROUPS, Change.ADDED, (), (g.id,)) for g in new]
    return out


def _edges(before: EdgeRecords, after: EdgeRecords) -> list[GraphChange]:
    """By what each edge says: its source, kind and target; its parts, and
    those of its source it leaves from, by their entries' words and roles,
    never their labels, which each answer picks; the claims it names, by
    their words; its where-used,
    generalizes and assumed flags, its partial and its mark. Each record counts, so a
    duplicate dropped on one side is a retraction."""

    def parts(records: EdgeRecords):
        entries = {(e.block, e.label): e for e in records.entries}

        def named(block: str, labels) -> tuple[object, ...]:
            found = []
            for label in labels:
                entry = entries.get((block, label))
                found.append(
                    (label,)
                    if entry is None
                    else (" ".join(entry.words.split()), entry.role, entry.standard)
                )
            return tuple(sorted(found, key=repr))

        def of(edge) -> tuple[object, ...]:
            return (
                named(edge.target.name, edge.parts),
                named(edge.source, edge.source_parts),
            )

        return of

    parts_before, parts_after = parts(before), parts(after)

    def target(e) -> tuple[object, ...]:
        return (e.source, e.kind, e.target)

    def says(e, of) -> tuple[object, ...]:
        return (
            *target(e),
            of(e),
            named_ends_key(e.claims, e.source_claims, e.restates_none),
            e.where_used,
            e.generalizes,
            e.assumed,
            e.partial,
            e.mark,
        )

    new = list(after.edges)
    gone = []
    for edge in before.edges:
        same = next(
            (e for e in new if says(e, parts_after) == says(edge, parts_before)), None
        )
        if same is None:
            gone.append(edge)
        else:
            new.remove(same)
    out = []
    for edge in gone:
        match = next((e for e in new if target(e) == target(edge)), None)
        if match is None:
            out.append(GraphChange(Layer.EDGES, Change.RETRACTED, (edge.id,), ()))
            continue
        new.remove(match)
        change = (
            Change.PARTS_CHANGED
            if parts_before(edge) != parts_after(match)
            else Change.FLAGS_CHANGED
        )
        out.append(GraphChange(Layer.EDGES, change, (edge.id,), (match.id,)))
    out += [GraphChange(Layer.EDGES, Change.ADDED, (), (e.id,)) for e in new]
    return out


def _standings(before: EdgeRecords, after: EdgeRecords) -> list[GraphChange]:
    """By block and claim words (whitespace aside); each record counts."""

    def claim(s) -> tuple[str, str]:
        return (s.block, " ".join(s.claim.split()))

    new = list(after.standings)
    out = []
    for standing in before.standings:
        other = next((s for s in new if claim(s) == claim(standing)), None)
        if other is None:
            out.append(
                GraphChange(Layer.STANDINGS, Change.RETRACTED, (standing.id,), ())
            )
            continue
        new.remove(other)
        if (other.status, other.declared) != (standing.status, standing.declared):
            out.append(
                GraphChange(
                    Layer.STANDINGS, Change.STATUS_CHANGED, (standing.id,), (other.id,)
                )
            )
    out += [GraphChange(Layer.STANDINGS, Change.ADDED, (), (s.id,)) for s in new]
    return out


def _frame(before: EdgeRecords, after: EdgeRecords) -> list[GraphChange]:
    was = [f.block for f in before.frame]
    now = [f.block for f in after.frame]
    return [
        *(
            GraphChange(Layer.FRAME, Change.RETRACTED, (b,), ())
            for b in was
            if b not in now
        ),
        *(
            GraphChange(Layer.FRAME, Change.ADDED, (), (b,))
            for b in now
            if b not in was
        ),
    ]
