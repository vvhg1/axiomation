"""Judgments beside the graph: what a reading rests on, when a
change lapses it, and a subject's status per aspect.

A judgment, a finding and a finding's answer are records in the one store,
filed by a person or an agent on a kept state and written by the resolver
(`Resolver.file`); each names the state it was made on and mints no state.
The graph never reads one. A reading rests on its subject and one hop of
the edges its aspect relies on (§ "The basis rule"), each element recorded
with its text's hash at that state (`basis`).

A reading lapses (§ "When a judgment lapses") by a walk along the kept
states from the one it was made on to the one read, each step parent to
child, over the recorded basis carried forward with the one hop at the
parent: a node's text changed (not where identity classes it `markup`) or
ended; a Δ that carries on its block, or its handle among the rule's moved
blocks, read on a step across versions; an edge no line continues under
the naming across states; the paper, on any change; the subject deleted. A `cited`
judgment lapses too where a file it read a page of prints other content,
or is absent or held back: the files are not walked, since the paper
cannot change them, but compared with the files seen since. Nothing
un-lapses, and nothing here is stored that the states give: every status
is computed (a walk's progress along the states is kept in the user's
cache, `derived`).
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left
from collections.abc import Callable, Collection, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from functools import cached_property
from typing import Any

from axiomation import computations, derived, versions
from axiomation.closure import CLOSING, carries, entry_label
from axiomation.computations import Ran
from axiomation.evidence import (
    FileStamp,
    PageStamp,
    RunStamp,
    Sources,
    normalize,
    provisional,
    same_content,
    work_of,
)
from axiomation.questions import review_file as rf
from axiomation.questions.edge_sheet import norm
from axiomation.records import (
    AGENT,
    FINDING,
    FINDING_ANSWER,
    FINDING_ANSWER_WITHDRAWAL,
    JUDGED_ASPECTS,
    JUDGMENT,
    NON_BLOCKING_ASPECTS,
    NOT_FILED,
    PERSON,
    READ_VERDICTS,
    REVIEWER,
    SEEN,
    Anchor,
    AnchorKind,
    AnswerWithdrawal,
    Aspect,
    BasisElement,
    BasisKind,
    Depth,
    FiledAt,
    FileSeen,
    Finding,
    FindingAnswer,
    FindingStatus,
    Judgment,
    JudgmentRecords,
    Publication,
    RecordStore,
    Ruling,
    RunSeen,
    Subject,
    ValuePair,
    Verdict,
    VersionOfRecord,
    WorkSeen,
    filed_after,
    judgment_data,
    unseen_by,
)
from axiomation.resolver import Filed, Resolver
from axiomation.versions import State

#: The filing form's version, its answers' template version.
FILING_VERSION = "1"
#: The verdicts that find a subject wrong or unshown.
NEGATIVE = (Verdict.NOT_SHOWN, Verdict.CONTRADICTED)
#: Identity's class for a change of markup alone, which lapses nothing, and
#: for a macro a block uses changed, which changes its text though its
#: words are the same.
MARKUP = "markup"
MACRO = "macro"
#: Identity's classes that say a block's text changed: each counts, whatever
#: the words compare as (`macro`'s words are the same by definition).
CHANGING = frozenset({"math", MACRO, "changed", "split", "merged"})


class JudgmentError(ValueError):
    """A filing the kept state cannot take, with why."""


# --- a kept state, read for judgments -----------------------------------------


@dataclass(frozen=True, eq=False)
class Seen:
    """A kept state's graph as the basis reads it: node ids, text ids and
    edges, each edge named across states (`versions.edge_ids`)."""

    state: State
    export: Mapping[str, Any]
    #: block or group id → node id, and back
    node_of: Mapping[str, str]
    ident_of: Mapping[str, str]
    #: node id → its text's hash (a group's from its members')
    text_of: Mapping[str, str]
    #: group node → its members' nodes
    members: Mapping[str, tuple[str, ...]]
    edge_ids: Mapping[str, tuple[Any, ...]]
    #: each standing: its node and its claim's words
    standings: tuple[tuple[str, str], ...]
    handles: Mapping[str, str]
    #: block node → its words, whitespace normalised
    words_of: Mapping[str, str] = field(default_factory=dict)
    #: node → its kept handle (`docs/model.md` § 7), the name shown
    names: Mapping[str, str] = field(default_factory=dict)
    #: each part an edge names: its block's node and its words
    parts: tuple[tuple[str, str], ...] = ()
    #: block node → the works it cites, by key, in the paper's order (the
    #: document half's citations within its owned runs)
    works_of: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @cached_property
    def edges(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.export.get("edges", []))

    def handle(self, node: str) -> str:
        """A node's handle as shown: its kept one, else its export's."""
        if node in self.names:
            return self.names[node]
        ident = self.ident_of.get(node)
        return node if ident is None else self.handles.get(ident, ident)

    @cached_property
    def edges_out(self) -> Mapping[tuple[str, str], list[tuple[int, dict[str, Any]]]]:
        """Each edge by its kind and source, with its place among the
        edges."""
        found: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
        for i, e in enumerate(self.export.get("edges", [])):
            found.setdefault((e["kind"], _source(e)), []).append((i, e))
        return found

    @cached_property
    def edges_onto(
        self,
    ) -> Mapping[tuple[str, str | None], list[tuple[int, dict[str, Any]]]]:
        """Each edge by its kind and the block or group it is onto (None
        for a file or the paper), with its place among the edges."""
        found: dict[tuple[str, str | None], list[tuple[int, dict[str, Any]]]] = {}
        for i, e in enumerate(self.export.get("edges", [])):
            found.setdefault((e["kind"], _onto(e)), []).append((i, e))
        return found

    @cached_property
    def ident_of_handle(self) -> Mapping[str, str]:
        """Handle → the block's or group's id that bears it, the first."""
        found: dict[str, str] = {}
        for ident, handle in self.handles.items():
            found.setdefault(handle, ident)
        return found


def _within(at: Mapping[str, Any], run: Mapping[str, Any]) -> bool:
    return (
        at["file"] == run["file"]
        and tuple(run["start"]) <= tuple(at["start"])
        and tuple(at["end"]) <= tuple(run["end"])
    )


def cited_works(export: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Each block's cited works, by key, in the paper's order: the document
    half's citations that lie within one of its owned runs."""
    citations = export.get("document", {}).get("citations", [])
    # the citations per file by start, so a run meets only those starting
    # within it
    by_file: dict[str, list[tuple[tuple[int, ...], int]]] = {}
    for i, c in enumerate(citations):
        by_file.setdefault(c["at"]["file"], []).append((tuple(c["at"]["start"]), i))
    for held in by_file.values():
        held.sort()
    found: dict[str, tuple[str, ...]] = {}
    for block in export.get("blocks", []):
        within: set[int] = set()
        for run in block.get("owned", []):
            held = by_file.get(run["file"], [])
            start, end = tuple(run["start"]), tuple(run["end"])
            for at, i in held[bisect_left(held, (start, -1)) :]:
                if at > end:
                    break
                if _within(citations[i]["at"], run):
                    within.add(i)
        keys = [key for i in sorted(within) for key in citations[i]["keys"]]
        if keys:
            found[block["id"]] = tuple(dict.fromkeys(keys))
    return found


def _hash(parts: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def seen(
    store: RecordStore,
    kept: State,
    texts: Mapping[str, tuple[str, ...]] | None = None,
    export: Mapping[str, Any] | None = None,
    names: Mapping[str, str] | None = None,
) -> Seen:
    """A kept state read for the basis; `texts` the store's block texts,
    read here where not given; `export` in place of the state's own, where
    an edges edit not yet kept corrects it (`edits.corrected_export`);
    `names` each node's kept handle, read here where not given."""
    if export is None:
        # a Seen only reads its export
        export = versions.kept_payload(store, kept, shared=True)
    texts = versions.texts(store) if texts is None else texts
    names = versions.handles(store, kept).kept if names is None else names
    node_of = {n.block: n.node for n in kept.nodes} | dict(kept.groups)
    text_of = {n.node: n.text for n in kept.nodes}
    members = {
        kept.groups[g["id"]]: tuple(node_of[b] for b in g["blocks"] if b in node_of)
        for g in export.get("groups", [])
        if g["id"] in kept.groups
    }
    for group, held in members.items():
        text_of[group] = _hash(text_of[m] for m in held)
    return Seen(
        state=kept,
        export=export,
        node_of=node_of,
        ident_of={node: ident for ident, node in node_of.items()},
        text_of=text_of,
        members=members,
        edge_ids=versions.edge_ids(kept, export),
        standings=tuple(versions.standing_ids(kept, export)),
        handles=dict(export.get("handles") or {}),
        words_of={n.node: norm(" ".join(texts.get(n.text, ()))) for n in kept.nodes},
        names=names,
        parts=tuple(
            (node_of.get(e.get("block", ""), ""), norm(e.get("anchor", "")))
            for e in export.get("entries", [])
        ),
        works_of={
            node_of[b]: keys for b, keys in cited_works(export).items() if b in node_of
        },
    )


#: A state's carried walks' name among the derived values.
WALKS = "walks-"


class Reader:
    """The kept states and the steps between them, each read once."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store
        self._seen: dict[str, Seen] = {}
        self._steps: dict[tuple[str, str], Step] = {}
        self._touched: dict[tuple[str, str], frozenset[str] | None] = {}
        self._chains: dict[str, tuple[State, ...]] = {}
        self.texts = versions.texts(store)
        #: each state's carried walks (`Carry`), by the walk's key; those
        #: carried here since they were last kept
        self._carries: dict[str, dict[Hashable, Carry]] = {}
        self._fresh: set[str] = set()

    @cached_property
    def _kept_walks(self) -> frozenset[str]:
        return derived.names()

    def _carries_at(self, state: str) -> dict[Hashable, Carry]:
        if state not in self._carries:
            name = f"{WALKS}{state}"
            held = derived.load(name) if name in self._kept_walks else None
            self._carries[state] = held if isinstance(held, dict) else {}
        return self._carries[state]

    def carried(self, key: Hashable, path: Sequence[State]) -> tuple[int, Carry | None]:
        """The walk `key` as carried to the latest state along `path` it
        was carried to, and that state's place on it; (0, None) where it
        was carried to none."""
        for i in range(len(path) - 1, 0, -1):
            found = self._carries_at(path[i].id).get(key)
            if found is not None:
                return i, found
        return 0, None

    def carry(self, key: Hashable, at: State, carry: Carry) -> None:
        """The walk `key` carried to `at`, kept by `keep`."""
        self._carries_at(at.id)[key] = carry
        self._fresh.add(at.id)

    def keep(self) -> None:
        """The walks carried since they were last kept, kept beside the
        store (`derived`), each state's in one value."""
        for state in sorted(self._fresh):
            derived.save(f"{WALKS}{state}", self._carries[state])
        self._fresh.clear()

    @cached_property
    def names(self) -> dict[str, str]:
        return versions.kept_handles(self.store)

    def seen(self, kept: State) -> Seen:
        if kept.id not in self._seen:
            self._seen[kept.id] = seen(self.store, kept, self.texts, names=self.names)
        return self._seen[kept.id]

    def touched(self, parent: State, child: State) -> frozenset[str] | None:
        key = (parent.id, child.id)
        if key not in self._touched:
            self._touched[key] = versions.touched(self.store, parent, child)
        return self._touched[key]

    def step(self, parent: State, child: State) -> Step:
        key = (parent.id, child.id)
        if key not in self._steps:
            self._steps[key] = Step(
                self.seen(parent),
                self.seen(child),
                versions.transition(self.store, parent, child),
            )
        return self._steps[key]

    def path(self, older: str, newer: State) -> list[State] | None:
        """The states from `older` to `newer` along `newer`'s parents, None
        where `older` is not among them."""
        if newer.id not in self._chains:
            self._chains[newer.id] = tuple(versions.chain(self.store, newer))
        path = self._chains[newer.id]
        ids = [s.id for s in path]
        return None if older not in ids else list(path[ids.index(older) :])


#: The kept states' readers, one a store while its kept states stand: a
#: state or a text kept reads them again (`versions.kept_stamp`).
READERS_HELD = 8
_READERS: versions.Held[Reader] = versions.Held(READERS_HELD)


def kept_reader(store: RecordStore) -> Reader:
    """`store`'s kept states' reader, shared by every read in this process
    while they stand: what one read worked out of them, another finds."""
    key = store.key
    stamp = versions.kept_stamp(store)
    found = _READERS.get(key, stamp)
    if found is None:
        found = Reader(store)
        _READERS.put(key, stamp, found, 1)
    return found


class Step:
    """One step between two kept states as every reading walks it: the
    transition, the naming across it, and what continues across it, each
    worked out once for all the readings that cross it."""

    def __init__(self, parent: Seen, child: Seen, t: versions.Transition) -> None:
        self.parent = parent
        self.child = child
        self.t = t
        self.names = versions.naming(
            t.origins,
            (n.node for n in parent.state.nodes),
            (n.node for n in child.state.nodes),
        )

    @cached_property
    def corrected(self) -> dict[str, list[Mapping[str, Any]]]:
        """Within a version: the child's Δ lines a person or an agent
        corrected (their class changed from the parent's), by block."""
        before = (self.parent.export.get("review") or {}).get("update") or {}
        was = before.get("deltas", [])
        found: dict[str, list[Mapping[str, Any]]] = {}
        for i, d in enumerate(self.t.update.get("deltas", [])):
            if i < len(was) and was[i]["class"] != d["class"]:
                found.setdefault(d["block"], []).append(d)
        return found

    @cached_property
    def moved_before(self) -> set[str | None]:
        """The parent's nodes its record moved by rule."""
        before = (self.parent.export.get("review") or {}).get("update") or {}
        return {
            self.parent.node_of.get(_ident(self.parent, r["handle"]))
            for r in before.get("moved_by_rule", [])
        }

    @cached_property
    def continued_claim(self) -> dict[int, int]:
        """Each parent standing's index → the child standing that continues
        it (`versions.continued_parts`)."""
        pairs = versions.continued_parts(
            self.parent.standings, self.child.standings, self.names
        )
        return {i: j for j, i in pairs.items()}

    @cached_property
    def continued_words(self) -> dict[tuple[str, str], str]:
        """Each part the parent's edges name (its node and words) → the words
        of the child's part that continues it."""
        pairs = versions.continued_parts(
            self.parent.parts, self.child.parts, self.names
        )
        return {self.parent.parts[i]: self.child.parts[j][1] for j, i in pairs.items()}

    @cached_property
    def child_edges(
        self,
    ) -> dict[tuple[str, str], list[tuple[str, str, Any, tuple[str, ...]]]]:
        """The child's edges by kind and each parent name of their source
        (`names`), in the child's order: each one's source, target, parts
        and parts without the claims it names."""
        found: dict[tuple[str, str], list[tuple[str, str, Any, tuple[str, ...]]]] = {}
        for source, k, t, parts in self.child.edge_ids.values():
            for name in dict.fromkeys(self.names(source)):
                found.setdefault((k, name), []).append(
                    (source, t, parts, _legend_parts(parts))
                )
        return found


# --- the basis -------------------------------------------------------------------


def _node(at: Seen, node: str, parts: Sequence[str] = ()) -> BasisElement:
    return BasisElement(BasisKind.NODE, node, at.text_of.get(node), tuple(parts))


def _paper(at: Seen) -> BasisElement:
    text = _hash(f"{n.node} {n.text}" for n in at.state.nodes)
    return BasisElement(BasisKind.PAPER, "paper", text)


def _target(at: Seen, edge: Mapping[str, Any]) -> BasisElement:
    t = edge["target"]
    if "block" in t or "group" in t:
        ident = t.get("block", t.get("group"))
        node = at.node_of.get(ident, ident)
        return _node(at, node, [entry_label(e) for e in t.get("entries", [])])
    if "file" in t:
        return BasisElement(BasisKind.FILE, t["file"], at.state.files.get(t["file"]))
    return _paper(at)


def _edge(at: Seen, edge: Mapping[str, Any]) -> BasisElement:
    source, kind, target, parts = at.edge_ids[edge["id"]]
    return BasisElement(BasisKind.EDGE, source, None, tuple(parts), (kind, target))


def _source(edge: Mapping[str, Any]) -> str:
    return edge["source"]


def _onto(edge: Mapping[str, Any]) -> str | None:
    t = edge["target"]
    return t.get("block", t.get("group"))


def _relied(at: Seen, sources: Iterable[str], kind: str) -> list[BasisElement]:
    """The edges of `kind` out of the blocks or groups `sources` (ids), and
    their targets."""
    held = sorted(
        (i, e)
        for source in set(sources)
        for i, e in at.edges_out.get((kind, source), ())
    )
    found: list[BasisElement] = []
    for _, e in held:
        found += [_edge(at, e), _target(at, e)]
    return found


def _ids(at: Seen, node: str) -> list[str]:
    """A node's block or group id, and a group's members' ids."""
    ident = at.ident_of.get(node)
    if ident is None:
        return []
    return [ident, *(at.ident_of[m] for m in at.members.get(node, ()))]


def _justifies(at: Seen, node: str) -> list[dict[str, Any]]:
    """The justifies edges out of `node` (a block or group), or onto it, in
    the edges' order."""
    ident = at.ident_of.get(node)
    held = dict(at.edges_onto.get(("justifies", ident), ()))
    if ident is not None:
        held |= dict(at.edges_out.get(("justifies", ident), ()))
    return [held[i] for i in sorted(held)]


def _valid(at: Seen, j: str, t: str | None) -> list[BasisElement]:
    found = [_node(at, j)]
    if t is not None:
        found.append(_node(at, t))
    t_ident = None if t is None else at.ident_of.get(t)
    for e in _justifies(at, j):
        if t_ident is not None and _onto(e) != t_ident:
            continue
        found += [_edge(at, e), _target(at, e), _node(at, at.node_of[_source(e)])]
        found += _relied(at, _ids(at, at.node_of[_source(e)]), "uses")
    # what T's statement takes as a hypothesis decides what J must show
    # (`docs/model.md` § 8): a flag set or taken off lapses the reading
    for _, e in at.edges_out.get(("uses", t_ident or ""), ()):
        if e.get("assumed"):
            found += [_edge(at, e), _target(at, e)]
    return found


def _accurate(at: Seen, node: str) -> list[BasisElement]:
    ident = at.ident_of.get(node)
    found = [_node(at, node)]
    for _, e in at.edges_out.get(("about", ident or ""), ()):
        found += [_edge(at, e), _target(at, e)]
        if e.get("where_used"):
            used = at.edges_onto.get(("uses", _onto(e)), ())
            found += [_edge(at, u) for _, u in used]
    return found


def code_file(at: Seen, key: str) -> bool:
    """Whether a file target is a code file at the state: kept, and code by
    its extension. A path the text writes but the paper does not
    ship is no code file: nothing of it is kept, and it asks nothing."""
    return key in at.state.files and computations.is_code(key)


def code_uses(at: Seen, node: str) -> list[str]:
    """The code files a block uses, by key, in the order of its edges."""
    ident = at.ident_of.get(node)
    return list(
        dict.fromkeys(
            e["target"]["file"]
            for _, e in at.edges_out.get(("uses", ident or ""), ())
            if "file" in e["target"] and code_file(at, e["target"]["file"])
        )
    )


def _computed(at: Seen, node: str, file: str | None) -> list[BasisElement]:
    """A `computed` reading's one hop: the block and its uses
    edge to the code file, with the file's blob; with no file (a finding's
    block anchor), its uses edges to every code file."""
    ident = at.ident_of.get(node)
    files = [file] if file is not None else code_uses(at, node)
    found = [_node(at, node)]
    for _, e in at.edges_out.get(("uses", ident or ""), ()):
        if e["target"].get("file") in files:
            found += [_edge(at, e), _target(at, e)]
    return found


def _qualifiers(at: Seen, elements: Sequence[BasisElement]) -> list[BasisElement]:
    """The qualifies edges onto each node the elements name, and their
    sources: a node is read with its qualifiers, each a premise of what
    rests on it (`docs/model.md` § 3)."""
    onto = {at.ident_of.get(e.name) for e in elements if e.kind is BasisKind.NODE}
    held = sorted(
        (i, e) for o in onto for i, e in at.edges_onto.get(("qualifies", o), ())
    )
    found: list[BasisElement] = []
    for _, e in held:
        found += [_edge(at, e), _node(at, at.node_of[_source(e)])]
    return found


#: The aspects whose basis names no edge (`docs/model.md` § 8's table): no
#: qualifier joins their hop.
_TEXT_ALONE = (
    Aspect.CITED,
    Aspect.PRESENTATION,
    Aspect.SIGNIFICANCE,
    Aspect.NOVELTY,
    Aspect.LITERATURE,
)


def one_hop(
    at: Seen, aspect: Aspect, nodes: Sequence[str], file: str | None = None
) -> tuple[BasisElement, ...]:
    """A reading's subject and the one hop of edges its aspect relies on, at
    a state: `nodes` the subject's (J and T for
    `valid`; T may be absent, then every justifies edge out of or onto J),
    or a finding's anchors' nodes, each read under the aspect; `file`, a
    `computed` subject's code file. Elements named twice are
    merged, a node named whole once whole."""
    found: list[BasisElement] = []
    if aspect is Aspect.VALID:
        found = _valid(at, nodes[0], nodes[1] if len(nodes) > 1 else None)
    else:
        for node in nodes:
            if node not in at.ident_of:
                continue
            if aspect in (Aspect.TRUE, Aspect.WELL_POSED):
                found += [_node(at, node), *_relied(at, _ids(at, node), "uses")]
            elif aspect is Aspect.ACCURATE:
                found += _accurate(at, node)
            elif aspect is Aspect.COMPUTED:
                found += _computed(at, node, file)
            else:
                found.append(_node(at, node))
    if aspect in _TEXT_ALONE:
        return merged(found)
    return merged([*found, *_qualifiers(at, found)])


def _key(e: BasisElement) -> tuple[Any, ...]:
    if e.kind is BasisKind.EDGE:
        return (e.kind, e.name, e.edge, e.parts)
    return (e.kind, e.name)


def merged(elements: Iterable[BasisElement]) -> tuple[BasisElement, ...]:
    """Each element once, in first-named order; a node's parts the union of
    those named, or none (whole) where it is named whole once."""
    found: dict[tuple[Any, ...], BasisElement] = {}
    for e in elements:
        key = _key(e)
        held = found.get(key)
        if held is None:
            found[key] = e
        elif e.kind is BasisKind.NODE and held.parts:
            parts = () if not e.parts else tuple(dict.fromkeys(held.parts + e.parts))
            found[key] = BasisElement(e.kind, e.name, held.text, parts)
    return tuple(found.values())


# --- the lapse ---------------------------------------------------------------------


class CauseKind(StrEnum):
    """Why a reading lapsed, in the order its status names the first."""

    DELETED = "deleted"
    TEXT = "text"
    MEANING = "meaning"
    EDGE = "edge"
    CITED_FILE = "cited-file"
    RUN = "run"


_ORDER = {kind: i for i, kind in enumerate(CauseKind)}


@dataclass(frozen=True)
class Cause:
    """One cause of a lapse: at the step into `state` from `parent`, what
    happened to `element`, in words (`why`), and its data."""

    kind: CauseKind
    parent: str
    state: str
    element: BasisElement
    why: str
    data: Mapping[str, Any] = field(default_factory=dict)


def first(causes: Sequence[Cause]) -> Cause | None:
    """The cause a status names: by kind (deleted, text, meaning, edges),
    then the earliest."""
    return min(causes, key=lambda c: _ORDER[c.kind], default=None)


@dataclass(frozen=True)
class Walked:
    """A reading walked to a state: why it lapsed, and its subject there:
    its nodes (None where deleted) and its claim's words."""

    causes: tuple[Cause, ...]
    nodes: tuple[str | None, ...]
    words: str | None = None

    @property
    def lapsed(self) -> bool:
        return bool(self.causes)


def _claim(step: Step, node: str, words: str) -> tuple[str, str] | None:
    """The child's claim that continues the parent's `words` on `node`
    (`versions.continued_parts`: within its node, then into a piece of its
    split or a merge's result), None where none does."""
    parent, child = step.parent, step.child
    if (node, words) in parent.standings:
        j = step.continued_claim.get(parent.standings.index((node, words)))
    else:
        older = [*parent.standings, (node, words)]
        pairs = versions.continued_parts(older, child.standings, step.names)
        j = next((j for j, k in pairs.items() if k == len(older) - 1), None)
    return None if j is None else child.standings[j]


def _moved(t: versions.Transition, block: str) -> list[Mapping[str, Any]]:
    handle = t.handles.get(block, block)
    return [r for r in t.update.get("moved_by_rule", []) if r["handle"] == handle]


def _class(t: versions.Transition, block: str | None) -> str | None:
    """Identity's class for a block of the child, across versions."""
    if block is None or not t.across:
        return None
    link = t.link_of_new.get(block)
    return None if link is None else link.cls


def _heir(t: versions.Transition, node: str, child: Seen) -> str | None:
    """The child's node that took in an ended node's text, None where none
    did (deleted)."""
    return next(
        (n for n, came in t.origins.items() if node in came and n in child.ident_of),
        None,
    )


def texts_changed(
    t: versions.Transition, parent: Seen, child: Seen, nodes: Sequence[str]
) -> list[str]:
    """The block nodes among `nodes` whose text changed into the child:
    identity says so (`CHANGING`, a macro it uses among them), or its words
    differ, whitespace collapsed as identity's own `norm` collapses it (a
    re-wrap, a move or identity's `same` is no change), not by markup
    alone. The kept text holds no comment, so no line break ending one
    is collapsed."""
    found = []
    for node in nodes:
        if node in parent.members or node in child.members:
            continue
        cls = _class(t, child.ident_of.get(node))
        before, after = parent.words_of.get(node), child.words_of.get(node)
        if cls in CHANGING or (before != after and cls != MARKUP):
            found.append(node)
    return found


def _node_causes(e: BasisElement, step: Step, subject: bool) -> list[Cause]:
    t, parent, child = step.t, step.parent, step.child
    p, c = parent.state.id, child.state.id
    node = e.name
    if node not in child.ident_of:
        heir = _heir(t, node, child)
        if heir is None:
            kind = CauseKind.DELETED if subject else CauseKind.TEXT
            return [Cause(kind, p, c, e, f"{parent.handle(node)} was deleted")]
        why = f"{parent.handle(node)} was merged into {child.handle(heir)} ({heir})"
        return [Cause(CauseKind.TEXT, p, c, e, why, {"into": heir})]
    now = child.members.get(node, (node,))
    changed = texts_changed(t, parent, child, [m for m in now if m in parent.text_of])
    if node in child.members and parent.members.get(node) != now:
        changed = changed or [node]
    found: list[Cause] = []
    if changed:
        classes = sorted(
            {_class(t, child.ident_of.get(m)) or "edited" for m in changed}
        )
        found.append(
            Cause(
                CauseKind.TEXT,
                p,
                c,
                e,
                f"{child.handle(node)}'s text changed ({', '.join(classes)})",
                {"nodes": changed, "classes": classes},
            )
        )
    if not t.across:
        return found + _corrected_causes(e, step, now, node)
    for m in now:
        block = child.ident_of.get(m)
        if block is None:
            continue
        lines = [d for d in t.update.get("deltas", []) if d["block"] == block]
        deltas = {d["entry"]: d["class"] for d in lines}
        if deltas and carries(deltas, e.parts if m == node else ()):
            shown = [
                cls + (f" [{entry}]" if entry else "")
                for entry, cls in deltas.items()
                if cls not in CLOSING
            ]
            found.append(
                Cause(
                    CauseKind.MEANING,
                    p,
                    c,
                    e,
                    f"{child.handle(m)} has a Δ that carries: {', '.join(shown)}",
                    {"node": m, "deltas": lines},
                )
            )
        for r in _moved(t, block):
            found.append(
                Cause(
                    CauseKind.MEANING,
                    p,
                    c,
                    e,
                    f"{child.handle(m)} was moved by the rule",
                    {"node": m, "causes": list(r["causes"])},
                )
            )
    return found


def _corrected_causes(
    e: BasisElement, step: Step, now: Sequence[str], node: str
) -> list[Cause]:
    """On a step within a version: a Δ entry a person or
    an agent corrected that now carries through the element's parts, and an
    owner the corrected marks newly move by rule. A correction that closes
    the change lapses nothing, and none takes a lapse back."""
    child, record = step.child, step.t.update
    p, c = step.parent.state.id, child.state.id
    found: list[Cause] = []
    for m in now:
        block = child.ident_of.get(m)
        if block is None:
            continue
        lines = step.corrected.get(block, [])
        deltas = {d["entry"]: d["class"] for d in lines}
        if deltas and carries(deltas, e.parts if m == node else ()):
            shown = [
                cls + (f" [{entry}]" if entry else "")
                for entry, cls in deltas.items()
                if cls not in CLOSING
            ]
            found.append(
                Cause(
                    CauseKind.MEANING,
                    p,
                    c,
                    e,
                    f"{child.handle(m)}'s Δ was corrected, and carries: "
                    + ", ".join(shown),
                    {"node": m, "deltas": lines},
                )
            )
        # a cut edit moves the record's handles onto its heirs: only a step
        # that corrected a Δ moves anything by rule
        if step.corrected and m not in step.moved_before:
            for r in _moved_record(record, child, block):
                found.append(
                    Cause(
                        CauseKind.MEANING,
                        p,
                        c,
                        e,
                        f"{child.handle(m)} was moved by the rule, its Δs corrected",
                        {"node": m, "causes": list(r["causes"])},
                    )
                )
    return found


def _ident(at: Seen, handle: str) -> str:
    """A handle of the state's export as its block's or group's id."""
    return at.ident_of_handle.get(handle, handle)


def _moved_record(
    record: Mapping[str, Any], at: Seen, block: str
) -> list[Mapping[str, Any]]:
    handle = at.handles.get(block, block)
    return [r for r in record.get("moved_by_rule", []) if r["handle"] == handle]


def _paper_causes(
    e: BasisElement, t: versions.Transition, parent: Seen, child: Seen
) -> list[Cause]:
    before = {n.node for n in parent.state.nodes}
    after = {n.node for n in child.state.nodes}
    changed = texts_changed(t, parent, child, sorted(before & after))
    begun, ended = sorted(after - before), sorted(before - after)
    if not (changed or begun or ended):
        return []
    why = (
        f"the paper changed: {len(changed)} blocks' text, {len(begun)} added, "
        f"{len(ended)} ended"
    )
    data = {"changed": changed, "begun": begun, "ended": ended}
    return [Cause(CauseKind.TEXT, parent.state.id, child.state.id, e, why, data)]


def _edge_line(at: Seen, e: BasisElement) -> str:
    assert e.edge is not None
    kind, target = e.edge
    parts = f" [{'; '.join(e.parts)}]" if e.parts else ""
    return f"{at.handle(e.name)} → {kind} {at.handle(target)}{parts}"


def _continued_edge(e: BasisElement, step: Step) -> BasisElement | None:
    """The child's edge that continues `e` under the naming across states: its kind,
    its source and target named by `e`'s ends, its parts the child's parts
    that continue `e`'s, and the claims it names left aside
    (`_legend_parts`). Across versions a part continues by shared
    characters within its node, so a part reworded is no edge gone; within
    a version the text is the same, so only the same words continue it, and
    an edit that points the edge at other words of its target is a gone
    edge and an added one."""
    assert e.edge is not None
    kind, target = e.edge
    own = _legend_parts(e.parts)
    wanted = own
    if step.t.across:
        onto = step.continued_words
        wanted = tuple(sorted(onto.get((target, w), w) for w in own))
    for source, t, parts, legend in step.child_edges.get((kind, e.name), ()):
        if legend in (own, wanted) and target in step.names(t):
            return BasisElement(BasisKind.EDGE, source, None, tuple(parts), (kind, t))
    return None


def _legend_parts(parts: Sequence[str]) -> tuple[str, ...]:
    """An edge's parts' words without the claims it names
    (`exchange.named_claims_key`): naming, narrowing or re-pointing a line's
    claim continues the edge, since the cover, not the edge, decides which
    claim's judgments are read."""
    return tuple(w for w in parts if not w.startswith(("⊢ ", "⊣ ")))


def _step(
    e: BasisElement, step: Step, subject: Sequence[str | None]
) -> tuple[list[Cause], BasisElement | None]:
    """What a step does to one element: its causes, and the element as the
    child holds it (None where it ended)."""
    t, parent, child = step.t, step.parent, step.child
    p, c = parent.state.id, child.state.id
    if e.kind is BasisKind.NODE:
        held = e.name in child.ident_of
        return _node_causes(e, step, e.name in subject), (
            BasisElement(e.kind, e.name, child.text_of.get(e.name), e.parts)
            if held
            else None
        )
    if e.kind is BasisKind.FILE:
        now = child.state.files.get(e.name)
        changed = now != parent.state.files.get(e.name)
        why = f"file {e.name} changed"
        return [Cause(CauseKind.TEXT, p, c, e, why)] if changed else [], BasisElement(
            e.kind, e.name, now
        )
    if e.kind is BasisKind.PAPER:
        return _paper_causes(e, t, parent, child), _paper(child)
    found = _continued_edge(e, step)
    if found is not None:
        return [], found
    line = _edge_line(parent, e)
    return [
        Cause(CauseKind.EDGE, p, c, e, f"the edge {line} is gone", {"line": line})
    ], None


class HopKind(StrEnum):
    """What a reading's one hop reads at a state."""

    #: a judgment's subject's (`judged_hop`)
    JUDGED = "judged"
    #: each of a finding's anchors' (`anchored_hop`)
    ANCHORED = "anchored"
    #: nothing: a ruling's or a check's claim, walked for its words alone
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Hop:
    """A reading's one hop at a state, from its subject's nodes there (None
    where one ended): a value, so a walk is named by what it reads."""

    kind: HopKind
    aspect: Aspect | None = None
    file: str | None = None

    def __call__(self, at: Seen, nodes: Sequence[str | None]) -> Sequence[BasisElement]:
        if self.kind is HopKind.NONE:
            return ()
        assert self.aspect is not None
        if self.kind is HopKind.ANCHORED:
            return merged(
                e
                for node in nodes
                if node is not None
                for e in one_hop(at, self.aspect, [node])
            )
        if None in nodes:
            return ()
        return one_hop(at, self.aspect, [n for n in nodes if n is not None], self.file)


def judged_hop(aspect: Aspect, file: str | None = None) -> Hop:
    """A judgment's one hop: its subject's (J and T for `valid`; the block
    and its code file for `computed`); none once J or T ended, which the
    judgment lapsed by."""
    return Hop(HopKind.JUDGED, aspect, file)


def anchored_hop(aspect: Aspect) -> Hop:
    """A finding's one hop: each anchor's, read under its aspect."""
    return Hop(HopKind.ANCHORED, aspect)


#: The hop of a reading walked for its claim's words alone.
NO_HOP = Hop(HopKind.NONE)


@dataclass(frozen=True, slots=True)
class Carry:
    """A walk as it stands at a state: its causes so far, its subject's
    nodes and claim's words there, and the basis it carries on. A walk to a
    later state goes on from it, and gives what the whole walk gives."""

    causes: tuple[Cause, ...]
    subject: tuple[str | None, ...]
    words: str | None
    tracked: tuple[BasisElement, ...]


def walk(
    reader: Reader,
    made_on: str,
    hop: Hop,
    nodes: Sequence[str | None],
    recorded: Sequence[BasisElement],
    at: State,
    words: str | None = None,
    retracted: Sequence[BasisElement] = (),
) -> Walked | None:
    """A reading made on state `made_on`, on the subject `nodes` with its
    recorded basis and its `hop`, walked to `at`. Its basis at `made_on` is
    the one hop there, less what its review's own lines took away
    (`retracted`), with the recorded basis: so a hop rule that names more
    than a record's lapses nothing by itself. At each step an element of
    the basis that ends, changes or is gone is a cause, and so is an edge
    the child's one hop holds that the basis never had; the basis goes on
    as the child holds it, with the child's one hop. `words` are a claim's
    on the subject's last node (T's for `valid`), carried by `_claim`. None
    where `made_on` is not along `at`'s parents (the reading does not bear
    on that state). The walk goes on from the latest state along the path
    it was carried to before (`Reader.carried`)."""
    path = reader.path(made_on, at)
    if path is None:
        return None
    key = (made_on, hop, tuple(nodes), tuple(recorded), words, tuple(retracted))
    begun, carry = reader.carried(key, path)
    # a carried walk's basis holds its hop; a new one's may not (its own
    # lines retracted from it)
    synced = carry is not None
    if carry is None:
        taken = {_key(e) for e in retracted}
        carry = Carry(
            (),
            tuple(nodes),
            words,
            merged(
                [
                    *recorded,
                    *(
                        e
                        for e in hop(reader.seen(path[0]), list(nodes))
                        if _key(e) not in taken
                    ),
                ]
            ),
        )
    if begun < len(path) - 1:
        carry = _walked(reader, hop, path[begun:], carry, synced)
        reader.carry(key, at, carry)
    return Walked(carry.causes, carry.subject, carry.words)


def _untouched(
    touched: frozenset[str],
    subject: Sequence[str | None],
    tracked: Sequence[BasisElement],
) -> bool:
    """Whether a step that touched `touched` leaves alone every node the
    walk reads, and, where its basis holds the paper, every node."""
    if touched and any(e.kind is BasisKind.PAPER for e in tracked):
        return False
    return touched.isdisjoint(_nodes_read(subject, tracked))


def _nodes_read(
    subject: Sequence[str | None], tracked: Sequence[BasisElement]
) -> set[str]:
    """The nodes a walk reads at a step: its subject's, and each node and
    edge end its basis holds."""
    found = {n for n in subject if n is not None}
    for e in tracked:
        if e.kind in (BasisKind.NODE, BasisKind.EDGE):
            found.add(e.name)
        if e.kind is BasisKind.EDGE and e.edge is not None:
            target = e.edge[1]
            if isinstance(target, str):
                found.add(target)
    return found


def _walked(
    reader: Reader, hop: Hop, path: Sequence[State], carry: Carry, synced: bool
) -> Carry:
    """`carry`, at `path`'s first state, walked along it (`walk`). Where
    its basis holds its hop (`synced`, and after any step walked), a step
    that touched none of what it reads is passed over."""
    subject = list(carry.subject)
    words = carry.words
    tracked = carry.tracked
    causes = list(carry.causes)
    for parent_state, child_state in zip(path, path[1:], strict=False):
        touched = reader.touched(parent_state, child_state)
        if synced and touched is not None and _untouched(touched, subject, tracked):
            # a step on the same text that changed none of what the walk
            # reads leaves it as it was
            continue
        synced = True
        step = reader.step(parent_state, child_state)
        t, parent, child = step.t, step.parent, step.child
        carried: list[BasisElement] = []
        for e in tracked:
            found, held = _step(e, step, subject)
            causes += found
            if held is not None:
                carried.append(held)
        claim = (
            None
            if words is None or subject[-1] is None
            else _claim(step, subject[-1], words)
        )
        if words is not None and subject[-1] is not None and claim is None:
            # a part continues, else it lapses on the heir
            ended = BasisElement(BasisKind.NODE, subject[-1])
            causes.append(
                Cause(
                    CauseKind.DELETED,
                    parent_state.id,
                    child_state.id,
                    ended,
                    f'the claim "{words}" of {parent.handle(subject[-1])} ended',
                    {"words": words},
                )
            )
            words = None
        subject = [
            None if n is None else n if n in child.ident_of else _heir(t, n, child)
            for n in subject
        ]
        if claim is not None:
            subject[-1], words = claim
        held_keys = {_key(e) for e in carried}
        now = hop(child, subject)
        for e in now:
            if e.kind is BasisKind.EDGE and _key(e) not in held_keys:
                line = _edge_line(child, e)
                causes.append(
                    Cause(
                        CauseKind.EDGE,
                        parent_state.id,
                        child_state.id,
                        e,
                        f"the edge {line} was added",
                        {"line": line, "added": True},
                    )
                )
        tracked = merged([*carried, *now])
    return Carry(tuple(causes), tuple(subject), words, tracked)


def rested_on(
    j: Judgment | Ruling,
    order: FiledAt,
    seen_files: Sequence[tuple[FiledAt, FileSeen]],
) -> tuple[dict[str, FileStamp], dict[str, FileStamp]]:
    """The files a judgment or a ruling rests on, by key, each it read a page of: as it
    was given it; else, a file it was not given (one a source's outage left
    unstamped when it was laid in), the file the store last saw there when
    it was filed, else the first it saw there after. And the store's latest
    stamp of every key, the corpus as it knows it now (docs/model.md § 8)."""
    known: dict[str, FileStamp] = {}
    first: dict[str, FileStamp] = {}
    latest: dict[str, FileStamp] = {}
    for when, f in sorted(seen_files, key=lambda item: item[0]):
        latest[f.stamp.key] = f.stamp
        if unseen_by(when, order):
            first.setdefault(f.stamp.key, f.stamp)
        else:
            known[f.stamp.key] = f.stamp
    given = first | known | {g.key: g for g in j.given}
    read = dict.fromkeys(p.key for p in j.pages if p.read)
    return {k: given[k] for k in read if k in given}, latest


def files_rested_on(
    store: RecordStore, j: Judgment
) -> tuple[dict[str, FileStamp], dict[str, FileStamp]]:
    """`rested_on`, from the store."""
    held = store.judgment_records()
    filed = store.filed_order()
    seen_files = [(filed.get(f.answer or "", NOT_FILED), f) for f in held.files]
    return rested_on(j, filed.get(j.answer or "", NOT_FILED), seen_files)


def sourced(
    j: Judgment | Ruling,
    order: FiledAt,
    seen_files: Sequence[tuple[FiledAt, FileSeen]],
    contents: Mapping[str, str],
    at: str,
) -> list[Cause]:
    """A judgment's or a ruling's file causes: each file it rests on (`rested_on`) whose
    latest stamp prints other content, or is absent or held back, unless a
    file of its work prints that content now under another key: a file
    that changed key moved (docs/architecture.md § 9). Another reading of an
    unchanged file lapses nothing; an outage is never filed, so says
    nothing."""
    if not any(p.read for p in j.pages):
        return []
    rests, latest = rested_on(j, order, seen_files)
    causes = []
    for key, was in rests.items():
        now = latest.get(key)
        if now is None or (now.read and same_content(was, now, contents)):
            continue
        if any(
            work_of(k) == work_of(key) and f.read and same_content(was, f, contents)
            for k, f in latest.items()
        ):
            continue
        before = _short(was, contents)
        what = (
            f"is {now.read_status.value}"
            if not now.read
            else f"prints other content ({_short(now, contents)}"
            + (f", not {before})" if before else ")")
        )
        causes.append(
            Cause(
                CauseKind.CITED_FILE,
                j.state,
                at,
                BasisElement(BasisKind.CITED_FILE, key, _short(now, contents)),
                f"the cited file {key} {what} now",
                {
                    "file": key,
                    "was": before,
                    "now": _short(now, contents),
                    "read_status": now.read_status.value,
                },
            )
        )
    return causes


def _short(stamp: FileStamp, contents: Mapping[str, str]) -> str:
    """A file as a cause names it: its content hash (past its form's tag),
    else its SHA-256, each by its first twelve digits; empty where it
    holds none."""
    content = stamp.content or contents.get(stamp.file, "")
    return (content.rpartition(":")[2] or stamp.file)[:12]


def _rerun(
    j: Judgment, order: FiledAt, seen_runs: Sequence[tuple[FiledAt, RunSeen]], at: str
) -> list[Cause]:
    """A `computed` judgment's run cause: the latest run of the
    same call since its filing (`order`), where it ran and printed another
    output. A run that did not run since says nothing of the output."""
    if j.run is None:
        return []
    latest = None
    for when, r in sorted(seen_runs, key=lambda item: item[0]):
        if unseen_by(when, order) and r.stamp.ran and r.stamp.call == j.run.call:
            latest = r.stamp
    if latest is None or latest.output == j.run.output:
        return []
    return [
        Cause(
            CauseKind.RUN,
            j.state,
            at,
            BasisElement(BasisKind.RUN, j.run.name, latest.output),
            f"the run {j.run.name} printed another output since "
            f"({latest.output[:12]}, not {j.run.output[:12]})",
            {"run": j.run.name, "was": j.run.output, "now": latest.output},
        )
    ]


def _evidence_causes(store: RecordStore, j: Judgment, at: str) -> list[Cause]:
    held = store.judgment_records()
    filed = store.filed_order()
    seen_files = [(filed.get(f.answer or "", NOT_FILED), f) for f in held.files]
    seen_runs = [(filed.get(r.answer or "", NOT_FILED), r) for r in held.runs]
    order = filed.get(j.answer or "", NOT_FILED)
    contents = file_contents(held.files)
    return sourced(j, order, seen_files, contents, at) + _rerun(j, order, seen_runs, at)


# --- filing ------------------------------------------------------------------------


@dataclass(frozen=True)
class Author:
    """Who files: a person, unnamed (`person`) or by name (the model's
    place), or an agent with its model and session."""

    host: str = PERSON
    model: str = PERSON
    session: str | None = None

    @property
    def name(self) -> str:
        if self.host == PERSON and self.model == PERSON:
            return PERSON
        return f"{self.host} {self.model}"

    @property
    def identity(self) -> str:
        """Who speaks: the name without the host's version, which a host's
        name carries after its harness (`claude-code 2.1.310`), so the same
        reader under an upgraded host is the same author. The name keeps
        the version, for history."""
        if self.person:
            return self.name
        return f"{self.host.split(' ', 1)[0]} {self.model}"

    @property
    def person(self) -> bool:
        return self.host == PERSON

    @property
    def proposes(self) -> bool:
        """An agent filing by hand, outside a review: its answer to a finding
        is a proposal, never what closes it."""
        return self.host == AGENT

    @property
    def reviewer(self) -> bool:
        """A person outside the paper reviewing it through a journal, under
        its label: neither the author nor an agent, and no review session."""
        return self.host == REVIEWER

    @property
    def by_review(self) -> bool:
        """A review session, filing as its host's harness: neither a person,
        an agent by hand, nor an outside reviewer."""
        return not self.person and not self.proposes and not self.reviewer


def _claims(at: Seen, node: str) -> list[str]:
    return [words for n, words in at.standings if n == node]


def covered_claims(at: Seen, edge: Mapping[str, Any]) -> list[str]:
    """The claims of its target a justifies edge argues (model § 4, its
    cover): those it names, else every `justified` claim of the target."""
    target = at.node_of.get(_onto(edge) or "")
    held = [
        (words, s["status"])
        for (node, words), s in zip(
            at.standings, at.export.get("standings", []), strict=True
        )
        if node == target
    ]
    named = {norm(w) for w in edge["target"].get("claims") or []}
    return [w for w, _ in held if norm(w) in named] or [
        w for w, status in held if status == "justified"
    ]


def _chosen_claim(at: Seen, node: str, claim: str | None, required: bool) -> str | None:
    """The claim of `node` that `claim` names (its words, or words found in
    one claim only), else its only claim; refused where it names none or
    several, or where the block holds several and none is named."""
    held = _claims(at, node)
    if claim is not None:
        wanted = norm(claim)
        found = [w for w in held if w == wanted] or [w for w in held if wanted in w]
        if len(found) != 1:
            raise JudgmentError(
                f"--claim {claim!r} names {len(found)} of {at.handle(node)}'s claims: "
                + "; ".join(f'"{w}"' for w in held)
            )
        return found[0]
    if len(held) == 1:
        return held[0]
    if not held and not required:
        return None
    if not held:
        raise JudgmentError(f"{at.handle(node)} holds no claim (no standing)")
    raise JudgmentError(
        f"{at.handle(node)} holds {len(held)} claims: name one with --claim: "
        + "; ".join(f'"{w}"' for w in held)
    )


def subject(
    at: Seen,
    aspect: Aspect,
    on: str,
    target: str | None = None,
    claim: str | None = None,
    span: str | None = None,
    text: str | None = None,
    work: str | None = None,
    file: str | None = None,
) -> Subject:
    """A judgment's subject at a kept state, from block or group ids: `on`
    (J for `valid`), `target` (T for `valid`, else none), a claim's words
    where its block holds several, a span's excerpt for `cited`, found once
    in `text`, the block's, the work it cites, one of the block's, and the
    code file a `computed` block uses. Refused, with why, where
    the state has no such subject."""
    if aspect not in JUDGED_ASPECTS:
        raise JudgmentError(f"{aspect.value} is a finding's aspect, not a judgment's")
    if work is not None and aspect is not Aspect.CITED:
        raise JudgmentError("--work names a cited judgment's work")
    if file is not None and aspect is not Aspect.COMPUTED:
        raise JudgmentError("--file names a computed judgment's code file")
    node = at.node_of.get(on)
    if node is None:
        raise JudgmentError(f"{on!r} is no block or group of state {at.state.id}")
    if aspect is Aspect.VALID:
        offered = [e for e in at.edges if e["kind"] == "justifies" and _source(e) == on]
        targets = list(dict.fromkeys(_onto(e) for e in offered))
        if target is None and len(targets) == 1:
            target = targets[0]
        if target is None or target not in targets:
            shown = (
                ", ".join(at.handle(at.node_of.get(t, t)) for t in targets if t)
                or "nothing"
            )
            raise JudgmentError(
                f"{at.handle(node)} justifies {shown}: name its claim's block with "
                "--for"
            )
        t_node = at.node_of[target]
        return Subject(node, t_node, _chosen_claim(at, t_node, claim, False))
    if target is not None:
        raise JudgmentError("--for names T, the claim a `valid` judgment's J argues")
    if node in at.members and aspect is not Aspect.VALID:
        raise JudgmentError(f"a {aspect.value} judgment is on a block, not a group")
    if aspect is Aspect.TRUE:
        return Subject(node, None, _chosen_claim(at, node, claim, True))
    if aspect is Aspect.ACCURATE:
        if not any(e["kind"] == "about" and _source(e) == on for e in at.edges):
            raise JudgmentError(f"{at.handle(node)} describes nothing (no about edge)")
        return Subject(node)
    if aspect is Aspect.COMPUTED:
        # one judgment per uses edge: the printed values it covers are its
        # evidence's
        if claim is not None or span is not None:
            raise JudgmentError(
                "a computed judgment is on a block and its code file: name the "
                "printed values in its evidence"
            )
        used = code_uses(at, node)
        if file is None and len(used) == 1:
            file = used[0]
        if file is None or file not in used:
            raise JudgmentError(
                f"{at.handle(node)} uses "
                + (", ".join(f"file:{f}" for f in used) or "no code file")
                + (f", not file:{file}" if file else ": name one with --file")
            )
        return Subject(node, file=file)
    if not span:
        raise JudgmentError("a cited judgment names its citation site with --span")
    found = 0 if text is None else norm(text).count(norm(span))
    if found != 1:
        raise JudgmentError(
            f"--span {span!r} is found {found} times in {at.handle(node)}'s text, "
            "not once"
        )
    cited = at.works_of.get(node, ())
    if work is None:
        raise JudgmentError(
            f"a cited judgment names its work with --work: {at.handle(node)} cites "
            + (", ".join(cited) or "no work")
        )
    if work not in cited:
        raise JudgmentError(
            f"{at.handle(node)} cites {', '.join(cited) or 'no work'}, not {work}"
        )
    return Subject(node, None, norm(span), work)


def file_records(
    store: RecordStore, kind: str, records: JudgmentRecords, author: Author
) -> Filed:
    # the time and session make each filing its own answer: an author may
    # file again what it filed before (A, then B, then A), the latest
    # speaking
    raw = json.dumps(
        {
            "kind": kind,
            "author": {
                "host": author.host,
                "model": author.model,
                "session": author.session,
            },
            "at": datetime.now(UTC).isoformat(timespec="microseconds"),
            "records": [judgment_data(r) for r in records.all()],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    try:
        return Resolver(store).file(
            kind,
            FILING_VERSION,
            raw,
            records,
            author.host,
            author.model,
            author.session,
        )
    except ValueError as error:
        raise JudgmentError(str(error)) from None


#: The aspects a judgment may read cited pages for: a `cited` judgment's
#: work, and for `valid` and `true` any work the paper cites (the argument
#: reader checks a result's hypotheses against its page, wherever the
#: paper cites it).
PAGED_ASPECTS = (Aspect.CITED, Aspect.VALID, Aspect.TRUE)


def subject_works(at: Seen, aspect: Aspect, on: Subject) -> tuple[str, ...]:
    """The works a subject cites: a cited one's own work; for `valid`, the
    works J's blocks cite; for `true`, the works its claim's block cites.
    A reading records each of their files, read or not, so a work that
    gains a file asks it again (`grown`)."""
    if aspect is Aspect.CITED:
        return () if on.work is None else (on.work,)
    if aspect not in PAGED_ASPECTS:
        return ()
    nodes = at.members.get(on.node) or (on.node,)
    return tuple(dict.fromkeys(k for n in nodes for k in at.works_of.get(n, ())))


def paper_works(at: Seen) -> tuple[str, ...]:
    """Every work the paper cites, in the paper's order."""
    return tuple(dict.fromkeys(k for keys in at.works_of.values() for k in keys))


def readable_works(at: Seen, sources: Sources | None) -> tuple[str, ...]:
    """Every work a `valid` or `true` reading, or a ruling, may read a page
    of: those the paper cites, wherever it cites them, and each other entry
    of its bibliography (`Sources.entries`; docs/model.md § 8)."""
    listed = () if sources is None else sources.entries()
    return tuple(dict.fromkeys((*paper_works(at), *listed)))


def reading_works(
    at: Seen, aspect: Aspect, on: Subject, pages: Iterable[PageStamp]
) -> tuple[str, ...]:
    """The works a reading records: its subject's (`subject_works`), and of
    a `valid` or `true` one each other work it read a page of (`pages`)."""
    cited = subject_works(at, aspect, on)
    if aspect is Aspect.CITED:
        return cited
    read = (work_of(p.key) for p in pages)
    return tuple(dict.fromkeys((*cited, *read)))


def uncited_works(
    at: Seen, aspect: Aspect, on: Subject, pages: Iterable[PageStamp]
) -> tuple[str, ...]:
    """Of the works a reading's pages are of, those its subject does not
    cite: what `show` names beside its pages."""
    cited = subject_works(at, aspect, on)
    return tuple(dict.fromkeys(w for p in pages if (w := work_of(p.key)) not in cited))


def files_seen(seen: Iterable[FileSeen]) -> dict[str, FileStamp]:
    """Each cited file's latest stamp, by key: the corpus as the store
    knows it. An outage is never filed, so says nothing."""
    return {f.stamp.key: f.stamp for f in seen}


def file_contents(seen: Iterable[FileSeen]) -> dict[str, str]:
    """Each file's content hash the store knows, by its SHA-256: hashed once
    for each file; a stamp with none (`FileStamp.content`) gives none
    (`evidence.same_content`)."""
    return {f.stamp.file: f.stamp.content for f in seen if f.stamp.content}


@dataclass(frozen=True, slots=True)
class Growth:
    """A work whose files a reading was given less of than the corpus
    holds now, and how, in words."""

    work: str
    why: str


def grown(
    s: SubjectStatus,
    at: Seen,
    files: Mapping[str, FileStamp],
    contents: Mapping[str, str] | None = None,
) -> tuple[Growth, ...]:
    """Why a subject is asked again, each in words: its held reading did
    not verify, and against the latest live reading on it, of the works it
    records (`reading_works`: its subject's, and each other it read a page
    of) a file reads now (`files`, the corpus as last seen) whose content
    it was not given (`evidence.same_content`, `contents` the store's), or
    the file under a work's key is one it was given under another (its
    cited edition changed). A reading that records no
    files is asked again where a work it named unread reads now, or a work
    holds another file that reads and it stamped no page of (docs/model.md
    § 8). Empty for any other subject: a `verified` reading stands while its
    files stand, a lapsed or a disputed one is asked otherwise."""
    if s.status is not StatusKind.HELD or s.held is None:
        return ()
    if s.held.judgment.verdict is Verdict.VERIFIED or s.aspect not in PAGED_ASPECTS:
        return ()
    # a later reading given the corpus as it is settles it, whoever's speaks
    latest = max((r for r in s.reads if r.live), key=lambda r: r.order, default=s.held)
    j = latest.judgment
    works = set(reading_works(at, s.aspect, s.subject, j.pages))
    now = sorted((k, f) for k, f in files.items() if work_of(k) in works and f.read)
    why: list[Growth] = []
    if j.given:
        was = {g.key: g for g in j.given}
        given = [g for g in j.given if g.read]
        for key, f in now:
            same = next((g for g in given if same_content(g, f, contents)), None)
            if same is None:
                then = was.get(key)
                unread = then is not None and not then.read
                why.append(
                    Growth(
                        work_of(key),
                        f"{key} reads a file it was not given"
                        + (f" ({then.read_status.value} then)" if unread else ""),
                    )
                )
            elif key == work_of(key) and same.key != key:
                why.append(
                    Growth(
                        key,
                        f"{key}'s cited edition is now the file it was given as "
                        f"{same.key}",
                    )
                )
        return tuple(why)
    named = {u.key: u.read_status for u in j.unread}
    for key, _ in now:
        if key in named:
            why.append(
                Growth(
                    key, f"read with {key} unread ({named[key].value}); it reads now"
                )
            )
        elif key != work_of(key) and key not in {p.key for p in j.pages}:
            why.append(
                Growth(
                    work_of(key), f"{work_of(key)} holds a file it was not given, {key}"
                )
            )
    return tuple(why)


def read_pages(
    on: Subject,
    verdict: Verdict,
    pages: Sequence[tuple[str, int]],
    sources: Sources | None,
    aspect: Aspect = Aspect.CITED,
    works: Sequence[str] = (),
) -> tuple[PageStamp, ...]:
    """A judgment's cited pages, each read again through `sources` and
    stamped there, never as an author gives a stamp. Refused: a page of
    another work than a cited judgment's own; for `valid` and `true` (and
    a ruling's), a page of a work outside `works`, those the paper cites
    and its bibliography holds (`readable_works`); for `cited`, a verdict
    on what the work holds with no
    page read; for `valid` and `true`, any page that failed to read (a
    failed read is never evidence)."""
    for key, index in pages:
        # a page of any file of the work: its cited edition or another
        if aspect is Aspect.CITED and work_of(key) != on.work:
            raise JudgmentError(
                f"--page {key}:{index} is a page of {key}, not of the cited work "
                f"{on.work}"
            )
        if aspect is not Aspect.CITED and work_of(key) not in works:
            raise JudgmentError(
                f"--page {key}:{index} is a page of {key}, which the paper cites "
                "nowhere and its bibliography does not hold"
            )
    stamps: tuple[PageStamp, ...] = ()
    if pages:
        if sources is None:
            raise JudgmentError("a cited page is read through the paper's sources")
        stamps = tuple(dict.fromkeys(sources.stamp(key, i) for key, i in pages))
    if aspect is not Aspect.CITED:
        failed = [s for s in stamps if not s.read]
        if failed:
            raise JudgmentError(
                "a failed read is never evidence: "
                + "; ".join(f"{s.name} {s.read_status.value}" for s in failed)
            )
        return stamps
    if verdict in READ_VERDICTS and not any(s.read for s in stamps):
        failed = "; ".join(f"{s.name} {s.read_status.value}" for s in stamps)
        raise JudgmentError(
            f"a {verdict.value} cited judgment rests on a page read: name one with "
            f"--page KEY:INDEX"
            + (f" (read: {failed}; a failed read is never evidence)" if failed else "")
        )
    return stamps


def judge(
    store: RecordStore,
    kept: State,
    aspect: Aspect,
    on: Subject,
    verdict: Verdict,
    depth: Depth,
    evidence: str,
    author: Author = Author(),  # noqa: B008 - frozen
    reaffirms: str | None = None,
    pages: Sequence[tuple[str, int]] = (),
    sources: Sources | None = None,
    ran: Ran | None = None,
    values: Sequence[ValuePair] = (),
    code: Sequence[str] = (),
    propose: bool = False,
) -> Filed:
    """A judgment filed on a kept state, its basis the subject's one hop
    there, each element at its text's hash; `reaffirms` the lapsed judgment
    a re-ask found standing (`reaffirm`). A person's filed as a proposal
    (`propose`) never speaks: the next review of its kind asks the subject
    again with it in view (`ProposedReading`). A judgment's `pages` (key and
    index; `cited`, `valid` and `true`) are read through
    `sources` and their stamps recorded. A
    `computed` judgment's run (`ran`, code's, `computations.run`) is
    recorded with the `values` it matched and the `code` lines it read
    them computed by, and as a run seen. Filed with no review's lay-in, it
    is taken as given the files the store last saw of the works it records
    (`reading_works`, `given_files`), each file it read a page of stamped
    now: one that differs from the store's latest is given as it reads now and filed
    with it as a file seen."""
    at = seen(store, kept)
    if propose and not author.person:
        raise JudgmentError(
            "a proposed reading is a person's, for a review to settle: an "
            "agent's reading by hand speaks as filed"
        )
    if propose and reaffirms is not None:
        raise JudgmentError("a reaffirmation is a reading, never a proposal")
    if propose:
        decided = [
            r
            for s in readings(store, kept).statuses
            if s.aspect is aspect and s.subject == on
            for r in s.reads
            if r.author.person
            and r.live
            and r.judgment.verdict is not Verdict.INCONCLUSIVE
        ]
        if decided:
            raise JudgmentError(
                f"a person's reading decides it ({decided[-1].judgment.id}), and no "
                "review asks it again: judge it yourself"
            )
    if pages and aspect not in PAGED_ASPECTS:
        raise JudgmentError(
            "only a cited, valid or true judgment reads a cited work's pages"
        )
    stamps = (
        read_pages(on, verdict, pages, sources, aspect, readable_works(at, sources))
        if aspect is Aspect.CITED or pages
        else ()
    )
    record = judgment_record(
        at,
        aspect,
        on,
        verdict,
        depth,
        evidence,
        stamps,
        run=None if ran is None else ran.stamp,
        values=values,
        output=None if ran is None else ran.stdout,
        code=code,
        script=kept_text(store, kept, on.file),
    )
    given, changed = given_files(
        store, reading_works(at, aspect, on, stamps), stamps, sources
    )
    record = replace(record, reaffirms=reaffirms, given=given, proposed=propose)
    return file_records(store, JUDGMENT, judged(record, files=changed), author)


def given_files(
    store: RecordStore,
    works: Collection[str],
    stamps: Sequence[PageStamp],
    sources: Sources | None,
) -> tuple[tuple[FileStamp, ...], list[FileStamp]]:
    """What a record filed with no review's lay-in was given: the files the
    store last saw of `works`, each file it read a page of (`stamps`)
    stamped now; and those of them that differ from the store's latest, to
    file with it as files seen (`_changed_files`)."""
    held = store.judgment_records()
    known = files_seen(held.files)
    changed = [] if sources is None else _changed_files(sources, stamps, known, held)
    given = tuple(
        f
        for k, f in sorted({**known, **{f.key: f for f in changed}}.items())
        if work_of(k) in works
    )
    return given, changed


def _changed_files(
    sources: Sources,
    stamps: Sequence[PageStamp],
    known: Mapping[str, FileStamp],
    held: JudgmentRecords,
) -> list[FileStamp]:
    """Each file a judgment read a page of, stamped now, that differs from
    the store's latest (or the store never saw): what it rests on, so its
    change lapses it, and never a stale or an absent stamp. An outage
    stamps nothing."""
    found = []
    contents = file_contents(held.files)
    for key in dict.fromkeys(s.key for s in stamps if s.read):
        stamp, outage = sources.file_stamp(key, contents)
        if not outage and stamp != known.get(key):
            found.append(stamp)
    return found


def judgment_record(
    at: Seen,
    aspect: Aspect,
    on: Subject,
    verdict: Verdict,
    depth: Depth,
    evidence: str,
    stamps: Sequence[PageStamp] = (),
    run: RunStamp | None = None,
    values: Sequence[ValuePair] = (),
    output: str | None = None,
    code: Sequence[str] = (),
    script: str | None = None,
    shown: Seen | None = None,
) -> Judgment:
    """A judgment's record at the state `at` reads, unnumbered: its basis
    the subject's one hop there, each element at its text's hash; where
    `shown` is the state as its review's own edge lines correct it, the
    hop there, with what those lines took away (`retracted`). A
    `computed` one's run and value pairs are checked (`_ran`), each
    value's output side against the run's `output` where it is given (an
    adopted judgment's was checked when it was filed)."""
    if not evidence.strip():
        raise JudgmentError("a judgment needs its evidence: what the reading found")
    values = tuple(ValuePair(normalize(v.text), normalize(v.output)) for v in values)
    code = tuple(norm(c) for c in code)
    if aspect is Aspect.COMPUTED:
        _ran(at, on, verdict, run, values, output)
        _read(on, verdict, code, script)
    elif run is not None or values or code:
        raise JudgmentError(
            "only a computed judgment names a run, its values and its code"
        )
    nodes = [on.node] + ([on.target] if on.target else [])
    basis = one_hop(shown or at, aspect, nodes, on.file)
    return Judgment(
        "",
        None,
        at.state.id,
        aspect,
        on,
        verdict,
        depth,
        evidence,
        basis,
        pages=tuple(stamps),
        run=run,
        values=values,
        code=code,
        retracted=()
        if shown is None
        else taken_away(one_hop(at, aspect, nodes, on.file), basis),
    )


def taken_away(
    hop: Sequence[BasisElement], basis: Sequence[BasisElement]
) -> tuple[BasisElement, ...]:
    """The elements of a state's one hop that a reading's basis, as its own
    lines correct the state, no longer holds."""
    held = {_key(e) for e in basis}
    return tuple(e for e in hop if _key(e) not in held)


def kept_text(store: RecordStore, kept: State, key: str | None) -> str | None:
    """A code file's text as the kept state holds it; None for no file."""
    if key is None or key not in kept.files:
        return None
    return versions.blob(store, kept.files[key]).decode("utf-8", "replace")


def _read(
    on: Subject, verdict: Verdict, code: Sequence[str], script: str | None
) -> None:
    """A `computed` judgment's quoted code: a
    `verified` one quotes the script's line that computes its values, so its
    reading of the code is the code's and not its docstring's paraphrase;
    each quote found in the kept file, whitespace aside, as a finding's
    words on a file are. Checked where the script is given (a filing, a
    session's check), never on a record read back."""
    if verdict is Verdict.VERIFIED and not code:
        raise JudgmentError(
            "a verified computed quotes the script's line that computes the "
            f"values it matched, copied from file:{on.file}: --code LINE"
        )
    if script is None:
        return
    text = norm(script)
    for line in code:
        if not line or line not in text:
            raise JudgmentError(f"--code {line!r}: not in file:{on.file}")


def _ran(
    at: Seen,
    on: Subject,
    verdict: Verdict,
    run: RunStamp | None,
    values: Sequence[ValuePair],
    output: str | None,
) -> None:
    r"""A `computed` judgment's run: of the subject's code file,
    as the kept state holds it; a run that did not run is never evidence,
    so it bears only `inconclusive`; a `verified` one names a run that ran
    and each value it matched as a pair, the block's words found in its
    text and the output's in the run's stdout, each where it lives, as a
    quote is checked on its page. That the two are one value is the
    reading's: the text writes `\{0,1,4\}` or `fifteen` where a script
    prints `[0, 1, 4]` or `15`, so no string rule decides it; the pair
    makes it visible. Refused, with why, otherwise."""
    if run is not None:
        if run.key != on.file:
            raise JudgmentError(
                f"the run is of file:{run.key}, not the subject's file:{on.file}"
            )
        if run.blob != at.state.files.get(run.key):
            raise JudgmentError(
                f"file:{run.key} changed since state {at.state.id} was kept: keep a "
                "new state first (`axiomation build` again), then judge on it"
            )
        if not run.ran and verdict is not Verdict.INCONCLUSIVE:
            raise JudgmentError(
                f"the run {run.status.value}, and a run that did not run is never "
                "evidence: file inconclusive, and a finding that the script does "
                "not run"
            )
    if values and run is None:
        raise JudgmentError("a value is matched to a run's output: name the run")
    if verdict is Verdict.VERIFIED:
        if run is None:
            raise JudgmentError(
                "a verified computed judgment names the run it read: --run KEY -- ARGS"
            )
        if not values:
            raise JudgmentError(
                "a verified computed judgment names each value it matched, the "
                "text's words and the output's: --value TEXT OUTPUT"
            )
    text = normalize(at.words_of.get(on.node, ""))
    for value in values:
        if not value.text or value.text not in text:
            raise JudgmentError(
                f"--value {value.text!r} …: not in {at.handle(on.node)}'s text"
            )
    if output is None:
        return
    printed = normalize(output)
    for value in values:
        if not value.output or value.output not in printed:
            raise JudgmentError(f"--value … {value.output!r}: not in the run's output")


def judged(*records: Judgment, files: Sequence[FileStamp] = ()) -> JudgmentRecords:
    """Judgments as filed: each, then each run it read as a run seen, and
    `files`, those of its works' files it read that the store had not seen,
    each as a file seen."""
    runs = dict.fromkeys(r.run for r in records if r.run is not None)
    return JudgmentRecords(
        judgments=records,
        runs=tuple(RunSeen("", None, r) for r in runs),
        files=tuple(FileSeen("", None, f) for f in files),
    )


def adopt(
    store: RecordStore,
    kept: State,
    ref: str,
    author: Author = Author(),  # noqa: B008 - frozen
) -> Filed:
    """A person's judgment taking an agent's as their own: its
    aspect, subject as walked to `kept`, verdict, depth, evidence and pages,
    its basis the one hop at `kept`, naming the judgment it adopts. Refused
    where the judgment is a person's, where it lapsed at `kept` (a lapsed
    reading is re-asked or reaffirmed, never adopted), or where `kept` no
    longer offers its subject."""
    if not author.person:
        raise JudgmentError("a person adopts a judgment, not an agent")
    wanted = judgment_named(store, ref)
    refuse_proposed(wanted)
    read = next(
        (r for r in readings(store, kept).reads if r.judgment.id == wanted.id), None
    )
    if read is None:
        raise JudgmentError(
            f"judgment {wanted.id} was made on state {wanted.state}, which is not "
            f"along state {kept.id}'s parents"
        )
    if read.author.person:
        raise JudgmentError(
            f"judgment {wanted.id} is a person's ({read.author.name}): a person's "
            "judgment needs no adopting"
        )
    if not read.live:
        cause = first(read.walked.causes)
        raise JudgmentError(
            f"judgment {wanted.id} lapsed at state {kept.id}"
            + (f" ({cause.why})" if cause else "")
            + ": re-ask it (`axiomation reask`) or reaffirm it, then adopt"
        )
    if None in read.walked.nodes:
        raise JudgmentError(f"judgment {wanted.id}'s subject was deleted")
    at = seen(store, kept)
    record = replace(
        judgment_record(
            at,
            wanted.aspect,
            read.subject,
            wanted.verdict,
            wanted.depth,
            wanted.evidence,
            wanted.pages,
            run=wanted.run,
            values=wanted.values,
            code=wanted.code,
        ),
        adopts=wanted.id,
        given=wanted.given,
        unread=wanted.unread,
    )
    # the pages were seen when the agent read them; adopting reads none
    return file_records(store, JUDGMENT, JudgmentRecords(judgments=(record,)), author)


def see_files(store: RecordStore, stamps: Sequence[FileStamp], author: Author) -> Filed:
    """Cited files as their sources hold them now, each filed as a file
    seen: what a person or a test files by hand; a review's and a
    recheck's are `corpus.observe`'s. A file whose content differs from the
    one a judgment read a page of, or that is absent or held back, lapses
    that judgment."""
    if not stamps:
        raise JudgmentError("no file to file")
    records = JudgmentRecords(files=tuple(FileSeen("", None, s) for s in stamps))
    return file_records(store, SEEN, records, author)


def see_runs(store: RecordStore, stamps: Sequence[RunStamp], author: Author) -> Filed:
    """Runs of a paper's scripts as code ran them, each filed as a run seen: what a
    person or a gate ran. A run whose output differs from the one a `computed` judgment
    read, of the same call, lapses it."""
    if not stamps:
        raise JudgmentError("no run to file")
    records = JudgmentRecords(runs=tuple(RunSeen("", None, s) for s in stamps))
    return file_records(store, SEEN, records, author)


def see_work(
    store: RecordStore,
    key: str,
    doi: str | None,
    publication: Publication,
    author: Author,
    source: str = "",
    versions_of_record: Sequence[VersionOfRecord] = (),
) -> Filed:
    """A cited work as resolved now, or as a person sets it: its DOI, its
    publication, where that came from, and a preprint's versions of
    record. `established` reads a retraction, `check` the rest; nothing
    lapses by it."""
    seen = WorkSeen("", None, key, doi, publication, source, tuple(versions_of_record))
    return file_records(store, SEEN, JudgmentRecords(works=(seen,)), author)


def speaking_works(
    works: Iterable[WorkSeen], authors: Mapping[str, Any]
) -> dict[str, WorkSeen]:
    """Each cited work's record that speaks, by key: a person's latest,
    before or after any resolver's, else the latest; save a retraction a
    resolver files after the person's latest, which speaks until a person
    reads it and sets the work again (docs/model.md § 8)."""
    latest: dict[str, WorkSeen] = {}
    by_person: dict[str, WorkSeen] = {}
    for w in works:
        latest[w.key] = w
        if author_of(authors, w.answer).person:
            by_person[w.key] = w
    return latest | {
        key: w
        for key, w in by_person.items()
        if not (latest[key] is not w and latest[key].retracted)
    }


#: Depth from weakest to strongest: a reaffirmation takes the weaker of two.
DEPTHS = (Depth.INFORMAL, Depth.COMPUTATIONAL, Depth.FORMAL)


def weaker(a: Depth, b: Depth) -> Depth:
    return min(a, b, key=DEPTHS.index)


def reaffirm(
    store: RecordStore,
    kept: State,
    ref: str,
    depth: Depth,
    evidence: str,
    author: Author = Author(),  # noqa: B008 - frozen
    text_of: Callable[[str], str | None] | None = None,
    pages: Sequence[tuple[str, int]] = (),
    sources: Sources | None = None,
) -> Filed:
    """A re-ask that found a lapsed judgment's conclusion standing: a new judgment on
    its aspect and subject as walked to `kept`, its verdict the lapsed one's, its basis
    the one hop now, its depth the weaker of `depth` (the re-reading's method) and the
    lapsed one's. Refused where the judgment holds at `kept`, where its subject or claim
    ended, or where `kept` no longer offers the subject; a cited span is found once in
    its block's text as `text_of` gives it by block id, as `judge` was given it
    (default: its words), and its `pages` read again through `sources`."""
    wanted = judgment_named(store, ref)
    refuse_proposed(wanted)
    if wanted.run is not None:
        # its evidence is a run of the script as it was: a new run is a new
        # reading
        raise JudgmentError(
            f"judgment {wanted.id} read a run of file:{wanted.run.key}: run it "
            "again and file a judgment (`axiomation judge --aspect computed --run`)"
        )
    j = wanted.subject
    walked = walk(
        Reader(store),
        wanted.state,
        judged_hop(wanted.aspect, j.file),
        [j.node] + ([j.target] if j.target else []),
        wanted.basis,
        kept,
        None if wanted.aspect is Aspect.CITED else j.words,
        wanted.retracted,
    )
    if walked is None:
        raise JudgmentError(
            f"judgment {wanted.id} was made on state {wanted.state}, which is not "
            f"along state {kept.id}'s parents"
        )
    if wanted.pages or wanted.run is not None:
        walked = replace(
            walked,
            causes=walked.causes + tuple(_evidence_causes(store, wanted, kept.id)),
        )
    if not walked.lapsed:
        raise JudgmentError(
            f"judgment {wanted.id} holds at state {kept.id}: nothing to reaffirm (a "
            "new reading is a judgment)"
        )
    changed = [c.data["file"] for c in walked.causes if c.kind is CauseKind.CITED_FILE]
    named = {work_of(key) for key, _ in pages}
    unread = [key for key in changed if work_of(key) not in named]
    if unread:
        # a reading resting on a file that changed stands only on its work's
        # pages read again
        raise JudgmentError(
            f"judgment {wanted.id} lapsed by its cited files {', '.join(unread)}: "
            "name a page of each work with --page, read again"
        )
    if None in walked.nodes:
        raise JudgmentError(
            f"judgment {wanted.id}'s subject was deleted: nothing to reaffirm"
        )
    words = j.words if wanted.aspect is Aspect.CITED else walked.words
    if j.words is not None and words is None:
        raise JudgmentError(
            f'judgment {wanted.id}\'s claim "{j.words}" ended: nothing to reaffirm'
        )
    at = seen(store, kept)
    on, *target = (at.ident_of[n] for n in walked.nodes if n is not None)
    text = at.words_of.get(walked.nodes[0] or "") if text_of is None else text_of(on)
    try:
        now = subject(
            at,
            wanted.aspect,
            on,
            target[0] if target else None,
            None if wanted.aspect is Aspect.CITED else words,
            words if wanted.aspect is Aspect.CITED else None,
            text,
            j.work,
            j.file,
        )
    except JudgmentError as error:
        raise JudgmentError(
            f"state {kept.id} no longer offers judgment {wanted.id}'s subject: {error}"
        ) from None
    return judge(
        store,
        kept,
        wanted.aspect,
        now,
        wanted.verdict,
        weaker(wanted.depth, depth),
        evidence,
        author,
        reaffirms=wanted.id,
        pages=pages,
        sources=sources,
    )


def anchored(
    at: Seen,
    kind: AnchorKind,
    ident: str | None = None,
    words: str | None = None,
    work: str | None = None,
    text: str | None = None,
    file: str | None = None,
) -> Anchor:
    """A finding's anchor at a kept state, from a block or group id: a
    part's words found among its claims or in its text, a span's in its
    text once; a file's lines by their words, found once in `text`, the
    kept file's."""
    if kind is AnchorKind.PAPER:
        return Anchor(kind)
    if kind is AnchorKind.FILE:
        if not file or file not in at.state.files:
            raise JudgmentError(
                f"a file anchor names a file state {at.state.id} keeps, not {file!r}"
            )
        if not words:
            raise JudgmentError("a file anchor names the file's lines by their words")
        found = 0 if text is None else norm(text).count(norm(words))
        if found != 1:
            raise JudgmentError(
                f"{words!r} is found {found} times in file {file}, not once"
            )
        return Anchor(kind, words=norm(words), file=file)
    if kind is AnchorKind.WORK:
        if not work:
            raise JudgmentError("a work anchor names the cited work's key")
        return Anchor(kind, work=work)
    node = None if ident is None else at.node_of.get(ident)
    if node is None:
        raise JudgmentError(f"{ident!r} is no block or group of state {at.state.id}")
    if kind is AnchorKind.GROUP and node not in at.members:
        raise JudgmentError(f"{at.handle(node)} is a block, not a group")
    if kind is not AnchorKind.GROUP and node in at.members:
        raise JudgmentError(f"{at.handle(node)} is a group: anchor it as one")
    if kind in (AnchorKind.BLOCK, AnchorKind.GROUP):
        return Anchor(kind, node)
    if not words:
        raise JudgmentError(f"a {kind.value} anchor names its words")
    found = 0 if text is None else norm(text).count(norm(words))
    if kind is AnchorKind.PART and norm(words) in _claims(at, node):
        found = 1
    if found != 1:
        raise JudgmentError(
            f"{words!r} is found {found} times in {at.handle(node)}'s text, not once"
        )
    return Anchor(kind, node, norm(words))


def anchor_nodes(anchors: Sequence[Anchor]) -> list[str]:
    return [a.node for a in anchors if a.node is not None]


def anchors_basis(
    at: Seen, aspect: Aspect, anchors: Sequence[Anchor]
) -> list[BasisElement]:
    """What a reading anchored on `anchors` rests on (a finding's, a
    suggestion's): each anchor's one hop under `aspect`, the paper for a
    `paper` anchor, a file's blob for a `file` one."""
    found: list[BasisElement] = []
    for node in anchor_nodes(anchors):
        found += one_hop(at, aspect, [node])
    if any(a.kind is AnchorKind.PAPER for a in anchors):
        found.append(_paper(at))
    # a finding on a file's lines lapses on its blob, not on a sentence
    found += [
        BasisElement(BasisKind.FILE, a.file, at.state.files.get(a.file))
        for a in anchors
        if a.kind is AnchorKind.FILE and a.file is not None
    ]
    return list(merged(found))


def find(
    store: RecordStore,
    kept: State,
    aspect: Aspect,
    anchors: Sequence[Anchor],
    severity: int,
    problem: str,
    fix: str | None = None,
    author: Author = Author(),  # noqa: B008 - frozen
) -> Filed:
    """A finding filed on a kept state: its basis its anchors' one hop under
    its aspect, and the paper where it is anchored on the paper."""
    record = finding_record(seen(store, kept), aspect, anchors, severity, problem, fix)
    return file_records(store, FINDING, JudgmentRecords(findings=(record,)), author)


def finding_record(
    at: Seen,
    aspect: Aspect,
    anchors: Sequence[Anchor],
    severity: int,
    problem: str,
    fix: str | None = None,
    shown: Seen | None = None,
) -> Finding:
    """A finding's record at the state `at` reads, unnumbered; `shown` as
    a judgment's (`judgment_record`)."""
    if severity not in (1, 2, 3):
        raise JudgmentError(f"severity is 1, 2 or 3, not {severity}")
    if not anchors:
        raise JudgmentError("a finding needs one anchor at least")
    if not problem.strip():
        raise JudgmentError("a finding names its problem")
    basis = tuple(anchors_basis(shown or at, aspect, anchors))
    return Finding(
        "",
        None,
        at.state.id,
        aspect,
        severity,
        problem,
        fix,
        tuple(anchors),
        basis,
        retracted=()
        if shown is None
        else taken_away(anchors_basis(at, aspect, anchors), basis),
    )


def _one(found: Sequence[Any], what: str, ref: str) -> Any:
    if len(found) != 1:
        raise JudgmentError(
            f"{ref!r} names {len(found)} {what}s"
            + (": " + ", ".join(r.id for r in found[:10]) if found else "")
        )
    return found[0]


def finding_named(store: RecordStore, ref: str) -> Finding:
    """The finding `ref` names: its id or a unique prefix of it."""
    held = store.judgment_records().findings
    exact = [f for f in held if f.id == ref]
    return _one(exact or [f for f in held if f.id.startswith(ref)], "finding", ref)


def refuse_proposed(j: Judgment) -> None:
    """A proposed reading is no reading to re-ask, reaffirm or adopt: it
    never speaks."""
    if j.proposed:
        raise JudgmentError(
            f"judgment {j.id} is a person's proposed reading, which never speaks: "
            "propose again (`judge --propose`), or judge it"
        )


def latest_filing(store: RecordStore, finding: Finding) -> Finding:
    """The latest filing of a finding: each refiling (`reaffirms`) followed
    from it, so the id the author knew names the finding it became."""
    by = {f.reaffirms: f for f in store.judgment_records().findings if f.reaffirms}
    seen = {finding.id}
    while finding.id in by and by[finding.id].id not in seen:
        finding = by[finding.id]
        seen.add(finding.id)
    return finding


def first_filing(findings: Sequence[Finding], finding: Finding) -> str:
    """The id a finding was first filed under, along what it reaffirms."""
    by = {f.id: f for f in findings}
    seen = {finding.id}
    while finding.reaffirms in by and finding.reaffirms not in seen:
        finding = by[finding.reaffirms]
        seen.add(finding.id)
    return finding.id


def judgment_named(store: RecordStore, ref: str) -> Judgment:
    """The judgment `ref` names: its id or a unique prefix of it."""
    held = store.judgment_records().judgments
    exact = [j for j in held if j.id == ref]
    return _one(exact or [j for j in held if j.id.startswith(ref)], "judgment", ref)


def answer_finding(
    store: RecordStore,
    ref: str,
    status: FindingStatus,
    note: str | None = None,
    author: Author = Author(),  # noqa: B008 - frozen
    of: str | None = None,
    propose: bool = False,
) -> Filed:
    """A later record answering a finding (`FindingRead.answer` says which
    speaks); an agent's, or a person's filed as one (`propose`), is a
    proposal (`Proposal`) and says why (`note`). A person proposes only
    what the review of its kind settles: a finding a review filed that no
    answer speaks for. A `duplicate` names the finding it duplicates (`of`,
    its id or a unique prefix), never itself nor one that is, along its own
    `duplicate` answers, a duplicate of it. A finding refiled since is
    answered at its latest filing (`latest_filing`)."""
    finding = latest_filing(store, finding_named(store, ref))
    if (status is FindingStatus.DUPLICATE) != (of is not None):
        raise JudgmentError(
            "a duplicate names the finding it duplicates with --of"
            if of is None
            else f"--of names what a duplicate duplicates, not a {status.value} one"
        )
    proposed = propose and author.person
    if author.proposes or proposed:
        if not (note or "").strip():
            raise JudgmentError(
                "a proposal is checked by a review or a person: say why with --note"
            )
        speaks = speaking_answer(store, finding.id)
        refiled = [
            f.id for f in store.judgment_records().findings if f.reaffirms == finding.id
        ]
        if speaks is not None and speaks.status is not FindingStatus.POSTPONED:
            raise JudgmentError(
                f"finding {finding.id} is answered {speaks.status.value}: nothing "
                "to propose"
            )
        if proposed and (
            speaks is not None
            or not author_of(store.authoritative(), finding.answer).by_review
        ):
            raise JudgmentError(
                f"finding {finding.id} is a person's to settle, never a review's: "
                "answer it without --propose"
            )
        if refiled:
            raise JudgmentError(
                f"finding {finding.id} was refiled as {refiled[0]}: propose on that one"
            )
        if finding.reading:
            raise JudgmentError(
                f"finding {finding.id} is a disagreement of reading a review handed "
                "back: no review asks it again, and a person's answer closes it, "
                "never a proposal"
            )
    original = None if of is None else latest_filing(store, finding_named(store, of)).id
    if original is not None and original in duplicated(store, finding.id):
        raise JudgmentError(
            f"{finding.id} cannot duplicate {original}: "
            + (
                "a finding is not its own duplicate"
                if original == finding.id
                else f"{original} is answered a duplicate of it"
            )
        )
    record = FindingAnswer("", None, finding.id, status, note, original, proposed)
    return file_records(
        store, FINDING_ANSWER, JudgmentRecords(answers=(record,)), author
    )


def withdraw_answer(
    store: RecordStore,
    ref: str,
    reason: str,
    author: Author = Author(),  # noqa: B008 - frozen
) -> Filed:
    """A person's withdrawal of an answer to a finding: it speaks no more,
    and the finding reads as its other answers leave it, open where none
    speaks, for a proposal or another answer to follow. `ref` names the
    answer, or a finding for the answer that speaks for its latest filing,
    else the proposal there: an exact id of either kind first, then a
    unique prefix (one review's filing numbers its findings and answers
    under one key, so `K-1-1` is a prefix of `K-1-10`). An agent never
    withdraws one: it proposes again."""
    if not author.person:
        raise JudgmentError("a withdrawal is a person's: an agent proposes again")
    if not reason.strip():
        raise JudgmentError("a withdrawal gives its reason")
    held = store.judgment_records()
    withdrawn = answer_withdrawals(store)
    named = [a for a in held.answers if a.id == ref]
    findings = [f for f in held.findings if f.id == ref]
    if not (named or findings):
        named = [a for a in held.answers if a.id.startswith(ref)]
        findings = [f for f in held.findings if f.id.startswith(ref)]
    if named and findings:
        raise JudgmentError(
            f"{ref!r} names an answer and a finding: give more of the id"
        )
    if findings:
        finding = latest_filing(store, _one(findings, "finding", ref))
        speaks, proposal = spoken(answers_of(store).get(finding.id, []))
        answer = speaks or (None if proposal is None else proposal.answer)
        if answer is None:
            raise JudgmentError(f"finding {finding.id} has no answer to withdraw")
    else:
        answer = _one(named, "answer", ref)
        if answer.id in withdrawn:
            raise JudgmentError(f"answer {answer.id} is withdrawn already")
    record = AnswerWithdrawal("", None, answer.id, reason)
    return file_records(
        store,
        FINDING_ANSWER_WITHDRAWAL,
        JudgmentRecords(answer_withdrawals=(record,)),
        author,
    )


def answer_withdrawals(store: RecordStore) -> dict[str, AnswerWithdrawal]:
    """Each withdrawn answer's withdrawal, by the answer's id."""
    return {w.finding_answer: w for w in store.judgment_records().answer_withdrawals}


def turned_down(store: RecordStore, finding: Finding) -> Proposal | None:
    """The proposal a refiling (`reaffirms`) turned down: the one still to
    settle on the filing it refiled, as `readings` reads it (`weighed`)."""
    if finding.reaffirms is None:
        return None
    return spoken(answers_of(store).get(finding.reaffirms, []))[1]


def reaffirm_refusal(at: Seen, f: FindingRead, turns_down: bool = False) -> str | None:
    """Why a lapsed finding cannot be refiled at the state `at` reads,
    whatever its words: it holds there (unless it holds only where it
    carries an agent's proposal and the review of its kind `turns_down`
    the proposal), it was reaffirmed or answered, or an anchor's node was
    deleted; None where it can be."""
    found = f.finding
    if f.live and not (turns_down and f.proposal is not None):
        return f"finding {found.id} holds at state {at.state.id}: nothing to reaffirm"
    if f.reaffirmed is not None:
        return f"finding {found.id} was reaffirmed already, as {f.reaffirmed}"
    if not f.open:
        assert f.answer is not None
        return (
            f"finding {found.id} is answered {f.answer.status.value}: nothing to "
            "reaffirm"
        )
    if None in f.walked.nodes:
        return (
            f"an anchor of finding {found.id} was deleted: answer it "
            "(`axiomation answer-finding`), or file the problem anew"
        )
    return None


def reaffirmed_finding(
    at: Seen,
    f: FindingRead,
    evidence: str,
    words: str | None = None,
    text_of: Callable[[str], str | None] | None = None,
    file_of: Callable[[str], str | None] | None = None,
    turns_down: bool = False,
    reading: bool = False,
) -> Finding:
    """A re-reading that found a lapsed finding's problem standing: the
    finding refiled at the state `at` reads, unnumbered, naming the one it
    reaffirms and why it stands; `reading`, a review that weighed the
    finding's proposal and found the disagreement one of reading. Its
    aspect, severity, problem, fix and pages carried; its anchors walked
    there (`reanchored`); `words` quotes the first worded anchor anew.
    Refused where `reaffirm_refusal` says why, or where an anchor's words
    do not stand once."""
    found = f.finding
    if not evidence.strip():
        raise JudgmentError("a reaffirmation says why the problem still stands")
    refused = reaffirm_refusal(at, f, turns_down)
    if refused is not None:
        raise JudgmentError(refused)
    anchors = reanchored(at, f, words, text_of, file_of)
    record = finding_record(
        at, found.aspect, anchors, found.severity, found.problem, found.fix
    )
    return replace(
        record,
        pages=found.pages,
        reaffirms=found.id,
        evidence=evidence.strip(),
        reading=reading,
    )


def reanchored(
    at: Seen,
    f: FindingRead,
    words: str | None = None,
    text_of: Callable[[str], str | None] | None = None,
    file_of: Callable[[str], str | None] | None = None,
    requoted: bool = True,
) -> list[Anchor]:
    """A lapsed finding's anchors walked to the state `at` reads, each
    one's words found again in its block's text as `text_of` gives it by
    block id (default: its words), a file's in `file_of`'s; `words` quotes
    the first worded anchor anew, a span on a group's member moved to the
    member that holds its words now. Without `requoted`, the first worded
    anchor is left out unchecked: what the other anchors refuse, whatever
    words quote it anew."""
    found = f.finding

    def text(ident: str) -> str | None:
        if text_of is not None:
            return text_of(ident)
        return at.words_of.get(at.node_of.get(ident, ""))

    nodes = iter(f.walked.nodes)
    anchors: list[Anchor] = []
    requote = words
    first = True
    group: str | None = None
    for a in found.anchors:
        node = next(nodes) if a.node is not None else None
        ident = None if node is None else at.ident_of[node]
        if a.kind is AnchorKind.GROUP:
            group = node
        quoted = a.words
        if a.words is not None and first:
            first = False
            if not requoted:
                continue
            if requote is not None:
                quoted, requote = requote, None
        if a.kind is AnchorKind.SPAN and group is not None and quoted is not None:
            # a span on a group's member moves to the member that holds its
            # words now, as a review places it
            holders = [
                m
                for m in at.members.get(group, ())
                if norm(quoted) in norm(text(at.ident_of[m]) or "")
            ]
            if len(holders) == 1:
                ident = at.ident_of[holders[0]]
        try:
            if a.kind is AnchorKind.FILE:
                assert a.file is not None
                anchors.append(
                    anchored(
                        at,
                        a.kind,
                        words=quoted,
                        text=None if file_of is None else file_of(a.file),
                        file=a.file,
                    )
                )
            elif a.kind in (AnchorKind.PART, AnchorKind.SPAN):
                assert ident is not None
                anchors.append(anchored(at, a.kind, ident, quoted, text=text(ident)))
            else:
                anchors.append(anchored(at, a.kind, ident, work=a.work))
        except JudgmentError as error:
            raise JudgmentError(requote_problem(at, f, a, error)) from None
    if requote is not None:
        raise JudgmentError(
            f"finding {found.id} quotes no words: there is nothing to quote anew"
        )
    return anchors


def requote_problem(at: Seen, f: FindingRead, a: Anchor, error: object) -> str:
    """Why an anchor of a lapsed finding does not stand at the state `at`
    reads: `error`, the anchor's own refusal, under the finding's name."""
    worded = a.kind in (AnchorKind.PART, AnchorKind.SPAN, AnchorKind.FILE)
    return f"finding {f.finding.id}'s anchor at state {at.state.id}: {error}" + (
        "; quote its words as they read now" if worded else ""
    )


def reaffirm_finding(
    store: RecordStore,
    kept: State,
    ref: str,
    evidence: str,
    author: Author = Author(),  # noqa: B008 - frozen
    words: str | None = None,
    text_of: Callable[[str], str | None] | None = None,
) -> Filed:
    """A lapsed finding found standing, refiled on `kept`
    (`reaffirmed_finding`): its re-reading's filing. A finding refiled
    since is taken at its latest filing (`latest_filing`)."""
    wanted = latest_filing(store, finding_named(store, ref))
    read = next(
        (f for f in readings(store, kept).findings if f.finding.id == wanted.id), None
    )
    if read is None:
        raise JudgmentError(
            f"finding {wanted.id} was made on state {wanted.state}, which is not "
            f"along state {kept.id}'s parents"
        )
    record = reaffirmed_finding(
        seen(store, kept),
        read,
        evidence,
        words,
        text_of,
        lambda key: kept_text(store, kept, key),
    )
    return file_records(store, FINDING, JudgmentRecords(findings=(record,)), author)


def duplicated(store: RecordStore, finding: str) -> list[str]:
    """`finding` and the findings answered, each by its latest answer, a
    duplicate of it or of one of them: those a duplicate of it would
    close a cycle through. The answer that speaks counts (`spoken`): an
    agent's proposed `duplicate` merges nothing."""
    latest = {f: spoken(a)[0] for f, a in answers_of(store).items()}
    found = [finding]
    for f in found:
        found += [
            g
            for g, a in latest.items()
            if a is not None
            and a.status is FindingStatus.DUPLICATE
            and a.of == f
            and g not in found
        ]
    return found


def original_of(finding: str, latest: Mapping[str, FindingAnswer | None]) -> str | None:
    """The finding a duplicate is one with, along `duplicate` answers to the
    first that is answered otherwise or not at all; None for a finding not
    answered a duplicate."""
    seen = [finding]
    while (
        (a := latest.get(seen[-1])) is not None
        and a.status is (FindingStatus.DUPLICATE)
        and a.of is not None
        and a.of not in seen
    ):
        seen.append(a.of)
    return seen[-1] if len(seen) > 1 else None


# --- reading: a subject's status per aspect ------------------------------------------


class StatusKind(StrEnum):
    """A subject's status per aspect."""

    UNJUDGED = "unjudged"
    HELD = "held"
    DISPUTED = "disputed"
    LAPSED = "lapsed"


@dataclass(frozen=True)
class Read:
    """A judgment read at a state: its author, its place in the store, and
    its walk there."""

    judgment: Judgment
    author: Author
    order: FiledAt
    walked: Walked
    #: the version of its aspect's question its review answered; None for
    #: a reading no review filed, a person's or one filed by hand
    question: int | None = None

    @property
    def outdated(self) -> bool:
        """Filed by a review under an older question of its aspect than its
        brief asks now: asked again (docs/architecture.md § 9)."""
        return self.question is not None and self.question < rf.QUESTION_VERSIONS.get(
            self.judgment.aspect.value, 1
        )

    @property
    def subject(self) -> Subject:
        """Its subject at the state read: its nodes (the judged ones where
        deleted) and its claim's words carried."""
        j = self.judgment.subject
        nodes = self.walked.nodes
        return Subject(
            nodes[0] or j.node,
            None if j.target is None else (nodes[1] or j.target),
            j.words if self.judgment.aspect is Aspect.CITED else self.walked.words,
            j.work,
            j.file,
        )

    @property
    def live(self) -> bool:
        return not self.walked.lapsed

    @property
    def provisional(self) -> bool:
        """Read off a copy that is not the version of record."""
        return provisional(self.judgment.pages)


@dataclass(frozen=True)
class Proposal:
    """An agent's answer to a finding filed by hand, or a person's filed as
    a proposal: shown beside the finding, never closing it, until the
    review of its kind or a person settles it (docs/model.md § 8)."""

    answer: FindingAnswer
    author: Author


@dataclass(frozen=True)
class FindingRead:
    """A finding read at a state: its author, place, walk (its anchors'
    nodes there), the answer that speaks (`spoken`) and an agent's
    proposal still to settle."""

    finding: Finding
    author: Author
    order: FiledAt
    walked: Walked
    answer: FindingAnswer | None
    #: the later finding that refiled it, standing (`reaffirm_finding`)
    reaffirmed: str | None = None
    #: an open finding's proposal, filed after the answer that speaks
    proposal: Proposal | None = None
    #: a refiled finding's proposal when it was refiled: the one its
    #: refiling turned down, the refiling's evidence its answer
    weighed: Proposal | None = None
    #: of a refiling, the proposal it turned down: its predecessor's
    #: `weighed`
    turned_down: Proposal | None = None
    #: an outside reviewer's finding whose answer does not address it
    #: (`reviewer_unaddressed`): open whatever that answer says
    unaddressed: bool = False
    #: the review session whose answer filed it; None: no review's
    filed_by: rf.Session | None = None

    @property
    def live(self) -> bool:
        return not self.walked.lapsed

    @property
    def open(self) -> bool:
        """No answer speaks, or `postponed` does, or a reviewer's finding's
        answer does not address it (`unaddressed`), and not refiled: a
        reaffirmation speaks for the finding it reaffirms."""
        return self.reaffirmed is None and (
            self.answer is None
            or self.answer.status is FindingStatus.POSTPONED
            or self.unaddressed
        )

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(n for n in self.walked.nodes if n is not None)

    @property
    def cause(self) -> Cause | None:
        """A lapsed finding's first cause, as a subject's is."""
        return first(self.walked.causes)

    @property
    def blocking(self) -> bool:
        """An open, live severity-3 finding: a negative judgment of its
        aspect; never one on how the paper reads or
        what it is worth (`NON_BLOCKING_ASPECTS`)."""
        return (
            self.open
            and self.live
            and self.finding.severity == 3
            and self.finding.aspect not in NON_BLOCKING_ASPECTS
        )


def review_settles(f: FindingRead) -> bool:
    """Whether the review of its kind settles an open finding: a review
    filed it and no answer speaks for it (an open finding's that does is a
    person's `postponed`, theirs to settle, never a review's); never one a
    review handed back as a disagreement of reading, the person's to
    answer."""
    return f.open and f.author.by_review and f.answer is None and not f.finding.reading


@dataclass(frozen=True)
class ProposedReading:
    """A person's reading filed as a proposal (`judge --propose`), and its
    re-ask: the first reading of its subject filed after it, whoever
    files it. Until one is, the next review of its kind asks the subject
    again with it in view (docs/model.md § 8)."""

    read: Read
    reask: Read | None = None

    @property
    def asks(self) -> bool:
        return self.reask is None and self.read.live


@dataclass(frozen=True)
class Dismissal:
    """A person's `dismissed` answer to a finding filed in the same answer
    as a negative reading of a subject it bears on, with the answer's order
    and author: the reading holds, and the next review of its kind asks the
    subject again with the answer's note, until a reading filed after the
    answer (docs/model.md § 8)."""

    finding: FindingRead
    author: Author
    order: FiledAt

    @property
    def answer(self) -> FindingAnswer:
        assert self.finding.answer is not None
        return self.finding.answer


@dataclass(frozen=True)
class SubjectStatus:
    """A subject's status per aspect at a state: `held` the judgment that
    holds it, `reads` each author's latest judgment (`_latest_status`),
    `findings` the open,
    live severity-3 ones that bear on it, `counted` those of them no
    person's later reading decided over, `dismissed` the person's
    dismissal that asks it again (`dismissal`)."""

    aspect: Aspect
    subject: Subject
    status: StatusKind
    held: Read | None = None
    reads: tuple[Read, ...] = ()
    findings: tuple[FindingRead, ...] = ()
    counted: tuple[FindingRead, ...] = ()
    #: each author's latest reading that concluded, live or lapsed: what a
    #: later `inconclusive` of theirs does not undo
    concluded: tuple[Read, ...] = ()
    dismissed: Dismissal | None = None
    #: whether a review's reading under an older question of its aspect
    #: speaks and none under the current one was filed: asked again
    #: (`asked_again`)
    outdated: bool = False
    #: why its held reading, which did not verify, is asked again: the
    #: evidence it was given grew (`grown`)
    grown: tuple[Growth, ...] = ()
    #: a person's latest proposed reading of it, and the reading that
    #: settled it, if one was filed since (`proposed_reading`)
    proposal: ProposedReading | None = None

    @property
    def cause(self) -> Cause | None:
        """A lapsed subject's cause: its latest reading's first cause, the
        reading a re-ask is offered for. An earlier author's reading may
        have lapsed by another change, before the latest was filed."""
        return first(self.reads[-1].walked.causes) if self.reads else None


def _status(
    aspect: Aspect,
    on: Subject,
    reads: Sequence[Read],
    findings: Sequence[FindingRead],
    gone: frozenset[str] = frozenset(),
) -> SubjectStatus:
    status = _latest_status(aspect, on, reads, findings, gone)
    concluded: dict[str, Read] = {}
    for r in sorted(reads, key=lambda r: r.order):
        if r.judgment.verdict is not Verdict.INCONCLUSIVE:
            concluded[r.author.identity] = r
    kept = (r for r in concluded.values() if r.judgment.id not in gone)
    return replace(status, concluded=tuple(sorted(kept, key=lambda r: r.order)))


def _latest_status(
    aspect: Aspect,
    on: Subject,
    reads: Sequence[Read],
    findings: Sequence[FindingRead],
    gone: frozenset[str] = frozenset(),
) -> SubjectStatus:
    """The status of `reads`, each author's latest speaking: an
    `inconclusive` takes away credit, never debit, so it never replaces its
    author's own live negative reading and stands beside it, differing from
    nothing; a negative reading a dismissal's re-ask superseded (`gone`,
    `superseded`) speaks for no author."""
    latest: dict[str, Read] = {}
    beside: dict[str, Read] = {}
    for r in sorted(reads, key=lambda r: r.order):
        key = r.author.identity
        prior = latest.get(key)
        if (
            r.judgment.verdict is Verdict.INCONCLUSIVE
            and prior is not None
            and prior.live
            and prior.judgment.verdict in NEGATIVE
        ):
            beside[key] = r
            continue
        latest[key] = r
        beside.pop(key, None)
    kept = [r for r in latest.values() if r.judgment.id not in gone]
    kept += beside.values()
    chosen = tuple(sorted(kept, key=lambda r: r.order))
    against = tuple(f for f in findings if f.blocking)
    if not chosen:
        return SubjectStatus(
            aspect, on, StatusKind.UNJUDGED, findings=against, counted=against
        )
    live = [r for r in chosen if r.live]
    if not live:
        return SubjectStatus(
            aspect, on, StatusKind.LAPSED, None, chosen, against, against
        )
    # the latest person who concluded decides over the agents' readings
    # made before theirs (a reading that could not tell decides nothing);
    # persons who differ stay disputed
    person = next(
        (
            r
            for r in reversed(live)
            if r.author.person and r.judgment.verdict is not Verdict.INCONCLUSIVE
        ),
        None,
    )
    counted = list(against)
    persons = _concluding_persons(live)
    live = [r for r in live if r.author.person or _unseen(r.order, persons)]
    counted = [f for f in counted if f.author.person or _unseen(f.order, persons)]
    verdicts = {r.judgment.verdict for r in live} - {Verdict.INCONCLUSIVE}
    if len(verdicts) > 1 or (Verdict.VERIFIED in verdicts and counted):
        return SubjectStatus(
            aspect, on, StatusKind.DISPUTED, None, chosen, against, tuple(counted)
        )
    holding = [r for r in live if not verdicts or r.judgment.verdict in verdicts]
    held = person if person in holding else holding[-1]
    return SubjectStatus(
        aspect, on, StatusKind.HELD, held, chosen, against, tuple(counted)
    )


#: What clears a live negative reading: the text corrected and read
#: again, or a person's judgment, which decides over the agents' made
#: before it (`docs/model.md` § 8).
NEGATIVE_CLEARS = (
    "the text corrected and read again, or your judgment over it (`axiomation judge`)"
)


def negative(s: SubjectStatus) -> Read | None:
    """The live negative reading a subject stands on: held (a person's, or
    agents agreeing), or a person's a later agent disputes."""
    if s.status is StatusKind.HELD:
        assert s.held is not None
        return s.held if s.held.judgment.verdict in NEGATIVE else None
    if s.status is StatusKind.DISPUTED:
        person = next(
            (
                r
                for r in reversed(s.reads)
                if r.live
                and r.author.person
                and r.judgment.verdict is not Verdict.INCONCLUSIVE
            ),
            None,
        )
        if person is not None and person.judgment.verdict in NEGATIVE:
            return person
    return None


def _concluding_persons(reads: Sequence[Read]) -> list[Read]:
    """The persons' live readings that concluded: each decides over the
    agents' readings it saw (`_status`)."""
    return [
        r
        for r in reads
        if r.live and r.author.person and r.judgment.verdict is not Verdict.INCONCLUSIVE
    ]


def _unseen(order: FiledAt, persons: Sequence[Read]) -> bool:
    """Whether no one of `persons`' readings saw the filing at `order`:
    persons who read apart each set aside what they saw."""
    return all(unseen_by(order, p.order) for p in persons)


def to_judge(s: SubjectStatus) -> bool:
    """Whether a disputed subject is a person's to judge: no negative
    reading stands on it (that is a defect), and its own readings differ,
    those a person's later reading decided over left out; one only a
    finding disputes is the finding's to answer."""
    if s.status is not StatusKind.DISPUTED or negative(s) is not None:
        return False
    persons = _concluding_persons(s.reads)
    live = [
        r for r in s.reads if r.live and (r.author.person or _unseen(r.order, persons))
    ]
    verdicts = {r.judgment.verdict for r in live} - {Verdict.INCONCLUSIVE}
    return len(verdicts) > 1


def lapsed_negative(s: SubjectStatus) -> Read | None:
    """The lapsed negative a subject stands on, unresolved: each author's
    latest reading that concluded, taken as live, stands on a negative one
    (`negative`) that lapsed, and no live reading concluded after it. An
    `inconclusive`, by anyone, settles nothing."""
    if s.status is not StatusKind.UNJUDGED and negative(s) is not None:
        return None
    revived = {
        r.judgment.id: replace(r, walked=replace(r.walked, causes=()))
        for r in s.concluded
    }
    if len(revived) == sum(r.live for r in s.concluded):
        return None
    before = _latest_status(s.aspect, s.subject, list(revived.values()), ())
    read = negative(before)
    if read is None:
        return None
    lapsed = next(r for r in s.concluded if r.judgment.id == read.judgment.id)
    if lapsed.live or any(
        r.live and filed_after(r.order, lapsed.order) for r in s.concluded
    ):
        return None
    return lapsed


def dismissal(
    s: SubjectStatus,
    reads: Sequence[Read],
    findings: Sequence[FindingRead],
    answered: Mapping[str, Sequence[tuple[FiledAt, Author, FindingAnswer]]],
) -> Dismissal | None:
    """The person's dismissal that asks a subject again: the subject stands
    on an agent's negative reading (`negative`, `lapsed_negative`), a
    finding of its aspect that a review filed and that bears on it
    (`findings`) is answered `dismissed` by a person, a negative reading of
    it (`reads`, all its readings) was filed in the finding's answer, and
    no reading of it was filed after the dismissal. The latest such; None
    where there is none. A person's dismissal says the problem is none, not
    that the reading filed with it is wrong: a reader asks the reading
    again with the note, once per dismissal. A person's own negative
    reading is their decision, which no review settles."""
    stands = negative(s) or lapsed_negative(s)
    if stands is None or stands.author.person:
        return None
    found = [
        d
        for d in _dismissals(reads, findings, answered)
        if not any(filed_after(r.order, d.order) for r in reads)
    ]
    return max(found, key=lambda d: d.order, default=None)


def superseded(
    reads: Sequence[Read],
    findings: Sequence[FindingRead],
    answered: Mapping[str, Sequence[tuple[FiledAt, Author, FindingAnswer]]],
    proposals: Sequence[Read] = (),
) -> frozenset[str]:
    """The agents' negative readings of a subject a person's dismissal's
    re-ask superseded: the first reading of it filed after the dismissal is
    the re-ask, whoever files it, and one that concludes supersedes the
    agents' negative readings filed with the finding dismissed, while the
    dismissal speaks; an `inconclusive` one leaves them standing
    (docs/model.md § 8). A person's proposed reading (`proposals`) is asked
    again the same way: its re-ask, concluding, supersedes the agents'
    negative readings filed before it. They stay in the store, as
    history."""
    gone: set[str] = set()
    for p in proposals:
        if not p.live:
            # a lapsed proposal asks nothing: a later reading never saw it
            continue
        reask = proposed_reading(p, reads).reask
        if reask is None or reask.judgment.verdict is Verdict.INCONCLUSIVE:
            continue
        gone |= {
            r.judgment.id
            for r in reads
            if filed_after(p.order, r.order)
            and r.judgment.verdict in NEGATIVE
            and not r.author.person
        }
    for d in _dismissals(reads, findings, answered):
        later = [r for r in reads if filed_after(r.order, d.order)]
        reask = min(later, key=lambda r: r.order, default=None)
        if reask is None or reask.judgment.verdict is Verdict.INCONCLUSIVE:
            continue
        gone |= {
            r.judgment.id
            for r in reads
            if r.judgment.answer == d.finding.finding.answer
            and r.judgment.verdict in NEGATIVE
            and not r.author.person
        }
    return frozenset(gone)


def proposed_reading(proposal: Read, reads: Sequence[Read]) -> ProposedReading:
    """A proposed reading and its re-ask among the subject's readings
    (`reads`, all of them): the first filed after it."""
    later = [r for r in reads if filed_after(r.order, proposal.order)]
    return ProposedReading(proposal, min(later, key=lambda r: r.order, default=None))


def requestioned(reads: Sequence[Read]) -> frozenset[str]:
    """The agents' readings of a subject under an older question of its
    aspect that a review's reading under the current one replaced: one that
    concludes, whoever files it; an `inconclusive` takes away credit, never
    debit, and leaves them standing. They stay in the store, as history."""
    if not any(
        r.question is not None
        and not r.outdated
        and r.judgment.verdict is not Verdict.INCONCLUSIVE
        for r in reads
    ):
        return frozenset()
    return frozenset(r.judgment.id for r in reads if r.outdated)


def asked_again(status: SubjectStatus, reads: Sequence[Read]) -> bool:
    """Whether a review's reading under an older question speaks for the
    subject and no review read it under the current one (`reads`, all its
    readings): what the next review asks again, once. A person's live
    reading that concludes decides over the agents' made before it, which
    no review settles: those are not asked again."""
    decided = [
        r.order
        for r in reads
        if r.author.person and r.live and r.judgment.verdict is not Verdict.INCONCLUSIVE
    ]
    return any(
        r.outdated and all(unseen_by(r.order, d) for d in decided) for r in status.reads
    ) and not any(r.question is not None and not r.outdated for r in reads)


def _dismissals(
    reads: Sequence[Read],
    findings: Sequence[FindingRead],
    answered: Mapping[str, Sequence[tuple[FiledAt, Author, FindingAnswer]]],
) -> list[Dismissal]:
    """Each person's dismissal of a finding a review filed in the same
    answer as a negative reading of the subject (`reads`, all its
    readings; `findings`, those of its aspect that bear on it)."""
    found: list[Dismissal] = []
    for f in findings:
        if f.answer is None or f.answer.status is not FindingStatus.DISMISSED:
            continue
        if not f.author.by_review:
            continue
        order, author, _ = next(
            a for a in answered.get(f.finding.id, ()) if a[2] is f.answer
        )
        if not author.person:
            continue
        if not any(
            r.judgment.answer == f.finding.answer and r.judgment.verdict in NEGATIVE
            for r in reads
        ):
            continue
        found.append(Dismissal(f, author, order))
    return found


@dataclass(frozen=True)
class Readings:
    """Every judgment and finding read at one state, and each subject's
    status per aspect there: the subjects the state offers (each standing's
    claim `true`, each justifies edge's `valid`, each block with an about
    edge `accurate`) and any a judgment is on."""

    at: Seen
    statuses: tuple[SubjectStatus, ...]
    findings: tuple[FindingRead, ...]
    reads: tuple[Read, ...]
    #: each cited work's record that speaks, by key (`speaking_works`)
    works: Mapping[str, WorkSeen] = field(default_factory=dict)
    #: the persons' proposed readings, which never speak
    proposals: tuple[Read, ...] = ()

    def block_of(self, node: str | None) -> str | None:
        """The block a node's readings show on: a group's, its first."""
        if node is None:
            return None
        if node in self.at.members:
            members = self.at.members[node]
            return self.at.ident_of.get(members[0]) if members else None
        return self.at.ident_of.get(node)

    def statuses_on(self, block: str) -> list[SubjectStatus]:
        return [s for s in self.statuses if self.block_of(s.subject.node) == block]

    def findings_on(self, block: str) -> list[FindingRead]:
        return [f for f in self.findings if block in map(self.block_of, f.nodes)]


def bears_on(f: FindingRead, on: Subject) -> bool:
    """Whether a finding is anchored on a subject: a block or group anchor on
    its node (J or T for `valid`), a part or span anchor on its claim's
    words where the subject has words; a file anchor on a `computed`
    subject's code file."""
    if on.file is not None and any(
        a.kind is AnchorKind.FILE and a.file == on.file for a in f.finding.anchors
    ):
        return True
    for anchor, node in zip(
        [a for a in f.finding.anchors if a.node is not None],
        f.walked.nodes,
        strict=False,
    ):
        if node is None or node not in (on.node, on.target):
            continue
        if anchor.kind in (AnchorKind.PART, AnchorKind.SPAN) and on.words:
            # a worded anchor bears on the claim its words meet, not on the
            # block's other claims (as `rulings.on_words` reads a ruling's)
            said, words = norm(anchor.words or ""), norm(on.words)
            if not (said and (said in words or words in said)):
                continue
        return True
    return False


def reviewer_first(f: FindingRead, by_id: Mapping[str, FindingRead]) -> FindingRead:
    """The finding `f` refiles, back to the first filing: whose finding it
    is, whoever refiled it."""
    while f.finding.reaffirms in by_id:
        f = by_id[f.finding.reaffirms]
    return f


def reviewer_unaddressed(found: Sequence[FindingRead]) -> list[FindingRead]:
    """`found`, each outside reviewer's finding marked `unaddressed` where
    its answer does not address it: a reviewer wants their finding fixed,
    or argued against (`dismissed` with a note), or shown a duplicate of
    one that is itself addressed (`docs/model.md` § 8). A duplicate of an
    open finding, or a dismissal without a note, leaves it open."""
    by_id = {f.finding.id: f for f in found}
    latest = {f.finding.id: f.reaffirmed for f in found if f.reaffirmed}

    def current(ident: str) -> FindingRead | None:
        seen: set[str] = set()
        while ident in latest and ident not in seen:
            seen.add(ident)
            ident = latest[ident]
        return by_id.get(ident)

    def addressed(f: FindingRead, walking: frozenset[str]) -> bool:
        a = f.answer
        if f.reaffirmed is not None or a is None:
            return f.reaffirmed is not None
        if a.status is FindingStatus.POSTPONED:
            return False
        if not reviewer_first(f, by_id).author.reviewer:
            return True
        if a.status is FindingStatus.DISMISSED:
            return bool(a.note)
        if a.status is FindingStatus.DUPLICATE:
            of = None if a.of is None else current(a.of)
            # a duplicate of a finding gone from the state, or of itself
            # round a circle, addresses nothing
            return (
                of is not None
                and of.finding.id not in walking
                and addressed(of, walking | {f.finding.id})
            )
        return True

    return [
        replace(f, unaddressed=True)
        if f.answer is not None
        and f.answer.status is not FindingStatus.POSTPONED
        and f.reaffirmed is None
        and not addressed(f, frozenset())
        else f
        for f in found
    ]


def spoken(
    answers: Sequence[tuple[FiledAt, Author, FindingAnswer]],
) -> tuple[FindingAnswer | None, Proposal | None]:
    """Of one finding's answers (each with its order and author), the one
    that speaks and the proposal after it: a person's latest answer speaks
    over every agent's, before or after; else a review's latest. An agent's
    answer filed by hand, or a person's filed as a proposal, never speaks:
    the latest one filed after the answer that speaks is the proposal."""
    ordered = sorted(answers, key=lambda a: a[0])
    persons = [a for a in ordered if a[1].person and not a[2].proposed]
    reviews = [a for a in ordered if a[1].by_review]
    speaks = (persons or reviews or [None])[-1]
    after = NOT_FILED if speaks is None else speaks[0]
    # a speaking answer sets aside only the proposals it saw
    proposed = [
        a
        for a in ordered
        if (a[1].proposes or a[2].proposed) and unseen_by(a[0], after)
    ]
    proposal = Proposal(proposed[-1][2], proposed[-1][1]) if proposed else None
    return (None if speaks is None else speaks[2]), proposal


def answers_of(
    store: RecordStore,
    filed: Mapping[str, FiledAt] | None = None,
    authors: Mapping[str, Any] | None = None,
    withdrawn: bool = False,
) -> dict[str, list[tuple[FiledAt, Author, FindingAnswer]]]:
    """Each finding's answers, each with its order in the store and its
    author, as `spoken` reads them: a withdrawn one never speaks, so it is
    left out, unless `withdrawn` asks for it too (to be shown so); `filed`
    and `authors` the store's answer order and authoritative answers, where
    the caller has them."""
    if filed is None:
        filed = store.filed_order()
    if authors is None:
        authors = store.authoritative()
    gone = set() if withdrawn else set(answer_withdrawals(store))
    found: dict[str, list[tuple[FiledAt, Author, FindingAnswer]]] = {}
    for a in store.judgment_records().answers:
        if a.id in gone:
            continue
        found.setdefault(a.finding, []).append(
            (filed.get(a.answer or "", NOT_FILED), author_of(authors, a.answer), a)
        )
    return found


def speaking_answer(store: RecordStore, finding: str) -> FindingAnswer | None:
    """The answer that speaks for a finding (`spoken`), else None."""
    return spoken(answers_of(store).get(finding, []))[0]


def author_of(store_answers: Mapping[str, Any], key: str | None) -> Author:
    answer = store_answers.get(key or "")
    if answer is None:
        return Author("?", "?")
    return Author(answer.host, answer.model, answer.session)


def offered(at: Seen) -> list[tuple[Aspect, Subject]]:
    """The subjects a state offers: each standing's claim `true`,
    each justifies edge's `valid` (per claim of T it covers; per
    claim of T where it covers none), each block with an about
    edge `accurate`, each uses edge from a block to a code file `computed`."""
    found: list[tuple[Aspect, Subject]] = []
    found += [(Aspect.TRUE, Subject(n, None, w)) for n, w in at.standings]
    for e in at.edges:
        if e["kind"] != "justifies" or _onto(e) not in at.node_of:
            continue
        j, t = at.node_of[_source(e)], at.node_of[_onto(e) or ""]
        claims = covered_claims(at, e) or _claims(at, t) or [None]
        found += [(Aspect.VALID, Subject(j, t, w)) for w in claims]
    about = dict.fromkeys(_source(e) for e in at.edges if e["kind"] == "about")
    found += [
        (Aspect.ACCURATE, Subject(at.node_of[b])) for b in about if b in at.node_of
    ]
    found += [
        (Aspect.COMPUTED, Subject(node, file=key))
        for b in dict.fromkeys(map(_source, at.edges))
        if (node := at.node_of.get(b)) is not None and node not in at.members
        for key in code_uses(at, node)
    ]
    return list(dict.fromkeys(found))


def readings(store: RecordStore, kept: State, reader: Reader | None = None) -> Readings:
    """The judgments and findings that bear on `kept` (made on it or on a
    state along its parents), each walked to it, and each subject's status
    per aspect there."""
    reader = reader or kept_reader(store)
    at = reader.seen(kept)
    held = store.judgment_records()
    answers = store.authoritative()
    # judgments and findings on one timeline: their answers' order in the
    # store, which is the order they were filed in
    filed = store.filed_order()
    seen_files = [(filed.get(f.answer or "", NOT_FILED), f) for f in held.files]
    seen_runs = [(filed.get(r.answer or "", NOT_FILED), r) for r in held.runs]
    contents = file_contents(held.files)
    reads: list[Read] = []
    proposals: list[Read] = []
    for j in held.judgments:
        order = filed.get(j.answer or "", NOT_FILED)
        walked = walk(
            reader,
            j.state,
            judged_hop(j.aspect, j.subject.file),
            [j.subject.node] + ([j.subject.target] if j.subject.target else []),
            j.basis,
            kept,
            None if j.aspect is Aspect.CITED else j.subject.words,
            j.retracted,
        )
        if walked is not None:
            if j.pages or j.run is not None:
                walked = replace(
                    walked,
                    causes=walked.causes
                    + tuple(sourced(j, order, seen_files, contents, kept.id))
                    + tuple(_rerun(j, order, seen_runs, kept.id)),
                )
            answer = answers.get(j.answer or "")
            question = (
                None if answer is None else rf.asked_version(answer, j.aspect.value)
            )
            read = Read(j, author_of(answers, j.answer), order, walked, question)
            # a person's proposed reading never speaks: kept apart
            (proposals if j.proposed else reads).append(read)
    answered = answers_of(store, filed, answers)
    found = []
    for f in held.findings:
        order = filed.get(f.answer or "", NOT_FILED)
        walked = walk(
            reader,
            f.state,
            anchored_hop(f.aspect),
            anchor_nodes(f.anchors),
            f.basis,
            kept,
            retracted=f.retracted,
        )
        if walked is not None:
            answer, proposal = spoken(answered.get(f.id, []))
            filing = answers.get(f.answer or "")
            found.append(
                FindingRead(
                    f,
                    author_of(answers, f.answer),
                    order,
                    walked,
                    answer,
                    proposal=proposal,
                    filed_by=rf.session_of(filing.kind) if filing else None,
                )
            )
    reader.keep()
    reaffirmed = {f.finding.reaffirms: f.finding.id for f in found}
    found = [
        replace(f, reaffirmed=reaffirmed.get(f.finding.id))
        if f.finding.id in reaffirmed
        else f
        for f in found
    ]
    found = reviewer_unaddressed(found)
    # a closed finding holds no proposal: nothing an agent files reopens it;
    # a refiled one's is the proposal its refiling weighed
    found = [
        f
        if f.open
        else replace(
            f,
            proposal=None,
            weighed=f.proposal if f.reaffirmed is not None else None,
        )
        for f in found
    ]
    weighed = {f.reaffirmed: f.weighed for f in found if f.weighed is not None}
    found = [
        replace(f, turned_down=weighed[f.finding.id]) if f.finding.id in weighed else f
        for f in found
    ]
    keys = (
        offered(at)
        + [(r.judgment.aspect, r.subject) for r in reads]
        + [(r.judgment.aspect, r.subject) for r in proposals if r.live]
    )
    files = files_seen(held.files)
    # each subject's readings and each aspect's findings, in filing order
    read_on: dict[tuple[Aspect, Subject], list[Read]] = {}
    for r in reads:
        read_on.setdefault((r.judgment.aspect, r.subject), []).append(r)
    proposed_on: dict[tuple[Aspect, Subject], list[Read]] = {}
    for r in proposals:
        proposed_on.setdefault((r.judgment.aspect, r.subject), []).append(r)
    found_on: dict[Aspect, list[FindingRead]] = {}
    for f in found:
        found_on.setdefault(f.finding.aspect, []).append(f)
    statuses = []
    for aspect, on in dict.fromkeys(keys):
        mine = read_on.get((aspect, on), [])
        proposed = proposed_on.get((aspect, on), [])
        against = [f for f in found_on.get(aspect, []) if bears_on(f, on)]
        gone = superseded(mine, against, answered, proposed) | requestioned(mine)
        status = _status(aspect, on, mine, against, gone)
        statuses.append(
            replace(
                status,
                dismissed=dismissal(status, mine, against, answered),
                outdated=asked_again(status, mine),
                grown=grown(status, at, files, contents),
                proposal=(
                    proposed_reading(max(proposed, key=lambda r: r.order), mine)
                    if proposed
                    else None
                ),
            )
        )
    order = {n.node: i for i, n in enumerate(kept.nodes)}

    def place(s: SubjectStatus) -> int:
        node = s.subject.node
        members = at.members.get(node)
        return order.get(members[0] if members else node, len(order))

    statuses.sort(key=place)
    return Readings(
        at,
        tuple(statuses),
        tuple(found),
        tuple(reads),
        speaking_works(held.works, answers),
        tuple(proposals),
    )
