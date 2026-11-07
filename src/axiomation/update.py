"""An update: a paper's graph taken from one version to the next by asking
only what the change touched.

A text edit. The store holds an identity answer for NEW (`axiomation diff OLD NEW --cut
CUT --store S`, or `update --from`), found by NEW's document hash; its raw holds all the
update needs from OLD, whose directory is never read again: NEW's rebased cut, the
lineage, the edges sheet carried onto NEW and OLD's texts. Then, stacked on the rebased
cut as `review_cut` stacks on a given cut:
- **the boundaries** (step 1, from the cutter; none with the cut held): one
  `decide_questions` chunk per run of the blocks to re-cut, an added
  block's run with its neighbours, context either side, no flags; its
  corrections weighed with the rebased cut as the origin;
- **the roles**: `touched_question` on the blocks that are not the
  rebased cut's own; unanswered, step 1 is taken back;
- **the groups**: the groups session whole where the rebase lost a
  group or a step 2 block lies in or next to one;
- **the edges then** start from the carried sheet carried again onto the
  cut the steps left, and the lineage composed with the rebased cut's to
  it;

- **what session 1 reopens**: carry's own reasons, the changed and
  added blocks, and `mechanical_reopen`'s: the `[position]` edges of a
  moved block, every `scope:paper` source and the rule's `where-used`
  reasons on any edit but markup, and around an insertion or a deletion
  the `[position]` edges onto its neighbours and the about edges onto its
  section's blocks;
- **the conditional reach**: `closure.impact_reach` from the
  changed blocks as if every part moved, and from each deleted block, over
  OLD's edges onto it, with the rule's Δ `removed`; each block reached and
  not open already is open on the condition written under its reaching
  edge;
- **session 1** (step 6, `edges_file.update_question`): the carried sheet,
  old text beside new, the Δ lines owed; its gate runs the rule on the
  writer's Δ lines and fixes every owner they leave closed. Past the
  switch's share of blocks open, the edges are asked fresh instead
  (`edges_file.session_question`), with no Δ and no follow-up;
- **session 2** (step 7, `followup`): what session 1's answer
  reveals beyond it, on the owners it left closed: the rule again on the
  graph it wrote, from every block with a Δ and every added block; the
  mirror check on a dropped uses edge; the reverse prefilter on a new
  part's words. Asked once at most (`followup_sessions_max`); what lies
  beyond is reported, not asked;
- **the round** (step 8, `driver.run_cut_rounds`): the last edges answer's
  cut corrections and `? role` flags, up to `cut_rounds`, the rebased cut
  the origin; the role corrections its standings imply applied
  before, with the answer (`driver.claims_applied`), as a build applies
  them;
- **the person rounds** (`driver.run_person_rounds`): each answer a
  person gave on NEW, by its layer's type rule, its re-ask holding the
  pins.

OLD's person pins, carried in the identity answer (`carried.pins`), hold
throughout: step 1 and the round drop a correction that would
change one, steps 2 and the round never ask a roles-pinned block, step 3
drops a groups correction on a pinned group, and every edges gate holds
the edges pins that the sheet it shows can hold (`person.held_against`);
the export reports each pin held, held with its meaning moved, or lapsed.
A fixed owner whose own lines no longer read on NEW (a comment now inside
a quote) is opened before session 1.

**An update of one session** replaces steps 1 to 7 below
`per_stage_update_above_recut_blocks` re-cut blocks: nothing is asked
before one session (`edges_file.plain_update_question`) on the
carried sheet, asked as a queue of the blocks the edit touched
(`update_queue`), the lines of every block it serves its own, the re-cut
blocks' roles
open (applied with its answer, as the edges' claim corrections are), a Δ
line owed per changed block, its cut lines applied as they are answered,
splits first, and the blocks they make served after
(`edges_file.plain_cut_applied`, `_session_cut`). No follow-up: what its
Δs reach over the carried edges is marked at $0 (`dependency_marks`),
moved by rule or stale with its causes, support computed only where the
graph decides it; the round and the person rounds follow as above.

Transactional: an unanswered question leaves no export, and the same call
resumes. With nothing re-cut and no group lost, no step asks anything;
with nothing open (a markup edit), nothing is asked and the carried sheet
is the edge half. `build` stays on `driver.review_cut`;
`update --from-nothing` is the build.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from axiomation import versions
from axiomation.assembler import Corrected, corrected_cut
from axiomation.changes import Layer
from axiomation.closure import (
    CLOSING,
    SCOPE_PAPER,
    EdgeGraph,
    Impact,
    Link,
    from_records,
    impact_reach,
    resolve_content,
    rule,
)
from axiomation.cutter import CutRules, DisplayRule
from axiomation.cutter.model import Cut, Form, Role, Segment
from axiomation.driver import (
    BY_EDGES,
    PINNED,
    CutRound,
    PersonRounds,
    Proposal,
    ReviewSettings,
    checker,
    claims_applied,
    cut_corrections_payload,
    cut_proposals,
    decided_by,
    first_authors,
    flags_raised,
    may_ask,
    near_group,
    person_payload,
    run_cut_rounds,
    run_person_rounds,
    unwound,
    weigh,
)
from axiomation.driver import changed_blocks as corrected_blocks
from axiomation.exchange import (
    Authors,
    cut_from_payload,
    cut_payload,
    edges_payload,
    flag_payload,
    line_authors,
    line_key_from_json,
    line_keys,
    named_claims_key,
    provenance,
    update_export,
    update_review,
)
from axiomation.flags import Raised
from axiomation.identity import (
    BlockClass,
    ExcerptClass,
    Lineage,
    Tier,
    Version,
    block_text,
    pair,
)
from axiomation.identity import Link as LineageLinkOf
from axiomation.person import (
    EdgePins,
    Pin,
    PinKind,
    Pinned,
    as_left,
    held_on,
    lapsed,
    marked,
    moved,
    pin_from,
    pin_place,
    pinned_of,
    pinned_question,
    unpinned,
)
from axiomation.questions import (
    Question,
    Stage,
    Template,
    decide_file,
    edges_file,
    groups_file,
    load_template,
    roles_file,
)
from axiomation.questions import edge_sheet as es
from axiomation.questions.chunks import DEFAULT_CHUNK, Chunk, units
from axiomation.questions.edges_file import (
    _REOPENS,
    EDGES_FILE,
    Carried,
    CarriedExcerpt,
    Gone,
    Hinted,
    Placed,
)
from axiomation.reader import Document, document_hash
from axiomation.records import (
    AGENT,
    IDENTITY,
    PERSON,
    AnswerRecord,
    CutCorrection,
    CutOp,
    Delta,
    Edge,
    EdgeKind,
    EdgeRecords,
    EdgeTarget,
    Entry,
    Flag,
    LineageLink,
    Mark,
    RecordError,
    RecordStore,
    Signal,
    TargetKind,
)
from axiomation.run_state import RunFile, text_in, text_of
from axiomation.runner import Runner, RunReport

if TYPE_CHECKING:
    # the queue imports the edits, which import this module
    from axiomation import update_queue

PRODUCER = "axiomation update"
#: A deleted block's name in the update's graph and its export: it has no
#: block of NEW.
DELETED = edges_file.DELETED_BLOCK
#: The classes whose block's text changed: it is shown old beside new and
#: owes its Δ lines.
CHANGED = (
    BlockClass.MATH,
    BlockClass.MACRO,
    BlockClass.CHANGED,
    BlockClass.SPLIT,
    BlockClass.MERGED,
)
#: Why step 1's corrections were taken back: the roles of the blocks they
#: changed went unanswered, as the deciding step's are.
STEP_ROLES_UNANSWERED = "not applied: the update's roles session is unanswered"


class Mode(StrEnum):
    """An update's two modes, as build's, naming whether
    step 1, the boundary call, can be asked: from the cutter, the
    boundaries of its changed regions may be asked (step 1, where any block
    is to re-cut); or the rebased cut's boundaries are held (`--hold-cut`),
    as `build --cut` holds a given cut's, and step 1 is never asked. Steps
    2 and 3 run in both. The mode says what could be asked, not what was:
    an update from the cutter with nothing to re-cut asks no step 1."""

    CUTTER = "cutter"
    HOLD_CUT = "hold-cut"


class UpdateError(ValueError):
    """An update that cannot start: no identity answer for NEW, or one
    without a carried sheet."""


@dataclass(frozen=True, slots=True)
class UpdateSettings:
    """An update's settings: the paper's files on disk (an edge may target
    them), the characters a session reads at most (`None`: no limit), the
    writer's brief and the update's section after it; the follow-up
    sessions asked at most (0: what session 1 reveals is reported, not
    asked), whether a new part's words open the blocks that write them
    (the reverse prefilter), and the share of NEW's blocks open in session
    1 past which the edges are asked fresh (`None`: never); the steps' and
    the round's review settings, and whether the rebased cut's boundaries
    are held."""

    files: tuple[str, ...] = ()
    limit: int | None = DEFAULT_CHUNK
    edges: Template = field(
        default_factory=lambda: load_template(edges_file.SESSION_TEMPLATE)
    )
    edges_update: Template = field(
        default_factory=lambda: load_template(edges_file.UPDATE_TEMPLATE)
    )
    #: Step 1's brief, the re-cut of the changed regions: the deciding step's
    #: cut rules on a diff, the region as it read before beside it.
    recut: Template = field(
        default_factory=lambda: load_template(decide_file.UPDATE_TEMPLATE)
    )
    followup_sessions_max: int = 1
    reverse_prefilter: bool = True
    fresh_edges_above_reopened_fraction: float | None = None
    #: Steps 1 to 3 and the round, as a build's: the
    #: stages a step asks (the boundaries step 1, at `rounds` 1 or more; the
    #: roles step 2; the groups step 3), the context blocks either side, the
    #: rounds of cut corrections (`cut_rounds`) and the templates. The edges
    #: alone by default: no step.
    review: ReviewSettings = field(
        default_factory=lambda: ReviewSettings(stages=(Stage.EDGES,))
    )
    #: Whether the rebased cut's boundaries are held (`--hold-cut`): no
    #: step 1.
    hold_cut: bool = False
    #: The update's shape: past this many re-cut blocks it is
    #: asked per stage (steps 1 to 3, session 1, the follow-up); with as
    #: many or fewer, in one session, asked as a queue of the blocks the
    #: edit touched. None: every update per stage.
    per_stage_update_above_recut_blocks: int | None = None
    #: The one session's Δ lines asked on blocks its Δ lines move by rule,
    #: at most, over the session: a frontier that would pass it carries
    #: wholly, unread.
    moved_delta_lines_max: int = 60
    #: The paper's directory, which the one session's queue reads to serve
    #: the paper and apply its cut lines as they are answered.
    paper: Path | None = None
    #: Who answers the one session, asked as a queue of the blocks the
    #: edit touched (`update_queue`); None: no host is seated for it, so
    #: with a runner an unanswered one session is refused.
    edges_queue: update_queue.Seats | None = None

    def single_session(self, recut: int) -> bool:
        """Whether an update with `recut` re-cut blocks is one session."""
        limit = self.per_stage_update_above_recut_blocks
        return limit is not None and recut <= limit

    @property
    def mode(self) -> Mode:
        """Whether step 1, the boundary call, can be asked: the cutter's
        where the stages hold the boundaries, `boundary_passes` (the
        review's `rounds`) is 1 or more and the cut is not held; else the
        cut is held. It says what could be asked, not what was."""
        asks = Stage.BOUNDARIES in self.review.stages and self.review.rounds >= 1
        return Mode.CUTTER if asks and not self.hold_cut else Mode.HOLD_CUT


@dataclass(frozen=True, slots=True)
class Reason:
    """Why an owner is open, and the changed block whose edit opened it
    (`origin`, a block of NEW or `deleted:<old id>`; None where no one
    edit did, as a `scope:paper` source on any edit)."""

    why: str
    origin: str | None = None


# --- step 4: what the change reopens -----------------------------------------------


def text_changed_blocks(lineage: Lineage) -> tuple[str, ...]:
    """NEW's blocks whose text changed (CHANGED, MATH, MACRO, and the
    blocks of a split or a merge), in the lineage's order."""
    return tuple(b for link in lineage.links if link.cls in CHANGED for b in link.new)


def lost_parts(sheet: es.Sheet, changed: Iterable[str]) -> list[tuple[str, str]]:
    """Each part of a changed block, as `(handle, label)`, whose words its
    new text no longer holds within one run: it stays on its heir, lost, for
    the writer to re-quote or give `removed`; the edges naming it follow that
    Δ by the rule."""
    return [
        (h, label)
        for h in dict.fromkeys(changed)
        for (target, label), words in sheet.legend.items()
        if target == h and not es.quoted(sheet, h, words)
    ]


def undone(sheet: str, owners: Collection[str]) -> str:
    """`sheet` with the done marks of `owners` (handles) taken off, and
    nothing else changed: unlike `edges_file.reopen`, no claim handle is
    written, so the carried lines stay as carry left them."""
    return "\n".join(
        line
        for line in sheet.split("\n")
        if not ((m := es.DONE.match(line)) and m.group(1) in owners)
    )


def mechanical_reopen(
    lineage: Lineage,
    records: EdgeRecords,
    cut: Cut,
    named: Mapping[str, str] | None = None,
) -> dict[str, Reason]:
    """What a text edit reopens by itself, by block or
    group id of `cut`, NEW's, over `records`, the edges carried onto it:

    - a changed or added block itself;
    - a moved block: the sources of `[position]` edges onto it, and itself
      where it has one;
    - any edit but markup: every `scope:paper` source, and the rule's
      `where-used` reasons on each changed block;
    - an insertion or a deletion: the sources of `[position]` edges onto
      the blocks beside it, and of about edges onto the blocks of its
      section (from its heading to the next);
    - markup: nothing.

    The records, not their closure graph, since a `[position]` mark is the
    edge's and the closure's links are a scorer's. `named` names
    the blocks in the reasons (the sheet's handles), else their ids."""
    found: dict[str, Reason] = {}

    def n(ident: str) -> str:
        return (named or {}).get(ident, ident)

    def add(owner: str, why: str, origin: str | None) -> None:
        found.setdefault(owner, Reason(why, origin))

    order = [b.id for b in cut.blocks]
    at = {b: i for i, b in enumerate(order)}
    for link in lineage.links:
        if link.cls in _REOPENS:
            for b in link.new:
                add(b, _REOPENS[link.cls], b)
    position = [e for e in records.edges if e.mark is Mark.POSITION]
    moved_blocks = {b for link in lineage.links if link.moved for b in link.new}
    for e in position:
        if e.target.kind is TargetKind.BLOCK and e.target.name in moved_blocks:
            add(
                e.source,
                f"its [position] edge onto {n(e.target.name)}, which moved",
                e.target.name,
            )
        if e.source in moved_blocks:
            add(e.source, "it moved, and points at a block by position", e.source)
    if any(
        link.cls not in (BlockClass.SAME, BlockClass.MARKUP) for link in lineage.links
    ):
        for e in records.edges:
            if e.target.kind is TargetKind.SCOPE:
                add(e.source, "its scope:paper edge: the paper changed", None)
        graph = from_records(cut, _with_known_parts(records))
        for b in text_changed_blocks(lineage):
            for source, e, why in rule(graph, b, edited=True).asked:
                if why.startswith("where-used:"):
                    add(
                        source,
                        f"its where-used edge onto {n(e.target)}, which {n(b)} uses, "
                        "changed",
                        b,
                    )
    # an insertion or a deletion: where it stands in NEW
    places: list[tuple[str, int, str]] = []  # (origin, index in NEW, what)
    added = {
        b for link in lineage.links if link.cls is BlockClass.ADDED for b in link.new
    }
    for b in sorted(added, key=at.__getitem__):
        places.append((b, at[b], "added"))
    olds = [o for link in lineage.links for o in link.old]
    for link in lineage.links:
        if link.cls is not BlockClass.DELETED:
            continue
        for old in link.old:
            before = _after_deleted(old, olds, lineage, at, -1)
            if before is not None:
                places.append((f"{DELETED}{old}", before + 1, "deleted"))
            else:
                after = _after_deleted(old, olds, lineage, at, 1)
                places.append(
                    (f"{DELETED}{old}", after if after is not None else 0, "deleted")
                )
    groups = {g.id: g.blocks for g in cut.groups}
    for origin, index, what in places:
        beside = _beside(order, index, added if what == "added" else set(), origin)
        section = set(_section(cut, index if what == "added" else max(index - 1, 0)))
        for e in position:
            if e.target.kind is TargetKind.BLOCK and e.target.name in beside:
                add(
                    e.source,
                    f"its [position] edge onto {n(e.target.name)}, beside a block "
                    f"{what}",
                    origin,
                )
        for e in records.edges:
            if e.kind is not EdgeKind.ABOUT:
                continue
            target = e.target.name
            held = (
                {target}
                if e.target.kind is TargetKind.BLOCK
                else set(groups.get(target, ()))
                if e.target.kind is TargetKind.GROUP
                else set()
            )
            if held & section:
                add(
                    e.source,
                    f"its about edge onto {n(target)}, in the section where a block "
                    f"was {what}",
                    origin,
                )
    return found


def _after_deleted(
    old: str, olds: list[str], lineage: Lineage, at: dict[str, int], step: int
) -> int | None:
    """The index in NEW of what the nearest kept OLD block on one side of
    `old` became: its last block before (`step` -1), its first after."""
    i = olds.index(old) + step
    while 0 <= i < len(olds):
        found = [at[b] for b in lineage.new_of(olds[i]) if b in at]
        if found:
            return max(found) if step < 0 else min(found)
        i += step
    return None


def _beside(
    order: list[str], index: int, skip: Collection[str], origin: str
) -> set[str]:
    """The blocks either side of a place in NEW's order, past the blocks of
    the same insertion (`skip`) and the inserted block itself."""
    found = set()
    i = index - 1
    while i >= 0 and (order[i] in skip or order[i] == origin):
        i -= 1
    if i >= 0:
        found.add(order[i])
    j = index if origin.startswith(DELETED) else index + 1
    while j < len(order) and (order[j] in skip or order[j] == origin):
        j += 1
    if j < len(order):
        found.add(order[j])
    return found


def _section(cut: Cut, index: int) -> list[str]:
    """The blocks of the section the block at `index` lies in: from the
    heading at or before it to the block before the next heading."""
    blocks = cut.blocks
    if not blocks:
        return []
    index = min(index, len(blocks) - 1)
    start = index
    while start > 0 and blocks[start].form is not Form.HEADING:
        start -= 1
    end = index + 1
    while end < len(blocks) and blocks[end].form is not Form.HEADING:
        end += 1
    return [b.id for b in blocks[start:end]]


def _with_known_parts(records: EdgeRecords) -> EdgeRecords:
    """`records` with an edge's parts dropped where the legend lost one
    (carry keeps such a line and reopens its source): the closure reads it
    as onto the block whole."""
    known = {(e.block, e.label) for e in records.entries}
    return dataclasses.replace(
        records,
        edges=tuple(
            e
            if all((e.target.name, p) in known for p in e.parts)
            else dataclasses.replace(e, parts=())
            for e in records.edges
        ),
    )


# --- step 5: the conditional reach ------------------------------------------------


def deleted_links(
    lineage: Lineage,
    old_edges: EdgeRecords,
    old_roles: Mapping[str, Sequence[str]],
) -> tuple[list[Link], list[tuple[str, str | None, str]]]:
    """Each deleted block's reach: OLD's edges
    onto it, each from its source's block in NEW (by the lineage: a split's
    heir, else its first) to `deleted:<old id>`, its content by OLD's
    entries' roles or the block's (`old_roles`, the identity answer's); and
    the rule's Δ `removed` on each of its parts, else on it whole; and
    OLD's qualifies edges from it, from `deleted:<old id>` to the target's
    block in NEW. An end deleted too, or a group, adds none."""
    entries = {(e.block, e.label): e for e in old_edges.entries}
    links: list[Link] = []
    rule_deltas: list[tuple[str, str | None, str]] = []
    for link in lineage.links:
        if link.cls is not BlockClass.DELETED:
            continue
        for old in link.old:
            name = f"{DELETED}{old}"
            labels = [e.label for e in old_edges.entries if e.block == old]
            rule_deltas += [(name, label, "removed") for label in labels] or [
                (name, None, "removed")
            ]
            for e in old_edges.edges:
                if (
                    e.source == old
                    and e.kind is EdgeKind.QUALIFIES
                    and e.target.kind is TargetKind.BLOCK
                    and (target := _new_block(lineage, e.target.name)) is not None
                ):
                    links.append(Link(name, e.kind.value, target, "", False, e.parts))
                if e.target.kind is not TargetKind.BLOCK or e.target.name != old:
                    continue
                source = _new_block(lineage, e.source)
                if source is None:
                    continue
                if e.claims:
                    # a named standing is asked
                    content, parts = "claim", e.parts
                elif e.parts and all((old, p) in entries for p in e.parts):
                    content = resolve_content(
                        [
                            "standard"
                            if entries[(old, p)].standard
                            else (r.value if (r := entries[(old, p)].role) else "none")
                            for p in e.parts
                        ]
                    )
                    parts = e.parts
                else:
                    content = resolve_content(list(old_roles.get(old, ())) or ["none"])
                    parts = ()
                links.append(
                    Link(
                        source,
                        e.kind.value,
                        name,
                        content,
                        e.where_used,
                        parts,
                        "where-used" if e.where_used else "",
                    )
                )
    return links, rule_deltas


def condition(impact: Impact) -> str:
    """The condition line an impact writes under its reaching edge."""
    if impact.meaning_moved:
        return es.moved_by_rule(impact.via.kind, impact.handle, impact.parts)
    return es.asked_if(impact.handle, impact.parts)


def _new_block(lineage: Lineage, old: str) -> str | None:
    """The block of NEW an OLD block became: a split's heir, else its
    first; None for a deleted block or a name the lineage lacks (a
    group)."""
    link = lineage.link_of_old(old)
    if link is None or not link.new:
        return None
    return link.heir if link.heir in link.new else link.new[0]


def records_payload(records: EdgeRecords) -> dict[str, Any]:
    """What a deleted block's reach reads of an edges answer (its entries
    and edges, by block id), as JSON: an identity answer carried from one
    that asked nothing holds them in its raw. An entry's
    words come last, for the export's `deltas[].words`."""
    return {
        "entries": [
            [e.block, e.label, e.role.value if e.role else None, e.standard, e.words]
            for e in records.entries
        ],
        "edges": [
            [
                e.source,
                e.kind.value,
                e.target.kind.value,
                e.target.name,
                list(e.parts),
                e.where_used,
            ]
            for e in records.edges
        ],
    }


def records_from_payload(payload: Mapping[str, Any]) -> EdgeRecords:
    """`records_payload` read back."""
    return EdgeRecords(
        entries=tuple(
            Entry("", None, block, label, words, Role(role) if role else None, standard)
            for block, label, role, standard, words in payload["entries"]
        ),
        edges=tuple(
            Edge(
                "",
                None,
                source,
                EdgeKind(kind),
                EdgeTarget(TargetKind(target_kind), target),
                (),
                tuple(parts),
                where_used=where_used,
            )
            for source, kind, target_kind, target, parts, where_used in payload["edges"]
        ),
    )


def conditional_reach(
    graph: EdgeGraph,
    changed: Sequence[str],
    rule_deltas: Sequence[tuple[str, str | None, str]],
) -> list[Impact]:
    """Step 5: what the rule could reach from the changed blocks, as if
    every part of each moved, and from each deleted block by its rule Δs:
    `closure.impact_reach` with every condition true."""
    deltas: dict[str, dict[str | None, str]] = {b: {None: "moved"} for b in changed}
    deleted: list[str] = []
    for name, entry, cls in rule_deltas:
        deltas.setdefault(name, {})[entry] = cls
        if name not in deleted:
            deleted.append(name)
    return impact_reach(graph, deltas, [*changed, *deleted])


# --- a person's pins, carried from OLD ---------------------------


def carried_pins(raw: Mapping[str, Any]) -> tuple[Pin, ...]:
    """OLD's person pins the identity answer carried onto NEW (`diff
    --store`), held and lapsed; none where it carried none."""
    return tuple(pin_from(p) for p in (raw.get("carried") or {}).get("pins", ()))


def pinned_from(raw: Mapping[str, Any], rebased: Cut) -> Pinned:
    """What the carried pins hold an update to: the held cut, roles and
    groups pins by NEW's ids, the edges pins of each OLD answer as the
    carried sheet and the sheet without them, on the rebased cut, so each
    later sheet finds them by carrying both, every one ranked before NEW's
    own person answers."""
    pins = carried_pins(raw)
    held = [p for p in pins if p.layer is Layer.EDGES and p.held]
    sheet = (raw.get("carried") or {}).get("sheet", "")
    # one pair per answer, ranked before every NEW answer's; a kept line
    # by its keep mark, the others by the sheet without them
    edges = tuple(
        EdgePins(
            unpinned(
                sheet,
                [p for p in held if p.rank == rank and p.kind is not PinKind.KEPT],
            ),
            marked(sheet, [p for p in held if p.rank == rank]),
            rebased,
            rank - CARRIED_RANKS,
        )
        for rank in sorted({p.rank for p in held})
    )
    return pinned_of(pins, edges, rebased)


#: How far below NEW's answers OLD's carried pins rank in store order.
CARRIED_RANKS = 1_000_000


def _unpinned_groups(
    found: Sequence[CutCorrection], pinned: Pinned, cut: Cut
) -> tuple[list[CutCorrection], list[Proposal]]:
    """Step 3's groups corrections on `cut` less those that change or
    dissolve a group a person pinned, each dropped with why: an ungroup (or attach)
    of a pinned group, and a group whose
    run makes a pinned group again or takes in one of a pinned group's
    blocks. A groups session changes a group as an ungroup and a group, so
    both are dropped."""
    order = [b.id for b in cut.blocks]
    kept: list[CutCorrection] = []
    dropped: list[Proposal] = []
    for c in found:
        held: str | None = None
        if c.group is not None and c.group in pinned.groups:
            held = c.group
        elif c.op is CutOp.GROUP and c.kind is not None and c.first is not None:
            made = f"{c.kind.value}@{c.first}"
            run = (
                order[order.index(c.first) : order.index(c.last) + 1]
                if c.first in order and c.last in order
                else [c.first]
            )
            touched = sorted(set(run) & pinned.members)
            if made in pinned.groups:
                held = made
            elif touched:
                held = ", ".join(touched)
        if held is not None:
            dropped.append(Proposal(c, 0, Stage.GROUPS.value, False, PINNED + held, ()))
        else:
            kept.append(c)
    return kept, dropped


# --- steps 1 to 3: the cut, from the cutter ----------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class Steps:
    """Steps 1 to 3 of an update, stacked on the rebased
    cut (`origin`) as `review_cut` stacks on a given cut: the cut they
    leave, the corrections that made it, step 1's proposals as weighed, the
    questions per stage (step 1's are the deciding step's, `Stage.DECIDE`),
    the keys unanswered and the run's reports. With nothing re-cut and no
    group lost, nothing is asked and `cut` is `origin`. `mode` is the
    update's (`UpdateSettings.mode`)."""

    origin: Cut
    cut: Cut
    mode: Mode = Mode.HOLD_CUT
    corrections: tuple[CutCorrection, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    questions: dict[Stage, tuple[Question, ...]] = field(default_factory=dict)
    unanswered: tuple[str, ...] = ()
    reports: tuple[RunReport, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.unanswered

    @property
    def asked(self) -> tuple[Question, ...]:
        """Every step's questions, in the order asked."""
        return tuple(q for qs in self.questions.values() for q in qs)


#: `old.md`'s head.
OLD_HEAD = (
    "# This part before the revision\n\n"
    "The blocks of the earlier version that this part of `cut.md` replaces, in\n"
    "reading order, as they were cut then, each under a line with its roles.\n"
    "Read it; do not edit it.\n"
)


def region_before(chunk: Chunk, raw: Mapping[str, Any], lineage: Lineage) -> str:
    """Step 1's `old.md` for `chunk`: the OLD blocks its blocks came from,
    and the OLD blocks deleted between them, in OLD's order, each with its
    roles; a region with no OLD block says it is new."""
    order = [b["id"] for b in raw["old_blocks"]]
    by_id = {b["id"]: b for b in raw["old_blocks"]}
    inside = set(chunk.blocks)
    came = {o for link in lineage.links if inside & set(link.new) for o in link.old}
    deleted = {o for link in lineage.links if not link.new for o in link.old}
    at = [n for n, o in enumerate(order) if o in came]
    shown = (
        [o for o in order[min(at) : max(at) + 1] if o in came or o in deleted]
        if at
        else []
    )
    if not shown:
        return OLD_HEAD + "\nNothing: this part is new in the revision.\n"
    blocks = [
        f"⟦was {', '.join(by_id[o]['roles']) or 'no role'}⟧\n{by_id[o]['text']}\n"
        for o in shown
    ]
    return OLD_HEAD + "\n" + "\n".join(blocks)


def recut_regions(
    cut: Cut, recut: Sequence[str], added: Collection[str], overlap: int
) -> tuple[Chunk, ...]:
    """Step 1's chunks, on the rebased cut: one per
    maximal run of the blocks to re-cut, where an added block's run takes
    its neighbours, one either side, as blocks of the chunk, not context
    (words appended to a claim may be
    joined to it); runs that then touch are one, and a block is never
    parted from the blocks nested in it. `overlap` blocks either side are
    context."""
    order = [block.id for block in cut.blocks]
    at = {block: n for n, block in enumerate(order)}
    unit_of: dict[int, tuple[int, int]] = {}
    start = 0
    for unit in units(cut):
        for n in range(start, start + len(unit)):
            unit_of[n] = (start, start + len(unit) - 1)
        start += len(unit)
    spans: list[list[int]] = []
    for n in sorted(at[b] for b in recut if b in at):
        lo, hi = (n - 1, n + 1) if order[n] in added else (n, n)
        lo, hi = unit_of[max(lo, 0)][0], unit_of[min(hi, len(order) - 1)][1]
        if spans and lo <= spans[-1][1] + 1:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    return tuple(
        Chunk(
            tuple(order[lo : hi + 1]),
            tuple(order[max(lo - overlap, 0) : lo]),
            tuple(order[hi + 1 : hi + 1 + overlap]),
        )
        for lo, hi in spans
    )


def cut_steps(
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    raw: Mapping[str, Any],
    rebased: Cut,
    lineage: Lineage,
    runner: Runner | None = None,
) -> Steps:
    """Steps 1 to 3, each asked where a
    runner is given and the steps before it are answered, each stored
    answer applied either way:

    1. **the boundaries**, from the cutter where the review's stages hold
       them: `decide_questions` on `recut_regions`, with no flags, its
       corrections weighed by `weigh` with the rebased cut as the origin
       (what the rebase carries is baseline), round 0;
    2. **the roles** where the stages hold them: `touched_question` on the
       blocks of the cut after step 1 that are not the rebased cut's own
       (re-cut, or joined or split by step 1), round 0; a one-to-one markup,
       maths or macro block keeps OLD's roles, since the rebase lists none
       to re-cut. Unanswered, step 1's corrections are taken back;
    3. **the groups** where the stages hold them: the groups session whole,
       pass 1, where the raw lists a group lost or a step 2 block lies in
       or next to a group run; else the rebased groups stand.

    A person's pins carried from OLD hold in each: step
    1 drops a correction that would change what a person pinned (a pinned
    start moved or removed; any change to a block pinned whole,
    roles-pinned or in a pinned group; a pinned group ungrouped), step 2
    never asks a roles-pinned block, step 3 drops a groups correction that
    changes or dissolves a pinned group or takes in one of its blocks."""
    review = settings.review
    check = checker(document)
    pinned = pinned_from(raw, rebased)
    questions: dict[Stage, tuple[Question, ...]] = {}
    reports: list[RunReport] = []
    corrections: list[CutCorrection] = []
    proposals: list[Proposal] = []
    required: list[Question] = []
    present = {block.id for block in rebased.blocks}
    recut = [b for b in raw.get("recut", ()) if b in present]
    after = rebased

    def asked(found: Sequence[Question]) -> bool:
        """`found` asked where the steps before it are settled; whether
        every one of them is answered."""
        if runner is not None and found and may_ask(required, store, reports):
            reports.append(runner.run(found, check))
        required.extend(found)
        answered = store.authoritative()
        return all(q.key in answered for q in found)

    def so_far() -> Steps:
        answered = store.authoritative()
        return Steps(
            rebased,
            after,
            settings.mode,
            tuple(corrections),
            tuple(proposals),
            questions,
            tuple(q.key for q in required if q.key not in answered),
            tuple(reports),
        )

    # step 1: the boundaries of the changed regions
    if settings.mode is Mode.CUTTER and recut:
        added = {
            b
            for link in lineage.links
            if link.cls is BlockClass.ADDED
            for b in link.new
        }
        bounded = decide_file.decide_questions(
            document,
            rebased,
            recut_regions(rebased, recut, added, review.overlap),
            settings.recut,
            (),
            review.outline,
            old=lambda chunk: region_before(chunk, raw, lineage),
            kickoff=decide_file.UPDATE_KICKOFF,
        )
        questions[Stage.DECIDE] = bounded
        if not asked(bounded):
            return so_far()
        proposed = [c for q in bounded for c in store.corrections_of(q.key)]
        proposals += weigh(
            document, rebased, [], rebased, proposed, 0, Stage.DECIDE.value, pinned
        )
        corrections += [p.correction for p in proposals if p.applied]
        if corrections:
            after = corrected_cut(document, rebased, corrections).cut

    # step 2: the roles of the blocks that are not the rebased cut's own
    touched: list[str] = []
    if Stage.ROLES in review.stages:
        stepped = set(corrected_blocks(rebased, after)) | set(recut)
        # a person's roles are never asked: a pinned block is context
        touched = [
            block.id
            for block in after.blocks
            if block.id in stepped and block.id not in pinned.roles
        ]
    if touched:
        roles_question = roles_file.touched_question(
            document, after, touched, review.templates.roles, review.overlap, 0
        )
        questions[Stage.ROLES] = (roles_question,)
        if not asked((roles_question,)):
            # taken back, as the deciding step's are: the cut stays the
            # rebased one, and the same call resumes the roles
            unwound(corrections, 0, proposals, 0, STEP_ROLES_UNANSWERED)
            after = rebased
            return so_far()
        corrections += store.corrections_of(roles_question.key)
        after = corrected_cut(document, rebased, corrections).cut

    # step 3: the groups, whole, where the change came near one
    if Stage.GROUPS in review.stages and (
        raw.get("touched") or near_group(after, touched)
    ):
        regrouped = groups_file.session_questions(
            document,
            after,
            review.chunk,
            review.templates.groups,
            review.overlap,
            1,
            lines=True,
        )
        questions[Stage.GROUPS] = regrouped
        # applied whenever answered, as a build applies its groups
        asked(regrouped)
        kept, dropped = _unpinned_groups(
            [c for q in regrouped for c in store.corrections_of(q.key)],
            pinned,
            after,
        )
        corrections += kept
        proposals += dropped
        if corrections:
            after = corrected_cut(document, rebased, corrections).cut
    return so_far()


def priced_steps(
    document: Document,
    settings: UpdateSettings,
    raw: Mapping[str, Any],
    steps: Steps,
) -> dict[Stage, tuple[Question, ...]]:
    """The steps' questions a dry run prices: those `steps` built, then
    each later step a stage holds, built on the cut they left as if the
    steps before it changed nothing (the roles of the blocks to re-cut, the
    groups where one was lost or lies near them)."""
    review = settings.review
    found = dict(steps.questions)
    present = {block.id for block in steps.cut.blocks}
    recut = [b for b in raw.get("recut", ()) if b in present]
    if Stage.ROLES not in found and Stage.ROLES in review.stages and recut:
        found[Stage.ROLES] = (
            roles_file.touched_question(
                document, steps.cut, recut, review.templates.roles, review.overlap, 0
            ),
        )
    if (
        Stage.GROUPS not in found
        and Stage.GROUPS in review.stages
        and (raw.get("touched") or near_group(steps.cut, recut))
    ):
        found[Stage.GROUPS] = groups_file.session_questions(
            document,
            steps.cut,
            review.chunk,
            review.templates.groups,
            review.overlap,
            1,
            lines=True,
        )
    return found


# --- the plan of an update ------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class Plan:
    """An update before it asks: the identity answer it starts from (its
    key and raw), NEW's rebased cut and the lineage, the carried sheet,
    every handle's id, the owners reopened (by handle, with why and
    origin), those open on condition (by handle, with their conditions),
    the impacts that made them (by handle), the rule's Δs, and session 1's
    question, None when nothing is open; `fresh` where the switch asks the
    edges fresh. OLD's edge records and the deleted blocks' links (by
    handle) are kept for the follow-up's graph. Where steps 1 to 3
    corrected the rebased cut (`steps`), `cut` is theirs, the lineage the
    identity answer's composed with theirs, the sheet carried onto their
    cut, and `raw` holds OLD's text and roles of every OLD block a step
    changed."""

    identity: str
    raw: dict[str, Any]
    cut: Cut
    lineage: Lineage
    carried: Carried
    handles: dict[str, str]
    reopened: dict[str, Reason]
    conditional: dict[str, list[str]]
    impacts: tuple[Impact, ...]
    rule_deltas: tuple[tuple[str, str | None, str], ...]
    question: Question | None
    old_edges: EdgeRecords
    deleted: tuple[Link, ...]
    fresh: bool = False
    steps: Steps | None = None
    #: A person's pins carried from OLD: what holds the
    #: update's model answers, and each pin as carried, held or lapsed.
    pinned: Pinned = field(default_factory=Pinned)
    pins: tuple[Pin, ...] = ()
    #: An update of one session: its question is the plain one,
    #: and what its answer's Δs reach is marked from `graph`, the carried
    #: edges by handle as the rule walks them, from `roots`, the changed
    #: blocks.
    single_session: bool = False
    graph: EdgeGraph | None = None
    roots: tuple[str, ...] = ()
    #: What today's edges check refuses in OLD's kept sheet, renamed onto
    #: this cut's handles (`edges_file.kept_problems`): OLD's, so no session
    #: is refused for one and no owner opened by one (`docs/model.md` § 7).
    kept_problems: tuple[str, ...] = ()

    @property
    def mode(self) -> Mode:
        """The mode its steps ran in; a plan made without them held the
        rebased cut."""
        return Mode.HOLD_CUT if self.steps is None else self.steps.mode


def lineage_from(links: Sequence[LineageLink]) -> Lineage:
    """An identity answer's lineage records as identity's `Lineage`."""
    return Lineage(
        tuple(
            LineageLinkOf(
                link.old,
                link.new,
                BlockClass(link.cls),
                Tier(link.tier),
                link.heir,
                link.moved,
            )
            for link in links
        )
    )


def rebased_cut(document: Document, raw: Mapping[str, Any]) -> Cut:
    """The identity answer's rebased cut, NEW's, as its raw holds it, named
    by the kept handles it gives, else by its words (`edges_file.named`)."""
    return edges_file.named(
        document,
        cut_from_payload(
            document, raw["cut"], CutRules(DisplayRule.IN_PARAGRAPH), roles=True
        ),
    )


def prepare(
    document: Document,
    settings: UpdateSettings,
    identity: str,
    raw: dict[str, Any],
    links: Sequence[LineageLink],
    old_edges: EdgeRecords,
    steps: Steps | None = None,
    *,
    single_session: bool = False,
    old_sheet: tuple[str, tuple[str, ...]] | None = None,
) -> Plan:
    """Steps 0 and 4 to 6 short of asking, from the identity answer
    `identity` (its raw and lineage) and OLD's edges answer's records, on
    the cut `steps` left where they corrected the rebased cut,
    else on the rebased cut. With `single_session`,
    step 6 is the update's one session on the carried sheet, asked as a
    queue where anything is open: steps 4 and 5 then only say whether
    anything is. `old_sheet`,
    OLD's kept sheet and its files, gives what today's check refuses
    there, on OLD's own files: OLD's problems, never a session's."""
    carried_raw = _carried_raw(identity, raw)
    cut = rebased_cut(document, raw) if steps is None else steps.origin
    pinned = pinned_from(raw, cut)
    lineage = lineage_from(links)
    carried = Carried(
        carried_raw["sheet"],
        tuple(carried_raw["reopened"]),
        dict(carried_raw["why"]),
        tuple(
            Hinted(h["handle"], h["excerpt"], h["hint"], h["block"])
            for h in carried_raw["hints"]
        ),
        tuple(
            CarriedExcerpt(x["block"], x["excerpt"], ExcerptClass(x["cls"]))
            for x in carried_raw["excerpts"]
        ),
        dict(carried_raw["causes"]),
        tuple(
            Gone(g["source"], g["line"], g["target"], g["old"])
            for g in carried_raw.get("gone", ())
        ),
    )
    if steps is not None and steps.corrections:
        cut, lineage, carried, raw = _onto_steps(
            document, settings, raw, steps.origin, steps.cut, lineage, carried
        )
    handles = edges_file.handles(document, cut)
    name = {ident: handle for handle, ident in handles.items()}
    records = edges_file.records(carried.sheet, handles)
    kept = (
        ()
        if old_sheet is None
        else edges_file.kept_problems(
            old_sheet[0],
            old_sheet[1],
            onto=old_handles_onto(old_sheet[0], lineage, cut, name),
            within=edges_file.nested(document, cut),
            claims_fixed=[name[b] for b in pinned.roles if b in name],
        )
    )

    # step 4: carry's reasons, then the rule's own
    reopened: dict[str, Reason] = {}
    changed = text_changed_blocks(lineage)
    own = {b for link in lineage.links if link.cls in _REOPENS for b in link.new}
    for handle in carried.reopened:
        ident = handles.get(handle)
        cause = carried.causes.get(handle)
        reopened[handle] = Reason(
            carried.why[handle],
            cause if cause is not None else name[ident] if ident in own else None,
        )
    for ident, reason in mechanical_reopen(lineage, records, cut, name).items():
        origin = reason.origin
        reopened.setdefault(
            name[ident],
            Reason(reason.why, name.get(origin, origin) if origin else None),
        )

    # a changed block's part whose words its new text no longer holds within
    # one run (`gone`): the writer quotes it as it reads now or gives it
    # `removed`; the edges naming it follow that Δ by the rule, on condition
    shown_carried = es.parse(carried.sheet)
    roots = [name[b] for b in changed]
    owed: dict[str, list[str | None]] = {}
    for h in roots:
        parts: list[str | None] = [
            label for target, label in shown_carried.legend if target == h
        ]
        owed[h] = parts or [None]
    gone = lost_parts(shown_carried, roots)

    # step 5: the conditional reach, in handles
    old_roles = {b["id"]: b["roles"] for b in raw["old_blocks"]}
    extra, rule_deltas = deleted_links(lineage, old_edges, old_roles)
    graph = _rule_graph(cut, records, lineage, old_roles, old_edges, extra, name)
    impacts = conditional_reach(graph, roots, rule_deltas)
    conditional: dict[str, list[str]] = {}
    placed: list[Placed] = []
    for impact in impacts:
        # a qualifies edge from a deleted block: no sheet line holds it, and
        # its condition, the rule's `removed`, is never false
        if impact.via.source not in handles:
            old = impact.via.source.removeprefix(DELETED)
            reopened.setdefault(
                impact.block,
                Reason(
                    f"a qualifies edge from deleted {old} moves its meaning",
                    impact.handle,
                ),
            )
    for impact in impacts:
        if impact.via.source not in handles:
            continue
        text = condition(impact)
        if impact.block not in reopened:
            held = conditional.setdefault(impact.block, [])
            if text not in held:
                held.append(text)
        e = impact.via
        placed.append(Placed(e.source, e.kind, e.target, e.parts, text))
    # an owner a gate would fix whose own lines no longer read (a quote a
    # comment now splits in a same block): opened, or no answer could pass
    for handle, why in edges_file.unreadable(
        document,
        cut,
        settings.files,
        carried.sheet,
        reopened,
        pinned.roles,
        gone,
        kept,
    ).items():
        reopened[handle] = Reason(why)
        conditional.pop(handle, None)
    order = list(handles)
    conditional = {h: conditional[h] for h in order if h in conditional}
    reopened = {h: reopened[h] for h in order if h in reopened}

    # step 6: the sheet shown and its question
    was = old_texts(raw, lineage, name)
    question = None
    fresh = False
    fraction = settings.fresh_edges_above_reopened_fraction
    if not reopened and not conditional:
        found = edges_file.sheet_problems(
            document, cut, settings.files, carried.sheet, pinned.roles, kept
        )
        if found:
            raise UpdateError(
                "nothing is open, but the sheet carried onto this version does not "
                "read on its rebased cut: " + "; ".join(found[:5])
            )
    elif single_session:
        # a block about the whole paper is read again in the session, as
        # the per-stage update's session 1 reads what `mechanical_reopen`
        # lists, rather than marked stale after it unread
        scoped = {
            name[e.source] for e in records.edges if e.target.kind is TargetKind.SCOPE
        }
        # a changed block owes its Δ and its lines whatever it is about
        own_roots = {name[b] for b in changed}
        whole_paper = [h for h in reopened if h in scoped and h not in own_roots]
        carried = dataclasses.replace(carried, sheet=undone(carried.sheet, whole_paper))
        recut = [b for b in recut_blocks(raw, cut) if b not in pinned.roles]
        from axiomation import update_queue

        question = pinned_question(
            lambda pins: edges_file.plain_update_question(
                document,
                cut,
                update_queue.brief(settings.edges),
                update_queue.KICKOFF,
                settings.files,
                carried.sheet,
                identity=identity,
                owed=list(changed),
                roles_open=[b for b in recut if b in name],
                gone=gone,
                pins=pins,
                claims_fixed=pinned.roles,
                paper=None if settings.paper is None else str(settings.paper),
                held={
                    "starts": pinned.starts,
                    "blocks": pinned.whole | pinned.roles | pinned.members,
                    "groups": pinned.groups,
                },
                kept=kept,
                graph=graph,
                roots=roots,
                rule_deltas=rule_deltas,
                moved_lines_max=settings.moved_delta_lines_max,
            ),
            pinned.edge_pins(document, cut, settings.files),
        )
    elif (
        fraction is not None
        and cut.blocks
        and _blocks_open(cut, handles, [*reopened, *conditional]) / len(cut.blocks)
        > fraction
    ):
        # the switch: so much is open that the edges are written fresh
        fresh = True
        question = edges_file.session_question(
            document,
            cut,
            settings.edges,
            settings.files,
            settings.limit,
            claims_fixed=pinned.roles,
        )
    else:
        opened = {h: r.why for h, r in reopened.items()} | {
            h: "; ".join(c) for h, c in conditional.items()
        }
        sheet = edges_file.update_sheet(
            document, cut, settings.files, carried, opened, was, placed
        )
        question = pinned_question(
            lambda pins: edges_file.update_question(
                document,
                cut,
                settings.edges,
                settings.edges_update,
                settings.files,
                sheet.sheet,
                session=1,
                reopened=edges_file.reopened_with(
                    sheet, {h: r.why for h, r in reopened.items()}, conditional
                ),
                conditional=conditional,
                owed=owed,
                graph=graph,
                rule_deltas=rule_deltas,
                identity=identity,
                hints=carried.hints,
                limit=settings.limit,
                gone=gone,
                pins=pins,
                claims_fixed=pinned.roles,
                kept=kept,
            ),
            pinned.edge_pins(document, cut, settings.files),
        )
    return Plan(
        identity,
        raw,
        cut,
        lineage,
        carried,
        handles,
        reopened,
        conditional,
        tuple(impacts),
        tuple(rule_deltas),
        question,
        old_edges,
        tuple(
            dataclasses.replace(e, source=name.get(e.source, e.source)) for e in extra
        ),
        fresh,
        steps,
        pinned,
        carried_pins(raw),
        single_session,
        graph,
        tuple(roots),
        kept,
    )


def meaning_notes(
    raw: Mapping[str, Any],
    lineage: Lineage,
    meanings: Mapping[str, str | None] | None,
) -> dict[str, str]:
    """Each block of NEW, by id, whose statement's meaning moved (its
    environment, or the declaration it reads, now means a conjecture where
    it meant a theorem), with the sentence that says so, as its words may
    not show it; `meanings`, NEW's by block id (None: none is said)."""
    if meanings is None:
        return {}
    old = {b["id"]: b for b in raw["old_blocks"]}
    found: dict[str, str] = {}
    for link in lineage.links:
        was = {old[o]["meaning"] for o in link.old if o in old and "meaning" in old[o]}
        for new in link.new:
            now = meanings.get(new)
            if was and was != {now}:
                found[new] = (
                    f"Its environment now reads as {now or 'no statement'}, "
                    f"where it read as {', '.join(sorted(was))}."
                )
    return found


def _carried_raw(identity: str, raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """The carried sheet an identity answer's raw holds, else why the
    update cannot start."""
    carried_raw = raw.get("carried")
    if carried_raw is None:
        raise UpdateError(
            f"identity answer {identity[:12]} carries no edges sheet: make it with "
            "`axiomation diff OLD NEW --cut CUT --store S` (or `update --from`)"
        )
    return carried_raw


def _onto_steps(
    document: Document,
    settings: UpdateSettings,
    raw: dict[str, Any],
    rebased: Cut,
    stepped: Cut,
    lineage: Lineage,
    carried: Carried,
) -> tuple[Cut, Lineage, Carried, dict[str, Any]]:
    """What the update reads once steps 1 to 3 corrected the rebased cut:
    the identity answer's carried sheet carried again
    onto `stepped`, within one version (a block the steps changed reopened,
    a group made or dissolved by carry's group rule, carry's reasons across
    versions kept, its unweighed cut lines too); the lineage composed with
    `identity.pair` of the two cuts, so an added block step 1 joined to a
    same neighbour is one changed block; and the raw with OLD's text and
    roles for every OLD block of a changed link that it held whole (a same
    or moved block, read off the rebased cut, which holds it unchanged).
    Carry's reasons and hints name the rebased cut's handles, which a step
    may shift (a slug's order suffix): each is taken onto `stepped` by its
    block's id (`handles_onto`), a reason on a group the steps dissolved
    dropped, since carry's group rule reopens what that group owned."""
    within = pair(Version(document, rebased), Version(document, stepped))
    onto = handles_onto(document, rebased, stepped, within)
    reopen: dict[str, str] = {}
    causes: dict[str, str] = {}
    for handle in carried.reopened:
        if (to := onto.get(handle)) is not None:
            why = carried.why[handle]
            reopen[to] = f"{reopen[to]}; {why}" if to in reopen else why
            if (cause := carried.causes.get(handle)) is not None:
                causes.setdefault(to, onto.get(cause, cause))
    recarried = edges_file.carry(
        document,
        carried.sheet,
        rebased,
        stepped,
        settings.files,
        reopen=reopen,
        cuts_weighed=False,
    )
    recarried = dataclasses.replace(
        recarried,
        hints=tuple(
            dataclasses.replace(h, handle=onto[h.handle], block=onto[h.block])
            for h in carried.hints
            if h.handle in onto and h.block in onto
        ),
        excerpts=excerpts_onto(document, carried.excerpts, rebased, stepped, within),
        causes={h: c for h, c in causes.items() if h in recarried.reopened},
        gone=tuple(
            dataclasses.replace(g, source=onto.get(g.source, g.source))
            for g in carried.gone
        ),
    )
    composed = lineage.compose(within)
    held = {b["id"] for b in raw["old_blocks"]}
    mid = {old: link.new for link in lineage.links for old in link.old}
    shown = [
        old
        for link in composed.links
        if link.cls in CHANGED
        for old in link.old
        if old not in held and mid.get(old)
    ]
    blocks = [rebased.block(mid[old][0]) for old in shown]
    return (
        stepped,
        composed,
        recarried,
        {
            **raw,
            "old_blocks": [
                *raw["old_blocks"],
                *(
                    {
                        "id": old,
                        "text": " ".join(document.text(run) for run in block.owned),
                        "roles": [role.value for role in block.roles],
                        "segments": [s.starts for s in block.segments],
                    }
                    for old, block in zip(shown, blocks, strict=True)
                ),
            ],
        },
    )


def heir_of(within: Lineage, ident: str) -> str | None:
    """The block `ident` became on the later cut of `within`: its link's
    heir, else the link's first block; None where it has none (a group's
    id, a deleted block)."""
    link = within.link_of_old(ident)
    if link is None or not link.new:
        return None
    return link.heir if link.heir in link.new else link.new[0]


def handles_onto(
    document: Document, before: Cut, after: Cut, within: Lineage
) -> dict[str, str]:
    """Each handle of `before` that names something on `after`, two cuts of
    one version (`within` their lineage), to its handle there, by id: a
    block or group `after` holds keeps its id, a block a correction changed
    goes to its heir (`heir_of`), and a group `after` does not hold has
    none. A handle is a slug with an order suffix, so a block added or
    removed before it may rename it; its id does not."""
    after_of = {ident: h for h, ident in edges_file.handles(document, after).items()}
    found: dict[str, str] = {}
    for handle, ident in edges_file.handles(document, before).items():
        target = ident if ident in after_of else heir_of(within, ident)
        if target is not None and target in after_of:
            found[handle] = after_of[target]
    return found


def excerpts_onto(
    document: Document,
    excerpts: Sequence[CarriedExcerpt],
    before: Cut,
    after: Cut,
    within: Lineage,
) -> tuple[CarriedExcerpt, ...]:
    """The excerpts carried across versions, named on `before`, each named
    on `after`, a later cut of the same version (the steps' cut, or the
    round's), as the export's handles are: a block a correction split goes
    to the part that holds the words, else its heir; a group `after` no
    longer holds to its first block, where carry leaves its lines."""
    handle_of = edges_file.handles(document, before)
    named = {ident: h for h, ident in edges_file.handles(document, after).items()}
    onto = handles_onto(document, before, after, within)
    first = {g.id: g.blocks[0] for g in before.groups}
    version = Version(document, after)
    found: list[CarriedExcerpt] = []
    for x in excerpts:
        ident = handle_of.get(x.block or "")
        link = None if ident is None else within.link_of_old(ident)
        if link is not None and link.new:
            holders = [
                b for b in link.new if es.norm(x.excerpt) in block_text(version, b)
            ]
            target = holders[0] if len(holders) == 1 else heir_of(within, ident)
            found.append(dataclasses.replace(x, block=named[target]))
        elif x.block in onto:
            found.append(dataclasses.replace(x, block=onto[x.block]))
        elif ident in first and (heir := heir_of(within, first[ident])) in named:
            found.append(dataclasses.replace(x, block=named[heir]))
        else:
            found.append(x)  # a deleted block's: no block
    return tuple(found)


def _rule_graph(
    cut: Cut,
    records: EdgeRecords,
    lineage: Lineage,
    old_roles: Mapping[str, Sequence[str]],
    old_edges: EdgeRecords,
    deleted: Sequence[Link],
    name: Mapping[str, str],
) -> EdgeGraph:
    """The graph the rule walks, by handle: `records` on NEW's cut, a
    re-cut block read with OLD's roles (`_read_as_old`), and the deleted
    blocks' links (`deleted_links`, a source by id or by handle)."""
    read_cut, read_records = _read_as_old(cut, records, lineage, old_roles, old_edges)
    graph = from_records(read_cut, _with_known_parts(read_records))
    return _by_handle(dataclasses.replace(graph, links=(*graph.links, *deleted)), name)


def _blocks_open(cut: Cut, handles: Mapping[str, str], owners: Sequence[str]) -> int:
    """How many of `owners` (handles) are blocks, its groups left out: the
    switch's share is of NEW's blocks."""
    blocks = {b.id for b in cut.blocks}
    return sum(handles.get(h) in blocks for h in owners)


def _read_as_old(
    cut: Cut,
    records: EdgeRecords,
    lineage: Lineage,
    old_roles: Mapping[str, Sequence[str]],
    old_edges: EdgeRecords,
) -> tuple[Cut, EdgeRecords]:
    """The cut and records the rule reads its contents from: a block the
    rebase left without roles (the cutter's, in a changed region, until
    a later step asks them) with the roles of the OLD blocks it came from, and a
    part of it without a role of its own with the role OLD's entry of the
    same label and words had. The sheet shown keeps the rebased cut."""
    olds_of = {b: link.old for link in lineage.links if link.old for b in link.new}
    patched: set[str] = set()
    blocks = []
    for block in cut.blocks:
        held = list(
            dict.fromkeys(
                role
                for old in olds_of.get(block.id, ())
                for role in old_roles.get(old, ())
            )
        )
        if block.roles or not held:
            blocks.append(block)
            continue
        roles = tuple(Role(r) for r in held)
        # one role is one segment; several unsegmented, a merge's shape
        segments = (Segment("", roles[0]),) if len(roles) == 1 else ()
        blocks.append(dataclasses.replace(block, roles=roles, segments=segments))
        patched.add(block.id)
    old_entries = {(e.label, es.norm(e.words)): e for e in old_edges.entries}
    entries = []
    for entry in records.entries:
        old = old_entries.get((entry.label, es.norm(entry.words)))
        if entry.block in patched and entry.role is None and old is not None:
            entry = dataclasses.replace(entry, role=old.role, standard=old.standard)
        entries.append(entry)
    return (
        dataclasses.replace(cut, blocks=tuple(blocks)),
        dataclasses.replace(records, entries=tuple(entries)),
    )


def _by_handle(graph: EdgeGraph, name: Mapping[str, str]) -> EdgeGraph:
    """The closure's graph with each block and group by its handle; a scope,
    a file and a deleted block keep their names."""

    def of(ident: str) -> str:
        return name.get(ident, ident)

    return EdgeGraph(
        blocks=tuple(of(b) for b in graph.blocks),
        groups={of(g): tuple(of(b) for b in m) for g, m in graph.groups.items()},
        links=tuple(
            dataclasses.replace(e, source=of(e.source), target=of(e.target))
            for e in graph.links
        ),
        ambient=frozenset(of(b) for b in graph.ambient),
    )


def plan(
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    identity: str | None = None,
    runner: Runner | None = None,
) -> tuple[AnswerRecord, Plan]:
    """Step 0: the identity answer that leads to NEW, by NEW's
    document hash (`identity` names one where two OLDs led to it), and the
    plan it gives, on the cut steps 1 to 3 leave (`planned`): asked where a
    runner is given, else as the store has them answered."""
    found = store.identity_for(document_hash(document), identity)
    if found is None:
        named = f" {identity[:12]}" if identity else ""
        raise UpdateError(
            f"the store holds no identity answer{named} leading to this version: "
            "give --from OLD --cut CUT, or run `axiomation diff OLD NEW --cut CUT "
            "--store S` first"
        )
    try:
        raw = versions.identity_raw(store, found)
    except json.JSONDecodeError:
        raise RecordError(
            f"identity answer {found.key[:12]}: its raw is not JSON"
        ) from None
    return found, planned(
        document, store, settings, found.key, raw, store.lineage_of(found.key), runner
    )


def planned(
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    identity: str,
    raw: dict[str, Any],
    links: Sequence[LineageLink],
    runner: Runner | None = None,
) -> Plan:
    """Steps 1 to 3 on the identity answer's rebased cut (`cut_steps`),
    then the plan on the cut they leave (`prepare`): where a step is
    unanswered, on the cut as the steps answered stand. An update of one
    session asks no step (`steps_settings`)."""
    _carried_raw(identity, raw)  # before any step is asked
    rebased = rebased_cut(document, raw)
    single = settings.single_session(len(recut_blocks(raw, rebased)))
    steps = cut_steps(
        document,
        store,
        steps_settings(settings) if single else settings,
        raw,
        rebased,
        lineage_from(links),
        runner,
    )
    old = versions.parent_by_raw(store, raw)
    try:
        old_sheet = None if old is None else versions.kept_sheet(store, old)
    except versions.VersionError as error:
        raise UpdateError(f"OLD's state {old.id if old else '?'}: {error}") from None
    return prepare(
        document,
        settings,
        identity,
        raw,
        links,
        old_edges_of(raw, store if old is None else store.seen_from(old.id)),
        steps,
        single_session=single,
        old_sheet=old_sheet,
    )


def old_handles_onto(
    sheet: str, lineage: Lineage, cut: Cut, name: Mapping[str, str]
) -> dict[str, str]:
    """Each handle of OLD's `sheet` to the handle of NEW's block or group
    that continues it, by the lineage: a block one link takes to one block
    (`name`, NEW's handle by id), and a group to the group of `cut` whose
    first block continues its first (the first the cut lists, where two
    share it).
    A block a split or a merge made has none: it is new."""
    parsed = es.parse(sheet)
    if len(parsed.block_handles) != len(parsed.block_ids):
        raise UpdateError("OLD's kept sheet names two blocks by one handle")
    id_of = dict(zip(parsed.block_handles, parsed.block_ids, strict=True))
    heir = {
        link.old[0]: link.new[0]
        for link in lineage.links
        if len(link.old) == 1 and len(link.new) == 1
    }
    onto = {
        h: name[heir[ident]]
        for h, ident in id_of.items()
        if ident in heir and heir[ident] in name
    }
    first: dict[str, str] = {}
    for g in cut.groups:
        if g.id in name:
            first.setdefault(g.blocks[0], name[g.id])
    for g, members in parsed.groups.items():
        continued = heir.get(id_of.get(members[0], "")) if members else None
        if continued in first:
            onto[g] = first[continued]
    return onto


def recut_blocks(raw: Mapping[str, Any], cut: Cut) -> list[str]:
    """The blocks of `cut` the identity answer lists to re-cut."""
    present = {block.id for block in cut.blocks}
    return [b for b in raw.get("recut", ()) if b in present]


def steps_settings(settings: UpdateSettings) -> UpdateSettings:
    """The settings steps 1 to 3 run under in an update of one session:
    no stage, so none is asked. The session writes the
    re-cut blocks' roles and proposes their cut on the rebased cut's
    groups, as the trial's did; the rounds after it ask as a build's."""
    return dataclasses.replace(
        settings, review=dataclasses.replace(settings.review, stages=())
    )


def old_edges_of(raw: Mapping[str, Any], store: RecordStore) -> EdgeRecords:
    """OLD's edge records an identity answer was carried from: the edges
    answer its `carried.from` names, or, carried from an update that asked
    nothing, the records its raw holds, since no answer in the store does."""
    carried = raw.get("carried") or {}
    if "records" in carried:
        return records_from_payload(carried["records"])
    if carried.get("from"):
        return store.edge_records_of(carried["from"])
    return EdgeRecords()


# --- step 7: the follow-up -----------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class Followup:
    """What a session's answer reveals beyond it: the
    owners no session so far opened that a next session opens, outright
    (by handle, with why and origin) or on condition (by handle, with their
    condition lines, each placed under its reaching edge, and the origin of
    the first), and what that session's gate reads: the graph of the
    answer's records (by handle), the walk's roots, the owners open so far
    (`earlier`), each with the origin it was opened from (`origins`), and
    the parts the edit removed (`gone`), whose Δ lines stand."""

    reopened: dict[str, Reason]
    conditional: dict[str, list[str]]
    conditional_from: dict[str, str | None]
    placed: tuple[Placed, ...]
    graph: EdgeGraph
    roots: tuple[str, ...]
    earlier: frozenset[str]
    origins: dict[str, str | None]
    gone: tuple[tuple[str, str], ...] = ()

    @property
    def owners(self) -> dict[str, Reason]:
        """Every owner it opens, a conditional one's conditions as its why."""
        return self.reopened | {
            h: Reason("; ".join(c), self.conditional_from.get(h))
            for h, c in self.conditional.items()
        }

    def origin_of(self, root: str) -> str | None:
        """What a walk from `root` names as its `from`: the origin `root`
        was opened from, else `root` itself."""
        return self.origins.get(root, root)


def followup(
    made: Plan,
    question: Question,
    answer: str,
    prefilter: bool = True,
    before: Followup | None = None,
) -> Followup:
    """What `answer`, the answer to the update session `question`,
    reveals beyond it, on the owners
    no session so far opened (`before`, the follow-up that session asked):

    - **the rule again**, over the graph of the answer's records, read as
      the plan reads the carried sheet's, from the session's roots, NEW's
      added blocks (changed whole: they owe no Δ) and every block with a
      Δ line, by those Δs and the rule's. A block it reaches opens
      outright where the block whose Δ decides it has one or needs none (a
      root, an added or deleted block, an owner open before), else on
      condition, its condition line under the reaching edge;
    - **the mirror check**: a uses edge B → A naming parts, shown from an
      owner the session opened and gone from its answer: each block C
      whose edge onto B quotes the words of a part of A it named, and
      which holds no edge onto A;
    - **the reverse prefilter** (`prefilter`): each part of the answer's
      legend whose words no legend line shown held; each block but its
      own whose text holds them, with no edge onto it.

    Whole-block edges are the rule's alone: the mirror check reads parts."""
    assert question.workspace is not None and question.workspace.gate is not None
    spec = json.loads(question.workspace.gate.spec)
    shown, sheet = es.parse(spec["shown"]), es.parse(answer)
    opened, true = edges_file.update_reach(spec, sheet)
    earlier = frozenset(opened) | frozenset(spec["update"].get("earlier", ()))
    origins: dict[str, str | None] = {h: r.origin for h, r in made.reopened.items()}
    if before is not None:
        origins |= before.origins | {h: r.origin for h, r in before.owners.items()}
    for impact in true:
        origins.setdefault(impact.block, origins.get(impact.origin, impact.origin))

    def closed(handle: str) -> bool:
        return handle in made.handles and handle not in earlier

    def origin(root: str) -> str | None:
        return origins.get(root, root)

    name = {ident: handle for handle, ident in made.handles.items()}
    graph = _rule_graph(
        made.cut,
        edges_file.records(answer, made.handles),
        made.lineage,
        {b["id"]: b["roles"] for b in made.raw["old_blocks"]},
        made.old_edges,
        made.deleted,
        name,
    )
    added = [
        name[b]
        for link in made.lineage.links
        if link.cls is BlockClass.ADDED
        for b in link.new
    ]
    roots = tuple(
        dict.fromkeys(
            [*spec["update"]["roots"], *added, *(d.source for d in sheet.deltas)]
        )
    )
    deltas: dict[str, dict[str | None, str]] = {}
    for d in sheet.deltas:
        if d.cls in es.IMPACT_CLASSES:
            deltas.setdefault(d.source, {})[d.label or None] = d.cls
    pseudo: list[str] = []
    for handle, entry, cls in made.rule_deltas:
        deltas.setdefault(handle, {})[entry] = cls
        if handle not in pseudo:
            pseudo.append(handle)
    impacts = [
        i for i in impact_reach(graph, deltas, [*roots, *pseudo]) if closed(i.block)
    ]

    # the rule again: outright where the deciding block's Δ is given or
    # none is owed, else on the condition of its Δ to come
    reopened: dict[str, Reason] = {}
    decided = {*deltas, *roots, *earlier}
    for impact in impacts:
        if impact.handle in decided:
            reopened.setdefault(
                impact.block,
                Reason(_reached(impact, added, deltas), origin(impact.origin)),
            )
    conditional: dict[str, list[str]] = {}
    conditional_from: dict[str, str | None] = {}
    placed: list[Placed] = []
    for impact in impacts:
        text = condition(impact)
        if impact.block not in reopened:
            held = conditional.setdefault(impact.block, [])
            if text not in held:
                held.append(text)
            conditional_from.setdefault(impact.block, origin(impact.origin))
        e = impact.via
        placed.append(Placed(e.source, e.kind, e.target, e.parts, text))

    for handle, (why, source) in edges_file.dropped_part_owners(
        shown, sheet, opened, closed
    ).items():
        reopened.setdefault(handle, Reason(why, origin(source)))
    if prefilter:
        for handle, (why, source) in edges_file.new_words_owners(
            shown, sheet, closed
        ).items():
            reopened.setdefault(handle, Reason(why, origin(source)))

    order = list(made.handles)
    return Followup(
        reopened={h: reopened[h] for h in order if h in reopened},
        conditional={
            h: conditional[h] for h in order if h in conditional and h not in reopened
        },
        conditional_from={
            h: conditional_from[h]
            for h in order
            if h in conditional and h not in reopened
        },
        placed=tuple(placed),
        graph=graph,
        roots=roots,
        earlier=earlier,
        origins={h: origins.get(h) for h in order if h in earlier},
        gone=tuple((h, label) for h, label in spec["update"].get("gone", ())),
    )


def _reached(
    impact: Impact, added: Collection[str], deltas: Mapping[str, object]
) -> str:
    """Why the rule opens a follow-up owner outright: the edge the change
    came by, and why it carries it on."""
    e, h = impact.via, impact.handle
    named = f" [{', '.join(impact.parts)}]" if impact.parts else ""
    edge = (
        f"its {e.kind} edge onto {e.target}{named}"
        if e.source == impact.block
        else f"a {e.kind} edge from {e.source}"
    )
    if h.startswith(DELETED):
        state = f"{h} is deleted"
    elif h in deltas:
        state = f"{h}'s Δ is not unchanged or reworded"
    elif h in added:
        state = f"{h} is new in this version"
    else:
        state = f"{h} has no Δ line"
    then = "moves its meaning" if impact.meaning_moved else "asks it"
    return f"{edge}, and {state}: the rule {then}"


def followup_question(
    document: Document,
    settings: UpdateSettings,
    made: Plan,
    follow: Followup,
    answer: str,
    session: int = 2,
) -> Question:
    """The follow-up session: the update question on the
    answer before it, as `update_sheet` re-renders it (its ⟦was⟧ and
    condition lines dropped, its Δ lines kept on their owners), the
    follow-up owners' done marks off and their conditions placed, keyed
    with its `session`. No text Δ is owed; the gate walks from the
    follow-up's roots and opens none of the owners open before."""
    opened = {h: r.why for h, r in follow.reopened.items()} | {
        h: "; ".join(c) for h, c in follow.conditional.items()
    }
    sheet = edges_file.update_sheet(
        document,
        made.cut,
        settings.files,
        Carried(answer, (), {}),
        opened,
        {},
        follow.placed,
    )
    return pinned_question(
        lambda pins: edges_file.update_question(
            document,
            made.cut,
            settings.edges,
            settings.edges_update,
            settings.files,
            sheet.sheet,
            session=session,
            reopened=edges_file.reopened_with(
                sheet,
                {h: r.why for h, r in follow.reopened.items()},
                follow.conditional,
            ),
            conditional=follow.conditional,
            owed={},
            graph=follow.graph,
            rule_deltas=made.rule_deltas,
            identity=made.identity,
            limit=settings.limit,
            gone=follow.gone,
            roots=follow.roots,
            earlier=follow.earlier,
            pins=pins,
            claims_fixed=made.pinned.roles,
            kept=made.kept_problems,
        ),
        made.pinned.edge_pins(document, made.cut, settings.files),
    )


# --- the run ---------------------------------------------------------------------


# --- an update of one session: what its Δs reach -------------

#: A qualifies edge the session retracted, as the walk's source: it moves
#: its target's meaning as a deleted block's qualifies edge does.
RETRACTED = "retracted:"


@dataclass(frozen=True, slots=True)
class Cause:
    """Why an owner is marked: `why` in words, the block whose change it
    carries (`origin`: a changed block, `deleted:<old id>` or
    `retracted:<handle>`), the edge it came through (`via`, as the
    sheet writes it; None where no one edge did), and the edited block the
    change started from (`root`: `origin` itself, or the block a walk to
    `origin` began at; None where no edit did: a retraction, a justification
    lost)."""

    why: str
    origin: str | None
    via: str | None = None
    root: str | None = None


@dataclass(frozen=True, slots=True)
class Marks:
    """What an update's one session leaves marked: the owners
    whose meaning the rule moved, and the owners stale, each with every
    cause that reached it, by handle in the sheet's order. A stale owner's
    lines stand until an answer re-checks it. `closed` names the blocks the
    rule would have moved whose own Δ line closed the change there, and
    `read` each block the session gave a Δ line of its own with the edit
    the move began at."""

    moved: dict[str, list[Cause]]
    stale: dict[str, list[Cause]]
    closed: tuple[str, ...] = ()
    read: dict[str, str] = field(default_factory=dict)


def _edge_text(link: Link) -> str:
    named = f" [{', '.join(link.parts)}]" if link.parts else ""
    return f"{link.source} → {link.kind} {link.target}{named}"


def as_asked(made: Plan, answer: str, document: Document) -> dict[str, str]:
    """Each handle of an update's one session's answer as the sheet it
    was asked on names its block: itself, or, for a block its
    gate's cut lines made, the block it was cut from."""
    question = made.question
    assert question is not None and question.workspace is not None
    assert question.workspace.gate is not None
    view = edges_file.plain_view(
        json.loads(question.workspace.gate.spec), answer, document
    )
    name = {ident: handle for handle, ident in made.handles.items()}
    came = edges_file.made_from(made.cut, view.cut) if view.cut is not None else {}
    return {
        h: name.get(ident) or name.get(came.get(ident, ""), h)
        for h, ident in view.handles.items()
    }


@dataclass(frozen=True, slots=True)
class MarkContext:
    """What an update's one session's marks are walked from, read once
    from its answer when it is filed (`mark_context`): the graph walked
    (the carried links, the links its answer wrote that they lack and its
    retracted qualifies edges), its handles and their block ids in the
    sheet's order, the changed blocks, the rule's Δs, the roots the walk
    starts from, its Δ lines in order (source as asked, label, class), the
    owners marked stale before the walk, the owners shown done, its own
    blocks, and the cut it was asked on. An update's step keeps it
    (`data`); a Δ corrected later walks it again (`marks_from`)."""

    graph: EdgeGraph
    handles: tuple[tuple[str, str], ...]
    made_roots: tuple[str, ...]
    rule_deltas: tuple[tuple[str, str | None, str], ...]
    roots: tuple[str, ...]
    deltas: tuple[tuple[str, str | None, str], ...]
    stale: Mapping[str, Sequence[Cause]]
    shown_done: frozenset[str]
    own: frozenset[str]
    cut: Mapping[str, Any]

    def data(self) -> dict[str, Any]:
        return {
            "graph": {
                "blocks": list(self.graph.blocks),
                "groups": {g: list(m) for g, m in self.graph.groups.items()},
                "links": [
                    [e.source, e.kind, e.target, e.content, e.where_used]
                    + [list(e.parts), e.flag]
                    for e in self.graph.links
                ],
                "ambient": sorted(self.graph.ambient),
            },
            "handles": [list(h) for h in self.handles],
            "made_roots": list(self.made_roots),
            "rule_deltas": [list(d) for d in self.rule_deltas],
            "roots": list(self.roots),
            "deltas": [list(d) for d in self.deltas],
            "stale": {
                h: [[c.why, c.origin, c.via, c.root] for c in found]
                for h, found in self.stale.items()
            },
            "shown_done": sorted(self.shown_done),
            "own": sorted(self.own),
            "cut": dict(self.cut),
        }

    @classmethod
    def of(cls, data: Mapping[str, Any]) -> MarkContext:
        return cls(
            EdgeGraph(
                tuple(data["graph"]["blocks"]),
                {g: tuple(m) for g, m in data["graph"]["groups"].items()},
                tuple(
                    Link(source, kind, target, content, where_used, tuple(parts), flag)
                    for source, kind, target, content, where_used, parts, flag in (
                        data["graph"]["links"]
                    )
                ),
                frozenset(data["graph"]["ambient"]),
            ),
            tuple((h, i) for h, i in data["handles"]),
            tuple(data["made_roots"]),
            tuple((h, e, c) for h, e, c in data["rule_deltas"]),
            tuple(data["roots"]),
            tuple((h, label, c) for h, label, c in data["deltas"]),
            {h: [Cause(*c) for c in found] for h, found in data["stale"].items()},
            frozenset(data["shown_done"]),
            frozenset(data["own"]),
            data["cut"],
        )


def _mark(held: dict[str, list[Cause]], owner: str, cause: Cause) -> None:
    if cause not in held.setdefault(owner, []):
        held[owner].append(cause)


def dependency_marks(
    made: Plan,
    answer: str,
    document: Document,
    corrected: Mapping[int, str] | None = None,
) -> Marks:
    """What the one session's answer reaches, at $0,
    over the carried edges (`made.graph`, OLD's carried onto NEW, so an
    edge the session removed still carries the change) and the edges its
    answer wrote that they lack, each Δ line's class
    as `corrected` gives it by the line's index, where a person's or an
    agent's Δ line corrected it on a later state:

    - **the rule** (`closure.impact_reach`) from the changed blocks by the
      answer's Δ lines, and from each deleted block by the rule's Δs: a
      block it moves is marked moved, one it asks stale;
    - on a changed block whose Δ carries the change (none, or one not
      `unchanged` or `reworded`): the other asked owners, each source of
      a `where-used` edge onto what it uses and of a `scope:paper` edge,
      stale;
    - **support, where the graph decides it**: what a moving block,
      or a group it lies in, justifies is stale; a `justifies` edge the
      session retracted, or a deleted block's, leaves its target stale,
      and a target no `justifies` edge reaches any more rests on nothing
      the graph holds, so the rule walks on from it; a `qualifies` edge
      retracted moves its target's meaning, from `retracted:<source>`.

    A `scope:paper` source the session was shown open read the change: its
    `scope:paper` cause is not marked.

    A retracted `uses` or `about` edge reaches nothing beyond its source.
    The changed and added blocks, and the blocks the session gave a Δ line
    of their own (each one its walk moved: a Δ that closes stops the change
    there, `closed`), are the session's own lines and are never marked."""
    return marks_from(mark_context(made, answer, document), corrected)


def mark_context(made: Plan, answer: str, document: Document) -> MarkContext:
    """`dependency_marks`'s context, read from the session's answer once."""
    assert made.graph is not None
    sheet = es.parse(answer)
    shown = es.parse(made.carried.sheet)
    name = {ident: handle for handle, ident in made.handles.items()}
    # a block its cut lines made speaks for the block it was cut from
    asked = as_asked(made, answer, document)
    deltas = tuple(
        (asked.get(d.source, d.source), d.label or None, d.cls) for d in sheet.deltas
    )
    roots = list(made.roots)
    for deleted, _, _ in made.rule_deltas:
        if deleted not in roots:
            roots.append(deleted)
    links = list(made.graph.links)
    # and the edges the answer wrote that the carried graph lacks, so what a
    # new edge carries the change to is marked too; a block its cut lines
    # made named by the block it came from
    ids = dict(made.handles) | {
        h: made.handles[a]
        for h, a in asked.items()
        if a in made.handles and h not in made.handles
    }
    try:
        records = edges_file.records(answer, ids)
    except KeyError:
        # an answer that names a block this plan does not (an answer to
        # another question, or a group its cut lines propose): its marks
        # walk the carried edges alone
        records = None
    if records is not None:
        written = _rule_graph(
            made.cut,
            records,
            made.lineage,
            {b["id"]: b["roles"] for b in made.raw["old_blocks"]},
            made.old_edges,
            made.deleted,
            name,
        )
        carried = {(x.source, x.kind, x.target, x.parts) for x in links}
        links += [
            x
            for x in written.links
            if (x.source, x.kind, x.target, x.parts) not in carried
        ]
    stale: dict[str, list[Cause]] = {}
    mark = _mark

    # the support the graph decides
    def covered(e: es.Edge, of: es.Sheet) -> frozenset[str]:
        # the claims a justifies line covers: those it names, else each of
        # its target's, so one of two lines onto one target is lost alone
        if e.kind != "justifies":
            return frozenset()
        return frozenset(e.parts) or frozenset(es.claim_handles(of, e.target))

    now: dict[tuple[str, str, str], set[str]] = {}
    for e in sheet.edges:
        key = (asked.get(e.source, e.source), e.kind, asked.get(e.target, e.target))
        now.setdefault(key, set()).update(covered(e, sheet))
    lost: list[str] = []
    for e in shown.edges:
        key = (e.source, e.kind, e.target)
        if e.kind not in ("qualifies", "justifies") or (
            key in now and covered(e, shown) <= now[key]
        ):
            continue
        if e.kind == "qualifies":
            pseudo = f"{RETRACTED}{e.source}"
            links.append(Link(pseudo, "qualifies", e.target, "", False, e.parts))
            if pseudo not in roots:
                roots.append(pseudo)
            continue
        edge = f"{e.source} → justifies {e.target}"
        mark(
            stale,
            e.target,
            Cause("its justification was retracted", e.source, edge),
        )
        lost.append(e.target)
    deleted_of = {
        old: f"{DELETED}{old}"
        for link in made.lineage.links
        if link.cls is BlockClass.DELETED
        for old in link.old
    }
    groups_now = {g.id for g in made.cut.groups}
    for e in made.old_edges.edges:
        target = _new_block(made.lineage, e.target.name)
        # a deleted block's justification, or a group's whose first block
        # was deleted (a group's id is `<kind>@<first block>`): a proof gone
        first = e.source.partition("@")[2]
        if e.source in deleted_of:
            source = deleted_of[e.source]
        elif e.source not in groups_now and first in deleted_of:
            source = f"{DELETED}{e.source}"
        else:
            continue
        if (
            e.kind is EdgeKind.JUSTIFIES
            and e.target.kind is TargetKind.BLOCK
            and target in name
        ):
            mark(
                stale,
                name[target],
                Cause(
                    "its justification was deleted",
                    source,
                    f"{source} → justifies {name[target]}",
                    source,
                ),
            )
            lost.append(name[target])
    justified = {
        asked.get(e.target, e.target) for e in sheet.edges if e.kind == "justifies"
    }
    for target in dict.fromkeys(lost):
        if target not in justified:
            mark(
                stale,
                target,
                Cause(
                    "no justifies edge reaches it now: it rests on nothing the "
                    "graph holds",
                    None,
                ),
            )
            if target not in roots:
                roots.append(target)

    own = set(made.roots) | {
        name[b]
        for link in made.lineage.links
        if link.cls is BlockClass.ADDED
        for b in link.new
        if b in name
    }
    return MarkContext(
        dataclasses.replace(made.graph, links=tuple(links)),
        tuple(made.handles.items()),
        tuple(made.roots),
        tuple(made.rule_deltas),
        tuple(roots),
        deltas,
        stale,
        frozenset(shown.done),
        frozenset(own),
        cut_payload(document, made.cut),
    )


def marks_from(
    context: MarkContext, corrected: Mapping[int, str] | None = None
) -> Marks:
    """`dependency_marks` from the session's filed context, each Δ line's
    class as `corrected` gives it by its index: today's rule over the facts
    filed, never the rule of the session's day."""
    handles = dict(context.handles)
    deltas: dict[str, dict[str | None, str]] = {}
    for i, (source, label, cls) in enumerate(context.deltas):
        cls = (corrected or {}).get(i, cls)
        if cls in es.IMPACT_CLASSES:
            held = deltas.setdefault(source, {})
            if held.get(label) in (None, *CLOSING):
                held[label] = cls
    for deleted, entry, cls in context.rule_deltas:
        deltas.setdefault(deleted, {})[entry] = cls
    roots = list(context.roots)
    stale = {h: list(c) for h, c in context.stale.items()}
    moved: dict[str, list[Cause]] = {}
    mark = _mark
    graph = context.graph
    made_roots = context.made_roots
    # an edit the change began at: a changed block or a deleted one; a
    # retraction or a lost justification is the session's, no edit
    edited = {*made_roots, *(h for h, _, _ in context.rule_deltas)}
    root_of: dict[str, str | None] = {r: r if r in edited else None for r in roots}
    for i in impact_reach(graph, deltas, roots):
        how = "its meaning moved by rule" if i.meaning_moved else "asked by the rule"
        root = i.origin if i.origin in edited else None
        cause = Cause(
            f"{how}, from {i.handle}'s change", i.handle, _edge_text(i.via), root
        )
        mark(moved if i.meaning_moved else stale, i.block, cause)
        root_of.setdefault(i.block, root)

    def carries(block: str) -> bool:
        held = deltas.get(block)
        return held is None or any(cls not in CLOSING for cls in held.values())

    moving = [b for b in roots if carries(b)] + [
        b for b in moved if b not in roots and carries(b)
    ]
    for block in moving:
        step = rule(graph, block, edited=block in made_roots)
        for target, source in step.support:
            mark(
                stale,
                target,
                Cause(
                    f"what justifies it changed: {source}",
                    block,
                    f"{source} → justifies {target}",
                    root_of.get(block),
                ),
            )
        if block not in made_roots:
            continue
        for source, e, why in step.asked:
            if e.target == SCOPE_PAPER and source not in context.shown_done:
                continue  # shown open in the session, which read it again
            if why.startswith("where-used:") or e.target == SCOPE_PAPER:
                mark(
                    stale,
                    source,
                    Cause(f"asked by the rule: {why}", block, _edge_text(e), block),
                )

    own = context.own
    # a block the session gave a Δ of its own read the edit's move: not
    # moved unread by it, though a move no edit began (a retraction) stands,
    # and what asks it (its support, a claim it uses) stands too
    read = {h for h in deltas if h in handles and h not in own}

    def unread(h: str) -> bool:
        return h not in read or any(c.root is None for c in moved[h])

    order = [h for h in handles if h not in own]
    return Marks(
        {h: moved[h] for h in order if h in moved and unread(h)},
        {h: stale[h] for h in order if h in stale},
        tuple(h for h in handles if h in read and h in moved and not carries(h)),
        {h: r for h in order if h in read and (r := root_of.get(h)) is not None},
    )


def lost_marks(made: Plan, answer: str, document: Document) -> dict[str, list[Cause]]:
    """Each source carry took an edge from because its target was deleted
    (`made.carried.gone`) where the sessions' last answer drew fewer
    edges of that kind from it, onto blocks the carried sheet did not have
    it point at, than it lost: a cause per lost edge, by handle. Which lost
    edge a new line replaces cannot be told, so each of them is marked.
    From the sessions' answer alone, as `dependency_marks`: a later
    answer clears the mark, never removes it."""
    if not made.carried.gone:
        return {}
    asked = as_asked(made, answer, document) if made.single_session else {}
    shown = es.parse(made.carried.sheet).edges
    written = [
        (asked.get(e.source, e.source), e.kind, asked.get(e.target, e.target))
        for e in es.parse(answer).edges
    ]
    lost: dict[tuple[str, str], list[Gone]] = {}
    for g in made.carried.gone:
        m = es.EDGE.match(g.line)
        assert m is not None, g.line
        lost.setdefault((g.source, m.group("kind")), []).append(g)
    found: dict[str, list[Cause]] = {}
    for (source, kind), gone in lost.items():
        before = {e.target for e in shown if (e.source, e.kind) == (source, kind)}
        instead = {
            t for s, k, t in written if (s, k) == (source, kind) and t not in before
        }
        if len(instead) >= len(gone):
            continue
        found.setdefault(source, []).extend(
            Cause(
                f"its edge onto {g.target}, deleted in this version, is gone, "
                "and the update's session drew no line in its place",
                f"{DELETED}{g.old}",
                f"{source} {g.line}",
                f"{DELETED}{g.old}",
            )
            for g in gone
        )
    return found


@dataclass(frozen=True, slots=True, eq=False)
class Update:
    """An update run: its plan, the identity answer, session 1's answer
    (None when it is unanswered or nothing was asked), the edge half's
    records (the last answered session's, or a round's re-ask's), the keys
    unanswered, and the run's reports; where session 1's answer revealed
    more, the follow-up, session 2's question and answer, and the owners
    reported, not asked (by handle: past the limit, or reached by session
    2's own answer). It also holds every question asked, per stage (the
    steps', the sessions', the rounds'), the cut corrections weighed (step
    1's and the rounds'), the rounds that applied any, the last round's
    re-ask answer where one was asked, and the rebased cut corrected by
    them all (None where nothing corrected it)."""

    plan: Plan
    identity: AnswerRecord
    answer: AnswerRecord | None
    edges: EdgeRecords
    unanswered: tuple[str, ...] = ()
    reports: tuple[RunReport, ...] = ()
    follow: Followup | None = None
    second_question: Question | None = None
    second: AnswerRecord | None = None
    not_asked: dict[str, Reason] = field(default_factory=dict)
    questions: dict[Stage, tuple[Question, ...]] = field(default_factory=dict)
    proposals: tuple[Proposal, ...] = ()
    cut_rounds: tuple[CutRound, ...] = ()
    reasked: AnswerRecord | None = None
    corrected: Corrected | None = None
    #: The person rounds on NEW, after the round.
    person: PersonRounds = PersonRounds()
    #: An update of one session whose queue applied its cut lines:
    #: its sheet and the cut it stands on, with its roles, which the edge
    #: half stands on unless a person's answer re-asked it.
    carried_onto: tuple[str, Cut] | None = None
    #: The blocks the one session's queue served again at its end, the
    #: check finding a problem of each: blocks its draw missed.
    mended: tuple[str, ...] = ()
    #: The texts of the answers the run held, by key: what its export
    #: reads (`sheet`).
    sheets: Mapping[str, str] = field(default_factory=dict)

    #: The store's run directory, where a replay in develop mode reads the
    #: texts its run did not hold (`run_state.text_in`).
    directory: Path | None = None

    def sheet(self, answer: AnswerRecord) -> str:
        """An answer's text as the run held it (`run_state.text_in`)."""
        return text_in(answer, self.sheets, self.directory)

    def session_facts(self, document: Document) -> dict[str, Any] | None:
        """What a later read takes from its sessions' answers, read from
        them once, for its step to keep: the one session's marks' context
        (`MarkContext`), and the follow-up a first session's answer
        revealed, its owners by handle with why and origin. None where no
        session was answered."""
        if self.answer is None:
            return None
        found: dict[str, Any] = {}
        if self.plan.single_session and self.plan.graph is not None:
            found["marks"] = mark_context(
                self.plan, self.sheet(self.answer), document
            ).data()
        if self.follow is not None:
            found["followup"] = {
                h: [r.why, r.origin] for h, r in self.follow.owners.items()
            }
        return found

    @property
    def complete(self) -> bool:
        return not self.unanswered

    @property
    def last(self) -> AnswerRecord | None:
        """The last update session's answer: its Δ lines are the update's,
        and its sheet is the edge half unless a round re-asked it."""
        return self.second or self.answer

    @property
    def edge_answer(self) -> AnswerRecord | None:
        """The answer the edge half is: a round's re-ask, else the last
        session's (None where nothing was asked)."""
        return self.reasked or self.last

    @property
    def stopped(self) -> RunReport | None:
        return next((r for r in self.reports if r.stopped is not None), None)


def run(
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    runner: Runner | None = None,
    *,
    identity: str | None = None,
    applied_before: Collection[str] = (),
    run: RunFile | None = None,
) -> Update:
    """A text edit's update: steps 1 to 3 and the plan on the cut
    they leave, then session 1 asked where a runner is given and anything
    is open, then session 2 where its answer reveals owners no session
    opened and `followup_sessions_max` allows (else they are reported, not
    asked), then the round on the last edges answer. Without a
    runner it asks nothing: the update from the store alone, which must
    equal the run's. An unanswered question at any step leaves the update
    not whole; the same call resumes it, each later key recomputed from the
    answers stored before it. `applied_before`, the given answers the state it
    starts from applied, are not applied again: where that state is of
    NEW's own version (its vocabulary map edited), its cut holds them."""
    found, made = plan(document, store, settings, identity, runner)
    steps = made.steps
    assert steps is not None
    reports = list(steps.reports)
    if not steps.complete:
        return Update(
            made,
            found,
            None,
            EdgeRecords(),
            steps.unanswered,
            tuple(reports),
            questions=dict(steps.questions),
            proposals=steps.proposals,
        )
    sessions = _sessions(document, store, settings, runner, found, made, reports, run)
    done = _round(document, store, settings, runner, sessions, applied_before, run)
    return dataclasses.replace(
        done,
        sheets={} if run is None else run.state.texts,
        directory=store.run_directory,
    )


def _sessions(
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    runner: Runner | None,
    found: AnswerRecord,
    made: Plan,
    reports: list[RunReport],
    run: RunFile | None = None,
) -> Update:
    """Sessions 1 and 2 (steps 6 and 7) on the plan, after the steps'
    `reports`."""
    question = made.question
    if question is None:
        edges = _carried_records(made)
        return Update(made, found, None, edges, reports=tuple(reports))
    answered = question.key in store.authoritative()
    mended: tuple[str, ...] = ()
    if runner is not None and made.single_session:
        # an answered question is not asked again, as a runner skips it
        if not answered:
            if settings.edges_queue is None:
                raise UpdateError(
                    "an update of one session is asked as a queue, which runs on "
                    "claude-code alone: seat the profile's edges on claude-code, "
                    "or unset per_stage_update_above_recut_blocks to update per "
                    "stage"
                )
            report, mended = _queued(document, store, settings, runner, made, question)
            reports.append(report)
    elif runner is not None:
        reports.append(runner.run((question,), checker(document)))
    answer = store.authoritative().get(question.key)
    if answer is None:
        return Update(
            made,
            found,
            None,
            EdgeRecords(),
            (question.key,),
            tuple(reports),
            mended=mended,
        )
    if made.fresh or made.single_session:
        # no follow-up: fresh edges hold no carried line; one session marks
        # what its Δs reach stale instead of asking it
        return Update(
            made,
            found,
            answer,
            store.edge_records_of(question.key),
            (),
            tuple(reports),
            mended=mended,
        )
    follow = followup(
        made,
        question,
        text_of(answer, run, store.run_directory),
        settings.reverse_prefilter,
    )
    if not follow.owners or settings.followup_sessions_max < 1:
        return Update(
            made,
            found,
            answer,
            store.edge_records_of(question.key),
            (),
            tuple(reports),
            follow,
            not_asked=follow.owners,
        )
    second_question = followup_question(
        document, settings, made, follow, text_of(answer, run, store.run_directory)
    )
    if runner is not None:
        reports.append(runner.run((second_question,), checker(document)))
    second = store.authoritative().get(second_question.key)
    if second is None:
        return Update(
            made,
            found,
            answer,
            EdgeRecords(),
            (second_question.key,),
            tuple(reports),
            follow,
            second_question,
        )
    # one follow-up session at most: what its own answer reaches is reported
    beyond = followup(
        made,
        second_question,
        text_of(second, run, store.run_directory),
        settings.reverse_prefilter,
        follow,
    )
    return Update(
        made,
        found,
        answer,
        store.edge_records_of(second_question.key),
        (),
        tuple(reports),
        follow,
        second_question,
        second,
        beyond.owners,
    )


def old_texts(
    raw: Mapping[str, Any], lineage: Lineage, name: Mapping[str, str]
) -> dict[str, list[str]]:
    """Each changed block's text in OLD, by its handle (`name`, id to
    handle): the texts of the OLD blocks it came from."""
    blocks_of = {b["id"]: b for b in raw["old_blocks"]}
    return {
        name[b]: [es.norm(blocks_of[o]["text"]) for o in link.old if o in blocks_of]
        for link in lineage.links
        if link.cls in CHANGED
        for b in link.new
        if b in name
    }


def _queue_sessions(
    store: RecordStore, answer: AnswerRecord
) -> dict[str, dict[str, Any]]:
    """The sessions of `answer`'s queue run, each with its cost, where a
    queue gave it: a queue's answer costs nothing itself, its sessions bear
    it."""
    from axiomation import update_queue
    from axiomation.runner.queued import SESSION_KIND

    if answer.host != update_queue.HOST or not answer.session:
        return {}
    return {
        a.key: {"stage": a.stage, "cost": a.cost}
        for a in store.answers()
        if a.kind == SESSION_KIND and (a.session or "").startswith(answer.session)
    }


def _queued(
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    runner: Runner,
    made: Plan,
    question: Question,
) -> tuple[RunReport, tuple[str, ...]]:
    """The one session's question asked as a queue, standing on the
    question: the changed and added blocks, the blocks their Δs move, and
    every other owner the update reopened, read again with why. Its report,
    and the blocks it served again at its end (`update_queue.Done`)."""
    from axiomation import update_queue

    assert settings.edges_queue is not None
    done = update_queue.ask(
        question,
        document,
        store,
        queue_shown(made, question),
        settings.edges,
        runner,
        settings.edges_queue.hosts,
        settings.edges_queue.context_tokens,
    )
    return update_queue.as_run_report(question, done), done.mended


def queue_shown(made: Plan, question: Question) -> update_queue.Shown:
    """What the one session's queue shows beside the carried sheet."""
    from axiomation import update_queue

    assert question.workspace is not None and question.workspace.gate is not None
    changed = set(json.loads(question.workspace.gate.spec)[edges_file.PLAIN]["owed"])
    name = {ident: handle for handle, ident in made.handles.items()}
    reread = {
        h: {"why": r.why, "origin": name.get(r.origin or "", r.origin)}
        for h, r in made.reopened.items()
        # a block the edit added is written, not read again
        if made.handles.get(h) not in changed and r.origin != h
    }
    # every OLD block NEW holds no block of: deleted, or merged into one
    held = {o for link in made.lineage.links if link.new for o in link.old}
    gone = {
        f"{DELETED}{b['id']}": es.norm(b["text"])
        for b in made.raw["old_blocks"]
        if b["id"] not in held
    }
    added = [
        name[b]
        for link in made.lineage.links
        if link.cls is BlockClass.ADDED
        for b in link.new
        if b in name
    ]
    notes = meaning_notes(
        made.raw, made.lineage, {b.id: b.meaning for b in made.cut.blocks}
    )
    return update_queue.Shown(
        reread,
        old_texts(made.raw, made.lineage, name),
        gone,
        added,
        {name[b]: note for b, note in notes.items() if b in name},
        tuple(made.raw.get("vocabulary") or ()),
        hints_of(made.carried),
        lost_of(made.carried),
    )


def queue_tasks(made: Plan, question: Question) -> int:
    """The tasks the one session's queue serves first: each changed or
    added block and each owner to read again; the blocks the answers move
    by rule, or split, come after and are not counted."""
    assert question.workspace is not None and question.workspace.gate is not None
    name = {ident: handle for handle, ident in made.handles.items()}
    owed = json.loads(question.workspace.gate.spec)[edges_file.PLAIN]["owed"]
    shown = queue_shown(made, question)
    return len({name.get(b, b) for b in owed} | set(shown.added) | set(shown.reread))


def lost_of(carried: Carried) -> dict[str, list[tuple[str, str]]]:
    """Each block's edge lines carry dropped onto a block the edit deleted,
    by its handle: the line, and the deleted block by its `deleted:` name,
    as the queue serves it."""
    found: dict[str, list[tuple[str, str]]] = {}
    for g in carried.gone:
        found.setdefault(g.source, []).append((g.line, f"{DELETED}{g.old}"))
    return found


def hints_of(carried: Carried) -> dict[str, list[tuple[str, str, str]]]:
    """Carry's hints by the block they reopened: each excerpt a line
    quoted, and where similar words now stand (the words, the block)."""
    found: dict[str, list[tuple[str, str, str]]] = {}
    for h in carried.hints:
        hint = (es.norm(h.excerpt), h.words, h.block)
        if hint not in found.setdefault(h.handle, []):
            found[h.handle].append(hint)
    return found


def _round(
    document: Document,
    store: RecordStore,
    settings: UpdateSettings,
    runner: Runner | None,
    update: Update,
    applied_before: Collection[str] = (),
    run: RunFile | None = None,
) -> Update:
    """Step 8: `driver.run_cut_rounds` on the last edges
    answer, up to `cut_rounds`, the rebased cut the origin, so a correction
    the rebase carried is baseline and changing it is a first flip; its
    roles and re-ask join the update's questions, and a round left
    unanswered leaves the update not whole, as it leaves a build's review.
    Then the person rounds on NEW (`driver.run_person_rounds`),
    whose re-asks join them too. The carried pins hold in both. `update`
    gains every question asked and the corrections weighed."""
    made = update.plan
    steps = made.steps
    assert steps is not None
    review = dataclasses.replace(settings.review, files=settings.files)
    check = checker(document)
    questions = dict(steps.questions)
    sessions = tuple(q for q in (made.question, update.second_question) if q)
    if sessions:
        questions[Stage.EDGES] = sessions
    corrections = list(steps.corrections)
    proposals = list(steps.proposals)
    reports = list(update.reports)
    required = [*steps.asked, *sessions]
    last = update.last
    reasked = None
    cut_rounds: tuple[CutRound, ...] = ()
    edges, answer = update.edges, last
    # the sheet the edge half stands on where no answer is it: the carried
    # one (nothing asked), or an update's one session's, on the cut its
    # queue's applied cut lines left
    onto: tuple[str, Cut] | None = None
    applied: tuple[str, Cut] | None = None
    # edges written fresh hold no carried line: their pins lapsed (reported
    # `lapsed (written fresh)`), and a gate that held them would deadlock
    pinned = dataclasses.replace(made.pinned, edges=()) if made.fresh else made.pinned
    if update.complete and last is not None:
        asked = next(q for q in sessions if q.key == last.key)
        assert asked.input_cut is not None
        asked_on = asked.input_cut
        if made.single_session:
            # the cut lines its queue applied while it ran, splits first
            #: the answer stands on the cut they leave
            stood = _session_cut(
                document,
                store,
                made,
                last,
                text_of(last, run, store.run_directory),
                corrections,
                proposals,
            )
            if stood is not None:
                asked_on = stood
                cut_rounds = (CutRound(1, corrected_blocks(made.cut, stood), ()),)
        # the role corrections the last session's standings imply,
        # and an update's one session's roles written, applied
        # with it, as a build applies its last pass's
        claims_applied(
            document,
            steps.origin,
            store,
            last,
            asked_on,
            0,
            corrections,
            proposals,
            roles_held=pinned.roles,
        )
        if made.single_session:
            edges = store.edge_records_of(last.key)
            if cut_rounds:
                # the edge half is its sheet, on the cut it stands on
                final = corrected_cut(document, steps.origin, corrections).cut
                onto, answer = (text_of(last, run, store.run_directory), final), None
                applied = onto
        else:
            _, edges, answer, cut_rounds = run_cut_rounds(
                document,
                steps.origin,
                store,
                review,
                runner,
                check,
                questions=questions,
                corrections=corrections,
                proposals=proposals,
                reports=reports,
                required=required,
                after=made.cut,
                edges=update.edges,
                edges_answer=last,
                pinned=pinned,
                run=run,
            )
    rounds = PersonRounds()
    if update.complete:
        if onto is None and answer is None:
            onto = (made.carried.sheet, made.cut)
        edges, answer, rounds = run_person_rounds(
            document,
            steps.origin,
            store,
            review,
            runner,
            check,
            questions=questions,
            corrections=corrections,
            proposals=proposals,
            reports=reports,
            required=required,
            edges=edges,
            edges_answer=answer,
            carried=onto if answer is None else None,
            pinned=pinned,
            applied_before=applied_before,
            run=run,
        )
    if answer is not None and (last is None or answer.key != last.key):
        reasked = answer
        update = dataclasses.replace(update, edges=edges)
    elif applied is not None:
        update = dataclasses.replace(update, edges=edges, carried_onto=applied)
    answered = store.authoritative()
    return dataclasses.replace(
        update,
        unanswered=tuple(
            dict.fromkeys(
                [
                    *update.unanswered,
                    *(
                        q.key
                        for qs in questions.values()
                        for q in qs
                        if q.key not in answered
                    ),
                ]
            )
        ),
        reports=tuple(reports),
        questions=questions,
        proposals=tuple(proposals),
        cut_rounds=cut_rounds,
        reasked=reasked,
        corrected=(
            corrected_cut(document, steps.origin, corrections) if corrections else None
        ),
        person=rounds,
    )


def _session_cut(
    document: Document,
    store: RecordStore,
    made: Plan,
    answer: AnswerRecord,
    sheet: str,
    corrections: list[CutCorrection],
    proposals: list[Proposal],
) -> Cut | None:
    """The cut an update's one session's answer stands on: its
    queue applied the session's cut lines while it ran (`Applied:` lines,
    `update_queue.Updating.apply`), so they are the answer's first cut
    corrections in the store, applied here, round 1, as they were in the
    session; any cut line still in the sheet came after the queue's last
    apply and is listed, not applied. Returns the cut the answer stands on,
    None where nothing applied (it stands on the cut it was asked on)."""
    question = made.question
    assert question is not None and question.workspace is not None
    assert question.workspace.gate is not None
    spec = json.loads(question.workspace.gate.spec)
    view = edges_file.plain_view(spec, sheet, document)
    stored = cut_proposals(store, answer)
    count = len(view.applied)
    for c in stored[:count]:
        corrections.append(c)
        proposals.append(
            Proposal(
                c, 1, answer.stage, True, None, _named_on(made.cut, c), (), by=BY_EDGES
            )
        )
    proposals += [
        Proposal(
            c,
            1,
            answer.stage,
            False,
            "not applied: written after the session's queue applied its cut lines",
            _named_on(view.cut or made.cut, c),
            by=BY_EDGES,
        )
        for c in stored[count:]
    ]
    return view.cut


def _named_on(cut: Cut, correction: CutCorrection) -> tuple[Any, ...]:
    """The blocks of `cut` a correction names, in its order."""
    ids = (correction.block,) if correction.block is not None else correction.blocks
    by_id = {block.id: block for block in cut.blocks}
    return tuple(by_id[i] for i in ids if i in by_id)


def asked_as(update: Update) -> dict[str, str]:
    """Each question an update asked, by key, as its summary names it: the
    step (`decide, step 1`, `roles, step 2`, `groups, step 3`), the
    session (`edges, session 1`), the round (`roles, round 1`, `edges,
    round 1`) or the person round (`edges, person round 1`)."""
    made = update.plan
    steps = made.steps
    found: dict[str, str] = {}
    step_of = {Stage.DECIDE: 1, Stage.ROLES: 2, Stage.GROUPS: 3}
    for stage, questions in (steps.questions if steps else {}).items():
        for q in questions:
            found[q.key] = f"{stage.value}, step {step_of[stage]}"
    for n, q in enumerate((made.question, update.second_question), 1):
        if q is not None:
            found[q.key] = f"edges, session {n}"
    person = update.person
    stage_of = {
        q.key: stage.value for stage, qs in update.questions.items() for q in qs
    }
    for done in (*person.applied, *([person.pending] if person.pending else [])):
        for key in done.asked:
            found[key] = f"{stage_of.get(key, 'edges')}, person round {done.number}"
    rounds = {stage: 0 for stage in (Stage.ROLES, Stage.EDGES)}
    for stage in rounds:
        for q in update.questions.get(stage, ()):
            if q.key not in found:
                rounds[stage] += 1
                found[q.key] = f"{stage.value}, round {rounds[stage]}"
    return found


def _carried_records(made: Plan) -> EdgeRecords:
    """The carried sheet's records, when nothing was asked: numbered under
    the identity answer's key, as the resolver numbers an answer's."""
    found = edges_file.records(made.carried.sheet, made.handles)
    count = iter(range(1, len(found.ids) + 1))

    def numbered(records: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(
            dataclasses.replace(
                r,
                id=f"{made.identity[:12]}-carried-{next(count)}",
                answer=made.identity,
            )
            for r in records
        )

    return EdgeRecords(
        *(numbered(getattr(found, f.name)) for f in dataclasses.fields(found))
    )


# --- the export ------------------------------------------------------------------


#: What changed in a carried block whose segment start was not re-found,
#: by its link's class: a same or moved block's hidden text (a comment
#: between a start's words), else its markup, maths or macro.
_DROPPED_AFTER = {
    "same": "its hidden text changed",
    "moved": "its hidden text changed",
    "markup": "a markup edit",
    "math": "a maths edit",
    "macro": "a macro change",
}


def dropped_start_flags(raw: Mapping[str, Any]) -> tuple[Flag, ...]:
    """A rule flag `part` on each block whose segment start the rebase did
    not re-find once in a block it carried with OLD's roles (a 1:1
    MARKUP, MATH or MACRO block keeps OLD's roles; a
    same or moved block whose hidden text changed alike): the block kept
    its role set, the boundary was dropped, and a later step may re-ask
    it. Its words are OLD's, so the export may not place them."""
    return tuple(
        Flag(
            id=f"rule-{Signal.PART.value}-{n}",
            answer=None,
            blocks=(d["block"],),
            signal=Signal.PART,
            evidence=(d["start"],),
            note="segment start not re-found after "
            + _DROPPED_AFTER.get(d["cls"], d["cls"]),
        )
        for n, d in enumerate(raw.get("dropped_starts", ()), 1)
    )


def update_payload(
    document: Document, update: Update, store: RecordStore
) -> dict[str, Any]:
    """A whole update's export: NEW's rebased cut,
    corrected by the steps and rounds where they applied any, its
    `paper_version`, the edge half (the last session's, or a round's
    re-ask's), and `review` with `update`; nothing a replay could not
    give."""
    if not update.complete:
        raise UpdateError("an update that is not whole has no export")
    made = update.plan
    question = made.question
    last = update.last
    answer = update.edge_answer or update.identity
    edges_question = next(
        (
            q
            for q in update.questions.get(Stage.EDGES, ())
            if update.reasked is not None and q.key == update.reasked.key
        ),
        None,
    )
    edge_cut = made.cut if edges_question is None else edges_question.input_cut
    edge_handles = made.handles if edges_question is None else edges_question.handles
    if edges_question is None and update.carried_onto is not None:
        # the one session's sheet carried onto the cut its cut lines left
        edge_cut = update.carried_onto[1]
        edge_handles = edges_file.handles(document, edge_cut)
    assert edge_cut is not None
    edge_half = edges_payload(
        document,
        edge_cut,
        update.edges,
        answer,
        edge_handles,
        _first_authors(update, store),
    )

    if made.fresh:
        # every block was asked; the owners that made the switch fire
        opened = {h: r.why for h, r in made.reopened.items()} | {
            h: "; ".join(c) for h, c in made.conditional.items()
        }
        origins = {h: r.origin for h, r in made.reopened.items()}
        reopened = [
            {"handle": h, "why": opened[h], "session": 1, "from": origins.get(h)}
            for h in made.handles
            if h in opened
        ]
    elif made.single_session:
        reopened = _touched(
            made, None if update.answer is None else update.sheet(update.answer)
        )
    else:
        reopened = _opened(
            made.reopened,
            question,
            None if update.answer is None else update.sheet(update.answer),
            1,
        )
    marks = (
        dependency_marks(made, update.sheet(update.answer), document)
        if made.single_session and update.answer is not None
        else None
    )
    follow = update.follow
    if follow is not None and update.second is not None:
        reopened += _opened(
            follow.reopened,
            update.second_question,
            update.sheet(update.second),
            2,
            follow,
        )
    final = update.corrected or Corrected(made.cut, (), ())
    # a round that applied corrections changed the cut the sessions wrote
    # on: the lineage, the excerpts and the Δs are taken onto its cut
    lineage, excerpts, within = made.lineage, made.carried.excerpts, None
    named = made.handles
    if update.cut_rounds or update.person.applied:
        within = pair(Version(document, made.cut), Version(document, final.cut))
        lineage = made.lineage.compose(within)
        excerpts = excerpts_onto(document, excerpts, made.cut, final.cut, within)
        named = edges_file.handles(document, final.cut)
    handle_of = {ident: handle for handle, ident in named.items()}
    onto = sessions_onto(made.handles, named, within)
    reopened = [
        {**r, "handle": onto(r["handle"]), "from": onto(r["from"])} for r in reopened
    ]
    # the Δs are the last session's, whatever a round re-asked after it
    written_records = (
        store.edge_records_of(last.key) if last is not None else update.edges
    )
    # a part's anchor words: as the session wrote them, else as carried (a
    # part whose legend line the writer deleted, its Δ `removed`)
    words_of = {
        (e.block, e.label): e.words
        for e in edges_file.records(made.carried.sheet, made.handles).entries
    } | {(e.block, e.label): e.words for e in written_records.entries}
    old_words = {(e.block, e.label): e.words for e in made.old_edges.entries}
    # a round's re-ask may have renamed a part (carry's `label-2`) or moved
    # it to a block the round changed: a Δ names the part as the edge half
    # exports it, found by its words among the blocks its own became
    exported = update.edges.entries if update.reasked is not None else ()
    edge_handle_of = {ident: handle for handle, ident in edge_handles.items()}

    def writer_delta(d: Delta) -> dict[str, Any]:
        words = None if d.entry is None else words_of.get((d.block, d.entry))
        block, entry = d.block, d.entry
        if within is not None:
            link = within.link_of_old(d.block)
            heirs = link.new if link is not None else ()
            block = heir_of(within, d.block) or block
            if words is not None:
                said = es.norm(words)
                block, entry = next(
                    (
                        (e.block, e.label)
                        for e in exported
                        if e.block in heirs and e.words and es.norm(e.words) == said
                    ),
                    (block, entry),
                )
        return {
            "block": block,
            "handle": handle_of.get(block, block),
            "entry": entry,
            "words": words,
            "class": d.cls.value,
            "note": d.note,
            "by": "writer",
        }

    deltas = [writer_delta(d) for d in written_records.deltas] + [
        {
            "block": name,
            "handle": name,
            "entry": entry,
            "words": (
                None
                if entry is None
                else old_words.get((name.removeprefix(DELETED), entry)) or None
            ),
            "class": cls,
            "note": "deleted in this version",
            "by": "rule",
        }
        for name, entry, cls in made.rule_deltas
        if not made.fresh
    ]
    written = (
        [
            es.norm(line)
            for lines in edges_file.written_lines(update.sheet(last)).values()
            for line in lines
        ]
        if last is not None
        else []
    )
    # the answers after the session: what they re-read, the Δs a person's
    # or an agent's corrected, and the marks recomputed from them
    answered = store.authoritative()
    record: dict[str, Any] = {"deltas": deltas}
    if made.single_session:
        record |= marks_entries(marks, onto)
        # a block read by its own Δ names the edit its move began at
        rooted = {onto(h): onto(r) for h, r in (marks.read if marks else {}).items()}
        record["deltas"] = [
            {**d, "root": rooted[d["handle"]]}
            if d["by"] == "writer" and d["handle"] in rooted
            else d
            for d in deltas
        ]
    last_session = update.second or update.answer
    if last_session is not None and (
        lost := lost_marks(made, update.sheet(last_session), document)
    ):
        record["lost"] = marks_entries(Marks({}, lost), onto)["stale"]
    name = {ident: h for h, ident in made.handles.items()}
    session_raw = update.sheet(update.answer) if update.answer is not None else None
    # the update's own sessions (a per-stage update's follow-up among them)
    # are no answers after it
    sessions = {q.key for q in (made.question, update.second_question) if q}

    def owner(ident: str) -> str | None:
        return handle_of.get(ident) or onto(name.get(ident))

    def remark(corrected: Mapping[int, str]) -> dict[str, list[Any]]:
        assert session_raw is not None
        found = dependency_marks(made, session_raw, document, corrected)
        return marks_entries(found, onto)

    first = dict(record)
    record, passes = folded(
        record,
        [
            (q, answered[q.key], update.sheet(answered[q.key]))
            for q in update.questions.get(Stage.EDGES, ())
            if q.key not in sessions
        ],
        store,
        owner,
        lambda q: blocks_onto(document, q.input_cut, final.cut),
        remark if made.single_session and session_raw is not None else None,
    )
    hints = [
        {
            "excerpt": h.excerpt,
            "hint": h.words,
            "kept": any(es.norm(h.words) in line for line in written),
        }
        for h in made.carried.hints
    ]
    asked = [q for qs in update.questions.values() for q in qs]
    # each queue's sessions after its answer, so what a later round adds
    # follows them, as it does on replay
    cost: dict[str, dict[str, Any]] = {}
    for a in (update.identity, *(answered.get(q.key) for q in asked)):
        if a is not None:
            cost[a.key] = {"stage": a.stage, "cost": a.cost}
            cost |= _queue_sessions(store, a)
    counts = {stage.value: len(qs) for stage, qs in update.questions.items()}
    counts.setdefault(Stage.EDGES.value, 0)
    # the steps' and the rounds' roles and groups flags, then the edge half's
    # a person's edge half raises no flag: the last model's stand
    flagged = update.person.flags_from or update.edge_answer
    flag_cut = edge_cut
    if flagged is not None and flagged is not update.edge_answer:
        flag_cut = next(
            q.input_cut
            for q in update.questions.get(Stage.EDGES, ())
            if q.key == flagged.key
        )
    raised_flags = [
        *flags_raised(store, update.questions, (Stage.ROLES, Stage.GROUPS)),
        *(
            Raised(flag, Stage.EDGES.value, flag_cut)
            for flag in (store.flags_of(flagged.key) if flagged else ())
        ),
    ]
    review: dict[str, Any] = {
        "complete": True,
        "cut": "rebased",
        "templates": {q.template.name: q.template.version for q in asked},
        "questions": counts,
        "unanswered": [],
        **cut_corrections_payload(document, update.proposals),
        "flags": [
            *(
                flag_payload(
                    document,
                    raised,
                    edge_handle_of,
                    decided_by(raised, update.proposals),
                )
                for raised in raised_flags
            ),
            *(
                flag_payload(document, Raised(flag, IDENTITY, final.cut), handle_of)
                for flag in dropped_start_flags(made.raw)
                if set(flag.blocks) <= {b.id for b in final.cut.blocks}
            ),
        ],
        "update": update_review(
            old=made.raw["old"],
            new=made.raw["new"],
            previous={
                block: list(link.old) for link in lineage.links for block in link.new
            },
            mode=made.mode.value,
            fresh=made.fresh,
            reopened=reopened,
            not_asked=[
                {"handle": onto(h), "why": r.why, "from": onto(r.origin)}
                for h, r in update.not_asked.items()
            ],
            deltas=record["deltas"],
            hints=hints,
            excerpts=[
                {"block": x.block, "excerpt": x.excerpt, "cls": x.cls.value}
                for x in excerpts
            ],
            cost=cost,
            **(
                {
                    "shape": "single-session",
                    "moved_by_rule": record["moved_by_rule"],
                    "stale": record["stale"],
                    "closed_by_delta": record.get("closed_by_delta", ()),
                    "reread": record.get("reread", ()),
                }
                if made.single_session
                else {"reread": record.get("reread", ())}
            ),
            lost=record.get("lost", ()),
            corrections_lost=made.raw["corrections_lost"],
            groups_lost=made.raw["groups_lost"],
        ),
    }
    if update.person or made.pins:
        # only where NEW holds a person's answer or OLD's pins were carried,
        # so every other export holds no `person` section; NEW's own pin on
        # a place takes the carried one's
        own = {pin_place(p) for p in update.person.pins}
        carried = [
            p
            for p in _pins_reported(
                document,
                update,
                first["deltas"],
                final.cut,
                edge_cut,
                (
                    update.carried_onto[0]
                    if update.carried_onto is not None and update.reasked is None
                    else update.sheet(update.edge_answer)
                    if update.edge_answer
                    else made.carried.sheet
                ),
                _files_of(made),
                moved_ids(first, named),
                passes,
            )
            if pin_place(p) not in own
        ]
        review["person"] = person_payload(update.person, carried)
    return update_export(document, final, PRODUCER, made.raw["new"], review, edge_half)


def carried_origins(
    exported: Mapping[str, Any],
    lineage: Lineage | None,
    carried: EdgeRecords,
    new_cut: Cut,
) -> list[dict[str, Any]]:
    """Each line carried onto NEW whose author OLD's export names: OLD's edge,
    self mark or standing named on NEW by identity's
    one-to-one links (`exchange.line_keys`' names; a group by its first
    block's heir, as a group pin is carried), matched to the carried line
    of that name; one across a split or merge is left out (its author is
    then OLD's sheet answer's). With no `lineage`, `exported` is NEW's
    own, its names NEW's."""
    anchors = {e["id"]: e["anchor"] for e in exported.get("entries", [])}

    def onto(ident: str) -> str | None:
        if "@" in ident:  # a group, `<kind>@<first block>[#n]`
            kind, _, first = ident.partition("@")
            heir = onto(first.split("#")[0])
            return next(
                (
                    g.id
                    for g in new_cut.groups
                    if g.kind.value == kind and g.blocks[0] == heir
                ),
                None,
            )
        if lineage is None:
            return ident
        link = lineage.link_of_old(ident)
        return link.new[0] if link is not None and len(link.new) == 1 else None

    named: dict[tuple[Any, ...], dict[str, Any]] = {}
    for edge in exported.get("edges", []):
        source = onto(edge["source"])
        target = edge["target"]
        if "block" in target:
            kind, name = "block", onto(target["block"])
        elif "group" in target:
            kind, name = "group", onto(target["group"])
        elif "file" in target:
            kind, name = "file", target["file"]
        elif "scope" in target:
            kind, name = "scope", target["scope"]
        else:
            continue
        if source is None or name is None:
            continue
        words = tuple(
            sorted(" ".join(anchors[e].split()) for e in target.get("entries", []))
        ) + named_claims_key(
            target.get("claims", ()),
            edge.get("source_claims", ()),
            bool(edge.get("restates_none")),
            bool(edge.get("assumed")),
        )
        named[("edge", source, edge["kind"], kind, name, words)] = edge["provenance"]
    # self marks and standings, named as `exchange.line_keys` names them,
    # where OLD's export names their author: a cut file given to `diff
    # --cut` may name none
    selfs = Counter(m["block"] for m in exported.get("self", []))
    for mark in exported.get("self", []):
        block = onto(mark["block"])
        if block is not None and "provenance" in mark:
            said = " ".join(e["text"] for e in mark["evidence"])
            several = selfs[mark["block"]] > 1
            named[("self", block, " ".join(said.split()) if several else "")] = mark[
                "provenance"
            ]
    standings = Counter(x["block"] for x in exported.get("standings", []))
    for standing in exported.get("standings", []):
        block = onto(standing["block"])
        if block is not None and "provenance" in standing:
            claim = " ".join(standing["claim"]["text"].split())
            several = standings[standing["block"]] > 1
            named[("standing", block, standing["status"], claim if several else "")] = (
                standing["provenance"]
            )
    return [
        {"key": list(key), "provenance": named[key]}
        for keys in line_keys(carried).values()
        for key in keys
        if key in named
    ]


def _first_authors(update: Update, store: RecordStore) -> Authors:
    """Each line of an update's edge half with the provenance of the answer
    that first wrote it: the chain is the lines carried
    from OLD, each its OLD author (`carried_origins`, identity's match;
    else the answer that wrote OLD's sheet), then the update's edges
    answers in the order asked."""
    made = update.plan
    carried = made.raw.get("carried") or {}
    # the step keeps no authors: a re-run after it was kept reads each
    # carried line's from the state the update kept with it
    found = carried.get("origins")
    kept = store.kept_step(made.identity) if found is None and carried else None
    if kept is not None:
        found = carried_origins(
            versions.kept_payload(store, versions.state(store, kept[0], waiting=True)),
            None,
            edges_file.records(made.carried.sheet, dict(made.handles)),
            made.cut,
        )
    origins = {line_key_from_json(o["key"]): o["provenance"] for o in found or []}
    old = versions.parent_by_raw(store, made.raw)
    fallback = _sheet_author(
        store if old is None else store.seen_from(old.id), carried.get("from")
    )
    base_records = edges_file.records(made.carried.sheet, dict(made.handles))
    base: dict[Any, dict[str, Any]] = {}
    for keys in line_keys(base_records).values():
        for key in keys:
            author = origins.get(key, fallback)
            if author is not None:
                base[key] = author
    if update.edge_answer is None:
        return line_authors(update.edges, [base])
    return first_authors(
        store,
        update.questions.get(Stage.EDGES, ()),
        update.edge_answer,
        update.edges,
        (base,),
    )


def _sheet_author(store: RecordStore, key: str | None) -> dict[str, Any] | None:
    """The provenance of the answer that wrote the sheet an identity answer
    carried, `key`: an edges answer's own; an identity answer's (an update
    that asked nothing), that of the sheet it carried in turn."""
    answered = store.authoritative()
    seen: set[str] = set()
    while key is not None and key not in seen:
        seen.add(key)
        answer = answered.get(key)
        if answer is None:
            return None
        if answer.kind != IDENTITY:
            return provenance(answer)
        key = (versions.identity_raw(store, answer).get("carried") or {}).get("from")
    return None


def _files_of(made: Plan) -> tuple[str, ...]:
    """The paper's files the sheet named, as its header holds them."""
    return tuple(es.parse(made.carried.sheet).files or ())


def _pins_reported(
    document: Document,
    update: Update,
    deltas: Sequence[Mapping[str, Any]],
    final: Cut,
    edge_cut: Cut,
    edge_raw: str,
    files: Sequence[str],
    moved_rule: Collection[str] = (),
    passes: Sequence[Mapping[str, Any]] = (),
) -> list[Pin]:
    """The pins carried from OLD as NEW's export reports them, each by its
    block's or group's id, as the update left them: a
    cut, roles or groups pin as the final cut holds it, one on a block a
    person's cut edit on NEW removed lapsed; an edges pin as the edge half
    holds it, found on its sheet by carrying the carried pair onto it (a
    line that no longer reads there lapses), every one lapsed where the
    edges were written fresh. A held pin is then held or moved as
    `moved_pins` says, by the Δ entries (`deltas`, a person's corrections
    among them) and the blocks the rule moved (`moved_rule`, by id: an
    update of one session has no gate that walks the rule, its marks do);
    then again after each later answer that corrected a Δ (`passes`, the
    record as it stood then, its handles the final cut's); a pin lapsed
    across the version keeps OLD's names."""
    made = update.plan
    # OLD's answers rank before NEW's own, in the export as in the gates
    pins = [dataclasses.replace(p, rank=p.rank - CARRIED_RANKS) for p in made.pins]
    moved_by_rule: set[str] = set(moved_rule)
    for question, answer in (
        (made.question, update.answer),
        (update.second_question, update.second),
    ):
        if question is None or answer is None or made.fresh or made.single_session:
            continue
        assert question.workspace is not None and question.workspace.gate is not None
        spec = json.loads(question.workspace.gate.spec)
        _, true = edges_file.update_reach(spec, es.parse(update.sheet(answer)))
        moved_by_rule |= {
            made.handles[i.block]
            for i in true
            if i.meaning_moved and i.block in made.handles
        }
    away = update.person.cut_away
    found: list[Pin] = []
    for pin in held_on([p for p in pins if p.layer is not Layer.EDGES], final):
        if pin.held and pin.owner in away:
            pin = dataclasses.replace(
                pin, state=lapsed("a person's cut edit changed its block")
            )
        found.append(pin)
    carried = [p for p in pins if p.layer is Layer.EDGES]
    found += [p for p in carried if not p.held]
    if any(p.held for p in carried):
        if made.fresh:
            found += [
                dataclasses.replace(p, state=lapsed("written fresh"))
                for p in carried
                if p.held
            ]
        else:
            ids = edges_file.handles(document, edge_cut)
            found += [
                dataclasses.replace(p, owner=ids.get(p.owner, p.owner))
                for p in as_left(
                    made.pinned.edge_pins(document, edge_cut, files),
                    edge_raw,
                    edges_file.structure(
                        document,
                        edge_cut,
                        files,
                        made.pinned.roles
                        | {
                            p.owner
                            for p in update.person.pins
                            if p.layer is Layer.ROLES and p.held
                        },
                    ),
                )
            ]
    found = moved_pins(found, carrying(deltas), moved_by_rule)
    named = edges_file.handles(document, final)
    for record in passes:
        found = moved_pins(found, carrying(record["deltas"]), moved_ids(record, named))
    return found


def moved_ids(record: Mapping[str, Any], named: Mapping[str, str]) -> set[str]:
    """The ids of the owners `review.update` lists moved by rule, by the
    final cut's handles (`named`, handle → id)."""
    return {
        named.get(m["handle"], m["handle"]) for m in record.get("moved_by_rule", [])
    }


def carrying(deltas: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Each block whose Δ entry carries the change, by id, with its class
    (its last such entry's): a writer's, or a person's or an agent's
    correction, never the rule's on a deleted block."""
    found: dict[str, str] = {}
    for d in deltas:
        if d["by"] != "rule" and d["class"] not in CLOSING:
            found[d["block"]] = d["class"]
    return found


def moved_pins(
    pins: Sequence[Pin], by_delta: Mapping[str, str], by_rule: Collection[str]
) -> list[Pin]:
    """Each held pin with its meaning moved where its block (a group's, its
    first) has a Δ that carries (`by_delta`) or the rule moved it
    (`by_rule`), for the person to confirm again; else
    as it was. A pin once moved stays so: a later Δ correction asks again,
    never takes the question back."""
    reported = []
    for pin in pins:
        if pin.held:
            block = (
                pin.owner.partition("@")[2].split("#")[0]
                if pin.layer is Layer.GROUPS
                else pin.owner
            )
            if block in by_delta:
                pin = dataclasses.replace(pin, state=moved(f"Δ {by_delta[block]}"))
            elif block in by_rule:
                pin = dataclasses.replace(pin, state=moved("by rule"))
        reported.append(pin)
    return reported


def _touched(made: Plan, sheet: str | None) -> list[dict[str, Any]]:
    """`review.update.reopened`'s entries of an update's one session:
    each owner shown without a done mark, and each whose lines its
    answer (its `sheet`) changed, done marks aside, with why (carry's or
    the rule's reason where it had one) and origin, session 1."""
    if sheet is None:
        return []

    def lines(sheet: str) -> dict[str, list[str]]:
        return {
            owner: [line for line in owned if not es.DONE.match(line)]
            for owner, owned in edges_file.written_lines(sheet, flags=True).items()
        }

    shown = es.parse(made.carried.sheet)
    before, after = lines(made.carried.sheet), lines(sheet)
    found = []
    # a block its gate's cut lines made is on the answer's sheet alone
    for h in dict.fromkeys([*made.handles, *after]):
        if h in shown.done and before.get(h, []) == after.get(h, []):
            continue
        reason = made.reopened.get(
            h, Reason("its lines rewritten by the update's session")
        )
        found.append(
            {"handle": h, "why": reason.why, "session": 1, "from": reason.origin}
        )
    return found


def sessions_onto(
    handles: Mapping[str, str], named: Mapping[str, str], within: Lineage | None
) -> Callable[[str | None], str | None]:
    """A handle of the sessions' cut as the final cut names its block
    (`named`, handle → id; `within`, the sessions' cut to the final one,
    None where it is the same): itself where the block stands, else its heir
    (a round's or a person's cut edit split, merged or renamed it); a
    deleted block's name and a scope stay as they are."""
    handle_of = {ident: handle for handle, ident in named.items()}

    def onto(handle: str | None) -> str | None:
        if handle is None or within is None:
            return handle
        ident = handles.get(handle)
        if ident is None:
            return handle
        heir = ident if ident in handle_of else heir_of(within, ident)
        return handle_of.get(heir, handle) if heir is not None else handle

    return onto


def marks_entries(
    marks: Marks | None, onto: Callable[[str | None], str | None]
) -> dict[str, list[Any]]:
    """An update's marks as `review.update` lists them: the
    owners moved by rule (`moved_by_rule`), each `{handle, causes}`, the
    owners stale (`stale`), each `{handle, causes, cleared_by}`, uncleared,
    and the blocks whose own Δ closed the change (`closed_by_delta`), each
    handle, origin and root on the final cut (`onto`)."""

    def causes(found: Sequence[Cause]) -> list[dict[str, Any]]:
        return [
            {"why": c.why, "from": onto(c.origin), "via": c.via, "root": onto(c.root)}
            for c in found
        ]

    found = marks if marks is not None else Marks({}, {})
    return {
        "moved_by_rule": [
            {"handle": onto(h), "causes": causes(c)} for h, c in found.moved.items()
        ],
        "stale": [
            {"handle": onto(h), "causes": causes(c), "cleared_by": None}
            for h, c in found.stale.items()
        ],
        "closed_by_delta": [onto(h) for h in found.closed],
    }


# --- what a later answer re-reads, and a Δ it corrects --------


def rereads(question: Question, answer: AnswerRecord, text: str) -> tuple[str, ...]:
    """The owners an edges answer given after the update's session re-read,
    by id on its question's cut: a model's re-ask's, those it reopened; a person's or an
    agent's, those whose lines it changed from the file shown, or kept by the keep mark,
    where it was shown the marks (an edit of the sheet; lines given without it, by
    `change`, re-read none). Its Δ lines and the stale marks beside its lines re-read
    nothing. `text` is the answer's text."""
    assert question.workspace is not None and question.workspace.gate is not None
    spec = json.loads(question.workspace.gate.spec)
    if answer.marks_shown is False:
        return ()
    if not spec.get(edges_file.PERSON_SPEC):
        owners = list(spec.get("reopened", ()))
    else:

        def lines(sheet: str) -> dict[str, list[str]]:
            return {
                owner: [line for line in owned if not line.startswith(es.DELTA_MARK)]
                for owner, owned in edges_file.written_lines(sheet).items()
            }

        before = lines(question.workspace.files[EDGES_FILE])
        after = lines(text)
        kept = {owner for owner, _ in es.parse(text).kept}
        owners = [
            h
            for h in dict.fromkeys([*before, *after])
            if before.get(h) != after.get(h) or h in kept
        ]
    return tuple(question.handles[h] for h in owners if h in question.handles)


def delta_fixes(
    entries: Sequence[Mapping[str, Any]],
    answer: AnswerRecord,
    records: EdgeRecords,
    onto: Callable[[str], str],
) -> dict[int, dict[str, Any]]:
    """The entries of `review.update.deltas` an answer's Δ lines correct,
    by index: each line on the entry of its block (`onto`
    the entries' cut) and part (its label, else its words), where its class
    or note differs; the entry as corrected, `by` the answer's author. A
    rule's entry (a deleted block's) stands under no block, and is never
    corrected."""
    words = {(e.block, e.label): es.norm(e.words) for e in records.entries}
    by = PERSON if answer.host == PERSON else AGENT
    found: dict[int, dict[str, Any]] = {}
    for d in records.deltas:
        block = onto(d.block)
        said = None if d.entry is None else words.get((d.block, d.entry))
        i = next(
            (
                i
                for i, x in enumerate(entries)
                if x["by"] != "rule"
                and x["block"] == block
                and (x["entry"] is None) == (d.entry is None)
                and (
                    d.entry is None
                    or x["entry"] == d.entry
                    or (x["words"] is not None and es.norm(x["words"]) == said)
                )
            ),
            None,
        )
        if i is None:
            continue
        entry = entries[i]
        if (entry["class"], entry["note"]) != (d.cls.value, d.note):
            found[i] = {**entry, "class": d.cls.value, "note": d.note, "by": by}
    return found


def corrected_classes(entries: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """Each Δ entry a person or an agent corrected, its class by its index,
    which is its writer's Δ line's in the session's answer (the writer's
    entries come first, in the sheet's order)."""
    return {i: d["class"] for i, d in enumerate(entries) if d["by"] in (PERSON, AGENT)}


def with_closed(record: Mapping[str, Any], closed: Sequence[str]) -> dict[str, Any]:
    """`record` with its `closed_by_delta` set to `closed`, where the export
    places it (after `stale`), and none where `closed` is empty."""
    found: dict[str, Any] = {}
    for key, value in record.items():
        if key == "closed_by_delta":
            continue
        found[key] = value
        if key == "stale" and closed:
            found["closed_by_delta"] = list(closed)
    return found


#: Marks given again from corrected classes, as `marks_entries` gives them.
Remark = Callable[[Mapping[int, str]], dict[str, list[Any]]]


def blocks_onto(
    document: Document, before: Cut | None, after: Cut
) -> Callable[[str], str]:
    """A block of `before` as `after` holds it: itself, else its heir."""
    if before is None or [b.id for b in before.blocks] == [b.id for b in after.blocks]:
        return lambda block: block
    within = pair(Version(document, before), Version(document, after))
    return lambda block: heir_of(within, block) or block


def folded(
    record: Mapping[str, Any],
    answered: Sequence[tuple[Question, AnswerRecord, str]],
    store: RecordStore,
    owner: Callable[[str], str | None],
    block_onto: Callable[[Question], Callable[[str], str]],
    remark: Remark | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """`record`, an update's `review.update` on the final cut, after the
    edges answers given after its session, each with its text, in run order,
    one fold for a run and for an edit's step:

    - each answer's re-reads (`rereads`, each id taken onto the final cut
      by `block_onto` and named there by `owner`) clear the stale marks
      open before it, which it was shown,
      and join `reread`, the first answer for each owner; a person's or an
      agent's also clear the lost edges' marks (`lost_marks`);
    - a person's or an agent's answer then corrects the Δ entries its Δ
      lines change (`delta_fixes`, its blocks taken onto the final cut by
      `block_onto`), and, where it did and the update has marks, `remark`
      gives them again from the corrected classes: an owner stale before
      keeps its `cleared_by`, a newly stale one starts open.

    Returns the record and, after each answer that corrected a Δ, the record
    as it stood then, by which a carried pin's moved state follows
    (`moved_pins`)."""
    found = dict(record)
    passes: list[dict[str, Any]] = []
    reread: dict[str, str] = {}
    for r in found.get("reread", []):
        # two owners a later cut edit merged: the first answer speaks
        reread.setdefault(r["handle"], r["by"])
    for question, answer, text in answered:
        # each id taken onto the final cut first, so an id of a cut no
        # longer held (a later cut edit's) is named by its heir
        onto = block_onto(question)
        heard = {
            h
            for ident in rereads(question, answer, text)
            if (h := owner(onto(ident)) or owner(ident)) is not None
        }
        for h in heard:
            reread.setdefault(h, question.key)
        if "stale" in found:
            found["stale"] = [
                {**s, "cleared_by": question.key}
                if s["cleared_by"] is None and s["handle"] in heard
                else s
                for s in found["stale"]
            ]
        assert question.workspace is not None and question.workspace.gate is not None
        if not json.loads(question.workspace.gate.spec).get(edges_file.PERSON_SPEC):
            continue
        if "lost" in found:
            # a lost edge is cleared only by an answer shown it: a
            # person's or an agent's, never a re-ask within the run
            found["lost"] = [
                {**s, "cleared_by": question.key}
                if s["cleared_by"] is None and s["handle"] in heard
                else s
                for s in found["lost"]
            ]
        fixes = delta_fixes(
            found["deltas"],
            answer,
            store.edge_records_of(answer.key),
            block_onto(question),
        )
        if not fixes:
            continue
        found["deltas"] = [fixes.get(i, d) for i, d in enumerate(found["deltas"])]
        if remark is not None and "stale" in found:
            before = {s["handle"]: s["cleared_by"] for s in found["stale"]}
            again = remark(corrected_classes(found["deltas"]))
            found["moved_by_rule"] = again["moved_by_rule"]
            found["stale"] = [
                {**s, "cleared_by": before.get(s["handle"])} for s in again["stale"]
            ]
            found = with_closed(found, again["closed_by_delta"])
        passes.append(dict(found))
    if reread:
        found["reread"] = [{"handle": h, "by": key} for h, key in reread.items()]
    return found, passes


def _opened(
    reopened: Mapping[str, Reason],
    question: Question | None,
    sheet: str | None,
    session: int,
    follow: Followup | None = None,
) -> list[dict[str, Any]]:
    """`review.update.reopened`'s entries of one session: the owners it
    reopened, and those open on condition whose conditions the writer's Δs
    made true, by the gate's own walk, each with the true conditions and
    the origin of the first (through `follow`'s origins, for a follow-up
    session's walk from a block opened before)."""
    if question is None or sheet is None:
        return []
    assert question.workspace is not None and question.workspace.gate is not None
    spec = json.loads(question.workspace.gate.spec)
    _, true = edges_file.update_reach(spec, es.parse(sheet))
    opened: dict[str, str] = {h: r.why for h, r in reopened.items()}
    origins: dict[str, str | None] = {h: r.origin for h, r in reopened.items()}
    held: dict[str, list[str]] = {}
    for impact in true:
        if impact.block in reopened:
            continue
        texts = held.setdefault(impact.block, [])
        if condition(impact) not in texts:
            texts.append(condition(impact))
        origins.setdefault(
            impact.block,
            follow.origin_of(impact.origin) if follow else impact.origin,
        )
    opened |= {h: "; ".join(texts) for h, texts in held.items()}
    return [
        {"handle": h, "why": opened[h], "session": session, "from": origins[h]}
        for h in question.handles
        if h in opened
    ]
