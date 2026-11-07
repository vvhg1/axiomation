"""The closure, the one rule: what an edit to a
block reaches over a graph's edges.

The product reads its own graph from its records (`from_records`): the
corrected cut with its roles and groups, and one answer's edge records;
and a kept state's from its export (`from_export`).

`impact_reach` is the rule walked from the blocks an update changed,
gated by the classes of their Δ lines, so `unchanged` and `reworded` close
a change where they stand.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from axiomation.cutter.model import Cut
from axiomation.records import EdgeRecords, TargetKind

MEANING_CONTENT = ("definition", "assumption")
SCOPE_PAPER = "scope:paper"


@dataclass(frozen=True)
class Link:
    """One edge as the closure reads it. `target` is a block or group, or
    `scope:paper` or `file:<path>`; `content` the role of what the edge
    relies on in its target (`justification` for a group, empty for a scope
    or a file). `parts` and `flag` are for printing."""

    source: str
    kind: str
    target: str
    content: str
    where_used: bool = False
    parts: tuple[str, ...] = ()
    flag: str = ""


@dataclass(frozen=True)
class EdgeGraph:
    blocks: tuple[str, ...]  # in the paper's order
    groups: dict[str, tuple[str, ...]]
    links: tuple[Link, ...]
    ambient: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Reached:
    handle: str
    via: Link  # the edge the change came through
    content: str  # the role of what that edge relies on in its target, or the reason
    changed: bool  # meaning changed without asking, so the walk goes on from it
    depth: int


@dataclass(frozen=True)
class Step:
    """What one changed block does to its neighbours under the rule."""

    goes_on: list[tuple[str, Link, str]]  # (block, edge, content): meaning changes
    asked: list[tuple[str, Link, str]]  # asked their bounded question; the chain stops
    support: list[tuple[str, str]]  # (block whose support is recomputed, by what)


@dataclass
class Reach:
    start: str
    reached: list[Reached]  # in the order the walk found them
    support: list[tuple[str, str]]


def rule(
    graph: EdgeGraph,
    changed: str,
    edited: bool = False,
    ambient: frozenset[str] = frozenset(),
) -> Step:
    """The closure, in one place: given a block
    whose meaning changed, what happens next. `edited` marks the block the
    edit touched (its text changed, so its edges are found again), against
    one whose meaning the closure moved.

    - A uses edge onto it relying on definition or assumption content: the
      source's meaning changes without asking, and the walk goes on from it.
    - A uses edge onto a claim's conclusion, an about edge, a justifies
      edge: the source is asked its bounded question, and the chain stops.
    - A qualifies edge from it: the target's meaning changes (a change to
      the assumption is a change to the definition it constrains).
    - Edges onto a group it lies in are asked (an about edge onto the
      proof), and what the group, or the block itself, justifies has its
      support recomputed: the support channel, reported apart.
    - On the edited block alone: every `where-used` edge onto a block it
      uses is asked (that block's incoming uses edges may have changed);
      every `scope:paper` edge is asked (any edit); and if the block is the
      ambient frame, every block after it changes meaning: the frame has no
      listed edges.
    """
    goes_on: list[tuple[str, Link, str]] = []
    asked: list[tuple[str, Link, str]] = []
    support: list[tuple[str, str]] = []
    containing = [g for g, members in graph.groups.items() if changed in members]
    for e in graph.links:
        if e.target == changed:
            if e.kind == "uses" and e.content in MEANING_CONTENT:
                goes_on.append((e.source, e, e.content))
            else:
                asked.append((e.source, e, e.content))
        elif e.target in containing:
            asked.append((e.source, e, "justification"))
        elif e.source == changed and e.kind == "qualifies":
            goes_on.append((e.target, e, e.content))
        elif e.kind == "justifies" and (e.source == changed or e.source in containing):
            support.append((e.target, e.source))
    if edited:
        used = {
            e.target for e in graph.links if e.source == changed and e.kind == "uses"
        }
        for e in graph.links:  # the specific reason first: a block is listed once
            if e.where_used and e.target in used:
                asked.append(
                    (
                        e.source,
                        e,
                        f"where-used: {changed}, a user of {e.target}, changed",
                    )
                )
        for e in graph.links:
            if e.target == SCOPE_PAPER:
                asked.append((e.source, e, "the paper, on any edit"))
        if changed in ambient:
            for block in _blocks_after(graph, changed):
                frame = Link(block, "uses", changed, "ambient frame")
                goes_on.append((block, frame, "ambient frame"))
    return Step(goes_on, asked, support)


def _blocks_after(graph: EdgeGraph, block: str) -> list[str]:
    blocks = list(graph.blocks)
    return blocks[blocks.index(block) + 1 :] if block in blocks else []


def reach(graph: EdgeGraph, start: str, ambient: frozenset[str] | None = None) -> Reach:
    """The blocks an edit to `start` (a block, or a group as each of its
    blocks) reaches, by the rule, breadth first. `ambient` names the frame's
    blocks; the graph's own when None."""
    frame = graph.ambient if ambient is None else ambient
    members = list(graph.groups.get(start, (start,)))
    changed: dict[str, int] = dict.fromkeys(members, 0)
    reached: dict[str, Reached] = {}
    support: list[tuple[str, str]] = []
    queue = list(members)
    while queue:
        node = queue.pop(0)
        depth = changed[node] + 1
        step = rule(graph, node, edited=node in members, ambient=frame)
        support += [pair for pair in step.support if pair not in support]
        for block, e, content in step.goes_on:
            if block in changed:
                continue
            changed[block] = depth
            reached[block] = Reached(block, e, content, True, depth)
            queue.append(block)
        for block, e, content in step.asked:
            if block not in changed and block not in reached:
                reached[block] = Reached(block, e, content, False, depth)
    return Reach(start, list(reached.values()), support)


#: The Δ classes that close a change where they stand.
CLOSING = frozenset({"unchanged", "reworded"})


@dataclass(frozen=True)
class Impact:
    """One edge by which a change reaches `block` under `impact_reach`:
    `via` the edge, `handle` and `parts` the block and its parts whose Δ
    decides it (the changed end of the edge: its target and the parts it
    names, or for a qualifies edge or the ambient frame the block it comes
    from, whole), whether the rule moves `block`'s meaning
    (`meaning_moved`) or asks it, the changed block the walk started from
    (`origin`) and how far from it (`depth`)."""

    block: str
    via: Link
    handle: str
    parts: tuple[str, ...]
    meaning_moved: bool
    origin: str
    depth: int


def carries(deltas: dict[str | None, str] | None, parts: tuple[str, ...]) -> bool:
    """Whether a block's Δs carry a change on through an edge naming
    `parts` (none: the block whole, its group, a qualifies edge). No Δ at
    all is a change whole; a part without a Δ of its own takes the block's
    whole one, else counts as changed. Onto the block whole, its whole Δ
    speaks for it where given, else every part's does."""
    if deltas is None:
        return True
    whole = deltas.get(None)
    if not parts:
        if whole is not None:
            return whole not in CLOSING
        return any(cls not in CLOSING for cls in deltas.values())
    return any(deltas.get(p, whole) not in CLOSING for p in parts)


def impact_reach(
    graph: EdgeGraph,
    deltas: dict[str, dict[str | None, str]],
    roots: Sequence[str],
    ambient: frozenset[str] | None = None,
) -> list[Impact]:
    """What a change to `roots` reaches by the rule: breadth first from each root, an
    edge onto a block carrying the change on only where the Δ of the part it names (of
    any part, for a whole-block edge, a group or a qualifies edge) is not unchanged or
    reworded. `deltas` gives each block's Δ classes by legend part, `None` for the block
    whole. A block whose meaning the rule moves goes on by its own Δs where it has any,
    else wholly; a block the walk only asks stops it. A root in the ambient frame moves
    every block after it; the edited block's other reasons (`where-used`, `scope:paper`)
    are not the walk's but `update.mechanical_reopen`'s. One impact per reaching edge,
    in the order found, so a block reached twice has two; a block reached is reached
    whether or not it is open already."""
    frame = graph.ambient if ambient is None else ambient
    rooted = frozenset(roots)
    moving: dict[str, tuple[str, int]] = {}
    for root in roots:
        moving.setdefault(root, (root, 0))
    queue = list(moving)
    found: list[Impact] = []
    seen: set[tuple[str, Link]] = set()
    while queue:
        node = queue.pop(0)
        origin, depth = moving[node]
        step = rule(graph, node, ambient=frame)
        goes_on = list(step.goes_on)
        if node in rooted and node in frame:
            # the rule's frame case, which it keeps for the edited block
            goes_on += [
                (b, Link(b, "uses", node, "ambient frame"), "ambient frame")
                for b in _blocks_after(graph, node)
            ]
        for moved, pairs in ((True, goes_on), (False, step.asked)):
            for block, e, _ in pairs:
                if block in rooted or block == node:
                    continue
                parts = e.parts if e.target == node else ()
                if not carries(deltas.get(node), parts):
                    continue
                if (block, e) not in seen:
                    seen.add((block, e))
                    found.append(
                        Impact(block, e, node, parts, moved, origin, depth + 1)
                    )
                if moved and block not in moving:
                    moving[block] = (origin, depth + 1)
                    queue.append(block)
    return found


def resolve_content(roles: list[str]) -> str:
    """The role a reliance on these roles counts as: definition or
    assumption content if any is (recall first),
    else the first."""
    return next(
        (r for r in roles if r in MEANING_CONTENT), roles[0] if roles else "none"
    )


def from_records(cut: Cut, records: EdgeRecords) -> EdgeGraph:
    """The product's graph as the closure reads it: an edge naming a standing's content
    is `claim`, a part's its entry's role (`standard` for a gloss
    of standard notation), a whole block's its segments' roles, else its
    roles; a group's `justification`."""
    blocks = {block.id: block for block in cut.blocks}
    entries = {(e.block, e.label): e for e in records.entries}

    def block_roles(block_id: str) -> list[str]:
        block = blocks.get(block_id)
        if block is None:
            return ["none"]
        segments = [s.role.value if s.role else "none" for s in block.segments]
        return segments or [r.value for r in block.roles] or ["none"]

    links = []
    for edge in records.edges:
        t = edge.target
        parts: tuple[str, ...] = ()
        if t.kind is TargetKind.GROUP:
            target, content = t.name, "justification"
        elif t.kind is TargetKind.FILE:
            target, content = f"file:{t.name}", ""
        elif t.kind is TargetKind.SCOPE:
            target, content = SCOPE_PAPER, ""
        elif edge.claims:
            # a named standing is asked, whatever parts beside it say
            target, parts, content = t.name, edge.parts, "claim"
        elif edge.parts:
            target, parts = t.name, edge.parts
            content = resolve_content(
                [
                    "standard"
                    if entries[(t.name, p)].standard
                    else (role.value if (role := entries[(t.name, p)].role) else "none")
                    for p in edge.parts
                ]
            )
        else:
            target, content = t.name, resolve_content(block_roles(t.name))
        links.append(
            Link(
                edge.source,
                edge.kind.value,
                target,
                content,
                edge.where_used,
                parts,
                "where-used" if edge.where_used else "",
            )
        )
    return EdgeGraph(
        blocks=tuple(block.id for block in cut.blocks),
        groups={group.id: group.blocks for group in cut.groups},
        links=tuple(links),
        ambient=frozenset(f.block for f in records.frame),
    )


def entry_label(entry_id: str) -> str:
    """A part's label from its entry's id in an export, `<handle> [<label>]`."""
    return entry_id.split(" [", 1)[1][:-1]


def from_export(export: dict[str, Any]) -> EdgeGraph:
    """A kept export's graph as the closure reads it, as
    `from_records` reads the records it was written from: an edge naming a
    standing's content `claim`, a part's its entry's role (`standard` for a
    gloss of standard notation), a whole block's its segments' roles, else
    its roles; a group's
    `justification`. Parts are named by their labels. A block whose roles
    the export does not carry reads `none`."""
    blocks = {b["id"]: b for b in export["blocks"]}
    entries = {e["id"]: e for e in export.get("entries", [])}

    def block_roles(block_id: str) -> list[str]:
        block = blocks.get(block_id)
        if block is None:
            return ["none"]
        segments = [s["role"] for s in block.get("segments", [])]
        return segments or list(block.get("roles", [])) or ["none"]

    links = []
    for edge in export.get("edges", []):
        t = edge["target"]
        parts: tuple[str, ...] = ()
        if "group" in t:
            target, content = t["group"], "justification"
        elif "file" in t:
            target, content = f"file:{t['file']}", ""
        elif "scope" in t:
            target, content = SCOPE_PAPER, ""
        elif t.get("claims"):
            target, content = t["block"], "claim"
            parts = tuple(entry_label(e) for e in t.get("entries", []))
        elif t.get("entries"):
            target = t["block"]
            parts = tuple(entry_label(e) for e in t["entries"])
            content = resolve_content(
                [
                    "standard" if entries[e].get("standard") else entries[e]["role"]
                    for e in t["entries"]
                ]
            )
        else:
            target, content = t["block"], resolve_content(block_roles(t["block"]))
        where_used = bool(edge.get("where_used"))
        links.append(
            Link(
                edge["source"],
                edge["kind"],
                target,
                content,
                where_used,
                parts,
                "where-used" if where_used else "",
            )
        )
    return EdgeGraph(
        blocks=tuple(blocks),
        groups={g["id"]: tuple(g["blocks"]) for g in export["groups"]},
        links=tuple(links),
        ambient=frozenset(export.get("ambient") or ()),
    )
