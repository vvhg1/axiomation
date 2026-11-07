"""The assembler: the cutter's cut, corrected by the records.

It applies cut corrections only: each correction, in
order, to the cut the corrections before it left; one that cannot apply is
a `Problem` and leaves the cut as it was. A block a split, merge or
rebound makes or changes takes a fresh hash and loses the cutter's doubts;
the candidates are re-derived over the corrected blocks; parts are ordered
by their block and start, groups by their first block, the longer first.

Deterministic, and no model: it imports only the standard library, the
reader, the cutter and the records (`tests/test_spine.py`).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from axiomation.cutter.blocks import BlockIndex, block_id
from axiomation.cutter.candidates import edge_candidates
from axiomation.cutter.continuation import within
from axiomation.cutter.groups import label_holders
from axiomation.cutter.hashing import block_hash
from axiomation.cutter.model import (
    Block,
    Cut,
    Doubt,
    Form,
    GivenHandles,
    Group,
    GroupKind,
    MarkedPart,
    Naming,
    PartKind,
    Role,
    Segment,
    role_set,
    segments_of,
)
from axiomation.cutter.text import FileView, views
from axiomation.reader import Document, Span
from axiomation.records import BLOCK_OPS, NONE, CutCorrection, CutOp

#: The group kinds whose role is justification; a step or a run of steps
#: carries none.
JUSTIFYING = frozenset({GroupKind.PROOF, GroupKind.PROOF_SECTION, GroupKind.DERIVATION})


@dataclass(frozen=True, slots=True)
class Problem:
    """A correction that could not apply, and why."""

    correction: str
    message: str


@dataclass(frozen=True, slots=True)
class Corrected:
    """The corrected cut, the ids of the corrections applied, in order, and
    the problems of those that were not."""

    cut: Cut
    applied: tuple[str, ...]
    problems: tuple[Problem, ...]


def owned_runs(document: Document, block: Block) -> tuple[str, ...]:
    """The text of each run a block owns, in order."""
    return tuple(document.text(run) for run in block.owned)


def corrected_cut(
    document: Document, cut: Cut, corrections: Sequence[CutCorrection]
) -> Corrected:
    """Each correction applied, in order, to the cut the ones before it
    left; one that cannot apply is a problem and changes nothing."""
    files = views(document)
    work = _from_cut(cut)
    applied: list[str] = []
    problems: list[Problem] = []
    for correction in corrections:
        trial = copy.deepcopy(work)
        try:
            shape = correction.check()
            if shape:
                raise _Refused("; ".join(shape))
            anchors = _anchors(trial) if correction.op in BLOCK_OPS else []
            _APPLY[correction.op](trial, correction, files)
            if anchors:
                _regroup(trial, anchors, f"{correction.op.value} {correction.id}")
        except _Refused as refused:
            problems.append(Problem(correction.id, str(refused)))
            continue
        work = trial
        applied.append(correction.id)
    return Corrected(
        _to_cut(document, cut, work, files), tuple(applied), tuple(problems)
    )


# --- the cut, mutable, in offsets --------------------------------------------


@dataclass
class _Block:
    id: str
    file: str
    extent: tuple[int, int]
    owned: list[tuple[int, int]]
    within: str | None
    form: Form
    environment: str | None
    meaning: str | None
    roles: tuple[Role, ...]
    segments: tuple[Segment, ...]
    hash: str
    doubts: tuple[Doubt, ...]
    #: made or changed by a correction: its hash is taken again and its
    #: doubts, the cutter's about a boundary now corrected, go
    changed: bool = False

    def first(self) -> int:
        return self.owned[0][0]

    def last(self) -> int:
        return self.owned[-1][1] - 1

    def owns(self, offset: int) -> bool:
        return any(start <= offset < end for start, end in self.owned)


@dataclass
class _Part:
    block: str
    kind: PartKind
    marker: str | None
    ordinal: int | None
    label: str | None
    file: str
    start: int
    end: int


@dataclass
class _Group:
    id: str
    kind: GroupKind
    blocks: list[str]
    names: str | None
    named_label: str | None
    named_by: Naming | None
    role: Role | None


@dataclass
class _Cut:
    blocks: list[_Block]
    parts: list[_Part]
    groups: list[_Group]

    def index(self, block_id: str) -> int:
        for i, block in enumerate(self.blocks):
            if block.id == block_id:
                return i
        raise KeyError(block_id)

    def block(self, block_id: str) -> _Block:
        return self.blocks[self.index(block_id)]

    def owner(self, file: str, offset: int) -> _Block | None:
        for block in self.blocks:
            if block.file == file and block.owns(offset):
                return block
        return None

    def group(self, group_id: str) -> _Group:
        for group in self.groups:
            if group.id == group_id:
                return group
        raise KeyError(group_id)


class _Refused(Exception):
    """An operation that cannot apply; the cut is left as it was."""


def _from_cut(cut: Cut) -> _Cut:
    return _Cut(
        blocks=[
            _Block(
                id=b.id,
                file=b.extent.file,
                extent=(b.extent.start, b.extent.end),
                owned=[(run.start, run.end) for run in b.owned],
                within=b.within,
                form=b.form,
                environment=b.environment,
                meaning=b.meaning,
                roles=b.roles,
                segments=b.segments,
                hash=b.hash,
                doubts=b.doubts,
            )
            for b in cut.blocks
        ],
        parts=[
            _Part(
                p.block,
                p.kind,
                p.marker,
                p.ordinal,
                p.label,
                p.at.file,
                p.at.start,
                p.at.end,
            )
            for p in cut.parts
        ],  # fmt: skip
        groups=[
            _Group(
                g.id, g.kind, list(g.blocks), g.names, g.named_label, g.named_by, g.role
            )
            for g in cut.groups
        ],
    )


def _carried_handles(
    original: Cut, blocks: Sequence[Block], groups: Sequence[_Group]
) -> GivenHandles | None:
    """`original`'s given handles on the corrected cut, by the rule a node
    follows (`docs/model.md` § 7): a block continuing one of its blocks
    (`continuation.within`) keeps that block's handle, and a group the
    handle of the group sharing most of its blocks, each once. A block or
    group begun here is minted on the sheet, past the reserved and every
    handle `original` gave that this cut does not keep."""
    given = original.handles
    if given is None:
        return None
    onto = within(_runs(original.blocks), _runs(blocks))
    by_id = {new: given.by_id[old] for new, old in onto.items() if old in given.by_id}
    shares = []
    for i, g in enumerate(groups):
        members = {onto[b] for b in g.blocks if b in onto}
        for j, was in enumerate(original.groups):
            shared = len(members & set(was.blocks))
            if shared and was.id in given.by_id:
                shares.append((-shared, i, j, g.id, was.id))
    taken: set[str] = set()
    for _, _, _, group, was in sorted(shares):
        if group not in by_id and was not in taken:
            by_id[group] = given.by_id[was]
            taken.add(was)
    dropped = set(given.by_id.values()) - set(by_id.values())
    return GivenHandles(by_id, given.reserved | dropped)


def _runs(blocks: Sequence[Block]) -> dict[str, list[tuple[str, int, int]]]:
    return {b.id: [(r.file, r.start, r.end) for r in b.owned] for b in blocks}


def _to_cut(
    document: Document, original: Cut, work: _Cut, files: dict[str, FileView]
) -> Cut:
    blocks = []
    for b in work.blocks:
        owned = tuple(Span(b.file, s, e) for s, e in b.owned)
        text = files[b.file].text
        blocks.append(
            Block(
                id=b.id,
                extent=Span(b.file, *b.extent),
                owned=owned,
                within=b.within,
                form=b.form,
                environment=b.environment,
                meaning=b.meaning,
                roles=b.roles,
                segments=b.segments,
                hash=block_hash(text[s:e] for s, e in b.owned) if b.changed else b.hash,
                doubts=() if b.changed else b.doubts,
            )
        )
    order = {block.id: i for i, block in enumerate(work.blocks)}
    parts = sorted(work.parts, key=lambda p: (order[p.block], p.start))
    groups = sorted(
        work.groups, key=lambda g: (order[g.blocks[0]], -len(g.blocks), g.id)
    )
    index = BlockIndex(blocks)
    return Cut(
        rules=original.rules,
        blocks=tuple(blocks),
        parts=tuple(
            MarkedPart(
                p.block,
                p.kind,
                p.marker,
                p.ordinal,
                p.label,
                Span(p.file, p.start, p.end),
            )
            for p in parts
        ),
        groups=tuple(
            Group(
                g.id,
                g.kind,
                tuple(g.blocks),
                g.names,
                g.named_label,
                g.named_by,
                g.role,
            )
            for g in groups
        ),
        candidates=edge_candidates(document, index, label_holders(document, index)),
        handles=_carried_handles(original, blocks, work.groups),
    )


# --- finding an excerpt --------------------------------------------------------


def occurrences(text: str, excerpt: str) -> list[tuple[int, int]]:
    """The spans of `text` where `excerpt` matches, each run of whitespace in
    either counting as one space; overlapping matches included (identity's
    rebase places starts by it)."""
    words = excerpt.split()
    if not words:
        return []
    pattern = re.compile("(?=(" + r"\s+".join(re.escape(w) for w in words) + "))")
    return [(m.start(1), m.end(1)) for m in pattern.finditer(text)]


def _find(
    excerpt: str, blocks: list[_Block], files: dict[str, FileView], what: str
) -> tuple[int, int]:
    """The one match of `excerpt` that starts on a character `blocks` own,
    searched over their joint extent."""
    chars = files[blocks[0].file].text
    start = min(b.extent[0] for b in blocks)
    end = max(b.extent[1] for b in blocks)
    matches = [
        (start + s, start + e)
        for s, e in occurrences(chars[start:end], excerpt)
        if any(b.owns(start + s) for b in blocks)
    ]
    names = " and ".join(b.id for b in blocks)
    if len(matches) != 1:
        raise _Refused(
            f"{what} occurs {len(matches)} times in the owned text of {names}: "
            f"{excerpt!r}"
        )
    return matches[0]


# --- block arithmetic ----------------------------------------------------------


def _trimmed(chars: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and chars[start].isspace():
        start += 1
    while end > start and chars[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _runs_between(
    runs: list[tuple[int, int]], start: int, end: int, chars: str
) -> list[tuple[int, int]]:
    """The parts of `runs` inside `[start, end)`, each trimmed."""
    found = []
    for run_start, run_end in runs:
        piece = _trimmed(chars, max(run_start, start), min(run_end, end))
        if piece is not None:
            found.append(piece)
    return found


def _extent_end(chars: str, start: int, end: int) -> int:
    """`end` pulled back over trailing whitespace, never before `start`."""
    while end > start and chars[end - 1].isspace():
        end -= 1
    return end


def _consecutive(cut: _Cut, ids: tuple[str, ...]) -> list[_Block]:
    try:
        indices = [cut.index(block_id) for block_id in ids]
    except KeyError as error:
        raise _Refused(f"block {error.args[0]} does not exist") from None
    blocks = [cut.blocks[i] for i in indices]
    if len({b.file for b in blocks}) != 1:
        raise _Refused(f"blocks {', '.join(ids)} are in two files")
    if len({b.within for b in blocks}) != 1:
        raise _Refused(f"blocks {', '.join(ids)} are at two nesting levels")
    inside = set(ids)
    for a, b in zip(indices, indices[1:], strict=False):
        if b <= a:
            raise _Refused(f"blocks {', '.join(ids)} are not in reading order")
        between = [x for x in cut.blocks[a + 1 : b] if x.within not in inside]
        if between:
            raise _Refused(
                f"blocks {cut.blocks[a].id} and {cut.blocks[b].id} are not "
                f"adjacent: {between[0].id} lies between"
            )
    return blocks


def _reorder(cut: _Cut, lo: int, hi: int) -> None:
    """Blocks `lo` to `hi` (exclusive), one file's, back in reading order."""
    cut.blocks[lo:hi] = sorted(cut.blocks[lo:hi], key=lambda b: b.extent[0])


def _span_of_region(cut: _Cut, index: int) -> int:
    """The index after block `index` and every block nested in it."""
    inside = {cut.blocks[index].id}
    end = index + 1
    while end < len(cut.blocks) and cut.blocks[end].within in inside:
        inside.add(cut.blocks[end].id)
        end += 1
    return end


def _rehome(cut: _Cut, old: set[str], pieces: list[_Block]) -> None:
    """Nested blocks and parts of the blocks in `old` follow the piece whose
    extent holds their start; a part running past that piece ends where
    the piece's owned text does, since a part lies inside its block. A part's start is
    an owned character (`_find`, the cutter's marker), so it lies before that end."""

    def piece_at(offset: int) -> _Block:
        chosen = pieces[0]
        for piece in pieces:
            if piece.extent[0] <= offset:
                chosen = piece
        return chosen

    for block in cut.blocks:
        if block.within in old:
            block.within = piece_at(block.extent[0]).id
    for part in cut.parts:
        if part.block in old:
            piece = piece_at(part.start)
            part.block = piece.id
            if part.end > piece.extent[1]:
                part.end = max(part.start + 1, piece.owned[-1][1])


# --- groups across a block operation -------------------------------------------


@dataclass(frozen=True)
class _Anchor:
    """A group held by its first and last owned characters, and the block it
    names by that block's first character."""

    group: _Group
    first: tuple[str, int]
    last: tuple[str, int]
    names: tuple[str, int] | None


def _anchors(cut: _Cut) -> list[_Anchor]:
    anchors = []
    for group in cut.groups:
        first, last = cut.block(group.blocks[0]), cut.block(group.blocks[-1])
        names = None
        if group.names is not None:
            named = cut.block(group.names)
            names = (named.file, named.first())
        anchors.append(
            _Anchor(group, (first.file, first.first()), (last.file, last.last()), names)
        )
    return anchors


def _regroup(cut: _Cut, anchors: list[_Anchor], op: str) -> None:
    for anchor in anchors:
        group = anchor.group
        first = cut.owner(*anchor.first)
        last = cut.owner(*anchor.last)
        if first is None or last is None:
            raise _Refused(f"{op} leaves group {group.id} without its first or last")
        if first.first() != anchor.first[1] or last.last() != anchor.last[1]:
            partly = first if first.first() != anchor.first[1] else last
            raise _Refused(
                f"{op} leaves block {partly.id} partly inside group {group.id}; "
                "ungroup it first and group it again after"
            )
        i, j = cut.index(first.id), cut.index(last.id)
        group.blocks = [b.id for b in cut.blocks[i : j + 1]]
        if anchor.names is not None:
            named = cut.owner(*anchor.names)
            if named is None or named.first() != anchor.names[1]:
                raise _Refused(
                    f"{op} moves the start of the block group {group.id} names; "
                    "attach it again after"
                )
            group.names = named.id


# --- the operations ------------------------------------------------------------


def _block(cut: _Cut, block_id: str | None) -> _Block:
    try:
        return cut.block(block_id or "")
    except KeyError:
        raise _Refused(f"block {block_id} does not exist") from None


def _split(cut: _Cut, c: CutCorrection, files: dict[str, FileView]) -> None:
    block = _block(cut, c.block)
    view = files[block.file]
    chars = view.text
    cuts = sorted({_find(excerpt, [block], files, "cut point")[0] for excerpt in c.at})
    if len(cuts) != len(c.at):
        raise _Refused("two cut points fall on one character")
    if cuts[0] <= block.first():
        raise _Refused(f"a cut point is the start of {block.id}: nothing to split")
    bounds = [block.extent[0], *cuts, block.extent[1]]
    pieces = []
    for k in range(len(bounds) - 1):
        start, end = bounds[k], bounds[k + 1]
        runs = _runs_between(block.owned, start, end, chars)
        if not runs:
            raise _Refused(f"a piece of {block.id} would own nothing")
        last_piece = k == len(bounds) - 2
        pieces.append(
            _Block(
                id=block.id if k == 0 else block_id(view, runs[0][0]),
                file=block.file,
                extent=(start, end if last_piece else _extent_end(chars, start, end)),
                owned=runs,
                within=block.within,
                form=block.form,
                environment=block.environment,
                meaning=block.meaning,
                roles=block.roles,
                # A single-role piece is one segment, the whole piece; a
                # mixed block's partition is re-placed by the roles pass.
                segments=segments_of(block.roles),
                hash=block.hash,
                doubts=block.doubts,
                changed=True,
            )
        )
    index = cut.index(block.id)
    end = _span_of_region(cut, index)
    cut.blocks[index : index + 1] = pieces
    _rehome(cut, {block.id}, pieces)
    _reorder(cut, index, end + len(pieces) - 1)


def _joined(runs: list[tuple[int, int]], chars: str) -> list[tuple[int, int]]:
    """Runs with only whitespace between them as one run: a block owns its
    extent less its nested blocks and hidden text (`Block`), so the pieces
    of a merge that nothing separates are not apart."""
    joined: list[tuple[int, int]] = []
    for start, end in runs:
        if joined and not chars[joined[-1][1] : start].strip():
            joined[-1] = (joined[-1][0], end)
        else:
            joined.append((start, end))
    return joined


def _merge(cut: _Cut, c: CutCorrection, files: dict[str, FileView]) -> None:
    blocks = _consecutive(cut, c.blocks)
    head = blocks[0]
    chars = files[head.file].text
    # A block with no letter or digit (a stray `}`) carries no form or role
    # into the merge.
    basis = [
        b
        for b in blocks
        if any(chars[i].isalnum() for s, e in b.owned for i in range(s, e))
    ] or blocks

    def agreed[T](values: list[T], otherwise: T) -> T:
        return values[0] if len(set(values)) == 1 else otherwise

    # The union where every piece has its roles: a merge of a claim and a
    # definition is a mixed block, unsegmented until the roles pass places
    # its boundary. A piece without roles leaves them unknown.
    roles = (
        role_set(role for b in basis for role in b.roles)
        if all(b.roles for b in basis)
        else ()
    )
    merged = _Block(
        id=head.id,
        file=head.file,
        extent=(head.extent[0], blocks[-1].extent[1]),
        owned=_joined([run for b in blocks for run in b.owned], chars),
        within=head.within,
        form=agreed([b.form for b in basis], Form.PROSE),
        environment=agreed([b.environment for b in basis], None),
        meaning=agreed([b.meaning for b in basis], None),
        roles=roles,
        segments=segments_of(roles),
        hash=head.hash,
        doubts=head.doubts,
        changed=True,
    )
    index = cut.index(head.id)
    last = _span_of_region(cut, cut.index(blocks[-1].id))
    gone = {b.id for b in blocks}
    for block in cut.blocks:
        if block.within in gone:
            block.within = head.id
    for part in cut.parts:
        if part.block in gone:
            part.block = head.id
    kept = [b for b in cut.blocks[index:last] if b.id not in gone]
    cut.blocks[index:last] = [merged, *kept]
    _reorder(cut, index, index + 1 + len(kept))


def _rebound(cut: _Cut, c: CutCorrection, files: dict[str, FileView]) -> None:
    a, b = _consecutive(cut, c.blocks)
    view = files[a.file]
    chars = view.text
    at = _find(c.at[0], [a, b], files, "boundary")[0]
    if at == b.first():
        raise _Refused(f"{b.id} already begins there: nothing to rebound")
    if at <= a.first():
        raise _Refused(f"the boundary is the start of {a.id}: {a.id} would own nothing")
    runs = a.owned + b.owned
    head = _runs_between(runs, a.extent[0], at, chars)
    tail = _runs_between(runs, at, b.extent[1], chars)
    new_a = copy.copy(a)
    new_a.extent = (a.extent[0], _extent_end(chars, a.extent[0], at))
    new_a.owned = head
    new_a.changed = True
    new_b = copy.copy(b)
    new_b.id = block_id(view, tail[0][0])
    new_b.extent = (at, b.extent[1])
    new_b.owned = tail
    new_b.changed = True
    ia = cut.index(a.id)
    end = _span_of_region(cut, cut.index(b.id))
    region = [x for x in cut.blocks[ia:end] if x.id not in (a.id, b.id)]
    cut.blocks[ia:end] = [new_a, new_b, *region]
    _rehome(cut, {a.id, b.id}, [new_a, new_b])
    _reorder(cut, ia, end)


def _named(cut: _Cut, names: str | None) -> str | None:
    if names in (None, NONE):
        return None
    return _block(cut, names).id


def _find_group(cut: _Cut, group_id: str | None) -> _Group:
    try:
        return cut.group(group_id or "")
    except KeyError:
        raise _Refused(f"group {group_id} does not exist") from None


def _group(cut: _Cut, c: CutCorrection, files: dict[str, FileView]) -> None:
    try:
        i, j = cut.index(c.first or ""), cut.index(c.last or "")
    except KeyError as error:
        raise _Refused(f"block {error.args[0]} does not exist") from None
    if j < i:
        raise _Refused(f"{c.last} comes before {c.first}")
    names = _named(cut, c.names)
    kind = GroupKind(c.kind)
    group_id = f"{kind.value}@{c.first}"
    if any(g.id == group_id for g in cut.groups):
        raise _Refused(f"group {group_id} exists already")
    cut.groups.append(
        _Group(
            id=group_id,
            kind=kind,
            blocks=[b.id for b in cut.blocks[i : j + 1]],
            names=names,
            named_label=None,
            named_by=None,
            role=Role.JUSTIFICATION if kind in JUSTIFYING else None,
        )
    )


def _ungroup(cut: _Cut, c: CutCorrection, files: dict[str, FileView]) -> None:
    cut.groups.remove(_find_group(cut, c.group))


def _attach(cut: _Cut, c: CutCorrection, files: dict[str, FileView]) -> None:
    group = _find_group(cut, c.group)
    group.names = _named(cut, c.names)
    group.named_label = None
    group.named_by = None


def _part(cut: _Cut, c: CutCorrection, files: dict[str, FileView]) -> None:
    block = _block(cut, c.block)
    start, end = _find(c.at[0], [block], files, "part")
    cut.parts.append(
        _Part(block.id, PartKind(c.kind), c.marker, None, None, block.file, start, end)
    )


def _unpart(cut: _Cut, c: CutCorrection, files: dict[str, FileView]) -> None:
    found = [p for p in cut.parts if p.block == c.block and p.marker == c.marker]
    if len(found) != 1:
        raise _Refused(
            f"{len(found)} parts of block {c.block} carry marker {c.marker!r}"
        )
    cut.parts.remove(found[0])


def _roles(cut: _Cut, c: CutCorrection, files: dict[str, FileView]) -> None:
    """The roles pass's answer: the block's roles and their partition. Each
    boundary after the first must be a verbatim excerpt found once in the
    block's owned text; `check` has already held the shape."""
    block = _block(cut, c.block)
    for segment in c.segments[1:]:
        _find(segment.starts, [block], files, "segment start")
    block.roles = c.roles
    block.segments = c.segments


_APPLY: dict[CutOp, Callable[[_Cut, CutCorrection, dict[str, FileView]], None]] = {
    CutOp.SPLIT: _split,
    CutOp.MERGE: _merge,
    CutOp.REBOUND: _rebound,
    CutOp.GROUP: _group,
    CutOp.UNGROUP: _ungroup,
    CutOp.ATTACH: _attach,
    CutOp.PART: _part,
    CutOp.UNPART: _unpart,
    CutOp.ROLES: _roles,
}
