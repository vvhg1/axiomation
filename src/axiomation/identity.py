"""Identity across two versions of a paper.

Which block of NEW is which block of OLD, decided mechanically and never
asked, by the first tier that decides, surest and cheapest first:

- **tier 0, the text map:** per file, difflib's line opcodes between the
  two texts. A block of OLD whose owned characters all lie in equal regions
  is the NEW block that owns exactly the mapped characters;
- **tier 1, a label** the block owns in both versions;
- **tier 2, an equal hash anywhere** in NEW not paired yet;
- **tier 3, an equal key** (`correspondence.keyed`: form, words outside
  mathematics, labels) in the same gap;
- **tier 4, in each gap:** a split or a merge by joined words, then one to
  one by word ratio ≥ `THETA` with a margin of `DELTA_PAIR` on both sides;
  then a gap of one block on each side, of equal form, whatever its ratio;
- **else** added or deleted.

A gap is the stretch between two blocks the text map paired. Two
candidates that tie are never guessed between: neither is paired. The
lineage is the pairing with each link's class and tier; positional ids
and sheet handles are not identity.
"""

from __future__ import annotations

import itertools
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from enum import StrEnum

from axiomation.assembler import occurrences
from axiomation.cutter.blocks import BlockIndex, block_id
from axiomation.cutter.candidates import edge_candidates
from axiomation.cutter.correspondence import Keyed, keyed
from axiomation.cutter.correspondence import words as words_of
from axiomation.cutter.groups import label_holders
from axiomation.cutter.hashing import block_hash
from axiomation.cutter.model import (
    ROLE_OF_MEANING,
    Block,
    Cut,
    Form,
    Group,
    MarkedPart,
    Naming,
    PartKind,
    Segment,
)
from axiomation.cutter.text import FileView, views
from axiomation.reader import (
    Document,
    Environment,
    EnvKind,
    ReadingOrder,
    Span,
    document_hash,
)
from axiomation.reader.declarations import reference_table
from axiomation.reader.environments import static_kind
from axiomation.records import CutOp

#: A one-to-one pair in a gap needs at least this word ratio (git's rename
#: detection.
THETA = 0.5
#: ... and must beat every other candidate of either block by this much.
DELTA_PAIR = 0.05
#: A fuzzy hint's largest normalised edit distance, and its margin over the
#: next match.
TAU = 0.25
DELTA_HINT = 0.10
#: Names the tiers and the thresholds: a change makes new keys.
IDENTITY_VERSION = (
    "identity-10 tiers=text-map(lines,words,breaks),label,hash,key,words,ratio,"
    "form(open headings) classes=meaning,conjecture"
    " restore=exact-words,anchored-words"
    " heir=label,start,words"
    " rebase-roles=mapped,lineage rebase-groups=one-to-one words=no-registers"
    f" theta={THETA} delta_pair={DELTA_PAIR} tau={TAU} delta_hint={DELTA_HINT}"
)


class BlockClass(StrEnum):
    """A block's text class."""

    SAME = "same"
    #: Hash equal, in another gap.
    MOVED = "moved"
    #: Equal words and labels, the mathematics differs.
    MATH = "math"
    #: Equal words, labels and mathematics; only the markup differs.
    MARKUP = "markup"
    #: Hash equal, but a macro it uses changed.
    MACRO = "macro"
    CHANGED = "changed"
    SPLIT = "split"
    MERGED = "merged"
    ADDED = "added"
    DELETED = "deleted"


class Tier(StrEnum):
    """What decided a link."""

    TEXT_MAP = "text-map"
    LABEL = "label"
    HASH = "hash"
    KEY = "key"
    #: A split or a merge, by joined words.
    WORDS = "words"
    RATIO = "ratio"
    FORM = "form"
    #: Added or deleted: nothing decided.
    NONE = "none"
    #: Two lineages composed (`Lineage.compose`).
    COMPOSED = "composed"


@dataclass(frozen=True, slots=True)
class Version:
    """One version of a paper: its document and the cut identity pairs."""

    document: Document
    cut: Cut


@dataclass(frozen=True, slots=True)
class Link:
    """OLD's blocks `old` became NEW's `new`. A split or a merge names its
    `heir`, the block on the many side that continues the one: the one
    holding its labels, else the one with most of its words.
    `moved` marks a one-to-one pair outside the
    longest run of pairs that keep their order, whatever its class: a moved
    block whose maths, words or macros also changed keeps its move, which
    its `[position]` edges are asked by."""

    old: tuple[str, ...]
    new: tuple[str, ...]
    cls: BlockClass
    tier: Tier
    heir: str | None = None
    moved: bool = False


@dataclass(frozen=True, slots=True)
class Lineage:
    """Every block of OLD and of NEW in exactly one link."""

    links: tuple[Link, ...]

    def new_of(self, old: str) -> tuple[str, ...]:
        return next((link.new for link in self.links if old in link.old), ())

    def old_of(self, new: str) -> tuple[str, ...]:
        return next((link.old for link in self.links if new in link.new), ())

    def link_of_old(self, old: str) -> Link | None:
        return next((link for link in self.links if old in link.old), None)

    def counts(self) -> dict[str, int]:
        """Blocks of OLD per class, and NEW's added blocks."""
        found: Counter[BlockClass] = Counter()
        for link in self.links:
            found[link.cls] += (
                len(link.new) if link.cls is BlockClass.ADDED else len(link.old)
            )
        return {cls.value: found.get(cls, 0) for cls in BlockClass}

    def compose(self, then: Lineage) -> Lineage:
        """v1→v3 from v1→v2 (`self`) and v2→v3 (`then`), over v2's ids;
        nothing is re-paired from v1. Blocks linked
        through v2 form one link; its class is its shape's (split, merged,
        added, deleted), else changed if a block split, merged, was added or
        deleted on the way, else the strongest of its parts'. It is moved
        when one of its parts was (a move and its return: moved). A split's
        or a merge's heir is the one the heirs lead to through v2, when it
        is on the many side."""
        # union-find over ("old", id), ("mid", id), ("new", id)
        parent: dict[tuple[str, str], tuple[str, str]] = {}

        def find(x: tuple[str, str]) -> tuple[str, str]:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def join(a: tuple[str, str], b: tuple[str, str]) -> None:
            parent[find(a)] = find(b)

        classes: dict[tuple[str, str], list[BlockClass]] = defaultdict(list)
        moved: set[tuple[str, str]] = set()
        nodes: list[tuple[str, str]] = []
        for side, lineage in (("first", self), ("then", then)):
            first_side, last_side = (
                ("old", "mid") if side == "first" else ("mid", "new")
            )
            for link in lineage.links:
                ends = [(first_side, i) for i in link.old] + [
                    (last_side, i) for i in link.new
                ]
                for end in ends:
                    find(end)
                    if end not in nodes:
                        nodes.append(end)
                for end in ends[1:]:
                    join(ends[0], end)
                classes[find(ends[0])].append(link.cls)
                if link.moved:
                    moved.add(find(ends[0]))
        members: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for node in nodes:
            members.setdefault(find(node), []).append(node)
        held: dict[tuple[str, str], list[BlockClass]] = defaultdict(list)
        for root, found in classes.items():
            held[find(root)] += found
        moved = {find(root) for root in moved}
        links = []
        for root, found in members.items():
            old = tuple(i for side, i in found if side == "old")
            new = tuple(i for side, i in found if side == "new")
            if not old and not new:
                continue  # a block added in v2 and deleted again
            cls = _composed_class(old, new, held[root])
            heir = None
            if cls is BlockClass.SPLIT:
                found = _ahead(then, _ahead(self, old[0]))
                heir = found if found in new else None
            elif cls is BlockClass.MERGED:
                found = _behind(self, _behind(then, new[0]))
                heir = found if found in old else None
            links.append(
                Link(
                    old,
                    new,
                    cls,
                    Tier.COMPOSED,
                    heir,
                    moved=root in moved and len(old) == len(new) == 1,
                )
            )
        return Lineage(tuple(links))


def _ahead(lineage: Lineage, block: str | None) -> str | None:
    """The block of the later version `block` continues as: a split's
    heir, else the link's first new block."""
    link = None if block is None else lineage.link_of_old(block)
    if link is None or not link.new:
        return None
    return link.heir if link.heir in link.new else link.new[0]


def _behind(lineage: Lineage, block: str | None) -> str | None:
    """The block of the earlier version that `block` continues: a merge's
    heir, else the link's first old block."""
    link = next((x for x in lineage.links if block in x.new), None)
    if link is None or not link.old:
        return None
    return link.heir if link.heir in link.old else link.old[0]


#: The meaning of a statement whose claim stands `conjectured`: a block
#: moved to it or from it is changed, whatever its role (`_Pairing._cls`).
CONJECTURE = "conjecture"


#: The classes a composed link takes the strongest of.
_STRENGTH = (
    BlockClass.SAME,
    BlockClass.MOVED,
    BlockClass.MARKUP,
    BlockClass.MACRO,
    BlockClass.MATH,
    BlockClass.CHANGED,
)


def _composed_class(
    old: tuple[str, ...], new: tuple[str, ...], parts: list[BlockClass]
) -> BlockClass:
    if not old:
        return BlockClass.ADDED
    if not new:
        return BlockClass.DELETED
    if len(old) == 1 and len(new) > 1:
        return BlockClass.SPLIT
    if len(old) > 1 and len(new) == 1:
        return BlockClass.MERGED
    if len(old) > 1:
        return BlockClass.CHANGED
    lost = (BlockClass.SPLIT, BlockClass.MERGED, BlockClass.ADDED, BlockClass.DELETED)
    if any(c in lost for c in parts):
        return BlockClass.CHANGED
    known = [c for c in parts if c in _STRENGTH]
    return max(known, key=_STRENGTH.index) if known else BlockClass.CHANGED


def diff_summary(old: Version, new: Version, lineage: Lineage) -> dict[str, object]:
    """What `axiomation diff` prints with `--json` (text only): both versions'
    hashes, the changed macros and map entries, the class counts and every
    link."""
    return {
        "identity": IDENTITY_VERSION,
        "old": document_hash(old.document),
        "new": document_hash(new.document),
        "macros": sorted(changed_macros(old.document, new.document)),
        # only where the maps differ: a diff of two unmapped versions
        # holds no map field
        **vocabulary_moved(old.document, new.document),
        "counts": lineage.counts(),
        "links": [
            {
                "old": list(link.old),
                "new": list(link.new),
                "cls": link.cls.value,
                "tier": link.tier.value,
                **({"heir": link.heir} if link.heir else {}),
                **({"moved": True} if link.moved else {}),
            }
            for link in lineage.links
        ],
    }


# --- excerpts -----------------------------------------------------


class ExcerptClass(StrEnum):
    """An excerpt of an OLD block re-found in NEW: it occurs in the text exactly once,
    or it does not."""

    #: Once, in a block the OLD block became.
    FOUND = "found"
    #: More than once in the window: nothing guessed; the block reopens.
    AMBIGUOUS = "ambiguous"
    #: Once, in a neighbour that is not a successor.
    MOVED = "moved"
    #: Nowhere: the excerpt is an orphan, demoted to the block it stood on.
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class Hint:
    """A fuzzy match for a removed excerpt: shown to the writer, never
    written into a carried line or the legend.
    `distance` is the edit distance over the excerpt's length."""

    block: str
    words: str
    distance: float


@dataclass(frozen=True, slots=True)
class Placement:
    """Where an excerpt went. `block` is where it was found once (FOUND,
    MOVED); for AMBIGUOUS and REMOVED the block it is demoted to, the OLD
    block's heir or first successor, `None` when the block was deleted."""

    excerpt: str
    cls: ExcerptClass
    block: str | None
    hint: Hint | None = None


def norm(text: str) -> str:
    """The one normaliser, on both sides: whitespace collapsed, as the edge
    sheet's `norm`."""
    return " ".join(text.split())


def block_text(version: Version, block_id: str) -> str:
    """A block's owned text, its runs joined by a space, normalised."""
    block = version.cut.block(block_id)
    return norm(" ".join(version.document.text(span) for span in block.owned))


def _count(needle: str, hay: str) -> int:
    found, at = 0, hay.find(needle)
    while at != -1:
        found += 1
        at = hay.find(needle, at + 1)
    return found


def place(excerpt: str, old: str, lineage: Lineage, new: Version) -> Placement:
    """Re-find `excerpt`, words of OLD's block `old`, in NEW: exactly, first in the
    blocks `old` became (once: found; more: ambiguous), and only if they hold it
    nowhere, in their neighbours, one block either side in NEW's order (once: moved;
    more: ambiguous); only then, if it is nowhere, the fuzzy pass over the same window,
    a hint and never a placement. A deleted block's window is what its OLD neighbours
    became, read from `lineage`'s order, which `pair` gives in OLD's reading order."""
    words = norm(excerpt)
    if not words:
        raise ValueError(f"an empty excerpt of {old} cannot be placed")
    link = lineage.link_of_old(old)
    if link is None:
        raise ValueError(f"{old} is no block of the lineage's OLD version")
    successors = list(link.new)
    order = [b.id for b in new.cut.blocks]
    at = {b: i for i, b in enumerate(order)}
    if successors:
        span = [at[b] for b in successors]
        neighbours = [
            order[i]
            for i in (min(span) - 1, max(span) + 1)
            if 0 <= i < len(order) and order[i] not in successors
        ]
    else:
        neighbours = _neighbours_of_deleted(old, lineage, order, at)
    # the paired block: a split's heir is NEW's, a merge's is OLD's
    heir = link.heir if link.heir in successors else None
    demoted = heir or (successors[0] if successors else None)
    texts = {b: block_text(new, b) for b in (*successors, *neighbours)}
    for window, cls in (
        (successors, ExcerptClass.FOUND),
        (neighbours, ExcerptClass.MOVED),
    ):
        held = {b: _count(words, texts[b]) for b in window}
        total = sum(held.values())
        if total > 1:
            return Placement(excerpt, ExcerptClass.AMBIGUOUS, demoted)
        if total == 1:
            (found,) = [b for b, n in held.items() if n]
            return Placement(excerpt, cls, found)
    return Placement(excerpt, ExcerptClass.REMOVED, demoted, _hint(words, texts))


def _neighbours_of_deleted(
    old: str, lineage: Lineage, order: list[str], at: dict[str, int]
) -> list[str]:
    """For a deleted block: the NEW blocks its OLD neighbours became, the
    one before and the one after."""
    olds = [b for link in lineage.links for b in link.old]
    if old not in olds:
        return []
    i = olds.index(old)
    found: list[str] = []
    for j in (i - 1, i + 1):
        if 0 <= j < len(olds):
            found += [b for b in lineage.new_of(olds[j]) if b in at]
    return sorted(set(found), key=at.__getitem__)


def _hint(words: str, texts: dict[str, str]) -> Hint | None:
    """The best approximate match of `words` in the window, when its edit
    distance is at most `TAU` of its length and it beats the next match
    that does not overlap it by `DELTA_HINT`; a tie gives none. A match
    that overlaps the best is the same place found again, never a rival."""
    if not words:
        return None
    matches = [
        (cost, block, start, end)
        for block, text in texts.items()
        for cost, start, end in _approximate(words, text)
    ]
    if not matches:
        return None
    matches.sort()
    best_cost, block, start, end = matches[0]
    rival = next(
        (
            cost
            for cost, other, s, e in matches[1:]
            if other != block or e <= start or s >= end
        ),
        None,
    )
    size = len(words)
    if best_cost / size > TAU:
        return None
    if rival is not None and (rival - best_cost) / size < DELTA_HINT:
        return None
    return Hint(block, texts[block][start:end], best_cost / size)


def _approximate(pattern: str, text: str) -> list[tuple[int, int, int]]:
    """Sellers' approximate substring match: for each end in `text`, the
    least edit distance of `pattern` to a substring ending there, with the
    substring's start: `(cost, start, end)`, the local minima only."""
    m = len(pattern)
    previous = list(range(m + 1))
    starts = [0] * (m + 1)
    found = []
    for j, char in enumerate(text, 1):
        current, current_starts = [0], [j]
        for i in range(1, m + 1):
            options = (
                (previous[i - 1] + (pattern[i - 1] != char), starts[i - 1]),
                (previous[i] + 1, starts[i]),
                (current[i - 1] + 1, current_starts[i - 1]),
            )
            cost, start = min(options)
            current.append(cost)
            current_starts.append(start)
        found.append((current[m], current_starts[m], j))
        previous, starts = current, current_starts
    # keep each end whose cost is no worse than its neighbours'
    return [
        f
        for k, f in enumerate(found)
        if (k == 0 or f[0] <= found[k - 1][0])
        and (k == len(found) - 1 or f[0] < found[k + 1][0])
    ]


# --- tier 0: the text map ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextMap:
    """Per file both versions read, the equal regions as `(old_start,
    old_end, new_start)` in characters: difflib's line opcodes, and inside
    each replaced run of lines that keeps its paragraph breaks its word
    opcodes, so a block on an edited line whose own words are unchanged
    still maps (a paragraph is often one line, and the cut inside it is a
    key's or a correction's). `bridged` holds, per file, each region whose
    gap to the next is whitespace on both sides (a line break moved, or
    become a space): a span may run across it."""

    regions: dict[str, tuple[tuple[int, int, int], ...]]
    bridged: dict[str, frozenset[int]] = field(default_factory=dict)

    @classmethod
    def build(cls, old: Document, new: Document) -> TextMap:
        texts = {source.path: source.text for source in new.files}
        regions, bridged = {}, {}
        for source in old.files:
            if source.path not in texts:
                continue
            a = source.text.splitlines(keepends=True)
            b = texts[source.path].splitlines(keepends=True)
            starts_a, starts_b = _starts(a), _starts(b)
            matcher = SequenceMatcher(None, a, b, autojunk=False)
            found: list[tuple[int, int, int]] = []
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    found.append((starts_a[i1], starts_a[i2], starts_b[j1]))
                elif tag == "replace" and _shape(a[i1:i2]) == _shape(b[j1:j2]):
                    found += _equal_words(
                        source.text, starts_a[i1], starts_a[i2],
                        texts[source.path], starts_b[j1], starts_b[j2],
                    )  # fmt: skip
            joined = tuple(_joined(found))
            regions[source.path] = joined
            bridged[source.path] = _bridged(joined, source.text, texts[source.path])
        return cls(regions, bridged)

    def span(self, span: Span) -> Span | None:
        """`span` in NEW's text when it lies in equal regions: one, or
        several each bridged to the next."""
        found = self.regions.get(span.file, ())
        starts = [start for start, _, _ in found]
        first = bisect_right(starts, span.start) - 1
        last = bisect_right(starts, max(span.start, span.end - 1)) - 1
        if first < 0 or span.end > found[last][1]:
            return None
        bridges = self.bridged.get(span.file, frozenset())
        if any(at not in bridges for at in range(first, last)):
            return None
        (start, _, target), (end_start, _, end_target) = found[first], found[last]
        return Span(
            span.file, target + span.start - start, end_target + span.end - end_start
        )


#: A line that is structure, not running text, by its start: a heading, a
#: list item, a quote, a fence or display line, a LaTeX environment or
#: sectioning command, a comment.
_STRUCTURE = re.compile(
    r"\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|```|~~~|\$\$|\\\[|\\\]|%"
    r"|\\(?:begin|end|item|part|chapter|section|subsection|subsubsection"
    r"|paragraph)\b)"
)


def _shape(lines: Sequence[str]) -> tuple[str, ...]:
    """The structure of `lines`: each blank line, each structure line by
    its marker, a run of text lines as one (a paragraph re-wrapped keeps
    its shape). Where the shapes differ, the edit joined or split
    paragraphs or made a line a heading or an item, and a boundary there
    is the cutter's again, so the run is not refined to words."""
    shape: list[str] = []
    for line in lines:
        if not line.strip():
            kind = "blank"
        elif found := _STRUCTURE.match(line):
            kind = found.group(1).strip()
        else:
            kind = "text"
        if not (kind == "text" and shape and shape[-1] == "text"):
            shape.append(kind)
    return tuple(shape)


def _joined(regions: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """Adjacent regions joined where they are adjacent on both sides, so a
    span across an equal line and the equal words after it maps."""
    out: list[tuple[int, int, int]] = []
    for start, end, target in regions:
        if out and out[-1][1] == start and out[-1][2] + start - out[-1][0] == target:
            out[-1] = (out[-1][0], end, out[-1][2])
        else:
            out.append((start, end, target))
    return out


def _bridged(regions: Sequence[tuple[int, int, int]], a: str, b: str) -> frozenset[int]:
    """Each region whose gap to the next is whitespace, and not empty, on
    both sides: the words run on across it (an empty gap on one side would
    join two words)."""
    found = set()
    for at, ((_, end, target), (start, _, next_target)) in enumerate(
        zip(regions, regions[1:], strict=False)
    ):
        old_gap, new_gap = a[end:start], b[target + end - regions[at][0] : next_target]
        if old_gap and new_gap and not old_gap.strip() and not new_gap.strip():
            found.add(at)
    return frozenset(found)


_TOKEN = re.compile(r"\s+|\S+")
#: Above this many token pairs a replaced run is not refined: difflib's
#: worst case is quadratic (tens of seconds on a re-wrapped appendix).
_WORDS_AT_MOST = 4_000_000


def _equal_words(
    a: str, a_start: int, a_end: int, b: str, b_start: int, b_end: int
) -> list[tuple[int, int, int]]:
    """The equal regions of `a[a_start:a_end]` and `b[b_start:b_end]` by
    difflib's opcodes over words and the whitespace between them, in the
    files' characters."""
    left = [(m.start(), m.group()) for m in _TOKEN.finditer(a, a_start, a_end)]
    right = [(m.start(), m.group()) for m in _TOKEN.finditer(b, b_start, b_end)]
    if len(left) * len(right) > _WORDS_AT_MOST:
        return []  # a whole file re-wrapped: its lines' regions only
    matcher = SequenceMatcher(
        None, [t for _, t in left], [t for _, t in right], autojunk=False
    )
    return [
        (left[i][0], left[i + n - 1][0] + len(left[i + n - 1][1]), right[j][0])
        for i, j, n in matcher.get_matching_blocks()
        if n
    ]


def _starts(lines: Sequence[str]) -> list[int]:
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    return starts


# --- the rebase ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class DroppedStart:
    """A segment start of an OLD block whose roles the rebase carried that
    no longer places once in its NEW block: dropped as a boundary, the
    block keeping its role set."""

    block: str
    starts: str
    #: The class of the link it did not survive; SAME for a block the text
    #: map kept, whose hidden text changed.
    cls: BlockClass


class Lapse(StrEnum):
    """Why a corrected boundary was not carried onto NEW."""

    #: The words at the boundary are not in NEW's re-cut blocks.
    WORDS = "words"
    #: They are there more than once, however many are taken.
    TWICE = "twice"
    #: A join's two sides stand, with another block between them.
    BETWEEN = "between"
    #: The block it falls in reads a changed map entry: the cutter's again.
    MAP = "map"

    @property
    def said(self) -> str:
        """The reason as a report says it."""
        return _LAPSE_SAID[self]


_LAPSE_SAID = {
    Lapse.WORDS: "its words changed",
    Lapse.TWICE: "its words are found more than once",
    Lapse.BETWEEN: "another block stands between its sides",
    Lapse.MAP: "its block reads a changed map entry",
}


@dataclass(frozen=True, slots=True)
class LostCorrection:
    """A boundary OLD's corrections made, or one they took away, that the
    rebase did not carry: a split's start, by the block that starts there,
    or a join across the cutter's boundary, by the joined block. `words`
    are OLD's at the boundary that the rebase could not place: a split's
    first words; a join's side whose words lapsed (`WORDS`, `TWICE`), the
    earlier where both did; else the words after the join."""

    block: str
    op: CutOp
    words: str
    why: Lapse


class GroupLapse(StrEnum):
    """Why one of OLD's groups was not carried onto NEW: its run is cut."""

    #: A member became several blocks.
    SPLIT = "split"
    #: A member was joined to another block.
    MERGED = "merged"
    #: A member is gone.
    DELETED = "deleted"
    #: Its members no longer stand as one run, in order.
    RUN = "run"
    #: The block it names became several, was joined or is gone, or its
    #: title now names another.
    NAMES = "names"

    @property
    def said(self) -> str:
        """The reason as a report says it."""
        return _GROUP_LAPSE_SAID[self]


_GROUP_LAPSE_SAID = {
    GroupLapse.SPLIT: "a member was split",
    GroupLapse.MERGED: "a member was joined to another block",
    GroupLapse.DELETED: "a member was deleted",
    GroupLapse.RUN: "its members no longer stand as one run",
    GroupLapse.NAMES: "the block it names was split, joined or deleted, or its "
    "title now names another",
}


@dataclass(frozen=True, slots=True)
class LostGroup:
    """One of OLD's groups the rebase did not carry, by OLD id, and why."""

    group: str
    why: GroupLapse


@dataclass(frozen=True, slots=True)
class Rebased:
    """OLD's corrected cut rebased onto NEW: every OLD
    block whose text the text map carries whole keeps its boundaries, roles
    and segments at the mapped place; in each changed region the cutter's
    blocks of NEW stand, and are the blocks to re-cut (`recut`, by NEW id,
    for the boundary question), parted or joined again where OLD's
    corrections did so and their words carry them: exactly (`_restored`),
    or by the text map at the boundary (`_anchored`; each one not carried
    is in `corrections_lost`). Then identity's lineage over these
    blocks carries OLD's roles to the NEW block of each one-to-one same,
    moved, math, markup or macro link the text map did not keep (moved
    whole, or only its maths or markup changed; `_carry_roles`), which is
    not re-cut; a carried or kept block keeps each segment whose start
    still places once, the others listed in `dropped_starts`. A group
    stands where each member and the block it names stand by a one-to-one
    link, of any class, its members still stand as one run, and a title in
    a first block the text map did not keep names what it named: an edit
    to a member's words keeps the group. Else it is lost (`groups_lost`, by
    OLD id, with why) and left out, not guessed. Beside OLD's groups stand the
    cutter's over re-cut blocks or a lost group's members, `proposed` (by
    NEW id), as a build of NEW would propose them."""

    cut: Cut
    recut: tuple[str, ...]
    groups_lost: tuple[LostGroup, ...]
    proposed: tuple[str, ...] = ()
    #: The segment starts the rebase could not re-find in a block that kept
    #: its roles, for the `part` flag.
    dropped_starts: tuple[DroppedStart, ...] = ()
    #: OLD's corrected boundaries in a changed stretch that the rebase could
    #: not carry, each with why.
    corrections_lost: tuple[LostCorrection, ...] = ()

    @property
    def touched(self) -> tuple[str, ...]:
        """The groups lost, by OLD id: the groups question's to ask again."""
        return tuple(lost.group for lost in self.groups_lost)


def rebase_cut(old: Version, new_document: Document, new_cutter: Cut) -> Rebased:
    """OLD's corrected cut onto NEW, one path for a store's cut and a given
    one; `new_cutter` is the cutter's cut of NEW under the same rules."""
    text_map = TextMap.build(old.document, new_document)
    files = views(new_document)
    # a block that reads a changed map entry is re-cut, its text kept or not
    marks = vocabulary_marks(old.document, new_document)
    mapped: dict[str, Block] = {}  # OLD id -> the block at its new place
    nested: dict[str, list[Span]] = defaultdict(list)
    for block in old.cut.blocks:
        if block.within is not None:
            nested[block.within].append(block.extent)
    for block in old.cut.blocks:
        if not _between_unchanged(block, text_map, nested[block.id]):
            continue  # text came between its runs: its place is not its own
        if marks and marks_any(block.owned, marks.old):
            continue
        owned = [text_map.span(run) for run in block.owned]
        if not owned or any(run is None for run in owned):
            continue
        runs = tuple(run for run in owned if run is not None)
        extent = _extent(block, runs, text_map)
        if extent is None:
            continue
        view = files[runs[0].file]
        mapped[block.id] = replace(
            block,
            id=block_id(view, runs[0].start),
            extent=extent,
            owned=runs,
            hash=block_hash(new_document.text(run) for run in runs),
        )
    new_id = {old_id: b.id for old_id, b in mapped.items()}
    covered: dict[str, list[tuple[int, int]]] = defaultdict(list)
    # what parts a stretch: a kept top-level block, never a kept footnote
    # inside the changed paragraph that holds it
    standing: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for b in mapped.values():
        for run in b.owned:
            covered[run.file].append((run.start, run.end))
            if b.within is None:
                standing[run.file].append((run.start, run.end))

    blocks = list(mapped.values())
    recut: list[str] = []
    carried: dict[str, str] = {}  # OLD id -> its NEW block, roles carried
    whole: list[Block] = []  # the cutter's blocks no kept block touches
    for block in new_cutter.blocks:
        rest = _uncovered(block.owned, covered, files)
        if not rest:
            continue
        if rest == block.owned:
            blocks.append(block)
            whole.append(block)
        else:
            # what is left of the cutter's block, one block per stretch no
            # kept block stands in: never a merge across one
            for piece in _stretches(rest, standing):
                view = files[piece[0].file]
                blocks.append(
                    replace(
                        block,
                        id=block_id(view, piece[0].start),
                        extent=_piece_extent(block, piece, standing),
                        owned=piece,
                        roles=(),
                        segments=(),
                        hash=block_hash(new_document.text(run) for run in piece),
                        doubts=(),
                    )
                )
                recut.append(blocks[-1].id)
            continue
        recut.append(blocks[-1].id)
    # a corrected boundary whose words outside maths are unchanged, carried
    # by exact words in any gap
    # where OLD's cutter put a boundary: one of OLD's cut elsewhere, or one
    # missing there, is a correction's
    layout = _layout(old)
    restored, replaced, carried_old = _restored(
        old,
        new_document,
        files,
        set(mapped),
        [b for b in whole if not (marks and marks_any(b.owned, marks.new))],
        new_cutter,
        layout,
    )
    blocks = [b for b in blocks if b.id not in replaced] + restored
    recut = [b for b in recut if b not in replaced] + [b.id for b in restored]
    # a corrected boundary in a changed stretch, carried where its words are
    # unchanged around it, though the text before or after it changed
    blocks, recut, corrections_lost = _anchored(
        old,
        new_document,
        text_map,
        files,
        layout,
        blocks,
        recut,
        set(mapped) | carried_old,
        {b.id for b in restored},
        {b.id for b in blocks if marks and marks_any(b.owned, marks.new)},
    )

    order = ReadingOrder.of(new_document)
    blocks.sort(key=lambda b: (order.key(b.extent), -(b.extent.end - b.extent.start)))
    blocks = _nested(blocks, old, new_id, new_cutter)
    blocks, dropped, carried_parts, lineage = _carry_roles(
        old, new_document, blocks, new_cutter, new_id, carried
    )
    # a kept block's hidden text may have changed (a comment between a
    # start's words): its starts are checked as a carried block's are
    kept_new = set(new_id.values())
    for n, b in enumerate(blocks):
        if b.id in kept_new and len(b.segments) > 1:
            blocks[n], lost = _with_roles_of(b, b, new_document)
            dropped += tuple(DroppedStart(b.id, w, BlockClass.SAME) for w in lost)
    recut = [b for b in recut if b not in carried.values()]
    ids = {**new_id, **carried}
    # a member, or the block a group names, stands by any one-to-one link,
    # CHANGED too: its hash moves, so the update asks the group's line again
    heirs = {
        link.old[0]: link.new[0]
        for link in lineage.links
        if len(link.old) == 1 and len(link.new) == 1
    }

    def standing(old_id: str) -> str | None:
        return ids.get(old_id) or heirs.get(old_id)

    groups: list[Group] = []
    lost_groups: list[LostGroup] = []
    lost_at: set[str] = set()  # NEW blocks a lost group's members stand in
    position = {b.id: n for n, b in enumerate(blocks)}
    within = {b.id: b.within for b in blocks}
    for group in old.cut.groups:
        members = [standing(b) for b in group.blocks]
        names = standing(group.names) if group.names is not None else None
        kept = tuple(m for m in members if m is not None)
        why = None
        if len(kept) != len(members):
            gone = next(b for b, m in zip(group.blocks, members, strict=True) if not m)
            link = lineage.link_of_old(gone)
            why = GroupLapse.DELETED if link is None else _MEMBER_LAPSE[link.cls]
        elif group.names is not None and names is None:
            why = GroupLapse.NAMES
        elif (
            group.named_by is Naming.TITLE
            and group.blocks[0] not in new_id
            and _title_names(new_cutter, group, kept[0]) != names
        ):
            # the title is its first block's words: the cutter reads them anew
            why = GroupLapse.NAMES
        elif not _a_run(kept, blocks, position, within):
            # inside a run, an added block joins: the groups question's
            why = GroupLapse.RUN
        if why is not None:
            lost_groups.append(LostGroup(group.id, why))
            lost_at.update(kept)
            continue
        groups.append(replace(group, blocks=kept, names=names))
    # the cutter's groups over NEW whose blocks all stand and that touch a
    # re-cut block or a lost group's member, as a build of NEW would
    # propose them, none sharing a block with a group of OLD's that stands
    present = {b.id for b in blocks}
    in_kept = {b for g in groups for b in g.blocks}
    proposals = [
        g
        for g in new_cutter.groups
        if set(g.blocks) <= present
        and (g.names is None or g.names in present)
        and not set(g.blocks) & in_kept
        and any(b in recut or b in lost_at for b in g.blocks)
    ]
    groups = _numbered(
        sorted(
            [*groups, *proposals],
            key=lambda g: (position[g.blocks[0]], -len(g.blocks), g.kind),
        )
    )
    proposed = tuple(
        g.id
        for g in groups
        if any((g.kind, g.blocks) == (p.kind, p.blocks) for p in proposals)
    )

    index = BlockIndex(blocks)
    parts = []
    for part in old.cut.parts:
        at = text_map.span(part.at)
        if part.block in mapped and at is not None:
            parts.append(replace(part, block=new_id[part.block], at=at))
    parts.extend(carried_parts)
    kept_ids = set(new_id.values()) | set(carried.values())
    for part in new_cutter.parts:
        holder = _holding(part.at, blocks)
        if holder is not None and holder not in kept_ids:
            parts.append(replace(part, block=holder))
    parts.sort(key=lambda part: (position[part.block], part.at.start))
    cut = Cut(
        rules=new_cutter.rules,
        blocks=tuple(blocks),
        parts=tuple(parts),
        groups=tuple(groups),
        candidates=edge_candidates(
            new_document, index, label_holders(new_document, index)
        ),
    )
    return Rebased(
        cut, tuple(recut), tuple(lost_groups), proposed, dropped, corrections_lost
    )


def _title_names(cutter: Cut, group: Group, first: str) -> str | None:
    """The block the cutter's group of `group`'s kind starting at `first`
    names by its title; None where it names none so."""
    return next(
        (
            g.names
            for g in cutter.groups
            if g.kind is group.kind
            and g.blocks[0] == first
            and g.named_by is Naming.TITLE
        ),
        None,
    )


#: Why a group is lost where a member has no one-to-one link, by its class.
_MEMBER_LAPSE = {
    BlockClass.SPLIT: GroupLapse.SPLIT,
    BlockClass.MERGED: GroupLapse.MERGED,
    BlockClass.DELETED: GroupLapse.DELETED,
}


#: The links whose NEW block takes OLD's roles though the text map did not
#: keep its place (their roles come with the rebase).
_CARRIED = (
    BlockClass.SAME,
    BlockClass.MOVED,
    BlockClass.MATH,
    BlockClass.MARKUP,
    BlockClass.MACRO,
)


def _displays(document: Document, block: Block) -> int:
    """How many of the reader's displays start on a character `block` owns:
    the cutter's own idea of a display, whatever its environment."""
    shown = [env.span for env in document.environments if env.kind is EnvKind.DISPLAY]
    outer = [
        span
        for span in shown
        if not any(
            other != span
            and other.file == span.file
            and other.start <= span.start
            and span.end <= other.end
            for other in shown
        )
    ]  # `$$\begin{align}…$$` is one display, not two
    return sum(
        1
        for span in outer
        if any(
            run.file == span.file and run.start <= span.start < run.end
            for run in block.owned
        )
    )


def _carry_roles(
    old: Version,
    new_document: Document,
    blocks: list[Block],
    new_cutter: Cut,
    new_id: dict[str, str],
    carried: dict[str, str],
) -> tuple[list[Block], tuple[DroppedStart, ...], list[MarkedPart], Lineage]:
    """Identity's own lineage over the rebased blocks decides what keeps
    OLD's roles: each one-to-one
    link of a class in `_CARRIED` whose NEW block the text map did not
    keep, with as many displays as OLD's (else a lead-in alone, its display
    apart, would take a merged block's roles) and parts OLD's alike
    (`_parts_placed`). `carried` gains each; the starts dropped, the
    carried blocks' parts and the lineage are returned."""
    provisional = Cut(new_cutter.rules, tuple(blocks), (), (), ())
    lineage = pair(old, Version(new_document, provisional))
    old_blocks = {b.id: b for b in old.cut.blocks}
    cutter_parts: dict[str, list[MarkedPart]] = defaultdict(list)
    for part in new_cutter.parts:
        holder = _holding(part.at, blocks)
        if holder is not None:
            cutter_parts[holder].append(part)
    kept = set(new_id.values())
    at = {b.id: n for n, b in enumerate(blocks)}
    out = list(blocks)
    dropped: list[DroppedStart] = []
    parts: list[MarkedPart] = []
    for link in lineage.links:
        if len(link.old) != 1 or len(link.new) != 1 or link.cls not in _CARRIED:
            continue
        (was_id,), (now_id,) = link.old, link.new
        if now_id in kept:
            continue
        was, now = old_blocks[was_id], out[at[now_id]]
        if _displays(old.document, was) != _displays(new_document, now):
            continue
        placed = _parts_placed(
            [p for p in old.cut.parts if p.block == was_id], cutter_parts[now_id]
        )
        if placed is None:
            continue
        placed = [replace(p, block=now_id) for p in placed]
        out[at[now_id]], lost = _with_roles_of(was, now, new_document)
        carried[was_id] = now_id
        parts.extend(placed)
        dropped.extend(DroppedStart(now_id, words, link.cls) for words in lost)
    return out, tuple(dropped), parts, lineage


#: The most blocks one carried correction joins or parts.
_RESTORE_RUN = 8
#: The most words a start's excerpt takes before it is given up as not
#: found once.
_START_WORDS = 12


def _restored(
    old: Version,
    new_document: Document,
    files: dict[str, FileView],
    kept: set[str],
    whole: list[Block],
    new_cutter: Cut,
    layout: set[tuple[str, int]],
) -> tuple[list[Block], set[str], set[str]]:
    """OLD's corrected boundaries the text map could not keep (the block
    moved, or only its maths or markup changed), carried by exact words
    outside maths over the cutter's blocks no kept block touches, in any
    gap: a run of the cutter's
    blocks whose joined words are one unkept OLD block's is that block
    again (a merge kept); a cutter block whose words are several
    consecutive unkept OLD blocks' is parted where each later one's first
    words are found once (`_placed_once`'s rule), else left whole. Only
    top-level blocks with words, nothing nested in them, a meaning each
    side alike or none; each match unique. The blocks made, the cutter's
    blocks they replace and the OLD blocks they carry, by id."""
    has_nested = {b.within for b in new_cutter.blocks if b.within is not None}
    has_nested |= {b.within for b in old.cut.blocks if b.within is not None}

    def text(document: Document, block: Block) -> str:
        return " ".join(document.text(run) for run in block.owned)

    def eligible(block: Block, document: Document) -> tuple[str, ...] | None:
        if block.within is not None or block.id in has_nested:
            return None
        found = words_of(text(document, block))
        return found or None

    olds = [b for b in old.cut.blocks if b.within is None]
    old_words = {b.id: eligible(b, old.document) for b in olds}
    # only a correction's boundary is carried: one the cutter of OLD did not
    # make (an author's join or a new heading is the cutter's again)

    def merged(block: Block) -> bool:
        """`block` holds a boundary the cutter of OLD made: a correction
        joined it."""
        first = (block.owned[0].file, block.owned[0].start)
        return any(
            (file, start) != first
            and file == block.extent.file
            and block.extent.start <= start < block.extent.end
            for file, start in layout
        )

    def parted(run: list[Block]) -> bool:
        """Every boundary inside `run` is a correction's, none the cutter's."""
        return all((b.owned[0].file, b.owned[0].start) not in layout for b in run[1:])

    open_old = [b for b in olds if b.id not in kept and old_words[b.id]]
    new_words = {b.id: eligible(b, new_document) for b in whole}
    order = {
        b.id: n for n, b in enumerate(b for b in new_cutter.blocks if b.within is None)
    }
    old_order = {b.id: n for n, b in enumerate(olds)}

    def runs(items: list[Block], at: dict[str, int], words: dict) -> list[list[Block]]:
        """Every run of 1 to `_RESTORE_RUN` blocks of `items` adjacent in
        `at`'s order, each with words."""
        found = []
        for i in range(len(items)):
            run = [items[i]]
            if not words[items[i].id]:
                continue
            found.append(list(run))
            for j in range(i + 1, min(i + _RESTORE_RUN, len(items))):
                if at[items[j].id] != at[run[-1].id] + 1 or not words[items[j].id]:
                    break
                run.append(items[j])
                found.append(list(run))
        return found

    def joined(run: list[Block], words: dict) -> tuple[str, ...]:
        return tuple(w for b in run for w in words[b.id])

    used_old: set[str] = set()
    used_new: set[str] = set()
    made: list[Block] = []
    # one cutter block, several OLD blocks: part it
    old_runs = [r for r in runs(open_old, old_order, old_words) if len(r) > 1]
    by_words: dict[tuple[str, ...], list[list[Block]]] = defaultdict(list)
    for run in old_runs:
        by_words[joined(run, old_words)].append(run)
    for cutter in whole:
        found = by_words.get(new_words[cutter.id] or (), [])
        if len(found) != 1 or cutter.id in used_new:
            continue
        (run,) = found
        if any(b.id in used_old for b in run) or not parted(run):
            continue
        if any(b.meaning not in (cutter.meaning, None) for b in run):
            continue
        pieces = _parted(cutter, run, old.document, new_document, files)
        if pieces is None:
            continue
        made.extend(pieces)
        used_new.add(cutter.id)
        used_old.update(b.id for b in run)
    # several cutter blocks, one OLD block: join them; a nested block in
    # `whole` (a footnote new in a parent the kept blocks part) has no place
    # in the top-level order
    open_new = [b for b in whole if b.id not in used_new and b.within is None]
    new_runs = [
        r
        for r in runs(open_new, order, new_words)
        if len(r) > 1 and len({b.extent.file for b in r}) == 1
    ]
    by_new: dict[tuple[str, ...], list[list[Block]]] = defaultdict(list)
    for run in new_runs:
        by_new[joined(run, new_words)].append(run)
    old_count = Counter(old_words[b.id] for b in open_old)
    for was in open_old:
        if was.id in used_old or old_count[old_words[was.id]] != 1:
            continue
        if not merged(was):
            continue
        found = by_new.get(old_words[was.id] or (), [])
        if len(found) != 1 or any(b.id in used_new for b in found[0]):
            continue
        (run,) = found
        if any(b.meaning not in (was.meaning, None) for b in run):
            continue
        owned = tuple(r for b in run for r in b.owned)
        made.append(
            replace(
                run[0],
                extent=Span(
                    run[0].extent.file, run[0].extent.start, run[-1].extent.end
                ),
                owned=owned,
                form=was.form,
                environment=was.environment,
                meaning=was.meaning,
                roles=(),
                segments=(),
                hash=block_hash(new_document.text(r) for r in owned),
                doubts=(),
            )
        )
        used_new.update(b.id for b in run)
        used_old.add(was.id)
    return made, used_new, used_old


def _layout(old: Version) -> set[tuple[str, int]]:
    """Where the cutter of OLD starts a top-level block, as `(file, offset)`:
    a boundary of OLD's cut elsewhere is a correction's, and one of these
    that OLD's cut lacks a correction joined."""
    from axiomation.cutter import cut as cutter_cut

    return {
        (b.owned[0].file, b.owned[0].start)
        for b in cutter_cut(old.document, old.cut.rules).blocks
        if b.within is None and b.owned
    }


#: The words either side of a corrected boundary that place it in NEW:
#: through the text map where they map unchanged, else found exactly once
#: in the re-cut blocks, growing to `_START_WORDS` until they are (a
#: replaced run whose paragraphs were joined or parted is aligned by its
#: lines alone, so no word in it maps). Fewer where the run holds fewer.
_ANCHOR_WORDS = 3
_WORD = re.compile(r"\S+")


def _anchored(
    old: Version,
    new_document: Document,
    text_map: TextMap,
    files: dict[str, FileView],
    layout: set[tuple[str, int]],
    blocks: list[Block],
    recut: list[str],
    kept: set[str],
    restored: set[str],
    marked: set[str],
) -> tuple[list[Block], list[str], tuple[LostCorrection, ...]]:
    """OLD's corrected boundaries in a changed stretch, carried where the
    words at each still stand, whatever changed before or after them
    (`_ANCHOR_WORDS`): a split where its later block's first words now
    are, the re-cut block holding them parted there; a join across a
    boundary of OLD's cutter where the words before it and after it now
    end and start two re-cut blocks with none between, which are joined.
    Only a boundary at risk is weighed: a split's two blocks both unkept,
    a join's block unkept (`kept`, by OLD id: those the text map or exact
    words carried). A boundary is made or taken only in re-cut blocks: not
    in one made by exact words (`restored`), whose cut is OLD's already,
    nor in one that reads a changed map entry (`marked`), which is the
    cutter's again; a block across files is left whole. The blocks, the
    re-cut ids and each boundary at risk not carried, with why; a nested
    block is placed in its parent after (`_nested`); one on a block the
    edit deleted is not listed. `layout` is where OLD's cutter starts a
    block (`_layout`)."""
    chars = {source.path: source.text for source in old.document.files}
    new_chars = {source.path: source.text for source in new_document.files}
    olds = [b for b in old.cut.blocks if b.within is None and b.owned]
    open_ids = set(recut) - restored - marked
    out, recut = list(blocks), list(recut)
    lost: list[LostCorrection] = []

    def gone(block: Block) -> bool:
        """Whether the edit deleted `block` with nothing in its place: none
        of its text maps, and between the equal regions either side of it
        NEW holds only whitespace. Its corrections went with it, which is
        no lapse to list."""
        file, (start, end) = block.extent.file, (block.extent.start, block.extent.end)
        regions = text_map.regions.get(file, ())
        if any(a < end and start < b for a, b, _ in regions):
            return False
        prev = [r for r in regions if r[1] <= start]
        nxt = [r for r in regions if r[0] >= end]
        lo = prev[-1][2] + prev[-1][1] - prev[-1][0] if prev else 0
        hi = nxt[0][2] if nxt else len(new_chars.get(file, ""))
        return file in new_chars and not new_chars[file][lo:hi].strip()

    def owner(file: str, at: int) -> int | None:
        """The top-level block that owns offset `at`, by index."""
        for n, b in enumerate(out):
            if b.within is None and any(
                r.file == file and r.start <= at < r.end for r in b.owned
            ):
                return n
        return None

    def found_once(words: list[str], after: bool) -> int | Lapse:
        """Where `words` start (`after`) or end in NEW's re-cut blocks, on
        a character a block owns, growing them until they are found once."""
        for n in range(
            min(_ANCHOR_WORDS, len(words)), min(_START_WORDS, len(words)) + 1
        ):
            excerpt = " ".join(words[:n] if after else words[-n:])
            hits = set()
            for b in out:
                if b.within is not None or b.id not in recut:
                    continue
                text = new_document.text(b.extent)
                for start, end in occurrences(text, excerpt):
                    at = b.extent.start + (start if after else end)
                    probe = at if after else at - 1
                    if any(
                        r.file == b.extent.file and r.start <= probe < r.end
                        for r in b.owned
                    ):
                        hits.add(at)
            if len(hits) == 1:
                return hits.pop()
            if not hits:
                return Lapse.WORDS
        return Lapse.TWICE

    def placed(run: Span, at: int, after: bool) -> tuple[str, int | Lapse] | None:
        """OLD's words at offset `at` of `run`, after it or before it, and
        where that boundary now stands in NEW; None where the run holds no
        word on that side."""
        text = chars[run.file]
        words = list(
            _WORD.finditer(text, at, run.end)
            if after
            else _WORD.finditer(text, run.start, at)
        )
        if not words:
            return None
        near = words[:_ANCHOR_WORDS] if after else words[-_ANCHOR_WORDS:]
        span = Span(run.file, near[0].start(), near[-1].end())
        said = old.document.text(span)
        moved = text_map.span(span)
        if moved is not None:
            return said, moved.start if after else moved.end
        return said, found_once([m.group() for m in words], after)

    # a split: the later block's first words, where they now stand
    for was, then in itertools.pairwise(olds):
        first = then.owned[0]
        if was.id in kept or then.id in kept or (first.file, first.start) in layout:
            continue
        found = placed(first, first.start, after=True)
        if found is None:
            continue
        said, at = found
        if isinstance(at, Lapse):
            if not gone(then):
                lost.append(LostCorrection(then.id, CutOp.SPLIT, said, at))
            continue
        n = owner(first.file, at)
        if n is None or out[n].owned[0].start == at:
            continue  # a boundary there already
        if out[n].id in marked:
            lost.append(LostCorrection(then.id, CutOp.SPLIT, said, Lapse.MAP))
            continue
        if out[n].id not in open_ids:
            continue  # made by exact words: OLD's cut there already
        pieces = _parted_at(out[n], at, files, new_document)
        if pieces is None:
            continue
        head, tail = pieces
        out[n : n + 1] = [head, tail]
        open_ids.add(tail.id)
        recut.insert(recut.index(head.id) + 1, tail.id)

    # a join: the words either side of the cutter's boundary, two re-cut
    # blocks ending and starting there with none between
    for was in olds:
        if was.id in kept:
            continue
        inner = sorted(
            (file, at)
            for file, at in layout
            if (file, at) != (was.owned[0].file, was.owned[0].start)
            and any(r.file == file and r.start <= at < r.end for r in was.owned)
        )
        for file, at in inner:
            run = next(r for r in was.owned if r.file == file and r.start <= at < r.end)
            earlier = [r for r in was.owned if r.file == file and r.start < at]
            after = placed(run, at, after=True)
            before = placed(earlier[-1], min(earlier[-1].end, at), after=False)
            if after is None or before is None:
                continue
            lapsed = next((s for s in (before, after) if isinstance(s[1], Lapse)), None)
            if lapsed is not None:
                said, lapse = lapsed
                assert isinstance(lapse, Lapse)
                if not gone(was):
                    lost.append(LostCorrection(was.id, CutOp.MERGE, said, lapse))
                continue
            said = after[0]
            end, start = before[1], after[1]
            assert isinstance(end, int) and isinstance(start, int)
            i, j = owner(file, end - 1), owner(file, start)
            if i is None or j is None or i == j:
                continue  # joined already
            left, right = out[i], out[j]
            if left.id in marked or right.id in marked:
                lost.append(LostCorrection(was.id, CutOp.MERGE, said, Lapse.MAP))
                continue
            if left.id not in open_ids or right.id not in open_ids:
                continue
            if left.extent.file != right.extent.file or not (
                left.owned[0].start < right.owned[0].start
            ):
                continue
            if any(
                b.within is None
                and b.id not in (left.id, right.id)
                and b.owned[0].file == file
                and left.owned[-1].end <= b.owned[0].start < right.owned[0].start
                for b in out
            ):
                lost.append(LostCorrection(was.id, CutOp.MERGE, said, Lapse.BETWEEN))
                continue
            owned: list[Span] = []
            for r in (*left.owned, *right.owned):
                gap = new_chars[r.file][owned[-1].end : r.start] if owned else "x"
                if owned and owned[-1].file == r.file and not gap.strip():
                    owned[-1] = Span(
                        r.file, owned[-1].start, r.end
                    )  # as the assembler joins
                else:
                    owned.append(r)
            out[i] = replace(
                left,
                extent=Span(left.extent.file, left.extent.start, right.extent.end),
                owned=tuple(owned),
                form=was.form,
                environment=was.environment,
                meaning=was.meaning,
                roles=(),
                segments=(),
                hash=block_hash(new_document.text(r) for r in owned),
                doubts=(),
            )
            del out[j]
            open_ids.discard(right.id)
            recut.remove(right.id)
    return out, recut, tuple(lost)


def _parted_at(
    block: Block, at: int, files: dict[str, FileView], document: Document
) -> tuple[Block, Block] | None:
    """`block` parted at offset `at`, a character it owns: the head keeps
    its id and what lies before, hidden text and nested blocks included;
    the tail starts at `at`. None for a block across files, or where a
    side would own nothing."""
    file = block.extent.file
    if any(r.file != file for r in block.owned):
        return None
    chars = files[file].text

    def trimmed(a: int, b: int) -> Span | None:
        while a < b and chars[a].isspace():
            a += 1
        while b > a and chars[b - 1].isspace():
            b -= 1
        return Span(file, a, b) if a < b else None

    head_runs = [trimmed(r.start, min(r.end, at)) for r in block.owned if r.start < at]
    tail_runs = [trimmed(max(r.start, at), r.end) for r in block.owned if r.end > at]
    head = tuple(r for r in head_runs if r is not None)
    tail = tuple(r for r in tail_runs if r is not None)
    if not head or not tail:
        return None
    end = trimmed(block.extent.start, at)
    assert end is not None

    def piece(ident: str, extent: Span, owned: tuple[Span, ...]) -> Block:
        return replace(
            block,
            id=ident,
            extent=extent,
            owned=owned,
            roles=(),
            segments=(),
            hash=block_hash(document.text(r) for r in owned),
            doubts=(),
        )

    return (
        piece(block.id, Span(file, block.extent.start, end.end), head),
        piece(
            block_id(files[file], tail[0].start),
            Span(file, tail[0].start, block.extent.end),
            tail,
        ),
    )


def _parted(
    cutter: Block,
    run: list[Block],
    old_document: Document,
    new_document: Document,
    files: dict[str, FileView],
) -> list[Block] | None:
    """`cutter` parted where each OLD block of `run` after the first starts:
    the shortest run of its first words found once in `cutter`, starting on
    a character it owns; None where one is not found once, or out of order."""
    file = cutter.extent.file
    if any(r.file != file for r in cutter.owned):
        return None
    text = new_document.text(cutter.extent)
    base = cutter.extent.start
    starts = [cutter.owned[0].start]
    for was in run[1:]:
        tokens = " ".join(old_document.text(r) for r in was.owned).split()
        at = None
        for n in range(1, min(_START_WORDS, len(tokens)) + 1):
            hits = [
                base + s
                for s, _ in occurrences(text, " ".join(tokens[:n]))
                if any(r.start <= base + s < r.end for r in cutter.owned)
            ]
            if len(hits) == 1:
                at = hits[0]
                break
            if not hits:
                break
        if at is None or at <= starts[-1]:
            return None
        starts.append(at)
    view = files[file]
    chars = view.text
    pieces = []
    bounds = [*starts, cutter.extent.end]
    for was, (lo, hi) in zip(run, zip(bounds, bounds[1:], strict=False), strict=True):
        owned = []
        for r in cutter.owned:
            a, b = max(r.start, lo), min(r.end, hi)
            while a < b and chars[a].isspace():
                a += 1
            while b > a and chars[b - 1].isspace():
                b -= 1
            if a < b:
                owned.append(Span(file, a, b))
        if not owned:
            return None
        pieces.append(
            replace(
                cutter,
                id=block_id(view, owned[0].start),
                extent=Span(file, owned[0].start, owned[-1].end),
                owned=tuple(owned),
                form=was.form,
                environment=was.environment,
                meaning=was.meaning,
                roles=(),
                segments=(),
                hash=block_hash(new_document.text(r) for r in owned),
                doubts=(),
            )
        )
    return pieces


def _parts_placed(
    own: list[MarkedPart], cutter: list[MarkedPart]
) -> list[MarkedPart] | None:
    """The parts of a carried block: the cutter's parts of its NEW block when
    they are OLD's, kind, label and (but for an item) marker alike, as a
    whole (an item's text
    or a labelled display edited, rows reordered, the block moved: the text
    says where each now is); None when they differ in any way (a part OLD's
    corrections made or removed, one an edit made or took away), and the
    block is left to the cutter, re-cut."""

    def named(parts: list[MarkedPart]) -> Counter[tuple[object, ...]]:
        # an item's marker is its bullet or number as written, which a
        # markup edit may restyle; a heading's is its words
        return Counter(
            (p.kind, p.label, None if p.kind is PartKind.ITEM else p.marker)
            for p in parts
        )

    return list(cutter) if named(own) == named(cutter) else None


def _with_roles_of(
    was: Block, block: Block, document: Document
) -> tuple[Block, tuple[str, ...]]:
    """`block` with OLD's `was` role set and each later segment whose start
    occurs once in it as the assembler places one (`_placed_once`); a start
    found no time or twice is dropped as a boundary and returned beside the
    block. Where the segments left do not cover the role set, none is
    placed (a mixed block), or with one role the one segment."""
    kept, dropped = list(was.segments[:1]), []
    for segment in was.segments[1:]:
        if _placed_once(segment.starts, block, document):
            kept.append(segment)
        else:
            dropped.append(segment.starts)
    if {s.role for s in kept if s.role is not None} != set(was.roles):
        kept = [Segment("", was.roles[0])] if len(was.roles) == 1 else []
    return replace(block, roles=was.roles, segments=tuple(kept)), tuple(dropped)


def _placed_once(excerpt: str, block: Block, document: Document) -> bool:
    """Whether `excerpt` matches once in `block`, as the assembler finds a
    segment start: over its extent, hidden text included, whitespace runs
    as one, starting on a character it owns."""
    text = document.text(block.extent)
    start = block.extent.start
    found = [
        s
        for s, _ in occurrences(text, excerpt)
        if any(
            run.file == block.extent.file and run.start <= start + s < run.end
            for run in block.owned
        )
    ]
    return len(found) == 1


def _extent(block: Block, runs: tuple[Span, ...], text_map: TextMap) -> Span | None:
    """A kept block's extent in NEW: its mapped runs, with the head before
    its first run and the tail after its last (a comment, a marker the
    extent holds and no run owns) where each maps still glued to its run;
    else the extent ends at the run, so nothing inserted beside the block
    falls inside it."""
    file, first, last = block.extent.file, block.owned[0], block.owned[-1]
    if runs[0].file != file or runs[-1].file != file:
        return None
    start, end = runs[0].start, runs[-1].end
    if first.file == file and block.extent.start < first.start:
        head = text_map.span(Span(file, block.extent.start, first.start))
        if head is not None and head.end == runs[0].start:
            start = head.start
    if last.file == file and last.end < block.extent.end:
        tail = text_map.span(Span(file, last.end, block.extent.end))
        if tail is not None and tail.start == runs[-1].end:
            end = tail.end
    return Span(file, start, end)


def _a_run(
    members: Sequence[str],
    blocks: Sequence[Block],
    position: dict[str, int],
    within: dict[str, str | None],
) -> bool:
    """Whether `members` still stand as one run of NEW's blocks, in order:
    every block between the first and the last is a member, or nested in
    one."""
    at = [position[m] for m in members]
    if at != sorted(at):
        return False
    held = set(members)
    return all(b.id in held or within[b.id] in held for b in blocks[at[0] : at[-1] + 1])


def _numbered(groups: Sequence[Group]) -> list[Group]:
    """Each group's id as the cutter gives it (`groups._with_ids`): its kind
    at its first block, `#n` from the second with that base on."""
    seen: dict[str, int] = {}
    out = []
    for group in groups:
        base = f"{group.kind.value}@{group.blocks[0]}"
        seen[base] = seen.get(base, 0) + 1
        out.append(
            replace(group, id=base if seen[base] == 1 else f"{base}#{seen[base]}")
        )
    return out


def _between_unchanged(block: Block, text_map: TextMap, inner: list[Span]) -> bool:
    """Whether the text between a block's owned runs is unchanged, apart
    from its nested blocks' (a footnote edited inside a paragraph leaves the
    paragraph its place; a paragraph inserted between its runs does not)."""
    for a, b in itertools.pairwise(block.owned):
        if a.file != b.file:
            continue
        pieces = [(a.end, b.start)]
        for extent in inner:
            if extent.file == a.file:
                pieces = [
                    piece
                    for start, end in pieces
                    for piece in (
                        (start, min(end, extent.start)),
                        (max(start, extent.end), end),
                    )
                    if piece[0] < piece[1]
                ]
        new_a, new_b = text_map.span(a), text_map.span(b)
        if new_a is None or new_b is None:
            return False
        if not pieces:
            if new_b.start - new_a.end != b.start - a.end:
                return False
            continue
        mapped = [text_map.span(Span(a.file, start, end)) for start, end in pieces]
        if any(m is None for m in mapped):
            return False
        # still glued to the runs either side: an insertion on an equal
        # region's edge maps every piece, but leaves a gap
        if pieces[0][0] == a.end and mapped[0].start != new_a.end:  # type: ignore[union-attr]
            return False
        if pieces[-1][1] == b.start and mapped[-1].end != new_b.start:  # type: ignore[union-attr]
            return False
    return True


def _piece_extent(
    block: Block, piece: tuple[Span, ...], covered: dict[str, list[tuple[int, int]]]
) -> Span:
    """A stretch's extent: its runs, reaching to the cutter block's own
    start or end where no kept block's run reaches into what it would take
    (the nested blocks and hidden text there are the piece's, as they were
    the block's)."""
    file, start, end = piece[0].file, piece[0].start, piece[-1].end
    if file != block.extent.file or piece[-1].file != file:
        return Span(file, start, end)
    spans = covered.get(file, ())
    # a kept merge's one run may begin before the cutter block it reaches
    # into: any overlap with the stretch to widen over stops it
    if not any(a < start and b > block.extent.start for a, b in spans):
        start = min(start, block.extent.start)
    if not any(a < block.extent.end and b > end for a, b in spans):
        end = max(end, block.extent.end)
    return Span(file, start, end)


def _stretches(
    runs: tuple[Span, ...], covered: dict[str, list[tuple[int, int]]]
) -> list[tuple[Span, ...]]:
    """`runs` cut wherever a covered stretch stands between two of them."""
    out: list[list[Span]] = []
    for run in runs:
        if out:
            last = out[-1][-1]
            between = last.file == run.file and any(
                last.end <= start and end <= run.start
                for start, end in covered.get(run.file, ())
            )
            if not between and last.file == run.file:
                out[-1].append(run)
                continue
        out.append([run])
    return [tuple(piece) for piece in out]


def _uncovered(
    owned: tuple[Span, ...],
    covered: dict[str, list[tuple[int, int]]],
    files: dict[str, FileView],
) -> tuple[Span, ...]:
    """The runs of `owned` outside the covered characters, each trimmed of
    whitespace as the cutter trims its runs."""
    rest: list[Span] = []
    for run in owned:
        pieces = [(run.start, run.end)]
        for start, end in covered.get(run.file, ()):
            pieces = [
                piece
                for a, b in pieces
                for piece in ((a, min(b, start)), (max(a, end), b))
                if piece[0] < piece[1]
            ]
        text = files[run.file].text
        for a, b in pieces:
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            if a < b:
                rest.append(Span(run.file, a, b))
    return tuple(rest)


def _holding(at: Span, blocks: Sequence[Block]) -> str | None:
    for block in blocks:
        if any(
            run.file == at.file and run.start <= at.start < run.end
            for run in block.owned
        ):
            return block.id
    return None


def _nested(
    blocks: list[Block], old: Version, new_id: dict[str, str], new_cutter: Cut
) -> list[Block]:
    """Each nested block's `within`: its old parent's new place where that
    mapped, else the smallest block whose extent holds it."""
    cutter_ids = {b.id for b in new_cutter.blocks}
    by_id = {b.id: b for b in blocks}
    old_within = {new_id[b.id]: b.within for b in old.cut.blocks if b.id in new_id}
    out = []
    for block in blocks:
        within = block.within
        if block.id in old_within:
            parent = old_within[block.id]
            within = new_id.get(parent) if parent is not None else None
            if parent is not None and within is None:
                within = _container(block, blocks)
        elif within is not None and (
            within not in by_id
            or within not in cutter_ids
            or not _holds(by_id[within].extent, block.extent)
        ):
            # the cutter's parent gone, or parted and this in another piece
            within = _container(block, blocks)
        out.append(block if within == block.within else replace(block, within=within))
    return out


def _holds(outer: Span, inner: Span) -> bool:
    return (
        outer.file == inner.file
        and outer.start <= inner.start
        and inner.end <= outer.end
    )


def _container(block: Block, blocks: Sequence[Block]) -> str | None:
    holders = [b for b in blocks if b.id != block.id and _holds(b.extent, block.extent)]
    if not holders:
        return None
    return min(holders, key=lambda b: b.extent.end - b.extent.start).id


# --- macros -----------------------------------------------------------------


def _uses(name: str) -> re.Pattern[str]:
    return re.compile(re.escape(name) + r"(?![A-Za-z@])")


def changed_macros(old: Document, new: Document) -> frozenset[str]:
    """The macros whose definitions differ between the versions (added and
    removed ones too), closed over the macros whose definitions use one."""

    def definitions(document: Document) -> dict[str, tuple[tuple[object, ...], ...]]:
        found: dict[str, list[tuple[object, ...]]] = defaultdict(list)
        for macro in document.macros:
            found[macro.name].append(
                (macro.command, macro.arguments, " ".join(macro.definition.split()))
            )
        return {name: tuple(value) for name, value in found.items()}

    before, after = definitions(old), definitions(new)
    changed = {n for n in before.keys() | after.keys() if before.get(n) != after.get(n)}
    bodies = {
        name: " ".join(
            str(d[-1]) for d in (*before.get(name, ()), *after.get(name, ()))
        )
        for name in before.keys() | after.keys()
    }
    grown = True
    while grown:
        grown = False
        for name, body in bodies.items():
            if name not in changed and any(_uses(c).search(body) for c in changed):
                changed.add(name)
                grown = True
    return frozenset(changed)


@dataclass(frozen=True, slots=True)
class VocabularyMarks:
    """What a changed vocabulary map reads, on each side
    (`docs/architecture.md` § 2): the names whose entries differ, and the
    spans that read one in OLD and in NEW. A block owning a marked span is
    changed, though its text is not: it is re-cut, and its roles and edges
    asked again."""

    names: tuple[str, ...] = ()
    old: tuple[Span, ...] = ()
    new: tuple[Span, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.names)


def vocabulary_marks(old: Document, new: Document) -> VocabularyMarks:
    """The map entries that differ between the versions' maps, in what the
    reader reads of them (an environment's kind and base; a reference's
    list, prefix and word; whether a command defines a label), and the
    spans they mark on each side, read symmetrically: an environment's every
    use and the proof that trails it, which it may attach or detach; a
    command's every use; for a label definer, also every reference to a
    label it defines on either side."""
    before, after = _entries(old), _entries(new)
    changed = sorted(
        place
        for place in before.keys() | after.keys()
        if before.get(place) != after.get(place)
    )
    if not changed:
        return VocabularyMarks()
    environments = {name for namespace, name in changed if namespace == "environment"}
    commands = {name for namespace, name in changed if namespace == "command"}
    proofs = {"proof"} | {
        e.name
        for document in (old, new)
        for e in document.vocabulary.environments
        if e.base == "proof"
    }
    definers = commands & {
        name for document in (old, new) for name in document.vocabulary.labels
    }
    defined = {
        label.name
        for document in (old, new)
        for label in document.labels
        if any(document.text(label.at).startswith(f"\\{name}") for name in definers)
    }

    def marked(document: Document) -> tuple[Span, ...]:
        spans: list[Span] = []
        uses = [env for env in document.environments if env.name in environments]
        for env in uses:
            spans.append(env.span)
            proof = _trailing(document, env, proofs)
            if proof is not None:
                spans.append(proof)
        for source in document.files:
            for name in commands:
                spans.extend(
                    Span(source.path, m.start(), m.end())
                    for m in _uses(name).finditer(source.text)
                )
        spans.extend(r.at for r in document.references if set(r.targets) & defined)
        return tuple(sorted(set(spans), key=lambda s: (s.file, s.start, s.end)))

    return VocabularyMarks(
        tuple(f"{namespace} {name}" for namespace, name in changed),
        marked(old),
        marked(new),
    )


def vocabulary_moved(old: Document, new: Document) -> dict[str, list[str]]:
    """The map entries that differ, as a diff and an identity answer name
    them; nothing where the maps agree."""
    names = vocabulary_marks(old, new).names
    return {"vocabulary": list(names)} if names else {}


def _entries(document: Document) -> dict[tuple[str, str], object]:
    """A document's map as the reader reads it, by namespace and name: an
    entry for a name code reads itself (a declaration, an environment
    read by its own name, a reference command of the paper's or a
    package's) is read as code reads it, whatever the map says, so it is
    left out."""
    vocabulary = document.vocabulary
    declared = {d.name for d in document.declarations}
    coded = set(reference_table(document.files).commands)
    found: dict[tuple[str, str], list[object]] = {}
    for e in vocabulary.environments:
        if e.name not in declared and static_kind(e.name) is None:
            found.setdefault(("environment", e.name), []).append((e.kind, e.base))
    for r in vocabulary.references:
        if r.command not in coded:
            found.setdefault(("command", r.command), []).append(
                ("reference", r.takes_list, r.prefix, r.word)
            )
    for name in vocabulary.labels:
        if name not in coded:
            found.setdefault(("command", name), []).append(("label",))
    # a name declared more than once reads each use under the declaration
    # in force: an edit of any of its declaring texts moves what it reads
    texts: dict[tuple[str, str], list[str | None]] = {}
    for a in vocabulary.answers:
        texts.setdefault((a.namespace, a.name), []).append(a.declaration)
    for place, declared in texts.items():
        if len(declared) > 1:
            found.setdefault(place, []).append(
                ("declared", *sorted(map(str, declared)))
            )
    return {place: tuple(value) for place, value in found.items()}


def _trailing(document: Document, env: Environment, proofs: set[str]) -> Span | None:
    """The proof environment that is the first thing after `env`, if any."""
    span = env.span
    text = document.file(span.file).text
    after = [
        other
        for other in document.environments
        if other.span.file == span.file and other.span.start >= span.end
    ]
    if not after:
        return None
    first = min(after, key=lambda other: other.span.start)
    if first.name in proofs and not text[span.end : first.span.start].strip():
        return first.span
    return None


def marks_any(spans: Iterable[Span], marks: Sequence[Span]) -> bool:
    """Whether any of `spans` overlaps a marked span."""
    return any(
        run.file == mark.file and run.start < mark.end and mark.start < run.end
        for run in spans
        for mark in marks
    )


def macro_uses(
    document: Document, cut: Cut, names: Iterable[str]
) -> dict[str, frozenset[str]]:
    """For each block, which of `names` its owned text uses."""
    patterns = {name: _uses(name) for name in names}
    found = {}
    for block in cut.blocks:
        text = " ".join(document.text(span) for span in block.owned)
        used = frozenset(n for n, p in patterns.items() if p.search(text))
        if used:
            found[block.id] = used
    return found


# --- the tiers --------------------------------------------------------------


def pair(
    old: Version,
    new: Version,
    *,
    theta: float = THETA,
    delta_pair: float = DELTA_PAIR,
) -> Lineage:
    """The lineage from OLD's cut to NEW's, by the tiers. Other thresholds
    than the shipped ones are for a sweep of them only: such
    a lineage is never stored, since `IDENTITY_VERSION` names the shipped
    ones."""
    return _Pairing(old, new, theta, delta_pair).run()


class _Pairing:
    def __init__(
        self, old: Version, new: Version, theta: float, delta_pair: float
    ) -> None:
        self.old, self.new = old, new
        self.theta, self.delta_pair = theta, delta_pair
        self.left = keyed(old.document, old.cut)
        self.right = keyed(new.document, new.cut)
        self.left_at = {k.block.id: i for i, k in enumerate(self.left)}
        self.right_at = {k.block.id: i for i, k in enumerate(self.right)}
        self.links: list[Link] = []
        self.paired_left: set[int] = set()
        self.paired_right: set[int] = set()
        self.gap_left: list[int] = []
        self.gap_right: list[int] = []
        #: The blocks the text map paired: no gap's own.
        self.mapped_left: set[int] = set()
        self.mapped_right: set[int] = set()

    def run(self) -> Lineage:
        self._text_map()
        self.mapped_left = set(self.paired_left)
        self.mapped_right = set(self.paired_right)
        self._gaps()
        self._labels()
        self._hashes()
        for gap in sorted(set(self.gap_left) | set(self.gap_right)):
            left = self._open(self.left, self.paired_left, self.gap_left, gap)
            right = self._open(self.right, self.paired_right, self.gap_right, gap)
            self._keys(left, right)
            self._words(left, right)
            self._ratios(left, right)
            self._form(gap, left, right)
        self._rest()
        self._moves()
        self._macros()
        self._vocabulary()
        return Lineage(tuple(self._ordered()))

    def _link(
        self,
        left: Sequence[int],
        right: Sequence[int],
        cls: BlockClass,
        tier: Tier,
        heir: str | None = None,
    ) -> None:
        self.paired_left.update(left)
        self.paired_right.update(right)
        self.links.append(
            Link(
                tuple(self.left[i].block.id for i in left),
                tuple(self.right[j].block.id for j in right),
                cls,
                tier,
                heir,
            )
        )

    def _cls(self, i: int, j: int) -> BlockClass:
        one, other = self.left[i], self.right[j]
        if one.block.meaning != other.block.meaning:
            # the hash leaves out `\begin{…}`: an environment whose meaning
            # changed is never the same block; changed where the role its
            # meaning says changed (lemma to definition, a proof to a lemma)
            # or where it moves to or from a conjecture, whose claim stands
            # `conjectured` and needs no argument; else only its markup
            # (lemma to theorem)
            was, now = (
                ROLE_OF_MEANING.get(k.block.meaning or "") for k in (one, other)
            )
            conjectured = {k.block.meaning == CONJECTURE for k in (one, other)}
            if was != now or len(conjectured) > 1:
                return BlockClass.CHANGED
            return BlockClass.MARKUP
        if one.block.hash == other.block.hash:
            return BlockClass.SAME  # or moved, by `_moves`
        if one.key == other.key:
            if one.mathematics != other.mathematics:
                return BlockClass.MATH
            return BlockClass.MARKUP
        return BlockClass.CHANGED

    def _text_map(self) -> None:
        text_map = TextMap.build(self.old.document, self.new.document)
        by_owned = {k.block.owned: j for j, k in enumerate(self.right)}
        for i, item in enumerate(self.left):
            if not item.block.owned:
                continue
            mapped = tuple(text_map.span(s) for s in item.block.owned)
            if any(s is None for s in mapped):
                continue
            j = by_owned.get(mapped)  # type: ignore[arg-type]
            if (
                j is not None
                and j not in self.paired_right
                and self.right[j].block.hash == item.block.hash
                and self.right[j].block.meaning == item.block.meaning
            ):
                self._link([i], [j], BlockClass.SAME, Tier.TEXT_MAP)

    def _gaps(self) -> None:
        """Each block's gap: the number of anchors before it, the anchors
        being the text map's pairs kept in order on both sides."""
        anchors = sorted(
            (self.left_at[link.old[0]], self.right_at[link.new[0]])
            for link in self.links
        )
        kept = _increasing(anchors)
        before_left = [i for i, _ in kept]
        before_right = sorted(j for _, j in kept)
        self.gap_left = [bisect_right(before_left, i) for i in range(len(self.left))]
        self.gap_right = [bisect_right(before_right, j) for j in range(len(self.right))]

    def _labels(self) -> None:
        def held(items: list[Keyed], paired: set[int]) -> dict[str, int]:
            return {
                label: i
                for i, item in enumerate(items)
                if i not in paired
                for label in item.key[2]  # type: ignore[union-attr]
            }

        left = held(self.left, self.paired_left)
        right = held(self.right, self.paired_right)
        partners: dict[int, set[int]] = defaultdict(set)
        backers: dict[int, set[int]] = defaultdict(set)
        for label, i in left.items():
            if label in right:
                partners[i].add(right[label])
                backers[right[label]].add(i)
        for i, found in sorted(partners.items()):
            (j,) = found if len(found) == 1 else (None,)
            if j is not None and backers[j] == {i}:
                self._link([i], [j], self._cls(i, j), Tier.LABEL)

    def _hashes(self) -> None:
        def unique(items: list[Keyed], paired: set[int]) -> dict[str, int]:
            count = Counter(
                k.block.hash for i, k in enumerate(items) if i not in paired
            )
            return {
                k.block.hash: i
                for i, k in enumerate(items)
                if i not in paired and count[k.block.hash] == 1
            }

        right = unique(self.right, self.paired_right)
        for hash_, i in unique(self.left, self.paired_left).items():
            if hash_ in right:
                self._link([i], [right[hash_]], self._cls(i, right[hash_]), Tier.HASH)

    @staticmethod
    def _open(
        items: list[Keyed], paired: set[int], gaps: list[int], gap: int
    ) -> list[int]:
        return [i for i in range(len(items)) if gaps[i] == gap and i not in paired]

    def _keys(self, left: list[int], right: list[int]) -> None:
        """Equal keys in one gap. A key held by as many blocks on each side
        pairs them in order; held by more on one side, it pairs none."""
        by_left: dict[object, list[int]] = defaultdict(list)
        by_right: dict[object, list[int]] = defaultdict(list)
        for i in left:
            by_left[self.left[i].key].append(i)
        for j in right:
            by_right[self.right[j].key].append(j)
        for key, ones in by_left.items():
            others = by_right.get(key, [])
            if len(ones) == len(others):
                for i, j in zip(ones, others, strict=True):
                    self._link([i], [j], self._cls(i, j), Tier.KEY)
        left[:] = [i for i in left if i not in self.paired_left]
        right[:] = [j for j in right if j not in self.paired_right]

    def _words(self, left: list[int], right: list[int]) -> None:
        """Splits, then merges: one block's words are exactly a run of two or
        more consecutive open blocks' on the other side."""
        for one, many, side, cls in (
            (left, right, self.right, BlockClass.SPLIT),
            (right, left, self.left, BlockClass.MERGED),
        ):
            own = self.left if cls is BlockClass.SPLIT else self.right
            document = (self.old if cls is BlockClass.SPLIT else self.new).document
            for position in list(one):
                target = own[position].words
                if not target:
                    continue
                run = _run_of(target, [side[k].words for k in many])
                if run is None:
                    continue
                children = many[run[0] : run[1]]
                heir = _heir(own[position], [side[k] for k in children], document)
                if cls is BlockClass.SPLIT:
                    self._link([position], children, cls, Tier.WORDS, heir)
                else:
                    self._link(children, [position], cls, Tier.WORDS, heir)
                one.remove(position)
                del many[run[0] : run[1]]

    def _ratios(self, left: list[int], right: list[int]) -> None:
        """One to one by word ratio: each pair at least θ (`THETA`),
        beating every other open candidate of both its blocks by δ_pair
        (`DELTA_PAIR`). A candidate under θ - δ_pair can neither pair nor
        rival one, so its ratio is not computed past difflib's upper
        bound."""
        floor = self.theta - self.delta_pair
        ratio: dict[tuple[int, int], float] = {}
        for i in left:
            for j in right:
                matcher = SequenceMatcher(
                    None, self.left[i].words, self.right[j].words, autojunk=False
                )
                if matcher.quick_ratio() >= floor and matcher.ratio() >= floor:
                    ratio[(i, j)] = matcher.ratio()
        of_left: dict[int, list[tuple[int, float]]] = defaultdict(list)
        of_right: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for (i, j), value in ratio.items():
            of_left[i].append((j, value))
            of_right[j].append((i, value))
        for (i, j), value in sorted(ratio.items(), key=lambda item: -item[1]):
            if value < self.theta:
                break
            if i in self.paired_left or j in self.paired_right:
                continue
            rivals = [r for b, r in of_left[i] if b != j and b not in self.paired_right]
            rivals += [
                r for a, r in of_right[j] if a != i and a not in self.paired_left
            ]
            if all(value - r >= self.delta_pair for r in rivals):
                j = self._split_heir(i, j)
                self._link([i], [j], self._cls(i, j), Tier.RATIO)
        left[:] = [i for i in left if i not in self.paired_left]
        right[:] = [j for j in right if j not in self.paired_right]

    def _split_heir(self, i: int, j: int) -> int:
        """A paragraph split by an edit, its seam reworded: OLD block `i`
        pairs by ratio with NEW block `j`, and an open neighbour of `j` in
        its gap starts with `i`'s first words, the two joined in reading
        order reaching θ against `i`. Then that neighbour continues `i`
        (the heir: label, else first words, else most words), and `j`
        is left to be ADDED."""
        old = self.left[i]
        start = _first_words(old, self.old.document)
        if _holds_once(self.right[j].words, start):
            return j
        for k in (j - 1, j + 1):
            if not 0 <= k < len(self.right) or k in self.paired_right:
                continue
            if self.gap_right[k] != self.gap_right[j]:
                continue
            neighbour = self.right[k]
            if neighbour.words[: len(start)] != start:
                continue
            first, second = sorted((k, j))
            joined = self.right[first].words + self.right[second].words
            matcher = SequenceMatcher(None, old.words, joined, autojunk=False)
            if matcher.ratio() >= self.theta:
                return k
        return j

    def _form(self, gap: int, left: list[int], right: list[int]) -> None:
        """A gap of exactly one block on each side besides the text map's,
        of one form: one changed block whatever its ratio (a retitled title
        block). And, counted per form, one heading (or one title) left open
        on each side by the surer tiers: one changed heading, its words a
        name (a renamed heading beside an edited block; narrowed
        to headings and titles: for prose the ratio
        tier has judged every pair already)."""
        whole_left = [
            i
            for i, g in enumerate(self.gap_left)
            if g == gap and i not in self.mapped_left
        ]
        whole_right = [
            j
            for j, g in enumerate(self.gap_right)
            if g == gap and j not in self.mapped_right
        ]
        if len(whole_left) == 1 and len(whole_right) == 1:
            (i,), (j,) = whole_left, whole_right
            open_ = i not in self.paired_left and j not in self.paired_right
            if open_ and self.left[i].block.form == self.right[j].block.form:
                self._link([i], [j], self._cls(i, j), Tier.FORM)
        for form in (Form.HEADING, Form.TITLE):
            named_left = [
                i
                for i in left
                if i not in self.paired_left and self.left[i].block.form is form
            ]
            named_right = [
                j
                for j in right
                if j not in self.paired_right and self.right[j].block.form is form
            ]
            if len(named_left) == 1 and len(named_right) == 1:
                self._link(
                    named_left,
                    named_right,
                    self._cls(*named_left, *named_right),
                    Tier.FORM,
                )

    def _rest(self) -> None:
        for i in range(len(self.left)):
            if i not in self.paired_left:
                self._link([i], [], BlockClass.DELETED, Tier.NONE)
        for j in range(len(self.right)):
            if j not in self.paired_right:
                self._link([], [j], BlockClass.ADDED, Tier.NONE)

    def _moves(self) -> None:
        """A one-to-one pair outside the longest run of pairs that keep
        their order is moved; a moved pair whose hash is equal is MOVED.
        The run is over every pair, not the gaps, so a reorder the text map
        did not anchor (one file become eight) is found too."""
        one_to_one = sorted(
            (self.left_at[link.old[0]], self.right_at[link.new[0]])
            for link in self.links
            if len(link.old) == len(link.new) == 1
        )
        kept = set(_increasing(one_to_one))
        moved = []
        for link in self.links:
            at = (
                (self.left_at[link.old[0]], self.right_at[link.new[0]])
                if len(link.old) == len(link.new) == 1
                else None
            )
            if at is None or at in kept:
                moved.append(link)
                continue
            cls = BlockClass.MOVED if link.cls is BlockClass.SAME else link.cls
            moved.append(replace(link, cls=cls, moved=True))
        self.links = moved

    def _macros(self) -> None:
        """A block that uses a changed macro renders differently though its
        hash may not move: same, moved and markup-only blocks become MACRO,
        changed in maths, keeping their move, as a changed block."""
        changed = changed_macros(self.old.document, self.new.document)
        if not changed:
            return
        old_uses = macro_uses(self.old.document, self.old.cut, changed)
        new_uses = macro_uses(self.new.document, self.new.cut, changed)
        marked = (BlockClass.SAME, BlockClass.MOVED, BlockClass.MARKUP)
        self.links = [
            replace(link, cls=BlockClass.MACRO)
            if link.cls in marked
            and (
                any(b in old_uses for b in link.old)
                or any(b in new_uses for b in link.new)
            )
            else link
            for link in self.links
        ]

    def _vocabulary(self) -> None:
        """A block that reads a changed map entry is read anew though its
        text is the same (`vocabulary_marks`): a link that would carry it
        unchanged is CHANGED."""
        marks = vocabulary_marks(self.old.document, self.new.document)
        if not marks:
            return
        old_blocks = {b.id: b for b in self.old.cut.blocks}
        new_blocks = {b.id: b for b in self.new.cut.blocks}
        carried = (
            BlockClass.SAME,
            BlockClass.MOVED,
            BlockClass.MARKUP,
            BlockClass.MACRO,
        )
        self.links = [
            replace(link, cls=BlockClass.CHANGED)
            if link.cls in carried
            and (
                any(marks_any(old_blocks[b].owned, marks.old) for b in link.old)
                or any(marks_any(new_blocks[b].owned, marks.new) for b in link.new)
            )
            else link
            for link in self.links
        ]

    def _ordered(self) -> list[Link]:
        """OLD's reading order; an added block after the link that holds
        the NEW block before it, added blocks in NEW's order."""
        held = sorted(
            (max(self.right_at[b] for b in link.new), self.left_at[link.old[0]])
            for link in self.links
            if link.old and link.new
        )

        def place(link: Link) -> tuple[int, int, int]:
            if link.old:
                return (min(self.left_at[b] for b in link.old), 0, 0)
            j = min(self.right_at[b] for b in link.new)
            before = [old for new, old in held if new < j]
            return (before[-1] if before else -1, 1, j)

        return sorted(self.links, key=place)


def _increasing(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The longest run of `pairs`, sorted by their first, whose seconds
    increase too (patience sorting)."""
    tails: list[int] = []
    tail_at: list[int] = []
    previous: list[int] = [-1] * len(pairs)
    for n, (_, second) in enumerate(pairs):
        at = bisect_right(tails, second)
        if at > 0 and tails[at - 1] == second:
            continue
        if at == len(tails):
            tails.append(second)
            tail_at.append(n)
        else:
            tails[at] = second
            tail_at[at] = n
        previous[n] = tail_at[at - 1] if at > 0 else -1
    out = []
    n = tail_at[-1] if tail_at else -1
    while n != -1:
        out.append(pairs[n])
        n = previous[n]
    return out[::-1]


def _run_of(
    target: tuple[str, ...], words: Sequence[tuple[str, ...]]
) -> tuple[int, int] | None:
    """The first run of two or more consecutive `words` that joined are
    `target`, as `(start, end)`."""
    for start in range(len(words)):
        joined: list[str] = []
        end = start
        for end in range(start, len(words)):
            joined.extend(words[end])
            if len(joined) >= len(target):
                break
        if end > start and tuple(joined) == target and words[start] and words[end]:
            return start, end + 1
    return None


#: Fewer words than this in a block's first sentence, and its first
#: words are this many words instead.
_FIRST_WORDS = 6


def _first_words(one: Keyed, document: Document) -> tuple[str, ...]:
    """A block's first words: those of its first sentence, or its first
    `_FIRST_WORDS` words where the sentence is shorter."""
    text = " ".join(document.text(run) for run in one.block.owned)
    end = re.search(r"(?<=[.!?])\s", text)
    found = words_of(text[: end.start()] if end else text)
    return found if len(found) >= _FIRST_WORDS else one.words[:_FIRST_WORDS]


def _holds_once(words: tuple[str, ...], run: tuple[str, ...]) -> bool:
    """Whether `run` stands exactly once, contiguous, in `words`."""
    if not run:
        return False
    n = len(run)
    return sum(1 for k in range(len(words) - n + 1) if words[k : k + n] == run) == 1


def _heir(one: Keyed, children: Sequence[Keyed], document: Document) -> str:
    """The child `one` continues in: the one
    holding its label, else the one holding its first words (found once),
    else the one with most of its words."""
    labels = set(one.key[2])  # type: ignore[arg-type]
    holding = [c for c in children if labels & set(c.key[2])]  # type: ignore[arg-type]
    if holding:
        return holding[0].block.id
    start = _first_words(one, document)
    opening = [c for c in children if _holds_once(c.words, start)]
    if len(opening) == 1:
        return opening[0].block.id
    return max(children, key=lambda c: len(c.words)).block.id
