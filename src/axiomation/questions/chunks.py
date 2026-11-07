"""Chunks: the cut's blocks in pieces a review session reads whole.

Greedy, at every level a chunk takes section
after section, in reading order, until the next would overfill `budget`
owned characters, and the next chunk goes on from there. A section too
large for a chunk on its own is chunked the same way at its subsections
(its opening text first), and so on down; text with no heading left is
chunked between blocks the same way, and a block larger than the budget
is a chunk alone. The paper before its first heading comes first, as a
section's opening text does. Chunks follow the reading order across
files. A block and every block nested in it are never parted.

Where text is chunked between blocks, each chunk shows the `overlap`
blocks on either side of the cut as read-only context; every block still
belongs to one chunk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from axiomation.assembler import owned_runs
from axiomation.cutter.blocks import BlockIndex
from axiomation.cutter.model import Block, Cut
from axiomation.reader import Document, Section

#: Owned characters a chunk holds at most: about a 500k-token window at
#: the way a session reads.
DEFAULT_CHUNK = 500_000

#: Blocks of context on either side of a cut between blocks.
DEFAULT_OVERLAP = 2

Unit = list[Block]


@dataclass(frozen=True, slots=True)
class Chunk:
    """Blocks reviewed together, in reading order, and the blocks shown
    before and after them, read-only, where a section was cut between
    blocks."""

    blocks: tuple[str, ...]
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()


@dataclass
class _Node:
    """A section: its heading's unit and the units before its first
    subsection (`lead`), then its subsections. The root has no heading."""

    level: int
    lead: list[Unit] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)

    def units(self) -> list[Unit]:
        return self.lead + [u for child in self.children for u in child.units()]


def units(cut: Cut) -> list[Unit]:
    """The blocks, each outermost one with every block nested in it."""
    found: list[Unit] = []
    inside: set[str] = set()
    for block in cut.blocks:
        if found and block.within is not None and block.within in inside:
            found[-1].append(block)
            inside.add(block.id)
        else:
            found.append([block])
            inside = {block.id}
    return found


def size(document: Document, unit: Sequence[Block]) -> int:
    return sum(len(text) for block in unit for text in owned_runs(document, block))


def sections_held(document: Document, cut: Cut) -> dict[str, Section]:
    """Each block that holds a section's heading, and the section."""
    index = BlockIndex(cut.blocks)
    held: dict[str, Section] = {}
    for section in document.sections:
        holder = index.owner(section.heading.file, section.heading.start)
        if holder is not None:
            held.setdefault(holder, section)
    return held


def _tree(document: Document, cut: Cut) -> _Node:
    held = sections_held(document, cut)
    root = _Node(level=-1)
    path = [root]
    for unit in units(cut):
        section = held.get(unit[0].id)
        if section is not None:
            while path[-1] is not root and path[-1].level >= section.level:
                path.pop()
            node = _Node(level=section.level)
            path[-1].children.append(node)
            path.append(node)
        # Text after a subsection, before the next heading, is the
        # subsection's: nothing in the source ends it sooner.
        path[-1].lead.append(unit)
    return root


def chunks(
    document: Document,
    cut: Cut,
    budget: int | None,
    overlap: int = DEFAULT_OVERLAP,
) -> tuple[Chunk, ...]:
    """The cut's blocks in chunks (above). `None` gives the whole paper as
    one chunk; `0`, one block (with its nested blocks) per chunk."""
    every = units(cut)
    if not every:
        return ()
    if budget is None:
        pieces = [_Piece(every)]
    else:
        pieces = _section(document, _tree(document, cut), budget)
    found = []
    for n, piece in enumerate(pieces):
        before = pieces[n - 1].units[-overlap:] if piece.cut_before and overlap else []
        after = (
            pieces[n + 1].units[:overlap]
            if n + 1 < len(pieces) and pieces[n + 1].cut_before and overlap
            else []
        )
        found.append(
            Chunk(
                blocks=_ids(piece.units),
                before=_ids(before),
                after=_ids(after),
            )
        )
    return tuple(found)


@dataclass
class _Piece:
    """A chunk's units; `cut_before` when it continues a section cut
    between blocks, so the piece before it is its context and it that
    piece's."""

    units: list[Unit]
    cut_before: bool = False


def _ids(found: Sequence[Unit]) -> tuple[str, ...]:
    return tuple(block.id for unit in found for block in unit)


def _size(document: Document, found: Sequence[Unit]) -> int:
    return sum(size(document, unit) for unit in found)


def _section(document: Document, node: _Node, budget: int) -> list[_Piece]:
    """A section (the root: the paper) as one chunk, or chunked greedily at
    its subsections, or, with none, between blocks."""
    found = node.units()
    if not found:
        return []
    if _size(document, found) <= budget:
        return [_Piece(found)]
    if not node.children:
        return _between_blocks(document, found, budget)
    pieces: list[_Piece] = []
    current: list[Unit] = []
    for part in [_Node(level=node.level, lead=node.lead), *node.children]:
        units_of = part.units()
        amount = _size(document, units_of)
        if current and (amount > budget or _size(document, current) + amount > budget):
            pieces.append(_Piece(current))
            current = []
        if amount > budget:
            pieces += _section(document, part, budget)
        else:
            current += units_of
    if current:
        pieces.append(_Piece(current))
    return pieces


def _between_blocks(document: Document, found: list[Unit], budget: int) -> list[_Piece]:
    """Blocks until the next would overfill the chunk; each chunk after
    the first continues the cut, so it and the one before are each
    other's context."""
    pieces: list[_Piece] = []
    current: list[Unit] = []
    for unit in found:
        if current and _size(document, current) + size(document, unit) > budget:
            pieces.append(_Piece(current, cut_before=bool(pieces)))
            current = []
        current.append(unit)
    if current:
        pieces.append(_Piece(current, cut_before=bool(pieces)))
    return pieces
