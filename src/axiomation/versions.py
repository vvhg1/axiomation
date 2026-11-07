"""A paper's kept versions: every state a whole `build` or
`update` left, appended to its store and never overwritten.

A state names its version (the document hash), its parent, the command
that made it, the files the run read (each a blob named by its content's
sha256), the export it wrote (named so too, and kept as its lines,
`state_lines`, rendered back byte for byte), the identity answer an
update started from, the person answers it applied, and a node id per
block and per group, minted once and stored (`docs/model.md` § 7). Each
block's text, its owned runs as the kept files hold them, is stored once
per distinct text. A version's graph is its kept export; a block's history
is computed from the exports along the parents, with identity's classes and
the update's causes.

Nothing here is ever rewritten: a state is appended to the store's
record, one entry with its lines' change against its parent's and the
texts and blobs it keeps first, a blob already kept kept once
(`RecordStore.keep_state`). The store's lock
(`RecordStore.locked`) covers every write, so a state is never read half
written.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable, Container, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from axiomation import computations, derived, settings, state_lines
from axiomation.cutter.continuation import sharing, within
from axiomation.cutter.model import Cut, CutRules, DisplayRule, GivenHandles
from axiomation.exchange import (
    ExchangeError,
    cut_from_payload,
    edges_answer_of,
    files_apart,
    named_claims_key,
    vocabulary_of,
)
from axiomation.kept_change import (
    value_applied,
    value_change,
)
from axiomation.questions import edges_file
from axiomation.questions.edge_sheet import CodeFile, free_handle, paper_files
from axiomation.questions.edges_file import DELETED_BLOCK as DELETED
from axiomation.questions.edges_file import KIND as EDGES_KIND
from axiomation.questions.edges_file import state_sheet
from axiomation.reader import Document, document_hash, files_hash, read_paper
from axiomation.reader.includes import STORE_DIRECTORY
from axiomation.records import (
    GITATTRIBUTES,
    IDENTITY,
    AnswerRecord,
    EntryKind,
    LineageLink,
    RecordStore,
    holds,
)

#: What a default store keeps out of the paper's git: a run's transcripts,
#: the locks, room for a cache a slow read may one day need, a file a
#: crash left half written, and a review session's reads while it runs.
GITIGNORE = (
    "transcripts/\n.lock\n.run\nruns/\ncache/\n*.tmp\nsessions/\ndevelop.jsonl\n"
)
#: How many hex digits of its content hash name a state.
STATE_ID_DIGITS = 16
NODE = "n"
GROUP = "g"


class VersionError(ValueError):
    """A state the store cannot record or read as asked."""


class Command(StrEnum):
    """What made a state."""

    BUILD = "build"
    UPDATE = "update"
    #: A person's or an agent's edit of the edges, a new state of the same
    #: version made from the kept one.
    EDIT = "edit"


@dataclass(frozen=True, slots=True)
class Node:
    """A block of a state: its id there, its node id, and its text's id."""

    block: str
    node: str
    text: str


@dataclass(frozen=True, slots=True)
class State:
    """One kept state. `files` maps each kept file's
    path, relative to the paper, to its blob; `groups` each group's id to
    its group node id."""

    id: str
    parent: str | None
    command: Command
    version: str
    root: str
    files: Mapping[str, str]
    export: str
    identity: str | None
    answers: tuple[str, ...]
    nodes: tuple[Node, ...]
    groups: Mapping[str, str]
    at: str
    #: What made it: its run's settings and templates, hashed
    #: (`run_state.made_from`). Provenance only: nothing is rebuilt on it.
    made_from: str | None = None

    def node_of(self, block: str) -> str | None:
        return next((n.node for n in self.nodes if n.block == block), None)

    def block_of(self, node: str) -> str | None:
        return next((n.block for n in self.nodes if n.node == node), None)

    def data(self) -> dict[str, Any]:
        return {"id": self.id, **_content(self), "at": self.at}


def _content(state: State) -> dict[str, Any]:
    """A state's fields that name it: all but its id and its time."""
    return {
        "parent": state.parent,
        "command": state.command.value,
        "version": state.version,
        "root": state.root,
        "files": dict(sorted(state.files.items())),
        "export": state.export,
        "identity": state.identity,
        "answers": list(state.answers),
        "nodes": [[n.block, n.node, n.text] for n in state.nodes],
        "groups": dict(state.groups),
        # only where known: a state kept without a run's state names none
        **({"made_from": state.made_from} if state.made_from is not None else {}),
    }


def _state_from(data: dict[str, Any]) -> State:
    return State(
        id=data["id"],
        parent=data["parent"],
        command=Command(data["command"]),
        version=data["version"],
        root=data["root"],
        files=dict(data["files"]),
        export=data["export"],
        identity=data["identity"],
        answers=tuple(data["answers"]),
        nodes=tuple(Node(*entry) for entry in data["nodes"]),
        groups=dict(data["groups"]),
        at=data["at"],
        made_from=data.get("made_from"),
    )


# --- where the store lives ---------------------------------------------------


def store_of(paper: Path) -> Path:
    """Where the paper's own store lives, `<paper>/axiomation/`, made or
    not."""
    return Path(paper) / STORE_DIRECTORY


def default_store(paper: Path) -> Path:
    """The paper's own store, `<paper>/axiomation/`, made on first use with
    its `.gitignore` and its `.gitattributes` (`records.GITATTRIBUTES`),
    never written over one already there."""
    directory = store_of(paper)
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in ((".gitignore", GITIGNORE), (".gitattributes", GITATTRIBUTES)):
        if not (directory / name).exists():
            (directory / name).write_text(text, encoding="utf-8")
    return directory


def keeps_state(store: Path) -> bool:
    """Whether the store at `store` keeps any state; nothing is made."""
    return holds(Path(store), EntryKind.STATE)


def carries_store(paper: Path) -> bool:
    """Whether a paper's directory holds a store of its own."""
    directory = store_of(paper)
    return directory.is_dir() and any(directory.iterdir())


# --- blobs and texts ----------------------------------------------------------


class Held[T]:
    """What this process derived from the store's files, by a key (a
    file's path, or what names a derived value), each with the stamp of
    what it was derived from when it was read: a blob is named by its
    content and the record is only appended to, so what was derived from
    a record that stands holds, and from one that changed is derived
    again; a value derived from what never changes (a kept state) is kept
    under the stamp `()`. Bounded by the values' weight,
    the least recently used going first; one lock, since the viewer reads
    on several threads."""

    def __init__(self, limit: int) -> None:
        self._held: OrderedDict[Hashable, tuple[tuple[int, ...], T, int]] = (
            OrderedDict()
        )
        self._weight = 0
        self._limit = limit
        self._lock = threading.Lock()

    def get(self, key: Hashable, stamp: tuple[int, ...]) -> T | None:
        with self._lock:
            held = self._held.get(key)
            if held is None or held[0] != stamp:
                return None
            self._held.move_to_end(key)
            return held[1]

    def put(self, key: Hashable, stamp: tuple[int, ...], value: T, weight: int) -> None:
        with self._lock:
            if key in self._held:
                self._weight -= self._held.pop(key)[2]
            self._held[key] = (stamp, value, weight)
            self._weight += weight
            while self._weight > self._limit and len(self._held) > 1:
                _, (_, _, dropped) = self._held.popitem(last=False)
                self._weight -= dropped


#: The blobs read and checked, weighed by their bytes.
BLOB_CACHE_BYTES = 256 * 1024 * 1024
_BLOBS: Held[bytes] = Held(BLOB_CACHE_BYTES)
_BLOB_NAME = re.compile(r"[0-9a-f]{64}")


def blob(store: RecordStore, name: str) -> bytes:
    """A kept blob's bytes, checked against its name."""
    # a blob's name comes from a state's row, which a paper's git carries
    if not _BLOB_NAME.fullmatch(name):
        raise VersionError(f"{name!r} is not a blob's name: 64 hex digits")
    key = (store.key, name)
    held = _BLOBS.get(key, ())
    if held is not None:
        return held
    data = store.kept_blob(name)
    if data is None:
        raise VersionError(f"the store holds no blob {name[:12]}")
    if hashlib.sha256(data).hexdigest() != name:
        raise VersionError(f"blob {name[:12]} does not hold its content")
    _BLOBS.put(key, (), data, len(data))
    return data


def text_id(runs: Sequence[str]) -> str:
    """A text's id: sha256 over its runs, each utf-8 and length-prefixed,
    as `document_hash` hashes files."""
    digest = hashlib.sha256()
    for run in runs:
        data = run.encode("utf-8")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def texts(store: RecordStore) -> dict[str, tuple[str, ...]]:
    """Every kept text by its id, the caller's own mapping."""
    return dict(store.kept_texts())


@dataclass(frozen=True, slots=True)
class _Kept:
    """The kept states as read: those in effect, every one (those that
    wait for the person's word too), and each one's lines by its id."""

    states: tuple[State, ...]
    every: tuple[State, ...]
    lines: Mapping[str, state_lines.Lines]


#: The kept states read, by their store, each while its kept states stand.
KEPT_STATES_HELD = 64
_KEPT_STATES: Held[_Kept] = Held(KEPT_STATES_HELD)
#: A state's row's fields its lines hold (`state_lines.Lines.node_rows`).
_ROW_IN_LINES = ("nodes", "groups")


# --- the kept files -----------------------------------------------------------


def snapshot(directory: Path, document: Document) -> dict[str, bytes]:
    """The files a run on `document` reads, by their paths in `directory`:
    the reader's files, those it never included (they enter the export's
    document half), the top-level files an edge may name (a PDF, a
    `.bib`), the paper's code files by their keys, one outside the
    paper by its `workspace:` key, and its bibliography where it lies
    elsewhere, keyed the same way; never the store. Each file
    the reader read must still hold what it read."""
    directory = Path(directory)
    paths = sorted(
        {source.path for source in document.files}
        | set(document.never_included)
        | set(paper_files(directory, document.never_included))
    )
    found = {path: (directory / path).read_bytes() for path in paths}
    for key, path in computations.code_files(directory).items():
        found.setdefault(key, path.read_bytes())
    bib = _kept_bibliography(directory)
    if bib is not None:
        found.setdefault(*bib)
    changed = [
        source.path
        for source in document.files
        if hashlib.sha256(found[source.path]).hexdigest() != source.sha256
    ]
    if changed:
        raise VersionError(
            f"the paper changed while it was read: {', '.join(changed)}; run again"
        )
    return found


def _kept_bibliography(directory: Path) -> tuple[str, bytes] | None:
    """The paper's bibliography by its key, where it is a file inside the
    paper or its workspace: a workspace shares one bibliography among its
    papers, and the review sessions read it from the state they review.
    None where it names none, or one outside both."""
    read = settings.load(directory)
    bib = settings.bibliography(read)
    if bib is None or not bib.is_file():
        return None
    bib = bib.resolve()
    if bib.is_relative_to(read.paper):
        return bib.relative_to(read.paper).as_posix(), bib.read_bytes()
    if read.workspace_root is not None and bib.is_relative_to(
        read.workspace_root.resolve()
    ):
        key = bib.relative_to(read.workspace_root.resolve()).as_posix()
        return computations.WORKSPACE + key, bib.read_bytes()
    return None


def kept_sheet_files(
    store: RecordStore, state: State, into: Path, never_included: Sequence[str]
) -> tuple[str, ...]:
    """The files a sheet shows on a kept state materialized into `into`:
    its top-level files but code, then each code file it keeps, one outside
    the paper among them, with its opening line from its blob."""
    plain = [
        f for f in paper_files(into, never_included) if not computations.is_code(f)
    ]
    code = [
        CodeFile(
            key, computations.opening_line(blob(store, name).decode("utf-8", "replace"))
        )
        for key, name in sorted(state.files.items())
        if computations.is_code(key)
    ]
    return (*plain, *code)


def materialize(store: RecordStore, state: State, into: Path) -> Document:
    """A state's kept files written into `into`, and read as today's reader
    reads them with the vocabulary map its export was read with: the
    state's version, whatever today's reader hashes (`Document.version`).
    Refused, naming them, where a kept blob does not hold its content, or
    today's reader does not read back a file the export names, byte for
    byte, or reads text beyond them (`exchange.files_apart`): a reader that
    now follows a preamble too opens it. A code file outside the paper
    (`workspace:`) is written nowhere: it is kept for the lapse, and the
    reader never reads it."""
    into = Path(into).resolve()
    for path, name in state.files.items():
        if path.startswith(computations.WORKSPACE):
            continue
        # a kept path comes from `states.jsonl`, which a paper's git carries:
        # never one that leaves `into`
        target = (into / path).resolve()
        if Path(path).is_absolute() or not target.is_relative_to(into):
            raise VersionError(
                f"state {state.id} keeps a file outside its paper: {path!r}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob(store, name))
    export = kept_payload(store, state)
    document = read_paper(into, Path(state.root), vocabulary_of(export))
    apart = files_apart(export, document)
    if apart:
        raise VersionError(
            f"state {state.id}: today's reader does not read its kept files as "
            f"its export names them: {', '.join(apart)}"
        )
    return replace(document, version=state.version)


def as_kept(store: RecordStore, state: State, document: Document) -> Document:
    """`document`, a paper's files as today's reader reads them, named by
    `state`'s version where they are that state's text: each file it reads
    kept by the state byte for byte, and the kept files read by today's
    reader as the same files. A reader that reads other files than the one
    that kept the state (a preamble it now follows) moves their hash, never
    the version their text was kept as (`docs/model.md` § 7). Else
    `document` as it is: an edit not yet updated."""
    if document.version is not None or document_hash(document) == state.version:
        return document
    if any(state.files.get(source.path) != source.sha256 for source in document.files):
        return document
    if files_hash(document_at(store, state)) != files_hash(document):
        return document
    return replace(document, version=state.version)


#: The states' documents read, by their store and their state: a state's
#: kept files never change, and today's reader is this process's code.
DOCUMENTS_HELD = 64
_DOCUMENTS: Held[Document] = Held(DOCUMENTS_HELD)


def document_at(store: RecordStore, state: State) -> Document:
    """A state's document layer: its kept files read by today's reader
    (`materialize`), in a directory that lasts only for the read, once a
    process. One path for every state: a key's export carries the files
    alone, a state's export what the reader found when it was kept, and a
    change in what the reader finds in them or which preambles it follows
    never refuses it."""
    key = (store.key, state.id)
    found = _DOCUMENTS.get(key, ())
    if found is None:
        with tempfile.TemporaryDirectory(prefix="axiomation-document-") as directory:
            found = materialize(store, state, Path(directory))
        _DOCUMENTS.put(key, (), found, 1)
    return found


def edges_answer(store: RecordStore, export: Mapping[str, Any]) -> AnswerRecord | None:
    """The answer a kept export's edges name: an edges answer, or an
    update's identity answer where it asked nothing; none where the export
    names no edges."""
    try:
        key = edges_answer_of(export)
    except ExchangeError as error:
        raise VersionError(str(error)) from None
    if key is None:
        return None
    answer = store.authoritative().get(key)
    if answer is None:
        raise VersionError(
            f"the store holds no answer {key[:12]}, which its edges name"
        )
    if answer.kind not in (EDGES_KIND, IDENTITY):
        raise VersionError(
            f"answer {key[:12]} of kind {answer.kind} gives no edges sheet"
        )
    return answer


def kept_edges(
    store: RecordStore,
    export: Mapping[str, Any],
    document: Document,
    cut: Cut,
    files: Sequence[str],
) -> tuple[AnswerRecord | None, str | None]:
    """The answer a kept export's edges name and the sheet they show on
    `cut`, the state's cut, rendered from the export
    (`edges_file.state_sheet`) in its answer's line order; none where the
    export names no edges."""
    answer = edges_answer(store, export)
    if answer is None:
        return None, None
    orders = store.edge_records_of(answer.key).orders
    return answer, state_sheet(document, cut, files, export, orders)


def kept_sheet(store: RecordStore, kept: State) -> tuple[str, tuple[str, ...]] | None:
    """The edges sheet a kept state shows on its cut, as an edit shows it,
    with the files it shows, the state's own; None where it holds no
    edges."""
    export = kept_payload(store, kept)
    store = store.seen_from(kept.id)
    with tempfile.TemporaryDirectory(prefix="axiomation-kept-") as directory:
        document = materialize(store, kept, Path(directory))
        files = kept_sheet_files(store, kept, Path(directory), document.never_included)
    rules = CutRules(DisplayRule(export["rules"]["displays"]))
    cut = cut_from_payload(document, export, rules, roles=True)
    _, sheet = kept_edges(store, export, document, cut, files)
    return None if sheet is None else (sheet, files)


# --- states -------------------------------------------------------------------


def states(store: RecordStore, waiting: bool = False) -> tuple[State, ...]:
    """Every kept state in effect, each parent before its children, in the
    order every joined copy reads alike (`RecordStore.kept_states`);
    `waiting`: with those made from a person's word no confirmation covers
    yet. Their own next edit builds on these (`docs/architecture.md` § 4,
    "The person's word"); every other read leaves them out."""
    with store.locked():
        return _kept(store).every if waiting else _states(store)


def kept_stamp(store: RecordStore) -> tuple[int, ...]:
    """The kept states as they stand. What is read from kept states alone
    holds while it holds: a state is only ever added, never changed."""
    return store.kept_stamp()


def _states(store: RecordStore) -> tuple[State, ...]:
    return _kept(store).states


def _kept(store: RecordStore) -> _Kept:
    """The kept states, each its row and its lines, folded from its
    parent's along the record; called under the store's lock."""
    stamp = store.kept_stamp()
    found = _KEPT_STATES.get(store.key, stamp)
    if found is None:
        lines: dict[str, state_lines.Lines] = {}
        kept = []
        rows = store.kept_states(waiting=True)
        changes = store.kept_lines(waiting=True)
        for row, change in zip(rows, changes, strict=True):
            try:
                folded = state_lines.applied(lines.get(row["parent"]), change)
            except state_lines.LinesError as error:
                raise VersionError(f"state {row['id']}: {error}") from None
            lines[row["id"]] = folded
            kept.append(
                _state_from(
                    {**row, "nodes": folded.node_rows(), "groups": folded.group_nodes()}
                )
            )
        effect = {row["id"] for row in store.kept_states()}
        found = _Kept(
            tuple(s for s in kept if s.id in effect),
            tuple(kept),
            MappingProxyType(lines),
        )
        _KEPT_STATES.put(store.key, stamp, found, 1)
    return found


def heads(found: Sequence[State]) -> tuple[State, ...]:
    """The states no other names as its parent, in `found`'s order."""
    parents = {s.parent for s in found}
    return tuple(s for s in found if s.id not in parents)


def tips(store: RecordStore, waiting: bool = False) -> tuple[State, ...]:
    """The kept states no other was written after: one, or several where
    joined copies kept them apart (`RecordStore.state_tips`)."""
    found = {s.id: s for s in states(store, waiting)}
    return tuple(found[i] for i in store.state_tips(waiting))


def latest(store: RecordStore, waiting: bool = False) -> State | None:
    """The state every other was written before; refused where joined
    copies kept states apart: which is the paper's is not the record's to
    pick (`docs/architecture.md` § 4, "Copies joined"). A state named
    reads either."""
    found = tips(store, waiting)
    if len(found) > 1:
        raise VersionError(apart(found))
    return found[0] if found else None


def latest_if_one(store: RecordStore) -> State | None:
    """The latest state, None where none is kept or joined copies kept
    states apart: for the reads that list the states rather than read
    one."""
    found = tips(store)
    return found[0] if len(found) == 1 else None


def refuse_apart(store: RecordStore) -> None:
    """Refused where joined copies kept states apart: nothing is kept on top
    of them, so a command that keeps a state refuses before it asks or
    files (`docs/architecture.md` § 4, "Copies joined")."""
    found = tips(store, waiting=True)
    if len(found) > 1:
        named = ", ".join(s.id for s in found)
        raise VersionError(
            f"joined copies kept states apart ({named}): nothing is kept on top "
            "of them until their work is merged; each is read with --state"
        )


def refuse_beside_waiting(store: RecordStore, parent: State | None) -> None:
    """Refused where a state waits for the person's word and `parent`, the
    state a new one would be kept on, is not it: one beside it would leave
    their edit off the state read next. What is kept while it waits is kept
    on it, and waits with it (`docs/architecture.md` § 4, "The person's
    word")."""
    latest = next(iter(tips(store, waiting=True)), None)
    if latest is None or latest in states(store):
        return
    if parent is None or parent.id != latest.id:
        raise VersionError(
            f"state {latest.id} waits for your word: what is kept now is kept on "
            "it, not beside it; `axiomation confirm` it, or drop it, first"
        )


def apart(found: Sequence[State]) -> str:
    """Why no state is the latest: the states kept apart."""
    named = ", ".join(s.id for s in found)
    return (
        f"joined copies kept states apart ({named}): name the one to read with "
        "--state; nothing is kept on top of them until their work is merged"
    )


def state(store: RecordStore, name: str, waiting: bool = False) -> State:
    """The state `name` names: its id, or a prefix of it naming one, among
    the states in effect; `waiting`: among those that wait for the
    person's word too, which only their next edit reads (a filing on a
    state they may drop would name a state no view holds)."""
    found = [s for s in states(store, waiting=waiting) if s.id.startswith(name)]
    if len(found) != 1:
        raise VersionError(
            f"{'no' if not found else len(found)} states of {store.directory} "
            f"start {name!r}"
        )
    return found[0]


def built(store: RecordStore, version: str) -> State | None:
    """The latest whole state of `version`: a build of it asks nothing
    and returns this (`docs/architecture.md` § 4)."""
    refuse_apart(store)
    return next((s for s in reversed(states(store)) if s.version == version), None)


def pending_answers(store: RecordStore, version: str) -> tuple[AnswerRecord, ...]:
    """The answers given on `version`, by a person or an agent, that no
    state of it applied or found stale yet: an edit whose step is not
    answered yet, which its own `edit` resumes. A stale
    one (its file no longer the one a state shows, `review.person.stale`)
    waits for nothing."""
    kept = [s for s in states(store, waiting=True) if s.version == version]
    done = {k for s in kept for k in s.answers}
    for s in kept:
        done |= set(export_outline(store, s).stale)
    given = store.given_answers(version, waiting=True)
    return tuple(a for a in given if a.key not in done)


def kept_export(store: RecordStore, kept: State) -> bytes:
    """A state's export, as the run wrote it: rendered from its lines (and
    kept in the user's cache, `derived`), or kept whole where they did not
    render it back when it was kept. Refused where neither gives its
    bytes."""
    key = (store.key, kept.export)
    held = _BLOBS.get(key, ())
    if held is not None:
        return held
    data = store.kept_blob(kept.export)
    if data is None:
        with store.locked():
            lines = _kept(store).lines.get(kept.id)
        if lines is None:
            raise VersionError(f"the store holds no state {kept.id}")
        # rendered once for this user: a read walking the chain reads every
        # state's export, and rendering one takes the time of reading many
        data = derived.held(f"export-{kept.export}", lambda: state_lines.render(lines))
    if hashlib.sha256(data).hexdigest() != kept.export:
        raise VersionError(
            f"state {kept.id}: its lines do not give its export {kept.export[:12]}"
        )
    _BLOBS.put(key, (), data, len(data))
    return data


def kept_payload(
    store: RecordStore, kept: State, shared: bool = False
) -> dict[str, Any]:
    """A state's export, parsed: built from its lines, never written out as
    text and read back, or read from its export where it is kept whole.
    Its lines were checked against their entry's hash as the record
    loaded; what today's code renders of them, against the export's hash
    (`kept_export`, its bytes held for this user once rendered), so a
    renderer that reads them otherwise is refused, not believed. `shared`:
    for a reader that changes none of it (`state_lines.rendered`)."""
    if store.kept_blob(kept.export) is None:
        with store.locked():
            lines = _kept(store).lines.get(kept.id)
        if lines is not None:
            kept_export(store, kept)
            payload: dict[str, Any] = state_lines.rendered(lines, shared=shared)
            return payload
    return json.loads(kept_export(store, kept))


@dataclass(frozen=True, slots=True)
class Outline:
    """What a walk along a chain reads of each state's export, parsed once
    for every walk: its block ids, then its group ids, in order; each id's
    handle; its update's record (`review.update`), as JSON text, so no
    reader shares another's copy; each block's owned runs, by id, each
    its file and its start and end `[line, column]`; and the given answers
    it found stale (`review.person.stale`)."""

    order: tuple[str, ...]
    handles: Mapping[str, str]
    update_json: str
    owned: tuple[tuple[str, tuple[tuple[str, int, int, int, int], ...]], ...]
    stale: tuple[str, ...] = ()

    def __reduce__(self) -> tuple[Any, ...]:
        # kept in the user's cache (`derived`): its handles as a plain mapping,
        # read-only again once read
        return (
            _outline_of,
            (self.order, dict(self.handles), self.update_json, self.owned, self.stale),
        )

    def update_record(self) -> dict[str, Any]:
        """The update's record, the reader's own copy; empty for none."""
        return json.loads(self.update_json)


#: The exports' outlines, by their store and their blobs, as many as a
#: long chain holds states.
OUTLINES_HELD = 1024
_OUTLINES: Held[Outline] = Held(OUTLINES_HELD)


def export_outline(store: RecordStore, kept: State) -> Outline:
    """`kept`'s export's outline (`Outline`)."""
    key = (store.key, kept.export)
    found = _OUTLINES.get(key, ())
    if found is None:
        # named by its blob's content: every entry of the record is checked
        # against its hash as it is loaded
        found = derived.held(f"outline-{kept.export}", lambda: _outline(store, kept))
        _OUTLINES.put(key, (), found, 1)
    return found


def _outline_of(
    order: tuple[str, ...],
    handles: dict[str, str],
    update_json: str,
    owned: tuple[tuple[str, tuple[tuple[str, int, int, int, int], ...]], ...],
    stale: tuple[str, ...],
) -> Outline:
    return Outline(order, MappingProxyType(handles), update_json, owned, stale)


#: The sections an outline reads, besides the blocks and groups.
_OUTLINED = ("handles", "review/update", "review/person/stale")


def _outline(store: RecordStore, kept: State) -> Outline:
    with store.locked():
        lines = (
            None
            if store.kept_blob(kept.export) is not None
            else _kept(store).lines.get(kept.id)
        )
    export = (
        json.loads(kept_export(store, kept))
        if lines is None
        else state_lines.rendered(lines, _OUTLINED)
    )
    return Outline(
        tuple(b["id"] for b in export["blocks"])
        + tuple(g["id"] for g in export["groups"]),
        MappingProxyType(dict(export.get("handles") or {})),
        json.dumps((export.get("review") or {}).get("update") or {}),
        tuple(
            (
                b["id"],
                tuple((r["file"], *r["start"], *r["end"]) for r in b["owned"]),
            )
            for b in export["blocks"]
        ),
        tuple(((export.get("review") or {}).get("person") or {}).get("stale", [])),
    )


def parent_by_export(store: RecordStore, export: bytes) -> State | None:
    """The latest state whose export is `export`, byte for byte."""
    name = hashlib.sha256(export).hexdigest()
    return next((s for s in reversed(states(store)) if s.export == name), None)


def parent_by_identity(store: RecordStore, identity: str) -> State | None:
    """The state an update by the identity answer `identity` started from:
    the latest of OLD's version whose edge half is the edges answer the
    identity answer carried (`edges_answer`; before, the answer its
    edges name)."""
    answer = store.authoritative().get(identity)
    if answer is None or answer.kind != IDENTITY:
        return None
    return parent_by_raw(store, identity_raw(store, answer))


def parent_by_raw(store: RecordStore, raw: Mapping[str, Any]) -> State | None:
    """`parent_by_identity` from the identity answer's raw, recorded or not
    (a dry run makes one in memory)."""
    carried = (raw.get("carried") or {}).get("from")
    # an edit's update may start from the person's waiting edit
    for kept in reversed(states(store, waiting=True)):
        if kept.version != raw.get("old"):
            continue
        export = kept_payload(store, kept)
        if export.get("edges_answer") == carried:
            return kept
    return None


def record(
    store: RecordStore,
    files: Mapping[str, bytes],
    version: str,
    root: str,
    export: bytes,
    command: Command,
    started_from: State | None = None,
    identity: str | None = None,
    answers: Sequence[str] = (),
    made_from: str | None = None,
    session: Mapping[str, Any] | None = None,
    given: tuple[Mapping[str, str], Mapping[str, str]] | None = None,
) -> tuple[State, bool]:
    """A whole run's state appended: its kept files and export, its nodes
    minted or continued, its texts; and whether it is new. Its parent is
    the latest state of its version that the same identity answer made
    (any, for a build: a re-run, a person's answer applied), else
    `started_from` (the state an update took); an edit's is always
    `started_from`, the state it was given on. A state equal to one kept,
    or a re-run whose export and files equal its parent's, adds nothing: a
    script or a figure changed alone is a state of its own, which lapses
    what rests on it. `version` is the document hash of the
    files the run read, `root` their root file; `session` what later
    reads take from an update's sessions' answers (`Update.session_facts`),
    kept in its step; `given` the node of each
    block and the group node of each group, as another's store minted them
    (a share's), in place of minting."""
    payload = json.loads(export)
    held = {f["path"]: f["sha256"] for f in payload["document"]["files"]}
    for path, sha in held.items():
        if path not in files or hashlib.sha256(files[path]).hexdigest() != sha:
            raise VersionError(f"the export's file {path} is not among the kept files")
    texts_of = {
        path: data.decode("utf-8", errors="replace") for path, data in files.items()
    }
    # read before the lock, which the store's own reads take
    linked = (
        None
        if identity is None
        else (identity, _identity_raw(store, identity), store.lineage_of(identity))
    )
    # an update's step, kept once, with the first state its update made
    step = (
        None
        if linked is None or store.kept_step(identity) is not None
        else identity_step(
            store,
            identity,
            linked[1],
            payload,
            _document_of(files, root, payload),
            session,
        )
    )
    with store.locked():
        kept = _states(store)
        refuse_apart(store)
        latest = next(iter(tips(store, waiting=True)), None)
        # a re-run of the same update (or build) continues its own state
        # while it is the latest; an update otherwise continues the state
        # it started from, and an edit always the state it was given on
        # its own state waiting for the person's word among them (an edit's
        # never: an edit's is the person's)
        own = [
            *kept,
            *(
                [latest]
                if latest is not None
                and latest not in kept
                and latest.command is not Command.EDIT
                else []
            ),
        ]
        same = [
            s
            for s in own
            if s.version == version and (identity is None or s.identity == identity)
        ]
        if command is Command.EDIT:
            if started_from is None:
                raise VersionError("an edit's state names the state it was given on")
            same = []
        # while a state waits, a re-run continues its own only where it is
        # that state: nothing is kept beside one that waits
        parent = (
            same[-1]
            if same and (latest is None or latest in kept or same[-1].id == latest.id)
            else started_from
        )
        export_name = hashlib.sha256(export).hexdigest()
        kept_files = {
            path: hashlib.sha256(data).hexdigest() for path, data in files.items()
        }
        if (
            parent is not None
            and parent.version == version
            and parent.export == export_name
            and parent.files == kept_files
        ):
            return parent, False
        parent_payload = None if parent is None else kept_payload(store, parent)
        continues = _continues(
            parent, parent_payload, payload, texts_of, linked, version
        )
        if given is None:
            # none a state holds, in effect or not: a waiting state's
            # nodes are its own once the person confirms it
            nodes, groups = _minted(
                _kept(store).every, parent, parent_payload, payload, continues
            )
        else:
            nodes, groups = dict(given[0]), dict(given[1])
            missing = [b["id"] for b in payload["blocks"] if b["id"] not in nodes] + [
                g["id"] for g in payload["groups"] if g["id"] not in groups
            ]
            if missing:
                raise VersionError(f"no node is given for {missing[0]}")
        offsets = _Offsets(texts_of)
        block_texts = {
            b["id"]: tuple(_cut(texts_of, offsets, run) for run in b["owned"])
            for b in payload["blocks"]
        }
        found = State(
            id="",
            parent=None if parent is None else parent.id,
            command=command,
            version=version,
            root=root,
            files=kept_files,
            export=export_name,
            identity=identity,
            answers=tuple(answers),
            nodes=tuple(
                Node(block, nodes[block], text_id(block_texts[block]))
                for block in (b["id"] for b in payload["blocks"])
            ),
            groups=groups,
            at=datetime.now(UTC).isoformat(timespec="seconds"),
            made_from=made_from,
        )
        content = json.dumps(_content(found), sort_keys=True).encode("utf-8")
        state_id = hashlib.sha256(content).hexdigest()[:STATE_ID_DIGITS]
        # a waiting one among them: a content kept once is never kept again
        existing = next((s for s in _kept(store).every if s.id == state_id), None)
        if existing is not None:
            return existing, False
        refuse_beside_waiting(store, parent)
        found = State(**{**_fields(found), "id": state_id})
        lines = state_lines.extract(_content(found)["nodes"], found.groups, export)
        # an export its lines do not give back byte for byte is kept whole
        whole = (export,) if state_lines.render(lines) != export else ()
        store.keep_state(
            {k: v for k, v in found.data().items() if k not in _ROW_IN_LINES},
            state_lines.change(
                None if parent is None else _kept(store).lines[parent.id], lines
            ),
            {text_id(runs): runs for runs in block_texts.values()},
            {
                hashlib.sha256(data).hexdigest(): data
                for data in (*files.values(), *whole)
            },
            # a file is kept as its change of the one its parent kept
            {
                name: parent.files[path]
                for path, name in kept_files.items()
                if parent is not None and path in parent.files
            },
            step,
        )
        if step is not None:
            store.drop_held_identity(step[_RAW]["new"])
        return found, True


def _fields(found: State) -> dict[str, Any]:
    return {name: getattr(found, name) for name in State.__slots__}


# --- spans --------------------------------------------------------------------


class _Offsets:
    """`[line, column]` positions to offsets, per file."""

    def __init__(self, texts_of: Mapping[str, str]) -> None:
        self.starts: dict[str, list[int]] = {}
        for path, text in texts_of.items():
            starts = [0]
            starts.extend(m.end() for m in re.finditer("\n", text))
            self.starts[path] = starts

    def run(self, data: Mapping[str, Any]) -> tuple[str, int, int]:
        starts = self.starts[data["file"]]
        line, column = data["start"]
        end_line, end_column = data["end"]
        return (
            data["file"],
            starts[line - 1] + column - 1,
            starts[end_line - 1] + end_column - 1,
        )


def _cut(
    texts_of: Mapping[str, str], offsets: _Offsets, data: Mapping[str, Any]
) -> str:
    """A run's characters in the kept files."""
    path, start, end = offsets.run(data)
    return texts_of[path][start:end]


def _owned(
    payload: Mapping[str, Any], offsets: _Offsets
) -> dict[str, list[tuple[str, int, int]]]:
    return {
        b["id"]: [offsets.run(run) for run in b["owned"]] for b in payload["blocks"]
    }


def _outline_owned(
    outline: Outline, offsets: _Offsets
) -> dict[str, list[tuple[str, int, int]]]:
    """`_owned` over an export's outline."""
    found = {}
    for block, runs in outline.owned:
        found[block] = [
            (
                path,
                offsets.starts[path][line - 1] + column - 1,
                offsets.starts[path][end_line - 1] + end_column - 1,
            )
            for path, line, column, end_line, end_column in runs
        ]
    return found


# --- continuation ---------------------------------------------------------------


def across(
    links: Sequence[LineageLink],
    old_order: Sequence[str] = (),
    new_order: Sequence[str] = (),
) -> dict[str, str]:
    """Identity's lineage as continuation: each NEW block that continues an
    OLD one. A one-to-one pair continues; a split continues in its first
    piece in NEW's reading order (`new_order`), which holds the old start,
    the other pieces new; a merge continues its first block in OLD's
    (`old_order`), the others end (the start decides, not
    identity's `Link.heir`, which carries the lines). An added block
    continues nothing; a deleted one ends."""
    old_at = {b: i for i, b in enumerate(old_order)}
    new_at = {b: i for i, b in enumerate(new_order)}

    def first(blocks: Sequence[str], at: Mapping[str, int]) -> str:
        return min(blocks, key=lambda b: at.get(b, len(at)))

    found: dict[str, str] = {}
    for link in links:
        if not link.old or not link.new:
            continue
        found[first(link.new, new_at)] = first(link.old, old_at)
    return found


def _identity_cut(store: RecordStore, identity: str) -> dict[str, Any]:
    """The rebased cut an identity answer holds: its step's alone where an
    update kept one, nothing else of the step read."""
    kept = store.kept_step(identity)
    if kept is None:
        return _identity_raw(store, identity)["cut"]
    state_id, step = kept
    by_id = {s.id: s for s in states(store, waiting=True)}
    return step_cut(kept_payload(store, by_id[state_id]), step[_RAW]["cut"])


def _identity_raw(store: RecordStore, identity: str) -> dict[str, Any]:
    answer = store.authoritative().get(identity)
    if answer is None or answer.kind != IDENTITY:
        raise VersionError(f"the store holds no identity answer {identity[:12]}")
    return identity_raw(store, answer)


# --- an update's step ---------------------------------------------------------

#: A step's fields: the identity answer it keeps, the state its update
#: started from, and the answer's raw less what the states give.
_ANSWER = "answer"
_OLD_STATE = "from"
_RAW = "raw"
#: A value the raw keeps as its change of what the states give, and what
#: the links are instead: the lineage records.
_CHANGE = "change"
_LINEAGE = "lineage"
#: What later reads take from the update's sessions' answers
#: (`session_facts`).
_SESSION = "session"
#: The carried sheet as its own lines (`edges_file.SheetLines`).
_LINES = "lines"


def identity_step(
    store: RecordStore,
    key: str,
    raw: Mapping[str, Any],
    new_export: Mapping[str, Any],
    document: Document,
    session: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """An update's step: its identity answer `raw` (key `key`) less what
    the states give (`docs/architecture.md` § 4). Its links are the
    lineage records; the carried lines' authors (`carried.origins`) are
    the run's, read only while NEW's export is written, which holds them;
    the carried sheet is kept as its own lines, which today's code renders
    on the rebased cut of NEW's text (`document`) on reading, or whole
    where that rendering does not give it back byte for byte; the rebased
    cut as its change of NEW's cut, the carried excerpts as their change
    of those NEW's export lists. `step_raw` gives the rest back exactly.
    What later reads take from its sessions' answers (`session`) is kept
    as given, the marks' cut as its change of NEW's cut
    (`session_facts`)."""
    old = parent_by_raw(store, raw)
    reduced: dict[str, Any] = {}
    for name, value in raw.items():
        if name == "links":
            value = _LINEAGE
        elif name == "cut":
            base = {k: new_export.get(k) for k in value}
            value = {"keys": list(value), _CHANGE: value_change(base, value)}
        elif name == "carried" and isinstance(value, Mapping):
            value = _carried_step(value, new_export, raw, document)
        reduced[name] = value
    step = {_ANSWER: key, _OLD_STATE: None if old is None else old.id, _RAW: reduced}
    if session is not None:
        facts = dict(session)
        if "marks" in facts:
            cut = facts["marks"]["cut"]
            base = {k: new_export.get(k) for k in cut}
            facts["marks"] = {
                **facts["marks"],
                "cut": {"keys": list(cut), _CHANGE: value_change(base, cut)},
            }
        step[_SESSION] = facts
    return step


def session_facts(store: RecordStore, key: str) -> dict[str, Any] | None:
    """What later reads take from the sessions' answers of the update whose
    identity answer is `key`, as its step keeps them
    (`update.Update.session_facts`); None where it keeps none: no session
    was answered."""
    kept = store.kept_step(key)
    if kept is None or _SESSION not in kept[1]:
        return None
    state_id, step = kept
    facts = dict(step[_SESSION])
    if "marks" in facts:
        new = {s.id: s for s in states(store, waiting=True)}[state_id]
        cut = facts["marks"]["cut"]
        facts["marks"] = {
            **facts["marks"],
            "cut": step_cut(kept_payload(store, new), cut),
        }
    return facts


def _rebased(document: Document, raw: Mapping[str, Any]) -> Cut:
    """The rebased cut an identity answer's raw holds, on NEW's text."""
    rules = CutRules(DisplayRule(raw["cut"]["rules"]["displays"]))
    return cut_from_payload(document, raw["cut"], rules, roles=True)


def _document_of(
    files: Mapping[str, bytes], root: str, export: Mapping[str, Any]
) -> Document:
    """A run's files read as `materialize` reads a state's, before it is
    kept."""
    with tempfile.TemporaryDirectory(prefix="axiomation-kept-") as directory:
        into = Path(directory).resolve()
        for path, data in files.items():
            if path.startswith(computations.WORKSPACE):
                continue
            target = (into / path).resolve()
            if Path(path).is_absolute() or not target.is_relative_to(into):
                raise VersionError(f"a run keeps a file outside its paper: {path!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return read_paper(into, Path(root), vocabulary_of(export))


def _carried_step(
    carried: Mapping[str, Any],
    new_export: Mapping[str, Any],
    raw: Mapping[str, Any],
    document: Document,
) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name, value in carried.items():
        if name == "origins":
            continue
        if name == "sheet" and isinstance(value, str):
            held = edges_file.sheet_lines(value)
            why = carried.get("why") or {}
            # a sheet its lines do not give back byte for byte is kept whole
            rebased = _rebased(document, raw)
            if edges_file.lines_sheet(document, rebased, held, why) == value:
                value = {_LINES: held.data()}
        elif name == "excerpts":
            value = {_CHANGE: value_change(_listed_excerpts(new_export), value)}
        found[name] = value
    return found


def _listed_excerpts(export: Mapping[str, Any]) -> Any:
    return ((export.get("review") or {}).get("update") or {}).get("excerpts")


def identity_raw(store: RecordStore, answer: AnswerRecord) -> dict[str, Any]:
    """An identity answer's raw as its update's step keeps it (`step_raw`),
    else as held until its update keeps one."""
    if store.kept_step(answer.key) is None:
        held = store.held_identity(answer.key)
        if held is not None:
            return held
    return step_raw(store, answer.key)


def step_raw(store: RecordStore, key: str) -> dict[str, Any]:
    """The identity answer `key`'s raw as its update's step keeps it
    (`identity_step`), each value given back from the states, without the
    carried lines' authors."""
    kept = store.kept_step(key)
    if kept is None:
        raise VersionError(f"no state keeps the step of identity answer {key[:12]}")
    state_id, step = kept
    by_id = {s.id: s for s in states(store, waiting=True)}
    new = by_id[state_id]
    new_export = kept_payload(store, new)
    found: dict[str, Any] = {}
    for name, value in step[_RAW].items():
        if name == "links":
            value = [_link_data(link) for link in store.lineage_of(key)]
        elif name == "cut":
            value = step_cut(new_export, value)
        found[name] = value
    carried = found.get("carried")
    if isinstance(carried, Mapping):
        found["carried"] = _carried_raw(store, new, carried, new_export, found)
    return found


def step_cut(new_export: Mapping[str, Any], kept: Mapping[str, Any]) -> dict[str, Any]:
    """The rebased cut a step keeps (`kept`, its change of NEW's cut)."""
    base = {k: new_export.get(k) for k in kept["keys"]}
    return value_applied(base, kept[_CHANGE])


def _carried_raw(
    store: RecordStore,
    new: State,
    carried: Mapping[str, Any],
    new_export: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name, value in carried.items():
        if name == "sheet" and isinstance(value, Mapping):
            document = document_at(store, new)
            value = edges_file.lines_sheet(
                document,
                _rebased(document, raw),
                edges_file.SheetLines.of(value[_LINES]),
                carried.get("why") or {},
            )
        elif name == "excerpts":
            value = value_applied(_listed_excerpts(new_export), value[_CHANGE])
        found[name] = value
    return found


def _link_data(link: LineageLink) -> dict[str, Any]:
    """A lineage record as the identity answer's raw writes its link
    (`exchange.diff_payload`)."""
    return {
        "old": list(link.old),
        "new": list(link.new),
        "cls": link.cls,
        "tier": link.tier,
        **({"heir": link.heir} if link.heir else {}),
        **({"moved": True} if link.moved else {}),
    }


def _continues(
    parent: State | None,
    parent_payload: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
    texts_of: Mapping[str, str],
    identity: tuple[str, dict[str, Any], tuple[LineageLink, ...]] | None,
    version: str,
) -> dict[str, str]:
    """Each block of the state's cut that continues one of the parent's:
    within one version by `within`; across versions by identity's lineage
    onto the rebased cut, then by `within` from the rebased cut to the cut
    the run ended on."""
    if parent is None or parent_payload is None:
        return {}
    offsets = _Offsets(texts_of)
    final = _owned(payload, offsets)
    if parent.version == version:
        return within(_owned(parent_payload, offsets), final)
    if identity is None:
        return {}
    key, raw, links = identity
    if raw.get("old") != parent.version:
        raise VersionError(
            f"identity answer {key[:12]} starts from {raw.get('old', '')[:12]}, "
            f"not from state {parent.id}'s version"
        )
    rebased = _owned(raw["cut"], offsets)
    onto = within(rebased, final)
    linked = across(links, [b["id"] for b in parent_payload["blocks"]], list(rebased))
    return {
        block: linked[from_rebased]
        for block, from_rebased in onto.items()
        if from_rebased in linked
    }


#: A node id's tail: lower-case base32, 10 characters, 50 bits drawn at
#: birth, so copies kept apart never mint one id twice (`docs/model.md` § 7)
ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
ID_LENGTH = 10
#: How a tail's character is drawn; the suite draws from a seed of its own
_draw: Callable[[str], str] = secrets.choice


def _fresh(prefix: str, taken: set[str]) -> str:
    """An id `prefix` and a random tail, none of `taken`; added to it."""
    while True:
        found = prefix + "".join(_draw(ID_ALPHABET) for _ in range(ID_LENGTH))
        if found not in taken:
            taken.add(found)
            return found


#: The fewest characters a node id is shown by, its prefix among them
SHORTEST_ID = 5


def short_ids(ids: Iterable[str]) -> dict[str, str]:
    """Each id by its shortest prefix none of the others starts with, of
    `SHORTEST_ID` characters at least: what a person reads and types
    (`docs/model.md` § 7)."""
    found = sorted(set(ids))
    shown = {}
    for i, ident in enumerate(found):
        near = found[max(i - 1, 0) : i] + found[i + 1 : i + 2]
        length = max([SHORTEST_ID] + [_shared_length(ident, o) + 1 for o in near])
        shown[ident] = ident[:length]
    return shown


def _shared_length(one: str, other: str) -> int:
    """How many leading characters `one` and `other` share."""
    return next(
        (i for i, (a, b) in enumerate(zip(one, other, strict=False)) if a != b),
        min(len(one), len(other)),
    )


def _minted(
    kept: Sequence[State],
    parent: State | None,
    parent_payload: Mapping[str, Any] | None,
    payload: Mapping[str, Any],
    continues: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """The state's node per block and group node per group: the parent's
    where a block continues one of its blocks (each parent node once),
    else a new one, drawn at random and none the store holds. A group
    continues the parent's group sharing most of its blocks' nodes, each
    once."""
    taken = {n.node for s in kept for n in s.nodes} | {
        g for s in kept for g in s.groups.values()
    }
    parent_nodes = {} if parent is None else {n.block: n.node for n in parent.nodes}
    nodes: dict[str, str] = {}
    used: set[str] = set()
    for b in payload["blocks"]:
        old = continues.get(b["id"])
        node = parent_nodes.get(old) if old is not None else None
        if node is None or node in used:
            node = _fresh(NODE, taken)
        used.add(node)
        nodes[b["id"]] = node
    groups: dict[str, str] = {}
    parent_groups = (
        []
        if parent is None or parent_payload is None
        else [
            (
                parent.groups[g["id"]],
                frozenset(parent_nodes[b] for b in g["blocks"] if b in parent_nodes),
            )
            for g in parent_payload["groups"]
            if g["id"] in parent.groups
        ]
    )
    shares = []
    for i, g in enumerate(payload["groups"]):
        members = frozenset(nodes[b] for b in g["blocks"] if b in nodes)
        for j, (gnode, old_members) in enumerate(parent_groups):
            shared = len(members & old_members)
            if shared:
                shares.append((-shared, i, j, g["id"], gnode))
    shares.sort()
    continued: set[str] = set()
    for _, _, _, gid, gnode in shares:
        if gid in groups or gnode in continued:
            continue
        groups[gid] = gnode
        continued.add(gnode)
    for g in payload["groups"]:
        if g["id"] not in groups:
            groups[g["id"]] = _fresh(GROUP, taken)
    return nodes, groups


# --- reads: a state's graph, a node's history, edges across states --------------


def graph(store: RecordStore, kept: State) -> dict[str, Any]:
    """A kept version's graph: its export, read through the node ids."""
    return {
        "state": kept.id,
        "version": kept.version,
        "nodes": {n.block: n.node for n in kept.nodes},
        "groups": dict(kept.groups),
        "export": kept_payload(store, kept),
    }


def chain(store: RecordStore, last: State | None = None) -> tuple[State, ...]:
    """The states from the root to `last` (the latest without it)."""
    found = states(store)
    by_id = {s.id: s for s in states(store, waiting=True)}
    at = last if last is not None else (found[-1] if found else None)
    out: list[State] = []
    while at is not None:
        out.append(at)
        at = by_id.get(at.parent) if at.parent is not None else None
    return tuple(reversed(out))


@dataclass(frozen=True, slots=True)
class Step:
    """A node at one state of its history: the state, its block there
    (None where the node ended), its text's id, how it came from the state
    before (`change`: identity's class across versions, `same` or `changed`
    within one, `added` or `built` where it began, `deleted`, `merged` or
    `recut` where it ended), the nodes a split or a merge joined it with
    (within one version, a cut edit's), the causes the run
    recorded for it, and its edges the step began, ended or changed
    (none where it began)."""

    state: str
    command: Command
    version: str
    block: str | None
    text: str | None
    change: str
    joined: tuple[str, ...] = ()
    causes: tuple[dict[str, Any], ...] = ()
    edges: tuple[EdgeChange, ...] = ()

    def data(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "command": self.command.value,
            "version": self.version,
            "block": self.block,
            "text": self.text,
            "change": self.change,
            "joined": list(self.joined),
            "causes": list(self.causes),
            "edges": [e.data() for e in self.edges],
        }


#: The kind an `EdgeChange` names for a self mark.
SELF_MARK = "self"
#: An edge's fields that say nothing of what it links: rewritten, it is
#: the same edge.
_EDGE_WORDING = frozenset({"id", "evidence", "provenance", "note", "mark"})


class EdgeStep(StrEnum):
    """What a step did to an edge or a self mark."""

    BEGAN = "began"
    ENDED = "ended"
    #: the same ends and kind, otherwise changed: the parts it names, or
    #: what it says of the link (partial, where used, …)
    CHANGED = "changed"
    #: across a split or a merge, the same edge gone on from this node to
    #: a node it was joined with (`piece`), or come from one onto it: the
    #: line carried, neither ended nor begun (`docs/model.md` § 7)
    CARRIED_TO = "carried-to"
    CARRIED_FROM = "carried-from"


@dataclass(frozen=True, slots=True)
class EdgeChange:
    """An edge, or a self mark, that a step began, ended or changed: what
    it did (`EdgeStep`), its kind (`SELF_MARK` for a self mark), the node
    it is from, the block or group node it points at (None for a self
    mark, or an edge onto a scope, which its parts name), the parts it
    names (its entries at the target, the claims it names at either end)
    and, changed, those it named before (`was`), who
    wrote it (`agent` or `person`), and, carried, the node of the split or
    merge it went on to or came from (`piece`). Its evidence, note or
    authorship rewritten is no change of it."""

    step: EdgeStep
    kind: str
    source: str
    target: str | None
    parts: tuple[str, ...]
    was: tuple[str, ...]
    by: str
    piece: str | None = None

    def data(self) -> dict[str, Any]:
        return {
            "step": self.step.value,
            "kind": self.kind,
            "source": self.source,
            "target": self.target,
            "parts": list(self.parts),
            "was": list(self.was),
            "by": self.by,
            "piece": self.piece,
        }

    def moved(self, node: str, piece: str) -> tuple[Any, ...]:
        """What makes it the same edge with `node`'s end on `piece`: its
        kind, its ends, its parts without the handle an entry names (a
        piece's handle is not its node's)."""
        ends = tuple(
            piece if end == node else end for end in (self.source, self.target)
        )
        return (self.kind, ends, tuple(p.split(" [", 1)[-1] for p in self.parts))


def _carried(
    node: str,
    joined: Sequence[str],
    changes: Mapping[str, Sequence[EdgeChange]],
) -> tuple[EdgeChange, ...]:
    """`node`'s edge changes at a step that split or merged it with
    `joined`: an edge it ended that the same step began on a joined node,
    carried there; one it began that a joined node ended, carried from
    there. The rest as they are."""
    out: list[EdgeChange] = []
    for e in changes.get(node, ()):
        found = None
        if e.step in (EdgeStep.ENDED, EdgeStep.BEGAN):
            other = EdgeStep.BEGAN if e.step is EdgeStep.ENDED else EdgeStep.ENDED
            for piece in joined:
                if any(
                    x.step is other and x.moved(piece, piece) == e.moved(node, piece)
                    for x in changes.get(piece, ())
                ):
                    found = piece
                    break
        if found is None:
            out.append(e)
        else:
            step = (
                EdgeStep.CARRIED_TO
                if e.step is EdgeStep.ENDED
                else EdgeStep.CARRIED_FROM
            )
            out.append(replace(e, step=step, piece=found))
    return tuple(out)


def _node(written: str) -> str:
    return written.removeprefix(state_lines.MARK)


def _kept_edges(lines: state_lines.Lines) -> dict[str, EdgeChange]:
    """A state's edges and self marks, each by what it links (all but its
    wording), as an `EdgeChange` that began."""
    found: dict[str, EdgeChange] = {}
    for path in ("edges", "self"):
        section = lines.sections.get(path)
        if section is None:
            continue
        for key in section.order:
            item = section.items[key]
            by = item["provenance"]["by"]
            same = {k: v for k, v in item.items() if k not in _EDGE_WORDING}
            if path == "self":
                claims = item.get("claims") or ()
                edge = EdgeChange(
                    EdgeStep.BEGAN,
                    SELF_MARK,
                    _node(item["block"]),
                    None,
                    tuple(f"“{c}”" for c in claims),
                    (),
                    by,
                )
            else:
                target = item["target"]
                parts = (
                    *(target.get("entries") or ()),
                    *(f"“{c}”" for c in target.get("claims") or ()),
                    *(f"on “{c}”" for c in item.get("source_claims") or ()),
                )
                # an edge onto a scope (the paper) points at no node
                onto = target.get("block") or target.get("group")
                if onto is None and target.get("scope"):
                    parts = (f"the {target['scope']}", *parts)
                edge = EdgeChange(
                    EdgeStep.BEGAN,
                    item["kind"],
                    _node(item["source"]),
                    None if onto is None else _node(onto),
                    tuple(parts),
                    (),
                    by,
                )
            found[json.dumps([path, same], sort_keys=True, ensure_ascii=False)] = edge
    return found


def _edge_changes(
    before: state_lines.Lines, after: state_lines.Lines
) -> Mapping[str, tuple[EdgeChange, ...]]:
    """Each node's edges a step began, ended or changed, from the two
    states' lines, on both its ends' nodes: an edge ended and one begun with
    the same kind and ends, one changed, paired in the lines' order."""
    old, new = _kept_edges(before), _kept_edges(after)
    ended: dict[tuple[str, str, str | None], list[EdgeChange]] = {}
    for same in (k for k in old if k not in new):
        e = old[same]
        ended.setdefault((e.kind, e.source, e.target), []).append(e)
    changes: list[EdgeChange] = []
    for same in (k for k in new if k not in old):
        e = new[same]
        was = ended.get((e.kind, e.source, e.target))
        if was:
            changes.append(replace(e, step=EdgeStep.CHANGED, was=was.pop(0).parts))
        else:
            changes.append(e)
    changes += [replace(e, step=EdgeStep.ENDED) for es in ended.values() for e in es]
    found: dict[str, list[EdgeChange]] = {}
    for e in changes:
        for node in dict.fromkeys(n for n in (e.source, e.target) if n):
            found.setdefault(node, []).append(e)
    return MappingProxyType({n: tuple(es) for n, es in found.items()})


@dataclass(frozen=True, slots=True)
class Transition:
    """From a parent state to a child: across versions, each child block's
    identity link (through the rebased cut) and each parent block's, and
    the child's block each rebased block became; the child export's
    `review.update` and handles."""

    across: bool
    link_of_new: Mapping[str, LineageLink]
    link_of_old: Mapping[str, LineageLink]
    update: Mapping[str, Any]
    handles: Mapping[str, str]
    final_of_rebased: Mapping[str, str] = field(default_factory=dict)
    origins: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def finals(self, rebased: Sequence[str]) -> tuple[str, ...]:
        """The child's blocks the rebased blocks became."""
        return tuple(
            b for r in rebased if (b := self.final_of_rebased.get(r)) is not None
        )


#: The transitions worked out, by their store and their two states: a kept
#: state, its export and its identity's lineage never change, so neither
#: does the step between two of them.
TRANSITIONS_HELD = 4096
_TRANSITIONS: Held[Transition] = Held(TRANSITIONS_HELD)
#: Each step's edge changes by node, held as the transitions are.
_EDGE_CHANGES: Held[Mapping[str, tuple[EdgeChange, ...]]] = Held(TRANSITIONS_HELD)


def touched(store: RecordStore, parent: State, child: State) -> frozenset[str] | None:
    """The block and group nodes a step from `parent` to `child` touched,
    read from the two states' lines: every node an item names that one
    holds and the other does not, and every node whose block, text or group
    differs. None where the step changed the paper's version or files, or
    a state holds no lines: then any node may have changed."""
    if parent.version != child.version or parent.files != child.files:
        return None
    with store.locked():
        held = _kept(store).lines
        before, after = held.get(parent.id), held.get(child.id)
    if before is None or after is None:
        return None
    found: set[str] = set()
    handles = state_lines.handle_nodes(before) | state_lines.handle_nodes(after)
    for path in before.sections.keys() | after.sections.keys():
        old = before.sections.get(path)
        new = after.sections.get(path)
        old_keys = set(old.order) if old is not None else set()
        new_keys = set(new.order) if new is not None else set()
        for key in old_keys ^ new_keys:
            item = (new if key in new_keys else old).items[key]  # type: ignore[union-attr]
            found.update(state_lines.nodes_named(item, handles))
    for old_section, new_section in (
        (before.nodes, after.nodes),
        (before.groups, after.groups),
    ):
        for node in old_section.items.keys() | new_section.items.keys():
            if old_section.items.get(node) != new_section.items.get(node):
                found.add(node)
    return frozenset(found)


def transition(store: RecordStore, parent: State, child: State) -> Transition:
    """From `parent` to `child` (`Transition`), worked out once a process.
    Shared by every reader: none changes it."""
    key = (store.directory.resolve(), parent.id, child.id)
    found = _TRANSITIONS.get(key, ())
    if found is None:
        found = derived.held(
            f"step-{parent.id}-{child.id}",
            lambda: _transition(store.seen_from(child.id), parent, child),
        )
        _TRANSITIONS.put(key, (), found, 1)
    return found


def _transition(store: RecordStore, parent: State, child: State) -> Transition:
    outline = export_outline(store, child)
    update = outline.update_record()
    handles = dict(outline.handles)
    texts_of = {
        path: blob(store, name).decode("utf-8", errors="replace")
        for path, name in child.files.items()
    }
    offsets = _Offsets(texts_of)
    final = _outline_owned(outline, offsets)
    parent_nodes = {n.block: n.node for n in parent.nodes}
    if parent.version == child.version or child.identity is None:
        sources: dict[str, list[str]] = {b: [] for b in final}
        if parent.version == child.version:
            parent_owned = _outline_owned(export_outline(store, parent), offsets)
            sources = sharing(parent_owned, final)
        return Transition(
            False, {}, {}, update, handles, origins=_origins(child, sources, parent)
        )
    links = store.lineage_of(child.identity)
    rebased = _owned(_identity_cut(store, child.identity), offsets)
    onto = within(rebased, final)
    by_rebased = {new: link for link in links for new in link.new}
    sources = {
        b: [o for r in shared if r in by_rebased for o in by_rebased[r].old]
        for b, shared in sharing(rebased, final).items()
    }
    order = {block: i for i, block in enumerate(parent_nodes)}
    sources = {
        b: sorted(dict.fromkeys(o), key=lambda x: order.get(x, len(order)))
        for b, o in sources.items()
    }
    return Transition(
        True,
        {b: by_rebased[r] for b, r in onto.items() if r in by_rebased},
        {old: link for link in links for old in link.old},
        update,
        handles,
        {r: b for b, r in onto.items()},
        _origins(child, sources, parent),
    )


def _origins(
    child: State, sources: Mapping[str, Sequence[str]], parent: State
) -> dict[str, tuple[str, ...]]:
    """Each node of `child` and the parent's nodes its text came from,
    from the parent's blocks each child block's text came from: a
    continued node itself first, a split's other piece the original, a
    merge's result each block it took in; an added node none."""
    parent_nodes = {n.block: n.node for n in parent.nodes}
    held = set(parent_nodes.values())
    found = {}
    for n in child.nodes:
        came = [parent_nodes[b] for b in sources.get(n.block, ()) if b in parent_nodes]
        if n.node in held:
            came.insert(0, n.node)
        found[n.node] = tuple(dict.fromkeys(came))
    return found


def origins(
    store: RecordStore, older: State, newer: State
) -> dict[str, tuple[str, ...]]:
    """Each node of `newer` and the nodes of `older` its text came from,
    along `newer`'s parents: within one version by shared
    characters, across versions through identity's lineage. A continued
    node names itself first; an added node names none."""
    path = chain(store, newer)
    ids = [s.id for s in path]
    if older.id not in ids:
        raise VersionError(f"state {older.id} is not before state {newer.id}")
    found = {n.node: (n.node,) for n in older.nodes}
    parent = older
    for kept in path[ids.index(older.id) + 1 :]:
        step = transition(store, parent, kept).origins
        found = {
            node: tuple(dict.fromkeys(o for c in came for o in found.get(c, ())))
            for node, came in step.items()
        }
        parent = kept
    return found


def begun_from(origins_of: Mapping[str, Sequence[str]], older: State) -> set[str]:
    """The nodes of `older` whose text a node begun since gives, in whole or
    in part: the blocks a split or a moved start cut a new block from."""
    held = {n.node for n in older.nodes}
    return {o for node, came in origins_of.items() if node not in held for o in came}


def _ended_causes(t: Transition, block: str) -> list[dict[str, Any]]:
    """What the run recorded for a parent's block that ended: the rule's
    Δs on it by its deleted name, never the child's lines on a block that
    took its id (ids are positions)."""
    return [
        {"delta": d["class"], "entry": d["entry"], "note": d["note"], "by": d["by"]}
        for d in t.update.get("deltas", [])
        if d["block"] == f"{DELETED}{block}"
    ]


def _causes(t: Transition, block: str) -> list[dict[str, Any]]:
    """What the run recorded for a block: its Δs, and why it was reopened,
    left unasked, moved by the rule or marked stale."""
    name = block
    found: list[dict[str, Any]] = [
        {
            "delta": d["class"],
            "entry": d["entry"],
            "note": d["note"],
            "by": d["by"],
        }
        for d in t.update.get("deltas", [])
        if d["block"] == name
    ]
    handle = t.handles.get(block, block)
    for kind in ("reopened", "not_asked"):
        found += [
            {kind: r["why"], "from": r["from"]}
            for r in t.update.get(kind, [])
            if r["handle"] == handle
        ]
    for kind in ("moved_by_rule", "stale"):
        found += [
            {kind: r["causes"]} for r in t.update.get(kind, []) if r["handle"] == handle
        ]
    return found


def history(
    store: RecordStore, name: str, last: State | None = None
) -> tuple[Step, ...]:
    """A node's history along the states up to `last` (the latest without
    it), `name` its node id or its block's id at `last`: each state it was
    in, and the one it ended at."""
    states_ = chain(store, last)
    if not states_:
        raise VersionError(f"{store.directory} keeps no state")
    node = name
    if all(s.block_of(name) is None for s in states_):
        node = states_[-1].node_of(name) or ""
        if not node:
            raise VersionError(f"no node or block {name!r} in the states")
    return histories(store, [node], last)[node]


def histories(
    store: RecordStore, nodes: Iterable[str], last: State | None = None
) -> dict[str, tuple[Step, ...]]:
    """Each node's history along the states up to `last` (the latest
    without it), as `history` gives it, each transition read once. A node
    no state holds has none."""
    states_ = chain(store, last)
    texts_by: dict[str, dict[str, str]] = {
        s.id: {n.node: n.text for n in s.nodes} for s in states_
    }
    blocks_by: dict[str, dict[str, str]] = {
        s.id: {n.node: n.block for n in s.nodes} for s in states_
    }
    found: dict[tuple[str, str], Transition] = {}

    begun: dict[tuple[str, str], dict[str, list[str]]] = {}
    with store.locked():
        held = _kept(store).lines
    place = store.directory.resolve()

    def edges_of(parent: State, kept: State) -> Mapping[str, Sequence[EdgeChange]]:
        """Each node's edges the step from `parent` to `kept` began, ended
        or changed, worked out once a process; none known where a state
        holds no lines."""
        key = (place, parent.id, kept.id)
        found = _EDGE_CHANGES.get(key, ())
        if found is None:
            before, after = held.get(parent.id), held.get(kept.id)
            found = (
                MappingProxyType({})
                if before is None or after is None
                else _edge_changes(before, after)
            )
            _EDGE_CHANGES.put(key, (), found, 1)
        return found

    def read(parent: State, kept: State) -> Transition:
        key = (parent.id, kept.id)
        if key not in found:
            found[key] = transition(store, parent, kept)
        return found[key]

    def begun_from(parent: State, kept: State) -> Mapping[str, Sequence[str]]:
        """Each node's text the nodes begun at `kept` came from, those
        nodes in the order of their origins."""
        key = (parent.id, kept.id)
        if key not in begun:
            held = blocks_by[parent.id]
            index: dict[str, list[str]] = {}
            for n, came in read(parent, kept).origins.items():
                if n not in held:
                    for o in came:
                        index.setdefault(o, []).append(n)
            begun[key] = index
        return begun[key]

    return {
        node: _history(states_, texts_by, blocks_by, node, read, begun_from, edges_of)
        for node in dict.fromkeys(nodes)
    }


def _history(
    states_: Sequence[State],
    texts_by: Mapping[str, Mapping[str, str]],
    blocks_by: Mapping[str, Mapping[str, str]],
    node: str,
    transition: Callable[[State, State], Transition],
    begun_from: Callable[[State, State], Mapping[str, Sequence[str]]],
    edges_of: Callable[[State, State], Mapping[str, Sequence[EdgeChange]]],
) -> tuple[Step, ...]:
    steps: list[Step] = []
    parent: State | None = None
    for kept in states_:
        block = blocks_by[kept.id].get(node)
        before = None if parent is None else blocks_by[parent.id].get(node)
        if block is None and before is None:
            parent = kept
            continue
        t = None if parent is None else transition(parent, kept)
        if block is None:
            assert parent is not None and before is not None and t is not None
            link = t.link_of_old.get(before) if t.across else None
            # the nodes that took in its text: a cut's own merge (a deleted
            # block's text went to none; identity's merge names its own)
            change = "recut"
            joined = tuple(n for n, came in t.origins.items() if node in came)
            if link is not None and not link.new:
                change = "deleted"
            elif link is not None and link.cls == "merged":
                change = "merged"
                joined = _nodes_of(kept, t.finals(link.new), node)
            steps.append(
                Step(
                    kept.id,
                    kept.command,
                    kept.version,
                    None,
                    None,
                    change,
                    joined,
                    tuple(_ended_causes(t, before)),
                    _carried(node, joined, edges_of(parent, kept)),
                )
            )
            break
        text = texts_by[kept.id][node]
        joined: tuple[str, ...] = ()
        if t is None:
            change = "built"
            causes: list[dict[str, Any]] = []
        elif t.across:
            assert parent is not None
            link = t.link_of_new.get(block)
            if before is None:
                # a node begun here: a split's other piece, else added
                change = (
                    "split" if link is not None and link.cls == "split" else "added"
                )
            else:
                change = "changed" if link is None else link.cls
            if link is not None and link.cls == "split":
                joined = _nodes_of(kept, t.finals(link.new), node)
            elif link is not None and link.cls == "merged":
                joined = _nodes_of(parent, link.old, node)
            else:
                # the run's own cut split or merged it
                joined = _joined_by_text(
                    t,
                    begun_from(parent, kept),
                    blocks_by[parent.id],
                    blocks_by[kept.id],
                    node,
                )
            causes = _causes(t, block)
        else:
            assert parent is not None
            change = (
                "added"
                if before is None
                else "same"
                if texts_by[parent.id][node] == text
                else "changed"
            )
            joined = _joined_by_text(
                t,
                begun_from(parent, kept),
                blocks_by[parent.id],
                blocks_by[kept.id],
                node,
            )
            causes = [
                {"person": key} for key in kept.answers if key not in parent.answers
            ]
        steps.append(
            Step(
                kept.id,
                kept.command,
                kept.version,
                block,
                text,
                change,
                joined,
                tuple(causes),
                # a node begun here has no edges before it to change, but
                # those a split carried onto it
                ()
                if parent is None or (before is None and not joined)
                else _carried(node, joined, edges_of(parent, kept)),
            )
        )
        parent = kept
    return tuple(steps)


def _joined_by_text(
    t: Transition,
    begun: Mapping[str, Sequence[str]],
    held: Container[str],
    kept_nodes: Container[str],
    node: str,
) -> tuple[str, ...]:
    """The nodes a cut joined `node` with by its text: a cut
    edit's, or across versions the run's own cut's. A node begun, those
    its text came from; a continued one, the nodes begun from its text and
    the ended ones it took in. A boundary moved between two continued
    nodes joins neither. `begun`: each node and the nodes begun from its
    text; `held` and `kept_nodes`: the parent's nodes and the child's."""
    came = t.origins.get(node, ())
    if node not in held:
        return came
    return tuple(begun.get(node, ())) + tuple(o for o in came if o not in kept_nodes)


def _nodes_of(kept: State, blocks: Sequence[str], but: str) -> tuple[str, ...]:
    """The nodes of `blocks` at `kept`, `but` left out."""
    return tuple(n for b in blocks if (n := kept.node_of(b)) and n != but)


# --- handles: a node's name across states ---------------------------------------


@dataclass(frozen=True, slots=True)
class Handles:
    """Each node's handle across a store's states (`docs/model.md` § 7):
    `kept`, the handle a node or group node was first given, held while it
    lives, and `holder`, the node holding each; `aliases`, each other name
    nodes bore on a sheet, to the nodes bearing it; `dropped`, each node's
    names another node holds as its kept handle."""

    kept: Mapping[str, str]
    holder: Mapping[str, str]
    aliases: Mapping[str, tuple[str, ...]]
    dropped: Mapping[str, tuple[str, ...]]

    @property
    def borne(self) -> frozenset[str]:
        """Every name a node bore, kept or not: none is given to another."""
        return frozenset(self.holder) | frozenset(self.aliases)

    def at(self, kept: State) -> dict[str, str]:
        """The state's block and group ids, each to its node's kept
        handle; a node no export named has none."""
        nodes = {n.block: n.node for n in kept.nodes} | dict(kept.groups)
        return {i: self.kept[n] for i, n in nodes.items() if n in self.kept}

    def aliases_of(self, node: str) -> tuple[str, ...]:
        return tuple(a for a, held in self.aliases.items() if node in held)


def handles(store: RecordStore, last: State | None = None) -> Handles:
    """The kept handles along the states from the root to `last` (the
    latest without it): each export's `handles` (the names of the sheet
    its edges answer was written on), its blocks in order, then its groups.
    A node takes as its kept handle the first name an export gives it, with
    the first free suffix (`-2`, `-3`, …) where another node bore that
    name; any other name it bears is an alias, dropped where another node
    holds it as kept."""
    kept, bearers = _named(store, chain(store, last))
    holder = {h: node for node, h in kept.items()}
    aliases: dict[str, tuple[str, ...]] = {}
    dropped: dict[str, list[str]] = {}
    for name, held in bearers.items():
        if name in holder:
            for node in held:
                if node != holder[name]:
                    dropped.setdefault(node, []).append(name)
        else:
            aliases[name] = tuple(held)
    return Handles(kept, holder, aliases, {n: tuple(d) for n, d in dropped.items()})


#: A state's handles folded along its chain, among the derived values.
NAMED = "named-"

#: Each node's kept handle and each name's bearers along a chain: what
#: `handles` folds, state by state.
_Named = tuple[dict[str, str], dict[str, dict[str, None]]]


def _named(store: RecordStore, path: Sequence[State]) -> _Named:
    """The kept handles and each name's bearers along `path`, a chain from
    the root: folded on from the latest state along it whose fold was kept
    (`derived`; a chain's fold never changes), and kept at its last."""
    begun = len(path)
    found: _Named | None = None
    while begun > 0 and found is None:
        found = derived.load(f"{NAMED}{path[begun - 1].id}")
        if found is None:
            begun -= 1
    kept, bearers = found if found is not None else ({}, {})
    for s in path[begun:]:
        outline = export_outline(store, s)
        names = outline.handles
        nodes = {n.block: n.node for n in s.nodes} | dict(s.groups)
        for ident in outline.order:
            name, node = names.get(ident), nodes.get(ident)
            if name is None or node is None:
                continue
            if node not in kept:

                def taken(n: str, node: str = node) -> bool:
                    return bool(set(bearers.get(n, ())) - {node})

                kept[node] = free_handle(name, taken)
                bearers.setdefault(kept[node], {})[node] = None
            bearers.setdefault(name, {})[node] = None
    if path and begun < len(path):
        derived.save(f"{NAMED}{path[-1].id}", (kept, bearers))
    return kept, bearers


def kept_handles(store: RecordStore) -> dict[str, str]:
    """Each node's kept handle, whichever chain holds it: the same in every
    chain that holds the state its node began at, the states before that one
    being the same."""
    found: dict[str, str] = {}
    for head in heads(states(store)):
        found |= handles(store, head).kept
    return found


def given_across(
    store: RecordStore,
    kept: State,
    old: Cut,
    new: Cut,
    links: Sequence[LineageLink] | Sequence[Any],
) -> GivenHandles:
    """`new`, a later version's cut, named by `kept`'s kept handles as its
    nodes will continue `old`'s, `kept`'s cut (`across`; a group the old
    group sharing most of its blocks, each once, as `_minted` continues
    it), past every name borne along `kept`'s chain."""
    found = handles(store, kept)
    at = found.at(kept)
    onto = across(links, [b.id for b in old.blocks], [b.id for b in new.blocks])
    by_id = {block: at[was] for block, was in onto.items() if was in at}
    shares = []
    for i, g in enumerate(new.groups):
        members = {onto[b] for b in g.blocks if b in onto}
        for j, was in enumerate(old.groups):
            shared = len(members & set(was.blocks))
            if shared:
                shares.append((-shared, i, j, g.id, was.id))
    taken: set[str] = set()
    for _, _, _, group, was in sorted(shares):
        if group in by_id or was in taken or was not in at:
            continue
        by_id[group] = at[was]
        taken.add(was)
    return GivenHandles(by_id, found.borne - set(by_id.values()))


def edge_ids(kept: State, export: Mapping[str, Any]) -> dict[str, tuple[Any, ...]]:
    """Each edge of a kept export named across states: its source's node, its kind, its
    target's node (a block's or a group's; a file or the paper's scope as itself) and
    the words of the parts and the claims it names, normalised, and its `assumed`
    flag (`exchange.named_claims_key`); nothing minted. By the edge's id in the
    export."""
    nodes = {n.block: n.node for n in kept.nodes} | dict(kept.groups)
    anchors: dict[str, str] = {
        e["id"]: " ".join(e["anchor"].split()) for e in export.get("entries", [])
    }
    found = {}
    for edge in export.get("edges", []):
        target = edge["target"]
        if "block" in target:
            named = nodes.get(target["block"], target["block"])
        elif "group" in target:
            named = nodes.get(target["group"], target["group"])
        else:
            ((kind, value),) = target.items()
            named = f"{kind}:{value}"
        parts = tuple(
            sorted(anchors.get(str(e), str(e)) for e in target.get("entries", []))
        ) + named_claims_key(
            target.get("claims", ()),
            edge.get("source_claims", ()),
            bool(edge.get("restates_none")),
            bool(edge.get("assumed")),
        )
        found[edge["id"]] = (
            nodes.get(edge["source"], edge["source"]),
            edge["kind"],
            named,
            parts,
        )
    return found


def standing_ids(kept: State, export: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Each standing of a kept export as a part to continue across states
    (`continued_parts`), in the export's order: its block's node and its
    claim's words, whitespace normalised."""
    nodes = {n.block: n.node for n in kept.nodes}
    return [
        (nodes.get(s["block"], s["block"]), " ".join(s["claim"]["text"].split()))
        for s in export.get("standings", [])
    ]


def naming(
    origins_of: Mapping[str, Sequence[str]],
    older: Iterable[str],
    newer: Iterable[str],
) -> Callable[[str], tuple[str, ...]]:
    """A later node's names in an earlier state, from `origins`: a
    continued node is itself and the ended nodes it took in; a node begun,
    the nodes its text came from; a boundary moved between two continued
    nodes names neither the other. A node `origins_of` does not hold is
    itself."""
    old, new = set(older), set(newer)

    def names(node: str) -> tuple[str, ...]:
        if node not in origins_of:
            return (node,)
        if node not in old:
            return tuple(origins_of[node])
        return (node, *(o for o in origins_of[node] if o not in new))

    return names


def shared_characters(a: str, b: str) -> int:
    """How many characters two texts share, in order (`difflib`'s matching
    blocks)."""
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    return sum(m.size for m in matcher.get_matching_blocks())


def continued_parts(
    older: Sequence[tuple[str, str]],
    newer: Sequence[tuple[str, str]],
    names: Callable[[str], Sequence[str]] = lambda node: (node,),
) -> dict[int, int]:
    """Each part of `newer` (its node and words) that continues a part of
    `older`, by index: first the same words in its own node or
    one it was begun from or took in (`names`: a split's piece, a merge's
    result); then within its own node by the most shared characters, then
    within such a node, each earlier part once, the largest share first; a
    part sharing no character continues none."""
    found: dict[int, int] = {}
    taken: set[int] = set()
    # the older parts by node, and by node and words, each in order: a
    # paper's thousands of parts are not scanned once per newer part
    by_node: dict[str, list[int]] = {}
    by_words: dict[tuple[str, str], list[int]] = {}
    for i, (old_node, old_words) in enumerate(older):
        by_node.setdefault(old_node, []).append(i)
        by_words.setdefault((old_node, old_words), []).append(i)
    for j, (node, words) in enumerate(newer):
        named = {node, *names(node)}
        i = min(
            (
                next((i for i in by_words.get((n, words), ()) if i not in taken), None)
                for n in named
            ),
            key=lambda i: len(older) if i is None else i,
        )
        if i is not None:
            found[j] = i
            taken.add(i)
    for same in (True, False):
        shares = []
        for j, (node, words) in enumerate(newer):
            if j in found:
                continue
            candidates = (
                by_node.get(node, [])
                if same
                else [i for n in set(names(node)) - {node} for i in by_node.get(n, [])]
            )
            for i in candidates:
                if i in taken:
                    continue
                shared = shared_characters(older[i][1], words)
                if shared:
                    shares.append((-shared, j, i))
        for _, j, i in sorted(shares):
            if j in found or i in taken:
                continue
            found[j] = i
            taken.add(i)
    return found
