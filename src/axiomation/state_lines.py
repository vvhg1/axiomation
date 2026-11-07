"""A kept state as its lines (`docs/architecture.md` § 4): its export split
into sections, and each state kept as its sections' change against its
parent's, the export rendered back from them byte for byte.

A section is one list or mapping of the export (its blocks, its edges, the
document's environments, the update's excerpts): its items in their order,
a list's each named by its content, a mapping's by its key. In every item a
block's or a group's id is written as its node, and a span inside a line
(an edge's evidence, a part's place, a block's owned runs) is placed from
its owner's start; so an item changes only where its owner's text or its
own words do, never because the text before it moved. What is left of the
export, its skeleton, holds each section's place. An edge's id, its
answer's record id, is rendered from its place where every edge's follows
the rule, and kept as written where not.

The node table is two sections of its own: each block node's block and
text, each group node's group, in the state's order.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from axiomation import kept_change
from axiomation.kept_change import ChangeError, written

#: Marks a node id written where a block's or a group's id stood, a span
#: placed from its owner's start, and a section's place in the skeleton:
#: an invisible separator, which no id, span or field of an export begins
#: with (a state whose export does not render back is kept whole).
MARK = "⁣"
_LIST = MARK + "["
_MAPPING = MARK + "{"
#: The export's mappings whose fields are each a section, or stay in the
#: skeleton (a value neither a list nor a mapping).
_SPLIT = frozenset({"", "document", "review", "review/update", "review/person"})
#: The sections whose items are placed from their own id's start, rendered
#: first, in this order; any other item is placed from its `source`'s or
#: its `block`'s.
_OWN = ("blocks", "groups")
#: The one field of an item never placed from its owner: a block's extent
#: is where its owner starts.
_EXTENT = "extent"
_SPAN_FIELDS = ["file", "start", "end"]
_EDGES = "edges"
_KEY_BYTES = 8


@dataclass(frozen=True, slots=True)
class Section:
    """One section: its items by key, and the keys in order (a list's item
    named twice where it stands twice)."""

    items: Mapping[str, Any]
    order: tuple[str, ...]


_NONE = Section(MappingProxyType({}), ())


@dataclass(frozen=True, slots=True)
class Lines:
    """A state's lines: its node table, its sections by their path in the
    export, its export's skeleton, and the rule its edges' ids follow
    (their answer's prefix, its attempt and the first record's number),
    None where they are written."""

    nodes: Section
    groups: Section
    sections: Mapping[str, Section]
    skeleton: Any
    edge_ids: tuple[str, int, int] | None

    def node_rows(self) -> list[list[str]]:
        """The node table as a state's row writes it: block, node, text."""
        return [
            [self.nodes.items[n][0], n, self.nodes.items[n][1]]
            for n in self.nodes.order
        ]

    def group_nodes(self) -> dict[str, str]:
        """Each group's id to its node, in order."""
        return {self.groups.items[n]: n for n in self.groups.order}


class LinesError(ValueError):
    """A change of lines that does not apply to the lines it is applied to."""


# --- a state's export to its lines ---------------------------------------------


def extract(
    nodes: Sequence[Sequence[str]], groups: Mapping[str, str], export: bytes
) -> Lines:
    """A state's lines: `nodes` its block, node and text rows, `groups` its
    group ids' nodes, `export` its export as written."""
    payload = json.loads(export)
    names = {block: node for block, node, _ in nodes} | dict(groups)
    extents = _extents(payload)
    edge_ids = _edge_ids(payload)
    sections: dict[str, Section] = {}

    def split(value: Any, path: str) -> Any:
        if path in _SPLIT and isinstance(value, dict):
            return {k: split(v, _joined(path, k)) for k, v in value.items()}
        if isinstance(value, list):
            items = [_item_of(path, x, names, extents) for x in value]
            if path == _EDGES and edge_ids is not None:
                for x in items:
                    x["id"] = None
            sections[path] = _listed(items)
            return _LIST
        if isinstance(value, dict):
            named = {_named(k, names): _named(v, names) for k, v in value.items()}
            sections[path] = Section(named, tuple(named))
            return _MAPPING
        return _named(value, names)

    skeleton = split(payload, "")
    return Lines(
        nodes=Section(
            {node: [block, text] for block, node, text in nodes},
            tuple(node for _, node, _ in nodes),
        ),
        groups=Section(
            {node: group for group, node in groups.items()}, tuple(groups.values())
        ),
        sections=sections,
        skeleton=skeleton,
        edge_ids=edge_ids,
    )


def _joined(path: str, name: str) -> str:
    return f"{path}/{name}" if path else name


def _extents(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Each block's extent, and each group's its first block's; none for
    a block without one."""
    found = {
        b["id"]: b[_EXTENT]
        for b in payload.get("blocks", [])
        if isinstance(b, dict) and _is_span(b.get(_EXTENT))
    }
    for g in payload.get("groups", []):
        if isinstance(g, dict) and g.get("blocks") and g["blocks"][0] in found:
            found[g["id"]] = found[g["blocks"][0]]
    return found


def _edge_ids(payload: Mapping[str, Any]) -> tuple[str, int, int] | None:
    """The rule the edges' ids follow (`kept_change.counted`), None where
    any does not."""
    return kept_change.counted([e.get("id") for e in payload.get(_EDGES, [])])


def _owner(path: str, item: Mapping[str, Any]) -> Any:
    if path in _OWN:
        return item.get("id")
    return item.get("source", item.get("block"))


def _item_of(
    path: str, item: Any, names: Mapping[str, str], extents: Mapping[str, Any]
) -> Any:
    if isinstance(item, dict):
        owner = _owner(path, item)
        extent = extents.get(owner) if isinstance(owner, str) else None
        if extent is not None:
            item = {
                k: v if k == _EXTENT else _placed(v, extent) for k, v in item.items()
            }
    return _named(item, names)


def _named(value: Any, names: Mapping[str, str]) -> Any:
    """`value` with each block or group id, a string or a key, as its node,
    marked."""
    if isinstance(value, str):
        node = names.get(value)
        return value if node is None else MARK + node
    if isinstance(value, dict):
        return {_named(k, names): _named(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [_named(v, names) for v in value]
    return value


def _is_span(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and list(value) == _SPAN_FIELDS
        and isinstance(value["file"], str)
        and _is_point(value["start"])
        and _is_point(value["end"])
    )


def _is_point(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(n) is int for n in value)
    )


def _placed(value: Any, extent: Mapping[str, Any]) -> Any:
    """`value` with each span in its owner's file, from its owner's start
    on, written from that start: lines from its first line, columns from
    its first column on that line alone."""
    if isinstance(value, dict):
        if _is_span(value) and value["file"] == extent["file"]:
            l0, c0 = extent["start"]
            points = []
            for line, column in (value["start"], value["end"]):
                if (line, column) < (l0, c0):
                    return value
                points += [line - l0, column - c0 if line == l0 else column]
            return {MARK: points}
        return {k: _placed(v, extent) for k, v in value.items()}
    if isinstance(value, list):
        return [_placed(v, extent) for v in value]
    return value


def _listed(items: Sequence[Any]) -> Section:
    found: dict[str, Any] = {}
    order = []
    for x in items:
        key = _key(x)
        found.setdefault(key, x)
        order.append(key)
    return Section(found, tuple(order))


def _key(item: Any) -> str:
    written = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    return hashlib.blake2b(written.encode("utf-8"), digest_size=_KEY_BYTES).hexdigest()


# --- lines to the export ---------------------------------------------------------


def render(lines: Lines) -> bytes:
    """The export the lines hold, as the exchange file writes it
    (`exchange.dumps`)."""
    payload = rendered(lines)
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def rendered(lines: Lines, within: Sequence[str] = ("",), shared: bool = False) -> Any:
    """The export the lines hold, parsed: each section whose path starts
    with one of `within`, the blocks and groups always (every span is
    placed from them); any other section None. A read of a few sections
    renders no other. `shared`: its sections are the ones every shared
    read of the same lines gets, for a reader that changes none of it."""
    ids = {n: lines.nodes.items[n][0] for n in lines.nodes.order} | dict(
        lines.groups.items
    )
    places = dict(_places(lines.skeleton, ""))
    extents: dict[str, Any] = {}
    done: dict[str, Any] = {}
    token = _ids_token(ids)
    placed_from = ""
    for path in sorted(places, key=lambda p: _OWN.index(p) if p in _OWN else len(_OWN)):
        if path not in _OWN and not any(path.startswith(w) for w in within):
            done[path] = None
            continue
        section = lines.sections.get(path, _NONE)
        # a list's items are named by their content; a mapping's by its keys
        held = (
            path,
            _digest(
                "\n".join(section.order)
                if places[path] == _LIST
                else json.dumps([[k, section.items[k]] for k in section.order])
            ),
            token,
            placed_from,
        )
        if path == _EDGES:
            held += (lines.edge_ids,)
        live = _LIVE.get(held) if shared else None
        written = None if live is not None else _SECTIONS.get(held)
        if live is not None:
            done[path] = live
        elif written is not None:
            done[path] = pickle.loads(written)
        elif places[path] == _MAPPING:
            done[path] = {
                _ided(k, ids): _ided(section.items[k], ids) for k in section.order
            }
        else:
            done[path] = _rendered_list(
                path, section, ids, extents, lines.edge_ids, shared
            )
        if written is None and live is None and not shared:
            if len(_SECTIONS) >= _SECTIONS_HELD:
                _SECTIONS.clear()
            _SECTIONS[held] = pickle.dumps(done[path])
        if shared and live is None:
            if len(_LIVE) >= _SECTIONS_HELD:
                _LIVE.clear()
            _LIVE[held] = done[path]
        if path in _OWN:
            extents = _extents({p: done.get(p, []) for p in _OWN})
            placed_from = _digest(json.dumps(extents, sort_keys=True))

    def filled(value: Any, path: str) -> Any:
        if path in places:
            return done[path]
        if path in _SPLIT and isinstance(value, dict):
            return {k: filled(v, _joined(path, k)) for k, v in value.items()}
        return _ided(value, ids)

    return filled(lines.skeleton, "")


def _places(skeleton: Any, path: str) -> Iterator[tuple[str, str]]:
    """Each section's path in the skeleton, and whether a list's or a
    mapping's stands there."""
    if skeleton in (_LIST, _MAPPING):
        yield path, skeleton
    elif path in _SPLIT and isinstance(skeleton, dict):
        for k, v in skeleton.items():
            yield from _places(v, _joined(path, k))


def _rendered_list(
    path: str,
    section: Section,
    ids: Mapping[str, str],
    extents: Mapping[str, Any],
    edge_ids: tuple[str, int, int] | None,
    shared: bool = False,
) -> list[Any]:
    items = []
    token = _ids_token(ids)
    for key in section.order:
        stored = section.items[key]
        owner = _owner(path, stored) if isinstance(stored, dict) else None
        if isinstance(owner, str) and owner.startswith(MARK):
            owner = ids.get(owner[1:], owner)
        held = (path, key, token, None if path in _OWN else repr(extents.get(owner)))
        if shared:
            live = _LIVE_ITEMS.get(held)
            if live is None:
                live = _item_rendered(path, stored, ids, extents)
                if len(_LIVE_ITEMS) >= _ITEMS_HELD:
                    _LIVE_ITEMS.clear()
                _LIVE_ITEMS[held] = live
            items.append(live)
            continue
        written = _ITEMS.get(held)
        if written is None:
            written = pickle.dumps(_item_rendered(path, stored, ids, extents))
            if len(_ITEMS) >= _ITEMS_HELD:
                _ITEMS.clear()
            _ITEMS[held] = written
        items.append(pickle.loads(written))
    if path == _EDGES and edge_ids is not None:
        given_ids = kept_change.counted_ids(edge_ids, len(items))
        # a shared item is another state's too: its id goes on a copy
        items = [
            {**item, "id": given} for item, given in zip(items, given_ids, strict=True)
        ]
    return items


def nodes_named(value: Any, handles: Mapping[str, str]) -> Iterator[str]:
    """Each node a stored item names: by its marked id, or by its handle
    (`handles`, a handle's node)."""
    if isinstance(value, str):
        if value.startswith(MARK) and len(value) > 1:
            yield value[1:]
        elif value in handles:
            yield handles[value]
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from nodes_named(k, handles)
            yield from nodes_named(v, handles)
    elif isinstance(value, list):
        for v in value:
            yield from nodes_named(v, handles)


def handle_nodes(lines: Lines) -> dict[str, str]:
    """Each handle the lines name a node by, to its node."""
    section = lines.sections.get("handles", _NONE)
    return {
        str(section.items[k]): k[1:]
        for k in section.order
        if isinstance(k, str) and k.startswith(MARK)
    }


#: Each item as rendered, by its section, its key, the ids it is rendered
#: with and its owner's extent: the states of a chain share most of their
#: items, so a walk over many renders each once. Held pickled, each read
#: its own copy.
_ITEMS: dict[tuple[Any, ...], bytes] = {}
_ITEMS_HELD = 200_000


#: Each section as rendered, by its path, its items in order, the ids and
#: the extents it is rendered with: most of a state's sections are its
#: parent's.
_SECTIONS: dict[tuple[Any, ...], bytes] = {}
_SECTIONS_HELD = 20_000

#: The same sections and items, unpickled, for the shared reads.
_LIVE: dict[tuple[Any, ...], Any] = {}
_LIVE_ITEMS: dict[tuple[Any, ...], Any] = {}


def _ids_token(ids: Mapping[str, str]) -> str:
    """A name for the ids a state renders with: equal ids, one name."""
    return _digest(json.dumps(sorted(ids.items()), separators=(",", ":")))


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=_KEY_BYTES).hexdigest()


def _item_rendered(
    path: str, stored: Any, ids: Mapping[str, str], extents: Mapping[str, Any]
) -> Any:
    item = _ided(stored, ids)
    if isinstance(item, dict):
        owner = _owner(path, item)
        extent = (
            item.get(_EXTENT)
            if path == "blocks"
            else extents.get(owner)
            if isinstance(owner, str)
            else None
        )
        if _is_span(extent):
            item = {
                k: v if k == _EXTENT else _unplaced(v, extent) for k, v in item.items()
            }
    return item


def _ided(value: Any, ids: Mapping[str, str]) -> Any:
    """`value` with each marked node as its block's or group's id."""
    if isinstance(value, str):
        if value.startswith(MARK) and value[1:] in ids:
            return ids[value[1:]]
        return value
    if isinstance(value, dict):
        return {_ided(k, ids): _ided(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_ided(v, ids) for v in value]
    return value


def _unplaced(value: Any, extent: Mapping[str, Any]) -> Any:
    if isinstance(value, dict):
        if list(value) == [MARK]:
            l0, c0 = extent["start"]
            line, column, end_line, end_column = value[MARK]
            return {
                "file": extent["file"],
                "start": [l0 + line, column + c0 if line == 0 else column],
                "end": [
                    l0 + end_line,
                    end_column + c0 if end_line == 0 else end_column,
                ],
            }
        return {k: _unplaced(v, extent) for k, v in value.items()}
    if isinstance(value, list):
        return [_unplaced(v, extent) for v in value]
    return value


# --- a state's lines against its parent's ----------------------------------------


def change(parent: Lines | None, lines: Lines) -> dict[str, Any]:
    """What `lines` change of `parent`'s (none: of no lines): each section
    changed, its order's changed runs and the items it adds or changes;
    the sections it drops; the skeleton and the edges' rule where they
    changed."""
    found: dict[str, Any] = {}
    for name in ("nodes", "groups"):
        changed = _section_change(
            _NONE if parent is None else getattr(parent, name), getattr(lines, name)
        )
        if changed:
            found[name] = changed
    before = {} if parent is None else parent.sections
    sections = {}
    for path, section in lines.sections.items():
        changed = _section_change(before.get(path, _NONE), section)
        if changed:
            sections[path] = changed
    if sections:
        found["sections"] = sections
    gone = [p for p in before if p not in lines.sections]
    if gone:
        found["gone"] = gone
    if parent is None or written(parent.skeleton) != written(lines.skeleton):
        found["skeleton"] = lines.skeleton
    if parent is None or parent.edge_ids != lines.edge_ids:
        found["edge_ids"] = None if lines.edge_ids is None else list(lines.edge_ids)
    return found


def _section_change(before: Section, after: Section) -> dict[str, Any]:
    found: dict[str, Any] = {}
    if before.order != after.order:
        found["order"] = kept_change.runs(before.order, after.order)
    # compared by their JSON: 1, 1.0 and true are three values
    put = {
        k: v
        for k, v in after.items.items()
        if k not in before.items or written(before.items[k]) != written(v)
    }
    if put:
        found["put"] = put
    return found


def applied(parent: Lines | None, changed: Mapping[str, Any]) -> Lines:
    """`parent`'s lines (none: no lines) with `changed` (`change`) applied;
    refused where it names an item neither holds."""
    sections = dict({} if parent is None else parent.sections)
    for path in changed.get("gone", ()):
        sections.pop(path, None)
    for path, section_change in changed.get("sections", {}).items():
        sections[path] = _applied_section(
            sections.get(path, _NONE), section_change, path
        )
    if parent is None and ("skeleton" not in changed or "edge_ids" not in changed):
        raise LinesError("the first lines name no skeleton")
    skeleton = changed.get("skeleton", None if parent is None else parent.skeleton)
    rule = changed.get("edge_ids", None if parent is None else parent.edge_ids)
    return Lines(
        nodes=_applied_section(
            _NONE if parent is None else parent.nodes, changed.get("nodes", {}), "nodes"
        ),
        groups=_applied_section(
            _NONE if parent is None else parent.groups,
            changed.get("groups", {}),
            "groups",
        ),
        sections=sections,
        skeleton=skeleton,
        edge_ids=None if rule is None else (str(rule[0]), int(rule[1]), int(rule[2])),
    )


def _applied_section(before: Section, changed: Mapping[str, Any], path: str) -> Section:
    if not changed:
        return before
    order: Sequence[str] = before.order
    if "order" in changed:
        try:
            order = tuple(kept_change.applied_runs(before.order, changed["order"]))
        except ChangeError as error:
            raise LinesError(f"section {path}: {error}") from None
    put = changed.get("put", {})
    items = {}
    for key in order:
        if key in put:
            items[key] = put[key]
        elif key in before.items:
            items[key] = before.items[key]
        else:
            raise LinesError(f"section {path}: no item {key}")
    return Section(items, tuple(order))
